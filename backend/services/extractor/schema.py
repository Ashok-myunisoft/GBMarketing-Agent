from typing import Optional

from pydantic import BaseModel, Field


class ExtractedCompanyRecord(BaseModel):
    """The LLM's structured-extraction output for one company, built from
    the unified document (services/crawler/document_builder.py). Every
    scalar field is optional - the model is instructed to leave a field
    empty rather than guess, per the "never hallucinate" rule in
    app/prompts/company_extraction.md.

    confidence/evidence/source_url are keyed per field name (see the
    prompt's EVIDENCE STORAGE contract), not one score for the whole
    record - so a confident contact name and an uncertain industry label
    from the same document are tracked independently.
    """

    company_name: Optional[str] = None
    website: Optional[str] = None
    contact_person: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    mobile: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    industry: Optional[str] = None
    business_category: Optional[str] = None

    confidence: "dict[str, int]" = Field(default_factory=dict)
    evidence: "dict[str, str]" = Field(default_factory=dict)
    source_url: "dict[str, str]" = Field(default_factory=dict)

    def confidence_for(self, field_name: str) -> int:
        return int(self.confidence.get(field_name, 0) or 0)

    def is_empty(self) -> bool:
        return not any(
            getattr(self, name)
            for name in (
                "company_name", "website",
                "contact_person", "designation", "email", "mobile", "address",
                "city", "state", "country", "pincode", "industry", "business_category",
            )
        )
