"""CLI helper for storing AI STACK secrets in the OS keyring.

Windows Credential Manager (or macOS Keychain / Linux Secret Service) encrypts
at rest and scopes access to the logged-in user — strictly better than a
plaintext .env file for day-to-day use. The env var still works as a fallback
for Docker / CI where a keyring backend isn't available.

Accounts:
  anythingllm       — maps to ANYTHINGLLM_API_KEY
  browser-ext       — maps to BROWSER_EXT_KEY
  ollama            — maps to OLLAMA_API_KEY
  private-password  — maps to PRIVATE_PASSWORD (dashboard private-workspace gate)

Usage:
  python -m core.keyring_helper list
      Show every account and whether a value is currently stored.

  python -m core.keyring_helper set <account>
      Prompt for the value interactively (never echoed to the terminal).

  python -m core.keyring_helper set <account> <value>
      Set inline. Avoid — the value lands in shell history. Prefer the
      interactive form when a human is typing.

  python -m core.keyring_helper get <account>
      Print the stored value to stdout. Suitable for `$(... get ...)`
      substitution in scripts — no prompts, no framing text.

  python -m core.keyring_helper delete <account>
      Remove the stored value. The env var (if set) still takes over.

Examples:
  # One-time setup on a fresh box:
  python -m core.keyring_helper set anythingllm
  python -m core.keyring_helper set private-password

  # Verify everything is stored:
  python -m core.keyring_helper list

  # Migrate from .env to keyring, then wipe .env:
  python -m core.keyring_helper set anythingllm "$ANYTHINGLLM_API_KEY"
  python -m core.keyring_helper set browser-ext  "$BROWSER_EXT_KEY"
  python -m core.keyring_helper set ollama       "$OLLAMA_API_KEY"
  unset ANYTHINGLLM_API_KEY BROWSER_EXT_KEY OLLAMA_API_KEY
  # then edit .env and blank those lines

Resolution order at runtime (see core.config._get_secret):
  1. OS keyring (this CLI writes here)
  2. Environment variable fallback
  3. Empty string

Exit codes:
  0 on success, 1 on usage error, 2 on keyring backend failure.
"""

from __future__ import annotations

import getpass
import sys

from .config import KEYRING_SERVICE, _KEYRING_ACCOUNTS


VALID_ACCOUNTS = sorted(set(_KEYRING_ACCOUNTS.values()))

# Env-var names stay visible so the list/migration hints can show operators
# exactly which variable each account shadows.
_ACCOUNT_TO_ENV = {account: env for env, account in _KEYRING_ACCOUNTS.items()}


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


def _count_stored(keyring) -> int:
    n = 0
    for acc in VALID_ACCOUNTS:
        try:
            if keyring.get_password(KEYRING_SERVICE, acc):
                n += 1
        except Exception:
            pass
    return n


def _print_first_run_hint() -> None:
    """Printed after list/set when the keyring is still empty — the "first
    run" case. A tight cheat sheet beats making the user re-read the docstring."""
    print()
    print("  Keyring is empty. Minimum setup:")
    print("    python -m core.keyring_helper set anythingllm")
    print("    python -m core.keyring_helper set private-password   # optional")
    print("  Or migrate from existing .env in one shot:")
    for env, account in _KEYRING_ACCOUNTS.items():
        print(f"    python -m core.keyring_helper set {account:<16} \"${env}\"")
    print("  Values stay encrypted in the OS keyring; env vars remain the fallback.")


def _cmd_set(account: str, value: str | None) -> None:
    _validate(account)
    keyring = _require_keyring()
    was_empty_before = _count_stored(keyring) == 0
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
    if was_empty_before and _count_stored(keyring) < len(VALID_ACCOUNTS):
        # First write. Nudge toward setting the rest so the user knows what
        # else is on offer — only shown the first time.
        remaining = [a for a in VALID_ACCOUNTS if a != account]
        print()
        print("  First entry stored. Remaining accounts:")
        for acc in remaining:
            env = _ACCOUNT_TO_ENV.get(acc, "")
            print(f"    {acc:<16} (shadows env {env})")
        print("  Set with:  python -m core.keyring_helper set <account>")


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
    stored = 0
    for acc in VALID_ACCOUNTS:
        try:
            present = keyring.get_password(KEYRING_SERVICE, acc) is not None
        except Exception:
            present = False
        env = _ACCOUNT_TO_ENV.get(acc, "")
        mark = "✓ set" if present else "– empty"
        stored += int(present)
        print(f"  {acc:<16} {mark:<8} (env fallback: {env})")
    if stored == 0:
        _print_first_run_hint()


def _usage(exit_code: int = 1) -> None:
    # Write help to stdout when the user explicitly asked for it (-h/--help),
    # stderr when they got here by mistake.
    stream = sys.stdout if exit_code == 0 else sys.stderr
    stream.write(__doc__ or "")
    sys.exit(exit_code)


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help", "help"):
        _usage(exit_code=0 if argv else 1)
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
