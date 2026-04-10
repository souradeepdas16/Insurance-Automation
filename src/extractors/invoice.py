"""Extract data from Final Repair Invoice / Bill."""

from __future__ import annotations

import re

from src.types import InvoiceData, InvoicePart
from src.utils.ai_client import vision_extract_json

# Words that indicate a labour item, not a part
_LABOUR_KEYWORDS_RE = re.compile(
    r"\b(paint|painting|remove|removal|refit|refitting|replace|replacement|r/r|denting|c/w|cutting|welding)\b",
    re.IGNORECASE,
)

# fmt: off
# pylint: disable=line-too-long
PROMPT = (
    """Extract data from this Final Repair Invoice / Bill from an Indian automobile dealer or workshop.
The document may have multiple pages provided as separate images.

This invoice represents the ACTUAL parts replaced and labour done (which may differ from the original estimate).

Extract:
1. PARTS: Every part in the invoice with its name and final billed price.
2. LABOUR: The total labour amount billed.

Respond with ONLY this JSON structure (no trailing commas, no comments, no extra text):
{"parts_assessed":[{"name":"Part Name","assessed_price":1234.56}],"labour_assessed_total":12345.67,"invoice_number":"Number","invoice_date":"DD.MM.YYYY","dealer_name":"Name","dealer_address":"Address","total_amount":12345.67,"gst_amount":1234.56}

Rules:
- Extract ALL parts from the invoice.
- Prices must be plain numbers (no commas, no currency symbols).
- If prices include GST, extract base price (before GST).
- IMPORTANT: Items whose description contains words like paint, painting, remove, removal, refit, refitting, replace, replacement, R/R, denting, C/W are LABOUR items, NOT parts. Do NOT include them in the parts list.
- Output MUST be valid JSON. No trailing commas. No markdown. No explanation."""
)


def extract_invoice(file_paths: list[str]) -> InvoiceData:
    data = vision_extract_json(file_paths, PROMPT, max_output_tokens=16384)
    parts = []
    labour_from_parts = 0.0  # cost of items filtered out of parts (labour-like)
    for p in data.get("parts_assessed", []):
        if _LABOUR_KEYWORDS_RE.search(p.get("name", "")):
            labour_from_parts += float(p.get("assessed_price", 0))
        else:
            parts.append(
                InvoicePart(
                    name=p.get("name", ""),
                    assessed_price=float(p.get("assessed_price", 0)),
                )
            )
    total_labour = float(data.get("labour_assessed_total", 0)) + labour_from_parts
    return InvoiceData(
        parts_assessed=parts,
        labour_assessed_total=total_labour,
        invoice_number=data.get("invoice_number", ""),
        invoice_date=data.get("invoice_date", ""),
        dealer_name=data.get("dealer_name", ""),
        dealer_address=data.get("dealer_address", ""),
        total_amount=float(data.get("total_amount", 0)),
        gst_amount=float(data.get("gst_amount", 0)),
    )
