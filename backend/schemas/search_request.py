from pydantic import BaseModel, Field
from typing import List, Optional


class SearchRequest(BaseModel):
    """
    Input contract for SearchAgent. Built by WorkflowOrchestrator from
    WorkflowContext so SearchAgent never depends on WorkflowContext directly.
    """

    # Target market segment to search within. Without it, SearchAgent has
    # no domain to constrain results to.
    industry: Optional[str] = None

    # Geographic scope (city/state/region) so results match where the
    # user actually wants leads, not just what industry they're in.
    location: Optional[str] = None

    # Additional free-text search terms (e.g. buyer persona, product
    # names) that refine the query beyond industry + location. A list
    # rather than a single string so multiple qualifiers can be combined
    # independently and inspected/filtered individually downstream.
    keywords: List[str] = Field(default_factory=list)

    # Upper bound on how many companies SearchAgent should return. This is
    # a safety ceiling, not a target - it must never be the reason a run
    # returns fewer companies than actually exist for the query. Each
    # provider already stops on its own once a region is genuinely
    # exhausted (GoogleMapsProvider/BusinessDirectoryProvider both scroll
    # or paginate until listing growth plateaus, backed by their own
    # MAX_SCROLL_ATTEMPTS/MAX_TRADEINDIA_PAGES caps on request duration),
    # so this only needs to sit far above any real result count.
    max_results: int = 100_000
