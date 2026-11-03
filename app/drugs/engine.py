"""check_visit(): the medication-safety check behind the doctor's review screen and
the pharmacy tools. Works fully offline from the curated KB; RxNorm and FDA labels
add RxCUIs, product names, label excerpts and label-mention interactions when they
can be reached within the time budget.

It never says a regimen is "safe" and never computes a dose.
"""
from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from itertools import combinations, product

from . import calculators, doses, formulary, net, openfda, prevention, rxnav
from .kb import Drug, kb
from .matching import match_row
from .normalize import contains_term, norm

SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
CATEGORY_ORDER = ["allergy", "interaction", "qt", "condition", "renal", "dose", "duplicate", "older_adult",
                  "name_check", "formulary", "calculator", "prevention"]
ACTIVE_ACTIONS = {"start", "continue", "change", ""}
STOP_ACTIONS = {"stop", "hold"}
NEW_ACTIONS = {"start", "change", ""}

OVERRIDES = {
    "allergy": ["Reaction was an intolerance, not an allergy", "Tolerated this drug before", "Benefit outweighs risk", "Other"],
    "interaction": ["Benefit outweighs risk", "Will monitor", "Patient already tolerating", "Other"],
    "qt": ["Benefit outweighs risk", "ECG and electrolytes will be monitored", "Patient already tolerating", "Other"],
    "duplicate": ["Intentional combination", "Switching between them", "Other"],
    "condition": ["Benefit outweighs risk", "Will monitor", "Condition resolved", "Other"],
    "renal": ["Dose already matches the label", "Will monitor kidney function", "Benefit outweighs risk", "Other"],
    "dose": ["Dose is intended", "Will correct the dose", "Other"],
    "older_adult": ["Benefit outweighs risk", "Will review for deprescribing", "Other"],
    "formulary": ["Noted", "Will request prior authorization", "Other"],
    "calculator": ["Noted"],
    "name_check": ["Confirmed both are intended", "Will correct the list"],
    "prevention": ["Done elsewhere", "Patient declined", "Noted"],
}


# ---------------------------------------------------------------- public helpers

_PAREN = re.compile(r"\s*\(([^()]{2,60})\)")


def strip_added_brands(text: str) -> tuple[str, list[str]]:
    """Remove brand names that AssemblyAI Medical Mode adds in brackets after a generic,
    e.g. "Lispro (Humalog)" -> "Lispro". Parentheticals that are not a brand of the
    preceding generic are left alone."""
    if not text or "(" not in text:
        return text, []
    k = kb()
    removed: list[str] = []

    def repl(m: re.Match) -> str:
        inner = m.group(1).strip()
        parts = [p.strip() for p in re.split(r",|/|;", inner) if p.strip()]
        generics = [k.brand_index.get(norm(p)) for p in parts]
        if not parts or any(g is None for g in generics):
            return m.group(0)
        before = text[max(0, m.start() - 60): m.start()].lower()
        for g in generics:
            d = k.get(g)
            tokens = {t for ing in (d.ingredients if d else [g]) for t in re.split(r"[\s\-/]+", ing) if len(t) >= 4}
            tokens |= {norm(a) for a in (d.aliases if d else []) if len(a) >= 4}
            if not any(t in before for t in tokens):
                return m.group(0)
        removed.extend(parts)
        return ""

    clean = _PAREN.sub(repl, text)
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean, removed


def brand_and_generic_terms(specialty: str) -> list[str]:
    """Drug names to boost in speech recognition for a specialty: generics first, then brands."""
    k = kb()
    spec = specialty if any(specialty in d.specialties for d in k.drugs.values()) else "family_medicine"
    chosen = [d for d in k.drugs.values() if spec in d.specialties]
    # high-risk and commonly confused names first, so a 100-term live limit keeps them
    chosen.sort(key=lambda d: (d.high_risk is None, not d.sound_alike, d.otc, d.generic))
    generics: list[str] = []
    for d in chosen:
        for ing in d.ingredients:
            name = ing if ing in k.drugs or len(d.ingredients) > 1 else d.generic
            if name not in generics:
                generics.append(name)
    brands: list[str] = []
    for d in chosen:
        for b in d.brands:
            if b not in brands:
                brands.append(b)
    out, seen = [], set()
    for term in generics + brands:
        t = term[:1].upper() + term[1:] if term.islower() else term
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


# ---------------------------------------------------------------- internals


@dataclass
class Med:
    key: str
    row: dict
    origin: str  # visit | chart
    action: str
    generic: str | None
    drug: Drug | None
    match: dict | None = None
    active: bool = True
    new: bool = False
    represented: bool = False

    @property
    def name(self) -> str:
        return disp(self.generic) if self.generic else (self.row.get("name") or "unknown")


@dataclass
class Finding:
    category: str
    indicator: str
    summary: str
    detail: str
    meds: list[str]
    sources: list[dict]
    rule_id: str
    effect: str = ""
    management: str = ""
    context: str = "new"
    group: str | None = None
    onc: bool = False
    graded: bool = True
    also: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def disp(generic: str | None) -> str:
    if not generic:
        return ""
    d = kb().get(generic)
    if d and len(d.ingredients) > 1:
        return generic.replace("-", "/")
    return generic


def _age(patient: dict) -> float | None:
    a = patient.get("age")
    if a not in (None, ""):
        try:
            return float(a)
        except (TypeError, ValueError):
            pass
    dob = patient.get("dob")
    if dob:
        try:
            d = date.fromisoformat(str(dob)[:10])
            t = date.today()
            return t.year - d.year - ((t.month, t.day) < (d.month, d.day))
        except ValueError:
            return None
    return None


def _items(lst, key: str) -> list[dict]:
    out = []
    for x in lst or []:
        if isinstance(x, str):
            out.append({key: x})
        elif isinstance(x, dict):
            out.append(x)
    return out


def _egfr(patient: dict, age: float | None) -> tuple[float | None, str]:
    e = patient.get("egfr")
    if e not in (None, ""):
        try:
            return float(e), "from the chart"
        except (TypeError, ValueError):
            pass
    for lab in _items(patient.get("labs"), "name"):
        name = str(lab.get("name", "")).lower()
        if "creatinine" in name and "clearance" not in name and "urine" not in name and "ratio" not in name:
            try:
                value = float(str(lab.get("value")).replace(",", ""))
            except (TypeError, ValueError):
                continue
            unit = str(lab.get("unit") or "mg/dL").lower()
            if "mol" in unit:
                value = value / 88.4
            if age is None or not patient.get("sex"):
                return None, ""
            try:
                egfr = calculators.ckd_epi_2021(value, age, patient.get("sex"))
            except ValueError:
                return None, ""
            when = f" on {lab['date']}" if lab.get("date") else ""
            return egfr, f"CKD-EPI 2021 from creatinine {value:g} mg/dL{when}"
    return None, ""


def _lab_latest(patient: dict, include: tuple[str, ...], exclude: tuple[str, ...] = ("urine", "ratio")) -> tuple[float | None, str]:
    """Latest numeric lab whose name contains one of `include` (by date, else smallest days_ago, else first)."""
    best = None
    for i, lab in enumerate(_items(patient.get("labs"), "name")):
        name = str(lab.get("name", "")).lower()
        if not any(t in name for t in include) or any(x in name for x in exclude):
            continue
        try:
            value = float(str(lab.get("value")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if lab.get("date"):
            key = (0, str(lab["date"]))
        elif lab.get("days_ago") is not None:
            key = (0, f"{-float(lab['days_ago']):020.3f}")
        else:
            key = (1, f"{-i:06d}")
        if best is None or key > best[0]:
            best = (key, value, str(lab.get("unit") or ""))
    return (best[1], best[2]) if best else (None, "")


def _creatinine(patient: dict) -> float | None:
    value, unit = _lab_latest(patient, ("creatinine",), ("clearance", "urine", "ratio"))
    if value is None:
        return None
    return value / 88.4 if "mol" in unit.lower() else value


def _weight(patient: dict) -> float | None:
    for src in (patient.get("vitals") or {}, patient):
        for key in ("weight_kg", "weight"):
            if src.get(key) not in (None, ""):
                try:
                    return float(src[key])
                except (TypeError, ValueError):
                    pass
    return None


def _crcl(age: float | None, weight: float | None, scr: float | None, sex) -> tuple[float | None, str]:
    """Cockcroft-Gault creatinine clearance with actual body weight (the basis DOAC and many renal labels use)."""
    if age is None or weight is None or not scr or scr <= 0:
        return None, ""
    value = (140 - age) * weight / (72 * scr)
    if str(sex or "").upper().startswith("F"):
        value *= 0.85
    return value, f"Cockcroft-Gault with actual body weight {weight:g} kg, creatinine {scr:g} mg/dL, age {age:.0f}"


def _smoking(patient: dict) -> dict:
    text = str(((patient.get("social") or {}).get("tobacco")) or patient.get("smoking") or "").lower()
    out: dict = {}
    if not text:
        return out
    if "never" in text:
        return {"ever": False, "current": False}
    out["ever"] = True
    out["current"] = bool(re.search(r"\b(current|smokes|smoker)\b", text)) and "former" not in text and "quit" not in text
    m = re.search(r"quit\s*(?:in\s*)?(\d{4})", text)
    if m:
        out["quit_years"] = date.today().year - int(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*pack[- ]years?", text)
    if m:
        out["pack_years"] = float(m.group(1))
    return out


_ALLERGIC_WORDS = ("hive", "rash", "anaphyla", "swell", "angioedema", "itch", "wheez", "short of breath", "shortness of breath",
                   "stevens", "sjs", "blister", "throat", "lips", "tongue", "urticaria", "bronchospasm", "peeling")
_INTOLERANCE_WORDS = ("intoleran", "nausea", "vomit", "upset stomach", "gi upset", "stomach upset", "constipat", "drowsy",
                      "drowsiness", "dizz", "headache", "jittery", "diarrhea", "side effect")


def _is_intolerance(reaction: str) -> bool:
    r = (reaction or "").lower()
    if not r or any(w in r for w in _ALLERGIC_WORDS):
        return False
    return any(w in r for w in _INTOLERANCE_WORDS)


def _icd_norm(code: str) -> str:
    return str(code or "").upper().replace(".", "").replace(" ", "")


def _problem_hit(problems: list[dict], icd_prefixes: list[str], terms: list[str]) -> str | None:
    prefixes = [_icd_norm(p) for p in icd_prefixes]
    for p in problems:
        code = _icd_norm(p.get("icd10") or p.get("code") or "")
        if code and any(code.startswith(px) for px in prefixes):
            return p.get("name") or p.get("icd10")
        name = p.get("name") or ""
        if name and any(contains_term(name, t) for t in terms):
            return name
    return None


def _card_id(category: str, rule_id: str, meds: list[str]) -> str:
    raw = f"{category}|{rule_id}|{'|'.join(sorted(meds))}"
    return "c_" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def _source_obj(src: dict | None, reviewed: str) -> dict | None:
    """date is the label's effective date when the text came from a live label,
    otherwise the date the curated rule was reviewed (date_type says which)."""
    if not src:
        return None
    if src.get("date"):
        return {"label": src.get("label"), "url": src.get("url"), "date": src["date"], "date_type": "label_effective"}
    return {"label": src.get("label"), "url": src.get("url"), "date": reviewed, "date_type": "reviewed"}


# Sign gate: every critical alert, and every warning this visit creates in these categories,
# needs an acknowledgement before the note can be signed.
ACK_WARNING_CATEGORIES = frozenset({"interaction", "qt", "allergy", "condition", "renal", "dose"})


def requires_ack(card: dict) -> bool:
    indicator = card.get("indicator")
    if indicator == "critical":
        return True
    return indicator == "warning" and card.get("context") == "new" and card.get("category") in ACK_WARNING_CATEGORIES


def _to_card(f: Finding, reviewed: str, grading: str) -> dict:
    detail = f.detail
    if f.also:
        detail += " Also: " + "; ".join(f.also) + "."
    src = f.sources[0] if f.sources else None
    card = {
        "id": _card_id(f.category, f.group or f.rule_id, f.meds),
        "indicator": f.indicator,
        "category": f.category,
        "summary": f.summary,
        "detail": detail,
        "meds": f.meds,
        "source": _source_obj(src, reviewed),
        "sources": [_source_obj(s, reviewed) for s in f.sources if s],
        "override_reasons": OVERRIDES.get(f.category, ["Noted", "Other"]),
        "effect": f.effect,
        "management": f.management,
        "context": f.context,
        "onc_high_priority": f.onc,
        "grading": grading if f.graded else None,
    }
    card.update(f.extra)
    card["requires_ack"] = requires_ack(card)
    return card


# ---------------------------------------------------------------- the check


def check_visit(medications: list[dict], patient: dict, *, timeout_s: float = 8.0, use_network: bool = True,
                include_prevention_reminders: bool = True) -> dict:
    k = kb()
    started = time.monotonic()
    deadline = started + max(1.0, float(timeout_s))
    tracker = net.SourceTracker()
    patient = patient or {}
    rows = [dict(r) for r in (medications or []) if isinstance(r, dict)]
    age = _age(patient)
    sex = patient.get("sex")
    problems = _items(patient.get("problems"), "name")
    allergies = _items(patient.get("allergies"), "substance")
    chart_rows = _items(patient.get("medications"), "name")
    insurance = patient.get("insurance") or {}
    plan_key = insurance.get("plan_key") or patient.get("plan_key")
    pregnant = bool(patient.get("pregnant"))
    egfr, egfr_basis = _egfr(patient, age)
    weight = _weight(patient)
    scr = _creatinine(patient)
    crcl, crcl_basis = _crcl(age, weight, scr, sex)
    potassium, _k_unit = _lab_latest(patient, ("potassium",))
    sodium, _na_unit = _lab_latest(patient, ("sodium",))
    af = _problem_hit(problems, ["I48"], ["atrial fibrillation", "afib", "a-fib", "atrial flutter"])
    problems_text = " ; ".join(p.get("name") or "" for p in problems)

    chart_generics = {g for g in (k.lookup(r.get("name")) for r in chart_rows) if g}

    # 1. match visit rows (network-assisted, in parallel)
    for i, r in enumerate(rows):
        r.setdefault("id", f"m{i + 1}")

    def _match(r):
        return match_row(r, chart_generics=chart_generics, problems_text=problems_text, tracker=tracker,
                         deadline=deadline, use_network=use_network)

    if rows:
        with ThreadPoolExecutor(max_workers=min(6, len(rows))) as pool:
            matches = list(pool.map(_match, rows))
    else:
        matches = []

    meds: list[Med] = []
    for r, m in zip(rows, matches):
        generic = m.get("ingredient") if m.get("ingredient") in k.drugs else None
        action = str(r.get("action") or "").strip().lower()
        med = Med(key=r["id"], row=r, origin="visit", action=action, generic=generic, drug=k.get(generic), match=m)
        med.active = action in ACTIVE_ACTIONS
        # New for this visit: started or changed, or recorded in the visit but not on the chart's list
        # (for example an over-the-counter NSAID the patient began on their own).
        med.new = action in NEW_ACTIONS or (med.active and generic is not None and generic not in chart_generics)
        meds.append(med)

    visit_by_generic: dict[str, Med] = {}
    for med in meds:
        if med.generic:
            visit_by_generic.setdefault(med.generic, med)
    for i, r in enumerate(chart_rows):
        generic = k.lookup(r.get("name"))
        med = Med(key=f"chart{i + 1}", row=r, origin="chart", action="current", generic=generic, drug=k.get(generic))
        vm = visit_by_generic.get(generic) if generic else None
        if vm is not None:
            med.represented = True
            # the visit row speaks for it when active, or stops it; if the visit only
            # discussed it, the chart's current medicine stays active
            med.active = not (vm.active or vm.action in STOP_ACTIONS)
        meds.append(med)

    active = [m for m in meds if m.active and m.drug is not None]
    visit_active = [m for m in active if m.origin == "visit"]

    findings: list[Finding] = []
    covered_pairs: set[frozenset] = set()

    def relevant(group_meds: list[Med], severity: str) -> str | None:
        if any(m.new for m in group_meds):
            return "new"
        if any(m.origin == "visit" for m in group_meds) and severity in ("critical", "warning"):
            return "existing"
        return None

    # 2. interaction rules (pairs and triples)
    rule_findings: list[Finding] = []
    for rule in k.interactions["rules"]:
        lists = [[m for m in active if k.matches(sel, m.drug)] for sel in rule["all"]]
        if any(not lst for lst in lists):
            continue
        seen_sets: set[frozenset] = set()
        for combo in product(*lists):
            generics = [m.generic for m in combo]
            if len(set(generics)) != len(generics):
                continue
            key = frozenset(generics)
            if key in seen_sets:
                continue
            seen_sets.add(key)
            severity = rule["severity"]
            ctx = relevant(list(combo), severity)
            if not ctx:
                continue
            note = ""
            if rule.get("dose_limit"):
                lim = rule["dose_limit"]
                limited = next((m for m in combo if m.generic == lim["drug"]), None)
                if limited is not None:
                    dm = doses.daily_mg(limited.row.get("dose"), limited.row.get("unit"), limited.row.get("frequency"))
                    if dm is not None and dm <= lim["max_mg_per_day"] + 1e-9:
                        severity = "info"
                        note = f" The recorded {lim['drug']} dose ({dm:g} mg/day) is within that limit."
            names = [m.name for m in combo]
            rule_findings.append(Finding(
                category=rule.get("category") or "interaction", indicator=severity,
                summary=f"{rule['title']}: {' + '.join(names)}",
                detail=f"{rule['effect']} What to do: {rule['management']}{note}",
                meds=[m.generic for m in combo], sources=rule["sources"], rule_id=rule["id"],
                effect=rule["effect"], management=rule["management"] + note, context=ctx,
                group=rule.get("group"), onc=bool(rule.get("onc")),
            ))

    # 2a. merge grouped findings (one card per effect, e.g. hyperkalemia)
    grouped: dict[str, list[Finding]] = {}
    singles: list[Finding] = []
    for f in rule_findings:
        if f.group:
            grouped.setdefault(f.group, []).append(f)
        else:
            singles.append(f)
    for group, fs in grouped.items():
        fs.sort(key=lambda f: (SEVERITY_RANK[f.indicator], -len(f.meds)))
        top = fs[0]
        meds_union: list[str] = []
        for f in fs:
            for g in f.meds:
                if g not in meds_union:
                    meds_union.append(g)
            covered_pairs |= {frozenset(p) for p in combinations(f.meds, 2)}
        merged = Finding(category=top.category, indicator=top.indicator, summary=top.summary, detail=top.detail,
                         meds=meds_union, sources=list(top.sources), rule_id=top.rule_id, effect=top.effect,
                         management=top.management, context="new" if any(f.context == "new" for f in fs) else "existing",
                         group=group, onc=any(f.onc for f in fs))
        if group == "hyperkalemia" and potassium is not None and potassium > 5.0:
            merged.indicator = "critical"
            merged.detail = f"Latest potassium {potassium:g} mmol/L, above 5.0. {merged.detail}"
            merged.extra["potassium"] = potassium
        for f in fs[1:]:
            line = f.summary
            if line != top.summary and line not in merged.also:
                merged.also.append(line)
        findings.append(merged)

    # 2b. pair dedupe for ungrouped findings: keep the most severe per set of medicines
    by_set: dict[frozenset, list[Finding]] = {}
    for f in singles:
        by_set.setdefault(frozenset(f.meds), []).append(f)
    for key, fs in by_set.items():
        fs.sort(key=lambda f: (SEVERITY_RANK[f.indicator], not f.onc))
        top = fs[0]
        for f in fs[1:]:
            if f.summary not in top.also:
                top.also.append(f.summary)
        findings.append(top)
        covered_pairs |= {frozenset(p) for p in combinations(key, 2)}

    # 2c. look-alike/sound-alike pairs that are both on the active list
    lasa_done: set[frozenset] = set()
    for m in active:
        for sa in m.drug.sound_alike:
            partner = next((o for o in active if o.generic == sa["drug"]), None)
            if partner is None or not (m.origin == "visit" or partner.origin == "visit"):
                continue
            pair = frozenset((m.generic, partner.generic))
            if pair in lasa_done:
                continue
            lasa_done.add(pair)
            note = f"Look-alike/sound-alike names on one list ({sa['pair']}): confirm the purpose of each."
            for x in (m, partner):
                if x.match is not None and x.match.get("status") == "matched" and not x.match.get("reason"):
                    x.match["reason"] = note
            findings.append(Finding(category="name_check", indicator="info",
                                    summary=f"Look-alike, sound-alike names on this list: {sa['pair']}",
                                    detail=f"Both {m.name} and {partner.name} are listed. ISMP lists these names as commonly confused. What to do: confirm each medicine's purpose and keep tall-man lettering on labels.",
                                    meds=[m.generic, partner.generic],
                                    sources=[{"label": "ISMP List of Confused Drug Names", "url": "https://www.ismp.org/recommendations/confused-drug-names-list"}],
                                    rule_id="lasa", graded=False, effect="Commonly confused names.",
                                    management="Confirm each medicine's purpose."))

    # 3. allergies
    allergy_groups = k.allergies["groups"]
    allergy_index = []
    for allergy in allergies:
        substance = str(allergy.get("substance") or "").strip()
        if not substance:
            continue
        reaction = str(allergy.get("reaction") or "").strip()
        a_generic = k.lookup(substance)
        a_drug = k.get(a_generic)
        groups = []
        for g in allergy_groups:
            if any(contains_term(substance, t) for t in g["terms"]):
                groups.append(g)
            elif a_drug and (a_drug.classes.intersection(g.get("member_classes", [])) or a_drug.generic in g.get("member_drugs", [])):
                groups.append(g)
        allergy_index.append((substance, reaction, a_generic, groups))
    for substance, reaction, a_generic, groups in allergy_index:
        label = substance + (f" ({reaction})" if reaction else "")
        for m in visit_active:
            hit = None
            if a_generic and (m.generic == a_generic or a_generic in (m.drug.ingredients if m.drug else [])):
                hit = ("critical", f"Allergy to {label}: {m.name} is the same drug", "Listed allergy to this medicine.", None, None, None)
            else:
                for g in groups:
                    in_group = bool(m.drug.classes.intersection(g.get("member_classes", []))) or m.generic in g.get("member_drugs", [])
                    if in_group:
                        sev = g.get("severity", "critical")
                        if g.get("same_drug_severity") and a_generic == m.generic:
                            sev = g["same_drug_severity"]
                        why = f"{m.name} belongs to the {g['label'].lower()}."
                        if g.get("note"):
                            why += " " + g["note"]
                        hit = (sev, f"Allergy to {label}: {m.name} is in the same class", why, g["source"], None, None)
                        break
                    for cross in g.get("cross", []):
                        if k.matches(cross, m.drug):
                            hit = (cross["severity"], f"Allergy to {label}: possible cross-reactivity with {m.name}", cross["note"], g["source"],
                                   cross.get("management"), cross.get("sources"))
                            break
                    if hit:
                        break
            if hit:
                sev, summary, why, src, advice, cross_sources = hit
                if _is_intolerance(reaction):
                    sev = {"critical": "warning", "warning": "info"}.get(sev, sev)
                    summary = summary.replace("Allergy to", "Intolerance to", 1)
                    why += " The recorded reaction sounds like an intolerance rather than an allergy."
                advice = advice or "Confirm the reaction history before prescribing; choose an alternative if the reaction was allergic."
                srcs = list(cross_sources or []) + ([src] if src else [])
                findings.append(Finding(category="allergy", indicator=sev, summary=summary,
                                        detail=f"{why} What to do: {advice}",
                                        meds=[m.generic], sources=srcs, rule_id=f"allergy:{norm(substance)}",
                                        effect=why, management=advice,
                                        context="new" if m.new else "existing", extra={"allergy": {"substance": substance, "reaction": reaction}}))

    # 4. conditions (problem list, pregnancy, eGFR-triggered)
    condition_hits = []
    for rule in k.conditions["rules"]:
        hit = _problem_hit(problems, rule.get("icd10", []), rule.get("terms", []))
        if not hit and rule.get("pregnancy") and pregnant:
            hit = "pregnancy"
        if not hit and rule.get("egfr_below") and egfr is not None and egfr < rule["egfr_below"]:
            hit = f"eGFR {egfr:.0f}"
        if hit:
            condition_hits.append((rule, hit))
    for rule, hit in condition_hits:
        for m in visit_active:
            if not k.matches(rule["drugs"], m.drug):
                continue
            findings.append(Finding(category="condition", indicator=rule["severity"], summary=f"{rule['summary']}: {m.name}",
                                    detail=f"Chart: {hit}. {rule['detail']}", meds=[m.generic], sources=[rule["source"]],
                                    rule_id=rule["id"], effect=rule["detail"], management="Review whether an alternative fits better.",
                                    context="new" if m.new else "existing", extra={"condition": hit}))

    # 4b. tramadol: hyponatremia warning (tramadol label), from sex/age and the sodium lab
    for m in visit_active:
        if m.generic != "tramadol" and "tramadol" not in (m.drug.ingredients if m.drug else []):
            continue
        reasons = []
        if sodium is not None and sodium < 135:
            reasons.append(f"sodium {sodium:g} mmol/L")
        if str(sex or "").upper().startswith("F") and age is not None and age > 65:
            reasons.append(f"woman aged {age:.0f}")
        if not reasons:
            continue
        detail = ("Tramadol label: hyponatremia (sodium below 135 mmol/L, many cases below 120) has been reported, most often in women over 65 "
                  "and within the first week, sometimes from SIADH. Assess for signs such as confusion or disorientation, especially when starting; "
                  "if they appear, treat (for example fluid restriction) and stop tramadol, following the label's guidance on safe discontinuation.")
        srcs = [{"label": "FDA label: tramadol, Warnings and Precautions: hyponatremia", "url": "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query=tramadol"}]
        if any(o.drug.classes & {"ssri", "snri"} for o in active if o is not m):
            detail += " SSRI and SNRI labels also warn of hyponatremia (SIADH), with older adults at greater risk."
            srcs.append({"label": "FDA label: sertraline, Warnings and Precautions: hyponatremia", "url": "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query=sertraline"})
        findings.append(Finding(category="condition", indicator="warning", summary=f"Tramadol and hyponatremia risk ({', '.join(reasons)})",
                                detail=detail, meds=[m.generic], sources=srcs, rule_id="tramadol-hyponatremia",
                                effect="Hyponatremia, sometimes severe.", management="Assess for confusion or disorientation, especially when starting.",
                                context="new" if m.new else "existing", extra={"sodium": sodium}))

    # 5. older adults
    if age is not None and age >= k.older["min_age"]:
        for rule in k.older["rules"]:
            for m in visit_active:
                if k.matches(rule["drugs"], m.drug):
                    findings.append(Finding(category="older_adult", indicator=rule["severity"],
                                            summary=f"{rule['summary']} ({age:.0f}): {m.name}", detail=rule["detail"],
                                            meds=[m.generic], sources=[k.older["source"]], rule_id=rule["id"],
                                            effect=rule["detail"], management="Consider deprescribing or a safer alternative.",
                                            context="new" if m.new else "existing"))

    # 6. renal (label text only)
    def renal_value(entry: dict) -> tuple[float | None, str]:
        if entry.get("basis") == "crcl" and crcl is not None:
            return crcl, "crcl"
        return egfr, "egfr"

    renal_needed: list[tuple[Med, dict, float, str]] = []
    if egfr is not None or crcl is not None:
        for m in visit_active:
            applicable = []
            for e in k.renal["drugs"].get(m.generic) or []:
                value, basis = renal_value(e)
                if value is not None and value < e["below"]:
                    applicable.append((e, value, basis))
            if applicable:
                e, value, basis = min(applicable, key=lambda x: x[0]["below"])
                renal_needed.append((m, e, value, basis))

    # 6b. DOAC label dose criteria in atrial fibrillation (criteria shown next to the patient's values; no dose is chosen)
    doac_af = k.renal.get("doac_af") or {}
    for m in visit_active:
        entry = doac_af.get(m.generic)
        if not entry or not af:
            continue
        recorded_mg = doses.dose_mg(m.row.get("dose"), m.row.get("unit"))
        recorded = " ".join(str(x) for x in (m.row.get("dose"), m.row.get("unit"), m.row.get("frequency")) if x) or "not stated"
        src = [{"label": f"FDA label: {m.generic}, Dosage and Administration", "url": "https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query=" + m.generic}]
        if "criteria" in entry:
            c = entry["criteria"]
            met = []
            if age is not None and age >= c["age_min"]:
                met.append(f"age {age:.0f} (80 or older)")
            if weight is not None and weight <= c["weight_max_kg"]:
                met.append(f"weight {weight:g} kg (60 kg or less)")
            if scr is not None and scr >= c["creatinine_min"]:
                met.append(f"creatinine {scr:g} mg/dL (1.5 or more)")
            unknown = [n for n, v in (("weight", weight), ("creatinine", scr), ("age", age)) if v is None]
            met_txt = ", ".join(met) if met else "none"
            if len(met) >= c["needed"] and (recorded_mg is None or recorded_mg > 2.5 + 1e-9):
                findings.append(Finding(category="dose", indicator="warning", summary=f"Apixaban: {len(met)} of 3 reduced-dose criteria met",
                                        detail=f"Criteria met: {met_txt}. Recorded: {recorded}. {entry['text']} The app does not choose a dose.",
                                        meds=[m.generic], sources=src, rule_id="doac:apixaban", effect=entry["text"],
                                        management="Compare the recorded dose with the label's criteria.", context="new" if m.new else "existing",
                                        extra={"criteria_met": met}))
            elif len(met) < c["needed"] and recorded_mg is not None and recorded_mg <= 2.5 + 1e-9 and not unknown:
                findings.append(Finding(category="dose", indicator="info", summary=f"Apixaban 2.5 mg with {len(met)} of 3 reduced-dose criteria",
                                        detail=f"Criteria met: {met_txt}. Recorded: {recorded}. {entry['text']} The app does not choose a dose.",
                                        meds=[m.generic], sources=src, rule_id="doac:apixaban", effect=entry["text"],
                                        management="Compare the recorded dose with the label's criteria.", context="new" if m.new else "existing",
                                        extra={"criteria_met": met}))
            continue
        if crcl is None:
            findings.append(Finding(category="dose", indicator="info", summary=f"{m.name}: creatinine clearance needed for the label dose",
                                    detail=f"{entry['text']} Weight or creatinine is missing, so creatinine clearance could not be calculated.",
                                    meds=[m.generic], sources=src, rule_id=f"doac:{m.generic}", graded=False))
            continue
        for rule in entry["crcl_rules"]:
            hit = ("max" in rule and crcl <= rule["max"]) or ("min" in rule and crcl > rule["min"])
            if hit:
                findings.append(Finding(category="dose", indicator=rule["severity"], summary=rule["summary"],
                                        detail=f"Creatinine clearance about {crcl:.0f} mL/min ({crcl_basis}). Recorded: {recorded}. {entry['text']} The app does not choose a dose.",
                                        meds=[m.generic], sources=src, rule_id=f"doac:{m.generic}", effect=entry["text"],
                                        management="Compare the recorded dose with the label's creatinine-clearance bands.",
                                        context="new" if m.new else "existing", extra={"crcl": round(crcl)}))
                break

    # 7. doses
    dose_checks: dict[str, dict] = {}
    for m in meds:
        if m.origin != "visit":
            continue
        dc, card = doses.check(m.generic, m.row.get("dose"), m.row.get("unit"), m.row.get("frequency"),
                               age=age, co_generics={o.generic for o in active if o is not m})
        dose_checks[m.key] = dc
        if card and m.active:
            findings.append(Finding(category="dose", indicator=card["indicator"], summary=card["summary"], detail=card["detail"],
                                    meds=[m.generic], sources=[card["source"]], rule_id=f"dose:{m.generic}",
                                    effect=card["detail"], management="Confirm the intended dose and schedule.",
                                    context="new" if m.new else "existing"))

    # 8. duplicates (same ingredient across products; same class)
    for a, b in combinations(active, 2):
        if a.generic == b.generic or not (a.new or b.new):
            continue
        pair = frozenset((a.generic, b.generic))
        shared_ing = set(a.drug.ingredients).intersection(b.drug.ingredients)
        if shared_ing:
            ing = sorted(shared_ing)[0]
            extra = " Keep the total acetaminophen under the label maximum (4,000 mg a day; many labels advise 3,000 mg)." if ing == "acetaminophen" else ""
            findings.append(Finding(category="duplicate", indicator="warning", summary=f"Duplicate ingredient ({ing}): {a.name} + {b.name}",
                                    detail=f"Both products contain {ing}.{extra} What to do: confirm both are intended.",
                                    meds=[a.generic, b.generic], sources=[], rule_id=f"dup-ing:{ing}", graded=False,
                                    context="new"))
            covered_pairs.add(pair)
            continue
        if pair in covered_pairs:
            continue
        common = [c for c in sorted(set(a.drug.own_classes) & set(b.drug.own_classes)) if (k.classes.get(c) or {}).get("duplicate")]
        if common:
            c = common[0]
            sev = k.classes[c]["duplicate"]
            label = k.class_label(c)
            findings.append(Finding(category="duplicate", indicator=sev, summary=f"Two {label.lower()}s: {a.name} + {b.name}",
                                    detail=f"Same therapeutic class ({label}). What to do: confirm whether one replaces the other.",
                                    meds=[a.generic, b.generic], sources=[], rule_id=f"dup-class:{c}", graded=False,
                                    effect="Same therapeutic class.", management="Confirm whether one replaces the other."))
            covered_pairs.add(pair)

    # 9. formulary per visit row
    def alternative_concerns(alt_generic: str, replaced: str) -> list[str]:
        """Patient-specific reasons not to suggest a formulary alternative."""
        d = k.get(alt_generic)
        if d is None:
            return ["unknown"]
        why = []
        if age is not None and age >= k.older["min_age"] and any(k.matches(r["drugs"], d) for r in k.older["rules"] if r["severity"] != "info"):
            why.append("older-adult flag")
        for _sub, _rx, a_gen, groups in allergy_index:
            if a_gen and (a_gen == d.generic or a_gen in d.ingredients):
                why.append("allergy")
            for g in groups:
                if d.classes.intersection(g.get("member_classes", [])) or d.generic in g.get("member_drugs", []):
                    why.append("allergy")
                for cross in g.get("cross", []):
                    if cross.get("severity") != "info" and k.matches(cross, d):
                        why.append("allergy cross-reactivity")
        for rule, _hit in condition_hits:
            if rule["severity"] != "info" and k.matches(rule["drugs"], d):
                why.append("condition")
        for e in k.renal["drugs"].get(d.generic) or []:
            value, _basis = renal_value(e)
            if value is not None and value < e["below"] and e["severity"] == "critical":
                why.append("kidney function")
        others = [m for m in active if m.generic != replaced]
        for rule in k.interactions["rules"]:
            if rule["severity"] == "info" or len(rule["all"]) != 2:
                continue
            s1, s2 = rule["all"]
            for o in others:
                if (k.matches(s1, d) and k.matches(s2, o.drug)) or (k.matches(s2, d) and k.matches(s1, o.drug)):
                    why.append("interaction")
                    break
        return why

    formulary_rows: dict[str, dict | None] = {}
    for m in meds:
        if m.origin != "visit":
            continue
        st = formulary.status(plan_key, m.generic) if plan_key else None
        formulary_rows[m.key] = st
        if st and m.new and m.active and m.generic and formulary.restricted(st):
            if st.get("on_formulary") is False and "Over-the-counter" in (st.get("note") or ""):
                continue  # OTC products are bought over the counter; the row still shows "not covered"
            alts = [a for a in formulary.alternatives(plan_key, m.generic, limit=8)
                    if not alternative_concerns(a["drug"], m.generic)][:3]
            if st.get("on_formulary") is False:
                summary = f"{m.name}: not covered on {st['plan_name']}"
            elif st.get("prior_auth"):
                summary = f"{m.name}: prior authorization required ({st['plan_name']})"
            elif st.get("step_therapy"):
                summary = f"{m.name}: step therapy applies ({st['plan_name']})"
            else:
                summary = f"{m.name}: {st.get('tier_label', 'higher tier').lower()} on {st['plan_name']}"
            alt_txt = ", ".join(f"{disp(x['drug'])} (tier {x['tier']}{', PA' if x['prior_auth'] else ''}{', ST' if x['step_therapy'] else ''})" for x in alts)
            detail = (st.get("note") or "") + (f" Covered options in the same class on this plan, screened against this patient's allergies, conditions, kidney function, age and other medicines: {alt_txt}." if alt_txt else "")
            findings.append(Finding(category="formulary", indicator="info", summary=summary, detail=detail.strip() + " " + formulary.RANKING,
                                    meds=[m.generic], sources=[{"label": formulary.LABEL, "url": k.formulary["layout_url"]}],
                                    rule_id=f"formulary:{plan_key}", graded=False, extra={"alternatives": alts}))

    # 10. calculator: CHA2DS2-VASc when atrial fibrillation is on the problem list and an antithrombotic is in play
    antithrombotics = [m for m in active if m.drug.classes & {"anticoagulant", "antiplatelet"}]
    if af and antithrombotics and any(m.origin == "visit" for m in active):
        comp = calculators.cha2ds2_vasc(
            chf=bool(_problem_hit(problems, ["I50"], ["heart failure"])),
            hypertension=bool(_problem_hit(problems, ["I10", "I11", "I12", "I13", "I15", "I16"], ["hypertension"])),
            age=age or 0,
            diabetes=bool(_problem_hit(problems, ["E10", "E11", "E13"], ["diabetes"])),
            stroke_tia=bool(_problem_hit(problems, ["I63", "I64", "G45", "I69", "Z86.73"], ["stroke", "tia", "transient ischemic attack"])),
            vascular=bool(_problem_hit(problems, ["I21", "I22", "I25.2", "I70", "I73.9"], ["myocardial infarction", "peripheral artery disease", "peripheral arterial disease"])),
            sex=sex or "M")
        parts = ", ".join(f"{c['item']} {c['points']}" for c in comp["components"]) or "no risk factors found in the chart"
        findings.append(Finding(category="calculator", indicator="info", summary=f"CHA2DS2-VASc {comp['value']} (from the chart)",
                                detail=f"{parts}. Computed from the problem list, age and sex; confirm the inputs.",
                                meds=[m.generic for m in antithrombotics],
                                sources=[calculators.CITATIONS["cha2ds2_vasc"]], rule_id="calc:cha2ds2vasc", graded=False,
                                extra={"value": comp["value"], "components": comp["components"]}))

    # 11. prevention: the chart's care gaps, then reminders from age, sex, smoking, BMI, problems and medicines
    chart_gaps = _items(patient.get("care_gaps"), "name")
    for gap in chart_gaps:
        name = gap.get("name")
        if not name:
            continue
        src_text = gap.get("source")
        src = {"label": src_text, "url": None} if isinstance(src_text, str) else (src_text if isinstance(src_text, dict) else None)
        findings.append(Finding(category="prevention", indicator="info", summary=name, detail=gap.get("detail") or "",
                                meds=[], sources=[src] if src else [], rule_id=f"gap:{norm(name)}", graded=False))
    if include_prevention_reminders and age is not None:
        gap_text = " ".join(f"{g.get('name', '')} {g.get('detail') or ''}".lower() for g in chart_gaps)
        vitals = patient.get("vitals") or {}
        rem = prevention.reminders(age, sex, smoking=_smoking(patient), bmi=vitals.get("bmi"),
                                   problems=problems, medicines=[m.generic for m in active if m.generic],
                                   done=patient.get("immunizations") or patient.get("completed_prevention") or [])
        extra_items = [r for r in rem if not any(key in gap_text for key in r.get("keys", []))]
        if extra_items:
            srcs, seen_src = [], set()
            for r in extra_items:
                for src in r.get("sources") or [r["source"]]:
                    if src["url"] not in seen_src:
                        seen_src.add(src["url"])
                        srcs.append(src)
            findings.append(Finding(category="prevention", indicator="info",
                                    summary=f"Other preventive care to consider ({len(extra_items)})",
                                    detail="; ".join(f"{r['name']} ({r['detail'].rstrip('.')})" for r in extra_items) + ". " + k.prevention["note"],
                                    meds=[], sources=srcs, rule_id="prevention:reminders", graded=False,
                                    extra={"items": [{"id": r["id"], "name": r["name"], "detail": r["detail"]} for r in extra_items]}))

    # 12. FDA labels: renal text and label-mention interactions not covered above
    label_pairs = []
    if use_network:
        for a, b in combinations(active, 2):
            if a.generic == b.generic or not (a.new or b.new):
                continue
            if frozenset((a.generic, b.generic)) in covered_pairs:
                continue
            label_pairs.append((a, b))
        label_pairs = label_pairs[:12]
    wanted: list[str] = []
    for m, _e, _v, _b in renal_needed:
        if m.generic not in wanted:
            wanted.append(m.generic)
    for a, b in label_pairs:
        for g in (a.generic, b.generic):
            if g not in wanted:
                wanted.append(g)
    wanted = wanted[:10]
    labels: dict[str, dict | None] = {}
    if use_network and wanted and deadline - time.monotonic() > 1.0:
        def _label(g):
            remaining = deadline - time.monotonic()
            if remaining < 0.8:
                return g, None, "time budget used"
            try:
                return g, openfda.label(g, timeout=min(6.0, remaining)), None
            except net.SourceUnavailable as exc:
                return g, None, str(exc)
        with ThreadPoolExecutor(max_workers=min(6, len(wanted))) as pool:
            for g, lab, err in pool.map(_label, wanted):
                labels[g] = lab
                if err:
                    tracker.fail(openfda.SOURCE, err)
                else:
                    tracker.ok(openfda.SOURCE)

    for m, entry, value, basis in renal_needed:
        lab = labels.get(m.generic)
        live = openfda.renal_excerpt(lab) if lab else None
        if basis == "crcl":
            detail = f"Creatinine clearance about {value:.0f} mL/min ({crcl_basis}). {entry['text']}"
        else:
            detail = f"eGFR {value:.0f} mL/min/1.73 m²" + (f" ({egfr_basis})" if egfr_basis else "") + f". {entry['text']}"
            if entry.get("basis") == "crcl":
                detail += " (The label uses creatinine clearance; weight or creatinine was missing, so eGFR was used.)"
        sources = [entry["source"]]
        if live:
            detail += f" Label text{' (effective ' + lab['effective_date'] + ')' if lab.get('effective_date') else ''}: “{live}”"
            sources = [{"label": f"FDA label: {m.generic} (DailyMed)", "url": lab["url"], "date": lab.get("effective_date")}] + sources
        findings.append(Finding(category="renal", indicator=entry["severity"], summary=f"{entry['summary']}: {m.name}",
                                detail=detail + " The app does not calculate doses.", meds=[m.generic], sources=sources,
                                rule_id=f"renal:{m.generic}", effect=entry["text"], management="Follow the label's renal dosing section.",
                                context="new" if m.new else "existing",
                                extra={"egfr": round(egfr) if egfr is not None else None, "crcl": round(crcl) if crcl is not None else None}))

    for a, b in label_pairs:
        for x, y in ((a, b), (b, a)):
            lab = labels.get(x.generic)
            if not lab:
                continue
            excerpt = openfda.mention_excerpt(lab["sections"].get("drug_interactions", ""), k.names_for(y.generic))
            if excerpt:
                findings.append(Finding(category="interaction", indicator="info", summary=f"FDA label for {x.name} mentions {y.name}",
                                        detail=f"“{excerpt}” Label mention, not graded: review the full Drug Interactions section.",
                                        meds=[x.generic, y.generic],
                                        sources=[{"label": f"FDA label: {x.generic}, Drug Interactions (DailyMed)", "url": lab["url"], "date": lab.get("effective_date")}],
                                        rule_id="label-mention", graded=False, extra={"label_mention": True}))
                break

    # 13. assemble
    reviewed = k.reviewed
    cards, seen_ids = [], set()
    for f in findings:
        c = _to_card(f, reviewed, k.grading)
        if c["id"] in seen_ids:
            continue
        seen_ids.add(c["id"])
        cards.append(c)
    cards.sort(key=lambda c: (SEVERITY_RANK.get(c["indicator"], 3),
                              CATEGORY_ORDER.index(c["category"]) if c["category"] in CATEGORY_ORDER else 99,
                              c.get("context") != "new"))

    out_rows = []
    for m in meds:
        if m.origin != "visit":
            continue
        d = m.drug
        row = dict(m.row)
        row.update({
            "match": m.match,
            "high_risk": bool(d and d.high_risk),
            "high_risk_reason": d.high_risk if d else None,
            "schedule": d.schedule if d else None,
            "boxed_warning": bool(d and d.boxed_warning),
            "formulary": formulary_rows.get(m.key),
            "dose_check": dose_checks.get(m.key) or {"status": "unknown", "detail": "", "source": None},
            "confirmed": bool(m.row.get("confirmed", False)),
            "dose_confirmed": bool(m.row.get("dose_confirmed", False)),
        })
        out_rows.append(row)

    def _src_status(name: str, used_label: str):
        st = tracker.status(name)
        if st is None:
            return {"name": name, "status": "not_used" if use_network else "unavailable",
                    "detail": used_label if use_network else "Network lookups switched off."}
        if st == "partial":
            return {"name": name, "status": "ok", "detail": "Some lookups were unavailable: " + tracker.detail(name)}
        if st == "unavailable":
            return {"name": name, "status": "unavailable", "detail": tracker.detail(name) + ". Curated rules still applied."}
        return {"name": name, "status": "ok", "detail": ""}

    sources_checked = [
        {"name": "Curated interaction, allergy, condition, dose and renal rules (CustomAi KB)", "status": "ok",
         "detail": f"{len(k.interactions['rules'])} interaction rules including the 15 ONC high-priority pairs; reviewed {reviewed}. {k.grading}."},
        _src_status(rxnav.SOURCE, "Not needed: every name matched the curated list."),
        _src_status(openfda.SOURCE, "No label lookups were needed for this list."),
    ]
    if age is not None and age >= k.older["min_age"]:
        sources_checked.append({"name": "STOPP/START v3 (older adults)", "status": "ok", "detail": "Selected criteria, CC BY 4.0."})
    if plan_key:
        sources_checked.append({"name": formulary.LABEL if plan_key != "COMMERCIAL" else "Commercial formulary",
                                "status": "ok" if plan_key != "COMMERCIAL" else "unavailable",
                                "detail": "" if plan_key != "COMMERCIAL" else k.formulary["commercial_note"]})

    return {
        "medications": out_rows,
        "cards": cards,
        "sources_checked": sources_checked,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "patient_context": {"age": age, "sex": sex, "egfr": round(egfr) if egfr is not None else None, "egfr_basis": egfr_basis,
                            "crcl": round(crcl) if crcl is not None else None, "crcl_basis": crcl_basis,
                            "potassium": potassium, "sodium": sodium, "plan_key": plan_key, "pregnant": pregnant},
        "empty_message": None if any(c["category"] in ("interaction", "qt", "allergy", "condition", "renal", "dose", "duplicate", "older_adult") for c in cards)
        else "No interaction found in these sources.",
        "rxnorm_attribution": rxnav.ATTRIBUTION,
        "elapsed_s": round(time.monotonic() - started, 2),
    }


def check_list(medications: list[dict], patient_ctx: dict | None = None, *, include_prevention: bool = False,
               timeout_s: float = 8.0, use_network: bool = True) -> dict:
    """Pharmacy tool: a plain list of medicines plus optional patient context."""
    ctx = patient_ctx or {}
    rows = []
    for i, m in enumerate(medications or []):
        if isinstance(m, str):
            m = {"name": m}
        if not (m.get("name") or "").strip():
            continue
        rows.append({"id": f"m{i + 1}", "name": m["name"].strip(), "dose": m.get("dose"), "unit": m.get("unit"),
                     "frequency": m.get("frequency"), "action": "start", "indication": m.get("indication")})
    patient: dict = {"age": ctx.get("age"), "sex": ctx.get("sex"), "egfr": ctx.get("egfr"), "pregnant": bool(ctx.get("pregnant")),
                     "problems": [{"name": p} if isinstance(p, str) else p for p in (ctx.get("problems") or [])],
                     "allergies": [{"substance": a} if isinstance(a, str) else a for a in (ctx.get("allergies") or [])],
                     "insurance": {"plan_key": ctx.get("plan_key")} if ctx.get("plan_key") else {}}
    labs = []
    for key, name, unit in (("creatinine", "Creatinine", "mg/dL"), ("potassium", "Potassium", "mmol/L"), ("sodium", "Sodium", "mmol/L")):
        if ctx.get(key) not in (None, ""):
            labs.append({"name": name, "value": ctx[key], "unit": unit})
    if labs:
        patient["labs"] = labs
    if ctx.get("weight_kg") not in (None, ""):
        patient["vitals"] = {"weight_kg": ctx.get("weight_kg")}
    result = check_visit(rows, patient, timeout_s=timeout_s, use_network=use_network, include_prevention_reminders=False)
    if include_prevention and ctx.get("age") not in (None, ""):
        result["prevention"] = prevention.reminders(ctx.get("age"), ctx.get("sex"), smoking=ctx.get("smoking"), bmi=ctx.get("bmi"))
        result["prevention_note"] = kb().prevention["note"]
    return result
