"""RAG helpers: retrieval, document upload, and embedding updates for AnythingLLM."""

from typing import Tuple, Optional
from pathlib import Path

from .http import _alm_request, upload_file
from .config import RAG_LIMIT, RAG_MAX_CHARS, ANYTHINGLLM_BASE


def get_rag_context(query: str, workspace: str) -> Tuple[str, int]:
    """Return (context_text, chunk_count) for a query from a workspace."""
    try:
        result = _alm_request("POST", f"workspace/{workspace}/retrieve", {"query": query, "limit": RAG_LIMIT}, timeout=12)
        chunks = [i.get("text") for i in result.get("items", []) if i.get("text")]
        return ("\n\n".join(chunks)[:RAG_MAX_CHARS], len(chunks))
    except Exception:
        return "", 0


def rag_add_text(title: str, text: str, workspace: str) -> bool:
    """Upload raw text to AnythingLLM and update workspace embeddings."""
    try:
        resp = _alm_request("POST", "document/raw-text", {"textContent": text, "metadata": {"title": title}}, timeout=30)
        docs = resp.get("documents") or []
        if not docs:
            return False
        loc = docs[0].get("location", "")
        if not loc:
            return False
        doc_location = f"custom-documents/{Path(loc).name}"
        _alm_request("POST", f"workspace/{workspace}/update-embeddings", {"adds": [doc_location], "deletes": []}, timeout=60)
        return True
    except Exception:
        return False


def rag_add_file(filepath: str, workspace: str) -> bool:
    """Upload a file to AnythingLLM and update embeddings."""
    path = Path(filepath)
    if not path.exists():
        return False
    try:
        url = f"{ANYTHINGLLM_BASE.rstrip('/')}/api/v1/document/upload"
        resp = upload_file(url, str(path), field_name="file")
        docs = resp.get("documents") or []
        if not docs:
            return False
        loc = docs[0].get("location", "")
        if not loc:
            return False
        doc_location = f"custom-documents/{Path(loc).name}"
        _alm_request("POST", f"workspace/{workspace}/update-embeddings", {"adds": [doc_location], "deletes": []}, timeout=60)
        return True
    except Exception:
        return False


def embed_browser_capture(title: str, url: str, content: str, workspace: str) -> bool:
    """Helper to embed browser-captured text into a workspace."""
    metadata = {
        "title": title,
        "url": url,
        "source": "browser-extension",
    }
    text = f"[{title}]\n{url}\n\n{content}"
    return rag_add_text(title, text, workspace)
