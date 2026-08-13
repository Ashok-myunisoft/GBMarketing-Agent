"""Thin adapter over the existing designation canonicalizer - no new logic.
See services/contact_extraction/designation_rules.py for the alias table
(extended, not replaced, with the additional titles this pipeline needs)."""

from typing import Optional

from services.contact_extraction.designation_rules import canonical_designation


def validate(raw_title: Optional[str]) -> Optional[str]:
    if not raw_title:
        return None
    return canonical_designation(raw_title)
