"""The clinical zone's only door to the drug-safety engine (app.drugs.engine).

The engine is imported lazily, so the clinical pipeline works before it exists or
if it fails to import; every fallback is reported, never silent.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import re
from typing import Any

log = logging.getLogger("medscribe.clinical")

# Brands AssemblyAI Medical Mode may add in parentheses after a generic name.
# Used only if the drug engine is unavailable.
_FALLBACK_BRANDS = {
    "humalog", "novolog", "lantus", "levemir", "tresiba", "basaglar", "toujeo", "ozempic", "trulicity",
    "victoza", "jardiance", "farxiga", "januvia", "tradjenta", "glucophage", "glucotrol", "amaryl",
    "advil", "motrin", "aleve", "naprosyn", "tylenol", "celebrex", "ultram", "norco", "percocet",
    "coumadin", "jantoven", "eliquis", "xarelto", "pradaxa", "plavix", "brilinta",
    "lipitor", "zocor", "crestor", "pravachol", "zetia", "lopressor", "toprol", "coreg", "norvasc",
    "zestril", "prinivil", "vasotec", "cozaar", "diovan", "entresto", "aldactone", "lasix", "klor-con",
    "apresoline", "vistaril", "atarax", "benadryl", "ambien", "zoloft", "lexapro", "celexa", "prozac",
    "wellbutrin", "cymbalta", "desyrel", "xanax", "ativan", "klonopin", "neurontin", "lyrica",
    "synthroid", "levoxyl", "keflex", "amoxil", "augmentin", "biaxin", "zithromax", "cipro", "levaquin",
    "bactrim", "flagyl", "diflucan", "prilosec", "nexium", "protonix", "pepcid", "zyrtec", "claritin",
    "singulair", "advair", "symbicort", "ventolin", "proair", "spiriva", "flonase", "imitrex", "fosamax",
}

_FALLBACK_TERMS = {
    "family_medicine": ["lisinopril", "amlodipine", "metformin", "atorvastatin", "levothyroxine", "omeprazole",
                        "sertraline", "losartan", "hydrochlorothiazide", "gabapentin", "amoxicillin", "albuterol"],
    "internal_medicine": ["lisinopril", "amlodipine", "metformin", "atorvastatin", "apixaban", "furosemide",
                          "losartan", "levothyroxine", "pantoprazole", "insulin glargine"],
    "cardiology": ["apixaban", "rivaroxaban", "warfarin", "metoprolol succinate", "carvedilol", "amiodarone",
                   "sacubitril/valsartan", "spironolactone", "atorvastatin", "rosuvastatin", "clopidogrel"],
    "endocrinology": ["metformin", "empagliflozin", "dapagliflozin", "semaglutide", "dulaglutide",
                      "insulin glargine", "insulin lispro", "levothyroxine", "methimazole", "linagliptin"],
    "pulmonology": ["albuterol", "tiotropium", "budesonide/formoterol", "fluticasone/salmeterol",
                    "montelukast", "prednisone", "azithromycin", "roflumilast"],
}

_PAREN = re.compile(r"\s*\(([A-Za-z][A-Za-z0-9\-]*(?:[ \-][A-Za-z0-9]+){0,2})\)")


def engine():
    try:
        return importlib.import_module("app.drugs.engine")
    except Exception as exc:  # missing or broken engine: callers fall back and say so
        log.warning("drug engine unavailable: %s", exc.__class__.__name__)
        return None


def engine_status() -> str:
    return "ok" if engine() is not None else "unavailable"


def _fallback_strip(text: str) -> tuple[str, list[str]]:
    removed: list[str] = []

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if inner.lower() in _FALLBACK_BRANDS:
            removed.append(inner)
            return ""
        return m.group(0)

    return _PAREN.sub(repl, text), removed


def strip_added_brands(text: str) -> tuple[str, list[str]]:
    """'Lispro (Humalog)' -> ('Lispro', ['Humalog']). Never raises."""
    if not text:
        return text or "", []
    eng = engine()
    fn = getattr(eng, "strip_added_brands", None) if eng else None
    if fn:
        try:
            clean, removed = fn(text)
            return clean, list(removed or [])
        except Exception as exc:
            log.warning("strip_added_brands failed in the drug engine: %s", exc)
    return _fallback_strip(text)


def brand_and_generic_terms(specialty: str) -> list[str]:
    eng = engine()
    fn = getattr(eng, "brand_and_generic_terms", None) if eng else None
    if fn:
        try:
            return [str(t) for t in (fn(specialty) or []) if t]
        except Exception as exc:
            log.warning("brand_and_generic_terms failed: %s", exc)
    return list(_FALLBACK_TERMS.get(specialty, _FALLBACK_TERMS["family_medicine"]))


async def check_visit(medications: list[dict], patient: dict, timeout_s: float = 8.0) -> tuple[dict | None, str]:
    """Returns (result, detail). result is None when the engine is unavailable or failed."""
    eng = engine()
    fn = getattr(eng, "check_visit", None) if eng else None
    if fn is None:
        return None, "Drug checks unavailable: the drug-safety engine is not installed on this server."
    try:
        if inspect.iscoroutinefunction(fn):
            result = await asyncio.wait_for(fn(medications, patient, timeout_s=timeout_s), timeout=max(30.0, timeout_s * 6))
        else:
            result = await asyncio.wait_for(
                asyncio.to_thread(fn, medications, patient, timeout_s=timeout_s), timeout=max(30.0, timeout_s * 6)
            )
    except asyncio.TimeoutError:
        return None, "Drug checks timed out; medicines still need to be confirmed by hand."
    except Exception as exc:
        log.exception("drug engine check_visit failed")
        return None, f"Drug checks failed ({exc.__class__.__name__}: {str(exc)[:160]}); medicines still need to be confirmed by hand."
    if not isinstance(result, dict):
        return None, "Drug checks returned an unexpected result; medicines still need to be confirmed by hand."
    return result, "Checked"


def fallback_medication_check(medications: list[dict], detail: str) -> dict[str, Any]:
    """Medication rows the clinician can still confirm when the engine is unavailable."""
    rows = []
    for m in medications:
        row = dict(m)
        row.setdefault("match", {"status": "unmatched", "rxcui": None, "ingredient": m.get("name", ""),
                                 "display": None, "candidates": [], "sound_alike": [], "source": "local"})
        row.setdefault("high_risk", False)
        row.setdefault("high_risk_reason", "")
        row.setdefault("schedule", None)
        row.setdefault("formulary", None)
        row.setdefault("dose_check", {"status": "unknown", "detail": "", "source": ""})
        row["confirmed"] = False
        row["dose_confirmed"] = False
        rows.append(row)
    return {
        "medications": rows,
        "cards": [],
        "sources_checked": [{"name": "Drug-safety engine", "status": "unavailable", "detail": detail}],
        "checked_at": None,
        "unavailable": True,
    }
