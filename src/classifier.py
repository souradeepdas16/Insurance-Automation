"""Document classifier — identifies document type via GPT Vision."""

from __future__ import annotations

import re

from typing import get_args

from src.types import DocumentType
from src.utils.ai_client import vision_extract_json_labeled, vision_request

VALID_TYPES: tuple[str, ...] = get_args(DocumentType)

CLASSIFY_PROMPT = """You are a document classifier for Indian vehicle insurance claims.
Look at this document image and identify its type.

Return ONLY one of these exact strings (nothing else):
- insurance_policy (vehicle insurance policy / cover note)
- registration_certificate (vehicle RC / registration certificate)
- driving_license (driving licence / DL)
- repair_estimate (repair estimate / quotation / proforma from workshop/dealer — look for words like "Estimate", "Quotation", "Proforma", "Repair Estimate", "Estimated Cost"; these do NOT have GST/tax breakdowns or payment details; prices shown are estimated/projected, not final)
- final_invoice (final repair bill / tax invoice from workshop/dealer — look for words like "Tax Invoice", "Invoice", "Bill", "Final Bill"; these typically include GST/CGST/SGST breakdowns, invoice number, and payment details; prices are actual/final amounts charged)
- route_permit (route permit document)
- fitness_certificate (fitness certificate)
- accident_document (FIR / accident report / police report / panchnama)
- survey_report (surveyor report / inspection report / loss assessment)
- claim_form (insurance claim form filled by insured)
- tax_report (road tax receipt / tax challan / tax payment document)
- labour_charges (standalone labour charges / labour bill / labour detail sheet)
- unknown (if none of the above)

IMPORTANT: To distinguish repair_estimate from final_invoice, check the document TITLE/HEADER carefully.
If the header says "Estimate", "Quotation", or "Proforma" → repair_estimate.
If the header says "Tax Invoice", "Invoice", or "Bill" → final_invoice.

Return ONLY the type string, no explanation."""


BATCH_CLASSIFY_PROMPT = """You are a document classifier for Indian vehicle insurance claims.
Each image/PDF above is labeled [file_1], [file_2], etc.
For EACH file, identify its document type.

Valid types (use ONLY these exact strings):
- insurance_policy
- registration_certificate
- driving_license
- repair_estimate (look for "Estimate", "Quotation", "Proforma" in the title/header — estimated/projected costs, NO GST breakdown)
- final_invoice (look for "Tax Invoice", "Invoice", "Bill" in the title/header — actual amounts with GST/CGST/SGST breakdown)
- route_permit
- fitness_certificate
- accident_document (FIR / accident report / police report / panchnama)
- survey_report (surveyor report / inspection report / loss assessment)
- claim_form (insurance claim form filled by insured)
- tax_report (road tax receipt / tax challan / tax payment document)
- labour_charges (standalone labour charges / labour bill / labour detail sheet)
- unknown

IMPORTANT: To distinguish repair_estimate from final_invoice, check the document TITLE/HEADER carefully.
If the header says "Estimate", "Quotation", or "Proforma" → repair_estimate.
If the header says "Tax Invoice", "Invoice", or "Bill" → final_invoice.

Return a JSON object mapping each file label to its type, e.g.:
{"file_1": "insurance_policy", "file_2": "registration_certificate", "file_3": "driving_license"}

Return ONLY the JSON object."""


NAME_UNKNOWN_PROMPT = """You are a document identifier for Indian vehicle insurance claims.
Look at this document/image and give it a short, descriptive name (2-4 words) that describes what it is.

Examples of good names: "Aadhar Card", "PAN Card", "Bank Statement", "Vehicle Photo", "Damage Photos", "Cancelled Cheque", "Passport", "Address Proof", "NOC Letter", "Payment Receipt", "Towing Bill", "Police Complaint", "Medical Report", "Salvage Photos"

Rules:
- Return ONLY the short name, nothing else
- Use Title Case
- Keep it 2-4 words maximum
- Be specific about what the document/image shows
- Do NOT return generic names like "Document", "Image", "Paper", "File"
- For photos of vehicle damage, use "Damage Photos"
- For photos of the vehicle (no damage visible), use "Vehicle Photos"
- For any ID card or certificate not in the standard list, name it specifically (e.g. "Voter ID Card", "Aadhar Card")"""


def name_unknown_document(file_path: str) -> str:
    """Use AI to generate a descriptive name for an unclassified document."""
    try:
        response = vision_request([file_path], NAME_UNKNOWN_PROMPT)
        name = response.strip().strip('"').strip("'").strip()
        # Sanitize: remove characters not safe for filenames
        name = re.sub(r'[<>:"/\\|?*]', "", name)
        # Limit length and ensure non-empty
        name = name[:60].strip()
        if not name or name.lower() in (
            "document",
            "image",
            "unknown",
            "file",
            "paper",
        ):
            return ""
        return name
    except Exception as e:
        print(f"  ⚠ Could not name unknown document {file_path}: {e}")
        return ""


def classify_document(file_path: str) -> DocumentType:
    """Classify a single document file (fallback for one-off use)."""
    response = vision_request([file_path], CLASSIFY_PROMPT)
    doc_type = re.sub(r"[^a-z_]", "", response.lower().strip())

    if doc_type in VALID_TYPES:
        return doc_type  # type: ignore[return-value]

    print(
        f'  ⚠  Unknown classification "{response}" for {file_path}, defaulting to "unknown"'
    )
    return "unknown"


def classify_documents_batch(
    file_paths: list[str],
) -> dict[str, DocumentType]:
    """Classify ALL files in a single API call.

    Returns {file_path: document_type} for every input file.
    """
    if not file_paths:
        return {}

    # Build labeled list: [("file_1", path), ("file_2", path), ...]
    labels = [f"file_{i + 1}" for i in range(len(file_paths))]
    labeled = list(zip(labels, file_paths))

    data = vision_extract_json_labeled(labeled, BATCH_CLASSIFY_PROMPT)

    result: dict[str, DocumentType] = {}
    for i, file_path in enumerate(file_paths):
        label = labels[i]
        raw = data.get(label, "unknown")
        doc_type = re.sub(r"[^a-z_]", "", str(raw).lower().strip())
        if doc_type not in VALID_TYPES:
            print(
                f'  ⚠  Unknown classification "{raw}" for {file_path}, '
                f'defaulting to "unknown"'
            )
            doc_type = "unknown"
        result[file_path] = doc_type  # type: ignore[assignment]

    return result
