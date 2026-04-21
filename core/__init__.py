"""Core shared utilities for AI STACK.

Version is logged at boot of every process that imports core so stale-worker
bugs are a one-line log lookup instead of a git bisect.
"""

__version__ = "0.2.0"

from .config import (
    OLLAMA_BASE,
    OLLAMA_API_KEY,
    ANYTHINGLLM_BASE,
    ANYTHINGLLM_KEY,
    BROWSER_EXT_BASE,
    BROWSER_EXT_KEY,
    DEFAULT_SMALL,
    DEFAULT_BIG,
    FAST_DRAFT_MODEL,
    FAST_CRITIC_MODEL,
    FAST_CODER_MODEL,
    DEFAULT_WORKSPACE,
    RAG_LIMIT,
    RAG_MAX_CHARS,
    HISTORY_KEEP,
    MEMORY_WORKSPACE,
    MEMORY_RECALL_COUNT,
    ROUTER_SERVER_URL,
    ROUTER_SERVER_PORT,
)
from .session import load_session, save_session, SESSION_FILE
from .auth import _auth_anything, _auth_browser, _auth_ollama
from .http import _post, _get, _ollama_post, _ollama_stream, _alm_request, upload_file
from .utils import _extract_json
from .prompts import (
    CLASSIFIER_SYSTEM,
    SORTER_SYSTEM,
    SORTER_PROMPT,
    THINKER_SYSTEM,
    THINKER_PROMPT,
    SUBAGENTS,
    GPU_OPTIONS_SMALL,
    GPU_OPTIONS_BIG,
    resolve_subagent,
)

__all__ = [
    "__version__",
    # config
    "OLLAMA_BASE", "OLLAMA_API_KEY",
    "ANYTHINGLLM_BASE", "ANYTHINGLLM_KEY",
    "BROWSER_EXT_BASE", "BROWSER_EXT_KEY",
    "DEFAULT_SMALL", "DEFAULT_BIG",
    "FAST_DRAFT_MODEL", "FAST_CRITIC_MODEL", "FAST_CODER_MODEL",
    "DEFAULT_WORKSPACE", "RAG_LIMIT", "RAG_MAX_CHARS", "HISTORY_KEEP",
    "MEMORY_WORKSPACE", "MEMORY_RECALL_COUNT",
    "ROUTER_SERVER_URL", "ROUTER_SERVER_PORT",
    # session / auth / http / utils
    "load_session", "save_session", "SESSION_FILE",
    "_auth_anything", "_auth_browser", "_auth_ollama",
    "_post", "_get", "_ollama_post", "_ollama_stream", "_alm_request", "upload_file",
    "_extract_json",
    # prompts
    "CLASSIFIER_SYSTEM", "SORTER_SYSTEM", "SORTER_PROMPT",
    "THINKER_SYSTEM", "THINKER_PROMPT",
    "SUBAGENTS", "GPU_OPTIONS_SMALL", "GPU_OPTIONS_BIG", "resolve_subagent",
]
