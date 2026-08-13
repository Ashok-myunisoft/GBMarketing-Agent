"""Thin adapter over the existing person-name heuristics - no new logic.
See services/contact_extraction/name_rules.py."""

from typing import Optional

from services.contact_extraction.name_rules import is_person_name, normalize_name


def validate(name: Optional[str]) -> Optional[str]:
    if not name or not is_person_name(name):
        return None
    return normalize_name(name)
