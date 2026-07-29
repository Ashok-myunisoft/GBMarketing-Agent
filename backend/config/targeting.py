"""
Static targeting reference data: industries, job-title functions, and
turnover/headcount bands. Broad Manufacturing searches fan out across the
specific taxonomy entries while retaining the request's overall result cap.
"""

import re


TARGET_INDUSTRIES = [
    {"level": "1st Level", "name": "Manufacturing"},
    {"level": "2nd Level", "name": "Auto components"},
    {"level": "2nd Level", "name": "Pump"},
    {"level": "2nd Level", "name": "Valve"},
    {"level": "2nd Level", "name": "Industrial equipments"},
    {"level": "2nd Level", "name": "Sheet metal fabrication"},
    {"level": "2nd Level", "name": "CNC job shop"},
    {"level": "2nd Level", "name": "Electrical panel"},
    {"level": "3rd Level", "name": "Foundry (Casting, Alloys, die, Tool die and moulds)"},
    {"level": "3rd Level", "name": "Gears and gear boxes (bearing)"},
    {"level": "3rd Level", "name": "Engineering (textile engineering components, agriculture engineering components)"},
    {"level": "3rd Level", "name": "Precision (CNC)"},
    {"level": "3rd Level", "name": "Defence and Aerospace Components"},
    {"level": "3rd Level", "name": "Automotive"},
    {"level": "3rd Level", "name": "Metal fabrication (sheet, steel, steel wires, steel coil, Press components)"},
    {"level": "3rd Level", "name": "Plastic (Pipe, Tubes and Fittings, Plastic components manufacturing)"},
    {"level": "3rd Level", "name": "Rubber (rubber components manufacturing)"},
    {"level": "3rd Level", "name": "Electronic and electrical components"},
    {"level": "3rd Level", "name": "Paper, printing and packaging"},
]

TARGET_DESIGNATIONS = [
    {
        "level": "1st Level",
        "function": "IT",
        "titles": [
            "CIO", "CTO", "IT Head", "ERP Manager", "EDP Manager", "Technical Heads",
            "System Admin", "CISO", "Digital Transformation Head", "IS (Infrastructure)",
        ],
    },
    {
        "level": "1st Level",
        "function": "HR",
        "titles": ["CHRO", "HR Head", "HR Manager", "Payroll Manager", "Talent Acquisition"],
    },
    {
        "level": "1st Level",
        "function": "C-Level",
        "titles": ["MD", "Managing Director", "CEO", "Director", "COO"],
    },
    {
        "level": "2nd Level",
        "function": "Production",
        "titles": ["Factory Head", "Plant Head", "Production Manager", "Operations Head", "Shop Floor Manager"],
    },
    {
        "level": "2nd Level",
        "function": "Finance",
        "titles": ["CFO", "Finance Manager", "Accounts Head", "Cost Accountant", "Commercial Head"],
    },
    {
        "level": None,
        "function": "Maintenance",
        "titles": ["Maintenance Manager", "Plant Head", "Engineering Head", "TPM Manager", "Utility Manager"],
    },
    {
        "level": None,
        "function": "Purchase",
        "titles": ["Purchase Manager", "Procurement Head", "Sourcing Manager", "Vendor Development", "Supply Chain"],
    },
    {
        "level": None,
        "function": "Sales",
        "titles": ["Business Head", "Sales Head", "Marketing Head"],
    },
    {
        "level": None,
        "function": "Service",
        "titles": ["Customer Support Manager"],
    },
    {
        "level": None,
        "function": "Quality",
        "titles": ["QA Manager", "QC Head", "Quality Engineer"],
    },
    {
        "level": None,
        "function": "Planning",
        "titles": ["PPC", "Production Planner", "Demand Planner"],
    },
    {
        "level": None,
        "function": "Warehouse",
        "titles": ["Stores Manager", "Warehouse Manager", "Inventory Controller"],
    },
    {
        "level": None,
        "function": "Logistics/Dispatch",
        "titles": [],
    },
    {
        "level": None,
        "function": "R&D/Design/Engineering",
        "titles": [],
    },
]

# Turnover bands in INR crore (Cr), lower bound inclusive.
TURNOVER_BANDS = [
    {"name": "Enterprise", "min_cr": 100, "max_cr": 500},
    {"name": "Mid Market", "min_cr": 25, "max_cr": 100},
    {"name": "MSME", "min_cr": 10, "max_cr": None},
]

MIN_TURNOVER_CR = 10
MIN_EMPLOYEE_COUNT = 100


def _primary_term(text: str) -> str:
    """Strips a trailing parenthetical qualifier, e.g. "Precision (CNC)" -> "precision"."""
    return text.split("(")[0].strip().lower()


def match_target_industry(text: str) -> "str | None":
    """
    Returns the matching TARGET_INDUSTRIES entry name if `text` looks
    like it belongs to one of the target industries (case-insensitive
    substring match against each entry's primary term), else None.

    2nd/3rd level (more specific) matches take priority over the
    generic 1st-level "Manufacturing" bucket - "Valve Manufacturing"
    should resolve to "Valve", not just "Manufacturing" - so the
    generic bucket is only returned as a fallback when nothing more
    specific matched.
    """

    if not text:
        return None

    normalized = text.lower()
    fallback = None

    for entry in TARGET_INDUSTRIES:
        primary = _primary_term(entry["name"])

        if not primary or primary not in normalized:
            continue

        if entry["level"] == "1st Level":
            fallback = fallback or entry["name"]
        else:
            return entry["name"]

    return fallback


def search_industry_queries(industry: "str | None") -> list[str]:
    """Returns one or more taxonomy queries for a user-selected industry.

    A specific target industry remains one precise query. A broad or omitted
    Manufacturing request fans out to the specific target segments, giving the
    discovery stage coverage across the configured ICP without duplicating the
    generic "Manufacturing" search for every segment.
    """
    matched = match_target_industry(industry or "")
    if matched and matched != "Manufacturing":
        return [matched]
    if industry and matched is None:
        return [industry]
    return [entry["name"] for entry in TARGET_INDUSTRIES if entry["level"] != "1st Level"]


def match_target_designation(title: str) -> "str | None":
    """
    Returns the matching function bucket name (e.g. "Finance", "IT") if
    `title` matches one of the target designation titles, else None.
    """

    if not title:
        return None

    normalized = title.lower()

    for entry in TARGET_DESIGNATIONS:
        for candidate in entry["titles"]:
            primary = _primary_term(candidate)
            # Word boundaries prevent abbreviations such as "MD" from
            # matching unrelated words and reduce false-positive contacts.
            if primary and re.search(r"\b" + re.escape(primary) + r"\b", normalized):
                return entry["function"]

    return None


def extract_target_designation(title: str) -> "str | None":
    """Returns the exact approved designation only for short, title-like text.

    This intentionally rejects paragraphs containing words such as "director"
    or "sales"; those are page content, not a person's job title.
    """
    normalized = re.sub(r"\s+", " ", (title or "").strip().lower())
    normalized = re.sub(r"^(designation|title|role)\s*[:\-]\s*", "", normalized)
    if not normalized or len(normalized) > 80:
        return None
    for entry in TARGET_DESIGNATIONS:
        for candidate in entry["titles"]:
            candidate_normalized = candidate.lower()
            if normalized == candidate_normalized:
                return candidate
    return None


def parse_turnover_range(text: "str | None") -> "tuple[float | None, float | None]":
    """Parses a turnover figure or slab string into an inclusive (min_cr, max_cr) range in Cr.

    Handles both a single figure ("12.5 Cr") and the slab wording GST-turnover
    lookups return, since the GST portal never exposes an exact figure to the
    public ("5 Cr to 25 Cr", "Above 500 Cr", "Up to 5 Cr"). Unrecognised text
    falls back to whatever single number can be found, or (None, None).
    """

    if not text:
        return None, None

    normalized = text.lower()
    numbers = [float(n) for n in re.findall(r"\d+(?:[,.]\d+)?", normalized.replace(",", ""))]

    if not numbers:
        return None, None
    if "above" in normalized and len(numbers) == 1:
        return numbers[0], None
    if ("upto" in normalized or "up to" in normalized or "below" in normalized) and len(numbers) == 1:
        return 0.0, numbers[0]
    if len(numbers) >= 2:
        return numbers[0], numbers[1]

    return numbers[0], numbers[0]


def turnover_band(turnover_cr: "float | None") -> "str | None":
    """Returns the matching TURNOVER_BANDS name for a turnover figure in Cr, or None if below MSME or unknown."""

    if turnover_cr is None:
        return None

    for band in TURNOVER_BANDS:
        if turnover_cr >= band["min_cr"] and (band["max_cr"] is None or turnover_cr < band["max_cr"]):
            return band["name"]

    return None
