"""Session file helpers: idempotent migration, atomic write, light lock.

Safety notes (implements the CRITICAL WATCH-OUTS):
- Migration is idempotent and only runs when an old local session exists and
  the centralized APPDATA session does not.
- Uses `os.replace` atomic move for writes and a tiny lockfile fallback to
  reduce risk of concurrent writes (200ms polling).
- Keeps a `.bak` copy of the original local session and appends a log entry
  to `CHANGES.md` in the repo root.
"""

import os
import time
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from .config import SESSION_FILE as SESSION_FILE_STR, SESSION_DIR as SESSION_DIR_STR, HISTORY_KEEP, DEFAULT_WORKSPACE

# Paths as Path objects
SESSION_DIR = Path(SESSION_DIR_STR)
SESSION_FILE = Path(SESSION_FILE_STR)
LOCK_PATH = Path(str(SESSION_FILE) + ".lock")


def ensure_session_dir():
    """Create the session dir; fallback to home if APPDATA denied."""
    global SESSION_DIR, SESSION_FILE, LOCK_PATH
    try:
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        alt = Path(os.path.expanduser("~")) / ".ai_router"
        try:
            alt.mkdir(parents=True, exist_ok=True)
            SESSION_DIR = alt
            SESSION_FILE = SESSION_DIR / "router_session.json"
            LOCK_PATH = Path(str(SESSION_FILE) + ".lock")
        except Exception:
            pass
    except Exception:
        # Best-effort: ignore other errors and allow higher-level code to handle IO errors
        pass


def _acquire_lock(timeout: float = 2.0, poll: float = 0.2) -> bool:
    start = time.time()
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return True
        except FileExistsError:
            if time.time() - start >= timeout:
                return False
            time.sleep(poll)
        except Exception:
            return False


def _release_lock() -> None:
    try:
        if LOCK_PATH.exists():
            LOCK_PATH.unlink()
    except Exception:
        pass


def migrate_old_session() -> None:
    """If a local `src/router_session.json` exists and the centralized session
    file does not, copy it to the centralized location and save a `.bak` next
    to the original. Appends an entry to `CHANGES.md`.
    """
    old = Path(__file__).parent.parent / "router_session.json"
    if not old.exists():
        return
    try:
        ensure_session_dir()
        if SESSION_FILE.exists():
            # Already migrated or a newer session exists — do nothing
            return

        changelog = Path(__file__).parent.parent / "CHANGES.md"
        # Create a backup of the original file (router_session.json.bak)
        backup = old.with_suffix(old.suffix + ".bak")
        try:
            if not backup.exists():
                shutil.copy2(str(old), str(backup))
        except Exception:
            pass

        # Copy content atomically to central session file
        try:
            data = old.read_text(encoding="utf-8")
            tmp = str(SESSION_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, str(SESSION_FILE))
            # Log the migration
            try:
                with open(changelog, "a", encoding="utf-8") as fch:
                    fch.write(f"{datetime.now().isoformat()} - Migrated session {old} -> {SESSION_FILE}; backup: {backup}\n")
            except Exception:
                pass
        except Exception as e:
            # Log failure if possible
            try:
                with open(changelog, "a", encoding="utf-8") as fch:
                    fch.write(f"{datetime.now().isoformat()} - Migration failed: {e}\n")
            except Exception:
                pass
    except Exception:
        # Non-fatal: don't raise on import
        pass


def load_session() -> Dict[str, Any]:
    """Load session JSON from the centralized path. Returns a minimal default
    structure if not present or if parsing fails.
    """
    # Ensure migration happened (idempotent)
    migrate_old_session()
    ensure_session_dir()
    if SESSION_FILE.exists():
        try:
            return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"history": [], "workspace": DEFAULT_WORKSPACE, "project": "general"}


def save_session(data: Dict[str, Any]) -> None:
    """Atomically write `data` to the centralized session file. Uses a tiny
    lockfile for concurrent-write scenarios and falls back to an atomic replace.
    """
    ensure_session_dir()
    data["history"] = data.get("history", [])[-HISTORY_KEEP:]
    tmp = str(SESSION_FILE) + ".tmp"
    if _acquire_lock():
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, str(SESSION_FILE))
        finally:
            _release_lock()
    else:
        # Best-effort fallback
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, str(SESSION_FILE))
        except Exception:
            pass


# Run migration on import so any consumer that imports `core.session` will
# have the one-time migration executed (idempotent).
try:
    migrate_old_session()
except Exception:
    pass
