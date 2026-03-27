"""Centralized path resolution — works in both normal Python and PyInstaller frozen mode."""

from __future__ import annotations

import sys
from pathlib import Path

# When frozen by PyInstaller:
#   BUNDLE_DIR  = sys._MEIPASS  (where bundled read-only assets live: config, templates, static)
#   APP_DIR     = directory containing the .exe  (where runtime data lives: .env, cases, data)
# When running from source:
#   Both point to the project root.

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    APP_DIR = Path(sys.executable).resolve().parent
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    APP_DIR = BUNDLE_DIR

PROJECT_ROOT = APP_DIR  # alias for backward compat
