"""Pure functions: which rows to upload and how sheet values become Affinity values.

No network calls here, so everything in this file is covered by tests/.
"""
import re
from datetime import datetime


# ---- which rows go to Affinity ------------------------------------------
def split_flags(text):
    return [f.strip().lower() for f in re.split(r"[,;\s]+", text or "") if f.strip()]


def row_is_eligible(row, ignore_flags=()):
    """Upload rule agreed with the scouting team (Nov 2026):
    - status 'new' and no blocking flags              -> upload
    - status 'new' or 'review' and decision 'approve' -> upload
    - anything else                                   -> leave alone
    Flags listed in ignore_flags (Config) don't block, e.g. invalid_phone."""
    status = (row.get("status") or "").strip().lower()
    ignore = {f.lower() for f in ignore_flags}
    blocking = [f for f in split_flags(row.get("flags")) if f not in ignore]
    decision = (row.get("decision") or "").strip().lower()
    if status == "new" and not blocking:
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
    def norm(t):  # Webflow sends "A,B", Typeform "A, B": compare both as "A, B"
        return re.sub(r"\s*,\s*", ", ", t.strip())

    remaining = ", " + norm(value) + ", "
    matched = []
    for o in sorted(options, key=lambda o: len(o["text"]), reverse=True):
        token = ", " + norm(o["text"]) + ", "
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


# ---- aliases (Aliases tab: sheet value -> Affinity option) ----------------
def apply_aliases(raw, aliases, field_id):
    """Replace known misspellings before dropdown matching.
    aliases: list of dicts with sheet_value, affinity_option, field_id ('' = all fields)."""
    out = raw
    for a in sorted(aliases, key=lambda a: len(a["sheet_value"]), reverse=True):
        if a["field_id"] and a["field_id"] != field_id:
            continue
        idx = out.lower().find(a["sheet_value"].lower())
        while idx >= 0:
            out = out[:idx] + a["affinity_option"] + out[idx + len(a["sheet_value"]):]
            idx = out.lower().find(a["sheet_value"].lower(), idx + len(a["affinity_option"]))
    return out


# ---- phone numbers -------------------------------------------------------
def normalise_phone(value):
    """International format, '+' and digits only. Returns None if unreadable.
    Same rules as registration-preprocessing: 00 -> +, leading 0 = Dutch (+31),
    9 digits starting with 6 = Dutch mobile, digits with a country code get a '+'."""
    v = re.sub(r"[\s\-().]", "", value.strip())
    if v.startswith("+"):
        digits, plus = v[1:], True
    else:
        digits, plus = v, False
    if not digits.isdigit():
        return None
    if plus:
        out = "+" + digits
    elif digits.startswith("00"):
        out = "+" + digits[2:]
    elif digits.startswith("0"):
        out = "+31" + digits[1:]
    elif len(digits) == 9 and digits.startswith("6"):
        out = "+31" + digits
    else:
        out = "+" + digits
    return out if 9 <= len(out) - 1 <= 15 else None


# ---- templates -----------------------------------------------------------
def render_template(template, values):
    """'{col}' is replaced by the row value, '{col|date}' by its first 10 characters.
    A part in [square brackets] is dropped when any placeholder inside it is empty,
    e.g. '{submitted_at|date}: {job_title}[ ({company})]'."""
    def fill(part):
        empty = False

        def sub(m):
            nonlocal empty
            name, _, fmt = m.group(1).partition("|")
            v = str(values.get(name.strip().lower(), "") or "").strip()
            if fmt.strip() == "date":
                v = v[:10]
            if not v:
                empty = True
            return v
        out = re.sub(r"\{([^{}]+)\}", sub, part)
        return out, empty

    result = []
    for opt, plain in re.findall(r"\[([^\[\]]*)\]|([^\[\]]+)", template):
        if opt:
            text, empty = fill(opt)
            if not empty:
                result.append(text)
        else:
            text, _ = fill(plain)
            result.append(text)
    return "".join(result).strip()


# ---- company names -------------------------------------------------------
LEGAL_SUFFIX = re.compile(r"[\s,]+(b\.?\s?v\.?|n\.?\s?v\.?|ltd\.?|gmbh|inc\.?|bv|holding)$", re.I)


def normalise_company(name):
    """'Villari B.V.' and 'villari' compare equal; used for organization lookup only."""
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    prev = None
    while prev != n:
        prev, n = n, LEGAL_SUFFIX.sub("", n).strip()
    return n


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
