"""Drug safety and formulary API (prefix /api). See docs/CONTRACTS.md, "Drugs contract"."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auth
from . import calculators, formulary, net, rxnav
from .engine import check_list
from .kb import kb
from .normalize import norm

router = APIRouter(tags=["drugs"])


class MedIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    dose: str | float | None = None
    unit: str | None = None
    frequency: str | None = None
    indication: str | None = None


class PatientIn(BaseModel):
    age: float | None = Field(default=None, ge=0, le=120)
    sex: str | None = None
    egfr: float | None = Field(default=None, ge=1, le=200)
    creatinine: float | None = Field(default=None, gt=0, le=30)
    weight_kg: float | None = Field(default=None, gt=0, le=400)
    potassium: float | None = Field(default=None, gt=0, le=12)
    sodium: float | None = Field(default=None, gt=80, le=200)
    allergies: list[str] = Field(default_factory=list, max_length=20)
    problems: list[str] = Field(default_factory=list, max_length=30)
    plan_key: str | None = None
    pregnant: bool = False


class CheckIn(BaseModel):
    medications: list[MedIn] = Field(min_length=1, max_length=25)
    patient: PatientIn | None = None
    include_prevention: bool = False


@router.post("/drugs/check")
def drugs_check(body: CheckIn, _s: dict = Depends(auth.require)):
    ctx = body.patient.model_dump() if body.patient else {}
    return check_list([m.model_dump() for m in body.medications], ctx, include_prevention=body.include_prevention)


@router.get("/drugs/search")
def drugs_search(q: str = Query(min_length=1, max_length=80), _s: dict = Depends(auth.require)):
    k = kb()
    query = norm(q)
    results, seen = [], set()
    if len(query) >= 2:
        starts, contains = [], []
        for generic, d in k.drugs.items():
            names = [(generic, "IN")] + [(b, "BN") for b in d.brands]
            for name, tty in names:
                key = norm(name)
                if key.startswith(query):
                    starts.append((len(key), name, tty, generic))
                elif query in key and len(query) >= 3:
                    contains.append((len(key), name, tty, generic))
        for _, name, tty, generic in sorted(starts) + sorted(contains):
            label = name if tty == "IN" else f"{name} ({generic.replace('-', '/')})"
            if label.lower() in seen:
                continue
            seen.add(label.lower())
            results.append({"rxcui": k.drugs[generic].rxcui if tty == "IN" else None, "name": name, "label": label,
                            "generic": generic, "tty": tty, "source": "local"})
            if len(results) >= 12:
                break
    rxnorm_status = "not_used"
    if len(results) < 3 and len(query) >= 4:
        try:
            for c in rxnav.approximate_term(q, max_entries=6, timeout=2.5):
                name = c.get("name")
                if not name or name.lower() in seen:
                    continue
                seen.add(name.lower())
                results.append({"rxcui": c["rxcui"], "name": name, "label": name, "generic": k.lookup(name), "tty": None, "source": "RxNorm"})
            rxnorm_status = "ok"
        except net.SourceUnavailable:
            rxnorm_status = "unavailable"
    return {"results": results, "rxnorm": rxnorm_status}


@router.get("/formulary/plans")
def formulary_plans(_s: dict = Depends(auth.require)):
    return {"plans": formulary.plans(), "label": formulary.LABEL}


@router.get("/formulary/lookup")
def formulary_lookup(plan_key: str = Query(min_length=2, max_length=20), drug: str = Query(min_length=1, max_length=80),
                     _s: dict = Depends(auth.require)):
    result = formulary.lookup(plan_key, drug)
    if result.get("error") == "Unknown plan.":
        raise HTTPException(404, "Unknown plan.")
    return result


class CalcIn(BaseModel):
    calculator: str
    inputs: dict = Field(default_factory=dict)


@router.post("/drugs/calc")
def drugs_calc(body: CalcIn, _s: dict = Depends(auth.require)):
    try:
        return calculators.run(body.calculator, body.inputs)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(422, f"Check the inputs: {exc}")


@router.get("/drugs/sources")
def drugs_sources(_s: dict = Depends(auth.require)):
    k = kb()
    return {
        "reviewed": k.reviewed,
        "grading": k.grading,
        "rules": len(k.interactions["rules"]),
        "onc_pairs": len({r.get("onc_pair") or r["id"] for r in k.interactions["rules"] if r.get("onc")}),
        "onc_rules": sum(1 for r in k.interactions["rules"] if r.get("onc")),
        "drugs": len(k.drugs),
        "formulary": formulary.LABEL,
        "rxnorm_attribution": rxnav.ATTRIBUTION,
        "sources": [
            {"name": "ONC high-priority drug-drug interactions (Phansalkar 2012)", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC3422823/"},
            {"name": "FDA drug labels (openFDA / DailyMed)", "url": "https://open.fda.gov/apis/drug/label/"},
            {"name": "RxNorm (NLM RxNav)", "url": "https://lhncbc.nlm.nih.gov/RxNav/"},
            {"name": "STOPP/START v3 (CC BY 4.0)", "url": "https://link.springer.com/article/10.1007/s41999-023-00777-y"},
            {"name": "USPSTF A and B recommendations", "url": "https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/uspstf-a-and-b-recommendations"},
            {"name": "CDC adult immunization schedule", "url": "https://www.cdc.gov/vaccines/hcp/imz-schedules/adult-age.html"},
        ],
    }
