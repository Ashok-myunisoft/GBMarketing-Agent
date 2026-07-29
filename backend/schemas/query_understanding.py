from pydantic import BaseModel
from typing import Optional

class QueryUnderstanding(BaseModel):
    intent: str
    industry: Optional[str] = None
    sub_industry: Optional[str] = None
    buyer_persona: Optional[str] = None
    location: Optional[str] = None
    workflow: Optional[str] = None
    confidence: float
    