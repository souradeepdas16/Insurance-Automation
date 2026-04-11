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


@dataclass
class InvoicePart:
    name: str = ""
    assessed_price: float = 0.0


@dataclass
class InvoiceData:
    parts_assessed: list[InvoicePart] = field(default_factory=list)
    labour_assessed_total: float = 0.0
    invoice_number: str = ""
    invoice_date: str = ""
    dealer_name: str = ""
    dealer_address: str = ""
    total_amount: float = 0.0
    gst_amount: float = 0.0


@dataclass
class RoutePermitData:
    permit_no: str = ""
    permit_holder_name: str = ""
    valid_upto: str = ""
    type_of_permit: str = ""
    route_area: str = ""


@dataclass
class FitnessCertData:
    valid_upto: str = ""


@dataclass
class ClaimFormData:
    date_of_accident: str = ""
    place_of_accident: str = ""


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


DocumentType = Literal[
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
]
