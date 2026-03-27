"""Classify and extract data from a document in a single API call per document."""

from __future__ import annotations

import dataclasses
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.types import (
    AllExtractedData,
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
)
from src.utils.ai_client import vision_extract_json

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
    "unknown",
)

PER_DOC_PROMPT = """You are a document classifier and data extractor for Indian vehicle insurance claims.

Step 1 — Identify the document type from this list:
insurance_policy | registration_certificate | driving_license | repair_estimate |
final_invoice | route_permit | fitness_certificate | accident_document |
survey_report | claim_form | tax_report | labour_charges | unknown

Step 2 — Extract the relevant fields for that type.
Use "" for missing text fields, 0 for missing numeric fields.
All dates must be in DD.MM.YYYY format. All prices as plain numbers (no commas, no ₹).

Return ONLY a single JSON object in exactly this format:
{"type":"<document_type>","data":{<fields>}}

━━━ SCHEMAS BY TYPE ━━━

insurance_policy (vehicle insurance policy / cover note):
{"type":"insurance_policy","data":{"insurer_name":"","insurer_address":"","policy_number":"","policy_period":"DD.MM.YYYY to DD.MM.YYYY","idv":0,"insured_name":"","insured_address":"","contact_number":"","hpa_with":""}}
• idv is a plain integer (e.g. 1320000, NOT "13,20,000")

registration_certificate (vehicle RC / registration certificate):
{"type":"registration_certificate","data":{"registration_number":"","date_of_reg_issue":"DD.MM.YYYY","date_of_reg_expiry":"DD.MM.YYYY","chassis_number":"last 6 digits","engine_number":"last 6 or full","make_year":"MAKE MODEL/YEAR","body_type":"","vehicle_class":"","laden_weight":"","unladen_weight":"","seating_capacity":0,"road_tax_paid_upto":"","fuel_type":"","colour":"","cubic_capacity":0}}
• If front+back are both visible, combine fields from both sides.

driving_license (driving licence / DL):
{"type":"driving_license","data":{"driver_name":"","dob":"DD.MM.YYYY","address":"","city_state":"","licence_number":"","alt_licence_number":"","date_of_issue":"DD.MM.YYYY","valid_till":"DD.MM.YYYY","issuing_authority":"","licence_type":""}}

repair_estimate (repair estimate / quotation from workshop or dealer):
{"type":"repair_estimate","data":{"parts":[{"sn":1,"name":"Part Name","estimated_price":0.0,"category":"metal"}],"labour":[{"sn":1,"description":"Labour description","rr":0,"denting":0,"cw":0,"painting":0}],"total_labour_estimated":0.0,"dealer_name":"","dealer_address":""}}
• Extract ALL parts (up to 50+). category must be "metal", "plastic", or "glass":
  - metal: panels, brackets, bolts, hinges, sensors, structural parts, washers, nuts
  - plastic: bumpers, trim, claddings, spoilers, reflectors, foam
  - glass: windshield, window glass, mirror glass, headlamp glass, tail lamp lens
• labour breakdown: rr=R/R (Remove/Refit), denting=Denting, cw=Cutting/Welding, painting=Painting

final_invoice (final repair bill / invoice from workshop or dealer):
{"type":"final_invoice","data":{"parts_assessed":[{"name":"Part Name","assessed_price":0.0}],"labour_assessed_total":0.0,"dealer_name":"","dealer_address":""}}
• Extract ALL parts. Use base price before GST if GST is shown separately.

route_permit (route permit / goods permit / passenger permit):
{"type":"route_permit","data":{"permit_no":"","permit_holder_name":"","valid_upto":"DD.MM.YYYY","type_of_permit":"","route_area":""}}
• valid_upto = permit validity end date. type_of_permit = service type (e.g. Goods Service). route_area = region/route covered.

fitness_certificate (fitness certificate / vehicle fitness):
{"type":"fitness_certificate","data":{"valid_upto":"DD.MM.YYYY"}}
• valid_upto = fitness certificate validity end date.

accident_document | survey_report | claim_form | tax_report | labour_charges | unknown:
{"type":"<detected_type>","data":{}}

━━━ RULES ━━━
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

    return all_data


# ─── Single-doc classify+extract ─────────────────────────────────────────────


# Gemini 2.5 Flash supports up to 65536 output tokens.
# Estimates with 50+ parts need ~3000+ tokens — use the full budget.
_MAX_OUTPUT_TOKENS = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "65536"))


def classify_and_extract_single(file_path: str) -> dict[str, Any]:
    """Classify and extract a single document in one API call.

    Returns {"type": "...", "data": {...}}
    Retries up to 2 times on JSON parse errors (e.g. truncated/malformed output).
    """
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            raw = vision_extract_json(
                [file_path], PER_DOC_PROMPT, max_output_tokens=_MAX_OUTPUT_TOKENS
            )
            doc_type = _clean_type(raw.get("type", "unknown"))
            return {"type": doc_type, "data": raw.get("data", {})}
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
                        f"    ⚠ JSON parse error on attempt {attempt + 1}/3 for {os.path.basename(file_path)}: {exc} — retrying..."
                    )
                else:
                    # Non-parse errors (e.g. network) — still retry
                    print(
                        f"    ⚠ Error on attempt {attempt + 1}/3 for {os.path.basename(file_path)}: {exc} — retrying..."
                    )
            else:
                raise last_exc


# ─── Parallel batch ───────────────────────────────────────────────────────────


def classify_and_extract_all(
    file_paths: list[str],
) -> dict[str, dict[str, Any]]:
    """Classify and extract all documents in parallel (one API call per doc).

    Returns {file_path: {"type": "...", "data": {...}}}
    """
    results: dict[str, dict[str, Any]] = {}

    with ThreadPoolExecutor(max_workers=5) as pool:
        future_to_path = {
            pool.submit(classify_and_extract_single, fp): fp for fp in file_paths
        }
        for future in as_completed(future_to_path):
            fp = future_to_path[future]
            try:
                results[fp] = future.result()
            except Exception as e:  # pylint: disable=broad-except
                print(f"    ✗ classify+extract failed for {fp}: {e}")
                results[fp] = {"type": "unknown", "data": {}}

    return results
