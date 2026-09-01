
import logging
import re
from typing import Optional

from playwright.sync_api import BrowserContext

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

        self._context: Optional[BrowserContext] = None

    @property
    def last_blocked(self) -> bool:
        """Whether the most recent resolve() call was blocked by Google
        rather than genuinely finding no GSTIN - see GoogleSearchService."""
        return self._google.last_blocked

    def resolve(self, company_name: Optional[str]) -> Optional[str]:
        """Returns a checksum-valid GSTIN found for the company, or None."""
        if not company_name:
            return None

        try:
            text = self._google.search_text(f"{company_name} GST Number", self._get_context())
            return self._first_valid_gstin(text)
        except Exception as exc:
            logger.warning("GST search failed for %r: %s", company_name, exc)
            return None

    def _get_context(self) -> BrowserContext:
        if self._context is None:
            self._context = self._browser.new_context()
        return self._context

    def close(self) -> None:
        """Closes the shared context. Call once the owning worker is done with this service."""
        if self._context is not None:
            self._context.close()
            self._context = None

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
