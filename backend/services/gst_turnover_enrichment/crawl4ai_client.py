"""
Crawl4AI client used only as a fallback to Firecrawl for GST/turnover
enrichment (see service.py). Firecrawl is always tried first; this is
invoked only when Firecrawl fails, errors, times out, or its content
yields no valid value for the existing extraction logic in
gst_extraction.py / turnover_extraction.py.

Never raises - any failure (package missing, navigation error, timeout)
degrades to a controlled failure result so a single company's GST/turnover
lookup can never crash or stop the rest of the batch.
"""

import asyncio
import logging
import queue
import threading
from dataclasses import dataclass

from core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Crawl4AIPage:
    url: str
    content: str = ""
    success: bool = False
    error: str = ""


class Crawl4AIClient:
    """Thin, defensive wrapper around Crawl4AI's async crawler."""

    def crawl(self, url: str) -> Crawl4AIPage:
        """Fetch one URL through Crawl4AI, rendering JavaScript. Never raises."""
        if not url or not url.strip():
            return Crawl4AIPage(url=url or "", error="Empty URL")

        try:
            # FastAPI runs jobs inside an already-active event loop.  Calling
            # asyncio.run there raises immediately, so give Crawl4AI its own
            # short-lived loop in a worker thread when necessary.
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._crawl_async(url))

        try:
            return self._crawl_in_worker_thread(url)
        except Exception as ex:
            logger.warning("[CRAWL4AI] url=%s status=failed reason=%s", url, ex)
            return Crawl4AIPage(url=url, error=str(ex))

    def _crawl_in_worker_thread(self, url: str) -> Crawl4AIPage:
        result_queue: "queue.Queue[object]" = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                result_queue.put(asyncio.run(self._crawl_async(url)))
            except BaseException as ex:  # surfaced in the calling thread below
                result_queue.put(ex)

        worker = threading.Thread(target=run, name="crawl4ai-event-loop", daemon=True)
        worker.start()
        # _crawl_async already has a navigation timeout.  This extra bound
        # protects the request handler from a crawler shutdown that stalls.
        worker.join(settings.CRAWL4AI_TIMEOUT_SECONDS + 5)
        if worker.is_alive():
            raise TimeoutError("crawl4ai worker did not finish before deadline")
        result = result_queue.get_nowait()
        if isinstance(result, BaseException):
            raise result
        return result  # type: ignore[return-value]

    async def _crawl_async(self, url: str) -> Crawl4AIPage:
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        except ImportError as ex:
            logger.warning("[CRAWL4AI] url=%s status=failed reason=not_installed detail=%s", url, ex)
            return Crawl4AIPage(url=url, error="crawl4ai is not installed")

        timeout_seconds = settings.CRAWL4AI_TIMEOUT_SECONDS
        browser_config = BrowserConfig(headless=True, verbose=False)
        run_config = CrawlerRunConfig(page_timeout=timeout_seconds * 1000)

        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await asyncio.wait_for(
                    crawler.arun(url=url, config=run_config), timeout=timeout_seconds
                )
        except asyncio.TimeoutError:
            logger.info("[CRAWL4AI] url=%s status=timeout", url)
            return Crawl4AIPage(url=url, error="timeout")
        except Exception as ex:
            logger.warning("[CRAWL4AI] url=%s status=failed reason=%s", url, ex)
            return Crawl4AIPage(url=url, error=str(ex))

        if not getattr(result, "success", False):
            error = getattr(result, "error_message", "") or "crawl failed"
            logger.info("[CRAWL4AI] url=%s status=failed reason=%s", url, error)
            return Crawl4AIPage(url=url, error=error)

        markdown = getattr(result, "markdown", "") or ""
        content = getattr(markdown, "raw_markdown", None)
        if content is None:
            content = markdown if isinstance(markdown, str) else str(markdown)
        content = content or (getattr(result, "html", "") or "")

        if not content:
            return Crawl4AIPage(url=url, error="empty content")

        logger.info("[CRAWL4AI] url=%s status=success", url)
        return Crawl4AIPage(url=url, content=content, success=True)
