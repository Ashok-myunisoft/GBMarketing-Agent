from pydantic import BaseModel
from typing import Optional

class QueryUnderstanding(BaseModel):
    intent: str
    industry: Optional[str] = None
    sub_industry: Optional[str] = None
    buyer_persona: Optional[str] = None
    location: Optional[str] = None
    workflow: Optional[str] = None
    # Optional user-requested result count, e.g. "find 20 companies".
    # PlannerAgent also derives this deterministically from the raw query so
    # a missed LLM field can never cause an oversized search.
    requested_company_count: Optional[int] = None
    confidence: float
    
