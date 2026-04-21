"""CLI helper for storing AI STACK secrets in the OS keyring.

Windows Credential Manager (or macOS Keychain / Linux Secret Service) encrypts
at rest and scopes access to the logged-in user — strictly better than a
plaintext .env file for day-to-day use. The env var still works as a fallback
for Docker / CI where a keyring backend isn't available.

Accounts:
  anythingllm   — maps to ANYTHINGLLM_API_KEY
  browser-ext   — maps to BROWSER_EXT_KEY
  ollama        — maps to OLLAMA_API_KEY

Usage:
  python -m core.keyring_helper set anythingllm                     # prompts
  python -m core.keyring_helper set anythingllm <value>             # inline
  python -m core.keyring_helper get anythingllm
  python -m core.keyring_helper delete anythingllm
  python -m core.keyring_helper list

Exit codes:
  0 on success, 1 on usage error, 2 on keyring backend failure.
"""

from __future__ import annotations

import getpass
import sys

from .config import KEYRING_SERVICE, _KEYRING_ACCOUNTS


VALID_ACCOUNTS = sorted(set(_KEYRING_ACCOUNTS.values()))


def _require_keyring():
    try:
        import keyring  # noqa: F401
        return keyring
    except Exception as exc:
        sys.stderr.write(
            "keyring package not available: "
            f"{exc}\nInstall with: pip install keyring\n"
        )
        sys.exit(2)


def _validate(account: str) -> None:
    if account not in VALID_ACCOUNTS:
        sys.stderr.write(
            f"unknown account: {account}\n"
            f"valid: {', '.join(VALID_ACCOUNTS)}\n"
        )
        sys.exit(1)


def _cmd_set(account: str, value: str | None) -> None:
    _validate(account)
    keyring = _require_keyring()
    if value is None:
        try:
            value = getpass.getpass(f"value for {account} (hidden): ")
        except (EOFError, KeyboardInterrupt):
            sys.stderr.write("\naborted\n")
            sys.exit(1)
    if not value:
        sys.stderr.write("empty value refused\n")
        sys.exit(1)
    try:
        keyring.set_password(KEYRING_SERVICE, account, value)
    except Exception as exc:
        sys.stderr.write(f"keyring set failed: {exc}\n")
        sys.exit(2)
    print(f"[+] stored {account} in {KEYRING_SERVICE}")


def _cmd_get(account: str) -> None:
    _validate(account)
    keyring = _require_keyring()
    try:
        val = keyring.get_password(KEYRING_SERVICE, account)
    except Exception as exc:
        sys.stderr.write(f"keyring get failed: {exc}\n")
        sys.exit(2)
    if val is None:
        sys.stderr.write(f"[-] no entry for {account}\n")
        sys.exit(1)
    print(val)


def _cmd_delete(account: str) -> None:
    _validate(account)
    keyring = _require_keyring()
    try:
        keyring.delete_password(KEYRING_SERVICE, account)
    except Exception as exc:
        sys.stderr.write(f"keyring delete failed: {exc}\n")
        sys.exit(2)
    print(f"[-] removed {account} from {KEYRING_SERVICE}")


def _cmd_list() -> None:
    keyring = _require_keyring()
    print(f"service: {KEYRING_SERVICE}")
    for acc in VALID_ACCOUNTS:
        try:
            present = keyring.get_password(KEYRING_SERVICE, acc) is not None
        except Exception:
            present = False
        print(f"  {acc:<14} {'✓ set' if present else '– empty'}")


def _usage() -> None:
    sys.stderr.write(__doc__ or "")
    sys.exit(1)


def main(argv: list[str]) -> None:
    if not argv:
        _usage()
    cmd, rest = argv[0], argv[1:]
    if cmd == "list":
        _cmd_list()
    elif cmd == "get" and len(rest) == 1:
        _cmd_get(rest[0])
    elif cmd == "delete" and len(rest) == 1:
        _cmd_delete(rest[0])
    elif cmd == "set" and len(rest) in (1, 2):
        _cmd_set(rest[0], rest[1] if len(rest) == 2 else None)
    else:
        _usage()


if __name__ == "__main__":
    main(sys.argv[1:])
