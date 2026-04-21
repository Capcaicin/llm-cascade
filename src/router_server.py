"""
Two-tier router server — OpenAI-compatible /chat/completions API.
Runs on port 3839. OpenClaw's vllm provider points here.

Routing logic:
  - 4b sorter reads the conversation and writes a JSON mission brief
  - If complexity >= 7 OR task_type in [code, research, analysis, creative] → 35b thinker
  - Otherwise → 4b handles it directly
"""

import json
import sys
import time
import os
import uuid
import socket
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Iterator

# Ensure project root is importable when running from src/
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
except Exception:
    pass

# Force UTF-8 output on Windows so box-drawing chars render
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, ConfigDict
from typing import Any, Union

# ── Config (centralized in core.config) ───────────────────────────────────────
# core.config loads .env itself, so we don't need a second load_dotenv here.
from core.config import (
    OLLAMA_BASE,
    ANYTHINGLLM_BASE,
    ANYTHINGLLM_KEY,
    BROWSER_EXT_KEY,
    DEFAULT_SMALL as SMALL_MODEL,
    DEFAULT_BIG as BIG_MODEL,
    FAST_DRAFT_MODEL,
    FAST_CRITIC_MODEL,
    FAST_CODER_MODEL,
    MEMORY_WORKSPACE,
    MEMORY_RECALL_COUNT,
    ROUTER_SERVER_PORT,
)

OPENCLAW_GATEWAY_PORT = 18789
HEAVY_TYPES          = {"code", "research", "analysis", "creative", "technical"}
COMPLEXITY_THRESHOLD = 6

# Clarity Engine — Tim's local Express SDK that merges raw text into a typed
# priority tree. When the "assistant" workspace is active, the router pulls RAG
# from every non-private workspace, ingests it into Clarity, and hands the
# organized view to the thinker.
CLARITY_BASE        = "http://localhost:3747"
CLARITY_ENGINE_DIR  = r"C:\Users\timan\clarity-engine\clarity-engine"

# Streamlit dashboard — chat/RAG UI, auto-starts alongside the router
DASHBOARD_SCRIPT    = r"C:\Users\timan\AI STACK\src\dashboard.py"
DASHBOARD_PORT      = 8501
ASSISTANT_MODEL_ALIAS = "router-assistant"
# All RAG workspaces EXCEPT "private" (walled) and "assistant" (the caller).
ASSISTANT_SOURCE_WORKSPACES = [
    "tim", "tcg-dot-bot", "movie_poster", "projects",
    "pen-test", "substances", "journal", "ideas",
    MEMORY_WORKSPACE,
]

# SORTER_SYSTEM / CLASSIFIER_SYSTEM live in core.prompts — imported above.

# ── ANSI helpers ──────────────────────────────────────────────────────────────
import ctypes
try:
    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
except Exception:
    pass

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
DIM    = "\033[2m"

def _c(color, text): return f"{color}{text}{RESET}"

# ── Service checker ───────────────────────────────────────────────────────────
def _port_open(port: int, timeout: float = 0.4, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _host_port_from_url(url: str, default_port: int) -> tuple[str, int]:
    """Parse host+port from a URL. Used so the boot check works inside docker
    where `OLLAMA_BASE=http://ollama:11434` — a plain `127.0.0.1` port test
    would miss the container's internal DNS name."""
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or "127.0.0.1"
        port = u.port or default_port
        return host, port
    except Exception:
        return "127.0.0.1", default_port


def _wait_for_port(port: int, timeout: float = 12.0, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(port):
            return True
        time.sleep(interval)
    return False


def _kill_port(port: int) -> bool:
    """Kill any process listening on the given TCP port. Returns True if something was killed."""
    if not _port_open(port):
        return False
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    except Exception:
        return False
    killed = False
    needle = f":{port} "
    for line in out.splitlines():
        if needle in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pid = parts[-1]
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    killed = True
                except Exception:
                    pass
    if killed:
        time.sleep(1.0)   # let the socket free up
    return killed


from core import __version__ as CORE_VERSION
from core.auth import _auth_anything, _auth_browser
from core.http import _ollama_post, _ollama_stream, _alm_request
from core.memory import ensure_memory_workspace, remember_async, memory_recall
from core.prompts import SUBAGENTS, resolve_subagent, THINKER_SYSTEM, CLASSIFIER_SYSTEM
from core.two_pass import two_pass_generate, two_pass_stream
from core.utils import _extract_json


class _Service:
    def __init__(self, label: str, port: int, start_cmd: list[str],
                 url: str = "", description: str = "",
                 check_url: str | None = None, stop_first: bool = False,
                 start_timeout: int = 6):
        self.label         = label
        self.port          = port
        self.start_cmd     = start_cmd
        self.url           = url
        self.description   = description
        self.check_url     = check_url
        self.stop_first    = stop_first
        self.start_timeout = start_timeout


_OLLAMA_HOST, _OLLAMA_PORT = _host_port_from_url(OLLAMA_BASE, 11434)
_ALM_HOST, _ALM_PORT       = _host_port_from_url(ANYTHINGLLM_BASE, 3001)

SERVICES = [
    _Service("Ollama (inference)", _OLLAMA_PORT, ["ollama", "serve"],
             url=OLLAMA_BASE,
             description="Local LLM runtime — serves the 4b sorter + 35b thinker"),
    _Service("AnythingLLM (RAG)",  _ALM_PORT,    [],
             url=ANYTHINGLLM_BASE,
             description="Desktop app — 10 seeded workspaces + embeddings"),
    _Service("OpenClaw Gateway",       OPENCLAW_GATEWAY_PORT,
             ["openclaw.cmd", "gateway"],
             url=f"http://localhost:{OPENCLAW_GATEWAY_PORT}",
             description="Discord + tool-calling bridge (routes to this router)",
             stop_first=False),
    _Service("Clarity Engine",         3747,
             ["npm.cmd", "--prefix", CLARITY_ENGINE_DIR, "start"],
             url="http://localhost:3747",
             description="Typed priority-tree SDK — used by router-assistant"),
    _Service("Streamlit Dashboard",    DASHBOARD_PORT,
             ["python", "-m", "streamlit", "run", DASHBOARD_SCRIPT,
              "--server.headless=true", f"--server.port={DASHBOARD_PORT}",
              "--browser.gatherUsageStats=false"],
             url=f"http://localhost:{DASHBOARD_PORT}",
             description="Chat UI + workspace browser (auto-starts with router)",
             start_timeout=15),
]


def _print_row(label: str, status: str, note: str = ""):
    pad = max(2, 28 - len(label))
    note_str = f"  {_c(DIM, note)}" if note else ""
    print(f"  {label}{' ' * pad}{status}{note_str}")


def _print_access(url: str, description: str):
    """Indented second line that shows URL + what the service is for."""
    if url:
        print(f"      {_c(DIM, '↳')} {_c(CYAN, url)}  {_c(DIM, description)}")
    elif description:
        print(f"      {_c(DIM, '↳ ' + description)}")


def ensure_services():
    print()
    print(_c(BOLD + CYAN, "  ╔══════════════════════════════════════╗"))
    print(_c(BOLD + CYAN, "  ║     Two-Tier Router  —  Boot Check   ║"))
    print(_c(BOLD + CYAN, "  ╚══════════════════════════════════════╝"))
    print()

    results = {}

    for svc in SERVICES:
        svc_host, svc_port = _host_port_from_url(svc.url, svc.port)
        already_up = _port_open(svc_port, host=svc_host)

        if already_up:
            _print_row(svc.label, _c(GREEN, "✔  already running"), f":{svc.port}")
            _print_access(svc.url, svc.description)
            results[svc.label] = "ok"
            continue

        # Can't auto-start desktop apps
        if not svc.start_cmd:
            _print_row(svc.label, _c(YELLOW, "⚠  not detected"),
                       f":{svc.port} — start manually")
            _print_access(svc.url, svc.description)
            results[svc.label] = "warn"
            continue

        print(f"  {svc.label:<28}", end="", flush=True)
        print(_c(YELLOW, "…  starting"), end="\r", flush=True)

        try:
            subprocess.Popen(
                svc.start_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            ok = _wait_for_port(svc.port, timeout=svc.start_timeout)
        except FileNotFoundError:
            ok = False

        if ok:
            _print_row(svc.label, _c(GREEN, "✔  started"),          f":{svc.port}")
            _print_access(svc.url, svc.description)
            results[svc.label] = "ok"
        else:
            _print_row(svc.label, _c(RED,   "✘  failed to start"),  f":{svc.port}")
            _print_access(svc.url, svc.description)
            results[svc.label] = "fail"

    # Small / Big model availability via Ollama tags
    if _port_open(_OLLAMA_PORT, host=_OLLAMA_HOST):
        try:
            req = urllib.request.Request(f"{OLLAMA_BASE}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as r:
                tags_data = json.loads(r.read())
            model_names = [m["name"] for m in tags_data.get("models", [])]
            for model, label in [(SMALL_MODEL, "4b sorter"), (BIG_MODEL, "35b thinker")]:
                found = any(model in n or n.startswith(model.split(":")[0]) for n in model_names)
                status = _c(GREEN, "✔  loaded") if found else _c(YELLOW, "⚠  not pulled")
                note   = model if found else f"run: ollama pull {model}"
                _print_row(f"  Model: {label}", status, note)
        except Exception:
            _print_row("  Models", _c(YELLOW, "⚠  could not check"), "")

    # RAG workspaces — parallel fetch. Cuts the 11-slug × ~1-4s serial pass
    # down to the slowest single call.
    if _port_open(_ALM_PORT, host=_ALM_HOST):
        ensure_memory_workspace()
        print()
        print(_c(DIM, "  RAG workspaces:"))
        ws_slugs = ["tim","tcg-dot-bot","movie_poster","projects","assistant",
                    "pen-test","substances","journal","ideas","private",
                    MEMORY_WORKSPACE]
        hdr = _auth_anything()

        def _ws_probe(slug: str):
            try:
                req = urllib.request.Request(f"{ANYTHINGLLM_BASE}/api/v1/workspace/{slug}", headers=hdr)
                with urllib.request.urlopen(req, timeout=2) as r:
                    data = json.loads(r.read())
                ws = data.get("workspace", [{}])
                ws = ws[0] if isinstance(ws, list) else ws
                return slug, len(ws.get("documents", [])), None
            except Exception as e:
                return slug, None, e

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(ws_slugs)) as ex:
            ws_results = list(ex.map(_ws_probe, ws_slugs))

        for slug, n, err in ws_results:
            if err is not None:
                _print_row(f"  • {slug}", _c(RED, "✘  unreachable"), "")
            else:
                ok = n > 0 or slug in ("private", MEMORY_WORKSPACE)
                status = _c(GREEN, f"✔  {n} docs") if ok else _c(YELLOW, f"⚠  {n} docs")
                _print_row(f"  • {slug}", status, "")

    # Config alignment — verify openclaw.json points at this router AND the
    # schema is clean (agents.defaults.model must be {primary, fallbacks} only).
    # We catch our own drift early instead of waiting for `openclaw doctor`.
    try:
        cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        providers = cfg.get("models", {}).get("providers", {})
        vllm_url  = providers.get("vllm", {}).get("baseUrl", "")
        defaults  = cfg.get("agents", {}).get("defaults", {})
        model     = defaults.get("model", {})
        primary   = model.get("primary", "")
        extra     = sorted(set(model.keys()) - {"primary", "fallbacks"})
        aliases   = sorted(defaults.get("models", {}).keys())
        registered_subs = sum(1 for a in aliases if a.startswith("vllm/router"))

        print()
        print(_c(DIM, "  OpenClaw routing:"))
        _print_row("  • vllm baseUrl",
                   _c(GREEN, "✔  " + vllm_url) if vllm_url.startswith("http://localhost:3839") else _c(RED, "✘  " + (vllm_url or "<unset>")), "")
        _print_row("  • primary model",
                   _c(GREEN, "✔  " + primary) if primary == "ollama/huihui_ai/qwen3.5-abliterated:4b" or primary == "vllm/router" else _c(YELLOW, "⚠  " + (primary or "<unset>")),
                   "abliterated default or vllm/router")
        _print_row("  • schema",
                   _c(GREEN, "✔  clean") if not extra else _c(RED, "✘  extra keys: " + ", ".join(extra)),
                   "agents.defaults.model must be {primary, fallbacks} only")
        _print_row("  • subagent aliases",
                   _c(GREEN, f"✔  {registered_subs}/7 registered") if registered_subs >= 7 else _c(YELLOW, f"⚠  {registered_subs}/7"),
                   "run: python .changes/register_openclaw_subagents.py")
    except FileNotFoundError:
        print(_c(YELLOW, "  OpenClaw config: not found (~/.openclaw/openclaw.json)"))
    except Exception as e:
        print(_c(YELLOW, f"  OpenClaw config: unreadable ({e})"))

    # Access panel — everything the user might want to hit from here
    print()
    print(_c(BOLD + CYAN, "  How to use"))
    print(_c(DIM,         "  ─────────"))
    _print_row("  Router API",       _c(CYAN, "http://localhost:3839"),
               "OpenAI-compatible /v1/chat/completions + /v1/agents/spawn")
    _print_row("  API docs",         _c(CYAN, "http://localhost:3839/docs"), "")
    _print_row("  Dashboard",        _c(CYAN, f"http://localhost:{DASHBOARD_PORT}"),
               "Streamlit chat UI (routes through this server)")
    _print_row("  CLI router",       _c(CYAN, "invoke.ps1"),
               "-Check  |  -Dashboard  |  -Serve  |  -Project <name>")

    # Model aliases — all 7 published subagents + the two raw Ollama models
    print()
    print(_c(DIM,         "  Model aliases:"))
    _print_row("  • router",              _c(GREEN, "auto"),       "4b sorter picks 4b or 35b")
    _print_row("  • router-assistant",    _c(GREEN, "assistant"),  "35b + cross-workspace RAG + Clarity")
    _print_row("  • router-fast",         _c(GREEN, "fast"),       "free-model draft for safe tasks")
    _print_row("  • router-sub-coder",    _c(GREEN, "code"),       "qwen2.5-coder:14b")
    _print_row("  • router-sub-draft",    _c(GREEN, "draft"),      "llama3.1:8b")
    _print_row("  • router-sub-critic",   _c(GREEN, "review"),     "mistral:latest")
    _print_row("  • router-sub-research", _c(GREEN, "research"),   "35b + cross-workspace RAG")
    _print_row("  • router-two-pass",           _c(GREEN, "2-pass"),    "35b draft + critic refine (~2x cost, higher quality)")
    _print_row("  • router-two-pass-uncensored",_c(GREEN, "2-pass+RT"), "draft + critic + red-team prepend (pentest)")
    _print_row(f"  • {SMALL_MODEL}", _c(DIM, "direct"), "raw 4b")
    _print_row(f"  • {BIG_MODEL}",   _c(DIM, "direct"), "raw 35b")

    # Quick manual — cheat sheet of one-liners for the most common actions
    print()
    print(_c(DIM,         "  Quick manual:"))
    print(_c(DIM,         "    Chat (curl):         ") +
          _c(CYAN, 'curl -s http://localhost:3839/v1/chat/completions -d \'{"model":"router","messages":[{"role":"user","content":"hi"}]}\''))
    print(_c(DIM,         "    Spawn subagent:      ") +
          _c(CYAN, 'curl -s http://localhost:3839/v1/agents/spawn -d \'{"alias":"router-sub-coder","prompt":"refactor x"}\''))
    print(_c(DIM,         "    Register w/ OpenClaw:") + " " +
          _c(CYAN, "python .changes/register_openclaw_subagents.py"))
    print(_c(DIM,         "    Pull free models:    ") +
          _c(CYAN, "ollama pull llama3.1:8b && ollama pull mistral && ollama pull qwen2.5-coder:14b"))
    print(_c(DIM,         "    Docker (all-in-one): ") +
          _c(CYAN, "docker compose up -d"))
    print(_c(DIM,         "    Logs / stop:         ") +
          _c(CYAN, "Ctrl-C stops router; dashboard & Clarity keep running"))

    any_fail = any(v == "fail" for v in results.values())
    print()
    if any_fail:
        print(_c(YELLOW, "  Some services failed — router will still start.\n"))
    else:
        print(_c(GREEN, "  All systems Go!\n"))


# ── Models ────────────────────────────────────────────────────────────────────
class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Union[str, list[Any], None] = ""

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = BIG_MODEL
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


def _flatten_content(content) -> str:
    """OpenClaw/OpenAI can send content as a list of blocks. Flatten to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # {"type":"text","text":"..."} is the common shape
                if "text" in block:
                    parts.append(str(block["text"]))
                elif block.get("type") == "text" and "content" in block:
                    parts.append(str(block["content"]))
        return "\n".join(parts)
    return str(content)

# Ollama / AnythingLLM helpers are provided by core.http (_ollama_post, _ollama_stream, _alm_request)


# Memory helpers (ensure_memory_workspace, remember_async, memory_recall)
# are provided by core.memory and imported above.


# ── Clarity Engine ────────────────────────────────────────────────────────────
def _clarity_request(method: str, path: str, body: dict | None = None, timeout: int = 20) -> dict:
    url = f"{CLARITY_BASE}/api/{path.lstrip('/')}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _clarity_up() -> bool:
    return _port_open(3747)


def _aggregate_non_private(query: str, per_ws: int = 2) -> list[str]:
    """Pull the top few RAG chunks from every non-private workspace."""
    if not query:
        return []
    out: list[str] = []
    for slug in ASSISTANT_SOURCE_WORKSPACES:
        try:
            resp = _alm_request(
                "POST", f"workspace/{slug}/retrieve",
                {"query": query[:600], "limit": per_ws}, timeout=8,
            )
        except Exception:
            continue
        items = resp.get("items") or resp.get("sources") or []
        for item in items[:per_ws]:
            text = (item.get("text") or item.get("pageContent") or "").strip()
            if text:
                out.append(f"[{slug}] {text[:360]}")
    return out


def _clarity_snapshot() -> str:
    """Read the current tree and return a compact text summary for the thinker."""
    try:
        stats = (_clarity_request("GET", "stats", timeout=6).get("data") or {})
    except Exception:
        return ""
    lines = [
        f"Clarity score: {stats.get('score', '?')}/100 — {stats.get('total', 0)} nodes",
    ]
    by_type = stats.get("byType") or {}
    if by_type:
        lines.append("By type: " + ", ".join(f"{k}={v}" for k, v in by_type.items()))
    for key, label in (("blockers", "Blockers"),
                       ("actionable", "Next actions"),
                       ("critical", "Critical")):
        bucket = stats.get(key) or []
        if bucket:
            lines.append(f"{label}:")
            for n in bucket[:5]:
                content = (n.get("content") or n.get("title") or "")[:160]
                if content:
                    lines.append(f"  - {content}")
    return "\n".join(lines)


def _clarity_ingest_async(text: str) -> None:
    """Fire-and-forget ingest — lets the tree grow over time without blocking."""
    if not text or not _clarity_up():
        return
    def worker():
        try:
            _clarity_request(
                "POST", "ingest",
                {"rawText": text[:6000], "platform": "unknown"},
                timeout=60,
            )
        except Exception as e:
            print(f"[clarity] ingest failed: {e}", flush=True)
    threading.Thread(target=worker, daemon=True).start()


def build_assistant_context(user_query: str) -> str | None:
    """Assistant-mode system block: cross-workspace RAG + Clarity Engine view.
    Skips the `private` workspace entirely. Returns None if nothing useful."""
    if not user_query:
        return None
    chunks = _aggregate_non_private(user_query)
    clarity_view = _clarity_snapshot() if _clarity_up() else ""

    # Grow the tree with this round's cross-workspace material (async).
    if chunks and _clarity_up():
        _clarity_ingest_async("\n\n".join(chunks[:10]))

    if not chunks and not clarity_view:
        return None

    parts = ["[Assistant Mode — cross-workspace view (private workspace excluded)]"]
    if clarity_view:
        parts.append("Clarity Engine state:")
        parts.append(clarity_view)
    if chunks:
        parts.append("")
        parts.append("Relevant context from workspaces:")
        parts.extend(chunks[:8])
    return "\n".join(parts)


# ── Routing logic ─────────────────────────────────────────────────────────────
# Workspace slugs that must ALWAYS stay on abliterated models regardless of the
# sorter's `safe_for_free_model` flag. Any mention of these topics routes big.
SENSITIVE_KEYWORDS = (
    "pentest", "pen-test", "exploit", "malware", "substance",
    "harm reduction", "private",
)


def _looks_sensitive(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in SENSITIVE_KEYWORDS)


def pick_model(messages: list[Message], prefer_free: bool = False) -> tuple[str, dict]:
    """Run the 4b sorter, then pick a model.

    Default path: abliterated 4b or 35b based on complexity.
    `prefer_free=True` (router-fast alias): if the sorter marks the task
    `safe_for_free_model=true` AND nothing looks sensitive, we dispatch to
    the free llama3.1:8b / qwen2.5-coder / mistral models for speed. Sensitive
    topics always stay on abliterated — no free-model fallback.
    """
    recent = messages[-3:] if len(messages) > 3 else messages
    sorter_msgs = [{"role": "system", "content": CLASSIFIER_SYSTEM}]
    for m in recent:
        text = _flatten_content(m.content)
        sorter_msgs.append({"role": m.role, "content": text[:800]})

    try:
        result = _ollama_post(
            {"model": SMALL_MODEL, "messages": sorter_msgs, "stream": False},
            timeout=30,
        )
        raw = result.get("message", {}).get("content", "{}").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        brief = json.loads(raw)
    except Exception:
        brief = {"complexity": 5, "task_type": "chat", "use_big_model": False,
                 "safe_for_free_model": False, "reason": "sorter failed"}

    joined = " ".join(_flatten_content(m.content) for m in recent)[:4000]
    sensitive = _looks_sensitive(joined)

    use_big = (
        brief.get("use_big_model", False)
        or brief.get("complexity", 0) >= COMPLEXITY_THRESHOLD
        or brief.get("task_type", "") in HEAVY_TYPES
    )

    if use_big:
        return BIG_MODEL, brief

    # Small-model branch. Upgrade to a free model only when explicitly allowed.
    if prefer_free and brief.get("safe_for_free_model") and not sensitive:
        task_type = (brief.get("task_type") or "").lower()
        if task_type == "code":
            return FAST_CODER_MODEL, {**brief, "reason": brief.get("reason", "") + " [free/coder]"}
        # Short prompts → mistral's compact instruct; longer → llama3.1:8b
        word_count = sum(len(_flatten_content(m.content).split()) for m in messages[-2:])
        free_choice = FAST_CRITIC_MODEL if word_count < 60 else FAST_DRAFT_MODEL
        return free_choice, {**brief, "reason": brief.get("reason", "") + " [free]"}

    return SMALL_MODEL, brief


# ── OpenAI-compat response builders ──────────────────────────────────────────
def _make_chunk(content: str, model: str, finish: bool = False) -> str:
    chunk = {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0,
                     "delta": {"content": content} if not finish else {},
                     "finish_reason": "stop" if finish else None}],
    }
    return f"data: {json.dumps(chunk)}\n\n"


def _make_response(content: str, model: str, tool_calls: list | None = None) -> dict:
    msg = {"role": "assistant", "content": content or ""}
    finish = "stop"
    if tool_calls:
        msg["tool_calls"] = [
            {
                "id": f"call_{uuid.uuid4().hex[:10]}",
                "type": "function",
                "function": {
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": json.dumps(tc.get("function", {}).get("arguments", {})),
                },
            }
            for tc in tool_calls
        ]
        finish = "tool_calls"
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Two-Tier Router", version="1.1.0")


@app.get("/")
def health():
    return {
        "status": "ok",
        "core_version": CORE_VERSION,
        "small": SMALL_MODEL,
        "big": BIG_MODEL,
        "free_models": {
            "draft": FAST_DRAFT_MODEL,
            "critic": FAST_CRITIC_MODEL,
            "coder": FAST_CODER_MODEL,
        },
        "aliases": list(SUBAGENTS.keys()),
    }


@app.get("/v1/models")
def list_models():
    data = []
    # Subagent aliases (auto-route + specialists)
    for alias, spec in SUBAGENTS.items():
        data.append({
            "id": alias,
            "object": "model",
            "owned_by": "local",
            "description": spec.get("purpose", ""),
        })
    # Direct-addressable base models
    for m in (SMALL_MODEL, BIG_MODEL, FAST_DRAFT_MODEL, FAST_CRITIC_MODEL, FAST_CODER_MODEL):
        data.append({"id": m, "object": "model", "owned_by": "local"})
    return {"object": "list", "data": data}


def _resolve_route(req_model: str, messages: list[Message], tools: list | None) -> tuple[str, dict, dict | None]:
    """Resolve (ollama_model, brief, subagent_spec) for this request.

    Returns subagent_spec=None for direct-model requests (no alias dispatch).
    """
    alias = (req_model or "").strip().lower()
    spec = resolve_subagent(alias)

    # Tool-calling: always abliterated 35b, ignoring aliases.
    if tools:
        return BIG_MODEL, {"use_big_model": True, "reason": "tools requested"}, None

    # Direct model request (caller named an Ollama model, not an alias)
    if spec is None:
        if alias in (SMALL_MODEL.lower(), BIG_MODEL.lower(),
                      FAST_DRAFT_MODEL.lower(), FAST_CRITIC_MODEL.lower(),
                      FAST_CODER_MODEL.lower()):
            return req_model, {"reason": "direct model"}, None
        # Unknown alias → fall through to default auto-route
        model, brief = pick_model(messages)
        return model, brief, None

    # Subagent alias path
    if spec.get("skip_sorter"):
        return spec["model"], {"reason": f"alias:{alias}"}, spec

    # Aliased but still sorter-driven (router, router-fast)
    prefer_free = bool(spec.get("prefer_free"))
    model, brief = pick_model(messages, prefer_free=prefer_free)
    return model, brief, spec


@app.post("/chat/completions")
@app.post("/v1/chat/completions")
def chat(req: ChatRequest):
    # Pull extras pydantic kept
    extras = req.model_dump()
    tools       = extras.get("tools")
    tool_choice = extras.get("tool_choice")

    model, brief, spec = _resolve_route(req.model, req.messages, tools)
    assistant_mode = bool(spec and spec.get("assistant_mode"))

    # Convert messages, preserving tool-call/result fields if present
    ollama_msgs = []
    for m in req.messages:
        raw = m.model_dump()
        entry = {"role": raw.get("role"), "content": _flatten_content(raw.get("content"))}
        if raw.get("tool_calls"):
            entry["tool_calls"] = raw["tool_calls"]
        if raw.get("tool_call_id"):
            entry["tool_call_id"] = raw["tool_call_id"]
        if raw.get("name"):
            entry["name"] = raw["name"]
        ollama_msgs.append(entry)

    # Inject the last few memory summaries as a system message so the model has
    # continuity across threads/restarts. Skipped for tool-call continuations to
    # avoid polluting a tool-result round trip.
    is_tool_followup = any(m.get("role") == "tool" for m in ollama_msgs[-3:])
    if not is_tool_followup:
        recall_query = next(
            (m["content"] for m in reversed(ollama_msgs) if m["role"] == "user" and m.get("content")),
            "",
        )
        recalled = memory_recall(recall_query, MEMORY_RECALL_COUNT)
        insert_at = 0
        for i, m in enumerate(ollama_msgs):
            if m["role"] == "system":
                insert_at = i + 1
            else:
                break
        # Subagent persona is injected FIRST among system messages so it owns the frame
        if spec and spec.get("system_prepend"):
            ollama_msgs.insert(insert_at, {"role": "system", "content": spec["system_prepend"]})
            insert_at += 1
        if recalled:
            block = "Context from previous conversations (most recent first):\n" + \
                    "\n".join(f"- {s}" for s in recalled)
            ollama_msgs.insert(insert_at, {"role": "system", "content": block})
            insert_at += 1
        if assistant_mode:
            assistant_ctx = build_assistant_context(recall_query)
            if assistant_ctx:
                ollama_msgs.insert(insert_at, {"role": "system", "content": assistant_ctx})

    payload = {"model": model, "messages": ollama_msgs, "stream": req.stream}
    options = dict(spec.get("options") or {}) if spec else {}
    if req.temperature is not None:
        options["temperature"] = req.temperature
    if options:
        payload["options"] = options
    if tools:
        payload["tools"] = tools
        payload["stream"] = False   # Ollama streams tool calls differently; keep it simple

    # Capture the most recent user message so we can summarize the turn afterwards.
    last_user_text = ""
    for m in reversed(req.messages):
        if m.role == "user":
            last_user_text = _flatten_content(m.content)
            break
    should_remember = bool(last_user_text) and not tools and not is_tool_followup

    # ── Two-Pass flow ─────────────────────────────────────────────────────────
    # spec["two_pass"] == True means: run full-fidelity draft + critic refine,
    # return refined answer only. ~1.8-2.2x single-pass cost. Pentest-hardened
    # when spec["uncensored"] is True.
    # Cloud-provider aliases fall through to local model today — see prompts.py.
    if spec and spec.get("two_pass") and not tools:
        uncensored = bool(spec.get("uncensored")) or _looks_sensitive(last_user_text)
        if req.stream:
            def two_pass_event_stream():
                collected: list[str] = []
                yield _make_chunk("", model)
                for delta in two_pass_stream(
                    query=last_user_text, messages=ollama_msgs, model=model,
                    options=options, uncensored=uncensored,
                ):
                    collected.append(delta)
                    yield _make_chunk(delta, model)
                yield _make_chunk("", model, finish=True)
                yield "data: [DONE]\n\n"
                if should_remember:
                    remember_async(last_user_text, "".join(collected))
            return StreamingResponse(two_pass_event_stream(), media_type="text/event-stream")
        # Non-streaming two-pass
        final = two_pass_generate(
            query=last_user_text, messages=ollama_msgs, model=model,
            options=options, uncensored=uncensored,
        )
        if should_remember and final:
            remember_async(last_user_text, final)
        return JSONResponse(_make_response(final, model))

    if req.stream and not tools:
        def event_stream():
            collected: list[str] = []
            yield _make_chunk("", model)
            for delta in _ollama_stream(payload):
                collected.append(delta)
                yield _make_chunk(delta, model)
            yield _make_chunk("", model, finish=True)
            yield "data: [DONE]\n\n"
            if should_remember:
                remember_async(last_user_text, "".join(collected))
        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        result = _ollama_post(payload, timeout=240)
        msg = result.get("message", {})
        content    = msg.get("content", "")
        tool_calls = msg.get("tool_calls") or None
        if should_remember and content and not tool_calls:
            remember_async(last_user_text, content)
        return JSONResponse(_make_response(content, model, tool_calls=tool_calls))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Subagent spawn endpoint ──────────────────────────────────────────────────
# Lets OpenClaw (or anything else) invoke a named subagent on an ad-hoc prompt
# without needing a full chat context. Useful for fan-out patterns:
#   supervisor -> POST /v1/agents/spawn { "alias": "router-sub-critic", "prompt": "..." }
#   -> returns synchronous completion
#
# For fire-and-forget (supervisor keeps going while subagent runs), pass
# "async": true and capture the returned job_id; poll GET /v1/agents/{job_id}.

import threading as _threading

_JOBS: dict[str, dict] = {}


class SpawnRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    alias: str
    prompt: str
    context: list[dict] | None = None
    async_: bool = False  # pydantic disallows `async` keyword


@app.post("/v1/agents/spawn")
def spawn_subagent(req: SpawnRequest):
    spec = resolve_subagent(req.alias)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown alias: {req.alias}")
    if spec.get("model") is None:
        raise HTTPException(status_code=400, detail=f"alias {req.alias} is auto-route; use /v1/chat/completions instead")

    model = spec["model"]
    messages = []
    if spec.get("system_prepend"):
        messages.append({"role": "system", "content": spec["system_prepend"]})
    for m in (req.context or []):
        role = m.get("role", "user")
        content = m.get("content", "")
        messages.append({"role": role, "content": str(content)})
    messages.append({"role": "user", "content": req.prompt})

    payload = {"model": model, "messages": messages, "stream": False}
    options = spec.get("options") or {}
    if options:
        payload["options"] = dict(options)

    def run_sync() -> dict:
        if spec.get("two_pass"):
            content = two_pass_generate(
                query=req.prompt, messages=messages, model=model,
                options=dict(options or {}),
                uncensored=bool(spec.get("uncensored")) or _looks_sensitive(req.prompt),
            )
        else:
            result = _ollama_post(payload, timeout=240)
            content = (result.get("message") or {}).get("content", "")
        return {
            "alias": req.alias,
            "model": model,
            "content": content,
            "finished_at": int(time.time()),
        }

    extras = req.model_dump()
    is_async = extras.get("async") or extras.get("async_") or False

    if is_async:
        job_id = f"job_{uuid.uuid4().hex[:10]}"
        _JOBS[job_id] = {"status": "running", "alias": req.alias, "model": model,
                          "started_at": int(time.time())}

        def worker():
            try:
                out = run_sync()
                _JOBS[job_id].update({"status": "done", **out})
            except Exception as e:
                _JOBS[job_id].update({"status": "error", "error": str(e)})

        _threading.Thread(target=worker, daemon=True).start()
        return {"job_id": job_id, "status": "running", "alias": req.alias, "model": model}

    try:
        return run_sync()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/v1/agents/{job_id}")
def get_job(job_id: str):
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()   # required for PyInstaller --onefile on Windows

    import uvicorn
    try:
        # Kill any previous instance hogging the router port before binding
        if _kill_port(ROUTER_SERVER_PORT):
            print(_c(YELLOW, f"  [~] cleared previous router on :{ROUTER_SERVER_PORT}"), flush=True)

        ensure_services()
        print(_c(DIM, f"  core v{CORE_VERSION}  |  Routing: <{COMPLEXITY_THRESHOLD} → 4b, heavy → 35b"))
        print(_c(DIM, f"  Subagents: " + ", ".join(SUBAGENTS.keys())))
        print()
        uvicorn.run(app, host="0.0.0.0", port=ROUTER_SERVER_PORT, log_level="warning")
    except Exception as exc:
        print(f"\n[FATAL] {exc}", flush=True)
        input("\nPress Enter to close...")
        sys.exit(1)
