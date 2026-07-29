from pydantic import BaseModel, Field
from typing import List, Optional

from schemas.company import Company


class WorkflowContext(BaseModel):

    # Original user message
    user_query: str

    # AI identified intent
    intent: Optional[str] = None

    # Workflow selected
    workflow: Optional[str] = None

    # Industry detected by AI
    industry: Optional[str] = None

    # Location detected by AI
    location: Optional[str] = None

    # Buyer persona detected by AI
    buyer_persona: Optional[str] = None

    # Confidence score of the query understanding step
    confidence: Optional[float] = None

    # Companies discovered later
    companies: List[Company] = Field(default_factory=list)

    # Optional integrations supplied by the caller.  No historic Excel file
    # or export file is assumed until a path is explicitly provided.
    existing_excel_path: Optional[str] = None
    export_path: Optional[str] = None

    # Logs
    logs: List[str] = Field(default_factory=list)
