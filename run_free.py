"""Run Insurance Automation using Gemini Web (gemini-webapi) — FREE, no API key.

Usage:
    python run_free.py                          # watch mode
    python run_free.py watch/test_case          # process a specific case
    python run_free.py --server                 # start web UI server

Prerequisites:
    1. Log in to https://gemini.google.com/ in your browser
    2. Copy the __Secure-1PSID and __Secure-1PSIDTS cookies
    3. Set them in your .env file:
         GEMINI_SECURE_1PSID=your-cookie-value
         GEMINI_SECURE_1PSIDTS=your-cookie-value
"""

import os
import sys

# ── Force Gemini Web provider BEFORE anything else loads ──────────────────────
os.environ["AI_PROVIDER"] = "google"

# Validate cookies early
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env")

if not os.environ.get("GEMINI_SECURE_1PSID"):
    print("ERROR: GEMINI_SECURE_1PSID not set.")
    print("  1. Go to https://gemini.google.com/ and log in")
    print("  2. Open DevTools → Application → Cookies")
    print("  3. Copy __Secure-1PSID and __Secure-1PSIDTS values")
    print("  4. Add to .env:")
    print("       GEMINI_SECURE_1PSID=your-cookie-value")
    print("       GEMINI_SECURE_1PSIDTS=your-cookie-value")
    sys.exit(1)

print("  Provider : Gemini Web (gemini-webapi)")
print(f"  Model    : {os.environ.get('AI_MODEL', 'default')}")
print("  Rate     : ~8 req/min (auto-throttled)")
print()

# ── Route to server or CLI ────────────────────────────────────────────────────
if __name__ == "__main__":
    if "--server" in sys.argv:
        import uvicorn

        print("Starting Insurance Automation server at http://localhost:8000")
        uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=True)
    else:
        from src.main import main

        # Allow positional arg: python run_free.py <case_dir>
        args = [a for a in sys.argv[1:] if a != "--server"]
        if len(args) == 1 and not args[0].startswith("--"):
            sys.argv = [sys.argv[0], "--process", args[0]]
        main()
