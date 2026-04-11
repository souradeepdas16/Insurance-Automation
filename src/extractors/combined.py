"""Classify and extract data from a document in a single API call per document."""

from __future__ import annotations

import dataclasses
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


class ProcessingCancelledError(Exception):
    """Raised when the user stops processing."""


from src.types import (
    AllExtractedData,
    ClaimFormData,
    DLData,
    EstimateData,
    EstimatePart,
    FitnessCertData,
    InvoiceData,
    InvoicePart,
    InsuranceData,
    LabourItem,
    RCData,
    RoutePermitData,
    VehicleImageData,
)
from src.utils.ai_client import (
    MAX_PAGES_PER_CALL,
    pdf_pages_to_base64,
    vision_extract_json,
    vision_extract_json_from_images,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

VALID_TYPES = (
    "insurance_policy",
    "registration_certificate",
    "driving_license",
    "repair_estimate",
    "final_invoice",
    "route_permit",
    "fitness_certificate",
    "accident_document",
    "survey_report",
    "claim_form",
    "tax_report",
    "labour_charges",
    "vehicle_image",
    "towing_bill",
    "unknown",
)

PER_DOC_PROMPT = """You are a document classifier and data extractor for Indian vehicle insurance claims.

IMPORTANT — A single file (PDF/image) may contain MULTIPLE different document types
(e.g. a driving license and registration certificate scanned together).
You MUST detect ALL document types present and extract data for each one separately.

Step 1 — For EACH distinct document found, identify its type from this list:
insurance_policy | registration_certificate | driving_license | repair_estimate |
final_invoice | route_permit | fitness_certificate | accident_document |
survey_report | claim_form | tax_report | labour_charges | towing_bill | unknown

CRITICAL — How to distinguish repair_estimate from final_invoice from towing_bill:
• Check the document TITLE / HEADER first:
  - "Estimate", "Quotation", "Service Quotation", "Proforma" → repair_estimate
  - "Tax Invoice", "Invoice", "Bill", "Final Bill" → final_invoice (ONLY for workshop/dealer repair bills)
  - "Towing", "Tow", "Crane", "Recovery", "Towing Bill", "Towing Charges" → towing_bill
• repair_estimate may still show CGST/UGST columns — that does NOT make it an invoice.
  The TITLE is the deciding factor.
• A "Quotation No." or "Estimate No." field → repair_estimate.
  A "GST Invc No." or "Invoice No." field → final_invoice.
• A document about towing/crane/vehicle recovery charges is ALWAYS towing_bill, NEVER final_invoice.

Step 2 — Extract the relevant fields for each detected document type.
Use "" for missing text fields, 0 for missing numeric fields.
All dates must be in DD.MM.YYYY format. All prices as plain numbers (no commas, no ₹).

Return a JSON object with a "documents" array. Each item has "type", "pages", and "data".
• "pages" = array of 1-based page numbers that belong to this document (e.g. [1,2] for pages 1-2).
  For images (single page), always use [1].
• If the file contains only ONE document type, still return the "documents" array with one item.

Format:
{"documents":[{"type":"<type>","pages":[1],"data":{<fields>}},{"type":"<type>","pages":[2,3],"data":{<fields>}}]}

━━━ SCHEMAS BY TYPE ━━━

insurance_policy (vehicle insurance policy / cover note):
{"type":"insurance_policy","pages":[1],"data":{"insurer_name":"","insurer_address":"","policy_number":"","policy_period":"DD.MM.YYYY to DD.MM.YYYY","idv":0,"insured_name":"","insured_address":"","contact_number":""}}
• idv is a plain integer (e.g. 1320000, NOT "13,20,000")

registration_certificate (vehicle RC / registration certificate):
{"type":"registration_certificate","pages":[1],"data":{"registration_number":"","date_of_reg_issue":"DD.MM.YYYY","date_of_reg_expiry":"DD.MM.YYYY","chassis_number":"last 6 digits","engine_number":"last 6 or full","make_year":"MAKE MODEL/YEAR","body_type":"","vehicle_class":"","laden_weight":"","unladen_weight":"","seating_capacity":0,"road_tax_paid_upto":"","fuel_type":"","colour":"","cubic_capacity":0,"hpa_with":""}}
• If front+back are both visible on separate pages, combine fields from both sides into ONE entry with both page numbers.
• hpa_with: name of the bank or financier shown in the Hypothecation/HPA field; use "" if not present.

driving_license (driving licence / DL):
{"type":"driving_license","pages":[1],"data":{"driver_name":"","dob":"DD.MM.YYYY","address":"","city_state":"","licence_number":"","alt_licence_number":"","date_of_issue":"DD.MM.YYYY","valid_till":"DD.MM.YYYY","issuing_authority":"","licence_type":""}}

repair_estimate (repair estimate / quotation / service quotation / proforma — header says "Estimate" or "Quotation"):
{"type":"repair_estimate","pages":[1],"data":{"parts":[{"sn":1,"name":"Part Name","estimated_price":0.0,"category":"metal"}],"labour":[{"sn":1,"description":"Labour description","rr":0,"denting":0,"cw":0,"painting":0}],"total_labour_estimated":0.0,"dealer_name":"","dealer_address":""}}
• Extract ALL parts (up to 50+). category must be "metal", "plastic", or "glass":
  - metal: panels, brackets, bolts, hinges, sensors, structural parts, washers, nuts
  - plastic: bumpers, trim, claddings, spoilers, reflectors, foam
  - glass: windshield, window glass, mirror glass, headlamp glass, tail lamp lens
• labour breakdown: rr=R/R (Remove/Refit), denting=Denting, cw=Cutting/Welding, painting=Painting

final_invoice (final repair bill / tax invoice — header says "Tax Invoice" or "Invoice", has GST Invc No.):
{"type":"final_invoice","pages":[1],"data":{"parts_assessed":[{"name":"Part Name","assessed_price":0.0}],"labour_assessed_total":0.0,"dealer_name":"","dealer_address":""}}
• Extract ALL parts. Use base price before GST if GST is shown separately.

route_permit (route permit / goods permit / passenger permit):
{"type":"route_permit","pages":[1],"data":{"permit_no":"","permit_holder_name":"","valid_upto":"DD.MM.YYYY","type_of_permit":"","route_area":""}}
• valid_upto = permit validity end date. type_of_permit = service type (e.g. Goods Service). route_area = region/route covered.

fitness_certificate (fitness certificate / vehicle fitness):
{"type":"fitness_certificate","pages":[1],"data":{"valid_upto":"DD.MM.YYYY"}}
• valid_upto = fitness certificate validity end date.

claim_form (insurance claim form filled by insured / claim intimation form):
{"type":"claim_form","pages":[1],"data":{"date_of_accident":"DD.MM.YYYY","place_of_accident":""}}
• date_of_accident = date of accident/loss as mentioned in the claim form.
• place_of_accident = place/location of accident/loss as mentioned in the claim form.

vehicle_image (vehicle damage photos / claim photos / survey photos with visible date):
{"type":"vehicle_image","pages":[1],"data":{"date_of_survey":"DD.MM.YYYY"}}
• date_of_survey = the date visible or stamped on the vehicle photo (e.g. date overlay, timestamp). Use "" if no date is visible.

towing_bill (towing charges / towing bill / crane charges / vehicle recovery bill):
{"type":"towing_bill","pages":[1],"data":{}}

accident_document | survey_report | tax_report | labour_charges | unknown:
{"type":"<detected_type>","pages":[1],"data":{}}

━━━ RULES ━━━
• If multiple DIFFERENT document types are in the same file, return a separate entry for each.
• If the same document type spans multiple pages (e.g. RC front+back), combine into ONE entry with all page numbers.
• Choose the MOST specific matching type.
• Output MUST be valid JSON. No markdown fences. No trailing commas. No explanation."""


# ─── Type validation ──────────────────────────────────────────────────────────


def _clean_type(raw: Any) -> str:
    t = re.sub(r"[^a-z_]", "", str(raw).lower().strip())
    return t if t in VALID_TYPES else "unknown"


# ─── Merge helpers ────────────────────────────────────────────────────────────


def _merge_simple(data_list: list[dict]) -> dict:
    """Merge flat dicts: last non-empty / non-zero value per field wins.

    Used for insurance, RC, DL where multiple images may cover the same document.
    """
    merged: dict[str, Any] = {}
    for d in data_list:
        for k, v in d.items():
            if v not in ("", None, 0, 0.0):
                merged[k] = v
    return merged


def _merge_lists(data_list: list[dict]) -> dict:
    """Merge estimate/invoice dicts: concatenate arrays, last wins for scalars.

    sn keys are stripped so the builder re-numbers cleanly from enumerate.
    """
    merged: dict[str, Any] = {}
    for d in data_list:
        for k, v in d.items():
            if isinstance(v, list):
                bucket = merged.setdefault(k, [])
                for item in v:
                    bucket.append({kk: vv for kk, vv in item.items() if kk != "sn"})
            elif v not in ("", None, 0, 0.0):
                merged[k] = v
    return merged


# ─── Data-class builders ──────────────────────────────────────────────────────


def _build_insurance(data: dict) -> InsuranceData:
    return InsuranceData(
        **{f.name: data.get(f.name, "") for f in dataclasses.fields(InsuranceData)}
    )


def _build_rc(data: dict) -> RCData:
    return RCData(**{f.name: data.get(f.name, "") for f in dataclasses.fields(RCData)})


def _build_dl(data: dict) -> DLData:
    return DLData(**{f.name: data.get(f.name, "") for f in dataclasses.fields(DLData)})


def _build_estimate(data: dict) -> EstimateData:
    parts = [
        EstimatePart(
            sn=i + 1,
            name=p.get("name", ""),
            estimated_price=float(p.get("estimated_price", 0)),
            category=p.get("category", ""),
        )
        for i, p in enumerate(data.get("parts", []))
    ]
    labour = [
        LabourItem(
            sn=i + 1,
            description=lv.get("description", ""),
            rr=float(lv.get("rr", 0)),
            denting=float(lv.get("denting", 0)),
            cw=float(lv.get("cw", 0)),
            painting=float(lv.get("painting", 0)),
        )
        for i, lv in enumerate(data.get("labour", []))
    ]
    return EstimateData(
        parts=parts,
        labour=labour,
        total_labour_estimated=float(data.get("total_labour_estimated", 0)),
        dealer_name=data.get("dealer_name", ""),
        dealer_address=data.get("dealer_address", ""),
        estimate_date=data.get("estimate_date", ""),
        estimate_number=data.get("estimate_number", ""),
    )


def _build_invoice(data: dict) -> InvoiceData:
    parts = [
        InvoicePart(
            name=p.get("name", ""),
            assessed_price=float(p.get("assessed_price", 0)),
        )
        for p in data.get("parts_assessed", [])
    ]
    return InvoiceData(
        parts_assessed=parts,
        labour_assessed_total=float(data.get("labour_assessed_total", 0)),
        invoice_number=data.get("invoice_number", ""),
        invoice_date=data.get("invoice_date", ""),
        dealer_name=data.get("dealer_name", ""),
        dealer_address=data.get("dealer_address", ""),
        total_amount=float(data.get("total_amount", 0)),
        gst_amount=float(data.get("gst_amount", 0)),
    )


def _build_route_permit(data: dict) -> RoutePermitData:
    return RoutePermitData(
        permit_no=data.get("permit_no", ""),
        permit_holder_name=data.get("permit_holder_name", ""),
        valid_upto=data.get("valid_upto", "") or data.get("validity_to_date", ""),
        type_of_permit=data.get("type_of_permit", "") or data.get("service_type", ""),
        route_area=data.get("route_area", "") or data.get("region_covered", ""),
    )


def _build_fitness_cert(data: dict) -> FitnessCertData:
    return FitnessCertData(
        valid_upto=data.get("valid_upto", ""),
    )


def _build_claim_form(data: dict) -> ClaimFormData:
    return ClaimFormData(
        date_of_accident=data.get("date_of_accident", ""),
        place_of_accident=data.get("place_of_accident", ""),
    )


def _build_vehicle_image(data: dict) -> VehicleImageData:
    return VehicleImageData(
        date_of_survey=data.get("date_of_survey", ""),
    )


def build_all_extracted_data(grouped: dict[str, list[dict]]) -> AllExtractedData:
    """Assemble AllExtractedData by merging per-doc results grouped by type.

    For insurance/RC/DL: last non-empty value per field wins (multi-page support).
    For estimate/invoice: parts/labour arrays are concatenated across pages.
    """
    all_data = AllExtractedData()

    if "insurance_policy" in grouped:
        all_data.insurance = _build_insurance(
            _merge_simple(grouped["insurance_policy"])
        )

    if "registration_certificate" in grouped:
        all_data.rc = _build_rc(_merge_simple(grouped["registration_certificate"]))

    if "driving_license" in grouped:
        all_data.dl = _build_dl(_merge_simple(grouped["driving_license"]))

    if "repair_estimate" in grouped:
        all_data.estimate = _build_estimate(_merge_lists(grouped["repair_estimate"]))

    if "final_invoice" in grouped:
        all_data.invoice = _build_invoice(_merge_lists(grouped["final_invoice"]))

    if "route_permit" in grouped:
        all_data.route_permit = _build_route_permit(
            _merge_simple(grouped["route_permit"])
        )

    if "fitness_certificate" in grouped:
        all_data.fitness_cert = _build_fitness_cert(
            _merge_simple(grouped["fitness_certificate"])
        )

    if "claim_form" in grouped:
        all_data.claim_form = _build_claim_form(_merge_simple(grouped["claim_form"]))

    if "vehicle_image" in grouped:
        all_data.vehicle_image = _build_vehicle_image(
            _merge_simple(grouped["vehicle_image"])
        )

    return all_data


# ─── Single-doc classify+extract ─────────────────────────────────────────────


# Gemini 2.5 Flash supports up to 65536 output tokens.
# Estimates with 50+ parts need ~3000+ tokens — use the full budget.
_MAX_OUTPUT_TOKENS = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "65536"))


def _parse_doc_results(raw: dict) -> list[dict[str, Any]]:
    """Parse API response into a list of document result dicts."""
    if "documents" in raw and isinstance(raw["documents"], list):
        results = []
        for doc in raw["documents"]:
            doc_type = _clean_type(doc.get("type", "unknown"))
            pages = doc.get("pages", [1])
            results.append(
                {"type": doc_type, "pages": pages, "data": doc.get("data", {})}
            )
        return results if results else [{"type": "unknown", "pages": [1], "data": {}}]

    # Backward compat: old single-doc format {"type": ..., "data": ...}
    doc_type = _clean_type(raw.get("type", "unknown"))
    return [{"type": doc_type, "pages": [1], "data": raw.get("data", {})}]


def _call_with_retry(
    call_fn, file_label: str, cancel_event: threading.Event | None = None
) -> list[dict[str, Any]]:
    """Call call_fn() up to 3 times, retrying on errors. Returns parsed doc list."""
    last_exc: Exception | None = None
    for attempt in range(3):
        if cancel_event and cancel_event.is_set():
            raise ProcessingCancelledError("Processing stopped by user")
        try:
            raw = call_fn()
            return _parse_doc_results(raw)
        except (ValueError, Exception) as exc:
            last_exc = exc
            if attempt < 2:
                import json as _json

                if (
                    isinstance(exc, _json.JSONDecodeError)
                    or "Unterminated" in str(exc)
                    or "json" in type(exc).__name__.lower()
                ):
                    print(
                        f"    ⚠ JSON parse error on attempt {attempt + 1}/3 for {file_label}: {exc} — retrying..."
                    )
                else:
                    print(
                        f"    ⚠ Error on attempt {attempt + 1}/3 for {file_label}: {exc} — retrying..."
                    )
            else:
                raise last_exc
    raise last_exc  # unreachable, but keeps type checkers happy


def classify_and_extract_single(
    file_path: str, cancel_event: threading.Event | None = None
) -> list[dict[str, Any]]:
    """Classify and extract a single document file.

    For images: one API call.
    For PDFs with <= MAX_PAGES_PER_CALL pages: one API call.
    For PDFs with > MAX_PAGES_PER_CALL pages: split into chunks,
      one API call per chunk, then merge results with corrected page numbers.

    Returns a list of {"type": "...", "pages": [...], "data": {...}} dicts.
    """
    if cancel_event and cancel_event.is_set():
        raise ProcessingCancelledError("Processing stopped by user")

    file_label = os.path.basename(file_path)
    ext = Path(file_path).suffix.lower()

    # Prepend the original filename to the prompt as a secondary hint
    prompt_with_filename = f'Original filename (use as a hint only, always prioritise the actual document content for classification): "{file_label}"\n\n{PER_DOC_PROMPT}'

    # ── Images or small PDFs — single call ────────────────────────────────────────
    if ext in IMAGE_EXTS:
        return _call_with_retry(
            lambda: vision_extract_json(
                [file_path], prompt_with_filename, max_output_tokens=_MAX_OUTPUT_TOKENS
            ),
            file_label,
            cancel_event,
        )

    # ── PDF — render pages, then decide if chunking is needed ─────────────────
    all_pages_b64 = pdf_pages_to_base64(file_path)
    total_pages = len(all_pages_b64)

    if total_pages <= MAX_PAGES_PER_CALL:
        # Small PDF — single call (pass the file directly)
        return _call_with_retry(
            lambda: vision_extract_json(
                [file_path], prompt_with_filename, max_output_tokens=_MAX_OUTPUT_TOKENS
            ),
            file_label,
            cancel_event,
        )

    # ── Large PDF — chunk pages and make multiple calls ───────────────────────
    print(
        f"    📄 {file_label}: {total_pages} pages → splitting into chunks of {MAX_PAGES_PER_CALL}"
    )
    all_results: list[dict[str, Any]] = []

    for chunk_start in range(0, total_pages, MAX_PAGES_PER_CALL):
        if cancel_event and cancel_event.is_set():
            raise ProcessingCancelledError("Processing stopped by user")

        chunk_end = min(chunk_start + MAX_PAGES_PER_CALL, total_pages)
        chunk_b64 = all_pages_b64[chunk_start:chunk_end]
        page_offset = chunk_start  # 0-based offset for this chunk

        chunk_label = f"{file_label} pages {chunk_start + 1}-{chunk_end}"
        print(f"      → Calling API for {chunk_label}")

        chunk_results = _call_with_retry(
            lambda _b64=chunk_b64, _lbl=chunk_label: vision_extract_json_from_images(
                _b64,
                PER_DOC_PROMPT,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                label=_lbl,
            ),
            chunk_label,
            cancel_event,
        )

        # Adjust page numbers: the AI returns 1-based pages relative to the chunk,
        # but we need 1-based pages relative to the full PDF.
        for doc in chunk_results:
            doc["pages"] = [p + page_offset for p in doc.get("pages", [1])]

        all_results.extend(chunk_results)

    # ── Merge documents that span chunk boundaries ────────────────────────────
    # If the same doc type appears at the end of one chunk and start of the next,
    # they might be the same document. Merge consecutive same-type entries.
    merged: list[dict[str, Any]] = []
    for doc in all_results:
        if (
            merged
            and merged[-1]["type"] == doc["type"]
            and merged[-1]["type"] != "unknown"
        ):
            # Same type as previous — merge pages and data
            merged[-1]["pages"].extend(doc["pages"])
            # For list fields (parts, labour), concatenate; for scalar fields, last wins
            prev_data = merged[-1]["data"]
            for k, v in doc["data"].items():
                if isinstance(v, list) and isinstance(prev_data.get(k), list):
                    prev_data[k].extend(v)
                elif v not in ("", None, 0, 0.0):
                    prev_data[k] = v
        else:
            merged.append(doc)

    return merged if merged else [{"type": "unknown", "pages": [1], "data": {}}]


# ─── Parallel batch ───────────────────────────────────────────────────────────


def classify_and_extract_all(
    file_paths: list[str],
    cancel_event: threading.Event | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Classify and extract all documents in parallel (one API call per file).

    Returns {file_path: [{"type": "...", "pages": [...], "data": {...}}, ...]}
    A single file may produce multiple document entries if it contains mixed types.
    """
    results: dict[str, list[dict[str, Any]]] = {}

    with ThreadPoolExecutor(max_workers=5) as pool:
        future_to_path = {
            pool.submit(classify_and_extract_single, fp, cancel_event): fp
            for fp in file_paths
        }
        for future in as_completed(future_to_path):
            fp = future_to_path[future]
            try:
                results[fp] = future.result()
            except ProcessingCancelledError:
                # Cancel remaining futures
                for f in future_to_path:
                    f.cancel()
                raise
            except Exception as e:  # pylint: disable=broad-except
                print(f"    ✗ classify+extract failed for {fp}: {e}")
                results[fp] = [{"type": "unknown", "pages": [1], "data": {}}]

    return results
