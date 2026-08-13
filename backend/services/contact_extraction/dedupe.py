"""
Duplicate resolution (Step 8) and same-person cross-source merging (Step 7):
folds name variants like "R Kumar" / "Ravi Kumar" / "R. Kumar" / "Ravi K."
into one candidate, unioning their evidence/sources instead of picking
whichever the crawl happened to see first.
"""

import re

from services.contact_extraction.models import ContactCandidate

_PUNCTUATION_RE = re.compile(r"[.,]")


def _name_tokens(name: str) -> "list[str]":
    return [_PUNCTUATION_RE.sub("", token).lower() for token in name.split() if token]


def _tokens_compatible(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) == 1:
        return b.startswith(a)
    if len(b) == 1:
        return a.startswith(b)
    return False


def _same_person(name_a: str, name_b: str) -> bool:
    """True if every token position is compatible (equal, or one is a
    single-initial prefix of the other) and at least one position is a
    genuine multi-letter match - so two names that are *both* bare initials
    at every position ("R K" vs "R M") never merge on initials alone."""

    tokens_a, tokens_b = _name_tokens(name_a), _name_tokens(name_b)
    if not tokens_a or len(tokens_a) != len(tokens_b):
        return False

    has_anchor = False
    for token_a, token_b in zip(tokens_a, tokens_b):
        if not _tokens_compatible(token_a, token_b):
            return False
        if len(token_a) >= 3 and len(token_b) >= 3:
            has_anchor = True

    return has_anchor


def merge(candidates: "list[ContactCandidate]") -> "list[ContactCandidate]":
    """Groups near-duplicate candidates and folds each group into one."""

    groups: "list[list[ContactCandidate]]" = []
    for candidate in candidates:
        group = next(
            (g for g in groups if any(_same_person(candidate.name, existing.name) for existing in g)),
            None,
        )
        if group is not None:
            group.append(candidate)
        else:
            groups.append([candidate])

    return [_merge_group(group) for group in groups]


def _merge_group(group: "list[ContactCandidate]") -> ContactCandidate:
    if len(group) == 1:
        return group[0]

    # The longest printed name is almost always the fullest one
    # ("Ravi Kumar" over "R Kumar"), so it survives as the display name.
    fullest = max(group, key=lambda candidate: len(candidate.name))

    matched_sources: set = set()
    source_urls: set = set()
    evidence: list = []
    canonical_designation = None
    email = phone = linkedin_url = None
    for candidate in group:
        matched_sources |= candidate.matched_sources
        source_urls |= candidate.source_urls
        evidence.extend(candidate.evidence)
        canonical_designation = canonical_designation or candidate.canonical_designation
        email = email or candidate.email
        phone = phone or candidate.phone
        linkedin_url = linkedin_url or candidate.linkedin_url

    return ContactCandidate(
        name=fullest.name,
        raw_title=fullest.raw_title,
        canonical_designation=canonical_designation,
        page_category=max(candidate.page_category for candidate in group),
        source_url=fullest.source_url,
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        source=fullest.source,
        evidence=evidence,
        matched_sources=matched_sources,
        source_urls=source_urls,
    )
