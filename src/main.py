"""Insurance Automation — main entry point with watch mode and CLI."""

from __future__ import annotations

import gc
import json
import os
import re
import shutil
import sys
import threading
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
from src.classifier import classify_document, name_unknown_document  # noqa: E402
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


def _merge_files_to_pdf(file_paths: list[str], output_path: str) -> None:
    """Merge multiple image/PDF files into a single PDF."""
    import io

    from PIL import Image
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()

    for fp in file_paths:
        ext = Path(fp).suffix.lower()
        if ext in IMAGE_EXTS:
            with Image.open(fp) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="PDF")
                buf.seek(0)
                reader = PdfReader(buf)
                for page in reader.pages:
                    writer.add_page(page)
        elif ext == ".pdf":
            reader = PdfReader(fp)
            for page in reader.pages:
                writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)
    writer.close()


def _extract_pdf_pages(pdf_path: str, pages: list[int], output_path: str) -> None:
    """Extract specific pages (1-based) from a PDF into a new PDF file."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for page_num in pages:
        idx = page_num - 1  # convert to 0-based
        if 0 <= idx < len(reader.pages):
            writer.add_page(reader.pages[idx])
    with open(output_path, "wb") as f:
        writer.write(f)
    writer.close()


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
    "vehicle_image": "Vehicle Image",
    "towing_bill": "Towing Bill",
    "aadhar_card": "Aadhar Card",
    "pan_card": "PAN Card",
    "discharge_voucher": "Discharge Voucher",
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

    for file_path, doc_list in combined_results.items():
        for doc_result in doc_list:
            doc_type = doc_result["type"]
            pages = doc_result.get("pages", [1])
            print(f"    {os.path.basename(file_path)} → {doc_type} (pages {pages})")
            grouped_data.setdefault(doc_type, []).append(doc_result["data"])

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
        for _fp, _doc_list in combined_results.items():
            for _res in _doc_list:
                _dt = _res["type"]
                _display = DOC_TYPE_DISPLAY_NAMES.get(_dt, "Extra Document")
                _ext = os.path.splitext(_fp)[1].lower()
                if _dt == "unknown":
                    _ai_name = name_unknown_document(_fp)
                    if _ai_name:
                        _display = _ai_name
                    elif _ext in IMAGE_EXTS:
                        _display = "Extra Image"
                _type_seen[_dt] = _type_seen.get(_dt, 0) + 1
                _cnt = _type_seen[_dt]
                _stem = _display if _cnt == 1 else f"{_display} ({_cnt})"
                with open(
                    str(OUTPUT_DIR / f"{_stem}.json"), "w", encoding="utf-8"
                ) as _jf:
                    json.dump(
                        {
                            "type": _dt,
                            "display_name": _display,
                            "source_file": os.path.basename(_fp),
                            "pages": _res.get("pages", [1]),
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


def process_case_from_db(
    case_id: int, cancel_event: threading.Event | None = None
) -> None:
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
        combined_results = classify_and_extract_all(
            valid_paths, cancel_event=cancel_event
        )
    except Exception as e:
        raise RuntimeError(f"Batch classification failed: {e}") from e

    # Build a flat list: (doc_db_record, doc_type, pages, extracted_data, source_file_path)
    classified_items: list[tuple[dict, str, list[int], dict, str]] = []

    for doc in valid_docs:
        file_path = doc["file_path"]
        doc_list = combined_results.get(
            file_path, [{"type": "unknown", "pages": [1], "data": {}}]
        )
        for doc_result in doc_list:
            doc_type = doc_result["type"]
            pages = doc_result.get("pages", [1])
            extracted_data = doc_result["data"]
            multi = len(doc_list) > 1
            if multi:
                print(f"    {doc['original_name']} → {doc_type} (pages {pages})")
            else:
                print(f"    {doc['original_name']} → {doc_type}")
            grouped_data.setdefault(doc_type, []).append(extracted_data)
            classified_items.append((doc, doc_type, pages, extracted_data, file_path))

    # ── Step 1b: Create classified/ with split/merged PDFs per type ──────────
    classified_dir = case_folder / "classified"
    classified_dir.mkdir(parents=True, exist_ok=True)

    # Group classified_items by doc_type
    type_to_items: dict[str, list[tuple[dict, list[int], dict, str]]] = {}
    for doc, doc_type, pages, extracted_data, file_path in classified_items:
        type_to_items.setdefault(doc_type, []).append(
            (doc, pages, extracted_data, file_path)
        )

    for doc_type, items in type_to_items.items():
        display = DOC_TYPE_DISPLAY_NAMES.get(doc_type, "Extra Document")

        if len(items) > 1 and doc_type != "unknown":
            # Multiple entries of same type (could be from different files or same split file)
            # → merge all relevant pages into one PDF
            all_source_paths: list[tuple[str, list[int]]] = [
                (fp, pgs) for (_, pgs, _, fp) in items
            ]
            source_names = list(
                dict.fromkeys(d["original_name"] for d, _, _, _ in items)
            )
            new_name = f"{display}.pdf"
            new_path = classified_dir / new_name

            try:
                _merge_files_to_pdf([fp for fp, _ in all_source_paths], str(new_path))
                print(f"    ✓ Merged {len(items)} {display} file(s) → {new_name}")
            except Exception as e:  # pylint: disable=broad-except
                print(f"    ✗ Merge failed for {display}: {e}, copying individually")
                for i, (doc, pages, extracted_data, fp) in enumerate(items, 1):
                    ext = Path(fp).suffix
                    fallback_name = (
                        f"{display}{ext}" if i == 1 else f"{display} ({i}){ext}"
                    )
                    shutil.copy2(fp, str(classified_dir / fallback_name))
                    update_document_classification(doc["id"], doc_type, fallback_name)
                continue

            for doc, _, _, _ in items:
                update_document_classification(doc["id"], doc_type, new_name)

            all_data_for_type = [ed for (_, _, ed, _) in items]
            with open(
                str(classified_dir / f"{display}.json"), "w", encoding="utf-8"
            ) as _jf:
                json.dump(
                    {
                        "type": doc_type,
                        "display_name": display,
                        "source_files": source_names,
                        "data": all_data_for_type,
                    },
                    _jf,
                    indent=2,
                    ensure_ascii=False,
                )
        else:
            # Single entry or unknown type → name each, then merge by same AI name
            if doc_type == "unknown" and len(items) > 1:
                # AI-name each unknown doc, group by name, merge same-name docs
                named_items: dict[str, list[tuple[dict, list[int], dict, str]]] = {}
                for doc, pages, extracted_data, fp in items:
                    ext = Path(fp).suffix.lower()
                    ai_name = name_unknown_document(fp)
                    if ai_name:
                        d = ai_name
                    elif ext in IMAGE_EXTS:
                        d = "Extra Image"
                    else:
                        d = display
                    named_items.setdefault(d, []).append(
                        (doc, pages, extracted_data, fp)
                    )

                _name_count: dict[str, int] = {}
                for d, sub_items in named_items.items():
                    if len(sub_items) > 1:
                        # Multiple unknown docs with same AI name → merge into PDF
                        new_name = f"{d}.pdf"
                        new_path = classified_dir / new_name
                        try:
                            _merge_files_to_pdf(
                                [fp for (_, _, _, fp) in sub_items], str(new_path)
                            )
                            print(
                                f"    ✓ Merged {len(sub_items)} {d} file(s) → {new_name}"
                            )
                        except Exception as e:  # pylint: disable=broad-except
                            print(
                                f"    ✗ Merge failed for {d}: {e}, copying individually"
                            )
                            for j, (doc, pages, extracted_data, fp) in enumerate(
                                sub_items, 1
                            ):
                                ext = Path(fp).suffix
                                fallback = f"{d}{ext}" if j == 1 else f"{d} ({j}){ext}"
                                shutil.copy2(fp, str(classified_dir / fallback))
                                update_document_classification(
                                    doc["id"], doc_type, fallback
                                )
                            continue

                        for doc, _, _, _ in sub_items:
                            update_document_classification(
                                doc["id"], doc_type, new_name
                            )

                        source_names = list(
                            dict.fromkeys(
                                doc["original_name"] for doc, _, _, _ in sub_items
                            )
                        )
                        with open(
                            str(classified_dir / f"{d}.json"), "w", encoding="utf-8"
                        ) as _jf:
                            json.dump(
                                {
                                    "type": doc_type,
                                    "display_name": d,
                                    "source_files": source_names,
                                    "data": [ed for (_, _, ed, _) in sub_items],
                                },
                                _jf,
                                indent=2,
                                ensure_ascii=False,
                            )
                    else:
                        # Single unknown doc with this name → copy as-is
                        doc, pages, extracted_data, fp = sub_items[0]
                        ext = Path(fp).suffix.lower()
                        _name_count[d] = _name_count.get(d, 0) + 1
                        cnt = _name_count[d]
                        if ext == ".pdf" and pages:
                            new_name = f"{d}.pdf" if cnt == 1 else f"{d} ({cnt}).pdf"
                            try:
                                _extract_pdf_pages(
                                    fp, pages, str(classified_dir / new_name)
                                )
                            except Exception as e:  # pylint: disable=broad-except
                                print(
                                    f"    ✗ Error processing {doc['original_name']}: {e}"
                                )
                                continue
                        else:
                            new_name = f"{d}{ext}" if cnt == 1 else f"{d} ({cnt}){ext}"
                            shutil.copy2(fp, str(classified_dir / new_name))

                        update_document_classification(doc["id"], doc_type, new_name)
                        with open(
                            str(classified_dir / f"{Path(new_name).stem}.json"),
                            "w",
                            encoding="utf-8",
                        ) as _jf:
                            json.dump(
                                {
                                    "type": doc_type,
                                    "display_name": d,
                                    "source_file": doc["original_name"],
                                    "pages": pages,
                                    "data": extracted_data,
                                },
                                _jf,
                                indent=2,
                                ensure_ascii=False,
                            )
            else:
                # Single entry for this type (known or unknown)
                _name_count: dict[str, int] = {}
                for i, (doc, pages, extracted_data, fp) in enumerate(items, 1):
                    ext = Path(fp).suffix.lower()
                    d = display
                    if doc_type == "unknown":
                        ai_name = name_unknown_document(fp)
                        if ai_name:
                            d = ai_name
                        elif ext in IMAGE_EXTS:
                            d = "Extra Image"
                    _name_count[d] = _name_count.get(d, 0) + 1
                    cnt = _name_count[d]
                    new_name = f"{d}.pdf" if cnt == 1 else f"{d} ({cnt}).pdf"

                    try:
                        # For PDFs with page info, extract only the relevant pages
                        if ext == ".pdf" and pages:
                            _extract_pdf_pages(
                                fp, pages, str(classified_dir / new_name)
                            )
                        else:
                            # Images or fallback: just copy (keep original extension)
                            new_name = f"{d}{ext}" if cnt == 1 else f"{d} ({cnt}){ext}"
                            shutil.copy2(fp, str(classified_dir / new_name))

                        update_document_classification(doc["id"], doc_type, new_name)

                        with open(
                            str(classified_dir / f"{Path(new_name).stem}.json"),
                            "w",
                            encoding="utf-8",
                        ) as _jf:
                            json.dump(
                                {
                                    "type": doc_type,
                                    "display_name": d,
                                    "source_file": doc["original_name"],
                                    "pages": pages,
                                    "data": extracted_data,
                                },
                                _jf,
                                indent=2,
                                ensure_ascii=False,
                            )

                        if len(pages) > 0 and ext == ".pdf":
                            print(
                                f"    ✓ {doc['original_name']} pages {pages} → {new_name}"
                            )
                    except Exception as e:  # pylint: disable=broad-except
                        print(f"    ✗ Error processing {doc['original_name']}: {e}")

    # # ── Step 2: Build AllExtractedData ────────────────────────────────────────
    # if cancel_event and cancel_event.is_set():
    #     return
    # print("  Step 2: Building extracted data...")
    # all_data = build_all_extracted_data(grouped_data)

    # # ── Step 3: Fill Excel (output inside case folder) ────────────────────────
    # if cancel_event and cancel_event.is_set():
    #     return
    # print("  Step 3: Filling Excel template...")
    # output_dir = case_folder / "output"
    # output_dir.mkdir(parents=True, exist_ok=True)

    # output_path = str(output_dir / f"{case_name}.xlsx")

    # try:
    #     ref_match = re.match(r"^(\d+)", case_name)
    #     fill_excel(all_data, output_path, ref_match.group(1) if ref_match else None)

    #     json_path = str(output_dir / f"{case_name}_extracted.json")
    #     with open(json_path, "w", encoding="utf-8") as jf:
    #         json.dump(asdict(all_data), jf, indent=2, ensure_ascii=False)

    #     doc_types = [k for k in grouped_data if k != "unknown"]
    #     print(f"\n  ✓ DONE: {case_name}")
    #     print(f"    Documents : {', '.join(doc_types)}")
    #     print(
    #         f"    Parts     : {len(all_data.estimate.parts) if all_data.estimate else 0}"
    #     )
    #     print(
    #         f"    Labour    : {len(all_data.estimate.labour) if all_data.estimate else 0}"
    #     )
    #     print(f"    Output    : {output_path}")
    #     print(f"    JSON dump : {json_path}")
    # except Exception as e:
    #     raise RuntimeError(f"Excel filling failed: {e}") from e
    # finally:
    #     # Release large intermediate objects and force garbage collection
    #     combined_results = None  # noqa: F841
    #     grouped_data = None  # noqa: F841
    #     classified_items = None  # noqa: F841
    #     all_data = None  # noqa: F841
    #     gc.collect()

    print(f"\n  ✓ Classification complete for: {case_name}")
    doc_types = [k for k in grouped_data if k != "unknown"]
    print(f"    Documents : {', '.join(doc_types)}")


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
    from src.utils.ai_client import _get_model

    print(" Insurance Automation — Watcher")
    print("=================================")
    print(f"Watching : {WATCH_DIR}")
    print(f"Output   : {OUTPUT_DIR}")
    print(f"Model    : {_get_model()}")
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
