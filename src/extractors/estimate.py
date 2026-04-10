"""Extract data from vehicle Repair Estimate / Quotation."""

from __future__ import annotations

import re

from src.types import EstimateData, EstimatePart, LabourItem
from src.utils.ai_client import vision_extract_json

# Words that indicate a labour item, not a part
_LABOUR_KEYWORDS_RE = re.compile(
    r"\b(paint|painting|remove|removal|refit|refitting|replace|replacement|r/r|denting|c/w|cutting|welding)\b",
    re.IGNORECASE,
)

# fmt: off
# pylint: disable=line-too-long
PROMPT = (
    """Extract data from this vehicle Repair Estimate / Quotation from an Indian automobile dealer or workshop.
The document may have multiple pages provided as separate images.

Extract TWO sections:

1. PARTS: Every part listed with its name and estimated price.
   For each part, also determine if it is "metal", "plastic", or "glass" based on the part name:
   - Metal: brackets, bolts, panels, structural parts, hinges, sensors, washers, nuts
   - Plastic: bumper covers, claddings, trim pieces, spoilers, reflectors, foam
   - Glass: windshield, window glass, mirror glass, headlamp glass, tail lamp lens

2. LABOUR: Every labour line item with its description and cost breakdown if available.
   Labour categories: R/R (Remove/Refit), Denting, C/W (Cutting/Welding), Painting.
   If the estimate only shows a total labour cost per item, put it under the most appropriate category.

Respond with ONLY this JSON structure (no trailing commas, no comments, no extra text):
{"parts":[{"sn":1,"name":"Part Name","estimated_price":1234.56,"category":"metal"}],"labour":[{"sn":1,"description":"Labour description","rr":0,"denting":0,"cw":0,"painting":0}],"total_labour_estimated":12345.67,"dealer_name":"Name","dealer_address":"Address","estimate_date":"DD.MM.YYYY","estimate_number":"Number"}

Rules:
- Extract ALL parts, even if there are many (up to 50+).
- Prices must be plain numbers (no commas, no currency symbols).
- Serial numbers (sn) start from 1.
- IMPORTANT: Items whose description contains words like paint, painting, remove, removal, refit, refitting, replace, replacement, R/R, denting, C/W are LABOUR items, NOT parts. Do NOT include them in the parts list. They belong in the labour list only.
- Output MUST be valid JSON. No trailing commas. No markdown. No explanation."""
)


def extract_estimate(file_paths: list[str]) -> EstimateData:
    data = vision_extract_json(file_paths, PROMPT, max_output_tokens=16384)
    parts = []
    labour_from_parts = 0.0  # cost of items filtered out of parts (labour-like)
    for i, p in enumerate(data.get("parts", [])):
        if _LABOUR_KEYWORDS_RE.search(p.get("name", "")):
            labour_from_parts += float(p.get("estimated_price", 0))
        else:
            parts.append(
                EstimatePart(
                    sn=p.get("sn", i + 1),
                    name=p.get("name", ""),
                    estimated_price=float(p.get("estimated_price", 0)),
                    category=p.get("category", ""),
                )
            )
    labour = [
        LabourItem(
            sn=l.get("sn", i + 1),
            description=l.get("description", ""),
            rr=float(l.get("rr", 0)),
            denting=float(l.get("denting", 0)),
            cw=float(l.get("cw", 0)),
            painting=float(l.get("painting", 0)),
        )
        for i, l in enumerate(data.get("labour", []))
    ]
    total_labour = float(data.get("total_labour_estimated", 0)) + labour_from_parts
    return EstimateData(
        parts=parts,
        labour=labour,
        total_labour_estimated=total_labour,
        dealer_name=data.get("dealer_name", ""),
        dealer_address=data.get("dealer_address", ""),
        estimate_date=data.get("estimate_date", ""),
        estimate_number=data.get("estimate_number", ""),
    )
