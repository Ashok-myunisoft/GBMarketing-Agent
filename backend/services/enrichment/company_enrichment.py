import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from core.config import settings
from services.browser_service import BrowserService
from services.crawler import document_builder
from services.crawler.html_crawler import CrawledPage, crawl, fetch_single_page
from services.crawler.pdf_crawler import CrawledPdf, fetch_pdf
from services.gst_turnover_enrichment.firecrawl_client import FirecrawlClient
from services.extractor import llm_extractor
from services.extractor.schema import ExtractedCompanyRecord
from services.tavily.base import SearchProvider, SearchResult
from services.tavily.query_builder import build_initial_queries, build_refined_queries
from services.tavily.source_ranker import rank_and_filter
from services.tavily.tavily_search import TavilySearchService
from services.validators import contact_validator, designation_validator

logger = logging.getLogger(__name__)

_MERGE_FIELDS = (
    "website", "email", "mobile", "address", "city", "state", "country",
    "pincode", "industry", "business_category",
)
# GST number/turnover are no longer part of this pipeline's output (see
# services/gst_turnover_enrichment - Firecrawl-only now, no Tavily
# involvement at all), so they're not tracked for retry-worthiness either.
_RETRY_TRACKED_FIELDS = ("contact_person", "designation")
_NON_MERGE_MODEL_FIELDS = {"confidence", "evidence", "source_url"}


@dataclass
class TavilyEnrichmentResult:
    """Ready-to-merge output of one gather() call."""

    contact_person: Optional[str] = None
    designation: Optional[str] = None
    contact_source_url: Optional[str] = None

    website: Optional[str] = None
    email: Optional[str] = None
    mobile: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    pincode: Optional[str] = None
    industry: Optional[str] = None
    business_category: Optional[str] = None

    field_confidence: "dict[str, int]" = field(default_factory=dict)
    field_evidence: "dict[str, str]" = field(default_factory=dict)
    field_sources: "dict[str, str]" = field(default_factory=dict)

    # The pages/PDFs this pass already crawled for this company - handed to
    # gst_turnover_enrichment so it can scan them first instead of repeating
    # an up-to-8-page+PDF crawl of a site this pipeline just visited.
    crawled_pages: "list[CrawledPage]" = field(default_factory=list)
    crawled_pdfs: "list[CrawledPdf]" = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.contact_person
            or self.website or self.email or self.mobile or self.address
            or self.city or self.state or self.country or self.pincode
            or self.industry or self.business_category
        )


class CompanyTavilyEnrichmentService:
    def __init__(
        self,
        browser: BrowserService,
        search_provider: Optional[SearchProvider] = None,
        firecrawl: Optional[FirecrawlClient] = None,
    ):
        self._browser = browser
        self._search = search_provider or TavilySearchService()
        # Same FirecrawlClient instance EnrichmentAgent already owns should
        # be passed in where possible (shares its global cache/rate
        # limiter/concurrency slots - see FirecrawlClient's docstring); a
        # fresh one is created here only as a fallback for callers/tests
        # that construct this service on its own.
        self._firecrawl = firecrawl or FirecrawlClient()

    def gather(self, company_name: str, website: Optional[str] = None) -> TavilyEnrichmentResult:
        has_known_website = bool(website and website.startswith("http"))
        if not settings.ENRICHMENT_USE_TAVILY_PIPELINE:
            return TavilyEnrichmentResult()
        if not self._search.is_configured and not has_known_website:
            # Nothing to search with and nothing to crawl either - genuinely
            # nothing this pipeline can do for this company right now.
            return TavilyEnrichmentResult()

        try:
            record, confidence, pages, pdfs = self._run_pass(
                company_name, website, build_initial_queries(company_name)
            )
            all_pages, all_pdfs = list(pages), list(pdfs)

            attempts = 0
            # Retrying only re-runs Tavily search with different wording -
            # without a working search provider it would just re-crawl the
            # identical website and ask the LLM the same question again, for
            # no possible gain, so skip it entirely in that case.
            while (
                self._search.is_configured
                and attempts < settings.ENRICHMENT_TAVILY_MAX_RETRIES
                and self._needs_retry(record, confidence)
            ):
                attempts += 1
                retry_record, retry_confidence, retry_pages, retry_pdfs = self._run_pass(
                    company_name, website, build_refined_queries(company_name)
                )
                record, confidence = self._merge_passes(record, confidence, retry_record, retry_confidence)
                all_pages.extend(retry_pages)
                all_pdfs.extend(retry_pdfs)

            return self._to_result(record, all_pages, all_pdfs)
        except Exception as ex:
            logger.warning("Tavily enrichment pipeline failed for '%s': %s", company_name, ex)
            return TavilyEnrichmentResult()

    @staticmethod
    def _needs_retry(record: ExtractedCompanyRecord, confidence: "dict[str, int]") -> bool:
        """Only worth re-searching a field the first pass actually found but
        weakly supported - not one it concluded genuinely has no public
        answer (a None value), which a differently-worded query won't change.
        Without this distinction, a company with no public contact person
        (common - see EnrichmentAgent's own docstring) would retry the whole
        search+crawl+LLM pass every time for no possible benefit."""
        return any(
            getattr(record, field_name) is not None
            and confidence.get(field_name, 0) < settings.ENRICHMENT_TAVILY_MIN_CONFIDENCE
            for field_name in _RETRY_TRACKED_FIELDS
        )

    @staticmethod
    def _merge_passes(
        record: ExtractedCompanyRecord,
        confidence: "dict[str, int]",
        retry_record: ExtractedCompanyRecord,
        retry_confidence: "dict[str, int]",
    ) -> "tuple[ExtractedCompanyRecord, dict[str, int]]":
        """Keeps whichever pass scored higher, per field - never just
        overwrites the first pass wholesale with the retry pass."""

        merged = record.model_copy(deep=True)
        merged_confidence = dict(confidence)

        for field_name in record.model_fields:
            if field_name in _NON_MERGE_MODEL_FIELDS:
                continue
            first_score = confidence.get(field_name, 0)
            second_score = retry_confidence.get(field_name, 0)
            if second_score > first_score:
                setattr(merged, field_name, getattr(retry_record, field_name))
                merged_confidence[field_name] = second_score
                if field_name in retry_record.evidence:
                    merged.evidence[field_name] = retry_record.evidence[field_name]
                if field_name in retry_record.source_url:
                    merged.source_url[field_name] = retry_record.source_url[field_name]

        return merged, merged_confidence

    def _run_pass(
        self, company_name: str, website: Optional[str], queries: "list[str]"
    ) -> "tuple[ExtractedCompanyRecord, dict[str, int], list[CrawledPage], list[CrawledPdf]]":
        search_started = time.monotonic()
        results = self._search_all(queries) if self._search.is_configured else []
        logger.info("[PERF] company search: %d ms", round((time.monotonic() - search_started) * 1000))
        ranked = rank_and_filter(results, company_name) if results else []

        has_known_website = bool(website and website.startswith("http"))
        if not ranked and not has_known_website:
            # No search hits and no known site to fall back to - there is
            # nothing to crawl, so there is nothing to hand the LLM.
            return ExtractedCompanyRecord(), {}, [], []

        crawl_started = time.monotonic()
        pages, pdfs = self._crawl(ranked, website)
        logger.info("[PERF] website crawl: %d ms", round((time.monotonic() - crawl_started) * 1000))
        document = document_builder.build_document(pages, pdfs)
        if not document:
            return ExtractedCompanyRecord(), {}, pages, pdfs

        extraction_started = time.monotonic()
        record = llm_extractor.extract(document, company_name)
        logger.info("[PERF] LLM extraction: %d ms", round((time.monotonic() - extraction_started) * 1000))
        return record, dict(record.confidence), pages, pdfs

    def _search_all(self, queries: "list[str]") -> "list[SearchResult]":
        results: "list[SearchResult]" = []
        # Search calls are independent HTTPS requests.  Bound the small pool
        # so query latency overlaps without creating a burst of API traffic.
        with ThreadPoolExecutor(max_workers=min(settings.TAVILY_SEARCH_CONCURRENCY, len(queries))) as executor:
            futures = [(query, executor.submit(self._search.search, query)) for query in queries]
            for query, future in futures:
                try:
                    results.extend(future.result())
                except Exception as ex:
                    logger.warning("Tavily query '%s' failed: %s", query, ex)
        return results

    def _crawl(
        self, ranked: "list[SearchResult]", website: Optional[str]
    ) -> "tuple[list[CrawledPage], list[CrawledPdf]]":
        pages: "list[CrawledPage]" = []
        pdfs: "list[CrawledPdf]" = []
        visited_hosts: "set[str]" = set()
        seen_pdf_urls: "set[str]" = set()

        # The single best-ranked non-PDF result seeds a deeper same-site
        # crawl (home + team/leadership/investor/etc, see html_crawler.crawl);
        # every other ranked URL is fetched as one standalone page/PDF -
        # visiting a directory listing or a government page doesn't warrant
        # crawling that entire site. `website` (the company's already-known
        # site) always wins as the seed when present, Tavily or not.
        primary_url = website if website and website.startswith("http") else None
        if not primary_url:
            primary_url = next((r.url for r in ranked if not r.url.lower().endswith(".pdf")), None)

        discovery_started = time.monotonic()
        if primary_url:
            primary_pages = crawl(self._browser, primary_url, settings.TAVILY_MAX_PAGES)
            pages.extend(primary_pages)
            visited_hosts.add(_host(primary_url))

            # GST certificates/annual reports/brochures linked from the
            # company's own pages matter just as much as ones Tavily happens
            # to surface directly - and are the only PDF source at all when
            # Tavily isn't available for this company.
            for page in primary_pages:
                for pdf_url, label in page.pdf_links:
                    if len(pdfs) >= settings.TAVILY_MAX_PDFS or pdf_url in seen_pdf_urls:
                        continue
                    seen_pdf_urls.add(pdf_url)
                    pdf = self._safe_fetch_pdf(pdf_url, label)
                    if pdf:
                        pdfs.append(pdf)
        logger.info("[PERF] website discovery: %d ms", round((time.monotonic() - discovery_started) * 1000))

        page_pdf_budget = settings.TAVILY_MAX_PAGES + settings.TAVILY_MAX_PDFS
        for result in ranked:
            if len(pages) + len(pdfs) >= page_pdf_budget:
                break
            if result.url.lower().endswith(".pdf"):
                if len(pdfs) >= settings.TAVILY_MAX_PDFS or result.url in seen_pdf_urls:
                    continue
                seen_pdf_urls.add(result.url)
                pdf = self._safe_fetch_pdf(result.url, result.title)
                if pdf:
                    pdfs.append(pdf)
                continue
            if _host(result.url) in visited_hosts:
                continue
            # Tavily's returned content is already the fetched page evidence.
            # Reuse it directly; only the verified primary site gets a
            # Playwright crawl unless the snippet is genuinely absent.
            tavily_text = result.raw_content or result.content
            if tavily_text:
                pages.append(CrawledPage(url=result.url, title=result.title, text=tavily_text))
                visited_hosts.add(_host(result.url))
                continue
            page = fetch_single_page(self._browser, result.url)
            if page:
                pages.append(page)
                visited_hosts.add(_host(result.url))

        return pages, pdfs

    def _safe_fetch_pdf(self, url: str, label: str) -> Optional[CrawledPdf]:
        """fetch_pdf() already catches its own Firecrawl/network/pypdf
        failures and returns None - this extra guard exists so that even an
        unexpected error at this call site (e.g. a bad argument) can never
        discard the rest of this company's already-collected pages/PDFs by
        propagating out of _crawl() into gather()'s broad try/except. One
        PDF failing is just one missing source, never a reason to fail the
        whole enrichment pass."""

        try:
            return fetch_pdf(url, label, firecrawl=self._firecrawl)
        except Exception as ex:
            logger.warning("PDF enrichment skipped for %s: %s", url, ex)
            return None

    @staticmethod
    def _to_result(
        record: ExtractedCompanyRecord,
        pages: "Optional[list[CrawledPage]]" = None,
        pdfs: "Optional[list[CrawledPdf]]" = None,
    ) -> TavilyEnrichmentResult:
        result = TavilyEnrichmentResult(
            field_confidence=dict(record.confidence),
            field_evidence=dict(record.evidence),
            field_sources=dict(record.source_url),
            crawled_pages=pages or [],
            crawled_pdfs=pdfs or [],
        )

        # A rejected contact_person (e.g. the LLM returned a bare title like
        # "Managing Director" with no name attached) must not also discard a
        # legitimately-extracted designation - the two are validated and
        # applied independently.
        contact = contact_validator.validate(record.contact_person)
        designation = designation_validator.validate(record.designation) if record.designation else None
        if contact:
            result.contact_person = contact
            result.contact_source_url = record.source_url.get("contact_person", "tavily")
        result.designation = designation

        for field_name in _MERGE_FIELDS:
            setattr(result, field_name, getattr(record, field_name))

        return result


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")