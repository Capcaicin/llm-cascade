"""Core configuration and defaults for AI STACK."""

import os

# Load .env if available (optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

APPDATA_ENV = os.getenv("APPDATA")
BASE_APPDATA = APPDATA_ENV if APPDATA_ENV else os.path.expanduser("~")

SESSION_DIR = os.path.join(BASE_APPDATA, ".ai_router")
SESSION_FILE = os.path.join(SESSION_DIR, "router_session.json")

def _strip_api_suffix(url: str) -> str:
    """AnythingLLM helpers expect the bare host. If a user .env still carries
    the legacy /api suffix, drop it so we don't double-prefix to /api/api/v1/..."""
    u = (url or "").rstrip("/")
    if u.endswith("/api"):
        u = u[: -len("/api")]
    return u


# ── Secret resolution ─────────────────────────────────────────────────────────
# Secrets are looked up from the OS keyring first (Windows Credential Manager /
# macOS Keychain / Secret Service on Linux) and fall back to env vars. This
# means on Windows the AnythingLLM key can live encrypted in Credential Manager
# instead of plaintext in .env — manage it via `python -m core.keyring_helper`.
# The env-var fallback stays so Docker and CI continue to work.
KEYRING_SERVICE = "ai-stack"
_KEYRING_ACCOUNTS = {
    "ANYTHINGLLM_API_KEY": "anythingllm",
    "BROWSER_EXT_KEY":     "browser-ext",
    "OLLAMA_API_KEY":      "ollama",
}


def _get_secret(env_var: str) -> str:
    """Keyring first, env var second. Empty string on miss.

    We eat every keyring exception — missing package, locked backend, no
    matching entry — because the env-var fallback must always be available.
    Resolution order is fixed; do not expose a flag that lets a compromised
    env var silently override a keyring-stored secret."""
    account = _KEYRING_ACCOUNTS.get(env_var)
    if account:
        try:
            import keyring  # local import so the module stays importable without the dep
            val = keyring.get_password(KEYRING_SERVICE, account)
            if val:
                return val
        except Exception:
            pass
    return os.getenv(env_var, "") or ""


OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_API_KEY = _get_secret("OLLAMA_API_KEY")
ANYTHINGLLM_BASE = _strip_api_suffix(os.getenv("ANYTHINGLLM_BASE_URL", "http://localhost:3001"))
ANYTHINGLLM_KEY = _get_secret("ANYTHINGLLM_API_KEY")
BROWSER_EXT_BASE = _strip_api_suffix(os.getenv("BROWSER_EXT_API", ANYTHINGLLM_BASE))
BROWSER_EXT_KEY = _get_secret("BROWSER_EXT_KEY")

# Primary (abliterated/uncensored) models
DEFAULT_SMALL = os.getenv("ROUTER_SMALL_MODEL", "huihui_ai/qwen3.5-abliterated:4b")
DEFAULT_BIG = os.getenv("ROUTER_BIG_MODEL", "huihui_ai/qwen3.5-abliterated:35b")

# Free / fast Ollama models used by subagent aliases. The abliterated pair above
# stays default for anything user-facing; these are only dispatched when the
# sorter tags a task as simple/safe, or when an explicit subagent alias is used.
FAST_DRAFT_MODEL = os.getenv("ROUTER_FAST_DRAFT_MODEL", "llama3.1:8b")
FAST_CRITIC_MODEL = os.getenv("ROUTER_FAST_CRITIC_MODEL", "mistral:latest")
FAST_CODER_MODEL = os.getenv("ROUTER_FAST_CODER_MODEL", "qwen2.5-coder:14b")

DEFAULT_WORKSPACE = os.getenv("ROUTER_WORKSPACE", "my-workspace")
RAG_LIMIT = int(os.getenv("ROUTER_RAG_LIMIT", "20"))
RAG_MAX_CHARS = int(os.getenv("ROUTER_RAG_CHARS", "14000"))
HISTORY_KEEP = int(os.getenv("ROUTER_HISTORY_KEEP", "25"))
MEMORY_WORKSPACE = os.getenv("ROUTER_MEMORY_WORKSPACE", "memory")
MEMORY_RECALL_COUNT = int(os.getenv("ROUTER_MEMORY_RECALL_COUNT", "5"))

ROUTER_SERVER_URL = os.getenv("ROUTER_SERVER_URL", "http://localhost:3839").rstrip("/")
ROUTER_SERVER_PORT = int(ROUTER_SERVER_URL.rsplit(":", 1)[-1]) if ROUTER_SERVER_URL.rsplit(":", 1)[-1].isdigit() else 3839
