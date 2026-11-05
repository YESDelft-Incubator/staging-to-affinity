"""Pure functions: which rows to upload and how sheet values become Affinity values.

No network calls here, so everything in this file is covered by tests/.
"""
import re
from datetime import datetime


# ---- which rows go to Affinity ------------------------------------------
def row_is_eligible(row):
    """Upload rule agreed with the scouting team (Nov 2026):
    - status 'new' and no flags                      -> upload
    - status 'new' or 'review' and decision 'approve' -> upload
    - anything else                                   -> leave alone
    """
    status = (row.get("status") or "").strip().lower()
    flags = (row.get("flags") or "").strip()
    decision = (row.get("decision") or "").strip().lower()
    if status == "new" and not flags:
        return True
    return status in ("new", "review") and decision == "approve"


def is_approved(row):
    return (row.get("decision") or "").strip().lower() == "approve"


# ---- dropdowns -----------------------------------------------------------
def match_dropdown(value, options):
    """Single dropdown: exact option text, case-insensitive. Returns option or None."""
    v = value.strip().lower()
    for o in options:
        if o["text"].strip().lower() == v:
            return o
    return None


def match_multi_dropdown(value, options):
    """Split a Typeform multi-choice string into dropdown options.

    Option texts can themselves contain commas ("Automotive, Transportation, and
    Logistics"), so the string is not split on commas. Options are matched longest
    first; whatever text is left over is returned as unknown.
    Returns (matched_options, unknown_parts).
    """
    remaining = ", " + value.strip() + ", "
    matched = []
    for o in sorted(options, key=lambda o: len(o["text"]), reverse=True):
        token = ", " + o["text"].strip() + ", "
        idx = remaining.lower().find(token.lower())
        if idx >= 0:
            matched.append(o)
            remaining = remaining[:idx] + ", " + remaining[idx + len(token):]
    unknown = [p.strip() for p in remaining.split(",") if p.strip()]
    return matched, unknown


TRUE_WORDS = {"true", "yes", "ja", "1"}
FALSE_WORDS = {"false", "no", "nee", "0"}


def match_boolean_dropdown(value, options):
    """Map TRUE/FALSE from the sheet to the dropdown's Yes/No style option."""
    v = value.strip().lower()
    words = TRUE_WORDS if v in TRUE_WORDS else FALSE_WORDS if v in FALSE_WORDS else None
    if words is None:
        return None
    for o in options:
        if o["text"].strip().lower() in words:
            return o
    return None


# ---- countries -----------------------------------------------------------
COUNTRY_ALIASES = {
    "nederland": "Netherlands", "the netherlands": "Netherlands", "nl": "Netherlands",
    "holland": "Netherlands", "netherlands": "Netherlands",
    "belgie": "Belgium", "belgië": "Belgium", "belgique": "Belgium",
    "duitsland": "Germany", "deutschland": "Germany",
    "frankrijk": "France", "spanje": "Spain", "españa": "Spain", "italie": "Italy",
    "italië": "Italy", "italia": "Italy", "verenigd koninkrijk": "United Kingdom",
    "uk": "United Kingdom", "england": "United Kingdom", "great britain": "United Kingdom",
    "usa": "United States", "us": "United States", "united states of america": "United States",
    "verenigde staten": "United States", "zwitserland": "Switzerland", "schweiz": "Switzerland",
    "oostenrijk": "Austria", "österreich": "Austria", "zweden": "Sweden", "denemarken": "Denmark",
    "noorwegen": "Norway", "polen": "Poland", "portugal": "Portugal", "griekenland": "Greece",
    "turkije": "Turkey", "türkiye": "Turkey", "india": "India", "china": "China",
}


def normalise_country(value):
    """Returns (english_name, recognised). Unrecognised values are kept as typed."""
    v = value.strip()
    key = v.lower().rstrip(".")
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key], True
    try:
        import pycountry
        c = pycountry.countries.lookup(v)
        return getattr(c, "common_name", None) or c.name, True
    except Exception:
        return v, False


# ---- dates ---------------------------------------------------------------
def to_iso_date(value):
    """Sheet dates are 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' (registration-preprocessing)."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    return None


def append_text(existing, addition, sep="; "):
    """Append without duplicating: 'December 2026' + 'March 2027'."""
    parts = [p.strip() for p in (existing or "").split(sep.strip()) if p.strip()]
    if addition.strip() and addition.strip() not in parts:
        parts.append(addition.strip())
    return sep.join(parts)


def truncate(text, n=300):
    text = str(text)
    return text if len(text) <= n else text[:n] + " [truncated]"


def clean_header(h):
    return re.sub(r"\s+", " ", (h or "").strip().lower())
