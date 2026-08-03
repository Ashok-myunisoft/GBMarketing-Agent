"""GSTIN discovery for the lead enrichment pipeline.

A single Google search ("<company name> GST Number") is enough to surface a
GSTIN directly in the rendered result snippets for most Indian companies, so
this reads the search-results page itself rather than following any link.
The first candidate that passes the GSTIN checksum is returned; no scoring
or LLM verification is involved - the checksum is the only correctness
guarantee, matching how a human would eyeball the same search results.
"""

import logging
import re
from typing import Optional

from services.browser_service import BrowserService
from services.google_search_service import GoogleSearchService

logger = logging.getLogger(__name__)

GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", re.IGNORECASE)
GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class GstEnrichmentService:
    """Finds a verified GSTIN from a single rendered Google-results page."""

    def __init__(self, browser: BrowserService):
        self._browser = browser
        self._google = GoogleSearchService(browser)

    def resolve(self, company_name: Optional[str]) -> Optional[str]:
        """Returns a checksum-valid GSTIN found for the company, or None."""
        if not company_name:
            return None

        context = self._browser.new_context()
        try:
            text = self._google.search_text(f"{company_name} GST Number", context)
            return self._first_valid_gstin(text)
        except Exception as exc:
            logger.warning("GST search failed for %r: %s", company_name, exc)
            return None
        finally:
            context.close()

    @classmethod
    def _first_valid_gstin(cls, text: str) -> Optional[str]:
        for raw in GSTIN_PATTERN.findall(text or ""):
            gst = raw.upper()
            if cls._is_valid_gstin(gst):
                return gst
        return None

    @staticmethod
    def _is_valid_gstin(gst: str) -> bool:
        if not GSTIN_PATTERN.fullmatch(gst):
            return False
        total, factor = 0, 1
        for char in gst[:-1]:
            value = GSTIN_CHARSET.index(char) * factor
            total += value // 36 + value % 36
            factor = 2 if factor == 1 else 1
        return gst[-1] == GSTIN_CHARSET[(36 - total % 36) % 36]
