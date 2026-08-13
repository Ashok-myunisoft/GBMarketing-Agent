from pydantic import BaseModel, Field
from typing import Any, List, Optional

from schemas.company import Company


class RemovedCompany(BaseModel):
    """One company that didn't make it into the final results, and why."""

    company_name: str
    # Pipeline stage that dropped it: "search_dedup" (duplicate across
    # providers) or "validation" (duplicate/out-of-area/ICP rejection).
    stage: str
    reason: str


class PipelineStats(BaseModel):
    """Company counts at each real stage of the pipeline, for answering
    "how many did we start with, and why did we end up with fewer" without
    re-running the job. Only search and validation actually change the
    company count (enrichment fills in fields on the same list, see
    orchestrator/workflow.py), so those are the two after_* counts that can
    differ from after_search_dedup.
    """

    raw_fetched: int = 0
    after_search_dedup: int = 0
    after_enrichment: int = 0
    after_validation: int = 0
    removed: List[RemovedCompany] = Field(default_factory=list)


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

    # Search result cap requested in the user's query. None means use the
    # existing SearchRequest maximum.
    requested_company_count: Optional[int] = None

    # Explicit size/registration filters detected in the user's own query
    # (see PlannerAgent). None/False means no such filter was requested, in
    # which case ValidationAgent treats turnover/employee-count/GST as
    # informational enrichment only and never rejects a company for it.
    requested_turnover_floor_cr: Optional[float] = None
    requested_employee_floor: Optional[float] = None
    requested_gst_required: bool = False

    # Companies discovered later
    companies: List[Company] = Field(default_factory=list)

    # Optional integrations supplied by the caller.  No historic Excel file
    # or export file is assumed until a path is explicitly provided.
    existing_excel_path: Optional[str] = None
    export_path: Optional[str] = None

    # How many companies from this run were newly added to the export vs.
    # skipped as duplicates/rejected. Lets callers tell "ran fine, nothing
    # new found" apart from "broke silently" without opening the Excel file.
    validation_stats: Optional[dict[str, Any]] = None

    # Company counts at each pipeline stage (raw fetched -> after search
    # dedup -> after enrichment -> after validation), plus the specific
    # reason every dropped company was removed. Populated incrementally by
    # WorkflowOrchestrator as each step runs.
    pipeline_stats: Optional[PipelineStats] = None

    # Logs
    logs: List[str] = Field(default_factory=list)
