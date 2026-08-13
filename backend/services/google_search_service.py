"""Deprecated compatibility wrapper for historical Google search imports.

No method in this module makes a Google request.  Tavily is used for web
discovery so old imports do not reintroduce browser-based Google automation.
"""

import logging
from playwright.sync_api import BrowserContext
from services.browser_service import BrowserService
from services.tavily.tavily_search import TavilySearchService

logger = logging.getLogger(__name__)

class GoogleSearchService:
    """Compatibility facade backed by Tavily; ``context`` is ignored."""

    def __init__(self, browser: BrowserService):
        self._search = TavilySearchService()
        self.last_blocked = False

    def search_text(self, query: str, context: BrowserContext) -> str:
        """Returns Tavily result evidence as text for legacy callers."""
        return "\n".join(
            " ".join(part for part in (result.title, result.content, result.raw_content or "") if part)
            for result in self._search.search(query, max_results=5)
        ) if self._search.is_configured else ""

    def organic_result_urls(self, query: str, context: BrowserContext, limit: int = 10) -> list[str]:
        if not self._search.is_configured:
            return []
        return [result.url for result in self._search.search(query, max_results=limit)][:limit]
