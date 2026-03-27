# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Insurance Automation.

Build with:
    pyinstaller insurance_automation.spec
"""

import os

block_cipher = None

ROOT = os.path.abspath(".")

a = Analysis(
    ["launcher.py"],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # Bundled read-only assets
        ("config", "config"),
        ("templates", "templates"),
        ("static", "static"),
    ],
    hiddenimports=[
        # --- FastAPI / uvicorn / Starlette internals ---
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "fastapi",
        "fastapi.middleware",
        "fastapi.middleware.cors",
        "starlette",
        "starlette.responses",
        "starlette.routing",
        "starlette.middleware",
        "starlette.middleware.cors",
        "starlette.staticfiles",
        "anyio",
        "anyio._backends",
        "anyio._backends._asyncio",
        # --- multipart ---
        "multipart",
        "python_multipart",
        # --- Our own modules (string-imported by uvicorn) ---
        "src",
        "src.api",
        "src.paths",
        "src.main",
        "src.classifier",
        "src.database",
        "src.filler",
        "src.types",
        "src.extractors",
        "src.extractors.dl",
        "src.extractors.estimate",
        "src.extractors.insurance",
        "src.extractors.invoice",
        "src.extractors.rc",
        "src.utils",
        "src.utils.ai_client",
        # --- Other deps that may be lazy-loaded ---
        "openai",
        "openpyxl",
        "PIL",
        "dotenv",
        "watchdog",
        "thefuzz",
        "sqlite3",
        "email.mime.multipart",
        "httptools",
        "httptools.parser",
        "httptools.parser.parser",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InsuranceAutomation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # keep console visible so user sees logs
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="InsuranceAutomation",
)
