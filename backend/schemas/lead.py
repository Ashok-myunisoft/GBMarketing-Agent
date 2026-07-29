from typing import List, Optional

from pydantic import BaseModel, Field

from schemas.company import Company
from schemas.contact import Contact


class Lead(BaseModel):

    company: Company

    # Search Information
    sources: List[str] = Field(default_factory=list)

    # Business Information
    turnover: Optional[str] = None

    employee_count: Optional[str] = None

    year_established: Optional[str] = None

    business_type: Optional[str] = None

    products: List[str] = Field(default_factory=list)

    # Contact Information
    contacts: List[Contact] = Field(default_factory=list)

    # AI Intelligence
    buyer_personas: List[str] = Field(default_factory=list)

    icp_match_score: Optional[float] = None

    lead_score: Optional[float] = None

    confidence: Optional[float] = None

    # Workflow
    validation_status: str = "pending"

    notes: List[str] = Field(default_factory=list)

    