"""Launcher for the AI Dashboard — runs streamlit against dashboard.py.

Bundled into a single .exe via PyInstaller so double-clicking doesn't open VS Code.
"""
import os
import sys
import subprocess
import socket
import time
from pathlib import Path


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def kill_port(port: int) -> None:
    if not port_open(port):
        return
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return
    for line in out.splitlines():
        if f":{port} " in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                subprocess.run(
                    ["taskkill", "/F", "/PID", parts[-1]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
    time.sleep(1)


def resolve_dashboard_py() -> Path:
    """Find dashboard.py — works both from source and from the PyInstaller exe."""
    # When frozen: exe is in bin/, dashboard.py is one level up
    # When source: dashboard.py is next to this script
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    for candidate in (here / "dashboard.py", here.parent / "dashboard.py"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError("dashboard.py not found next to launcher or one level up")


def main() -> int:
    try:
        dash = resolve_dashboard_py()
    except FileNotFoundError as e:
        print(f"[FATAL] {e}")
        input("Press Enter to close...")
        return 1

    kill_port(8501)
    print(f"[*] Starting AI Dashboard — http://localhost:8501")
    print(f"    Source: {dash}")
    print()

    # Use streamlit's CLI as an imported module so a bundled exe can find it
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print("[FATAL] streamlit is not installed")
        input("Press Enter to close...")
        return 1

    sys.argv = [
        "streamlit", "run", str(dash),
        "--server.headless=false",
        "--server.port=8501",
        "--browser.gatherUsageStats=false",
    ]
    return stcli.main()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\n[FATAL] {exc}")
        input("Press Enter to close...")
        sys.exit(1)
