"""Extract data from Indian vehicle Registration Certificate (RC)."""

from __future__ import annotations

import dataclasses

from src.types import RCData
from src.utils.ai_client import vision_extract_json

PROMPT = """Extract data from this Indian vehicle Registration Certificate (RC).
The document may have front and back sides provided as separate images.
Extract ALL fields. If a field is not visible, use empty string "".

Respond with ONLY this JSON structure (no trailing commas, no comments, no extra text):
{"registration_number":"HR 20AY 7179","date_of_reg_issue":"DD.MM.YYYY","date_of_reg_expiry":"DD.MM.YYYY","chassis_number":"Last 6 digits","engine_number":"Last 6 digits or full","make_year":"MAKE MODEL/YEAR","body_type":"Saloon","vehicle_class":"LMVCAR","laden_weight":"Weight in Kg","unladen_weight":"1277 Kg","seating_capacity":5,"fuel_type":"Petrol","colour":"White","road_tax_paid_upto":"Date or LTT","registered_owner":"Owner name with title","cubic_capacity":999,"hpa_with":"Bank/financier name if hypothecated"}

Rules:
- Combine data from both sides of RC if multiple images provided.
- Dates in DD.MM.YYYY format.
- Numeric fields (seating_capacity, cubic_capacity) should be plain numbers.
- hpa_with: name of the bank or financier shown in the Hypothecation/HPA field; use "" if not present.
- Output MUST be valid JSON. No trailing commas. No markdown. No explanation."""


def extract_rc(file_paths: list[str]) -> RCData:
    data = vision_extract_json(file_paths, PROMPT)
    return RCData(**{f.name: data.get(f.name, "") for f in dataclasses.fields(RCData)})
