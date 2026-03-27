"""Launcher entry-point for the packaged Insurance Automation executable.

When the user double-clicks the .exe this script:
1. Opens the web UI in the default browser.
2. Starts the FastAPI/uvicorn server (blocking).
"""

from __future__ import annotations

# MUST be the very first thing in a frozen Windows app that uses any concurrency
import multiprocessing

multiprocessing.freeze_support()

import socket
import sys
import threading
import time
import traceback
import webbrowser

# Ensure src.paths is resolved first (sets BUNDLE_DIR / APP_DIR)
from src.paths import APP_DIR
from dotenv import load_dotenv

load_dotenv(APP_DIR / ".env")

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def _is_already_running() -> bool:
    """Return True if something is already listening on HOST:PORT."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((HOST, PORT)) == 0


def _open_browser() -> None:
    """Wait briefly for the server to be ready, then open the browser."""
    time.sleep(2.0)
    webbrowser.open(URL)


def main() -> None:
    try:
        # If already running just bring up the browser and exit
        if _is_already_running():
            print("Insurance Automation is already running.")
            print(f"Opening {URL} in your browser...")
            webbrowser.open(URL)
            return

        # Import the app object directly — more reliable in frozen mode than string reference
        from src.api import app  # noqa: PLC0415
        import uvicorn  # noqa: PLC0415

        print("=" * 50)
        print("  Insurance Automation")
        print("=" * 50)
        print(f"  App folder : {APP_DIR}")
        print(f"  Server     : {URL}")
        print()
        print("  Opening browser automatically...")
        print("  Press Ctrl+C in this window to stop.\n")

        # Ensure runtime directories exist next to the .exe
        for d in ("cases", "data", "watch", "output"):
            (APP_DIR / d).mkdir(parents=True, exist_ok=True)

        threading.Thread(target=_open_browser, daemon=True).start()

        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            reload=False,  # reload is incompatible with frozen exe
            log_level="info",
        )

    except Exception:  # pylint: disable=broad-except
        print("\n" + "=" * 50)
        print("  ERROR — Insurance Automation failed to start")
        print("=" * 50)
        traceback.print_exc()
        print()
        input("Press Enter to close this window...")
        sys.exit(1)


if __name__ == "__main__":
    main()
