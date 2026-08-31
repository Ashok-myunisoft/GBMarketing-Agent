"""
Deterministic person-name validation and normalization (Step 4).

Replaces `EnrichmentAgent._looks_like_person_name`/`_normalise_person_name`,
which rejected initials ("A.K. Sharma"), apostrophes ("D'Souza") and
mis-capitalized anything with punctuation. No spaCy/NER dependency: this is
pure rule-based tokenizing, matching the site-wide "deterministic first"
approach - the LLM fallback in llm_disambiguator.py only engages when
scoring can't confidently pick between candidates, not for name shape itself.
"""

import re

from services.contact_extraction.designation_rules import is_exact_match as _is_known_designation

# Same stoplist EnrichmentAgent used (moved here, not duplicated elsewhere):
# rejects company/nav/product words that would otherwise pass the shape
# checks below (e.g. "Butterfly Valves" is 2 alphabetic words too).
NAME_EXCLUDE_WORDS = {
    "pvt", "ltd", "limited", "private", "industries", "enterprises", "company",
    "corporation", "llp", "inc", "solutions", "technologies", "email", "phone",
    "contact", "address", "products", "services", "engineers", "engineering",
    "about", "us", "home", "careers", "our", "more", "read", "learn", "view",
    "get", "india", "manufacturing", "coimbatore",
    "who", "are", "automation", "valves", "butterfly", "online", "retail",
    "channel", "customer", "centric", "approach", "major", "market", "bengaluru",
    "karnataka", "tamil", "nadu",
}

_TITLE_TOKENS = {"dr", "mr", "mrs", "ms", "shri", "smt", "prof"}
_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii"}

# Generic single-word job-title terms that services/contact_extraction/
# designation_rules.py's canonical table either doesn't define on their own
# (e.g. bare "President") or only defines as part of a compound title (e.g.
# "IT Head", "Country Head", never standalone "Head"). A candidate made up
# entirely of these words (e.g. "Chief Executive Officer") is a title, not a
# person's name, even though none of its individual words are company/nav
# text and would otherwise be excluded by NAME_EXCLUDE_WORDS above.
_GENERIC_TITLE_WORDS = {"president", "chief", "executive", "officer", "head", "chairperson"}

# A single initial ("K", "A.") or a run of them without spaces ("A.K.").
_INITIAL_RE = re.compile(r"^[A-Za-z]\.?$")
_MULTI_INITIAL_RE = re.compile(r"^(?:[A-Za-z]\.){2,4}$")
# A real word: letters only, optionally hyphenated or with an apostrophe -
# covers "John", "D'Souza", "Mary-Anne".
_WORD_RE = re.compile(r"^[A-Za-z]+(?:['-][A-Za-z]+)+$|^[A-Za-z]{2,}$")


def _is_title_or_suffix(token: str) -> bool:
    return token.strip(".").lower() in _TITLE_TOKENS | _SUFFIX_TOKENS


def is_person_name(value: str) -> bool:
    """Whether `value` plausibly reads as a person's name, not page/company text.

    Accepts initials ("A.K. Sharma", "K S Mani", "R Srinivasan"), prefixes
    ("Dr Raj Kumar"), apostrophes ("John D'Souza"), and hyphenated names
    ("Mary-Anne Thomas") - none of which the old exact-shape validator did.
    """

    if not value or not value.strip():
        return False

    tokens = [token.strip(",()") for token in value.strip().split()]
    tokens = [token for token in tokens if token]
    if not tokens:
        return False

    core = list(tokens)
    while core and _is_title_or_suffix(core[0]):
        core.pop(0)
    while core and _is_title_or_suffix(core[-1]):
        core.pop()

    # A single leftover token ("Nff", "Login", a stray icon-font glyph) is
    # indistinguishable from a real single-word name by shape alone - every
    # example this validator needs to accept (initials included) has at
    # least a first and last part, so requiring 2 keeps that false-positive
    # class out without rejecting anything in scope.
    if not 2 <= len(core) <= 5:
        return False

    # A candidate that is itself a known job title/designation - whether a
    # single canonical term or a short compound like "Managing Director" -
    # is not a person's name, however name-shaped it reads structurally.
    # Checked up front so a two-word title doesn't slip through just
    # because both of its words individually pass the per-token checks
    # below (neither "Managing" nor "Director" is in NAME_EXCLUDE_WORDS).
    if _is_known_designation(" ".join(core)):
        return False

    real_words = 0
    title_words = 0
    for token in core:
        if _INITIAL_RE.match(token) or _MULTI_INITIAL_RE.match(token):
            continue
        if not _WORD_RE.match(token):
            return False
        lowered = token.lower()
        if len(token) >= 2 and lowered in NAME_EXCLUDE_WORDS:
            return False
        if lowered in _GENERIC_TITLE_WORDS:
            title_words += 1
        real_words += 1

    # Every real (non-initial) token was a generic title word (e.g. "Chief
    # Executive Officer") - none of those words individually qualifies as
    # company/nav text above, but together they are nothing but a title.
    if real_words and title_words == real_words:
        return False

    return real_words >= 1


def normalize_name(value: str) -> str:
    """Title-cases every alphabetic run in each token, respecting internal
    punctuation instead of blindly `.capitalize()`-ing the whole token.

    "d'souza" -> "D'Souza" (not "D'souza"); "a.k." -> "A.K."; "mary-anne" ->
    "Mary-Anne".
    """

    tokens = value.strip().split()
    return " ".join(
        re.sub(r"[A-Za-z]+", lambda match: match.group(0).capitalize(), token)
        for token in tokens
    )