from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SourceCandidate:
    """One GST/turnover value found by a single tier on a single page/PDF."""

    value: str
    # "website" | "pdf" | "annual_report" | "jamku"
    source: str
    source_url: str
    currency: Optional[str] = None
    financial_year: Optional[str] = None
    metric: Optional[str] = None
    # "ai_web_search" is the OpenAI Responses API's own web-search research
    # (see openai_research_client.py) - the sole GST/turnover mechanism now.
    source_type: str = "scraped_page"
    # Short surrounding text (~1-2 lines) the value was actually found in -
    # lets ai_validator.py judge whose identity a GSTIN/turnover figure
    # belongs to, or reject a non-turnover financial metric, instead of
    # seeing only a bare value. Always optional/best-effort: an empty string
    # means no snippet was captured, never a required field.
    evidence: str = ""


@dataclass
class FieldResult:
    """The final resolved value for one field (GST or turnover), with the
    confidence score and distinct source types that supported it."""

    value: str = ""
    confidence: int = 0
    sources: list = field(default_factory=list)
    source_url: Optional[str] = None
    financial_year: Optional[str] = None
    metric: Optional[str] = None
    status: str = "not_found"
    source_type: Optional[str] = None
    currency: Optional[str] = None


@dataclass
class GstTurnoverResult:
    gst: FieldResult = field(default_factory=FieldResult)
    turnover: FieldResult = field(default_factory=FieldResult)
    # Always False - no source in this pipeline reaches live Google, so this
    # can never actually be tripped. Kept only so any caller still reading
    # this field (pre-dating the Firecrawl/Tavily-removal redesign) sees a
    # valid value rather than a missing attribute.
    gst_blocked: bool = False

    def as_dict(self) -> dict:
        return {
            "gst_number": self.gst.value,
            "gst_confidence": self.gst.confidence,
            "gst_sources": self.gst.sources,
            "turnover": self.turnover.value,
            "turnover_value": self.turnover.value or None,
            "turnover_unit": (self.turnover.value.split()[-1] if self.turnover.value else None),
            "turnover_currency": self.turnover.currency or "INR",
            "turnover_period": self.turnover.financial_year,
            "turnover_confidence": self.turnover.confidence,
            "turnover_sources": self.turnover.sources,
            "turnover_financial_year": self.turnover.financial_year,
            "turnover_metric": self.turnover.metric,
            "gst_source": self.gst.source_url,
            "turnover_source": self.turnover.source_url,
            "gst_source_type": self.gst.source_type,
            "turnover_source_type": self.turnover.source_type,
            "enrichment_status": self.gst.status if self.gst.value else self.turnover.status,
        }
