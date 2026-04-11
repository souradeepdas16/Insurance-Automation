"""FastAPI backend for Insurance Automation UI."""

from __future__ import annotations

import io
import os
import queue
import shutil
import sys
import threading
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ── Per-case log capture ──────────────────────────────────────────────────────
# Maps case_id -> list of log lines (in-memory, cleared on new run)
_case_logs: dict[int, list[str]] = {}
# Maps thread_id -> case_id so the stdout interceptor knows which case owns each line
_thread_case_map: dict[int, int] = {}
_log_lock = threading.Lock()

# ── Per-case cancellation ─────────────────────────────────────────────────────
# Maps case_id -> threading.Event; set() means "please stop"
_cancel_events: dict[int, threading.Event] = {}

# ── Processing queue (one-at-a-time) ──────────────────────────────────────────
_processing_queue: queue.Queue[int] = queue.Queue()


class _ThreadAwareCapture(io.TextIOBase):
    """Tee: writes every print() to real stdout AND to per-case log buffer."""

    def __init__(self, real: object) -> None:
        self._real = real
        self._pending: dict[int, str] = {}  # partial lines keyed by thread id

    def write(self, s: str) -> int:  # type: ignore[override]
        self._real.write(s)
        tid = threading.get_ident()
        case_id = _thread_case_map.get(tid)
        if case_id is not None:
            with _log_lock:
                buf = self._pending.get(tid, "") + s
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    _case_logs.setdefault(case_id, []).append(line)
                self._pending[tid] = buf
        return len(s)

    def flush(self) -> None:
        self._real.flush()

    # Forward everything else (readline, etc.) to real stdout
    def __getattr__(self, name: str):
        return getattr(self._real, name)


# Install once at module load so all print() calls are captured
_real_stdout = sys.stdout
sys.stdout = _ThreadAwareCapture(_real_stdout)

from src.database import (
    init_db,
    get_all_settings,
    get_setting,
    set_setting,
    create_case,
    get_case,
    list_cases,
    update_case_status,
    delete_case,
    add_document,
    delete_document,
    get_document_by_id,
    get_documents_by_case,
    reset_stuck_processing,
)

from src.paths import APP_DIR, BUNDLE_DIR

PROJECT_ROOT = APP_DIR
STATIC_DIR = BUNDLE_DIR / "static"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".pdf"}

app = FastAPI(title="Insurance Automation", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    n = reset_stuck_processing()
    if n:
        print(
            f"  ⚠ Reset {n} case(s) stuck in 'processing'/'queued' from a previous run."
        )
    # Start the single processing worker thread
    worker = threading.Thread(
        target=_queue_worker, daemon=True, name="processing-worker"
    )
    worker.start()


# ── Settings ──────────────────────────────────────────────────────────────────


@app.get("/api/settings")
def api_get_settings():
    return get_all_settings()


@app.put("/api/settings")
def api_update_settings(body: dict):
    for key, value in body.items():
        if key == "cases_folder":
            folder = Path(value)
            folder.mkdir(parents=True, exist_ok=True)
        set_setting(key, str(value))
    return get_all_settings()


# ── Cases ─────────────────────────────────────────────────────────────────────


@app.get("/api/cases")
def api_list_cases():
    return list_cases()


@app.post("/api/cases")
def api_create_case(name: str = Form(...)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Case name is required")

    cases_folder = Path(get_setting("cases_folder"))
    cases_folder.mkdir(parents=True, exist_ok=True)

    # Sanitize folder name
    safe_name = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in name)
    case_folder = cases_folder / safe_name

    if case_folder.exists():
        raise HTTPException(400, f"Case folder '{safe_name}' already exists")

    case_folder.mkdir(parents=True)
    case = create_case(name, str(case_folder))
    return case


@app.get("/api/cases/{case_id}")
def api_get_case(case_id: int):
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")

    # Check for output files
    case_folder = Path(case["folder_path"])
    output_dir = case_folder / "output"
    case["output_files"] = []
    if output_dir.exists():
        case["output_files"] = [f.name for f in output_dir.iterdir() if f.is_file()]
    return case


@app.delete("/api/cases/{case_id}")
def api_delete_case(case_id: int):
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")

    # Remove the folder from disk
    case_folder = Path(case["folder_path"])
    if case_folder.exists():
        shutil.rmtree(case_folder, ignore_errors=True)

    delete_case(case_id)
    return {"ok": True}


# ── Document Upload ───────────────────────────────────────────────────────────


@app.post("/api/cases/{case_id}/upload")
async def api_upload_documents(case_id: int, files: list[UploadFile] = File(...)):
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    if case["status"] not in ("created", "completed", "failed"):
        raise HTTPException(400, "Cannot upload to a case that is currently processing")

    case_folder = Path(case["folder_path"])
    docs_dir = case_folder / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)

    uploaded = []
    for f in files:
        ext = Path(f.filename).suffix.lower()
        if ext not in SUPPORTED_EXTS:
            continue

        # Use unique prefix to avoid collisions
        safe_name = f"{uuid.uuid4().hex[:8]}_{f.filename}"
        file_path = docs_dir / safe_name
        content = await f.read()
        file_path.write_bytes(content)

        doc = add_document(case_id, f.filename, str(file_path))
        uploaded.append(doc)

    if not uploaded:
        raise HTTPException(400, "No valid files uploaded (supported: jpg, png, pdf)")

    return uploaded


# ── Processing ────────────────────────────────────────────────────────────────


def _run_processing(case_id: int) -> None:
    """Run the full processing pipeline (called by the queue worker thread)."""
    from src.main import process_case_from_db  # lazy import to avoid circular

    # Transition from queued to processing
    update_case_status(case_id, "processing")

    tid = threading.get_ident()
    with _log_lock:
        _thread_case_map[tid] = case_id

    cancel_event = _cancel_events.get(case_id)

    try:
        process_case_from_db(case_id, cancel_event=cancel_event)
        if cancel_event and cancel_event.is_set():
            update_case_status(case_id, "failed", "Processing stopped by user")
            print("  ⏹ Processing stopped by user.")
        else:
            update_case_status(case_id, "completed")
    except Exception as e:
        if cancel_event and cancel_event.is_set():
            update_case_status(case_id, "failed", "Processing stopped by user")
            print("  ⏹ Processing stopped by user.")
        else:
            update_case_status(case_id, "failed", str(e))
    finally:
        with _log_lock:
            _thread_case_map.pop(tid, None)
            # Clean up partial line buffer for this thread
            if isinstance(sys.stdout, _ThreadAwareCapture):
                sys.stdout._pending.pop(tid, None)
        _cancel_events.pop(case_id, None)
        # Free accumulated log lines for this case to release memory
        with _log_lock:
            _case_logs.pop(case_id, None)


def _queue_worker() -> None:
    """Background worker that processes one case at a time from the queue."""
    while True:
        case_id = _processing_queue.get()
        try:
            # Check if the case was cancelled while queued
            cancel_event = _cancel_events.get(case_id)
            if cancel_event and cancel_event.is_set():
                update_case_status(case_id, "failed", "Processing stopped by user")
                _cancel_events.pop(case_id, None)
                with _log_lock:
                    _case_logs.pop(case_id, None)
                continue
            _run_processing(case_id)
        except Exception:
            pass  # _run_processing handles its own errors
        finally:
            _processing_queue.task_done()


@app.get("/api/cases/{case_id}/logs")
def api_get_logs(case_id: int, after: int = 0):
    """Return log lines for a case starting from line index `after`."""
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    with _log_lock:
        lines = list(_case_logs.get(case_id, []))
    return {
        "lines": lines[after:],
        "total": len(lines),
        "done": case["status"] not in ("processing", "queued"),
    }


@app.post("/api/cases/{case_id}/process")
def api_process_case(case_id: int):
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    if case["status"] in ("processing", "queued"):
        raise HTTPException(400, "Case is already processing or queued")
    if not case["documents"]:
        raise HTTPException(400, "No documents uploaded yet")

    # Set status to "queued" and prepare log/cancel state
    update_case_status(case_id, "queued")
    with _log_lock:
        _case_logs[case_id] = []

    # Create a fresh cancellation event for this run
    cancel_event = threading.Event()
    _cancel_events[case_id] = cancel_event

    # Enqueue — the worker thread will pick it up
    _processing_queue.put(case_id)

    return {"ok": True, "status": "queued"}


@app.post("/api/cases/{case_id}/stop")
def api_stop_case(case_id: int):
    """Signal a running or queued processing job to stop."""
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    if case["status"] not in ("processing", "queued"):
        raise HTTPException(400, "Case is not currently processing or queued")

    cancel_event = _cancel_events.get(case_id)
    if cancel_event:
        cancel_event.set()
        return {"ok": True, "message": "Stop signal sent"}
    else:
        # No in-memory cancel event means the thread is gone (e.g. server
        # restarted while processing).  Force-reset the status so the user
        # can retry.
        update_case_status(case_id, "failed", "Processing orphaned — forced stop")
        return {"ok": True, "message": "No active thread; status reset to failed"}


# ── Document viewer ─────────────────────────────────────────────────────────


@app.get("/api/cases/{case_id}/documents/{doc_id}")
def api_serve_document(case_id: int, doc_id: int):
    """Serve an uploaded document inline for in-browser viewing."""
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")

    doc = get_document_by_id(doc_id)
    if not doc or doc["case_id"] != case_id:
        raise HTTPException(404, "Document not found")

    file_path = Path(doc["file_path"])
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "File not found on disk")

    # Security: file must reside inside the case folder
    case_folder = Path(case["folder_path"])
    if not file_path.resolve().is_relative_to(case_folder.resolve()):
        raise HTTPException(400, "Invalid file path")

    ext = file_path.suffix.lower()
    if ext == ".pdf":
        media_type = "application/pdf"
    elif ext in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
    elif ext == ".png":
        media_type = "image/png"
    else:
        media_type = "application/octet-stream"

    return FileResponse(path=str(file_path), media_type=media_type)


@app.delete("/api/cases/{case_id}/documents/{doc_id}")
def api_delete_document(case_id: int, doc_id: int):
    """Delete a single uploaded document from a case."""
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    if case["status"] in ("processing", "queued"):
        raise HTTPException(400, "Cannot delete documents while case is processing")

    doc = get_document_by_id(doc_id)
    if not doc or doc["case_id"] != case_id:
        raise HTTPException(404, "Document not found")

    # Remove the file from disk
    file_path = Path(doc["file_path"])
    if file_path.exists() and file_path.is_file():
        case_folder = Path(case["folder_path"])
        if file_path.resolve().is_relative_to(case_folder.resolve()):
            file_path.unlink()

    # Remove classified copy if it exists
    if doc.get("classified_name"):
        classified_path = (
            Path(case["folder_path"]) / "classified" / doc["classified_name"]
        )
        if classified_path.exists() and classified_path.is_file():
            classified_path.unlink()
        # Also remove sidecar JSON
        sidecar = classified_path.with_suffix(".json")
        if sidecar.exists() and sidecar.is_file():
            sidecar.unlink()

    delete_document(doc_id)
    return {"ok": True}


@app.get("/api/cases/{case_id}/extracted")
def api_get_extracted_data(case_id: int):
    """Return the extracted JSON data for a completed case."""
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")

    case_folder = Path(case["folder_path"])
    case_name = case["name"]

    # Try main extracted JSON
    json_path = case_folder / "output" / f"{case_name}_extracted.json"
    if not json_path.exists():
        raise HTTPException(404, "No extracted data available yet")

    import json as _json

    data = _json.loads(json_path.read_text(encoding="utf-8"))
    return data


@app.get("/api/cases/{case_id}/classified/{filename}")
def api_serve_classified(case_id: int, filename: str):
    """Serve a classified document inline for in-browser viewing."""
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")

    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "Invalid filename")

    classified_dir = Path(case["folder_path"]) / "classified"
    file_path = classified_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Classified file not found")

    if not file_path.resolve().is_relative_to(classified_dir.resolve()):
        raise HTTPException(400, "Invalid file path")

    ext = file_path.suffix.lower()
    if ext == ".pdf":
        media_type = "application/pdf"
    elif ext in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
    elif ext == ".png":
        media_type = "image/png"
    else:
        media_type = "application/octet-stream"

    return FileResponse(path=str(file_path), media_type=media_type)


@app.get("/api/cases/{case_id}/classified/download/zip")
def api_download_classified_zip(case_id: int):
    """Download all classified documents for a case as a ZIP archive."""
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")

    classified_dir = Path(case["folder_path"]) / "classified"
    if not classified_dir.exists() or not classified_dir.is_dir():
        raise HTTPException(404, "No classified documents found")

    files = [
        f
        for f in classified_dir.iterdir()
        if f.is_file() and f.suffix.lower() != ".json"
    ]
    if not files:
        raise HTTPException(404, "No classified documents found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            # Security: ensure file is inside classified_dir
            if not f.resolve().is_relative_to(classified_dir.resolve()):
                continue
            zf.write(f, f.name)
    buf.seek(0)

    safe_name = "".join(
        c if c.isalnum() or c in (" ", "-", "_") else "_" for c in case["name"]
    )
    filename = f"{safe_name}_classified.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Output file download ─────────────────────────────────────────────────────


@app.get("/api/cases/{case_id}/output/{filename}")
def api_download_output(case_id: int, filename: str):
    case = get_case(case_id)
    if not case:
        raise HTTPException(404, "Case not found")

    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(400, "Invalid filename")

    output_dir = Path(case["folder_path"]) / "output"
    file_path = output_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "Output file not found")

    # Verify the resolved path is within output_dir
    if not file_path.resolve().is_relative_to(output_dir.resolve()):
        raise HTTPException(400, "Invalid file path")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


# ── Serve frontend ───────────────────────────────────────────────────────────

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
