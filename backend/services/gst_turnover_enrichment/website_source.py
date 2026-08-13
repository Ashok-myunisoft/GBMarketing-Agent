import logging
from urllib.parse import urlparse

from core.config import settings

from services.gst_turnover_enrichment.firecrawl_client import (
    FirecrawlClient,
    extract_links,
)

from services.gst_turnover_enrichment.gst_extraction import (
    find_valid_gstins,
)

from services.gst_turnover_enrichment.models import (
    SourceCandidate,
)

from services.gst_turnover_enrichment.pdf_utils import (
    fetch_pdf_text,
    is_annual_report,
    same_site_pdf_links,
)

from services.gst_turnover_enrichment.turnover_extraction import (
    find_turnover_candidates,
)


logger = logging.getLogger(__name__)


# ============================================================
# FIRECRAWL PAGE LIMITS
# ============================================================
#
# Previous:
#
#     MAX_PAGES = 8
#     MAX_PDFS_PER_PAGE = 6
#
# That can generate many Firecrawl requests per company.
#
# New conservative defaults:
#
#     MAX_PAGES = 3
#     MAX_PDFS_PER_PAGE = 2
#
# These can still be configured through environment variables.
# ============================================================

MAX_PAGES = max(
    1,
    int(
        getattr(
            settings,
            "FIRECRAWL_MAX_PAGES",
            3,
        )
    ),
)

MAX_PDFS_PER_PAGE = max(
    0,
    int(
        getattr(
            settings,
            "FIRECRAWL_MAX_PDFS_PER_PAGE",
            2,
        )
    ),
)


# ============================================================
# LINK HINTS
# ============================================================

LINK_TEXT_HINTS = (
    "about",
    "about-us",
    "contact",
    "company",
    "legal",
    "privacy",
    "terms",
    "gst",
    "gstin",
    "gst-number",
    "registration",
    "compliance",
    "certificate",
    "certification",
    "tax",
    "invoice",
    "downloads",
    "download",
    "brochure",
    "profile",
    "investor",
    "annual report",
    "annual-report",
    "financial",
    "presentation",
)


_PRIORITY_HINTS = (
    "gst",
    "gstin",
    "gst-number",
    "legal",
    "annual",
    "investor",
    "registration",
)


# ============================================================
# CANDIDATE URL DISCOVERY
# ============================================================

def _candidate_urls(
    html: str,
    base_url: str,
    limit: int = MAX_PAGES,
) -> "list[str]":
    """
    Returns a small ranked list of same-site URLs.

    Priority is given to pages likely to contain:

        GST
        registration
        legal information
        financial information
        annual reports
    """

    base_host = (
        urlparse(base_url)
        .netloc
        .lower()
        .removeprefix("www.")
    )

    ranked: list[
        tuple[int, str]
    ] = []

    seen: set[str] = set()

    for absolute, label in extract_links(
        html,
        base_url,
    ):

        parsed = urlparse(
            absolute
        )

        if parsed.scheme not in {
            "http",
            "https",
        }:
            continue

        current_host = (
            parsed.netloc
            .lower()
            .removeprefix("www.")
        )

        if current_host != base_host:
            continue

        normalized_url = (
            absolute
            .strip()
            .rstrip("/")
        )

        if normalized_url in seen:
            continue

        seen.add(
            normalized_url
        )

        haystack = (
            f"{label.lower()} "
            f"{parsed.path.lower()} "
            f"{parsed.query.lower()}"
        )

        if not any(
            term in haystack
            for term in LINK_TEXT_HINTS
        ):
            continue

        if any(
            term in haystack
            for term in _PRIORITY_HINTS
        ):
            priority = 0
        else:
            priority = 1

        ranked.append(
            (
                priority,
                normalized_url,
            )
        )

    ranked.sort(
        key=lambda item: item[0]
    )

    return [
        url
        for _, url in ranked[:limit]
    ]


# ============================================================
# PAGE EXTRACTION
# ============================================================

def _extract_from_page(
    page,
) -> "tuple[list[SourceCandidate], list[SourceCandidate]]":
    """
    Extract GST and turnover candidates from one Firecrawl page.

    Both fields use the SAME Firecrawl response.
    """

    gst_candidates = [
        SourceCandidate(
            value=gst,
            source="website",
            source_url=page.url,
        )
        for gst in find_valid_gstins(
            page.html or ""
        )
    ]

    turnover_candidates = [
        SourceCandidate(
            value=candidate["value"],
            source="website",
            source_url=page.url,
            currency=candidate[
                "currency"
            ],
            financial_year=candidate[
                "financial_year"
            ],
            metric=candidate[
                "metric"
            ],
        )
        for candidate in find_turnover_candidates(
            page.markdown
            or page.html
            or ""
        )
    ]

    return (
        gst_candidates,
        turnover_candidates,
    )


# ============================================================
# PDF SCAN
# ============================================================

def _scan_pdfs(
    firecrawl: FirecrawlClient,
    html: str,
    base_url: str,
) -> "tuple[list[SourceCandidate], list[SourceCandidate]]":
    """
    Scan only a small number of same-site PDFs.

    PDFs are only called when the caller still needs additional
    evidence.
    """

    gst_candidates: list[
        SourceCandidate
    ] = []

    turnover_candidates: list[
        SourceCandidate
    ] = []

    if (
        not html
        or MAX_PDFS_PER_PAGE <= 0
    ):
        return (
            gst_candidates,
            turnover_candidates,
        )

    pdf_links = same_site_pdf_links(
        html,
        base_url,
        limit=MAX_PDFS_PER_PAGE,
    )

    for pdf_url, label in pdf_links:

        text = fetch_pdf_text(
            firecrawl,
            pdf_url,
        )

        if not text:
            continue

        # ----------------------------------------------------
        # GST
        # ----------------------------------------------------

        for gst in find_valid_gstins(
            text
        ):

            gst_candidates.append(
                SourceCandidate(
                    value=gst,
                    source="pdf",
                    source_url=pdf_url,
                )
            )

        # ----------------------------------------------------
        # TURNOVER
        # ----------------------------------------------------

        pdf_source = (
            "annual_report"
            if is_annual_report(
                label
            )
            else "pdf"
        )

        for candidate in find_turnover_candidates(
            text
        ):

            turnover_candidates.append(
                SourceCandidate(
                    value=candidate[
                        "value"
                    ],
                    source=pdf_source,
                    source_url=pdf_url,
                    currency=candidate[
                        "currency"
                    ],
                    financial_year=candidate[
                        "financial_year"
                    ],
                    metric=candidate[
                        "metric"
                    ],
                )
            )

    return (
        gst_candidates,
        turnover_candidates,
    )


# ============================================================
# EXISTING COMPATIBILITY FUNCTION
# ============================================================

def collect_from_pages(
    pages,
    pdfs,
) -> "tuple[list[SourceCandidate], list[SourceCandidate]]":
    """
    Extract GST/turnover from already fetched pages/PDFs.

    Kept for compatibility with existing callers/tests.
    """

    gst_candidates: list[
        SourceCandidate
    ] = []

    turnover_candidates: list[
        SourceCandidate
    ] = []

    # --------------------------------------------------------
    # WEBSITE PAGES
    # --------------------------------------------------------

    for page in pages:

        haystack = (
            getattr(page, "html", "")
            or getattr(page, "text", "")
            or ""
        )

        for gst in find_valid_gstins(
            haystack
        ):

            gst_candidates.append(
                SourceCandidate(
                    value=gst,
                    source="website",
                    source_url=page.url,
                )
            )

        page_text = (
            getattr(page, "text", "")
            or haystack
        )

        for candidate in find_turnover_candidates(
            page_text
        ):

            turnover_candidates.append(
                SourceCandidate(
                    value=candidate[
                        "value"
                    ],
                    source="website",
                    source_url=page.url,
                    currency=candidate[
                        "currency"
                    ],
                    financial_year=candidate[
                        "financial_year"
                    ],
                    metric=candidate[
                        "metric"
                    ],
                )
            )

        if (
            gst_candidates
            and turnover_candidates
        ):
            return (
                gst_candidates,
                turnover_candidates,
            )

    # --------------------------------------------------------
    # PDFS
    # --------------------------------------------------------

    for pdf in pdfs:

        text = getattr(
            pdf,
            "text",
            "",
        )

        if not text:
            continue

        for gst in find_valid_gstins(
            text
        ):

            gst_candidates.append(
                SourceCandidate(
                    value=gst,
                    source="pdf",
                    source_url=pdf.url,
                )
            )

        pdf_source = (
            "annual_report"
            if getattr(
                pdf,
                "category",
                "",
            )
            == "annual_report"
            else "pdf"
        )

        for candidate in find_turnover_candidates(
            text
        ):

            turnover_candidates.append(
                SourceCandidate(
                    value=candidate[
                        "value"
                    ],
                    source=pdf_source,
                    source_url=pdf.url,
                    currency=candidate[
                        "currency"
                    ],
                    financial_year=candidate[
                        "financial_year"
                    ],
                    metric=candidate[
                        "metric"
                    ],
                )
            )

    return (
        gst_candidates,
        turnover_candidates,
    )


# ============================================================
# MAIN COLLECTION PIPELINE
# ============================================================

def collect(
    firecrawl: FirecrawlClient,
    website: str,
    company_name: str = "",
) -> "tuple[list[SourceCandidate], list[SourceCandidate]]":
    """
    Collect GST and turnover candidates from a company's own website.

    Efficient flow:

        Homepage
            ↓
        Extract GST/turnover
            ↓
        If both found -> STOP
            ↓
        Relevant pages
            ↓
        Extract GST/turnover
            ↓
        If both found -> STOP
            ↓
        Small PDF scan
            ↓
        STOP

    Every Firecrawl call passes through the global rate limiter
    in FirecrawlClient.
    """

    gst_candidates: list[
        SourceCandidate
    ] = []

    turnover_candidates: list[
        SourceCandidate
    ] = []

    if not website:
        return (
            gst_candidates,
            turnover_candidates,
        )

    try:

        # ====================================================
        # STEP 1: HOMEPAGE
        # ====================================================

        logger.info(
            "[FIRECRAWL] company=%s url=%s",
            company_name,
            website,
        )

        started = __import__(
            "time"
        ).monotonic()

        homepage = firecrawl.scrape(
            website
        )

        logger.info(
            "[PERF] Firecrawl homepage: %d ms",
            round(
                (
                    __import__(
                        "time"
                    ).monotonic()
                    - started
                )
                * 1000
            ),
        )

        if not homepage.success:

            if homepage.error_code == "ANTI_BOT":

                logger.info(
                    "[FIRECRAWL] anti_bot company=%s url=%s",
                    company_name,
                    website,
                )

            else:

                logger.warning(
                    "[FIRECRAWL] "
                    "homepage failed "
                    "url=%s "
                    "reason=%s",
                    website,
                    homepage.error,
                )

            return (
                gst_candidates,
                turnover_candidates,
            )

        logger.info(
            "[FIRECRAWL] scrape_success company=%s url=%s",
            company_name,
            website,
        )

        # ====================================================
        # STEP 2: EXTRACT FROM HOMEPAGE
        # ====================================================

        page_gst, page_turnover = (
            _extract_from_page(
                homepage
            )
        )

        gst_candidates.extend(
            page_gst
        )

        turnover_candidates.extend(
            page_turnover
        )

        # ====================================================
        # EARLY STOP
        # ====================================================

        if (
            gst_candidates
            and turnover_candidates
        ):

            logger.info(
                "[FIRECRAWL] "
                "Both GST and turnover found "
                "on homepage; stopping crawl."
            )

            return (
                gst_candidates,
                turnover_candidates,
            )

        # ====================================================
        # STEP 3: RELEVANT SAME-SITE PAGES
        # ====================================================

        remaining_urls = _candidate_urls(
            homepage.html,
            homepage.url,
            limit=MAX_PAGES,
        )

        logger.info(
            "[FIRECRAWL] "
            "candidate_pages=%d "
            "url=%s",
            len(remaining_urls),
            website,
        )

        # IMPORTANT:
        #
        # Do NOT use ThreadPoolExecutor here.
        #
        # Sequential requests allow the global Firecrawl
        # rate limiter to control the API properly.

        for url in remaining_urls:

            # Both already found.
            if (
                gst_candidates
                and turnover_candidates
            ):
                break

            page = firecrawl.scrape(
                url
            )

            if not page.success:

                logger.info(
                    "[FIRECRAWL] "
                    "page skipped "
                    "url=%s "
                    "reason=%s",
                    url,
                    page.error,
                )

                continue

            page_gst, page_turnover = (
                _extract_from_page(
                    page
                )
            )

            gst_candidates.extend(
                page_gst
            )

            turnover_candidates.extend(
                page_turnover
            )

            logger.info(
                "[FIRECRAWL] "
                "page=%s "
                "gst_candidates=%d "
                "turnover_candidates=%d",
                url,
                len(gst_candidates),
                len(turnover_candidates),
            )

            # Stop as soon as both are found.
            if (
                gst_candidates
                and turnover_candidates
            ):

                logger.info(
                    "[FIRECRAWL] "
                    "GST + turnover found; "
                    "stopping page crawl."
                )

                break

        # ====================================================
        # STEP 4: PDF SCAN ONLY IF NEEDED
        # ====================================================
        #
        # PDFs are expensive because each PDF is another
        # Firecrawl request.
        #
        # Therefore we do NOT scan homepage PDFs before
        # checking relevant HTML pages.
        # ====================================================

        if not (
            gst_candidates
            and turnover_candidates
        ):

            pdf_gst, pdf_turnover = (
                _scan_pdfs(
                    firecrawl,
                    homepage.html,
                    homepage.url,
                )
            )

            gst_candidates.extend(
                pdf_gst
            )

            turnover_candidates.extend(
                pdf_turnover
            )

        # ====================================================
        # FINAL RESULT
        # ====================================================

        logger.info(
            "[FIRECRAWL] "
            "completed url=%s "
            "gst_candidates=%d "
            "turnover_candidates=%d",
            website,
            len(gst_candidates),
            len(turnover_candidates),
        )

        return (
            gst_candidates,
            turnover_candidates,
        )

    except Exception as ex:

        logger.warning(
            "Website GST/turnover crawl failed "
            "for %s: %s",
            website,
            ex,
        )

        # Never reject the company because enrichment failed.

        return (
            gst_candidates,
            turnover_candidates,
        )