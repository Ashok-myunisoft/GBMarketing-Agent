import logging
from typing import Optional

from playwright.sync_api import (
    sync_playwright,
    Playwright,
    Browser,
    BrowserContext,
    Page,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)

from core.config import settings

logger = logging.getLogger(__name__)


class BrowserService:
    """
    Owns the Playwright browser lifecycle so providers never launch or
    close a browser themselves. Providers ask this service for a Page
    and drive it with Playwright locators; this service only handles
    startup, shutdown, context/page creation, and navigation
    timeouts/retries.
    """

    def __init__(
        self,
        headless: Optional[bool] = None,
        timeout_ms: Optional[int] = None,
        max_retries: int = 2,
    ):
        self._headless = settings.PLAYWRIGHT_HEADLESS if headless is None else headless
        self._timeout_ms = settings.PLAYWRIGHT_TIMEOUT_MS if timeout_ms is None else timeout_ms
        self._max_retries = max_retries

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    @property
    def is_running(self) -> bool:
        return self._browser is not None

    def start(self) -> None:
        """Launch the browser process. Safe to call more than once."""

        if self.is_running:
            return

        logger.info("Starting browser (headless=%s)", self._headless)

        self._playwright = sync_playwright().start()

        try:
            self._browser = self._playwright.chromium.launch(headless=self._headless)
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise

    def stop(self) -> None:
        """Shut down the browser process. Safe to call more than once."""

        if self._browser is not None:
            self._browser.close()
            self._browser = None

        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

        logger.info("Browser stopped")

    def __enter__(self) -> "BrowserService":
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()

    def new_context(self, **kwargs) -> BrowserContext:
        """Create an isolated browser context (own cookies/storage)."""

        if not self.is_running:
            raise RuntimeError("BrowserService.start() must be called before new_context()")

        return self._browser.new_context(**kwargs)

    def new_page(self, context: Optional[BrowserContext] = None) -> Page:
        """Create a page inside the given context, or a fresh context if none is passed."""

        owned_context = context or self.new_context()
        page = owned_context.new_page()
        page.set_default_timeout(self._timeout_ms)
        return page

    def goto(self, page: Page, url: str, wait_until: str = "domcontentloaded") -> None:
        """Navigate to a URL, retrying transient failures up to max_retries times."""

        attempts = self._max_retries + 1
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                logger.info("Navigating to %s (attempt %d/%d)", url, attempt, attempts)
                page.goto(url, wait_until=wait_until, timeout=self._timeout_ms)
                return
            except (PlaywrightTimeoutError, PlaywrightError) as e:
                last_error = e
                logger.warning(
                    "Navigation failed for %s (attempt %d/%d): %s", url, attempt, attempts, e
                )

        raise RuntimeError(f"Failed to load '{url}' after {attempts} attempt(s)") from last_error

    def get_page_content(self, url: str, wait_until: str = "domcontentloaded") -> str:
        """
        One-shot convenience for simple HTML fetches: starts the browser
        if it isn't already running, opens an isolated context/page,
        navigates, and returns the rendered HTML.

        Providers that need locator-based extraction across a session
        (e.g. paginated results) should use start()/new_page()/goto()
        directly and call stop() once when fully done, instead of this.
        """

        owns_lifecycle = not self.is_running

        if owns_lifecycle:
            self.start()

        context = self.new_context()

        try:
            page = self.new_page(context)
            self.goto(page, url, wait_until=wait_until)
            return page.content()
        finally:
            context.close()
            if owns_lifecycle:
                self.stop()
