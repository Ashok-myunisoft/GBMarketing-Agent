"""
Exercises dom_extractor.py's two strategies (container-first, text-block
fallback) against minimal fake Playwright-like locators, without needing a
real browser. The fake only implements the handful of operations
dom_extractor.py actually calls: locator()/count()/nth()/first/inner_text()/
get_attribute().
"""

import re
import unittest

from services.contact_extraction import dom_extractor
from services.contact_extraction.models import PageCategory


class _FakeElement:
    def __init__(self, tag, class_attr="", text="", attrs=None, children=None):
        self.tag = tag
        self.class_attr = class_attr
        self.text = text
        self.attrs = attrs or {}
        self.children = children or []

    def iter_subtree(self):
        yield self
        for child in self.children:
            yield from child.iter_subtree()


def _matches_clause(element: _FakeElement, clause: str) -> bool:
    clause = clause.strip()
    class_match = re.match(r"^\[class\*='([^']+)' i\]$", clause)
    if class_match:
        return class_match.group(1).lower() in (element.class_attr or "").lower()
    href_match = re.match(r'^a\[href([\^*])="([^"]+)"\]$', clause)
    if href_match:
        if element.tag != "a":
            return False
        op, value = href_match.groups()
        href = element.attrs.get("href", "")
        return href.startswith(value) if op == "^" else value in href
    return element.tag == clause


def _matches_any(element: _FakeElement, selector: str) -> bool:
    return any(_matches_clause(element, clause) for clause in selector.split(","))


class _FakeLocator:
    def __init__(self, elements):
        self.elements = elements

    def count(self):
        return len(self.elements)

    def nth(self, index):
        return _FakeLocator([self.elements[index]])

    @property
    def first(self):
        return _FakeLocator(self.elements[:1])

    def inner_text(self, timeout=None):
        return self.elements[0].text if self.elements else ""

    def get_attribute(self, name):
        if not self.elements:
            return None
        if name == "class":
            return self.elements[0].class_attr
        return self.elements[0].attrs.get(name)

    def locator(self, selector):
        matched, seen = [], set()
        for root in self.elements:
            for descendant in root.iter_subtree():
                if descendant is root or id(descendant) in seen:
                    continue
                if _matches_any(descendant, selector):
                    matched.append(descendant)
                    seen.add(id(descendant))
        return _FakeLocator(matched)


class _FakePage:
    def __init__(self, root, url):
        self._root = root
        self.url = url

    def locator(self, selector):
        matched, seen = [], set()
        for descendant in self._root.iter_subtree():
            if id(descendant) in seen:
                continue
            if _matches_any(descendant, selector):
                matched.append(descendant)
                seen.add(id(descendant))
        return _FakeLocator(matched)


class DomExtractorTests(unittest.TestCase):
    def test_extracts_name_and_title_from_a_team_card(self):
        """<div class="team-member-card"><h3>Name</h3><p>Title</p></div>."""

        card = _FakeElement(
            "div",
            class_attr="team-member-card",
            text="John D'Souza\nManaging Director",
            children=[
                _FakeElement("h3", text="John D'Souza"),
                _FakeElement("p", text="Managing Director"),
            ],
        )
        page = _FakePage(_FakeElement("body", children=[card]), "https://example.com/leadership")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.LEADERSHIP)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "John D'Souza")
        self.assertEqual(candidates[0].canonical_designation, "Managing Director")
        self.assertEqual(candidates[0].page_category, PageCategory.LEADERSHIP)

    def test_container_email_and_linkedin_are_scoped_to_that_persons_card(self):
        card = _FakeElement(
            "div",
            class_attr="director-profile",
            text="Ravi Kumar\nDirector",
            children=[
                _FakeElement("h3", text="Ravi Kumar"),
                _FakeElement("p", text="Director"),
                _FakeElement("a", attrs={"href": "mailto:ravi@example.com"}),
                _FakeElement("a", attrs={"href": "https://linkedin.com/in/ravikumar"}),
            ],
        )
        page = _FakePage(_FakeElement("body", children=[card]), "https://example.com/board")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.BOARD)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].email, "ravi@example.com")
        self.assertEqual(candidates[0].linkedin_url, "https://linkedin.com/in/ravikumar")

    def test_falls_back_to_text_block_scan_when_no_container_matches(self):
        body = _FakeElement(
            "body",
            text="Ravi Kumar\nManaging Director\nSome Products Inc",
        )
        page = _FakePage(body, "https://example.com/about")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.ABOUT)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "Ravi Kumar")
        self.assertEqual(candidates[0].canonical_designation, "Managing Director")

    def test_unstructured_page_with_no_designation_yields_nothing(self):
        body = _FakeElement("body", text="Welcome to our company\nWe make valves")
        page = _FakePage(body, "https://example.com/home")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.OTHER)

        self.assertEqual(candidates, [])

    def test_generic_feature_card_with_a_fuzzy_designation_match_is_rejected(self):
        """Regression for the redbridgevalves.com false positive: a generic
        "feature-card" (not a team/member/profile/... class) whose body text
        merely *contains* a taxonomy phrase, with no email/phone/LinkedIn to
        back it up, must not be trusted."""

        card = _FakeElement(
            "div",
            class_attr="feature-card",
            text="Delivery Reliable\nGreat supply chain performance",
            children=[
                _FakeElement("h3", text="Delivery Reliable"),
                _FakeElement("p", text="Great supply chain performance"),
            ],
        )
        page = _FakePage(_FakeElement("body", children=[card]), "https://example.com/home")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.OTHER)

        self.assertEqual(candidates, [])

    def test_generic_list_item_with_an_exact_designation_match_is_still_accepted(self):
        """A plain <li> with no person-specific class is still trusted when
        the title is an exact taxonomy hit ("CEO"), even with no email/phone/
        LinkedIn - unlike the fuzzy-match case above."""

        item = _FakeElement(
            "li",
            text="Ravi Kumar\nCEO",
            children=[_FakeElement("h3", text="Ravi Kumar"), _FakeElement("p", text="CEO")],
        )
        page = _FakePage(_FakeElement("body", children=[item]), "https://example.com/home")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.OTHER)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "Ravi Kumar")

    def test_text_block_fallback_rejects_a_fuzzy_designation_neighbour_pair(self):
        """Regression for the redbridgevalves.com false positive reaching the
        text-block fallback: with no container to anchor on, a designation
        line that only *fuzzy*-matches the taxonomy (not an exact "CEO"/
        "Managing Director" hit) must not be paired with whatever
        name-shaped line happens to sit next to it on the page."""

        body = _FakeElement(
            "body",
            text="Delivery Reliable\nGreat supply chain performance",
        )
        page = _FakePage(body, "https://example.com/home")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.OTHER)

        self.assertEqual(candidates, [])

    def test_container_heading_with_title_concatenated_directly_against_name(self):
        """A heading like "Managing Director John Smith" (title and name
        with no separator at all - no dash, comma, or line break) must be
        split into (name, designation), not lost entirely or kept with the
        title text still embedded in the name."""

        card = _FakeElement(
            "div",
            class_attr="team-member-card",
            text="Managing Director John Smith",
            children=[_FakeElement("h3", text="Managing Director John Smith")],
        )
        page = _FakePage(_FakeElement("body", children=[card]), "https://example.com/leadership")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.LEADERSHIP)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "John Smith")
        self.assertEqual(candidates[0].canonical_designation, "Managing Director")

    def test_text_block_fallback_recovers_name_from_a_concatenated_neighbour(self):
        """Same title-concatenated-with-no-separator case, reached via the
        text-block fallback: the exact-match designation line ("CEO") stays
        the title, and the neighbouring line's own leading title-prefix is
        stripped to recover a usable name."""

        body = _FakeElement(
            "body",
            text="CEO\nManaging Director John Smith",
        )
        page = _FakePage(body, "https://example.com/about")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.ABOUT)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "John Smith")
        self.assertEqual(candidates[0].canonical_designation, "CEO")

    def test_generic_card_with_a_fuzzy_match_is_accepted_when_corroborated(self):
        """The same fuzzy-match generic card as above is accepted once it
        carries an actual contact signal (here, an email) - corroboration,
        not the class name, is what makes it trustworthy."""

        card = _FakeElement(
            "div",
            class_attr="feature-card",
            text="Delivery Reliable\nGreat supply chain performance",
            children=[
                _FakeElement("h3", text="Delivery Reliable"),
                _FakeElement("p", text="Great supply chain performance"),
                _FakeElement("a", attrs={"href": "mailto:delivery@example.com"}),
            ],
        )
        page = _FakePage(_FakeElement("body", children=[card]), "https://example.com/home")

        candidates = dom_extractor.extract_candidates(page, page.url, PageCategory.OTHER)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].email, "delivery@example.com")


if __name__ == "__main__":
    unittest.main()
