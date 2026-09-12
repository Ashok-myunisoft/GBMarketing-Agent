"""
Multi-query search strategy: a single query for a company misses most of
what's discoverable, so every company is searched with several targeted
variations and the results merged (see source_ranker.rank_and_filter).

build_refined_queries() is used only on the confidence-gated retry pass
(a field's confidence came back below settings.ENRICHMENT_TAVILY_MIN_CONFIDENCE).

GST/turnover-specific queries (GSTIN, GST certificate, Annual Turnover,
Annual Report, revenue from operations, Financial Statements) were removed
from both lists - that pair no longer comes from this pipeline at all (see
services/gst_turnover_enrichment, which has its own SearXNG/Crawl4AI search
and crawl), so searching for them here would just spend Tavily quota on
results nothing reads.
"""


def build_initial_queries(company_name: str) -> "list[str]":
    name = company_name.strip()
    return [
        f"{name} official website",
        f"{name} managing director",
        f"{name} leadership",
    ]


def build_refined_queries(company_name: str) -> "list[str]":
    name = company_name.strip()
    return [
        f"{name} Managing Director",
    ]
