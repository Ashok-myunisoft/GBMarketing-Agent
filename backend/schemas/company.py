from pydantic import BaseModel, Field
from typing import Optional


class Company(BaseModel):
    company_name: str
    website: Optional[str] = None
    phone: Optional[str] = None
    phone_alt: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    industry: Optional[str] = None
    turnover: Optional[str] = None
    employee_count: Optional[str] = None
    gst: Optional[str] = None
    cin: Optional[str] = None
    region: Optional[str] = None
    remarks: Optional[str] = None
    followup: Optional[str] = None

    # Named individual contact, when the company's own website actually
    # publishes one (rare for manufacturing/industrial B2B - confirmed
    # by hand across several live sites, most only offer a generic
    # enquiry form or company-level phone/email, not a named person).
    contact_person: Optional[str] = None
    designation: Optional[str] = None
    linkedin_url: Optional[str] = None

    # Validation deliberately distinguishes an explicitly rejected record
    # from one whose financial/industry data simply was not public.
    validation_status: str = "pending"
    validation_notes: list[str] = Field(default_factory=list)

    # Populated by the Tavily/LLM enrichment pipeline (services/enrichment)
    # when available. Additive only - no existing field's meaning changes,
    # and none of these are written to the Excel export (ExportAgent reads
    # only its fixed EXPORT_COLUMNS list), so the export format is untouched.
    country: Optional[str] = None
    pincode: Optional[str] = None
    business_category: Optional[str] = None
    field_confidence: dict[str, int] = Field(default_factory=dict)
    field_evidence: dict[str, str] = Field(default_factory=dict)
    field_sources: dict[str, str] = Field(default_factory=dict)
    # Internal enrichment metadata; deliberately not part of the fixed Excel
    # layout so existing workbooks remain compatible.
    field_status: dict[str, str] = Field(default_factory=dict)
    turnover_financial_year: Optional[str] = None
    turnover_metric: Optional[str] = None
