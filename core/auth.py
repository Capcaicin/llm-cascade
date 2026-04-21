"""Authorization header helpers for AnythingLLM, browser extension, and Ollama."""

from .config import ANYTHINGLLM_KEY, BROWSER_EXT_KEY, OLLAMA_API_KEY


def _auth_anything() -> dict:
    return {"Authorization": f"Bearer {ANYTHINGLLM_KEY}"} if ANYTHINGLLM_KEY else {}


def _auth_browser() -> dict:
    return {"Authorization": f"Bearer {BROWSER_EXT_KEY}"} if BROWSER_EXT_KEY else {}


def _auth_ollama() -> dict:
    """Empty for local Ollama; Bearer header when OLLAMA_API_KEY is set
    (hosted Ollama, Ollama Cloud, or a reverse-proxy-secured local instance)."""
    return {"Authorization": f"Bearer {OLLAMA_API_KEY}"} if OLLAMA_API_KEY else {}
