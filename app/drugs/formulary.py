"""Medicare Part D formulary lookup.

Two sources, never mixed in one answer:

- **Real data** (when ready): CMS's quarterly Part D public use file, loaded in the
  background by app/drugs/partd.py for two real New Jersey plans (one standalone PDP
  as plan key PDP-A, one MA-PD as MAPD-B). Tier numbers come straight from
  TIER_LEVEL_VALUE; the file does not name tiers, so none are invented. A tier is
  called "specialty" only when the plan's beneficiary cost file says so.
- **Sample** (while loading, or if the CMS file can't be read): a demo formulary laid
  out like the CMS Basic Drugs Formulary File, labelled as demo data wherever shown.

Alternatives are ranked by tier, then generic products, then fewer restrictions,
then name. Sponsors have no influence on formulary data or ranking.
"""
from __future__ import annotations

from . import partd
from .kb import kb

SAMPLE_LABEL = "Sample formulary (structure of the CMS Part D files; demo data)"
LABEL = SAMPLE_LABEL  # kept for callers that read the constant; use label() for the live label
RANKING = "Alternatives are sorted by tier, then generic availability. Sponsors have no influence on this list."


def _f() -> dict:
    return kb().formulary


def _real() -> dict | None:
    return partd.ready()


def source() -> str:
    """'cms_puf' when answering from the real CMS data, else 'sample'."""
    return "cms_puf" if _real() else "sample"


def label(plan_key: str | None = None) -> str:
    real = _real()
    if not real:
        return SAMPLE_LABEL
    base = f"Medicare Part D formulary: CMS public use file, {real.get('release_label')}"
    p = (real.get("plans") or {}).get(plan_key or "")
    return f"{base} ({p['name']})" if p else base


def source_url() -> str:
    real = _real()
    return (real.get("landing_url") or partd.LANDING_URL) if real else _f()["layout_url"]


def uses_real(plan_key: str | None) -> bool:
    real = _real()
    return bool(real and plan_key in (real.get("plans") or {}))


def loader_status() -> dict:
    """Status for the UI: state sample|loading|ready|failed, detail, release, source (cms_puf|sample), label."""
    st = partd.status()
    real = _real()
    if real and st.get("state") != "ready":
        # another process built it, or a refresh failed: the saved subset is what is served
        st = {**st, "state": "ready", "detail": partd._ready_detail(real, partd.subset_age_days(real))}
    st["source"] = source()
    st["label"] = label()
    return st


def source_checked(plan_key: str) -> dict:
    """The formulary entry for check_visit's sources_checked: which data answered, and the loader state."""
    st = loader_status()
    if uses_real(plan_key):
        return {"name": label(plan_key), "status": "ok", "state": st["state"], "data": "cms_puf", "detail": st["detail"],
                "url": (_real() or {}).get("landing_url")}
    if st["state"] == "loading":
        detail = f"The real CMS Part D formulary is still loading ({st['detail']}); this check used the sample formulary."
    elif st["state"] == "ready":
        detail = "This plan is not in the loaded CMS subset; this check used the sample formulary."
    else:
        detail = st["detail"]
    return {"name": SAMPLE_LABEL, "status": "ok", "state": st["state"], "data": "sample", "detail": detail,
            "url": _f()["layout_url"]}


# ---------------------------------------------------------------- plans

def plans() -> list[dict]:
    f = _f()
    real = _real()
    if real:
        out = []
        for pk in ("PDP-A", "MAPD-B"):
            p = real["plans"].get(pk)
            if p:
                out.append({**p, "source": label(pk), "data": "cms_puf", "release": real.get("release_label"),
                            "source_url": real.get("source_url")})
        if not out:
            real = None
    if not real:
        out = [{**dict(p), "data": "sample"} for p in f["plans"].values()]
    out.append({"plan_key": "COMMERCIAL", "name": "Commercial plan", "type": "Commercial", "contract_id": None,
                "plan_id": None, "year": f["year"], "source": f["commercial_note"], "data": "none"})
    return out


def plan(plan_key: str | None) -> dict | None:
    if not plan_key:
        return None
    return next((p for p in plans() if p["plan_key"] == plan_key), None)


# ---------------------------------------------------------------- status per medicine

def _status_from_entry(e: dict) -> dict:
    tiers = _f()["tiers"]
    return {
        "on_formulary": True,
        "tier": e["tier"],
        "tier_label": tiers.get(str(e["tier"])),
        "tier_text": f"Tier {e['tier']} · {tiers.get(str(e['tier']))}",
        "prior_auth": bool(e["pa"]),
        "step_therapy": bool(e["st"]),
        "quantity_limit": bool(e["ql"]),
        "ql_detail": e.get("ql_detail"),
        "note": e.get("note"),
        "generic_available": bool(e.get("generic_available", True)),
    }


def _real_entry_status(real: dict, plan_key: str, generic: str) -> dict:
    p = real["plans"][plan_key]
    e = (real.get("drugs") or {}).get(plan_key, {}).get(generic)
    base = {"plan_key": plan_key, "plan_name": p["name"], "available": True, "source": label(plan_key),
            "data": "cms_puf", "release": real.get("release_label")}
    if not e or not e.get("mapped"):
        return {**base, "on_formulary": None, "tier": None, "tier_label": None, "prior_auth": False, "step_therapy": False,
                "quantity_limit": False, "ql_detail": None,
                "note": "Not matched to RxNorm drug products, so this plan's formulary was not checked."}
    if not e.get("covered"):
        d = kb().get(generic)
        reason = _f()["not_covered"].get(generic)
        if not reason and d is not None and d.otc:
            reason = "Over-the-counter product: Part D excludes most non-prescription drugs."
        reason = (reason + " " if reason else "") + "No covered product on this plan's formulary in the CMS public use file."
        return {**base, "on_formulary": False, "tier": None, "tier_label": None, "prior_auth": False, "step_therapy": False,
                "quantity_limit": False, "ql_detail": None, "note": reason.strip(), "generic_available": None}
    # The CMS file numbers tiers but does not name them, so the label is the number out of the plan's
    # tier count ("Tier 2 of 6"), plus "(specialty)" where the beneficiary cost file flags that tier.
    tier_text = e.get("tier_text") or f"Tier {e['tier']}"
    return {**base, "on_formulary": True, "tier": e["tier"], "tier_label": tier_text, "tier_text": tier_text,
            "tier_count": p.get("max_tier"), "specialty": bool(e.get("specialty")),
            "prior_auth": bool(e.get("pa")), "step_therapy": bool(e.get("st")), "quantity_limit": bool(e.get("ql")),
            "ql_detail": e.get("ql_detail"), "note": None, "generic_available": bool(e.get("generic_product")),
            "products": e.get("products") or [], "rxcui": e.get("best_rxcui")}


def status(plan_key: str | None, generic: str | None) -> dict | None:
    """Row-level formulary status for check_visit; None when there is no plan."""
    if not plan_key:
        return None
    f = _f()
    if plan_key == "COMMERCIAL":
        return {"plan_key": "COMMERCIAL", "plan_name": "Commercial plan", "available": False, "note": f["commercial_note"], "source": None}
    real = _real()
    if real and plan_key in real["plans"]:
        if not generic:
            p = real["plans"][plan_key]
            return {"plan_key": plan_key, "plan_name": p["name"], "available": True, "source": label(plan_key),
                    "data": "cms_puf", "on_formulary": None, "note": "Medicine not matched; formulary not checked."}
        return _real_entry_status(real, plan_key, generic)
    p = f["plans"].get(plan_key)
    if not p:
        return {"plan_key": plan_key, "plan_name": None, "available": False, "note": "Plan not in the formulary data.", "source": label()}
    base = {"plan_key": plan_key, "plan_name": p["name"], "available": True, "source": SAMPLE_LABEL, "data": "sample"}
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
    if st.get("prior_auth") or st.get("step_therapy"):
        return True
    if st.get("data") == "cms_puf":
        # Tier numbers mean different things on different plans (tier 6 is often a low-cost select tier),
        # so for real plans only the specialty flag from the cost file counts as a cost restriction.
        return bool(st.get("specialty"))
    return (st.get("tier") or 0) >= 4


def alternatives(plan_key: str, generic: str, limit: int = 5) -> list[dict]:
    f = _f()
    group = f["groups"].get(generic)
    if not group:
        return []
    real = _real()
    out = []
    if real and plan_key in real["plans"]:
        for g, e in (real.get("drugs") or {}).get(plan_key, {}).items():
            if g == generic or f["groups"].get(g) != group or not e.get("covered"):
                continue
            out.append({"drug": g, "tier": e["tier"], "tier_label": e.get("tier_text"), "tier_text": e.get("tier_text"),
                        "specialty": bool(e.get("specialty")),
                        "prior_auth": bool(e.get("pa")), "step_therapy": bool(e.get("st")), "quantity_limit": bool(e.get("ql")),
                        "ql_detail": e.get("ql_detail"), "generic_available": bool(e.get("generic_product"))})
    else:
        if plan_key not in f["entries"]:
            return []
        tiers = f["tiers"]
        for g, e in f["entries"][plan_key].items():
            if g == generic or f["groups"].get(g) != group:
                continue
            out.append({"drug": g, "tier": e["tier"], "tier_label": tiers.get(str(e["tier"])),
                        "tier_text": f"Tier {e['tier']} · {tiers.get(str(e['tier']))}", "prior_auth": e["pa"],
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
    lab = label(plan_key)
    common = {"data": source(), "loader": loader_status()}
    if not p:
        return {"plan": None, "drug": drug, "status": None, "alternatives": [], "source": lab, "error": "Unknown plan.", **common}
    if plan_key == "COMMERCIAL":
        return {"plan": p, "drug": drug, "status": None, "alternatives": [], "source": None, "note": _f()["commercial_note"], **common}
    if not generic:
        return {"plan": p, "drug": drug, "status": None, "alternatives": [], "source": lab,
                "error": "Medicine not recognized. Try the generic name.", **common}
    st = status(plan_key, generic)
    alts = alternatives(plan_key, generic) if restricted(st) or (st and (st.get("tier") or 0) >= 3) else alternatives(plan_key, generic, limit=3)
    real = _real()
    return {"plan": p, "drug": drug, "status": st, "alternatives": alts, "source": lab, "ranking": RANKING,
            "layout_url": (real or {}).get("layout_url") or _f()["layout_url"],
            "source_url": (real or {}).get("source_url"), **common}


# Start loading the real CMS data in the background (no-op offline and in tests; never raises).
partd.start_background()
