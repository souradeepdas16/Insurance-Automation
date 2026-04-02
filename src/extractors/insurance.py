"""Extract data from Indian vehicle Insurance Policy documents."""

from __future__ import annotations

import dataclasses

from src.types import InsuranceData
from src.utils.ai_client import vision_extract_json

PROMPT = """Extract data from this Indian vehicle Insurance Policy document.
Extract ALL fields from the document image(s). If a field is not visible, use empty string "".

Respond with ONLY this JSON structure (no trailing commas, no comments, no extra text):
{"insurer_name":"Insurance company name","insurer_address":"Branch address","policy_number":"Policy number","policy_period":"22.03.2025 to 21.03.2026","idv":1320000,"insured_name":"Insured name with title","insured_address":"Full address","contact_number":"Phone number","tp_policy_number":"TP policy number"}

Rules:
- IDV must be a plain number (e.g. 1320000, not "13,20,000").
- All dates in DD.MM.YYYY format.
- Output MUST be valid JSON. No trailing commas. No markdown. No explanation."""


def extract_insurance(file_paths: list[str]) -> InsuranceData:
    data = vision_extract_json(file_paths, PROMPT)
    return InsuranceData(
        **{f.name: data.get(f.name, "") for f in dataclasses.fields(InsuranceData)}
    )
