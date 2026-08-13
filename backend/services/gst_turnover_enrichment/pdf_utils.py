"""
Fetches and reads text from a bounded number of same-site PDFs linked on a
page - generalized from enrichment_agent.py's original GST-only PDF scan so
both GST and turnover extraction can run against the same fetched text.
"""

import logging
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from services.gst_turnover_enrichment.firecrawl_client import FirecrawlClient, extract_links

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 10

# Link text/URL hints that mark a PDF as an Annual Report / investor
# document specifically, scored higher than a generic company PDF.
_ANNUAL_REPORT_HINTS = ("annual", "report", "investor")
_PDF_PRIORITY_HINTS = (
    "gst", "registration", "certificate", "compliance", "tax",
    "annual", "report", "invoice",
)


def same_site_pdf_links(html: str, base_url: str, limit: int = 6) -> "list[tuple[str, str]]":
    """Returns [(absolute_pdf_url, lowercased link label+path), ...] for
    same-site PDFs linked in `html`, so callers can classify each one."""

    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    for absolute, label in extract_links(html, base_url):
        parsed = urlparse(absolute)
        if ".pdf" not in parsed.path.lower() or parsed.netloc.lower().removeprefix("www.") != base_host:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        results.append((absolute, f"{label.lower()} {parsed.path.lower()} {parsed.query.lower()}"))
        if len(results) >= limit:
            break

    def sort_key(item: tuple[str, str]) -> int:
        _, label = item
        for index, hint in enumerate(_PDF_PRIORITY_HINTS):
            if hint in label:
                return index
        return len(_PDF_PRIORITY_HINTS)

    return sorted(results, key=sort_key)


def is_annual_report(label: str) -> bool:
    return any(hint in label for hint in _ANNUAL_REPORT_HINTS)


def fetch_pdf_text(
    firecrawl: FirecrawlClient, url: str, max_bytes: int = MAX_PDF_BYTES, max_pages: int = MAX_PDF_PAGES
) -> Optional[str]:
    """Firecrawl-first PDF text extraction, falling back to a raw fetch + pypdf
    read only if Firecrawl itself fails - keeps a working path either way."""

    page = firecrawl.scrape(url)
    if page.success and (page.markdown or page.html):
        return page.markdown or page.html

    return _fetch_pdf_text_fallback(url, max_bytes=max_bytes, max_pages=max_pages)


def _fetch_pdf_text_fallback(url: str, max_bytes: int = MAX_PDF_BYTES, max_pages: int = MAX_PDF_PAGES) -> Optional[str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=15) as response:
            payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            return None
        reader = PdfReader(BytesIO(payload))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
    except Exception as ex:
        logger.debug("Failed to fetch/read PDF %s: %s", url, ex)
        return None
