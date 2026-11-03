"""openFDA drug label client (FDA Structured Product Labeling, public domain).

Endpoint: https://api.fda.gov/drug/label.json?search=...&limit=1
Fields used: boxed_warning, contraindications, drug_interactions, warnings_and_cautions,
warnings, dosage_and_administration, use_in_specific_populations, effective_time,
set_id, openfda.generic_name, openfda.brand_name.
Without an API key openFDA allows 240 requests/minute and 1,000/day per IP; with
a free key, 120,000/day (https://open.fda.gov/apis/authentication/).
"""
from __future__ import annotations

import re

from ..config import settings
from . import net
from .kb import kb

SOURCE = "FDA drug labels (openFDA)"
SECTIONS = ("boxed_warning", "contraindications", "drug_interactions", "warnings_and_cautions", "warnings",
            "precautions", "dosage_and_administration", "use_in_specific_populations")


def dailymed_url(set_id: str | None, generic: str) -> str:
    if set_id:
        return f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={set_id}"
    from urllib.parse import quote
    return f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query={quote(generic)}"


def _search_expr(generic: str) -> str:
    d = kb().get(generic)
    ingredients = d.ingredients if d else [generic]
    parts = [f'openfda.generic_name:"{i}"' for i in ingredients]
    return " AND ".join(parts)


def label(generic: str, timeout: float = 6.0) -> dict | None:
    """Most relevant current label for a generic, or None if openFDA has none.

    Raises net.SourceUnavailable when openFDA cannot be reached.
    """
    expr = _search_expr(generic)
    base = settings.openfda_base.rstrip("/")
    params_base = {"limit": 1}
    if settings.openfda_api_key:
        params_base["api_key"] = settings.openfda_api_key
    result = None
    for product_filter in (' AND openfda.product_type:"HUMAN PRESCRIPTION DRUG"', ""):
        search = expr + product_filter
        data = net.get_json(f"{base}/drug/label.json", {**params_base, "search": search},
                            cache_key=f"openfda:label:{search}", timeout=timeout, not_found_ok=True)
        results = (data or {}).get("results") or []
        if results:
            result = results[0]
            break
    if not result:
        return None
    sections = {}
    for key in SECTIONS:
        val = result.get(key)
        if isinstance(val, list):
            sections[key] = " ".join(str(v) for v in val)
        elif isinstance(val, str):
            sections[key] = val
    eff = str(result.get("effective_time") or "")
    date = f"{eff[0:4]}-{eff[4:6]}-{eff[6:8]}" if len(eff) >= 8 and eff[:8].isdigit() else None
    set_id = result.get("set_id") or ((result.get("openfda") or {}).get("spl_set_id") or [None])[0]
    return {
        "generic": generic,
        "sections": sections,
        "effective_date": date,
        "set_id": set_id,
        "url": dailymed_url(set_id, generic),
        "brand_names": (result.get("openfda") or {}).get("brand_name") or [],
    }


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def mention_excerpt(section_text: str, names: list[str], max_len: int = 320) -> str | None:
    """The first sentence in a label section that names one of `names` (word match)."""
    if not section_text:
        return None
    pats = [re.compile(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", re.I) for n in names if len(n) >= 4]
    for s in sentences(section_text):
        if any(p.search(s) for p in pats):
            return s if len(s) <= max_len else s[: max_len - 1].rstrip() + "…"
    return None


_RENAL = re.compile(r"\b(renal|kidney|eGFR|creatinine clearance|CrCl|CLcr|dialysis)\b", re.I)


def renal_excerpt(lab: dict, max_chars: int = 600) -> str | None:
    """Label sentences about renal impairment, from dosing and specific-populations sections."""
    out, total = [], 0
    for key in ("dosage_and_administration", "use_in_specific_populations", "contraindications"):
        for s in sentences(lab["sections"].get(key, "")):
            if _RENAL.search(s) and s not in out:
                if total + len(s) > max_chars:
                    return " ".join(out) or (s[: max_chars - 1] + "…")
                out.append(s)
                total += len(s) + 1
    return " ".join(out) or None
