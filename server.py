"""Launch the Insurance Automation web server."""

import sys
from pathlib import Path

# Ensure we are running inside the virtual environment
_root = Path(__file__).resolve().parent
venv_python = _root / "venv" / "Scripts" / "python.exe"  # Windows
if not venv_python.exists():
    venv_python = _root / "venv" / "bin" / "python"  # Linux
if venv_python.exists() and sys.executable != str(venv_python):
    import subprocess, os

    subprocess.run([str(venv_python), __file__] + sys.argv[1:])
    sys.exit()

import uvicorn

if __name__ == "__main__":
    print("Starting Insurance Automation server at http://localhost:8000")
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=False)
