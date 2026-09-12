import re


# ============================================================
# TARGET INDUSTRIES
# ============================================================

TARGET_INDUSTRIES = [
    {"level": "1st Level", "name": "Manufacturing"},

    {"level": "2nd Level", "name": "Auto components"},
    {"level": "2nd Level", "name": "Pump"},
    {"level": "2nd Level", "name": "Valve"},
    {"level": "2nd Level", "name": "Industrial equipments"},
    {"level": "2nd Level", "name": "Sheet metal fabrication"},
    {"level": "2nd Level", "name": "CNC job shop"},
    {"level": "2nd Level", "name": "Electrical panel"},

    {
        "level": "3rd Level",
        "name": "Foundry (Casting, Alloys, die, Tool die and moulds)",
    },
    {
        "level": "3rd Level",
        "name": "Gears and gear boxes (bearing)",
    },
    {
        "level": "3rd Level",
        "name": (
            "Engineering "
            "(textile engineering components, agriculture engineering components)"
        ),
    },
    {
        "level": "3rd Level",
        "name": "Precision (CNC)",
    },
    {
        "level": "3rd Level",
        "name": "Defence and Aerospace Components",
    },
    {
        "level": "3rd Level",
        "name": "Automotive",
    },
    {
        "level": "3rd Level",
        "name": (
            "Metal fabrication "
            "(sheet, steel, steel wires, steel coil, Press components)"
        ),
    },
    {
        "level": "3rd Level",
        "name": (
            "Plastic "
            "(Pipe, Tubes and Fittings, Plastic components manufacturing)"
        ),
    },
    {
        "level": "3rd Level",
        "name": (
            "Rubber "
            "(rubber components manufacturing)"
        ),
    },
    {
        "level": "3rd Level",
        "name": "Electronic and electrical components",
    },
    {
        "level": "3rd Level",
        "name": "Paper, printing and packaging",
    },
]


# ============================================================
# TARGET DESIGNATIONS
# ============================================================

TARGET_DESIGNATIONS = [
    {
        "level": "1st Level",
        "function": "IT",
        "titles": [
            "CIO",
            "CTO",
            "IT Head",
            "ERP Manager",
            "EDP Manager",
            "Technical Heads",
            "System Admin",
            "CISO",
            "Digital Transformation Head",
            "IS (Infrastructure)",
        ],
    },
    {
        "level": "1st Level",
        "function": "HR",
        "titles": [
            "CHRO",
            "HR Head",
            "HR Manager",
            "Payroll Manager",
            "Talent Acquisition",
        ],
    },
    {
        "level": "1st Level",
        "function": "C-Level",
        "titles": [
            "MD",
            "Managing Director",
            "CEO",
            "Director",
            "COO",
        ],
    },
    {
        "level": "2nd Level",
        "function": "Production",
        "titles": [
            "Factory Head",
            "Plant Head",
            "Production Manager",
            "Operations Head",
            "Shop Floor Manager",
        ],
    },
    {
        "level": "2nd Level",
        "function": "Finance",
        "titles": [
            "CFO",
            "Finance Manager",
            "Accounts Head",
            "Cost Accountant",
            "Commercial Head",
        ],
    },
    {
        "level": None,
        "function": "Maintenance",
        "titles": [
            "Maintenance Manager",
            "Plant Head",
            "Engineering Head",
            "TPM Manager",
            "Utility Manager",
        ],
    },
    {
        "level": None,
        "function": "Purchase",
        "titles": [
            "Purchase Manager",
            "Procurement Head",
            "Sourcing Manager",
            "Vendor Development",
            "Supply Chain",
        ],
    },
    {
        "level": None,
        "function": "Sales",
        "titles": [
            "Business Head",
            "Sales Head",
            "Marketing Head",
        ],
    },
    {
        "level": None,
        "function": "Service",
        "titles": [
            "Customer Support Manager",
        ],
    },
    {
        "level": None,
        "function": "Quality",
        "titles": [
            "QA Manager",
            "QC Head",
            "Quality Engineer",
        ],
    },
    {
        "level": None,
        "function": "Planning",
        "titles": [
            "PPC",
            "Production Planner",
            "Demand Planner",
        ],
    },
    {
        "level": None,
        "function": "Warehouse",
        "titles": [
            "Stores Manager",
            "Warehouse Manager",
            "Inventory Controller",
        ],
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


# ============================================================
# TURNOVER BANDS
# ============================================================

# Turnover bands in INR crore (Cr), lower bound inclusive.
TURNOVER_BANDS = [
    {
        "name": "Enterprise",
        "min_cr": 100,
        "max_cr": 500,
    },
    {
        "name": "Mid Market",
        "min_cr": 25,
        "max_cr": 100,
    },
    {
        "name": "MSME",
        "min_cr": 10,
        "max_cr": None,
    },
]


MIN_TURNOVER_CR = 10
MIN_EMPLOYEE_COUNT = 100


# ============================================================
# INDUSTRY NORMALIZATION
# ============================================================

def _primary_term(text: str) -> str:
    """
    Strips a trailing parenthetical qualifier.

    Example:

        Precision (CNC)
        ->
        precision
    """
    return text.split("(")[0].strip().lower()


_INDUSTRY_STOPWORDS = {
    "and",
    "of",
    "the",
    "for",
    "in",
    "on",
    "or",
    "a",
    "to",
}


def _first_keyword(entry_name: str) -> str | None:
    """
    Returns the first significant word of an industry entry.

    Example:

        "Paper, printing and packaging"

        -> "paper"
    """

    words = [
        word
        for word in re.findall(
            r"[a-z0-9]+",
            _primary_term(entry_name),
        )
        if word not in _INDUSTRY_STOPWORDS
    ]

    return words[0] if words else None


def _normalize_industry_text(text: str | None) -> str:
    """
    Normalizes user/LLM industry text for comparison while preserving
    the original text separately for targeted search.

    This is intentionally conservative.
    """

    if not text:
        return ""

    normalized = text.strip().lower()

    # Normalize separators.
    normalized = re.sub(
        r"[-_/]+",
        " ",
        normalized,
    )

    # Collapse whitespace.
    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    )

    return normalized.strip()


# ============================================================
# GENERIC INDUSTRY WORDS
# ============================================================

# These words do NOT provide enough information to perform a
# targeted industry search by themselves.
#
# Important:
# "manufacturing" is generic.
# "companies" is generic.
# "industries" is generic.
#
# But:
#
# "textile manufacturing"
#
# contains meaningful information:
#
# "textile"
#
# Therefore it should be searched directly.

_GENERIC_INDUSTRY_WORDS = {
    "manufacturing",
    "manufacturer",
    "manufacturers",
    "manufacturing company",
    "manufacturing companies",
    "manufacturing industry",
    "manufacturing industries",

    "industry",
    "industries",

    "company",
    "companies",

    "business",
    "businesses",

    "firm",
    "firms",

    "enterprise",
    "enterprises",

    "organization",
    "organizations",

    "organisation",
    "organisations",

    "supplier",
    "suppliers",

    "vendor",
    "vendors",

    "manufacturer company",
    "manufacturer companies",

    "industrial",
}


def _industry_tokens(text: str) -> list[str]:
    """
    Tokenizes industry text into meaningful alphanumeric tokens.
    """

    return re.findall(
        r"[a-z0-9]+",
        _normalize_industry_text(text),
    )


def _has_specific_industry_content(industry: str | None) -> bool:
    """
    Returns True when the industry text contains meaningful information
    beyond generic filler words.

    Examples:

        "manufacturing companies"
            -> False

        "manufacturing industry"
            -> False

        "textile manufacturing companies"
            -> True

        "industrial automation companies"
            -> True

        "pharmaceutical equipment"
            -> True

    This function deliberately does NOT try to classify the industry.
    It only determines whether the user's text contains enough
    meaningful content to justify a targeted search.
    """

    normalized = _normalize_industry_text(industry)

    if not normalized:
        return False

    tokens = _industry_tokens(normalized)

    if not tokens:
        return False

    # Remove generic filler tokens.
    meaningful_tokens = [
        token
        for token in tokens
        if token not in {
            "manufacturing",
            "manufacturer",
            "manufacturers",
            "industry",
            "industries",
            "company",
            "companies",
            "business",
            "businesses",
            "firm",
            "firms",
            "enterprise",
            "enterprises",
            "organization",
            "organizations",
            "organisation",
            "organisations",
            "supplier",
            "suppliers",
            "vendor",
            "vendors",
            "industrial",
        }
    ]

    return bool(meaningful_tokens)


def _clean_targeted_industry_query(industry: str) -> str:
    """
    Cleans an unknown industry phrase before using it as a search query.

    It intentionally preserves the actual industry wording.

    Example:

        "Textile Machinery Companies"

        -> "Textile Machinery"

    This prevents unnecessary repetition of generic words such as
    "companies" while preserving the user's real industry description.
    """

    cleaned = re.sub(
        r"\s+",
        " ",
        industry.strip(),
    )

    # Remove generic trailing business nouns.
    cleaned = re.sub(
        r"\b(?:companies|company|businesses|business|firms|firm)\b$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Remove generic leading/trailing "industry/industries" only when
    # they are acting as filler.
    cleaned = re.sub(
        r"^\s*(?:industry|industries)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        r"\s+(?:industry|industries)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned,
    ).strip()

    return cleaned


# ============================================================
# INDUSTRY MATCHING
# ============================================================

def match_target_industry(text: str) -> str | None:
    """
    Returns the matching TARGET_INDUSTRIES entry name if `text` looks
    like it belongs to one of the target industries.

    Matching rules:

    1. Specific 2nd/3rd-level industries take priority.
    2. Generic 1st-level Manufacturing is only a fallback.
    3. Unknown industries return None.

    Examples:

        "Valve companies"
            -> "Valve"

        "Pump manufacturers"
            -> "Pump"

        "Manufacturing companies"
            -> "Manufacturing"

        "Textile machinery companies"
            -> None
    """

    if not text:
        return None

    normalized = _normalize_industry_text(text)

    if not normalized:
        return None

    fallback = None

    for entry in TARGET_INDUSTRIES:
        primary = _primary_term(
            entry["name"]
        )

        keyword = _first_keyword(
            entry["name"]
        )

        matched_by_phrase = (
            bool(primary)
            and primary in normalized
        )

        matched_by_keyword = (
            bool(keyword)
            and re.search(
                r"\b" + re.escape(keyword) + r"\b",
                normalized,
            )
            is not None
        )

        if not (
            matched_by_phrase
            or matched_by_keyword
        ):
            continue

        # Generic Manufacturing is only a fallback.
        if entry["level"] == "1st Level":
            fallback = fallback or entry["name"]

        else:
            # Specific taxonomy match.
            return entry["name"]

    return fallback


# ============================================================
# INDUSTRY SEARCH QUERY GENERATION
# ============================================================

def search_industry_queries(
    industry: str | None,
) -> list[str]:
    """
    Returns the search queries that should be used for industry discovery.

    Rules:

    1. Specific catalogued industry:
           return exactly that catalogued industry.

       Example:
           Valve
           -> ["Valve"]

    2. Generic Manufacturing:
           expand across all configured 2nd/3rd-level taxonomy entries.

       Example:
           Manufacturing
           -> [
                "Auto components",
                "Pump",
                "Valve",
                ...
              ]

    3. Unknown but meaningful industry:
           search exactly that industry text as ONE targeted query.

       Example:
           Textile machinery
           -> ["Textile machinery"]

           Industrial automation
           -> ["Industrial automation"]

    4. Empty/generic industry:
           use the configured manufacturing taxonomy.
    """

    original = (industry or "").strip()

    if not original:
        print(
            "[TARGETING][INDUSTRY] "
            "No industry provided; expanding manufacturing taxonomy."
        )

        return [
            entry["name"]
            for entry in TARGET_INDUSTRIES
            if entry["level"] != "1st Level"
        ]

    matched = match_target_industry(
        original
    )

    # --------------------------------------------------------
    # CASE 1:
    # Specific catalogued segment.
    # --------------------------------------------------------

    if (
        matched
        and matched.strip().lower() != "manufacturing"
    ):
        print(
            "[TARGETING][INDUSTRY] "
            f"Specific catalogued industry: {matched!r}"
        )

        return [matched]

    # --------------------------------------------------------
    # CASE 2:
    # Generic Manufacturing.
    #
    # Do NOT search:
    #
    #     Manufacturing
    #
    # once for every taxonomy entry.
    #
    # Instead search each configured specific segment.
    # --------------------------------------------------------

    if (
        matched
        and matched.strip().lower() == "manufacturing"
    ):
        # If the text itself contains specific information in addition
        # to "manufacturing", do NOT fan out.
        #
        # Example:
        #
        # "textile manufacturing companies"
        #
        # should be one targeted query.
        if _has_specific_industry_content(original):

            targeted = _clean_targeted_industry_query(
                original
            )

            if targeted:
                print(
                    "[TARGETING][INDUSTRY] "
                    "Generic Manufacturing match contains "
                    f"specific text; targeted query={targeted!r}"
                )

                return [targeted]

        print(
            "[TARGETING][INDUSTRY] "
            "Generic Manufacturing request; "
            "expanding configured taxonomy."
        )

        return [
            entry["name"]
            for entry in TARGET_INDUSTRIES
            if entry["level"] != "1st Level"
        ]

    # --------------------------------------------------------
    # CASE 3:
    # No catalogued match.
    #
    # If the user/LLM supplied meaningful industry text,
    # search exactly that text once.
    # --------------------------------------------------------

    if matched is None:

        if _has_specific_industry_content(
            original
        ):

            targeted = _clean_targeted_industry_query(
                original
            )

            if targeted:
                print(
                    "[TARGETING][INDUSTRY] "
                    "Unknown but meaningful industry; "
                    f"using one targeted query={targeted!r}"
                )

                return [targeted]

        # Only generic filler.
        print(
            "[TARGETING][INDUSTRY] "
            "Industry contains no specific information; "
            "expanding manufacturing taxonomy."
        )

        return [
            entry["name"]
            for entry in TARGET_INDUSTRIES
            if entry["level"] != "1st Level"
        ]

    # Defensive fallback.
    return [original]


# ============================================================
# DESIGNATION MATCHING
# ============================================================

def match_target_designation(
    title: str,
) -> str | None:
    """
    Returns the matching function bucket name
    (e.g. "Finance", "IT") if `title` matches one
    of the target designation titles, else None.
    """

    if not title:
        return None

    normalized = title.lower()

    for entry in TARGET_DESIGNATIONS:
        for candidate in entry["titles"]:

            primary = _primary_term(
                candidate
            )

            # Word boundaries prevent abbreviations such as
            # "MD" from matching unrelated words.
            if (
                primary
                and re.search(
                    r"\b"
                    + re.escape(primary)
                    + r"\b",
                    normalized,
                )
            ):
                return entry["function"]

    return None


# ============================================================
# DESIGNATION EXTRACTION
# ============================================================

def extract_target_designation(
    title: str,
) -> str | None:
    """
    Returns the exact approved designation only for short,
    title-like text.

    This intentionally rejects paragraphs containing words such
    as "director" or "sales"; those are page content, not a
    person's job title.
    """

    normalized = re.sub(
        r"\s+",
        " ",
        (title or "").strip().lower(),
    )

    normalized = re.sub(
        r"^(designation|title|role)\s*[:\-]\s*",
        "",
        normalized,
    )

    if not normalized:
        return None

    if len(normalized) > 80:
        return None

    for entry in TARGET_DESIGNATIONS:
        for candidate in entry["titles"]:

            candidate_normalized = (
                candidate.lower()
            )

            if (
                normalized
                == candidate_normalized
            ):
                return candidate

    return None


# ============================================================
# TURNOVER RANGE
# ============================================================

def parse_turnover_range(
    text: str | None,
) -> tuple[float | None, float | None]:
    """
    Parses a turnover figure or slab string into an inclusive
    (min_cr, max_cr) range in Cr.

    Handles:

        "12.5 Cr"
        "5 Cr to 25 Cr"
        "Above 500 Cr"
        "Up to 5 Cr"

    Unrecognized text falls back to whatever single number can
    be found, or (None, None).
    """

    if not text:
        return None, None

    normalized = text.lower()

    numbers = [
        float(number)
        for number in re.findall(
            r"\d+(?:[,.]\d+)?",
            normalized.replace(",", ""),
        )
    ]

    if not numbers:
        return None, None

    if (
        "above" in normalized
        and len(numbers) == 1
    ):
        return numbers[0], None

    if (
        (
            "upto" in normalized
            or "up to" in normalized
            or "below" in normalized
        )
        and len(numbers) == 1
    ):
        return 0.0, numbers[0]

    if len(numbers) >= 2:
        return numbers[0], numbers[1]

    return numbers[0], numbers[0]


# ============================================================
# TURNOVER -> CRORE (unit-aware)
# ============================================================

# Same unit vocabulary services/gst_turnover_enrichment/turnover_extraction.py
# produces ("crore|cr|lakh|lac|lakhs|million|mn|billion|bn") - unlike
# parse_turnover_range above (which assumes every figure is already in Cr,
# fine for its own use validating against a user-specified Cr floor), this
# is unit-aware: "128 Million" and "128 Crore" are NOT the same magnitude
# (128 Million = 12.8 Cr), and blindly treating them the same would silently
# write a wrong number into any numeric column driven off this.
_TURNOVER_UNIT_TO_CRORE = {
    "crore": 1.0, "cr": 1.0,
    "lakh": 0.01, "lac": 0.01, "lakhs": 0.01,
    "million": 0.1, "mn": 0.1,
    "billion": 100.0, "bn": 100.0,
}
_TURNOVER_VALUE_UNIT_RE = re.compile(
    r"([\d.]+)\s*(crore|cr\.?|lakh|lac|lakhs|million|mn|billion|bn)\b",
    re.IGNORECASE,
)


def turnover_to_crore(text: str | None) -> float | None:
    """Converts a turnover figure/slab string (e.g. "128 Crore", "50
    Million", or a jamku slab "5 Cr to 25 Cr") into a single number in
    Crore (Rs 1,00,00,000), doing real unit conversion rather than
    assuming every number is already in Cr. Returns None when nothing
    with a recognizable unit is found - never guesses a unit.

    For a slab/range, returns the LOWER bound - conservative, so a range
    this can't resolve to one exact figure is never overstated.
    """
    if not text:
        return None
    matches = _TURNOVER_VALUE_UNIT_RE.findall(text.replace(",", ""))
    crore_values = []
    for raw_value, unit in matches:
        multiplier = _TURNOVER_UNIT_TO_CRORE.get(unit.rstrip(".").lower())
        if multiplier is None:
            continue
        try:
            crore_values.append(float(raw_value) * multiplier)
        except ValueError:
            continue
    return min(crore_values) if crore_values else None


# ============================================================
# TURNOVER BAND
# ============================================================

def turnover_band(
    turnover_cr: float | None,
) -> str | None:
    """
    Returns the matching TURNOVER_BANDS name for a turnover
    figure in Cr, or None if below MSME or unknown.
    """

    if turnover_cr is None:
        return None

    for band in TURNOVER_BANDS:

        if (
            turnover_cr >= band["min_cr"]
            and (
                band["max_cr"] is None
                or turnover_cr < band["max_cr"]
            )
        ):
            return band["name"]

    return None