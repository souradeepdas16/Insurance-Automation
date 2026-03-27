"""Insurance Automation — main entry point with watch mode and CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from threading import Timer
from dotenv import load_dotenv

from src.paths import APP_DIR

PROJECT_ROOT = APP_DIR
load_dotenv(APP_DIR / ".env")

# pylint: disable=wrong-import-position
from src.classifier import classify_document  # noqa: E402
from src.extractors.combined import (  # noqa: E402
    build_all_extracted_data,
    classify_and_extract_all,
)
from src.filler import fill_excel  # noqa: E402
from src.types import AllExtractedData  # noqa: E402

WATCH_DIR = PROJECT_ROOT / "watch"
OUTPUT_DIR = PROJECT_ROOT / "output"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
DEBOUNCE_SEC = 3.0

# Human-readable names for classified document types
DOC_TYPE_DISPLAY_NAMES: dict[str, str] = {
    "insurance_policy": "Insurance Policy",
    "registration_certificate": "Registration Certificate",
    "driving_license": "Driving License",
    "repair_estimate": "Repair Estimate",
    "final_invoice": "Final Invoice",
    "route_permit": "Route Permit",
    "fitness_certificate": "Fitness Certificate",
    "accident_document": "Accident Document",
    "survey_report": "Survey Report",
    "claim_form": "Claim Form",
    "tax_report": "Tax Report",
    "labour_charges": "Labour Charges",
    "unknown": "Extra Document",
}

# Track processing state
_case_state: dict[str, str] = {}
_debouncers: dict[str, Timer] = {}


# ─── Case processing ─────────────────────────────────────────────────────────


def process_case(
    case_dir: str,
) -> None:
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    case_name = os.path.basename(case_dir)
    print(f"\n=== Processing case: {case_name} ===")

    all_files = [
        os.path.join(case_dir, f)
        for f in sorted(os.listdir(case_dir))
        if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS
    ]

    if not all_files:
        print("  No supported files found.")
        return

    print(f"  Found {len(all_files)} file(s)")

    # ── Step 1: Classify and extract (one API call per doc, parallel) ────────
    print("  Step 1: Classifying and extracting documents...")
    grouped_data: dict[str, list[dict]] = {}
    combined_results: dict[str, dict] = {}

    try:
        combined_results = classify_and_extract_all(all_files)
    except Exception as e:  # pylint: disable=broad-except
        print(f"    ✗ Classification+extraction failed: {e}")
        return

    for file_path, result in combined_results.items():
        doc_type = result["type"]
        print(f"    {os.path.basename(file_path)} → {doc_type}")
        grouped_data.setdefault(doc_type, []).append(result["data"])

    # ── Step 2: Build AllExtractedData ────────────────────────────────────────
    all_data = build_all_extracted_data(grouped_data)

    # ── Step 3: Fill Excel ────────────────────────────────────────────────────
    print("  Step 3: Filling Excel template...")
    output_path = str(OUTPUT_DIR / f"{case_name}.xlsx")

    try:
        ref_match = re.match(r"^(\d+)", case_name)
        fill_excel(all_data, output_path, ref_match.group(1) if ref_match else None)

        json_path = str(OUTPUT_DIR / f"{case_name}_extracted.json")
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(asdict(all_data), jf, indent=2, ensure_ascii=False)

        # Per-doc sidecar JSONs
        _type_seen: dict[str, int] = {}
        for _fp, _res in combined_results.items():
            _dt = _res["type"]
            _display = DOC_TYPE_DISPLAY_NAMES.get(_dt, "Extra Document")
            _ext = os.path.splitext(_fp)[1].lower()
            if _ext in IMAGE_EXTS and _dt == "unknown":
                _display = "Extra Image"
            _type_seen[_dt] = _type_seen.get(_dt, 0) + 1
            _cnt = _type_seen[_dt]
            _stem = _display if _cnt == 1 else f"{_display} ({_cnt})"
            with open(str(OUTPUT_DIR / f"{_stem}.json"), "w", encoding="utf-8") as _jf:
                json.dump(
                    {
                        "type": _dt,
                        "display_name": _display,
                        "source_file": os.path.basename(_fp),
                        "data": _res["data"],
                    },
                    _jf,
                    indent=2,
                    ensure_ascii=False,
                )

        doc_types = [k for k in grouped_data if k != "unknown"]
        print(f"\n  ✓ DONE: {case_name}")
        print(f"    Documents : {', '.join(doc_types)}")
        print(
            f"    Parts     : {len(all_data.estimate.parts) if all_data.estimate else 0}"
        )
        print(
            f"    Labour    : {len(all_data.estimate.labour) if all_data.estimate else 0}"
        )
        print(f"    Output    : {output_path}")
        print(f"    JSON dump : {json_path}")
    except Exception as e:  # pylint: disable=broad-except
        print(f"  ✗ Excel filling failed: {e}")


# ─── DB-backed processing (used by web UI) ───────────────────────────────────


def process_case_from_db(case_id: int) -> None:
    """Process a case using database records. Renames files on classification.
    Outputs are saved inside the case folder under output/."""
    from src.database import (
        get_case,
        get_documents_by_case,
        reset_document_classifications,
        update_document_classification,
    )

    case = get_case(case_id)
    if not case:
        raise ValueError(f"Case {case_id} not found")

    case_name = case["name"]
    case_folder = Path(case["folder_path"])
    docs = get_documents_by_case(case_id)

    print(f"\n=== Processing case (DB): {case_name} ===")

    if not docs:
        raise ValueError("No documents found for this case")

    # ── Clean up previous run outputs ─────────────────────────────────────────
    for subfolder in ("output", "classified"):
        target = case_folder / subfolder
        if target.exists():
            shutil.rmtree(target)
            print(f"  Removed previous {subfolder}/")
    reset_document_classifications(case_id)

    print(f"  Found {len(docs)} document(s)")

    # ── Step 1: Classify and extract (one API call per doc, parallel) ────────
    print("  Step 1: Classifying and extracting documents...")
    grouped_data: dict[str, list[dict]] = {}
    type_counters: dict[str, int] = {}

    # Gather valid file paths
    valid_docs = []
    for doc in docs:
        file_path = doc["file_path"]
        if not os.path.exists(file_path):
            print(f"    ✗ File missing: {file_path}")
            continue
        valid_docs.append(doc)

    if not valid_docs:
        raise ValueError("No valid document files found")

    valid_paths = [d["file_path"] for d in valid_docs]

    try:
        combined_results = classify_and_extract_all(valid_paths)
    except Exception as e:
        raise RuntimeError(f"Batch classification failed: {e}") from e

    for doc in valid_docs:
        file_path = doc["file_path"]
        _res = combined_results.get(file_path, {"type": "unknown", "data": {}})
        doc_type = _res["type"]
        extracted_data = _res["data"]
        print(f"    {doc['original_name']} → {doc_type}")

        try:
            # Copy file to classified/ subfolder, e.g. insurance_policy_1.jpg
            classified_dir = case_folder / "classified"
            classified_dir.mkdir(parents=True, exist_ok=True)

            ext = Path(file_path).suffix
            type_counters[doc_type] = type_counters.get(doc_type, 0) + 1
            count = type_counters[doc_type]
            display = DOC_TYPE_DISPLAY_NAMES.get(doc_type, "Extra Document")

            # Use clean names: "Insurance Policy.jpg", "Repair Estimate (2).pdf"
            if ext.lower() in IMAGE_EXTS and doc_type == "unknown":
                display = "Extra Image"
            if count == 1:
                new_name = f"{display}{ext}"
            else:
                new_name = f"{display} ({count}){ext}"
            new_path = classified_dir / new_name

            shutil.copy2(file_path, str(new_path))

            update_document_classification(doc["id"], doc_type, new_name)

            # Write per-doc sidecar JSON alongside the classified file
            with open(
                str(classified_dir / f"{Path(new_name).stem}.json"),
                "w",
                encoding="utf-8",
            ) as _jf:
                json.dump(
                    {
                        "type": doc_type,
                        "display_name": display,
                        "source_file": doc["original_name"],
                        "data": extracted_data,
                    },
                    _jf,
                    indent=2,
                    ensure_ascii=False,
                )

            grouped_data.setdefault(doc_type, []).append(extracted_data)

        except Exception as e:  # pylint: disable=broad-except
            print(f"    ✗ Error processing {doc['original_name']}: {e}")

    # ── Step 2: Build AllExtractedData ────────────────────────────────────────
    print("  Step 2: Building extracted data...")
    all_data = build_all_extracted_data(grouped_data)

    # ── Step 3: Fill Excel (output inside case folder) ────────────────────────
    print("  Step 3: Filling Excel template...")
    output_dir = case_folder / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = str(output_dir / f"{case_name}.xlsx")

    try:
        ref_match = re.match(r"^(\d+)", case_name)
        fill_excel(all_data, output_path, ref_match.group(1) if ref_match else None)

        json_path = str(output_dir / f"{case_name}_extracted.json")
        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(asdict(all_data), jf, indent=2, ensure_ascii=False)

        doc_types = [k for k in grouped_data if k != "unknown"]
        print(f"\n  ✓ DONE: {case_name}")
        print(f"    Documents : {', '.join(doc_types)}")
        print(
            f"    Parts     : {len(all_data.estimate.parts) if all_data.estimate else 0}"
        )
        print(
            f"    Labour    : {len(all_data.estimate.labour) if all_data.estimate else 0}"
        )
        print(f"    Output    : {output_path}")
        print(f"    JSON dump : {json_path}")
    except Exception as e:
        raise RuntimeError(f"Excel filling failed: {e}") from e


# ─── Files dropped directly in watch root ────────────────────────────────────


def _process_direct_files() -> None:
    files = [
        f
        for f in os.listdir(WATCH_DIR)
        if os.path.isfile(WATCH_DIR / f)
        and os.path.splitext(f)[1].lower() in SUPPORTED_EXTS
    ]
    if not files:
        return

    case_name = f"case_{int(time.time())}"
    case_dir = WATCH_DIR / case_name
    os.makedirs(case_dir, exist_ok=True)

    for f in files:
        os.rename(WATCH_DIR / f, case_dir / f)
    print(f"\n  Moved {len(files)} file(s) → {case_name}/")
    process_case(str(case_dir))


# ─── Debounced case scheduler ─────────────────────────────────────────────────


def _schedule_case(case_dir: str) -> None:
    if case_dir in _debouncers:
        _debouncers[case_dir].cancel()

    def _run() -> None:
        _debouncers.pop(case_dir, None)
        if _case_state.get(case_dir) == "processing":
            print(f"  ⏳ {os.path.basename(case_dir)} already processing — skipped")
            return
        _case_state[case_dir] = "processing"
        try:
            process_case(case_dir)
        finally:
            _case_state[case_dir] = "done"

    t = Timer(DEBOUNCE_SEC, _run)
    _debouncers[case_dir] = t
    t.start()


# ─── Watch mode ───────────────────────────────────────────────────────────────


def start_watcher() -> None:
    from watchdog.events import FileSystemEventHandler  # noqa: PLC0415
    from watchdog.observers import Observer  # noqa: PLC0415

    os.makedirs(WATCH_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=================================")
    print(" Insurance Automation — Watcher")
    print("=================================")
    print(f"Watching : {WATCH_DIR}")
    print(f"Output   : {OUTPUT_DIR}")
    print(f"Model    : {os.environ.get('AI_MODEL', 'openai/gpt-5.4-pro')}")
    print()
    print("Create a subfolder in watch/ for each claim case and")
    print("drop all documents for that case inside it.")
    print("Processing starts 3s after the last file is added.")
    print("Press Ctrl+C to stop.\n")

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if event.is_directory:
                return
            file_path = event.src_path
            ext = os.path.splitext(file_path)[1].lower()
            if ext not in SUPPORTED_EXTS:
                return

            rel = os.path.relpath(file_path, WATCH_DIR)
            parts = rel.split(os.sep)

            if len(parts) >= 2:
                case_dir = str(WATCH_DIR / parts[0])
                print(f"  📄 {rel}")
                _schedule_case(case_dir)
            else:
                print(f"  📄 {rel} (root — will auto-create case folder)")
                if "__root__" in _debouncers:
                    _debouncers["__root__"].cancel()
                t = Timer(5.0, _process_direct_files)
                _debouncers["__root__"] = t
                t.start()

    observer = Observer()
    observer.schedule(Handler(), str(WATCH_DIR), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down watcher...")
        observer.stop()
    observer.join()


# ─── CLI entry point ──────────────────────────────────────────────────────────


def main() -> None:
    args = sys.argv[1:]

    if len(args) >= 2 and args[0] == "--process":
        target = os.path.abspath(args[1])
        process_case(target)
    else:
        start_watcher()


if __name__ == "__main__":
    main()
