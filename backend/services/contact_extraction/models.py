from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class PageCategory(IntEnum):
    """Ranks a page by how likely it is to name a real decision-maker.

    Values double as the page-weight component of the confidence score, so
    a plain int comparison (or subtraction) already reflects Step 1's
    "leadership over board over about over contact" ordering.
    """

    OTHER = 0
    CONTACT = 10
    TEAM = 20
    ABOUT = 25
    BOARD = 35
    LEADERSHIP = 40


@dataclass
class ContactCandidate:
    """One possible (name, title) pairing found on a page or external source."""

    name: str
    raw_title: str
    canonical_designation: Optional[str]
    page_category: PageCategory
    source_url: str
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None
    # "website" | "filesure" | "linkedin" - which system this candidate came
    # from, used for the cross-source agreement bonus in scorer.py.
    source: str = "website"
    evidence: list = field(default_factory=list)
    score: float = 0.0
    # Every distinct source that agreed on this candidate after dedupe.py
    # merges duplicates together - starts as just `source` for a fresh,
    # unmerged candidate.
    matched_sources: set = field(default_factory=set)
    # Every distinct page/source URL this candidate (or a merged duplicate)
    # was seen on - starts as just `source_url`.
    source_urls: set = field(default_factory=set)

    def __post_init__(self):
        if not self.matched_sources:
            self.matched_sources = {self.source}
        if not self.source_urls:
            self.source_urls = {self.source_url}


@dataclass
class ContactExtractionResult:
    """The final Step 9 output: one ranked contact plus its supporting evidence."""

    contact_person: Optional[str]
    designation: Optional[str]
    linkedin_url: Optional[str]
    confidence: float
    evidence: list
    source_urls: list
