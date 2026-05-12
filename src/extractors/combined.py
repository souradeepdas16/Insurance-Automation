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
    InvoiceLabourItem,
    InvoicePart,
    InsuranceData,
    LabourItem,
    MotorSurveyReportData,
    RCData,
    RcStatusData,
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
    "police_report",
    "survey_report",
    "motor_survey_report",
    "claim_form",
    "tax_report",
    "labour_charges",
    "vehicle_image",
    "towing_bill",
    "aadhar_card",
    "pan_card",
    "discharge_voucher",
    "kyc_form",
    "cancelled_cheque",
    "ncb_certificate",
    "pre_inspection_report",
    "rc_status",
    "gst_registration",
    "affidavit",
    "self_statement",
    "payment_receipt",
    "weigh_slip",
    "medical_record",
    "partnership_deed",
    "form_64vb",
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
final_invoice | route_permit | fitness_certificate | police_report |
survey_report | motor_survey_report | claim_form | tax_report | labour_charges | towing_bill |
aadhar_card | pan_card | discharge_voucher | kyc_form |
cancelled_cheque | ncb_certificate | pre_inspection_report | rc_status | gst_registration |
affidavit | self_statement | payment_receipt |
weigh_slip | medical_record | partnership_deed | form_64vb | unknown

━━━ CRITICAL — HOW TO DISTINGUISH EACH DOCUMENT TYPE ━━━

★★★ FUNDAMENTAL RULE ★★★
FIRST, read the HEADING / TITLE of the document (the prominent text at the top of the page).
The heading tells you WHAT the document IS. Only after identifying the heading should you
look at the body content for confirmation. Do NOT let body content override the heading.
Example: A page titled "MOTOR SURVEY REPORT" is a motor_survey_report — even if its body lists
vehicle registration details, insurance particulars, etc. Those details are just reference data.

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
  ★ A MOTOR SURVEY REPORT / SPOT SURVEY REPORT listing vehicle particulars (registration no,
    chassis, engine no, etc.) is NOT a registration_certificate — it is a motor_survey_report.
    Survey reports are prepared by a Surveyor & Loss Assessor and contain "MOTOR SURVEY REPORT"
    or "SURVEY REPORT" in their header. They list vehicle details for REFERENCE, but the document
    itself is a surveyor's report, not the government-issued RC card.

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

▶ claim_form — The actual INSURANCE CLAIM FORM / claim intimation form issued by an insurance
  company — a PRINTED FORM with BLANK FIELDS or SECTIONS to be filled by the insured.
  IT IS: A structured pre-printed form FROM AN INSURANCE COMPANY with labeled sections
  specifically about an insurance claim for vehicle damage / loss.
  LOOK FOR (TITLE on page 1): "MOTOR CLAIM FORM", "Claim Intimation Form", "Motor Vehicle
  Claim Form", "Motor OD Claim Form", "Motor Accident Claim Form", "Claim Form".
  LOOK FOR (SECTIONS — may span multiple pages):
    • INSURED section — insured's name, address, policy number
    • THE INSURED VEHICLE section — make, model, engine no., chassis no., reg. no.,
      questions like "Was the vehicle in proper working condition?"
    • DRIVER AT THE TIME OF ACCIDENT section — driver's name, age, address, DL details,
      "Was the licence temporary/permanent?", "Has he been charged by police?"
    • ACCIDENT / LOSS DETAILS section — date of accident, time, place, speed,
      "Give a short description of the accident", nature of damage
    • DECLARATION / SIGNATURE section — insured's declaration and signature
    • Insurance company branding / letterhead (e.g. United India, New India, ICICI Lombard,
      Bajaj Allianz, HDFC ERGO, National Insurance, Oriental Insurance, IFFCO Tokio, etc.)
  MULTI-PAGE: Claim forms are usually 2-5 pages. ALL continuation pages of the same claim
  form must be grouped under ONE claim_form entry with all page numbers (e.g. [1,2,3,4]).
  Even if later pages lack the "CLAIM FORM" title, they belong to the same form if they
  continue the sections above (e.g. page 2 has "Driver Details", page 3 has "Declaration").
  IT IS NOT: A hospital / medical admission form (those have "ADMISSION", "PATIENT NAME",
  "WARD", "MRD No.", "Consultant", hospital branding — NOT insurance company branding).
  IT IS NOT: An affidavit, a letter, a sworn statement, or any narrative document.
  IT IS NOT: A driving license, Aadhaar card, PAN card, or any identity document.
  IT IS NOT: An FIR or police report (those are police_report).
  IT IS NOT: A survey report (those are from a surveyor, not a form filled by the insured).
  IT IS NOT: A discharge voucher (those are signed AFTER claim settlement).

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

▶ fitness_certificate — The actual government certificate confirming a vehicle is roadworthy.
  IT IS: A certificate issued by RTO/transport authority after vehicle inspection.
  LOOK FOR: "Fitness Certificate", "Certificate of Fitness", validity date ("Valid Upto"),
  RTO stamp, vehicle details, inspection result.
  IT IS NOT: A route_permit or registration_certificate.
  IT IS NOT: A VAHAN portal "RC STATUS" printout — that is rc_status (see below).

▶ rc_status — A VAHAN portal "RC STATUS" printout (online verification page from parivahan.gov.in).
  IT IS: An online printout from the VAHAN / National Register e-Services portal showing RC details.
  LOOK FOR: "RC STATUS" header, VAHAN/parivahan.gov.in branding, "National Register e-Services",
  Fitness/REGN validity, PUCC validity, Insurance Details section, Permit Details section,
  owner details, vehicle class, fuel type, registration authority info.
  ★ This is NOT a registration_certificate — it is an online status printout.
  IT IS NOT: A fitness_certificate (physical certificate from RTO).
  IT IS NOT: A registration_certificate (physical RC card).

▶ police_report — FIR, police report, or any official police/incident report about the accident.
  IT IS: An official document FROM THE POLICE or authorities about the accident.
  LOOK FOR: "FIR", "First Information Report", "Police Report", "Accident Report",
  "General Diary", "DDR", police station name, IO (Investigating Officer) name, FIR number.
  IT IS NOT: A claim_form (FIR is from POLICE; claim form is an INSURANCE company form).
  IT IS NOT: An affidavit (an affidavit is a sworn personal statement, not a police report).

▶ survey_report — A DETAILED survey/assessment report by a licensed surveyor, typically multi-page
  with damage assessment, photographs, recommended repair amounts, and final assessment.
  IT IS: A professional assessment report by a licensed surveyor appointed by the insurer.
  LOOK FOR: "Survey Report", "Surveyor Report", "Assessment Report", "Final Survey Report",
  surveyor name & licence, damage assessment details, photographs, recommended repair amounts,
  multiple sections covering insurance particulars, vehicle particulars, damage details, and assessment.
  IT IS NOT: A repair_estimate. IT IS NOT: A motor_survey_report (see below).

▶ motor_survey_report — A SPOT / INITIAL motor survey report, typically 1-2 pages, done at the
  scene or at first inspection. Often titled "MOTOR SURVEY REPORT" or "MOTOR SURVEY REPORT SPOT".
  IT IS: A brief spot survey form filled by a surveyor at the initial vehicle inspection.
  LOOK FOR: "MOTOR SURVEY REPORT", "MOTOR SURVEY REPORT SPOT", "Spot Survey",
  surveyor name & licence, "Surveyor & Loss Assessor", vehicle particulars (Reg No, Chassis,
  Engine), insurance particulars, "PHYSICALLY VERIFIED" stamp, "Form 24" reference.
  ★ If the heading says "MOTOR SURVEY REPORT" or "MOTOR SURVEY REPORT SPOT", classify as
    motor_survey_report — NOT as registration_certificate, NOT as survey_report.
  IT IS NOT: A registration_certificate (even though it lists vehicle reg details).
  IT IS NOT: A survey_report (survey_report is the detailed final report; this is the spot form).

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

▶ cancelled_cheque — A cancelled cheque from a bank account.
  IT IS: A physical bank cheque with "CANCELLED" written across it, used for bank verification.
  LOOK FOR: "CANCELLED" written across the cheque, bank name, IFSC code, MICR code,
  account number, cheque number, bank branch details.
  IT IS NOT: A payment receipt or bank statement.

▶ ncb_certificate — No Claim Bonus (NCB) certificate / confirmation from an insurer.
  IT IS: A letter or certificate from an insurance company confirming the insured's NCB entitlement.
  LOOK FOR: "No Claim Bonus", "NCB", "NCB Certificate", "NCB Confirmation", "NCB Declaration",
  NCB percentage, previous policy details, claim-free years, insurer letterhead.
  IT IS NOT: An insurance_policy (NCB cert confirms bonus entitlement; policy is the full coverage doc).

▶ pre_inspection_report — Pre-insurance inspection report of the vehicle.
  IT IS: An inspection report done BEFORE issuing or renewing an insurance policy.
  LOOK FOR: "Pre-Inspection", "Pre-Insurance Survey", "Break-In Inspection", "Pre-Insp",
  vehicle condition assessment, photographs of vehicle before insurance, inspector details.
  IT IS NOT: A survey_report (survey is AFTER accident; pre-inspection is BEFORE insurance).

▶ gst_registration — GST registration certificate issued by the tax authority.
  IT IS: A certificate of registration under the Goods and Services Tax Act.
  LOOK FOR: "Certificate of Registration", "GST", "GSTIN", "Goods and Services Tax",
  GSTIN number (e.g. 06AABCU9603R1ZM), trade name, legal name, date of registration,
  principal place of business, government of India emblem.
  IT IS NOT: A tax_report (GST registration is the certificate; tax report is a payment/filing doc).

▶ affidavit — A sworn notarized statement / affidavit (general or third-party).
  IT IS: A sworn written statement on stamp paper, signed before a notary or magistrate.
  LOOK FOR: "AFFIDAVIT", "SWORN STATEMENT", "I hereby solemnly affirm", stamp paper,
  notary seal/stamp, "Before the Notary", oath/declaration text, deponent's signature.
  Also includes TP (third-party) affidavits — any affidavit about third-party damage,
  injury, or liability is also classified as affidavit.
  IT IS NOT: A claim_form, police_report, self_statement, or any specific document type above.

▶ self_statement — Self-statement or personal declaration by the insured about the incident.
  IT IS: A written statement by the insured describing the accident/incident in their own words.
  LOOK FOR: "Self Statement", "Statement", "Personal Statement", "My Statement",
  narrative description of accident by the insured, handwritten or typed on plain paper,
  insured's signature, may mention "I was driving..." or similar first-person account.
  IT IS NOT: An affidavit (no notary/stamp paper) or a claim_form (not an insurance company form).

▶ payment_receipt — Payment receipt or acknowledgment for any payment made.
  IT IS: A receipt confirming payment has been made or received.
  LOOK FOR: "Payment Receipt", "Receipt", "Acknowledgment", "Money Receipt", amount paid,
  payment date, received from/by, payment mode (cash/cheque/online), receipt number.
  IT IS NOT: A final_invoice (invoice is a bill; receipt is proof of payment).

▶ weigh_slip — Weigh slip, goods receipt (GR), or load challan for commercial vehicles.
  IT IS: A document recording the weight of goods/vehicle at a weighbridge, or a goods receipt/load challan.
  LOOK FOR: "Weigh Slip", "Weighment Slip", "Weigh Bridge", "Goods Receipt", "GR",
  "Load Challan", "Goods Invoice", gross weight, tare weight, net weight, vehicle number,
  commodity/goods description, weighbridge name.
  IT IS NOT: A final_invoice or repair_estimate.

▶ medical_record — Medical record, report, or certificate related to the driver or injured party.
  IT IS: A medical document from a hospital or doctor about injury/fitness of the driver or third party.
  LOOK FOR: "Medical Record", "Medical Report", "Medical Certificate", "Fitness Certificate" (medical),
  "Discharge Summary", "OPD Record", doctor's name, hospital name, patient details,
  diagnosis, treatment details, injury description.
  IT IS NOT: A claim_form or fitness_certificate (which is for VEHICLE fitness, not human medical fitness).

▶ partnership_deed — Partnership deed or firm registration document.
  IT IS: A legal document establishing a business partnership, relevant when the insured is a firm.
  LOOK FOR: "Partnership Deed", "Deed of Partnership", "Partnership Agreement",
  partner names, firm name, terms of partnership, notarized, registered.
  IT IS NOT: An affidavit or any identity document.

▶ form_64vb — Form 64VB (tax clearance certificate for vehicle transfer).
  IT IS: A form under the Income Tax Act for obtaining a no-objection certificate for vehicle transfer.
  LOOK FOR: "Form 64VB", "Form No. 64VB", "64VB", Income Tax Act reference,
  vehicle transfer details, seller/buyer details, tax clearance.
  IT IS NOT: A tax_report or registration_certificate.

▶ unknown — ONLY if the document does NOT match ANY of the above types.
  Use this as a last resort. Provide a short 2-4 word descriptive name.
  EXAMPLES of unknown documents: Bank Statement, Voter ID,
  NOC Letter, Consent Letter, Legal Notice, Ownership Transfer.

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
• An AFFIDAVIT is a sworn notarized statement (including TP affidavits) → classify as "affidavit". NOT "unknown".
• An FIR / police report → police_report, NOT claim_form.
• A surveyor's detailed damage assessment → survey_report, NOT repair_estimate.
• A Motor Survey Report / Spot Survey Report → motor_survey_report, NOT registration_certificate, NOT survey_report.
  Even if it lists vehicle particulars (reg no, chassis, engine no), it is the SURVEYOR'S spot report.
• A VAHAN portal "RC STATUS" printout → rc_status, NOT registration_certificate, NOT fitness_certificate.
• A towing/crane/recovery bill → towing_bill, NEVER final_invoice.
• A cancelled cheque → cancelled_cheque, NOT "unknown".
• An NCB letter/certificate → ncb_certificate, NOT insurance_policy.
• A pre-inspection report → pre_inspection_report, NOT survey_report.
• A GST registration certificate → gst_registration, NOT tax_report.
• A self-statement by the insured → self_statement, NOT affidavit (no stamp paper/notary).
• A payment receipt → payment_receipt, NOT final_invoice.
• A weigh slip / GR / load challan → weigh_slip, NOT "unknown".
• A medical record/report → medical_record, NOT claim_form.
• A partnership deed → partnership_deed, NOT "unknown".
• A Form 64VB → form_64vb, NOT tax_report.
• A hospital / medical admission form (with "ADMISSION", "PATIENT", "WARD", hospital name)
  is a medical_record.
• A claim_form is ALWAYS from an INSURANCE COMPANY, never from a hospital or police station.

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
• insurer_address = the policy issuing office address OR issuing office address OR policy servicing office address OR claim contact address OR "policy signed at" location. This is the BRANCH/OFFICE address of the insurance company — NOT the policy type, NOT the UIN number, NOT the product name.
• policy_period = validity or period of insurance or period of cover. Format: "DD.MM.YYYY to DD.MM.YYYY".
• idv is a plain integer (e.g. 1320000, NOT "13,20,000")

registration_certificate (vehicle RC / registration certificate):
{"type":"registration_certificate","pages":[1],"data":{"registration_number":"","date_of_reg_issue":"DD.MM.YYYY","date_of_reg_expiry":"DD.MM.YYYY","chassis_number":"last 6 digits","engine_number":"last 6 or full","make_year":"MAKE MODEL/YEAR","body_type":"","vehicle_class":"","laden_weight":"","unladen_weight":"","seating_capacity":0,"road_tax_paid_upto":"","fuel_type":"","colour":"","cubic_capacity":0,"registered_owner":"","hpa_with":""}}
• registration_number = registration number or regn. no. or regn number or reg no.
• date_of_reg_issue = date of registration or date of regn or regn date or registration date.
• date_of_reg_expiry = valid upto or regn validity or fitness validity or fitness upto. If not mentioned on RC, leave empty (will be picked from fitness certificate separately).
• chassis_number = chassis no. or ch. no. or ch. number — extract last 6 digits only.
• engine_number = engine no. or eng. no. or engin. number — extract last 6 digits or full number.
• make_year = manufacturer name or maker name & model / variant (e.g. "MARUTI SWIFT/VXI 2020").
• laden_weight = laden weight or registered laden wt. or RLW or GVW.
• unladen_weight = unladen weight or registered unladen wt. or ULW.
• registered_owner = registered owner or regd owner or name of owner or name of regd owner or owner name.
• cubic_capacity = cubic capacity or CC.
• If front+back are both visible on separate pages, combine fields from both sides into ONE entry with both page numbers.
• hpa_with: name of the bank or financier shown in the Hypothecation/HPA field; use "" if not present.

driving_license (driving licence / DL / driving license status / DL status):
{"type":"driving_license","pages":[1],"data":{"driver_name":"","dob":"DD.MM.YYYY","address":"","city_state":"","licence_number":"","alt_licence_number":"","date_of_issue":"DD.MM.YYYY","valid_till":"DD.MM.YYYY","valid_till_nt":"DD.MM.YYYY","valid_till_transport":"DD.MM.YYYY","issuing_authority":"","licence_type":""}}
• licence_number = DL no. or number or license no. or code like HR-14, PB-04, WB-09, HP-03 followed by digits.
• valid_till = validity or valid upto or valid till — the overall/primary validity date shown on the DL.
• valid_till_nt = validity date for Non-Transport (NT) vehicle classes (LMV, MCWG, etc.). Look in the vehicle class table on the back of the DL. Use "" if not found.
• valid_till_transport = validity date for Transport (T) vehicle classes (HMV, HTV, Trans, etc.). Look in the vehicle class table on the back of the DL. Use "" if not found.
• licence_type = class of vehicle or vehicle class or authorized to drive or licensed to drive. Extract all vehicle classes listed on the DL separated by hyphens (e.g. "LMV-MCWG" or "LMV-HMV-TRANS").
• date_of_issue = date of issue or issued on or issue date.
• issuing_authority = issuing authority or licensing authority.
• Also extract from "Know your driving License status" or "driving license details" or "DL status" online printouts.

repair_estimate (repair estimate / quotation / service quotation / proforma — header says "Estimate" or "Quotation"):
{"type":"repair_estimate","pages":[1],"data":{"parts":[{"sn":1,"name":"Part Name","estimated_price":0.0,"category":"metal"}],"labour":[{"sn":1,"description":"Labour description","estimated_price":0.0,"rr":0,"denting":0,"cw":0,"painting":0}],"total_labour_estimated":0.0,"dealer_name":"","dealer_address":"","workshop_status":""}}
• The estimate has TWO sections: "Labour charges" and "Parts charges". Extract them SEPARATELY.
• parts = ONLY items from the "Parts charges" section. Use base price before GST.
• labour = ONLY items from the "Labour charges" section. Use base price (Labour/Unit Price) before GST.
• Do NOT mix labour items into parts or vice versa.
• For each part, category must be "metal", "plastic", or "glass":
  - metal: panels, brackets, bolts, hinges, sensors, structural parts, washers, nuts
  - plastic: bumpers, trim, claddings, spoilers, reflectors, foam
  - glass: windshield, window glass, mirror glass, headlamp glass, tail lamp lens
• For each labour item: estimated_price = the Labour/Unit Price amount.
  Also categorize into rr/denting/cw/painting columns based on the operation type:
  - Paint / PR → painting column
  - Replacement / R/R → rr column
  - BR / Body Repair / Denting → denting column
  - C/W / Cutting / Welding → cw column
  Set the matching column to the estimated_price value; leave others as 0.

final_invoice (final repair bill / tax invoice — header says "Tax Invoice" or "Invoice", has GST Invc No.):
{"type":"final_invoice","pages":[1],"data":{"parts_assessed":[{"name":"Part Name","assessed_price":0.0}],"labour_assessed":[{"description":"Labour description","assessed_price":0.0}],"labour_assessed_total":0.0,"dealer_name":"","dealer_address":"","workshop_status":""}}
• The invoice has TWO sections: "Labour charges" and "Parts charges". Extract them SEPARATELY.
• parts_assessed = ONLY items from the "Parts charges" section. Use base price before GST.
• labour_assessed = ONLY items from the "Labour charges" section. Use base price before GST.
• labour_assessed_total = sum of all labour assessed prices.
• Do NOT mix labour items into parts_assessed or vice versa.

route_permit (route permit / goods permit / passenger permit):
{"type":"route_permit","pages":[1],"data":{"permit_no":"","permit_holder_name":"","valid_upto":"DD.MM.YYYY","type_of_permit":"","route_area":"","permit_no_auth":"","valid_upto_auth":"DD.MM.YYYY"}}
• There are TWO parts: Part A and Authorization (also called Form 47 or Part B).
• permit_no = Part A route permit number. valid_upto = Part A validity end date.
• permit_no_auth = Authorization permit number (from Authorization / Form 47 / Part B). valid_upto_auth = Authorization validity end date. Use "" if not present.
• type_of_permit = "Goods carrying" or "Passenger carrying" — normalize to one of these two values.
• route_area = if ONLY Part A is available, write "Whole State". If Authorization (Part B) is ALSO available, write "Whole of India".

fitness_certificate (fitness certificate / vehicle fitness):
{"type":"fitness_certificate","pages":[1],"data":{"valid_upto":"DD.MM.YYYY"}}
• valid_upto = fitness certificate validity end date.

claim_form (insurance claim form filled by insured / claim intimation form / intimation letter):
{"type":"claim_form","pages":[1,2,3],"data":{"date_of_accident":"DD.MM.YYYY","place_of_accident":"","cause_of_accident":"brief description of how the accident happened","fir_detail":"FIR number and details, or Nil","injury_third_party":"injury or third party loss details, or Nil"}}
• Claim forms / intimation letters are usually 2-5 pages. Group ALL pages into ONE entry (e.g. "pages":[1,2,3,4]).
• date_of_accident = date & time of accident — pick from the claim form or intimation letter.
• place_of_accident = place of accident — pick from the claim form or intimation letter.
• cause_of_accident = brief narrative of how the accident happened (what the insured stated).
• fir_detail = FIR number/police station if mentioned, otherwise "Nil (As Per Claim Form)".
• injury_third_party = any injury or third party loss mentioned, otherwise "Nil (As Per Claim Form)".

vehicle_image (vehicle damage photos / claim photos / survey photos with visible date):
{"type":"vehicle_image","pages":[1],"data":{"date_of_survey":"DD.MM.YYYY"}}
• date_of_survey = the date visible or stamped on the vehicle photo (e.g. date overlay, timestamp watermark). This is used as the date of allotment of survey and date & time of survey. Use "" if no date is visible.

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

police_report (FIR / police report / DDR / GD entry about the accident):
{"type":"police_report","pages":[1],"data":{"fir_no":"","fir_date":"DD.MM.YYYY","police_station":""}}
• fir_no = FIR no. or GD no. or DDR number — extract the number only.
• fir_date = date filed (DD.MM.YYYY).
• police_station = name of the police station (e.g. "Habbra", "Sadar").
• These three fields will be formatted as: "FIR no. 1234, dtd. 01/02/2026, PS Habbra".

survey_report (surveyor's detailed assessment report):
{"type":"survey_report","pages":[1],"data":{"report_no":"","report_date":"DD.MM.YYYY","surveyor_name":"","surveyor_phone":"","surveyor_city":""}}
• report_no = survey report number. report_date = date of report. surveyor_name = name of surveyor. surveyor_phone = phone number. surveyor_city = city.

motor_survey_report (motor survey report / spot survey report):
{"type":"motor_survey_report","pages":[1],"data":{"report_no":"","report_date":"DD.MM.YYYY","surveyor_name":"","surveyor_phone":"","surveyor_city":""}}
• Same fields as survey_report. report_no = reference/report number. surveyor_name = surveyor & loss assessor name.

tax_report | labour_charges:
{"type":"<detected_type>","pages":[1],"data":{}}

cancelled_cheque (cancelled cheque for bank verification):
{"type":"cancelled_cheque","pages":[1],"data":{}}

ncb_certificate (No Claim Bonus certificate / NCB confirmation):
{"type":"ncb_certificate","pages":[1],"data":{}}

pre_inspection_report (pre-insurance inspection report):
{"type":"pre_inspection_report","pages":[1],"data":{}}

rc_status (VAHAN portal RC STATUS printout):
{"type":"rc_status","pages":[1],"data":{"valid_upto":"DD.MM.YYYY"}}
• valid_upto = registration/fitness validity date from the printout.

gst_registration (GST registration certificate):
{"type":"gst_registration","pages":[1],"data":{}}

affidavit (sworn notarized statement / affidavit including TP affidavits):
{"type":"affidavit","pages":[1],"data":{}}

self_statement (self-statement / personal declaration by insured):
{"type":"self_statement","pages":[1],"data":{}}

payment_receipt (payment receipt / money receipt):
{"type":"payment_receipt","pages":[1],"data":{}}

weigh_slip (weigh slip / goods receipt / load challan):
{"type":"weigh_slip","pages":[1],"data":{}}

medical_record (medical record / report / certificate):
{"type":"medical_record","pages":[1],"data":{}}

partnership_deed (partnership deed / firm registration):
{"type":"partnership_deed","pages":[1],"data":{}}

form_64vb (Form 64VB tax clearance for vehicle transfer):
{"type":"form_64vb","pages":[1],"data":{}}

unknown (document that does not match any type above):
{"type":"unknown","pages":[1],"data":{"name":"Short Descriptive Name"}}
• "name" = a short 2-4 word Title Case label describing the document. This name will be used as the FILE NAME, so make it accurate and descriptive.
• ALWAYS read the form heading / title / letterhead at the top of the document and use it as the name.
• If there is no clear heading, describe what the document IS based on its content (e.g. "Bank Statement", "Voter ID Card", "NOC Letter", "Consent Form", "Authorization Letter", "Ownership Transfer", "Vehicle Photos").
• Every document has SOME identifiable heading, title, or purpose — find it and use it. There is NO excuse for a generic name.
• Do NOT use generic names like "Document", "Image", "Paper", "File", "Unknown", "Unclassified", "Extra Document", "Extra Image", "Miscellaneous", "Other", "Scanned Document", "Scanned Image".
• The name MUST clearly identify what the document is about — it will be the only label shown to the user.

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
            estimated_price=float(lv.get("estimated_price", 0)),
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
    labour = [
        InvoiceLabourItem(
            description=l.get("description", ""),
            assessed_price=float(l.get("assessed_price", 0)),
        )
        for l in data.get("labour_assessed", [])
    ]
    return InvoiceData(
        parts_assessed=parts,
        labour_assessed=labour,
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


def _build_motor_survey_report(data: dict) -> MotorSurveyReportData:
    return MotorSurveyReportData(
        report_no=data.get("report_no", ""),
        report_date=data.get("report_date", ""),
        surveyor_name=data.get("surveyor_name", ""),
        surveyor_phone=data.get("surveyor_phone", ""),
        surveyor_city=data.get("surveyor_city", ""),
    )


def _build_rc_status(data: dict) -> RcStatusData:
    return RcStatusData(
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

    if "claim_form" in grouped:
        all_data.claim_form = _build_claim_form(_merge_simple(grouped["claim_form"]))

    if "vehicle_image" in grouped:
        all_data.vehicle_image = _build_vehicle_image(
            _merge_simple(grouped["vehicle_image"])
        )

    if "police_report" in grouped:
        all_data.accident_doc = _build_accident_doc(
            _merge_simple(grouped["police_report"])
        )

    if "survey_report" in grouped:
        all_data.survey_report = _build_survey_report(
            _merge_simple(grouped["survey_report"])
        )

    if "motor_survey_report" in grouped:
        all_data.motor_survey_report = _build_motor_survey_report(
            _merge_simple(grouped["motor_survey_report"])
        )

    if "rc_status" in grouped:
        all_data.rc_status = _build_rc_status(_merge_simple(grouped["rc_status"]))

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
