"""
AI Router Dashboard — Streamlit UI
Two-tier Ollama routing (4b sorter → 35b thinker) + AnythingLLM RAG
"""

import os, json, re, hashlib, time, urllib.request, urllib.error
import streamlit as st
from datetime import datetime
from pathlib import Path

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass
# Ensure project root is importable when running from src/
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
except Exception:
    pass
# ─── Config (centralized in core.config) ─────────────────────────────────────
from core import __version__ as CORE_VERSION
from core.config import (
    OLLAMA_BASE, ANYTHINGLLM_BASE, ANYTHINGLLM_KEY,
    DEFAULT_SMALL, DEFAULT_BIG, RAG_LIMIT, RAG_MAX_CHARS,
    ROUTER_SERVER_URL,
)
from core.auth import _auth_anything, _auth_browser, _auth_ollama
from core.http import _post, _get, _ollama_stream, _alm_request
from core.utils import _extract_json
from core.prompts import (
    SORTER_SYSTEM, SORTER_PROMPT, THINKER_SYSTEM, THINKER_PROMPT,
    GPU_OPTIONS_SMALL as GPU_SMALL, GPU_OPTIONS_BIG as GPU_BIG,
)

SMALL_MODEL = DEFAULT_SMALL
BIG_MODEL = DEFAULT_BIG
PRIVATE_HASH = hashlib.sha256("Tlbyr123".encode()).hexdigest()

WORKSPACES = {
    "Assistant":    {"slug": "assistant",    "icon": "🤖", "color": "#4f8ef7"},
    "Tim":          {"slug": "tim",          "icon": "👤", "color": "#7c3aed"},
    "TCG.bot":      {"slug": "tcg-dot-bot",  "icon": "🃏", "color": "#059669"},
    "Movie_Poster": {"slug": "movie_poster", "icon": "🎬", "color": "#dc2626"},
    "Projects":     {"slug": "projects",     "icon": "💻", "color": "#0891b2"},
    "Pen Test":     {"slug": "pen-test",     "icon": "🛡️", "color": "#b45309"},
    "Substances":   {"slug": "substances",   "icon": "⚗️", "color": "#7c3aed"},
    "Journal":      {"slug": "journal",      "icon": "📓", "color": "#be185d"},
    "Ideas":        {"slug": "ideas",        "icon": "💡", "color": "#d97706"},
    "Private":      {"slug": "private",      "icon": "🔒", "color": "#374151", "locked": True},
}

# Prompt constants now live in core.prompts (imported above) — one source for
# CLI, dashboard, and router_server so behavior never drifts.

# HTTP/auth helpers are provided by core.http and core.auth


# ─── Services ─────────────────────────────────────────────────────────────────
@st.cache_data(ttl=20)
def check_services():
    results = {}
    for name, check in [
        ("ollama",      lambda: _get(f"{OLLAMA_BASE}/api/tags")),
        ("anythingllm", lambda: _alm_request("GET", "workspaces")),
        ("router",      lambda: _get(f"{ROUTER_SERVER_URL}/")),
    ]:
        try: check(); results[name] = True
        except Exception: results[name] = False
    return results


# ─── RAG ──────────────────────────────────────────────────────────────────────
def get_rag(query, slug):
    try:
        r = _alm_request("POST", f"workspace/{slug}/retrieve", {"query": query, "limit": RAG_LIMIT}, timeout=12)
        chunks = [i.get("text") for i in r.get("items", []) if i.get("text")]
        return "\n\n".join(chunks)[:RAG_MAX_CHARS], len(chunks)
    except Exception:
        return "", 0


# ─── Router-server client (SSE) ──────────────────────────────────────────────
# All chat traffic goes through router_server so memory/Clarity/subagent logic
# stays centralized. If router_server is down we fall back to direct Ollama so
# the dashboard stays useful.

def _workspace_alias(ws_name: str) -> str:
    """Map workspace → router alias.
    Assistant gets the cross-workspace alias; everything else auto-routes."""
    if ws_name == "Assistant":
        return "router-assistant"
    return "router"


def _sse_iter(resp):
    """Yield deltas from an OpenAI-compatible SSE stream."""
    for line_bytes in resp:
        if not line_bytes:
            continue
        for line in line_bytes.splitlines():
            line = line.strip()
            if not line or not line.startswith(b"data:"):
                continue
            payload = line[len(b"data:"):].strip()
            if payload == b"[DONE]":
                return
            try:
                obj = json.loads(payload)
            except Exception:
                continue
            for ch in obj.get("choices", []):
                delta = (ch.get("delta") or {}).get("content", "")
                if delta:
                    yield delta


def stream_via_router(ws_name: str, query: str, rag: str, history: list):
    """POST to router_server /v1/chat/completions. Yields text deltas.
    Raises on HTTP failure so the caller can fall back."""
    alias = _workspace_alias(ws_name)
    messages = []
    if rag:
        messages.append({
            "role": "system",
            "content": f"[Workspace RAG — {ws_name}]\n{rag}",
        })
    for m in history[-8:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": query})

    body = json.dumps({
        "model": alias,
        "messages": messages,
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        f"{ROUTER_SERVER_URL}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        yield from _sse_iter(r)


# ─── Local fallback (direct Ollama) ───────────────────────────────────────────
# Only used when the router_server is down. Goes through the same core prompts
# so behavior matches.

def _direct_sorter(query, rag, chunks, history):
    hist_text = json.dumps(history[-4:]) if history else "none"
    prompt = SORTER_PROMPT.format(query=query, rag=rag[:8000], rag_chunks=chunks, history=hist_text)
    try:
        r = _post(f"{OLLAMA_BASE}/api/chat", {
            "model": SMALL_MODEL,
            "messages": [{"role": "system", "content": SORTER_SYSTEM},
                         {"role": "user",   "content": prompt}],
            "stream": False, "options": GPU_SMALL,
        }, headers=_auth_ollama(), timeout=90)
        parsed = _extract_json(r["message"]["content"])
        if parsed: return parsed
    except Exception: pass
    return {"task_type": "complex", "direct_answer": "", "needs_big_ai": True,
            "reason": "sorter fallback",
            "mission_brief": {"objective": query, "context_summary": rag[:2000],
                              "instructions": ["Address the query directly."],
                              "output_format": "clear prose", "constraints": "none", "priority": "quality"}}


def _direct_thinker(query, brief, history):
    hist_text = json.dumps(history[-6:]) if history else "none"
    instructions = "\n".join(brief.get("instructions", ["Complete the task."]))
    prompt = THINKER_PROMPT.format(
        objective=brief.get("objective", query), output_format=brief.get("output_format", "prose"),
        constraints=brief.get("constraints", "none"), priority=brief.get("priority", "balanced"),
        context_summary=brief.get("context_summary", ""), instructions=instructions,
        query=query, history=hist_text,
    )
    payload = {
        "model": BIG_MODEL,
        "messages": [{"role": "system", "content": THINKER_SYSTEM}, {"role": "user", "content": prompt}],
        "stream": True, "options": GPU_BIG,
    }
    for delta in _ollama_stream(payload):
        yield delta


def stream_response(ws_name: str, query: str, rag: str, chunks: int, history: list, router_up: bool):
    """Unified streaming — router first, Ollama direct as fallback."""
    if router_up:
        try:
            yield from stream_via_router(ws_name, query, rag, history)
            return
        except Exception as e:
            # Router had a bad day — fall through to direct Ollama so the UI keeps working
            yield f"\n_(router error: {e}; falling back to direct Ollama)_\n\n"
    sorter = _direct_sorter(query, rag, chunks, history)
    if sorter.get("needs_big_ai"):
        yield from _direct_thinker(query, sorter.get("mission_brief", {}), history)
    else:
        yield sorter.get("direct_answer") or "(no answer)"


# ─── Session ──────────────────────────────────────────────────────────────────
def ws_history(name):
    key = f"hist_{name}"
    if key not in st.session_state: st.session_state[key] = []
    return st.session_state[key]

def ws_append(name, role, content, meta=None):
    ws_history(name).append({"role": role, "content": content,
                              "ts": datetime.now().isoformat(), **(meta or {})})

def export_md(name):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md = f"# {name} — Export\n**{ts}**\n\n---\n\n"
    for m in ws_history(name):
        role  = "**Tim**" if m["role"] == "user" else "**AI**"
        badge = f" `{m.get('model','').split(':')[-1]}` `{m.get('rag_chunks',0)} RAG`" if m["role"] == "assistant" else ""
        md   += f"{role}{badge} _{m['ts'][:19]}_\n\n{m['content']}\n\n---\n\n"
    return md


# ─── CSS ──────────────────────────────────────────────────────────────────────
CSS = """
<style>
/* ── Base ── */
html, body, [class*="css"] {
    font-family: 'Inter', 'Segoe UI', sans-serif;
}
.stApp { background: #0a0a0f; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: #0f0f1a !important;
    border-right: 1px solid #1e1e2e;
}
[data-testid="stSidebar"] * { color: #c9d1d9 !important; }

/* ── Sidebar buttons (workspace list) ── */
[data-testid="stSidebar"] .stButton button {
    background: #161625 !important;
    border: 1px solid #2a2a3e !important;
    border-radius: 8px !important;
    color: #c9d1d9 !important;
    text-align: left !important;
    padding: 8px 12px !important;
    font-size: 0.85rem !important;
    transition: all 0.15s ease !important;
    margin-bottom: 2px !important;
}
[data-testid="stSidebar"] .stButton button:hover {
    background: #1e1e35 !important;
    border-color: #4f8ef7 !important;
    color: #fff !important;
}
[data-testid="stSidebar"] .stButton button[kind="primary"] {
    background: linear-gradient(135deg, #1a2a4a, #1e1e45) !important;
    border-color: #4f8ef7 !important;
    color: #7ab3ff !important;
    font-weight: 600 !important;
}

/* ── Main panel ── */
.main .block-container {
    background: #0a0a0f;
    padding-top: 1.5rem;
    max-width: 900px;
}

/* ── Page header ── */
.page-header {
    background: linear-gradient(135deg, #0f0f1a 0%, #1a1040 100%);
    border: 1px solid #2a2a4e;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 16px;
}
.page-header .ws-icon { font-size: 2.2rem; }
.page-header .ws-name {
    font-size: 1.6rem;
    font-weight: 700;
    color: #e6edf3;
    margin: 0;
}
.page-header .ws-slug {
    font-size: 0.75rem;
    color: #6e7681;
    font-family: monospace;
    margin-top: 2px;
}

/* ── Status dots ── */
.status-row {
    display: flex;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;
}
.status-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #161625;
    border: 1px solid #2a2a3e;
    border-radius: 20px;
    padding: 4px 10px;
    font-size: 0.73rem;
    color: #8b949e;
}
.dot-green { width:7px; height:7px; border-radius:50%; background:#22c55e; flex-shrink:0; }
.dot-red   { width:7px; height:7px; border-radius:50%; background:#ef4444; flex-shrink:0; }
.dot-gray  { width:7px; height:7px; border-radius:50%; background:#6b7280; flex-shrink:0; }

/* ── Chat bubbles ── */
[data-testid="chat-message-container"] {
    background: transparent !important;
}
/* User bubble */
[data-testid="chat-message-container"][data-testid*="user"],
div[class*="stChatMessage"]:has([data-testid*="user"]) {
    background: transparent;
}
.user-bubble {
    background: linear-gradient(135deg, #1a2a4a, #1e1e45);
    border: 1px solid #2a4a7a;
    border-radius: 12px 12px 4px 12px;
    padding: 12px 16px;
    color: #c9d1d9;
    max-width: 85%;
    margin-left: auto;
}
.ai-bubble {
    background: #161625;
    border: 1px solid #2a2a3e;
    border-radius: 4px 12px 12px 12px;
    padding: 14px 16px;
    color: #e6edf3;
}

/* ── Meta pills ── */
.meta-row {
    display: flex;
    gap: 6px;
    margin-bottom: 8px;
    flex-wrap: wrap;
    align-items: center;
}
.pill {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.3px;
}
.pill-35b  { background: #052e16; color: #4ade80; border: 1px solid #166534; }
.pill-4b   { background: #1c1917; color: #fbbf24; border: 1px solid #78350f; }
.pill-rag  { background: #0c1a2e; color: #60a5fa; border: 1px solid #1d4ed8; }
.pill-time { background: #1a1625; color: #a78bfa; border: 1px solid #4c1d95; }

/* ── Chat input ── */
[data-testid="stChatInput"] > div {
    background: #161625 !important;
    border: 1px solid #2a2a3e !important;
    border-radius: 12px !important;
}
[data-testid="stChatInput"] textarea {
    color: #e6edf3 !important;
    background: transparent !important;
}
[data-testid="stChatInput"] button {
    background: #4f8ef7 !important;
    border-radius: 8px !important;
}

/* ── Password gate ── */
.lock-gate {
    text-align: center;
    padding: 60px 40px;
    background: #0f0f1a;
    border: 1px solid #2a2a3e;
    border-radius: 16px;
    margin-top: 40px;
}
.lock-icon { font-size: 3rem; margin-bottom: 12px; }

/* ── Divider ── */
hr { border-color: #1e1e2e !important; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0a0a0f; }
::-webkit-scrollbar-thumb { background: #2a2a3e; border-radius: 3px; }

/* ── Toggle ── */
[data-testid="stToggle"] { color: #8b949e !important; }

/* ── Download button ── */
[data-testid="stDownloadButton"] button {
    background: #161625 !important;
    border: 1px solid #2a2a3e !important;
    color: #8b949e !important;
    border-radius: 8px !important;
    font-size: 0.8rem !important;
}

/* ── Sidebar section header ── */
.sidebar-section {
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    color: #484f58 !important;
    padding: 8px 4px 4px;
}

/* ── Brand header in sidebar ── */
.brand {
    padding: 8px 0 16px;
    text-align: center;
}
.brand-title {
    font-size: 1.1rem;
    font-weight: 800;
    letter-spacing: 2px;
    background: linear-gradient(135deg, #4f8ef7, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.brand-sub {
    font-size: 0.65rem;
    color: #484f58 !important;
    font-family: monospace;
    margin-top: 2px;
}
</style>
"""


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="AI Router", page_icon="⚡", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)

    # Session init
    if "workspace"         not in st.session_state: st.session_state.workspace = "Assistant"
    if "private_unlocked"  not in st.session_state: st.session_state.private_unlocked = False
    if "rag_enabled"       not in st.session_state: st.session_state.rag_enabled = True

    services = check_services()

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("""
        <div class="brand">
            <div class="brand-title">⚡ AI ROUTER</div>
            <div class="brand-sub">4b sorter → 35b thinker</div>
        </div>
        """, unsafe_allow_html=True)

        # Service status
        ol_dot  = '<span class="dot-green"></span>' if services["ollama"]      else '<span class="dot-red"></span>'
        al_dot  = '<span class="dot-green"></span>' if services["anythingllm"] else '<span class="dot-red"></span>'
        rt_dot  = '<span class="dot-green"></span>' if services.get("router")  else '<span class="dot-gray"></span>'
        st.markdown(f"""
        <div class="status-row">
          <span class="status-chip">{ol_dot} Ollama</span>
          <span class="status-chip">{al_dot} AnythingLLM</span>
          <span class="status-chip">{rt_dot} Router</span>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div class="sidebar-section">Workspaces</div>', unsafe_allow_html=True)

        for name, cfg in WORKSPACES.items():
            icon   = cfg["icon"]
            is_cur = st.session_state.workspace == name
            label  = f"{icon}  {name}"
            if st.button(label, key=f"ws_{name}", use_container_width=True,
                         type="primary" if is_cur else "secondary"):
                st.session_state.workspace = name
                if not cfg.get("locked"):
                    st.session_state.private_unlocked = False
                st.rerun()

        st.divider()
        st.markdown('<div class="sidebar-section">Settings</div>', unsafe_allow_html=True)
        st.session_state.rag_enabled = st.toggle("RAG retrieval", value=st.session_state.rag_enabled)

        st.divider()
        cur_hist = ws_history(st.session_state.workspace)
        if cur_hist:
            c1, c2 = st.columns(2)
            if c1.button("🗑 Clear", use_container_width=True):
                st.session_state[f"hist_{st.session_state.workspace}"] = []
                st.rerun()
            c2.download_button("⬇ Export",
                data=export_md(st.session_state.workspace),
                file_name=f"{st.session_state.workspace}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                mime="text/markdown", use_container_width=True)

        st.caption(f"core v{CORE_VERSION}")

    # ── Main panel ───────────────────────────────────────────────────────────
    ws_name = st.session_state.workspace
    ws_cfg  = WORKSPACES[ws_name]
    slug    = ws_cfg["slug"]
    color   = ws_cfg.get("color", "#4f8ef7")

    # Page header
    st.markdown(f"""
    <div class="page-header">
        <div class="ws-icon">{ws_cfg["icon"]}</div>
        <div>
            <div class="ws-name">{ws_name}</div>
            <div class="ws-slug">workspace: {slug} &nbsp;·&nbsp; rag: {"on" if st.session_state.rag_enabled else "off"}</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Private gate ─────────────────────────────────────────────────────────
    if ws_cfg.get("locked") and not st.session_state.private_unlocked:
        st.markdown("""
        <div class="lock-gate">
            <div class="lock-icon">🔒</div>
            <p style="color:#8b949e; font-size:0.9rem; margin-bottom:20px;">
                This workspace is password protected.
            </p>
        </div>
        """, unsafe_allow_html=True)
        col = st.columns([1, 2, 1])[1]
        with col:
            pw = st.text_input("Password", type="password", label_visibility="collapsed",
                               placeholder="Enter password...")
            if st.button("Unlock", type="primary", use_container_width=True):
                if hashlib.sha256(pw.encode()).hexdigest() == PRIVATE_HASH:
                    st.session_state.private_unlocked = True
                    st.rerun()
                else:
                    st.error("Incorrect password.")
        return

    if not ws_cfg.get("locked"):
        st.session_state.private_unlocked = False

    # ── Chat history ─────────────────────────────────────────────────────────
    history = ws_history(ws_name)

    for msg in history:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                model  = msg.get("model", "")
                rag_n  = msg.get("rag_chunks", 0)
                reason = msg.get("reason", "")
                secs   = msg.get("elapsed", 0)
                size   = "35b" if "35b" in model else "4b"
                pill   = f'<span class="pill pill-35b">35b</span>' if size == "35b" else f'<span class="pill pill-4b">4b</span>'
                rag_pill  = f'<span class="pill pill-rag">📚 {rag_n} RAG</span>' if rag_n else ""
                time_pill = f'<span class="pill pill-time">{secs}s</span>' if secs else ""
                reason_md = f'<span style="color:#484f58; font-size:0.7rem; font-style:italic;">{reason}</span>' if reason else ""
                st.markdown(f"""
                <div class="meta-row">{pill}{rag_pill}{time_pill}{reason_md}</div>
                """, unsafe_allow_html=True)
                st.markdown(msg["content"])
            else:
                st.markdown(msg["content"])

    # ── Chat input ───────────────────────────────────────────────────────────
    if prompt := st.chat_input(f"Message {ws_name}..."):
        if not services["ollama"]:
            st.error("Ollama is offline. Start it and try again.")
            return

        ws_append(ws_name, "user", prompt)
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            t0 = time.perf_counter()

            rag, rag_chunks = ("", 0)
            if st.session_state.rag_enabled and services["anythingllm"]:
                with st.spinner("Retrieving context..."):
                    rag, rag_chunks = get_rag(prompt, slug)

            alias = _workspace_alias(ws_name)
            router_up = services.get("router", False)
            pill_text = alias if router_up else "local"
            pill      = f'<span class="pill pill-35b">{pill_text}</span>'
            rag_pill  = f'<span class="pill pill-rag">📚 {rag_chunks} RAG</span>' if rag_chunks else ""
            st.markdown(f'<div class="meta-row">{pill}{rag_pill}</div>', unsafe_allow_html=True)

            answer = st.write_stream(stream_response(
                ws_name, prompt, rag, rag_chunks, history, router_up,
            )) or ""

            elapsed = round(time.perf_counter() - t0, 1)
            st.markdown(f'<span class="pill pill-time">{elapsed}s</span>', unsafe_allow_html=True)

        ws_append(ws_name, "assistant", answer, {
            "model": alias if router_up else f"direct/{BIG_MODEL}",
            "rag_chunks": rag_chunks,
            "reason": "via router_server" if router_up else "direct fallback",
            "elapsed": elapsed,
        })


if __name__ == "__main__":
    main()
