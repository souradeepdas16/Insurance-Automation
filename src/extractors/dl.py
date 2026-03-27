"""Extract data from Indian Driving License (DL)."""

from __future__ import annotations

import dataclasses

from src.types import DLData
from src.utils.ai_client import vision_extract_json

PROMPT = """Extract data from this Indian Driving License (DL).
The document may have front and back sides provided as separate images.
Extract ALL fields. If a field is not visible, use empty string "".

Respond with ONLY this JSON structure (no trailing commas, no comments, no extra text):
{"driver_name":"Full name with title","dob":"DD.MM.YYYY","address":"Street address","city_state":"City/State","country":"INDIA","licence_number":"DL number","alt_licence_number":"Alt number in parentheses","date_of_issue":"DD.MM.YYYY","valid_till":"DD.MM.YYYY","issuing_authority":"RTO name","licence_type":"Licence class"}

Rules:
- Combine data from front and back if multiple images provided.
- All dates in DD.MM.YYYY format.
- Output MUST be valid JSON. No trailing commas. No markdown. No explanation."""


def extract_dl(file_paths: list[str]) -> DLData:
    data = vision_extract_json(file_paths, PROMPT)
    return DLData(**{f.name: data.get(f.name, "") for f in dataclasses.fields(DLData)})
