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

Extract every line item from the invoice. For each item, determine whether it is a PART or LABOUR.

Respond with ONLY this JSON structure (no trailing commas, no comments, no extra text):
{"items":[{"name":"Item Name","price":1234.56,"type":"part"}],"invoice_number":"Number","invoice_date":"DD.MM.YYYY","dealer_name":"Name","dealer_address":"Address","total_amount":12345.67,"gst_amount":1234.56}

Rules:
- Extract ALL line items from the invoice.
- Prices must be plain numbers (no commas, no currency symbols).
- If prices include GST, extract base price (before GST).
- For each item, set "type" to "part" or "labour".
- SECTION-BASED classification: If the invoice has sections/headings like "Labour Charges", "Labour Details", "Labour", "Service Charges", "Repair Charges", "Job Charges" etc., then ALL items listed under that section are LABOUR regardless of their individual names. Similarly items under "Parts", "Spare Parts", "Parts Replaced" sections are PARTS.
- KEYWORD-BASED classification: Even outside a labour section, items whose description contains words like paint, painting, remove, removal, refit, refitting, replace, replacement, R/R, denting, C/W, cutting, welding are LABOUR items.
- When both section heading and keywords conflict, the SECTION heading takes priority.
- Output MUST be valid JSON. No trailing commas. No markdown. No explanation."""
)


def extract_invoice(file_paths: list[str]) -> InvoiceData:
    data = vision_extract_json(file_paths, PROMPT, max_output_tokens=16384)
    parts = []
    total_labour = 0.0

    for item in data.get("items", []):
        name = item.get("name", "")
        price = float(item.get("price", 0))
        item_type = item.get("type", "part").lower().strip()

        # Trust AI's section-based classification first;
        # fall back to keyword check for items tagged as "part"
        is_labour = item_type == "labour" or _LABOUR_KEYWORDS_RE.search(name)

        if is_labour:
            total_labour += price
        else:
            parts.append(InvoicePart(name=name, assessed_price=price))

    # Backward-compat: also accept old format if AI returns it
    for p in data.get("parts_assessed", []):
        name = p.get("name", "")
        price = float(p.get("assessed_price", 0))
        if _LABOUR_KEYWORDS_RE.search(name):
            total_labour += price
        else:
            parts.append(InvoicePart(name=name, assessed_price=price))
    total_labour += float(data.get("labour_assessed_total", 0))

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
