from typing import List

from pydantic import BaseModel, Field

from schemas.lead import Lead


class WorkflowContext(BaseModel):

    user_query: str

    workflow: str | None = None

    intent: str | None = None

    industry: str | None = None

    location: str | None = None

    buyer_persona: str | None = None

    turnover: str | None = None

    leads: List[Lead] = Field(default_factory=list)