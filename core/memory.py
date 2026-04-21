"""Memory helpers: summarize turns and store one-sentence memories in AnythingLLM."""

import json
import threading
import time
from typing import List

from .http import _ollama_post, _alm_request
from .config import MEMORY_WORKSPACE, MEMORY_RECALL_COUNT, DEFAULT_SMALL


def ensure_memory_workspace() -> bool:
    try:
        data = _alm_request("GET", f"workspace/{MEMORY_WORKSPACE}")
        ws = data.get("workspace")
        if isinstance(ws, list) and ws:
            return True
        if isinstance(ws, dict) and ws:
            return True
    except Exception:
        pass
    try:
        _alm_request("POST", "workspace/new", {"name": MEMORY_WORKSPACE}, timeout=20)
        return True
    except Exception:
        return False


def _memory_save(summary: str) -> None:
    summary = (summary or "").strip()
    if not summary:
        return
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    body = {
        "textContent": summary,
        "metadata": {
            "title":       f"memory-{ts}",
            "description": summary[:240],
            "docAuthor":   "router",
            "docSource":   "router-memory",
        },
    }
    try:
        resp = _alm_request("POST", "document/raw-text", body, timeout=30)
        docs = resp.get("documents") or []
        if not docs:
            return
        loc = docs[0].get("location", "")
        if not loc:
            return
        loc = f"custom-documents/{loc.split('/')[-1]}"
        _alm_request("POST", f"workspace/{MEMORY_WORKSPACE}/update-embeddings", {"adds": [loc], "deletes": []}, timeout=45)
    except Exception:
        pass


def _summarize_turn(user_msg: str, assistant_msg: str) -> str:
    user_msg = (user_msg or "").strip()[:1200]
    assistant_msg = (assistant_msg or "").strip()[:1200]
    if not user_msg or not assistant_msg:
        return ""
    prompt = (
        "Summarize this exchange in ONE sentence (<=30 words). "
        "Capture the concrete facts, decisions, or open questions so a future "
        "assistant can pick up the thread. No preamble.\n\n"
        f"User: {user_msg}\nAssistant: {assistant_msg}\n\nSummary:"
    )
    try:
        res = _ollama_post({
            "model": DEFAULT_SMALL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }, timeout=45)
        return (res.get("message", {}).get("content", "") or "").strip()[:240]
    except Exception:
        return ""


def remember_async(user_msg: str, assistant_msg: str) -> None:
    def worker():
        summary = _summarize_turn(user_msg, assistant_msg)
        if summary:
            _memory_save(summary)
    threading.Thread(target=worker, daemon=True).start()


def memory_recall(query: str = "", n: int = MEMORY_RECALL_COUNT) -> List[str]:
    if query:
        try:
            resp = _alm_request("POST", f"workspace/{MEMORY_WORKSPACE}/retrieve", {"query": query[:800], "limit": n}, timeout=12)
            items = resp.get("items") or resp.get("sources") or []
            out = []
            for i in items:
                text = (i.get("text") or i.get("pageContent") or "").strip()
                if text:
                    out.append(text[:240])
            if out:
                return out
        except Exception:
            pass

    try:
        data = _alm_request("GET", f"workspace/{MEMORY_WORKSPACE}")
    except Exception:
        return []
    ws = data.get("workspace") or [{}]
    ws = ws[0] if isinstance(ws, list) else ws
    docs = ws.get("documents") or []
    docs.sort(key=lambda d: d.get("createdAt") or d.get("lastUpdatedAt") or "", reverse=True)
    out: List[str] = []
    for d in docs:
        meta = d.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        text = (d.get("pageContent") or d.get("description") or meta.get("description") or "").strip()
        if not text or text.lower() == "no description found":
            continue
        out.append(text[:240])
        if len(out) >= n:
            break
    return out
