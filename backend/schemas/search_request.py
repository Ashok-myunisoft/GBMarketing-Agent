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

    # Upper bound on how many companies SearchAgent should return. Caps
    # the workload handed to DeduplicationAgent/EnrichmentAgent later and
    # bounds provider request duration (GoogleMapsProvider scrolls until
    # this many results are loaded). Defaults high, reflecting a
    # "maximum companies" search rather than a quick preview.
    max_results: int = 100
