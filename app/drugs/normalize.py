"""Text normalization for drug names and medication rows."""
from __future__ import annotations

import re
import unicodedata

_SALTS = {
    "hydrochloride", "hcl", "sodium", "potassium", "calcium", "succinate", "tartrate", "besylate", "maleate",
    "mesylate", "citrate", "sulfate", "sulphate", "fumarate", "bromide", "acetate", "phosphate", "magnesium",
    "monohydrate", "dihydrate", "bitartrate", "hyclate", "monohydrochloride", "dipropionate", "propionate",
    "furoate", "xinafoate", "trihydrate", "disodium", "medoxomil", "mofetil",
}
_FORM_WORDS = {
    "tablet", "tablets", "tab", "tabs", "capsule", "capsules", "cap", "caps", "oral", "po", "solution", "suspension",
    "injection", "inj", "injectable", "cream", "ointment", "gel", "patch", "transdermal", "er", "xr", "xl", "sr", "cr",
    "dr", "la", "ec", "odt", "ir", "extended", "release", "delayed", "immediate", "chewable", "pen", "vial", "inhaler",
    "inhalation", "nebulizer", "nebuliser", "spray", "nasal", "drops", "liquid", "syrup", "elixir", "pill", "pills",
    "mg", "mcg", "g", "ml", "units", "unit", "iu", "meq", "%",
}
_DOSE_TOKEN = re.compile(r"^\d+(?:[.,]\d+)?(?:mg|mcg|g|ml|units?|iu|meq|%)?$")


def norm(text: str | None) -> str:
    """Lower-case, strip accents and marks, unify combination separators."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("®", "").replace("™", "").replace("’", "'")
    s = s.replace("'s", "s").replace("'", "")
    s = re.sub(r"\s+and\s+", "-", s)
    s = re.sub(r"\s*[/+]\s*", "-", s)
    s = re.sub(r"[^a-z0-9.\-% ]+", " ", s)
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"(?<!\d)\.(?!\d)", " ", s)  # drop dots that are not decimal points
    s = re.sub(r"\s+", " ", s).strip(" -")
    return s


def strip_dose_and_form(text: str) -> str:
    """'lisinopril 20 mg oral tablet' -> 'lisinopril'."""
    parts = [p for p in norm(text).split(" ") if p and p not in _FORM_WORDS and not _DOSE_TOKEN.match(p)]
    return " ".join(parts)


def strip_salts(text: str) -> str:
    """'losartan potassium' -> 'losartan' (only used when the full name is unknown)."""
    parts = [p for p in text.split(" ") if p not in _SALTS]
    return " ".join(parts)


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", norm(text)))


def contains_term(haystack: str, term: str) -> bool:
    """Word-boundary containment on normalized text."""
    h = f" {norm(haystack)} "
    t = norm(term)
    if not t:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", h) is not None
