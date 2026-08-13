"""
Combines every crawled HTML page and PDF into ONE unified document, sectioned
by type, so a single LLM call can reason across all of them at once instead
of per-page regex extraction. HTML section names reuse
contact_extraction/page_classifier.py's PageCategory - the same categories
that already drive contact scoring - so "leadership" here means exactly
what it means there.
"""

from core.config import settings
from services.crawler.html_crawler import CrawledPage
from services.crawler.pdf_crawler import CrawledPdf
from services.contact_extraction.models import PageCategory

_PAGE_SECTION_NAMES = {
    PageCategory.LEADERSHIP: "LEADERSHIP",
    PageCategory.BOARD: "MANAGEMENT",
    PageCategory.ABOUT: "ABOUT",
    PageCategory.TEAM: "TEAM",
    PageCategory.CONTACT: "CONTACT",
    PageCategory.OTHER: "HOME",
}
_PDF_SECTION_NAMES = {
    "gst_certificate": "GST CERTIFICATE",
    "annual_report": "ANNUAL REPORT",
    "financial_report": "FINANCIAL REPORT",
    "brochure": "BROCHURE",
    "document": "DOCUMENT",
}


def _section(name: str, url: str, text: str) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    return f"===== {name} ({url}) =====\n{body}\n"


def build_document(
    pages: "list[CrawledPage]",
    pdfs: "list[CrawledPdf]",
    max_chars: "int | None" = None,
) -> str:
    """Assembles the unified "===== SECTION =====" document, capped at
    max_chars. Higher-value sections (GST/financial/leadership) are kept
    ahead of generic ones (a plain HOME page) so truncation drops the least
    valuable content first when the budget is exceeded."""

    max_chars = max_chars or settings.TAVILY_MAX_DOCUMENT_CHARS

    sections: "list[tuple[int, str]]" = []
    for page in pages:
        name = _PAGE_SECTION_NAMES.get(page.category, "HOME")
        priority = 1 if name == "HOME" else 0
        text = _section(name, page.url, page.text)
        if text:
            sections.append((priority, text))

    for pdf in pdfs:
        name = _PDF_SECTION_NAMES.get(pdf.category, "DOCUMENT")
        text = _section(name, pdf.url, pdf.text)
        if text:
            sections.append((0, text))

    sections.sort(key=lambda item: item[0])

    document = ""
    for _, section_text in sections:
        if len(document) + len(section_text) > max_chars:
            remaining = max_chars - len(document)
            if remaining > 200:
                document += section_text[:remaining]
            break
        document += section_text
    return document.strip()
