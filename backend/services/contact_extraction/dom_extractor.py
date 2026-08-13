"""
Candidate generation (Steps 2 and 3): DOM container traversal first, a
whole-page text-block scan only as a fallback for sites with no structured
markup. Every match becomes a `ContactCandidate` - nothing is returned early,
so `dedupe.py`/`scorer.py` always get to weigh every option found on a page.
"""

import re

from services.contact_extraction.designation_rules import canonical_designation, is_exact_match
from services.contact_extraction.models import ContactCandidate, PageCategory
from services.contact_extraction.name_rules import is_person_name, normalize_name

# Class substrings that are themselves strong, person-specific evidence -
# "team-member-card", "boardMemberProfile", etc. A container matching one of
# these is trusted on a heading+designation match alone (STRONG_CLASS_TERMS).
#
# "card"/"li"/"article"/"tr" are far too generic on their own - a "why
# choose us" feature card, a pricing card, or a plain <ul><li> nav item all
# match "card"/"li" just as readily as an actual person's card, and a
# marketing sentence can coincidentally contain a taxonomy phrase as a
# substring (seen live: a "Delivery Reliable" feature card whose body text
# happened to contain "supply chain"). Containers that only match these
# WEAK_CLASS_TERMS need a corroborating signal - see _passes_weak_tier_check.
STRONG_CLASS_TERMS = (
    "team", "member", "profile", "director", "leadership", "management", "executive", "board",
)
CONTAINER_SELECTOR = (
    "[class*='team' i], [class*='member' i], [class*='profile' i], "
    "[class*='director' i], [class*='leadership' i], [class*='management' i], "
    "[class*='executive' i], [class*='board' i], [class*='card' i], "
    "li, article, tr"
)
HEADING_SELECTOR = "h1, h2, h3, h4, h5, h6, strong, b"
MAX_CONTAINERS = 60
# A container whose own text runs this long is almost certainly a page
# section wrapper (or the whole page, for an over-broad selector match),
# not one person's card.
MAX_CONTAINER_TEXT_LENGTH = 400

_LINE_SPLIT_RE = re.compile(r"\s*[-,|]\s*")
_PARENTHETICAL_RE = re.compile(r"\s*([A-Za-z][A-Za-z .'\-]{2,80}?)\s*\(([^()]{2,80})\)\s*")


def extract_candidates(page, source_url: str, page_category: PageCategory) -> "list[ContactCandidate]":
    """Returns every plausible (name, title) candidate found on this page."""

    candidates = _extract_from_containers(page, source_url, page_category)
    if candidates:
        return candidates
    return _extract_from_text_blocks(page, source_url, page_category)


def _extract_from_containers(page, source_url, page_category) -> "list[ContactCandidate]":
    candidates: list[ContactCandidate] = []
    seen: set = set()

    try:
        containers = page.locator(CONTAINER_SELECTOR)
        count = min(containers.count(), MAX_CONTAINERS)
    except Exception:
        return candidates

    for index in range(count):
        container = containers.nth(index)
        try:
            text = (container.inner_text(timeout=500) or "").strip()
        except Exception:
            continue
        if not text or len(text) > MAX_CONTAINER_TEXT_LENGTH:
            continue

        name, title = _name_and_title_from_container(container, text)
        if not name or not title:
            continue

        designation = canonical_designation(title)
        if not designation or not is_person_name(name):
            continue

        key = (name.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)

        candidate = _build_candidate(container, source_url, page_category, name, title, designation)
        if not _is_strong_container(container) and not is_exact_match(title):
            # A generic <li>/<article>/card-classed container with no
            # person-specific class name, matched only via the substring
            # fallback (not an exact "CEO"/"Managing Director" hit) needs
            # some other corroborating signal before it's trusted - a real
            # person's card almost always surfaces at least one of these.
            if not (candidate.email or candidate.phone or candidate.linkedin_url):
                continue

        candidates.append(candidate)

    return candidates


def _is_strong_container(container) -> bool:
    try:
        class_attr = (container.get_attribute("class") or "").lower()
    except Exception:
        return False
    return any(term in class_attr for term in STRONG_CLASS_TERMS)


def _name_and_title_from_container(container, text: str) -> "tuple[str | None, str | None]":
    """Prefers a heading-as-name / rest-of-card-as-title read of this card."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    try:
        heading = container.locator(HEADING_SELECTOR).first
        heading_text = (heading.inner_text(timeout=500) or "").strip() if heading.count() > 0 else ""
    except Exception:
        heading_text = ""

    if heading_text and is_person_name(heading_text):
        remainder = [line for line in lines if line != heading_text]
        if remainder:
            return heading_text, remainder[0]

    for name, title in _line_pairs(lines):
        return name, title

    return None, None


def _line_pairs(lines: "list[str]") -> "list[tuple[str, str]]":
    """(name, title) pairs from adjacent lines or a single "Name - Title" line."""

    pairs: "list[tuple[str, str]]" = []

    for index in range(len(lines) - 1):
        first, second = lines[index], lines[index + 1]
        if is_person_name(first) and canonical_designation(second):
            pairs.append((first, second))
        elif is_person_name(second) and canonical_designation(first):
            pairs.append((second, first))

    for line in lines:
        pairs.extend(_same_line_pairs(line))

    return pairs


def _same_line_pairs(line: str) -> "list[tuple[str, str]]":
    """Covers "Name (Title)", "Name - Title", "Name, Title", "Name | Title", either order."""

    results: "list[tuple[str, str]]" = []

    parenthetical = _PARENTHETICAL_RE.fullmatch(line)
    if parenthetical:
        results.append(parenthetical.groups())

    parts = _LINE_SPLIT_RE.split(line, maxsplit=1)
    if len(parts) == 2 and all(2 <= len(part) <= 80 for part in parts):
        results.append((parts[0], parts[1]))
        results.append((parts[1], parts[0]))

    return [
        (name, title) for name, title in results
        if is_person_name(name) and canonical_designation(title)
    ]


def _build_candidate(container, source_url, page_category, name, title, designation) -> ContactCandidate:
    email = _mailto(container)
    phone = _tel(container)
    linkedin = _linkedin(container)

    return ContactCandidate(
        name=normalize_name(name),
        raw_title=title.strip(),
        canonical_designation=designation,
        page_category=page_category,
        source_url=source_url,
        email=email,
        phone=phone,
        linkedin_url=linkedin,
        source="website",
        evidence=[f'Found on {source_url} ({page_category.name.title()} page) as "{title.strip()}"'],
    )


def _mailto(container) -> "str | None":
    href = _first_href(container, 'a[href^="mailto:"]')
    if not href:
        return None
    email = href.replace("mailto:", "").split("?")[0].strip()
    return email or None


def _tel(container) -> "str | None":
    href = _first_href(container, 'a[href^="tel:"]')
    if not href:
        return None
    number = href.replace("tel:", "").strip()
    return number or None


def _linkedin(container) -> "str | None":
    try:
        links = container.locator('a[href*="linkedin.com"]')
        for index in range(min(links.count(), 5)):
            href = links.nth(index).get_attribute("href")
            if href and "/in/" in href:
                return href
    except Exception:
        pass
    return None


def _first_href(container, selector: str) -> "str | None":
    try:
        links = container.locator(selector)
        if links.count() == 0:
            return None
        return links.first.get_attribute("href")
    except Exception:
        return None


def _extract_from_text_blocks(page, source_url, page_category) -> "list[ContactCandidate]":
    """Whole-page line scan, only reached when no structured card matched anything."""

    try:
        body_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return []

    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    candidates: "list[ContactCandidate]" = []
    seen: set = set()

    for index, line in enumerate(lines):
        for name, title in _same_line_pairs(line):
            _append_text_candidate(candidates, seen, name, title, source_url, page_category)

        # Pairing a designation-bearing line with a name-shaped neighbour
        # has no container/heading structure to anchor on, so it's only
        # trusted for an *exact* title hit ("Managing Director", "CEO") -
        # the same bar containers hold a corroboration-free match to. A
        # fuzzy substring match (confirmed in production: a "Great supply
        # chain performance" marketing line coincidentally containing
        # "supply chain") is exactly the free-text case that substring
        # matching is too permissive for without a real container to
        # corroborate it, and this fallback has no equivalent of a
        # container's email/phone/LinkedIn signal to fall back on.
        if not is_exact_match(line):
            continue
        neighbors = lines[max(0, index - 1):index] + lines[index + 1:index + 2]
        for neighbor in neighbors:
            if is_person_name(neighbor):
                _append_text_candidate(candidates, seen, neighbor, line, source_url, page_category)

    return candidates


def _append_text_candidate(candidates, seen, name, title, source_url, page_category) -> None:
    designation = canonical_designation(title)
    if not designation or not is_person_name(name):
        return
    key = (name.lower(), title.lower())
    if key in seen:
        return
    seen.add(key)
    candidates.append(
        ContactCandidate(
            name=normalize_name(name),
            raw_title=title.strip(),
            canonical_designation=designation,
            page_category=page_category,
            source_url=source_url,
            source="website",
            evidence=[f'Found on {source_url} ({page_category.name.title()} page) as "{title.strip()}"'],
        )
    )
