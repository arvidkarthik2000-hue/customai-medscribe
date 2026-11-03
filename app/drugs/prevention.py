"""Adult prevention reminders (USPSTF A/B and the adult immunization schedule).

Computed from age, sex, smoking, BMI, the problem list and medicines. Reminders only: the
clinician confirms in the record what has already been done.
"""
from __future__ import annotations

from .kb import kb
from .normalize import contains_term, norm


def _sex(s) -> str | None:
    s = str(s or "").strip().lower()
    if s in ("f", "female", "woman"):
        return "F"
    if s in ("m", "male", "man"):
        return "M"
    return None


_ICD = {
    "diabetes": (["E08", "E09", "E10", "E11", "E13"], ["diabetes"]),
    "hypertension": (["I10", "I11", "I12", "I13", "I15", "I16"], ["hypertension"]),
    "depression": (["F32", "F33", "F34.1"], ["depression", "depressive"]),
    "anxiety": (["F41"], ["anxiety"]),
    "osteoporosis": (["M80", "M81"], ["osteoporosis"]),
    "ascvd": (["I20", "I21", "I22", "I23", "I24", "I25", "I63", "I65", "I66", "I70", "Z95.1", "Z95.5"], ["coronary artery disease", "myocardial infarction", "stroke"]),
    "dyslipidemia": (["E78"], ["hyperlipidemia", "dyslipidemia", "high cholesterol"]),
}


def _has(problems, key: str) -> bool:
    prefixes, terms = _ICD[key]
    for p in problems or []:
        if isinstance(p, str):
            p = {"name": p}
        code = str(p.get("icd10") or p.get("code") or "").upper().replace(" ", "")
        if code and any(code.startswith(x) for x in prefixes):
            return True
        name = p.get("name") or ""
        if name and any(contains_term(name, t) for t in terms):
            return True
    return False


def _done_ids(done) -> set[str]:
    out = set()
    for d in done or []:
        name = d if isinstance(d, str) else (d.get("id") or d.get("name") or "")
        out.add(norm(name))
    return out


def reminders(age, sex, *, smoking: dict | None = None, bmi: float | None = None,
              problems=None, medicines=None, done=None) -> list[dict]:
    """Eligibility reminders. smoking: {current: bool, pack_years: float, quit_years: float, ever: bool}.
    problems and medicines are optional; requirements that need them are skipped when they are absent."""
    p = kb().prevention
    try:
        age = float(age)
    except (TypeError, ValueError):
        return []
    sx = _sex(sex)
    s = smoking or {}
    meds = {norm(m) for m in (medicines or []) if m}
    k = kb()
    on_statin = any((k.get(m) and "statin" in k.get(m).classes) for m in meds)
    done_ids = _done_ids(done)
    have_problems = problems is not None
    out = []
    for item in p["items"]:
        if age < item["min_age"] or (item["max_age"] is not None and age > item["max_age"]):
            continue
        if item["sex"] and item["sex"] != sx:
            continue
        if item.get("once") and (norm(item["id"]) in done_ids or any(norm(key) in done_ids for key in item.get("keys", []))):
            continue
        req = item.get("requires")
        if req == "smoking":
            ok = (s.get("pack_years") or 0) >= 20 and (s.get("current") or (s.get("quit_years") is not None and s["quit_years"] <= 15))
            if not ok:
                continue
        if req == "ever_smoked" and not s.get("ever") and not s.get("current"):
            continue
        if req == "overweight" and not (bmi is not None and float(bmi) >= 25):
            continue
        if req == "overweight_no_diabetes" and not (bmi is not None and float(bmi) >= 25 and not _has(problems, "diabetes")):
            continue
        if req == "cvd_risk_no_statin":
            if not have_problems or on_statin or _has(problems, "ascvd"):
                continue
            risk_factor = any(_has(problems, x) for x in ("dyslipidemia", "diabetes", "hypertension")) or s.get("current")
            if not risk_factor:
                continue
        if req == "no_hypertension" and (not have_problems or _has(problems, "hypertension")):
            continue
        if req == "no_depression" and (not have_problems or _has(problems, "depression")):
            continue
        if req == "no_anxiety" and (not have_problems or _has(problems, "anxiety")):
            continue
        if req == "no_osteoporosis" and _has(problems, "osteoporosis"):
            continue
        sources = [p["sources"][item["source"]]]
        if item.get("also_source"):
            sources.append(p["sources"][item["also_source"]])
        out.append({"id": item["id"], "name": item["name"], "detail": item["detail"], "source": sources[0],
                    "sources": sources, "keys": item.get("keys", [])})
    return out
