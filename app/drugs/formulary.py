"""Medicare Part D formulary lookup on the sample formulary.

Decision (see app/drugs/kb/README.md): CMS publishes Part D formulary data only as
large quarterly/monthly public use file downloads, with no row-level API that the
demo host can query at runtime. The demo therefore ships a sample formulary laid out
like the CMS Basic Drugs Formulary File (tier, prior authorization, step therapy,
quantity limit) and labels it as demo data wherever it appears.

Alternatives are ranked by tier, then generic availability, then name. Sponsors have
no influence on formulary data or ranking.
"""
from __future__ import annotations

from .kb import kb

LABEL = "Sample formulary (structure of the CMS Part D files; demo data)"
RANKING = "Alternatives are sorted by tier, then generic availability. Sponsors have no influence on this list."


def _f() -> dict:
    return kb().formulary


def plans() -> list[dict]:
    f = _f()
    out = [dict(p) for p in f["plans"].values()]
    out.append({"plan_key": "COMMERCIAL", "name": "Commercial plan", "type": "Commercial", "contract_id": None,
                "plan_id": None, "year": f["year"], "source": f["commercial_note"]})
    return out


def plan(plan_key: str | None) -> dict | None:
    if not plan_key:
        return None
    if plan_key == "COMMERCIAL":
        return next(p for p in plans() if p["plan_key"] == "COMMERCIAL")
    return _f()["plans"].get(plan_key)


def _status_from_entry(e: dict) -> dict:
    tiers = _f()["tiers"]
    return {
        "on_formulary": True,
        "tier": e["tier"],
        "tier_label": tiers.get(str(e["tier"])),
        "prior_auth": bool(e["pa"]),
        "step_therapy": bool(e["st"]),
        "quantity_limit": bool(e["ql"]),
        "ql_detail": e.get("ql_detail"),
        "note": e.get("note"),
        "generic_available": bool(e.get("generic_available", True)),
    }


def status(plan_key: str | None, generic: str | None) -> dict | None:
    """Row-level formulary status for check_visit; None when there is no plan."""
    if not plan_key:
        return None
    f = _f()
    if plan_key == "COMMERCIAL":
        return {"plan_key": "COMMERCIAL", "plan_name": "Commercial plan", "available": False, "note": f["commercial_note"], "source": None}
    p = f["plans"].get(plan_key)
    if not p:
        return {"plan_key": plan_key, "plan_name": None, "available": False, "note": "Plan not in the sample formulary.", "source": LABEL}
    base = {"plan_key": plan_key, "plan_name": p["name"], "available": True, "source": LABEL}
    if not generic:
        return {**base, "on_formulary": None, "note": "Medicine not matched; formulary not checked."}
    entry = f["entries"].get(plan_key, {}).get(generic)
    if entry:
        return {**base, **_status_from_entry(entry)}
    reason = f["not_covered"].get(generic) or "Not on this sample formulary."
    return {**base, "on_formulary": False, "tier": None, "tier_label": None, "prior_auth": False, "step_therapy": False,
            "quantity_limit": False, "ql_detail": None, "note": reason}


def restricted(st: dict | None) -> bool:
    if not st or not st.get("available"):
        return False
    if st.get("on_formulary") is False:
        return True
    return bool(st.get("prior_auth") or st.get("step_therapy") or (st.get("tier") or 0) >= 4)


def alternatives(plan_key: str, generic: str, limit: int = 5) -> list[dict]:
    f = _f()
    group = f["groups"].get(generic)
    if not group or plan_key not in f["entries"]:
        return []
    tiers = f["tiers"]
    out = []
    for g, e in f["entries"][plan_key].items():
        if g == generic or f["groups"].get(g) != group:
            continue
        out.append({"drug": g, "tier": e["tier"], "tier_label": tiers.get(str(e["tier"])), "prior_auth": e["pa"],
                    "step_therapy": e["st"], "quantity_limit": e["ql"], "ql_detail": e.get("ql_detail"),
                    "generic_available": e.get("generic_available", True)})
    out.sort(key=lambda a: (a["tier"], not a["generic_available"], a["prior_auth"] or a["step_therapy"], a["drug"]))
    return out[:limit]


def group_label(generic: str) -> str | None:
    f = _f()
    g = f["groups"].get(generic)
    if not g:
        return None
    return f["group_labels"].get(g) or kb().class_label(g)


def lookup(plan_key: str, drug_name: str) -> dict:
    """GET /api/formulary/lookup."""
    k = kb()
    p = plan(plan_key)
    generic = k.lookup(drug_name)
    drug = {"query": drug_name, "generic": generic, "brands": k.get(generic).brands if generic else [],
            "group": group_label(generic) if generic else None}
    if not p:
        return {"plan": None, "drug": drug, "status": None, "alternatives": [], "source": LABEL, "error": "Unknown plan."}
    if plan_key == "COMMERCIAL":
        return {"plan": p, "drug": drug, "status": None, "alternatives": [], "source": None, "note": _f()["commercial_note"]}
    if not generic:
        return {"plan": p, "drug": drug, "status": None, "alternatives": [], "source": LABEL,
                "error": "Medicine not recognized. Try the generic name."}
    st = status(plan_key, generic)
    alts = alternatives(plan_key, generic) if restricted(st) or (st and (st.get("tier") or 0) >= 3) else alternatives(plan_key, generic, limit=3)
    return {"plan": p, "drug": drug, "status": st, "alternatives": alts, "source": LABEL, "ranking": RANKING,
            "layout_url": _f()["layout_url"]}
