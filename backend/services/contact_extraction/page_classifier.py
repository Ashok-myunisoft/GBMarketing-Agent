"""
Ranks a page by how likely it is to name a real decision-maker (Step 1).

This is deliberately separate from `enrichment_agent.CONTACT_PAGE_LINK_TEXT`,
which stays a flat keyword list driving the *shared* supplemental-page crawl
used by email/GST/address extraction. This module only labels the pages that
crawl already visits for contact-scoring purposes - it never decides which
URLs get visited.
"""

import re

from services.contact_extraction.models import PageCategory

_LEADERSHIP_TERMS = ("leadership", "executive-team", "executive team", "management-team", "our-leadership")
_BOARD_TERMS = ("board", "board-of-directors", "board of directors", "directors")
_ABOUT_TERMS = ("about", "who-we-are", "who we are", "company", "corporate-profile")
_TEAM_TERMS = ("team", "our-team", "our-people", "people", "management")
_CONTACT_TERMS = ("contact", "contact-us", "get-in-touch", "reach-us")

# Checked in this order so the most decision-maker-dense category wins when
# a URL/label matches more than one tier (e.g. "/about/leadership").
_TIERS = (
    (PageCategory.LEADERSHIP, _LEADERSHIP_TERMS),
    (PageCategory.BOARD, _BOARD_TERMS),
    (PageCategory.ABOUT, _ABOUT_TERMS),
    (PageCategory.TEAM, _TEAM_TERMS),
    (PageCategory.CONTACT, _CONTACT_TERMS),
)


def classify(url: str, link_label: str = "") -> PageCategory:
    """Returns the highest-ranked PageCategory matching the URL path or link text."""

    haystack = f"{(url or '').lower()} {(link_label or '').lower()}"
    haystack = re.sub(r"[_/]", "-", haystack)

    for category, terms in _TIERS:
        if any(term in haystack for term in terms):
            return category

    return PageCategory.OTHER
