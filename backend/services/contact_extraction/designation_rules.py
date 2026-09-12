"""
Designation normalization (Step 5): reconciles synonyms/abbreviations that
`config/targeting.py`'s exact-match `extract_target_designation` treats as
unrelated strings (e.g. "MD" never matched "Managing Director").

This wraps `config/targeting.py`'s taxonomy rather than editing it, so the
existing `match_target_designation`/`extract_target_designation` functions -
and anything else that imports that module - keep working exactly as before.
"""

import re

from config.targeting import TARGET_DESIGNATIONS

# Maps a normalized synonym/abbreviation to the canonical designation string
# that should end up in `Company.designation`. Canonical values are either
# an existing `TARGET_DESIGNATIONS` title (so they stay consistent with the
# rest of the taxonomy) or, where the taxonomy has no entry yet, a sensible
# human-readable label.
DESIGNATION_ALIASES = {
    "md": "Managing Director",
    "managing director": "Managing Director",
    "founder & managing director": "Managing Director",
    "founder and managing director": "Managing Director",
    "managing partner": "Managing Partner",
    "executive director": "Executive Director",
    "whole time director": "Whole-time Director",
    "whole-time director": "Whole-time Director",
    "director": "Director",
    "chief executive officer": "CEO",
    "ceo": "CEO",
    "founder & ceo": "CEO",
    "founder and ceo": "CEO",
    "founder": "Founder",
    "co-founder": "Founder",
    "cofounder": "Founder",
    "vice president": "Vice President",
    "vp": "Vice President",
    "general manager": "General Manager",
    "gm": "General Manager",
    "purchase head": "Procurement Head",
    "procurement head": "Procurement Head",
    "purchase manager": "Purchase Manager",
    "head supply chain": "Supply Chain",
    "supply chain head": "Supply Chain",
    "commercial manager": "Commercial Head",
    "materials manager": "Stores Manager",
    "hr head": "HR Head",
    "people operations": "HR Head",
    "head people operations": "HR Head",
    "talent acquisition": "Talent Acquisition",
    "chro": "CHRO",
    # Additional titles from the Tavily/LLM extraction pipeline's expanded
    # designation coverage (services/enrichment/company_enrichment.py) -
    # CTO/COO/CFO/Business Head already resolve via the TARGET_DESIGNATIONS
    # taxonomy above and don't need an alias here.
    "owner": "Owner",
    "proprietor": "Proprietor",
    "partner": "Partner",
    "chairman": "Chairman",
    "vice chairman": "Vice Chairman",
    "managing trustee": "Managing Trustee",
    "promoter": "Promoter",
    "country head": "Country Head",
    "regional head": "Regional Head",
    "operations director": "Operations Director",
    "technical director": "Technical Director",
}

_STRIP_LABEL_RE = re.compile(r"^(designation|title|role)\s*[:\-]\s*")

# Taxonomy titles first (identity mapping), then aliases layered on top so
# an overlapping key (e.g. "md", already a literal taxonomy title) resolves
# to our preferred canonical form ("Managing Director") instead of the bare
# abbreviation. Longest-first so "Managing Director" is tried before the
# bare "Director" it also contains during the substring fallback - otherwise
# free text like "Founder, Managing Director, ABC Pvt Ltd" would resolve to
# the less specific title.
_CANONICAL_BY_TERM = {title.lower(): title for group in TARGET_DESIGNATIONS for title in group["titles"]}
_CANONICAL_BY_TERM.update(DESIGNATION_ALIASES)
_TERMS_BY_LENGTH_DESC = sorted(_CANONICAL_BY_TERM.items(), key=lambda item: len(item[0]), reverse=True)


def _normalize(raw_title: str) -> str:
    normalized = re.sub(r"\s+", " ", (raw_title or "").strip().lower())
    return _STRIP_LABEL_RE.sub("", normalized)


# A real job title, even a compound one ("Founder & Managing Director,
# ABC Pvt Ltd"), reads as a short phrase, never a full sentence. Without
# this cap, marketing prose that happens to contain a taxonomy phrase as a
# substring - e.g. "Our supply chain efficiency ... make our deliveries
# reliable" contains "supply chain" - would wrongly canonicalize to that
# title. The exact/alias dict lookup above is unaffected: it already
# requires the *entire* normalized string to equal a short entry.
_MAX_FALLBACK_WORDS = 8


def canonical_designation(raw_title: str) -> "str | None":
    """Returns the canonical designation `raw_title` refers to, or None.

    Tries an exact alias/taxonomy hit first (cheap, unambiguous), then a
    word-boundary substring search for short free text like "Founder &
    Managing Director, XYZ Pvt Ltd" that won't equal any single entry
    outright - but not for anything sentence-length (see _MAX_FALLBACK_WORDS).
    """

    normalized = _normalize(raw_title)
    if not normalized or len(normalized) > 120:
        return None

    if normalized in _CANONICAL_BY_TERM:
        return _CANONICAL_BY_TERM[normalized]

    if len(normalized.split()) > _MAX_FALLBACK_WORDS:
        return None

    for term, canonical in _TERMS_BY_LENGTH_DESC:
        if re.search(r"\b" + re.escape(term) + r"\b", normalized):
            return canonical

    return None


def strip_known_designation_prefix(raw_title: str) -> "tuple[str, str | None]":
    """If `raw_title` starts with a known designation phrase followed by
    more words, returns (remaining_text, canonical_designation) with that
    leading phrase removed; otherwise (raw_title, None).

    Covers a title concatenated directly against a name with no separator
    at all ("Managing Director John Smith"), which name_rules.is_person_name
    would otherwise wrongly accept as a single 4-word name (neither
    "Managing" nor "Director" individually looks like company/nav text, and
    the whole string never equals a known designation exactly, so the
    existing whole-string check in is_person_name doesn't catch it). Only a
    *prefix* match counts - a name that merely contains a title-like word
    elsewhere isn't split.
    """
    words = (raw_title or "").strip().split()
    if len(words) < 2:
        return raw_title, None
    lowered_words = [word.lower() for word in words]
    for term, canonical in _TERMS_BY_LENGTH_DESC:
        term_words = term.split()
        count = len(term_words)
        if not count or count >= len(words):
            continue
        if lowered_words[:count] == term_words:
            return " ".join(words[count:]), canonical
    return raw_title, None


def is_exact_match(raw_title: str) -> bool:
    """True when `raw_title` itself is a known title/alias, rather than a
    longer piece of free text that merely *contains* one as a substring.

    dom_extractor.py uses this to require corroborating evidence (an email/
    phone/LinkedIn link in the same container) before trusting a generic,
    non-person-specific container on the weaker substring-match path - an
    exact hit like "CEO" or "Managing Director" needs no such backup.
    """

    return _normalize(raw_title) in _CANONICAL_BY_TERM
