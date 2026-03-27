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
- repair_estimate (repair estimate / quotation from workshop/dealer)
- final_invoice (final repair bill / invoice from workshop/dealer)
- route_permit (route permit document)
- fitness_certificate (fitness certificate)
- accident_document (FIR / accident report / police report / panchnama)
- survey_report (surveyor report / inspection report / loss assessment)
- claim_form (insurance claim form filled by insured)
- tax_report (road tax receipt / tax challan / tax payment document)
- labour_charges (standalone labour charges / labour bill / labour detail sheet)
- unknown (if none of the above)

Return ONLY the type string, no explanation."""


BATCH_CLASSIFY_PROMPT = """You are a document classifier for Indian vehicle insurance claims.
Each image/PDF above is labeled [file_1], [file_2], etc.
For EACH file, identify its document type.

Valid types (use ONLY these exact strings):
- insurance_policy
- registration_certificate
- driving_license
- repair_estimate
- final_invoice
- route_permit
- fitness_certificate
- accident_document (FIR / accident report / police report / panchnama)
- survey_report (surveyor report / inspection report / loss assessment)
- claim_form (insurance claim form filled by insured)
- tax_report (road tax receipt / tax challan / tax payment document)
- labour_charges (standalone labour charges / labour bill / labour detail sheet)
- unknown

Return a JSON object mapping each file label to its type, e.g.:
{"file_1": "insurance_policy", "file_2": "registration_certificate", "file_3": "driving_license"}

Return ONLY the JSON object."""


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
