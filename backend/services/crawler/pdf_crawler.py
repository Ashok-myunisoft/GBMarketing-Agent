"""
Thin wrapper over gst_turnover_enrichment/pdf_utils.py's already-working PDF
fetch (pypdf-based text extraction, size/page caps), generalized with a
broader category label (GST certificate / annual report / financial report /
brochure / generic) so document_builder can section a PDF's extracted text
correctly. No PDF-parsing logic is duplicated here.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from services.gst_turnover_enrichment.firecrawl_client import FirecrawlClient
from services.gst_turnover_enrichment.pdf_utils import fetch_pdf_text, is_annual_report

logger = logging.getLogger(__name__)

_CATEGORY_HINTS = (
    ("gst_certificate", ("gst certificate", "gst-certificate", "gstin certificate")),
    ("annual_report", ("annual report", "annual-report")),
    ("financial_report", ("financial statement", "financial-statement", "balance sheet", "financials")),
    ("brochure", ("brochure", "catalogue", "catalog", "profile")),
)


@dataclass
class CrawledPdf:
    url: str
    category: str
    text: str


def classify_pdf(label: str) -> str:
    haystack = (label or "").lower()
    for category, hints in _CATEGORY_HINTS:
        if any(hint in haystack for hint in hints):
            return category
    return "annual_report" if is_annual_report(haystack) else "document"


def fetch_pdf(url: str, label: str = "", firecrawl: Optional[FirecrawlClient] = None) -> Optional[CrawledPdf]:
    """Downloads and extracts text from one PDF at any absolute URL - not
    restricted to the company's own domain, since annual reports/financial
    statements are frequently hosted off-site (stock exchange, investor
    relations portal).

    `firecrawl` is optional so existing callers that don't have a client
    handy keep working: fetch_pdf_text() requires one (Firecrawl-first, then
    a raw-fetch+pypdf fallback), so a fresh, unshared FirecrawlClient() is
    created here when the caller doesn't pass one - it degrades to the same
    fallback path on its own if Firecrawl isn't configured/reachable.

    Any failure (bad Firecrawl argument, network error, unreadable PDF) is
    caught and logged here rather than raised, so one bad PDF never takes
    down the rest of a company's contact enrichment pass - see
    services/enrichment/company_enrichment.py's ERROR ISOLATION contract.
    """

    try:
        text = fetch_pdf_text(firecrawl or FirecrawlClient(), url)
    except Exception as ex:
        logger.warning("PDF fetch failed for %s: %s", url, ex)
        return None
    if not text:
        return None
    return CrawledPdf(url=url, category=classify_pdf(f"{label} {url}"), text=text)