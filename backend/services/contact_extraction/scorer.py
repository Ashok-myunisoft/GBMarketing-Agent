"""
Confidence scoring (Step 6): page weight + designation seniority + evidence
bonuses, as one tunable table instead of magic numbers scattered through the
extraction code.
"""

from urllib.parse import urlparse

from services.contact_extraction.models import ContactCandidate

# Seniority weight per canonical designation (from designation_rules.py).
# Titles not listed fall back to a heuristic based on the word "head"/
# "manager" in the canonical string, then DEFAULT_DESIGNATION_WEIGHT.
DESIGNATION_WEIGHTS = {
    "Managing Director": 100,
    "Managing Partner": 90,
    "Whole-time Director": 90,
    "Executive Director": 85,
    "CEO": 100,
    "Founder": 90,
    "Director": 75,
    "COO": 80,
    "CFO": 80,
    "CTO": 75,
    "CIO": 75,
    "CHRO": 75,
    "Vice President": 70,
    "General Manager": 60,
    "HR Head": 55,
    "Procurement Head": 55,
    "IT Head": 55,
    "Supply Chain": 50,
    "Commercial Head": 50,
    "Stores Manager": 40,
    "Purchase Manager": 40,
    "Talent Acquisition": 40,
    "Manager": 40,
}
DEFAULT_DESIGNATION_WEIGHT = 30

BONUS = {
    "corporate_email": 20,
    "linkedin": 50,
    "filesure": 30,
    "cross_source_website": 20,
    "phone_present": 5,
}


def designation_weight(canonical_designation: "str | None") -> float:
    if not canonical_designation:
        return 0.0
    if canonical_designation in DESIGNATION_WEIGHTS:
        return float(DESIGNATION_WEIGHTS[canonical_designation])
    lowered = canonical_designation.lower()
    if "head" in lowered:
        return 50.0
    if "manager" in lowered:
        return 40.0
    return float(DEFAULT_DESIGNATION_WEIGHT)


def _is_corporate_email(email: "str | None", company_website: "str | None") -> bool:
    if not email or not company_website or "@" not in email:
        return False
    email_domain = email.rsplit("@", 1)[-1].lower().removeprefix("www.")
    site_domain = (urlparse(company_website).netloc or company_website).lower().removeprefix("www.")
    return bool(email_domain) and bool(site_domain) and email_domain == site_domain


def score(candidate: ContactCandidate, company_website: "str | None" = None) -> float:
    """Mutates and returns `candidate.score`; safe to call more than once."""

    total = float(int(candidate.page_category))
    total += designation_weight(candidate.canonical_designation)

    if _is_corporate_email(candidate.email, company_website):
        total += BONUS["corporate_email"]
    if candidate.linkedin_url or "linkedin" in candidate.matched_sources:
        total += BONUS["linkedin"]
    if "filesure" in candidate.matched_sources:
        total += BONUS["filesure"]
    if "website" in candidate.matched_sources and len(candidate.matched_sources) > 1:
        total += BONUS["cross_source_website"]
    if candidate.phone:
        total += BONUS["phone_present"]

    candidate.score = total
    return total
