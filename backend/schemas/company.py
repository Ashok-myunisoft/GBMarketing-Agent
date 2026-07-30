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
    gst_confidence: Optional[int] = None
    gst_sources: list[str] = Field(default_factory=list)
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
