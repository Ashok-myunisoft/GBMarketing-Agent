"""
Fetches ranked URLs (from Tavily search + source_ranker) via the existing
Playwright-backed BrowserService, and discovers further same-site pages
worth visiting (team/leadership/management/board/annual-report/etc).

This is the same "keyword-hinted internal link discovery, visit until the
page budget runs out" pattern already used by
gst_turnover_enrichment/website_source.py's collect() and
enrichment_agent.py's _supplemental_urls, generalized here to the fuller
page-type list this pipeline needs to cover. No Playwright lifecycle code
is duplicated - BrowserService already owns that.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

from core.config import settings
from services.browser_service import BrowserService
from services.contact_extraction.models import PageCategory
from services.contact_extraction.page_classifier import classify as classify_page
from services.gst_turnover_enrichment.pdf_utils import same_site_pdf_links

logger = logging.getLogger(__name__)

# Expands enrichment_agent.CONTACT_PAGE_LINK_TEXT / website_source.LINK_TEXT_HINTS
# to the fuller set of page types this pipeline should discover.
LINK_TEXT_HINTS = (
    "about", "about-us", "contact", "contact-us", "legal", "privacy",
    "team", "our-team", "leadership", "management", "board", "directors",
    "board-of-directors", "company", "our-story", "investor", "investors",
    "financials", "financial", "downloads", "download", "media", "press",
    "certificates", "certification", "registration", "gst", "gstin",
    "annual-report", "annual report", "reports", "brochure", "profile",
)
_PRIORITY_HINTS = (
    "gst", "gstin", "annual-report", "annual report", "investor",
    "financials", "financial", "registration", "certificates", "board",
)


@dataclass
class CrawledPage:
    url: str
    category: "PageCategory" = PageCategory.OTHER
    title: str = ""
    text: str = ""
    html: str = ""
    # (absolute_pdf_url, link label+path) found on this page - see
    # gst_turnover_enrichment/pdf_utils.py.same_site_pdf_links. Populated so
    # a company's own GST certificate/annual report/brochure PDFs get fed to
    # the LLM extractor even when Tavily didn't independently surface them.
    pdf_links: "list[tuple[str, str]]" = field(default_factory=list)


def discover_internal_links(page, base_url: str, limit: int) -> "list[str]":
    """Returns a bounded, priority-ranked list of same-site pages worth
    visiting next, found via keyword-hinted anchor text/href on `page`
    (already loaded at `base_url`)."""

    if limit <= 0:
        return []

    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    ranked: "list[tuple[int, str]]" = []
    try:
        links = page.locator("a[href]")
        count = links.count()
    except Exception:
        return []

    for i in range(count):
        try:
            href = links.nth(i).get_attribute("href") or ""
            label = (links.nth(i).inner_text(timeout=500) or "").lower()
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if parsed.scheme not in {"http", "https"} or parsed.netloc.lower().removeprefix("www.") != base_host:
                continue
            # A PDF link (e.g. "company-profile.pdf") can match these same
            # keyword hints ("profile", "annual-report", "brochure"...) but
            # must never go through browser.goto() - Playwright treats it as
            # a download, not a navigable page, and the navigation fails
            # after burning its full retry budget (see BrowserService.goto).
            # same_site_pdf_links() (_read_pdf_links below) already collects
            # every same-site PDF on each visited page for the existing PDF
            # extraction path, so skipping it here loses nothing.
            if ".pdf" in parsed.path.lower():
                continue
            haystack = f"{label} {parsed.path.lower()}"
            if not any(term in haystack for term in LINK_TEXT_HINTS):
                continue
            priority = 0 if any(term in haystack for term in _PRIORITY_HINTS) else 1
            ranked.append((priority, absolute))
        except Exception:
            continue

    return list(dict.fromkeys(url for _, url in sorted(ranked)))[:limit]


def _read_page(page) -> "tuple[str, str, str]":
    try:
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        text = ""
    try:
        html = page.content()
    except Exception:
        html = ""
    try:
        title = page.title()
    except Exception:
        title = ""
    return text or "", html or "", title or ""


def _read_pdf_links(page) -> "list[tuple[str, str]]":
    try:
        html = page.content()
    except Exception:
        return []
    try:
        return same_site_pdf_links(html, page.url, limit=6)
    except Exception:
        return []


def crawl(browser: BrowserService, start_url: str, max_pages: Optional[int] = None) -> "list[CrawledPage]":
    """Visits `start_url` plus a bounded set of same-site pages discovered
    via keyword-hinted internal links, returning each page's rendered
    text/HTML. Mirrors gst_turnover_enrichment/website_source.py's collect()."""

    max_pages = max_pages or settings.TAVILY_MAX_PAGES
    pages: "list[CrawledPage]" = []

    context = browser.new_context()
    try:
        page = browser.new_page(context)
        try:
            browser.goto(page, start_url)

            urls_to_visit = [page.url] + discover_internal_links(page, page.url, max(0, max_pages - 1))

            for index, url in enumerate(urls_to_visit[:max_pages]):
                if index > 0:
                    try:
                        browser.goto(page, url)
                    except Exception:
                        continue

                text, html, title = _read_page(page)
                pages.append(
                    CrawledPage(
                        url=page.url, category=classify_page(page.url), title=title, text=text, html=html,
                        pdf_links=_read_pdf_links(page),
                    )
                )
            return pages
        finally:
            page.close()
    except Exception as ex:
        logger.warning("HTML crawl failed for %s: %s", start_url, ex)
        return pages
    finally:
        context.close()


def fetch_single_page(browser: BrowserService, url: str) -> Optional[CrawledPage]:
    """One-shot fetch for a standalone URL (a directory listing, a
    government page) that shouldn't trigger a deeper same-site crawl."""

    context = browser.new_context()
    try:
        page = browser.new_page(context)
        try:
            browser.goto(page, url)
            text, html, title = _read_page(page)
            return CrawledPage(
                url=page.url, category=classify_page(page.url), title=title, text=text, html=html,
                pdf_links=_read_pdf_links(page),
            )
        finally:
            page.close()
    except Exception as ex:
        logger.warning("Single-page fetch failed for %s: %s", url, ex)
        return None
    finally:
        context.close()