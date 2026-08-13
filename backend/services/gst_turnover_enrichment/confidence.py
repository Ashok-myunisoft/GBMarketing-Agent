"""
Confidence scoring for GST/turnover candidates gathered across every tier.
Points are additive per source that surfaced a given value, plus a bonus
when two or more distinct source types independently agree on the exact
same value - the strongest signal available, since it isn't just one page's
unverified claim.
"""

from urllib.parse import urlparse
from services.gst_turnover_enrichment.models import SourceCandidate

GST_SOURCE_POINTS = {"authoritative": 95, "official_document": 92, "annual_report": 90, "filesure": 85, "website": 82, "pdf": 80, "search": 80}

# "Annual Report" and "Company PDF" are scored separately per the spec's own
# table - a PDF is only tagged "annual_report" when its link text/URL says
# so (pdf_utils.is_annual_report); anything else found in a PDF is the
# generic, lower-weighted "pdf" tier.
TURNOVER_SOURCE_POINTS = {"annual_report": 95, "official_document": 92, "filesure": 85, "website": 80, "pdf": 80, "search": 80}

MATCHING_BONUS = 30
MAX_CONFIDENCE = 100


def score_candidates(candidates: "list[SourceCandidate]", points_table: dict) -> "dict[str, int]":
    """Returns {value: confidence} for every distinct value found across all candidates."""

    totals: dict[str, int] = {}
    domains_seen: dict[str, set] = {}

    for candidate in candidates:
        # Multiple pages from one domain are one assertion, not independent
        # corroboration. Keep the strongest source tier for that domain.
        domain = urlparse(candidate.source_url).netloc.lower().removeprefix("www.") or candidate.source_url
        by_domain = domains_seen.setdefault(candidate.value, {})
        by_domain[domain] = max(by_domain.get(domain, 0), points_table.get(candidate.source, 0))

    for value, domain_points in domains_seen.items():
        # One source establishes the base. A second independent domain can
        # corroborate it, but cannot inflate confidence beyond the cap.
        strongest = max(domain_points.values())
        totals[value] = strongest + (MATCHING_BONUS if len(domain_points) > 1 else 0)

    return {value: min(MAX_CONFIDENCE, total) for value, total in totals.items()}
