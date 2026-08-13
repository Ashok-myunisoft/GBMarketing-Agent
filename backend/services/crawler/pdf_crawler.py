"""
Thin wrapper over gst_turnover_enrichment/pdf_utils.py's already-working PDF
fetch (pypdf-based text extraction, size/page caps), generalized with a
broader category label (GST certificate / annual report / financial report /
brochure / generic) so document_builder can section a PDF's extracted text
correctly. No PDF-parsing logic is duplicated here.
"""

from dataclasses import dataclass
from typing import Optional

from services.gst_turnover_enrichment.pdf_utils import fetch_pdf_text, is_annual_report

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


def fetch_pdf(url: str, label: str = "") -> Optional[CrawledPdf]:
    """Downloads and extracts text from one PDF at any absolute URL - not
    restricted to the company's own domain, since annual reports/financial
    statements are frequently hosted off-site (stock exchange, investor
    relations portal)."""

    text = fetch_pdf_text(url)
    if not text:
        return None
    return CrawledPdf(url=url, category=classify_pdf(f"{label} {url}"), text=text)
