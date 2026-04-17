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
    AccidentDocData,
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
    SurveyReportData,
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
    "aadhar_card",
    "pan_card",
    "discharge_voucher",
    "kyc_form",
    "unknown",
)

PER_DOC_PROMPT = """You are a document classifier and data extractor for Indian vehicle insurance claims.

IMPORTANT — A single file (PDF/image) may contain MULTIPLE different document types
(e.g. a driving license and registration certificate scanned together).
You MUST detect ALL document types present and extract data for each one separately.

HOWEVER — For a single-page image (JPEG/PNG), there is almost always ONLY ONE document.
Do NOT return multiple types for the same single image unless you can clearly see two
PHYSICALLY SEPARATE documents scanned together in that one image (e.g. a DL card AND an
RC card side by side). If the image shows ONE document, return exactly ONE entry.
Never guess a second type — only report what you can actually see.

Step 1 — For EACH distinct document found, identify its type from this list:
insurance_policy | registration_certificate | driving_license | repair_estimate |
final_invoice | route_permit | fitness_certificate | accident_document |
survey_report | claim_form | tax_report | labour_charges | towing_bill |
aadhar_card | pan_card | discharge_voucher | kyc_form | unknown

━━━ CRITICAL — HOW TO DISTINGUISH EACH DOCUMENT TYPE ━━━

★★★ FUNDAMENTAL RULE ★★★
Classify a document by WHAT IT PHYSICALLY IS — not by what it MENTIONS or REFERS TO.
Many documents reference other documents' details (e.g. an affidavit may quote an Aadhaar
number, a DL number, a policy number, and an accident — but it is still just an AFFIDAVIT,
not an aadhar_card, not a driving_license, not an insurance_policy, not a claim_form).
Always ask: "What TYPE of document am I looking at?" — not "What information does it contain?"

▶ insurance_policy — The actual vehicle insurance policy document or cover note ISSUED BY an insurance company.
  IT IS: A formal policy schedule / certificate printed on insurance company letterhead.
  LOOK FOR: "Policy Schedule", "Certificate of Insurance", "Cover Note", policy number, IDV
  (Insured Declared Value), premium amount, coverage period, insured's name, vehicle details.
  IT IS NOT: Any document that merely quotes a policy number. An affidavit or letter mentioning
  a policy number is NOT an insurance_policy.

▶ registration_certificate — The actual government-issued vehicle RC card (smart card or paper form).
  IT IS: A physical card or Form 23 issued by the State Transport Authority for a specific vehicle.
  LOOK FOR: "Registration Certificate" / "Form 23" header, transport authority emblem,
  registration number (e.g. HR03N4949), chassis number, engine number, owner name,
  vehicle class (LMV/HMV/MCWG), fuel type, maker's name, body type.
  IT IS NOT: Any document that merely mentions a registration number or vehicle details.
  An affidavit quoting a vehicle reg number is NOT an RC.
  ★ A VAHAN portal "RC STATUS" printout (online verification page) is NOT a registration_certificate.
    VAHAN printouts show "RC STATUS" header, VAHAN/National Register e-Services branding,
    and sections like Validity, Insurance Details, Permit Details → classify as fitness_certificate.

▶ driving_license — The actual government-issued driving licence (DL) card.
  IT IS: A physical government ID CARD with the holder's photo, issued by a transport authority.
  LOOK FOR: "DRIVING LICENCE" / "DRIVING LICENSE" header, "UNION OF INDIA" or state transport
  logo, PHOTO of the holder printed on the card, licence number, Date of Birth,
  vehicle class table (LMV/MCWG/HMV), "Date of Issue", "Valid Till", compact card format.
  IT IS NOT: Any document that merely mentions a DL number. An affidavit or claim form stating
  "Driving License No. HR-03..." is NOT a driving_license — it is whatever document it physically is.

▶ aadhar_card — The actual physical Aadhaar identity card issued by UIDAI.
  IT IS: A printed card/letter from UIDAI with the holder's PHOTO, biometric ID, and QR code.
  LOOK FOR: "UIDAI" logo printed on the card, "Aadhaar" / "आधार" as the card title,
  12-digit Aadhaar number displayed prominently AS THE CARD'S OWN NUMBER, holder's photograph
  printed on the card, enrolment number, QR code, government of India emblem.
  IT IS NOT: Any document that merely mentions an Aadhaar number in its text. An affidavit
  stating "Aadhar Card No. 8376 1640 5083" is NOT an aadhar_card — it is an affidavit.
  A claim form asking for Aadhaar details is NOT an aadhar_card.

▶ pan_card — The actual physical PAN identity card issued by the Income Tax Department.
  IT IS: A laminated card with the holder's PHOTO, printed by NSDL/UTIITSL.
  LOOK FOR: "INCOME TAX DEPARTMENT" / "GOVT. OF INDIA" printed on the card, "Permanent Account
  Number" as the card title, 10-character alphanumeric PAN (e.g. ABCDE1234F) displayed prominently,
  holder's photograph on the card, signature, hologram.
  IT IS NOT: Any document that mentions a PAN number. A bank form or affidavit quoting a PAN
  number is NOT a pan_card.

▶ claim_form — The actual insurance claim form / claim intimation form — a PRINTED FORM with
  BLANK FIELDS or SECTIONS TO BE FILLED by the insured and submitted to the insurance company.
  IT IS: A structured form (often pre-printed by the insurance company) with labeled sections
  like "Date of Accident", "Place of Accident", "Description of Loss", "Driver Details",
  "Policy Number", checkboxes, and a declaration section. Usually has insurance company branding.
  LOOK FOR: "CLAIM FORM" / "Claim Intimation" / "Motor Claim Form" as the title,
  insurance company letterhead/logo, structured fields/boxes to fill in, tabular layout.
  IT IS NOT: An affidavit, a letter, a sworn statement, or any narrative document that
  merely describes an accident. If the document header says "AFFIDAVIT" — it is NOT a claim_form.
  IT IS NOT: A driving license, Aadhaar card, PAN card, or any identity document.
  IT IS NOT: An FIR or police report (those are accident_document).

▶ repair_estimate — Repair estimate / quotation / proforma from a garage or dealer.
  IT IS: A workshop/dealer document listing parts and labour with ESTIMATED prices BEFORE repair.
  LOOK FOR: Header says "Estimate", "Quotation", "Service Quotation", "Proforma".
  Has "Quotation No." or "Estimate No." field. Lists parts with estimated prices.
  May show CGST/UGST columns — that does NOT make it an invoice. The TITLE decides.
  IT IS NOT: A final_invoice (estimate is BEFORE repair; invoice is AFTER repair).

▶ final_invoice — Final repair bill / tax invoice from workshop/dealer AFTER repair is done.
  IT IS: A workshop/dealer bill issued AFTER repairs are completed, with final prices and GST.
  LOOK FOR: Header says "Tax Invoice", "Invoice", "Bill", "Final Bill".
  Has "GST Invc No." or "Invoice No." field. Lists parts with final assessed prices + GST.
  ONLY for workshop/dealer repair bills — NOT for towing charges.
  IT IS NOT: A repair_estimate (invoice is AFTER repair; estimate is BEFORE).

▶ towing_bill — Bill for towing / crane / vehicle recovery charges.
  IT IS: A bill or receipt specifically for vehicle towing, crane hire, or recovery services.
  LOOK FOR: "Towing", "Tow", "Crane", "Recovery", "Towing Bill", "Towing Charges",
  "Vehicle Recovery", "Crane Charges" in header or body.
  A document about towing/crane/vehicle recovery charges is ALWAYS towing_bill,
  NEVER final_invoice, NEVER repair_estimate.

▶ route_permit — The actual government-issued permit document for a vehicle to ply on routes.
  IT IS: A permit certificate issued by RTO/transport authority for a specific vehicle.
  LOOK FOR: "Route Permit", "Goods Permit", "Passenger Permit", "National Permit",
  permit number, permit holder name, route/area, validity period, RTO stamp.
  IT IS NOT: A fitness_certificate or registration_certificate.

▶ fitness_certificate — The actual government certificate confirming a vehicle is roadworthy,
  OR a VAHAN portal "RC STATUS" printout showing vehicle fitness/registration validity.
  IT IS: A certificate issued by RTO/transport authority after vehicle inspection,
  OR an online VAHAN (National Register e-Services) RC Status page.
  LOOK FOR: "Fitness Certificate", "Certificate of Fitness", validity date ("Valid Upto"),
  OR "RC STATUS" header with VAHAN/parivahan.gov.in branding, Fitness/REGN validity,
  PUCC validity, Insurance Details section, Permit Details section.
  IT IS NOT: A route_permit or registration_certificate.
  ★ VAHAN RC Status printouts → fitness_certificate (NOT registration_certificate).

▶ accident_document — FIR, police report, or any official police/incident report about the accident.
  IT IS: An official document FROM THE POLICE or authorities about the accident.
  LOOK FOR: "FIR", "First Information Report", "Police Report", "Accident Report",
  "General Diary", "DDR", police station name, IO (Investigating Officer) name, FIR number.
  IT IS NOT: A claim_form (FIR is from POLICE; claim form is an INSURANCE company form).
  IT IS NOT: An affidavit (an affidavit is a sworn personal statement, not a police report).

▶ survey_report — Report prepared by a surveyor/assessor inspecting vehicle damage.
  IT IS: A professional assessment report by a licensed surveyor appointed by the insurer.
  LOOK FOR: "Survey Report", "Surveyor Report", "Assessment Report", surveyor name & licence,
  damage assessment details, photographs, recommended repair amounts.
  IT IS NOT: A repair_estimate (survey report is by an independent surveyor; estimate is from the garage).

▶ tax_report — Tax-related report or receipt for the vehicle (road tax, token tax).
  IT IS: A tax payment document or receipt from a government authority.
  LOOK FOR: "Tax Report", "Road Tax", "Tax Receipt", "Token Tax", tax payment details.
  IT IS NOT: A final_invoice, NOT a pan_card.

▶ labour_charges — Standalone labour charges document (separate from estimate/invoice).
  IT IS: A document listing ONLY labour charges without a parts list.
  LOOK FOR: Labour-only breakdown (denting, painting, welding, R&R charges) WITHOUT parts.
  If the document also has parts → it is likely a repair_estimate or final_invoice instead.

▶ vehicle_image — Photograph(s) of the vehicle showing damage, taken during claim/survey.
  IT IS: An actual PHOTOGRAPH of a physical vehicle — not a document scan.
  LOOK FOR: Photo of a car/truck/bike, visible damage, date/time overlay or timestamp watermark.
  IT IS NOT: A scanned text document. If it has text, headers, or form fields, it is NOT this.

▶ discharge_voucher — Discharge/satisfaction voucher signed by insured after claim settlement.
  IT IS: A post-settlement document where the insured acknowledges receiving the claim amount.
  LOOK FOR: "Discharge Voucher", "Satisfaction Voucher", "Final Discharge", "Full & Final
  Settlement", "No Claim Voucher", settlement amount, insured's declaration of no further claims.
  IT IS NOT: A claim_form (discharge is AFTER settlement; claim is BEFORE/AT filing).

▶ kyc_form — Know Your Customer form / KYC document / customer verification form.
  IT IS: A KYC form used for identity/address verification, typically required by insurers.
  LOOK FOR: "KYC", "Know Your Customer", "Customer Verification", "Identity Verification Form",
  customer details fields (name, address, ID proof, photo, signature).
  IT IS NOT: An individual ID card (aadhar_card, pan_card) — those are standalone ID documents.
  A KYC form may reference Aadhaar/PAN numbers but is a SEPARATE verification form.

▶ unknown — ONLY if the document does NOT match ANY of the above types.
  Use this as a last resort. Provide a short 2-4 word descriptive name.
  EXAMPLES of unknown documents: Affidavit, Bank Statement, Cancelled Cheque, Voter ID,
  NOC Letter, Consent Letter, Legal Notice, Ownership Transfer, Payment Receipt.
  An AFFIDAVIT (sworn notarized statement) does not match any of the above types → classify
  as unknown with name "Affidavit".

━━━ KEY NEGATIVE RULES ━━━
• CLASSIFY BY WHAT THE DOCUMENT PHYSICALLY IS — not by what information it contains or references.
• A document that MENTIONS an Aadhaar number is NOT automatically an aadhar_card.
  Only the actual UIDAI-issued card with photo and QR code is an aadhar_card.
• A document that MENTIONS a DL number is NOT automatically a driving_license.
  Only the actual government-issued DL card with photo is a driving_license.
• A document that MENTIONS a PAN number is NOT automatically a pan_card.
  Only the actual Income Tax Dept card with photo is a pan_card.
• A document that MENTIONS a policy number is NOT automatically an insurance_policy.
  Only the actual policy schedule from the insurer is an insurance_policy.
• A document that MENTIONS an accident is NOT automatically a claim_form.
  Only the actual insurance company claim form (structured form with fields) is a claim_form.
• An AFFIDAVIT is a sworn notarized statement — it is ALWAYS "unknown" with name "Affidavit",
  even if it mentions Aadhaar, DL, policy, accident, or vehicle details.
• An FIR / police report → accident_document, NOT claim_form.
• A surveyor's damage assessment → survey_report, NOT repair_estimate.
• A towing/crane/recovery bill → towing_bill, NEVER final_invoice.

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
{"type":"driving_license","pages":[1],"data":{"driver_name":"","dob":"DD.MM.YYYY","address":"","city_state":"","licence_number":"","alt_licence_number":"","date_of_issue":"DD.MM.YYYY","valid_till":"DD.MM.YYYY","valid_till_nt":"DD.MM.YYYY","valid_till_transport":"DD.MM.YYYY","issuing_authority":"","licence_type":""}}
• valid_till = the overall/primary validity date shown on the DL.
• valid_till_nt = validity date for Non-Transport (NT) vehicle classes (LMV, MCWG, etc.). Look in the vehicle class table on the back of the DL. Use "" if not found.
• valid_till_transport = validity date for Transport (T) vehicle classes (HMV, HTV, Trans, etc.). Look in the vehicle class table on the back of the DL. Use "" if not found.
• licence_type = all vehicle classes listed on the DL separated by hyphens (e.g. "LMV-MCWG" or "LMV-HMV-TRANS").

repair_estimate (repair estimate / quotation / service quotation / proforma — header says "Estimate" or "Quotation"):
{"type":"repair_estimate","pages":[1],"data":{"parts":[{"sn":1,"name":"Part Name","estimated_price":0.0,"category":"metal"}],"labour":[{"sn":1,"description":"Labour description","rr":0,"denting":0,"cw":0,"painting":0}],"total_labour_estimated":0.0,"dealer_name":"","dealer_address":"","workshop_status":""}}
• Extract ALL parts (up to 50+). category must be "metal", "plastic", or "glass":
  - metal: panels, brackets, bolts, hinges, sensors, structural parts, washers, nuts
  - plastic: bumpers, trim, claddings, spoilers, reflectors, foam
  - glass: windshield, window glass, mirror glass, headlamp glass, tail lamp lens
• labour breakdown: rr=R/R (Remove/Refit), denting=Denting, cw=Cutting/Welding, painting=Painting

final_invoice (final repair bill / tax invoice — header says "Tax Invoice" or "Invoice", has GST Invc No.):
{"type":"final_invoice","pages":[1],"data":{"parts_assessed":[{"name":"Part Name","assessed_price":0.0}],"labour_assessed_total":0.0,"dealer_name":"","dealer_address":"","workshop_status":""}}
• Extract ALL parts. Use base price before GST if GST is shown separately.

route_permit (route permit / goods permit / passenger permit):
{"type":"route_permit","pages":[1],"data":{"permit_no":"","permit_holder_name":"","valid_upto":"DD.MM.YYYY","type_of_permit":"","route_area":"","permit_no_auth":"","valid_upto_auth":"DD.MM.YYYY"}}
• permit_no = Part A permit number. valid_upto = Part A validity end date.
• permit_no_auth = Authorization permit number. valid_upto_auth = Authorization validity end date. Use "" if not present.
• type_of_permit = service type (e.g. Goods Service, All India Tourist Permit). route_area = region/route covered.

fitness_certificate (fitness certificate / vehicle fitness):
{"type":"fitness_certificate","pages":[1],"data":{"valid_upto":"DD.MM.YYYY"}}
• valid_upto = fitness certificate validity end date.

claim_form (insurance claim form filled by insured / claim intimation form):
{"type":"claim_form","pages":[1],"data":{"date_of_accident":"DD.MM.YYYY","place_of_accident":"","cause_of_accident":"brief description of how the accident happened","fir_detail":"FIR number and details, or Nil","injury_third_party":"injury or third party loss details, or Nil"}}
• date_of_accident = date of accident/loss as mentioned in the claim form.
• place_of_accident = place/location of accident/loss as mentioned in the claim form.
• cause_of_accident = brief narrative of how the accident happened (what the insured stated).
• fir_detail = FIR number/police station if mentioned, otherwise "Nil (As Per Claim Form)".
• injury_third_party = any injury or third party loss mentioned, otherwise "Nil (As Per Claim Form)".

vehicle_image (vehicle damage photos / claim photos / survey photos with visible date):
{"type":"vehicle_image","pages":[1],"data":{"date_of_survey":"DD.MM.YYYY"}}
• date_of_survey = the date visible or stamped on the vehicle photo (e.g. date overlay, timestamp). Use "" if no date is visible.

towing_bill (towing charges / towing bill / crane charges / vehicle recovery bill):
{"type":"towing_bill","pages":[1],"data":{}}

aadhar_card (Aadhar card / UIDAI card / Aadhaar identity card):
{"type":"aadhar_card","pages":[1],"data":{}}

pan_card (PAN card / income tax permanent account number card):
{"type":"pan_card","pages":[1],"data":{}}

discharge_voucher (discharge voucher / satisfaction voucher / final discharge / no-claim voucher):
{"type":"discharge_voucher","pages":[1],"data":{}}

kyc_form (KYC form / Know Your Customer form / customer verification form):
{"type":"kyc_form","pages":[1],"data":{}}

accident_document (FIR / police report / DDR / GD entry about the accident):
{"type":"accident_document","pages":[1],"data":{"fir_no":"","fir_date":"DD.MM.YYYY","police_station":""}}
• fir_no = FIR/DDR/GD number. fir_date = date filed. police_station = name of police station.

survey_report (surveyor's assessment report):
{"type":"survey_report","pages":[1],"data":{"report_no":"","report_date":"DD.MM.YYYY","surveyor_name":"","surveyor_phone":"","surveyor_city":""}}
• report_no = survey report number. report_date = date of report. surveyor_name = name of surveyor. surveyor_phone = phone number. surveyor_city = city.

tax_report | labour_charges:
{"type":"<detected_type>","pages":[1],"data":{}}

unknown (document that does not match any type above):
{"type":"unknown","pages":[1],"data":{"name":"Short Descriptive Name"}}
• "name" = a short 2-4 word Title Case label describing the document (e.g. "Bank Statement", "Cancelled Cheque", "Damage Photos", "Voter ID Card", "NOC Letter").
• Do NOT use generic names like "Document", "Image", "Paper", "File", "Unknown".

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
        workshop_status=data.get("workshop_status", ""),
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
        workshop_status=data.get("workshop_status", ""),
    )


def _build_route_permit(data: dict) -> RoutePermitData:
    return RoutePermitData(
        permit_no=data.get("permit_no", ""),
        permit_holder_name=data.get("permit_holder_name", ""),
        valid_upto=data.get("valid_upto", "") or data.get("validity_to_date", ""),
        type_of_permit=data.get("type_of_permit", "") or data.get("service_type", ""),
        route_area=data.get("route_area", "") or data.get("region_covered", ""),
        permit_no_auth=data.get("permit_no_auth", ""),
        valid_upto_auth=data.get("valid_upto_auth", ""),
    )


def _build_fitness_cert(data: dict) -> FitnessCertData:
    return FitnessCertData(
        valid_upto=data.get("valid_upto", ""),
    )


def _build_claim_form(data: dict) -> ClaimFormData:
    return ClaimFormData(
        date_of_accident=data.get("date_of_accident", ""),
        place_of_accident=data.get("place_of_accident", ""),
        cause_of_accident=data.get("cause_of_accident", ""),
        fir_detail=data.get("fir_detail", ""),
        injury_third_party=data.get("injury_third_party", ""),
    )


def _build_vehicle_image(data: dict) -> VehicleImageData:
    return VehicleImageData(
        date_of_survey=data.get("date_of_survey", ""),
    )


def _build_accident_doc(data: dict) -> AccidentDocData:
    return AccidentDocData(
        fir_no=data.get("fir_no", ""),
        fir_date=data.get("fir_date", ""),
        police_station=data.get("police_station", ""),
    )


def _build_survey_report(data: dict) -> SurveyReportData:
    return SurveyReportData(
        report_no=data.get("report_no", ""),
        report_date=data.get("report_date", ""),
        surveyor_name=data.get("surveyor_name", ""),
        surveyor_phone=data.get("surveyor_phone", ""),
        surveyor_city=data.get("surveyor_city", ""),
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

    if "accident_document" in grouped:
        all_data.accident_doc = _build_accident_doc(
            _merge_simple(grouped["accident_document"])
        )

    if "survey_report" in grouped:
        all_data.survey_report = _build_survey_report(
            _merge_simple(grouped["survey_report"])
        )

    return all_data


# ─── Single-doc classify+extract ─────────────────────────────────────────────


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

    # ── PDF — get page count WITHOUT rendering (saves memory) ─────────────────
    import fitz as _fitz

    with _fitz.open(file_path) as _doc:
        total_pages = len(_doc)

    if total_pages <= MAX_PAGES_PER_CALL:
        # Small PDF — single call (renders pages once inside vision_extract_json)
        return _call_with_retry(
            lambda: vision_extract_json(
                [file_path], prompt_with_filename, max_output_tokens=_MAX_OUTPUT_TOKENS
            ),
            file_label,
            cancel_event,
        )

    # ── Large PDF — render ONE chunk at a time to limit memory ────────────────
    import gc as _gc

    print(
        f"    📄 {file_label}: {total_pages} pages → splitting into chunks of {MAX_PAGES_PER_CALL}"
    )
    all_results: list[dict[str, Any]] = []

    for chunk_start in range(0, total_pages, MAX_PAGES_PER_CALL):
        if cancel_event and cancel_event.is_set():
            raise ProcessingCancelledError("Processing stopped by user")

        chunk_end = min(chunk_start + MAX_PAGES_PER_CALL, total_pages)
        page_offset = chunk_start  # 0-based offset for this chunk

        # Render only this chunk's pages (not the whole PDF)
        chunk_b64 = pdf_pages_to_base64(
            file_path, start_page=chunk_start, end_page=chunk_end
        )

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

        # Free chunk memory before rendering next chunk
        del chunk_b64
        _gc.collect()

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

    with ThreadPoolExecutor(max_workers=1) as pool:
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
