"""
Two-Tier Ollama Router v2 + Browser Extension
4b sorter: reads RAG, writes structured mission brief
35b thinker: executes the mission
AnythingLLM RAG • Browser capture • Multi-workspace • GPU-maxed
"""

import json
import logging
import os
import re
import sys
import argparse
import time
import ctypes
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure project root is importable when running from src/
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
except Exception:
    pass

from core.session import SESSION_FILE, load_session, save_session  # centralized: atomic write + lock + migration
# ─── Windows ANSI / VT100 ─────────────────────────────────────────────────────
def _enable_ansi() -> bool:
    if sys.platform != "win32":
        return True
    try:
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        m = ctypes.c_ulong()
        k.GetConsoleMode(h, ctypes.byref(m))
        k.SetConsoleMode(h, m.value | 0x0004)
        return True
    except Exception:
        return False

_ANSI = _enable_ansi() or bool(os.getenv("FORCE_COLOR"))
def _c(code: str) -> str: return code if _ANSI else ""

RST  = _c("\033[0m");   DIM  = _c("\033[90m");  BOLD = _c("\033[1m")
YEL  = _c("\033[93m");  GRN  = _c("\033[92m");  CYN  = _c("\033[96m")
MAG  = _c("\033[95m");  RED  = _c("\033[91m");  WHT  = _c("\033[97m")
BLU  = _c("\033[94m");  BG_D = _c("\033[48;5;235m")

# ─── Config ───────────────────────────────────────────────────────────────────
# All env-driven config comes from core.config (single source of truth) —
# including the bare-host AnythingLLM URL. See `.env.example`. Local redefs
# here previously defaulted to the legacy `/api`-suffixed form and silently
# 404'd whenever a user set ANYTHINGLLM_BASE_URL to the bare host.
from core.config import (
    OLLAMA_BASE,
    ANYTHINGLLM_BASE,
    ANYTHINGLLM_KEY,
    BROWSER_EXT_BASE,
    BROWSER_EXT_KEY,
    DEFAULT_SMALL,
    DEFAULT_BIG,
    DEFAULT_WORKSPACE,
    RAG_LIMIT,
    RAG_MAX_CHARS,
    HISTORY_KEEP,
)
HISTORY_CONTEXT   = 8

# GPU options
GPU_OPTIONS_SMALL = {"num_gpu": 99, "num_thread": 6,  "temperature": 0.2,  "num_ctx": 16384}
GPU_OPTIONS_BIG   = {"num_gpu": 99, "num_thread": 8,  "temperature": 0.75, "num_ctx": 32768, "repeat_penalty": 1.1}

# Multi-workspace config with URL patterns for auto-detection
WORKSPACE_PATTERNS = {
    "assistant":    ["localhost", "127.0.0.1", "claude.ai", "openai.com"],
    "tim":          ["github.com/capcaicin", "github.com/timan"],
    "tcg-dot-bot":  ["pokemoncenter", "pokemon", "tcgplayer", "tcg"],
    "movie_poster": ["trakt.tv", "plex.tv", "infuse", "letterboxd", "imdb"],
    "projects":     ["github.com", "gitlab.com", "stackoverflow"],
    "pen-test":     ["hackthebox", "tryhackme", "picoctf", "exploit-db", "nvd.nist"],
    "substances":   ["tripsit", "psychonautwiki", "erowid", "dancesafe", "pillreports"],
    "journal":      [],
    "ideas":        [],
    "private":      [],
}

# Session helpers are centralized in core.session (handles migration + atomic writes)
WIDTH = 72

logging.basicConfig(level=logging.WARNING, format="%(message)s")


from core.auth import _auth_anything, _auth_browser
from core.http import _post, _get, _ollama_stream, _alm_request, upload_file
from core.utils import _extract_json
from core.rag import get_rag_context, rag_add_text, rag_add_file, embed_browser_capture
from core.prompts import (
    SORTER_SYSTEM as _SORTER_SYSTEM,
    SORTER_PROMPT as _SORTER_PROMPT,
    THINKER_SYSTEM as _THINKER_SYSTEM,
    THINKER_PROMPT as _THINKER_PROMPT,
)


# ─── Availability checks ──────────────────────────────────────────────────────
def ollama_available() -> bool:
    try: _get(f"{OLLAMA_BASE}/api/tags"); return True
    except Exception: return False

def model_pulled(model: str) -> bool:
    try:
        return any(m.get("name", "") == model
                   for m in _get(f"{OLLAMA_BASE}/api/tags").get("models", []))
    except Exception: return False

def anythingllm_available() -> bool:
    try:
        _get(f"{ANYTHINGLLM_BASE}/api/v1/auth",
             headers=_auth_anything())
        return True
    except Exception: return False

def browser_extension_available() -> bool:
    # brx- keys authenticate workspace chat, not a ping endpoint.
    # Verify by hitting the workspace list with the main API key.
    try:
        _get(f"{ANYTHINGLLM_BASE}/api/v1/workspaces",
             headers=_auth_anything(), timeout=3)
        return True
    except Exception:
        return False


# ─── Browser extension integration ────────────────────────────────────────────
def get_browser_context() -> Optional[dict]:
    """Get current page context from browser extension."""
    try:
        result = _get(
            f"{BROWSER_EXT_BASE}/api/v1/extension/context",
            headers=_auth_browser(),
            timeout=5,
        )
        return result.get("context", {})
    except Exception:
        return None

def detect_workspace_from_url(url: str) -> str:
    """Auto-detect workspace based on URL patterns."""
    if not url:
        return DEFAULT_WORKSPACE
    url_lower = url.lower()
    for ws, patterns in WORKSPACE_PATTERNS.items():
        if any(p.lower() in url_lower for p in patterns):
            return ws
    return DEFAULT_WORKSPACE

# RAG helpers are provided by core.rag (get_rag_context, rag_add_text, rag_add_file, embed_browser_capture)


# ─── Sorter (4b) ──────────────────────────────────────────────────────────────
# Prompts come from core.prompts (imported above) — single source of truth.


def run_sorter(query: str, rag: str, rag_chunks: int, history: list, model: str) -> dict:
    history_text = json.dumps(history[-4:], indent=2) if history else "none"
    prompt = _SORTER_PROMPT.format(
        query=query, rag=rag[:9000], rag_chunks=rag_chunks, history=history_text,
    )
    payload = {
        "model":    model,
        "messages": [
            {"role": "system", "content": _SORTER_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        "stream":  False,
        "options": GPU_OPTIONS_SMALL,
    }
    try:
        result  = _post(f"{OLLAMA_BASE}/api/chat", payload, timeout=90)
        parsed  = _extract_json(result["message"]["content"])
        if parsed:
            return parsed
    except Exception as exc:
        print(f"  {RED}sorter error: {exc}{RST}")
    return {
        "task_type": "complex", "direct_answer": "", "needs_big_ai": True,
        "reason": "sorter fallback",
        "mission_brief": {
            "objective": query, "context_summary": rag[:2000],
            "instructions": ["1. Address the user query directly and completely."],
            "output_format": "clear prose", "constraints": "none", "priority": "quality",
        },
    }


# ─── Big thinker (35b) ────────────────────────────────────────────────────────
# Prompts come from core.prompts (imported above) — single source of truth.


def run_thinker(query: str, brief: dict, history: list, model: str) -> str:
    history_text = json.dumps(history[-HISTORY_CONTEXT:], indent=2) if history else "none"
    instructions = "\n".join(brief.get("instructions", ["Complete the task."]))
    prompt = _THINKER_PROMPT.format(
        objective      = brief.get("objective", query),
        output_format  = brief.get("output_format", "clear prose"),
        constraints    = brief.get("constraints", "none"),
        priority       = brief.get("priority", "balanced"),
        context_summary= brief.get("context_summary", ""),
        instructions   = instructions,
        query          = query,
        history        = history_text,
    )
    messages  = [
        {"role": "system", "content": _THINKER_SYSTEM},
        {"role": "user",   "content": prompt},
    ]
    collected = []
    try:
        payload = {"model": model, "messages": messages, "options": GPU_OPTIONS_BIG}
        for delta in _ollama_stream(payload):
            print(delta, end="", flush=True)
            collected.append(delta)
    except Exception as exc:
        print(f"\n{RED}stream error: {exc}{RST}")
    return "".join(collected)


# ─── Session ──────────────────────────────────────────────────────────────────
# load_session / save_session come from core.session (atomic write + lockfile +
# one-time migration from the old local router_session.json). Do not redefine
# them here — that silently bypasses the hardening.


# ─── Router ───────────────────────────────────────────────────────────────────
class Router:
    def __init__(self, workspace: str, small_model: str, big_model: str, no_rag: bool = False):
        self.small_model = small_model
        self.big_model   = big_model
        self.no_rag      = no_rag
        session          = load_session()
        self.history: list = session.get("history", [])
        self.project     = session.get("project", "general")
        self.workspace   = workspace if workspace != "default" else session.get("workspace", DEFAULT_WORKSPACE)
        self.turn        = len([t for t in self.history if t.get("role") == "user"])
        self.ext_available = browser_extension_available()

    def _save(self):
        save_session({"history": self.history, "workspace": self.workspace, "project": self.project})

    def _header(self) -> None:
        rag_str  = f"{GRN}on{RST}"  if not self.no_rag else f"{YEL}off{RST}"
        ext_str  = f"{GRN}✓{RST}"   if self.ext_available else f"{YEL}✗{RST}"
        sm = self.small_model.split("/")[-1]
        bm = self.big_model.split("/")[-1]
        print()
        print(f"{MAG}{BOLD}{'━'*WIDTH}{RST}")
        print(f"{MAG}{BOLD}  OLLAMA ROUTER v2{RST}  {DIM}local inference + RAG + workspaces{RST}")
        print(f"  {DIM}sort   {YEL}{sm}{RST}  {DIM}→  {GRN}{bm}{RST}")
        print(f"  {DIM}ws     {WHT}{self.workspace}{RST}  "
              f"{DIM}project {WHT}{self.project}{RST}  "
              f"{DIM}rag {rag_str}  "
              f"{DIM}ext {ext_str}")
        print(f"  {DIM}turn {WHT}{self.turn}{RST}  "
              f"{DIM}session {WHT}{SESSION_FILE.name}{RST}")
        print(f"{MAG}{BOLD}{'━'*WIDTH}{RST}")
        print(f"  {DIM}/help · /capture · /project · /workspace · /export · quit{RST}\n")

    def process(self, query: str) -> None:
        t0 = time.perf_counter()
        self.turn += 1
        self.history.append({"role": "user", "content": query, "ts": datetime.now().isoformat()})

        print(f"\n{DIM}{'─'*WIDTH}{RST}")
        print(f"  {DIM}turn {WHT}{self.turn}{RST}  {datetime.now().strftime('%H:%M:%S')}{RST}")

        # RAG
        rag, rag_chunks = ("", 0) if self.no_rag else get_rag_context(query, self.workspace)
        if not self.no_rag and rag_chunks:
            print(f"  {GRN}▸{RST} RAG  {GRN}{rag_chunks} chunks{RST}  {DIM}[{self.workspace}]{RST}")

        # Sort
        print(f"  {DIM}▸ sorting...{RST}", end="\r")
        t_sort  = time.perf_counter()
        sorter  = run_sorter(query, rag, rag_chunks, self.history, self.small_model)
        sort_ms = int((time.perf_counter() - t_sort) * 1000)

        routed = f"{GRN}35b{RST}" if sorter.get("needs_big_ai") else f"{YEL}4b{RST}"
        reason = sorter.get("reason", "")
        brief  = sorter.get("mission_brief", {})
        print(f"  {GRN}▸{RST} route → {routed}  {DIM}{reason}  {sort_ms}ms{RST}")

        # Execute
        print()
        if sorter.get("needs_big_ai"):
            print(f"{GRN}{BOLD}[35b]{RST}{DIM} {'─'*(WIDTH-6)}{RST}\n")
            answer = run_thinker(query, brief, self.history, self.big_model)
        else:
            answer = sorter.get("direct_answer") or "(no answer)"
            print(f"{YEL}{BOLD}[4b]{RST}{DIM} {'─'*(WIDTH-5)}{RST}\n")
            print(answer)

        elapsed = time.perf_counter() - t0
        words   = len(answer.split())
        print(f"\n{DIM}  {'─'*32}  {elapsed:.1f}s  ~{words} words{RST}")

        self.history.append({"role": "assistant", "content": answer, "ts": datetime.now().isoformat()})
        self._save()

    def handle_command(self, cmd: str) -> None:
        parts = cmd.strip().split(maxsplit=1)
        verb  = parts[0].lower()
        arg   = parts[1] if len(parts) > 1 else ""

        if verb == "/help":
            print(f"""
{DIM}  Workspace & Projects
  {WHT}/workspace{DIM}                  show current workspace
  {WHT}/workspace <slug>{DIM}           switch workspace
  {WHT}/workspace-list{DIM}             list all workspaces
  {WHT}/project{DIM}                    show current project
  {WHT}/project <name>{DIM}             switch project (auto-maps workspace)
  {WHT}/projects{DIM}                   list known projects

  RAG & Documents
  {WHT}/rag [on|off]{DIM}               toggle RAG retrieval
  {WHT}/rag-add <title> | <text>{DIM}   add text to workspace
  {WHT}/rag-file <path>{DIM}            upload & embed file
  {WHT}/workspace-docs{DIM}             list documents in workspace

  Capture & Export
  {WHT}/capture{DIM}                    grab current browser page → workspace
  {WHT}/export{DIM}                     export conversation as markdown

  Utilities
  {WHT}/clear{DIM}                      clear conversation history
  {WHT}/history{DIM}                    show recent turns
  {WHT}/models{DIM}                     show models + GPU config
  {WHT}/status{DIM}                     full config snapshot
  {WHT}/save{DIM}                       force-save session
  {WHT}quit{DIM}                        exit{RST}""")

        elif verb == "/capture":
            if not self.ext_available:
                print(f"  {RED}browser extension not available{RST}")
                return
            ctx = get_browser_context()
            if not ctx:
                print(f"  {RED}failed to get page context{RST}")
                return
            url   = ctx.get("url", "")
            title = ctx.get("title", "untitled")
            text  = ctx.get("selectedText", "") or ctx.get("pageText", "")[:5000]
            ws    = detect_workspace_from_url(url)
            if ws != self.workspace:
                self.workspace = ws
                print(f"  {GRN}auto-switched workspace → {WHT}{ws}{RST}")
            print(f"  {DIM}embedding from browser...{RST}", end="\r")
            ok = embed_browser_capture(title, url, text, ws)
            if ok:
                print(f"  {GRN}captured & embedded{RST}  {DIM}[{title}]{RST}")
            else:
                print(f"  {RED}capture failed{RST}")

        elif verb == "/project":
            if not arg:
                print(f"  project: {WHT}{self.project}{RST}"); return
            self.project = arg
            # Map project name to workspace
            ws = arg.lower().replace(" ", "-")
            if ws in WORKSPACE_PATTERNS:
                self.workspace = ws
                print(f"  {GRN}project → {WHT}{arg}{RST}  workspace → {WHT}{ws}{RST}")
            else:
                print(f"  {GRN}project → {WHT}{arg}{RST}  (workspace: {self.workspace})")
            self._save()

        elif verb == "/workspace":
            if not arg:
                print(f"  workspace: {WHT}{self.workspace}{RST}"); return
            self.workspace = arg
            self._save()
            print(f"  {GRN}workspace → {WHT}{self.workspace}{RST}")

        elif verb == "/rag-add":
            if "|" not in arg:
                print(f"  {YEL}usage:{RST} /rag-add <title> | <text>"); return
            title, text = [s.strip() for s in arg.split("|", 1)]
            print(f"  {DIM}embedding to [{self.workspace}]...{RST}", end="\r")
            ok = rag_add_text(title, text, self.workspace)
            print(f"  {GRN}✓ embedded{RST} {DIM}{title}{RST}" if ok else f"  {RED}✗ failed{RST}")

        elif verb == "/rag-file":
            if not arg:
                print(f"  {YEL}usage:{RST} /rag-file <path>"); return
            p = Path(arg.strip('"').strip("'"))
            print(f"  {DIM}embedding {p.name}...{RST}", end="\r")
            ok = rag_add_file(str(p), self.workspace)
            print(f"  {GRN}✓ embedded{RST} {DIM}{p.name}{RST}" if ok else f"  {RED}✗ failed{RST}")

        elif verb == "/rag":
            if arg == "off": self.no_rag = True
            elif arg == "on": self.no_rag = False
            state = f"{GRN}on{RST}" if not self.no_rag else f"{YEL}off{RST}"
            print(f"  RAG {state}")

        elif verb == "/clear":
            self.history = []; self.turn = 0; self._save()
            print(f"  {GRN}history cleared{RST}")

        elif verb == "/history":
            print()
            for t in self.history[-10:]:
                role  = "Tim" if t.get("role") == "user" else "AI"
                color = CYN if role == "Tim" else GRN
                text  = t.get("content", "")[:120].replace("\n", " ")
                ts    = t.get("ts", "")[:19]
                print(f"  {color}{role:<5}{RST}  {DIM}{ts}  {RST}{text}")

        elif verb == "/models":
            print(f"\n  {DIM}small      {WHT}{self.small_model}{RST}")
            print(f"  {DIM}big        {WHT}{self.big_model}{RST}")
            print(f"  {DIM}GPU        {WHT}99 layers (max offload){RST}")
            print(f"  {DIM}ctx small  {WHT}{GPU_OPTIONS_SMALL['num_ctx']:,}{RST}")
            print(f"  {DIM}ctx big    {WHT}{GPU_OPTIONS_BIG['num_ctx']:,}{RST}")

        elif verb == "/status":
            rag_str = f"{GRN}on{RST}" if not self.no_rag else f"{YEL}off{RST}"
            ext_str = f"{GRN}yes{RST}" if self.ext_available else f"{YEL}no{RST}"
            print()
            print(f"  {DIM}workspace      {WHT}{self.workspace}{RST}")
            print(f"  {DIM}project        {WHT}{self.project}{RST}")
            print(f"  {DIM}rag            {rag_str}")
            print(f"  {DIM}browser ext    {ext_str}")
            print(f"  {DIM}turns          {WHT}{self.turn}{RST}")
            print(f"  {DIM}session        {WHT}{SESSION_FILE}{RST}")

        elif verb == "/save":
            self._save()
            print(f"  {GRN}saved{RST}")

        elif verb == "/workspace-list":
            print()
            for name in sorted(WORKSPACE_PATTERNS.keys()):
                marker = f"{GRN}●{RST}" if name == self.workspace else " "
                patterns = ", ".join(WORKSPACE_PATTERNS[name][:2])
                print(f"  {marker} {WHT}{name:<20}{DIM}{patterns}{RST}")

        elif verb == "/projects":
            projects = [
                ("assistant",    "Assistant",        "general-purpose, full tech cheatsheet"),
                ("tim",          "Tim",              "personal profile + preferences"),
                ("tcg-dot-bot",  "TCG.bot",          "Clarity Buyer auto-purchase bot"),
                ("movie_poster", "Movie_Poster",     "digital signage (Trakt + Plex)"),
                ("projects",     "Projects",         "active projects + dev patterns"),
                ("pen-test",     "Pen Test",         "pentest methodology + CTF"),
                ("substances",   "Substances",       "harm reduction reference"),
                ("journal",      "Journal",          "reflection + personal writing"),
                ("ideas",        "Ideas",            "idea capture + evaluation"),
                ("private",      "Private",          "password-gated (Tlbyr123)"),
            ]
            print()
            for slug, name, desc in projects:
                marker = f"{GRN}●{RST}" if slug == self.project else " "
                print(f"  {marker} {WHT}{name:<25}{DIM}{desc}{RST}")
                print(f"       {DIM}→ workspace: {slug}{RST}")

        elif verb == "/workspace-docs":
            print(f"  {DIM}workspace documents: {self.workspace}{RST}")
            try:
                result = _get(
                    f"{ANYTHINGLLM_BASE}/api/v1/workspace/{self.workspace}",
                    headers=_auth_anything(),
                    timeout=10,
                )
                docs = result.get("documents", [])
                if not docs:
                    print(f"    {DIM}(no documents){RST}")
                    return
                for doc in docs[:15]:
                    name = doc.get("filename", doc.get("docName", "unnamed"))
                    status = "▸" if doc.get("status") == "complete" else "⟳"
                    print(f"    {status} {DIM}{name}{RST}")
            except Exception as e:
                print(f"    {RED}error: {e}{RST}")

        elif verb == "/export":
            if not self.history:
                print(f"  {YEL}no conversation to export{RST}"); return
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"router_export_{self.project}_{ts}.md"
            path = Path(__file__).parent / filename
            md = f"# Conversation Export\n\n"
            md += f"**Project:** {self.project}  \n"
            md += f"**Workspace:** {self.workspace}  \n"
            md += f"**Exported:** {datetime.now().isoformat()}  \n\n"
            md += f"---\n\n"
            for turn in self.history:
                role = "**Tim**" if turn.get("role") == "user" else "**AI**"
                content = turn.get("content", "")
                ts = turn.get("ts", "")[:19]
                md += f"{role} _{ts}_\n\n{content}\n\n---\n\n"
            try:
                path.write_text(md, encoding="utf-8")
                print(f"  {GRN}exported → {WHT}{filename}{RST}")
            except Exception as e:
                print(f"  {RED}export failed: {e}{RST}")

        else:
            print(f"  {RED}unknown command{RST}")

    def run(self) -> None:
        self._header()
        while True:
            try:
                user_input = input(f"\n{CYN}{BOLD}Tim{RST}  ").strip()
            except (KeyboardInterrupt, EOFError):
                print(f"\n  {DIM}session saved — goodbye{RST}\n")
                self._save(); break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print(f"\n  {DIM}session saved — goodbye{RST}\n")
                self._save(); break
            if user_input.startswith("/"):
                self.handle_command(user_input); continue

            self.process(user_input)


# ─── Entry point ──────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Two-tier Ollama Router + Browser")
    parser.add_argument("--workspace",   default="default", help="AnythingLLM workspace")
    parser.add_argument("--small-model", default=DEFAULT_SMALL, help="Sorter model")
    parser.add_argument("--big-model",   default=DEFAULT_BIG, help="Thinker model")
    parser.add_argument("--project",     default="general", help="Project name")
    parser.add_argument("--no-rag",      action="store_true", help="Disable RAG")
    parser.add_argument("--check",       action="store_true", help="Check connectivity")
    args = parser.parse_args()

    if args.check:
        w = 16
        ok = lambda b: f"{GRN}OK{RST}" if b else f"{RED}FAIL{RST}"
        np = lambda b: f"{GRN}OK{RST}" if b else f"{YEL}NOT PULLED{RST}"
        print()
        print(f"  {'Ollama':<{w}}  {ok(ollama_available())}")
        print(f"  {'AnythingLLM':<{w}}  {ok(anythingllm_available())}")
        print(f"  {'Browser ext':<{w}}  {ok(browser_extension_available())}")
        print(f"  {'Small model':<{w}}  {np(model_pulled(args.small_model))}")
        print(f"  {'Big model':<{w}}  {np(model_pulled(args.big_model))}")
        print()
        return

    if not ollama_available():
        print(f"\n{RED}  Ollama not reachable — start it first{RST}\n")
        sys.exit(1)

    Router(
        workspace=args.workspace,
        small_model=args.small_model,
        big_model=args.big_model,
        no_rag=args.no_rag,
    ).run()


if __name__ == "__main__":
    main()
