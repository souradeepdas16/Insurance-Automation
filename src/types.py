from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass  # pylint: disable=too-many-instance-attributes
class InsuranceData:  # pylint: disable=too-many-instance-attributes
    insurer_name: str = ""
    insurer_address: str = ""
    policy_number: str = ""
    policy_period: str = ""
    idv: str | int | float = ""
    insured_name: str = ""
    insured_address: str = ""
    contact_number: str = ""
    tp_policy_number: str = ""


@dataclass  # pylint: disable=too-many-instance-attributes
class RCData:  # pylint: disable=too-many-instance-attributes
    registration_number: str = ""
    date_of_reg_issue: str = ""
    date_of_reg_expiry: str = ""
    chassis_number: str = ""
    engine_number: str = ""
    make_year: str = ""
    body_type: str = ""
    vehicle_class: str = ""
    laden_weight: str = ""
    unladen_weight: str = ""
    seating_capacity: str | int = ""
    fuel_type: str = ""
    colour: str = ""
    road_tax_paid_upto: str = ""
    registered_owner: str = ""
    cubic_capacity: str | int = ""
    hpa_with: str = ""


@dataclass  # pylint: disable=too-many-instance-attributes
class DLData:  # pylint: disable=too-many-instance-attributes
    driver_name: str = ""
    dob: str = ""
    address: str = ""
    city_state: str = ""
    country: str = "INDIA"
    licence_number: str = ""
    alt_licence_number: str = ""
    date_of_issue: str = ""
    valid_till: str = ""
    valid_till_nt: str = ""
    valid_till_transport: str = ""
    issuing_authority: str = ""
    licence_type: str = ""


@dataclass
class EstimatePart:
    sn: int = 0
    name: str = ""
    estimated_price: float = 0.0
    category: str = ""  # metal | plastic | glass


@dataclass
class LabourItem:
    sn: int = 0
    description: str = ""
    estimated_price: float = 0.0
    rr: float = 0.0
    denting: float = 0.0
    cw: float = 0.0
    painting: float = 0.0


@dataclass
class EstimateData:
    parts: list[EstimatePart] = field(default_factory=list)
    labour: list[LabourItem] = field(default_factory=list)
    total_labour_estimated: float = 0.0
    dealer_name: str = ""
    dealer_address: str = ""
    estimate_date: str = ""
    estimate_number: str = ""
    workshop_status: str = ""


@dataclass
class InvoicePart:
    name: str = ""
    assessed_price: float = 0.0


@dataclass
class InvoiceLabourItem:
    description: str = ""
    assessed_price: float = 0.0


@dataclass
class InvoiceData:
    parts_assessed: list[InvoicePart] = field(default_factory=list)
    labour_assessed: list[InvoiceLabourItem] = field(default_factory=list)
    labour_assessed_total: float = 0.0
    invoice_number: str = ""
    invoice_date: str = ""
    dealer_name: str = ""
    dealer_address: str = ""
    total_amount: float = 0.0
    gst_amount: float = 0.0
    workshop_status: str = ""


@dataclass
class RoutePermitData:
    permit_no: str = ""
    permit_holder_name: str = ""
    valid_upto: str = ""
    type_of_permit: str = ""
    route_area: str = ""
    permit_no_auth: str = ""
    valid_upto_auth: str = ""


@dataclass
class FitnessCertData:
    valid_upto: str = ""


@dataclass
class ClaimFormData:
    date_of_accident: str = ""
    place_of_accident: str = ""
    cause_of_accident: str = ""
    fir_detail: str = ""
    injury_third_party: str = ""


@dataclass
class AccidentDocData:
    fir_no: str = ""
    fir_date: str = ""
    police_station: str = ""


@dataclass
class SurveyReportData:
    report_no: str = ""
    report_date: str = ""
    surveyor_name: str = ""
    surveyor_phone: str = ""
    surveyor_city: str = ""


@dataclass
class MotorSurveyReportData:
    report_no: str = ""
    report_date: str = ""
    surveyor_name: str = ""
    surveyor_phone: str = ""
    surveyor_city: str = ""


@dataclass
class RcStatusData:
    valid_upto: str = ""


@dataclass
class VehicleImageData:
    date_of_survey: str = ""


@dataclass
class AllExtractedData:
    insurance: Optional[InsuranceData] = None
    rc: Optional[RCData] = None
    dl: Optional[DLData] = None
    estimate: Optional[EstimateData] = None
    invoice: Optional[InvoiceData] = None
    route_permit: Optional[RoutePermitData] = None
    fitness_cert: Optional[FitnessCertData] = None
    claim_form: Optional[ClaimFormData] = None
    vehicle_image: Optional[VehicleImageData] = None
    accident_doc: Optional[AccidentDocData] = None
    survey_report: Optional[SurveyReportData] = None
    motor_survey_report: Optional[MotorSurveyReportData] = None
    rc_status: Optional[RcStatusData] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AllExtractedData":
        def _build(klass, val):
            if val is None:
                return None
            return klass(
                **{k: v for k, v in val.items() if k in klass.__dataclass_fields__}
            )

        est_dict = d.get("estimate")
        estimate = None
        if est_dict:
            parts = [EstimatePart(**p) for p in est_dict.pop("parts", [])]
            labour = [LabourItem(**l) for l in est_dict.pop("labour", [])]
            estimate = EstimateData(
                parts=parts,
                labour=labour,
                **{
                    k: v
                    for k, v in est_dict.items()
                    if k in EstimateData.__dataclass_fields__
                },
            )

        inv_dict = d.get("invoice")
        invoice = None
        if inv_dict:
            parts_assessed = [
                InvoicePart(**p) for p in inv_dict.pop("parts_assessed", [])
            ]
            labour_assessed = [
                InvoiceLabourItem(**l) for l in inv_dict.pop("labour_assessed", [])
            ]
            invoice = InvoiceData(
                parts_assessed=parts_assessed,
                labour_assessed=labour_assessed,
                **{
                    k: v
                    for k, v in inv_dict.items()
                    if k in InvoiceData.__dataclass_fields__
                },
            )

        return cls(
            insurance=_build(InsuranceData, d.get("insurance")),
            rc=_build(RCData, d.get("rc")),
            dl=_build(DLData, d.get("dl")),
            estimate=estimate,
            invoice=invoice,
            route_permit=_build(RoutePermitData, d.get("route_permit")),
            fitness_cert=_build(FitnessCertData, d.get("fitness_cert")),
            claim_form=_build(ClaimFormData, d.get("claim_form")),
            vehicle_image=_build(VehicleImageData, d.get("vehicle_image")),
            accident_doc=_build(AccidentDocData, d.get("accident_doc")),
            survey_report=_build(SurveyReportData, d.get("survey_report")),
            motor_survey_report=_build(
                MotorSurveyReportData, d.get("motor_survey_report")
            ),
            rc_status=_build(RcStatusData, d.get("rc_status")),
        )


DocumentType = Literal[
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
]
