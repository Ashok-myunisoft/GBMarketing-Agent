"""
Ranks and filters Tavily results per the SOURCE PRIORITY rules: a company's
own website, annual reports, financial statements, investor pages, GST
certificates, and government sites outrank trusted business directories,
which outrank social media - and anything that doesn't look related to the
company at all is dropped rather than fed into the crawl/LLM step.
"""

import re
from urllib.parse import urlparse

from services.tavily.base import SearchResult

_HIGH_PRIORITY_HINTS = (
    "annual report", "annual-report", "investor", "financial statement",
    "financial-statement", "gst certificate", "gst-certificate",
    ".gov.in", ".nic.in", "mca.gov.in", "gst.gov.in",
)
_MEDIUM_PRIORITY_DOMAINS = (
    "indiamart.com", "tradeindia.com", "tradeindia.co.in",
    "exportersindia.com", "justdial.com", "sulekha.com",
)
_LOW_PRIORITY_DOMAINS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com",
)
_IRRELEVANT_DOMAINS = (
    "youtube.com", "wikipedia.org", "quora.com", "reddit.com",
    "naukri.com", "indeed.com", "glassdoor.com", "pinterest.com",
)
_STOPWORDS = {"private", "limited", "ltd", "pvt", "llp", "inc", "co", "the", "and"}


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _company_tokens(company_name: str) -> "set[str]":
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9]+", company_name or "")
        if len(token) > 2 and token.lower() not in _STOPWORDS
    }


def _tier(url: str, company_tokens: "set[str]") -> int:
    """Lower is more trusted. Mirrors the prompt's High/Medium/Low priority tiers."""

    domain = _domain(url)
    haystack = f"{domain} {url.lower()}"

    if any(hint in haystack for hint in _HIGH_PRIORITY_HINTS):
        return 0
    if any(domain.endswith(medium) for medium in _MEDIUM_PRIORITY_DOMAINS):
        return 2
    if any(domain.endswith(low) for low in _LOW_PRIORITY_DOMAINS):
        return 4

    # A domain whose root incorporates a company-name token, and isn't a known
    # directory/social site, is very likely the company's own official
    # website - the single highest-priority source - even though nothing
    # about the domain itself says "official".
    domain_root = domain.split(".")[0]
    if company_tokens and any(token in domain_root for token in company_tokens):
        return 0

    return 3  # unrecognized domain: below directories, above social


def is_relevant(result: SearchResult, company_tokens: "set[str]") -> bool:
    domain = _domain(result.url)
    if any(domain.endswith(irrelevant) for irrelevant in _IRRELEVANT_DOMAINS):
        return False
    if not company_tokens:
        return True

    haystack = f"{result.title} {result.content}".lower()
    return any(token in haystack for token in company_tokens) or any(
        token in domain for token in company_tokens
    )


def rank_and_filter(results: "list[SearchResult]", company_name: str) -> "list[SearchResult]":
    """Dedupes by normalized URL, drops irrelevant results, then sorts by
    trust tier (ties broken by Tavily's own relevance score)."""

    tokens = _company_tokens(company_name)

    seen: "set[str]" = set()
    deduped: "list[SearchResult]" = []
    for result in results:
        key = result.url.rstrip("/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(result)

    filtered = [result for result in deduped if is_relevant(result, tokens)]
    return sorted(filtered, key=lambda result: (_tier(result.url, tokens), -result.score))
