"""Medicine-name matching: local KB first, then RxNorm; sound-alike guard."""
from __future__ import annotations

import difflib
import re
import time

from . import net, rxnav
from .kb import Drug, kb
from .normalize import contains_term, norm, strip_dose_and_form


def _remaining(deadline: float | None) -> float:
    if deadline is None:
        return 6.0
    return deadline - time.monotonic()


def fuzzy_local(name: str, n: int = 4, cutoff: float = 0.82) -> list[tuple[str, float]]:
    """[(generic, ratio)] from close spellings of known names."""
    k = kb()
    q = strip_dose_and_form(name) or norm(name)
    if len(q) < 4:
        return []
    keys = difflib.get_close_matches(q, list(k.index.keys()), n=n * 3, cutoff=cutoff)
    out: dict[str, float] = {}
    for key in keys:
        g = k.index[key]
        r = difflib.SequenceMatcher(None, q, key).ratio()
        out[g] = max(out.get(g, 0.0), r)
    return sorted(out.items(), key=lambda kv: -kv[1])[:n]


def _uses_hit(drug: Drug, text: str) -> bool:
    return any(contains_term(text, u) for u in drug.uses)


def sound_alike_check(drug: Drug, *, indication: str, problems_text: str, chart_generics: set[str]) -> list[str]:
    """Reasons to doubt the match because a confusable partner fits the context better."""
    k = kb()
    reasons = []
    for sa in drug.sound_alike:
        partner = k.get(sa["drug"])
        if not partner:
            continue
        if partner.generic in chart_generics and drug.generic not in chart_generics:
            reasons.append(f"The chart lists {partner.generic}, a known look-alike/sound-alike ({sa['pair']}); this row says {drug.generic}.")
            continue
        if indication and partner.uses and _uses_hit(partner, indication) and not _uses_hit(drug, indication):
            reasons.append(f"The stated reason (\"{indication}\") fits {partner.generic} ({sa['pair']}) rather than {drug.generic}.")
            continue
        if not indication and problems_text and partner.uses and drug.uses and _uses_hit(partner, problems_text) and not _uses_hit(drug, problems_text):
            reasons.append(f"The chart's problems fit {partner.generic} ({sa['pair']}) better than {drug.generic}; confirm which was meant.")
    return reasons


def _display_from_products(generic: str, dose, unit, tracker: net.SourceTracker, deadline) -> tuple[str | None, str | None]:
    """Find an RxNorm clinical drug name like 'lisinopril 20 MG Oral Tablet'."""
    if dose in (None, "") or not unit or _remaining(deadline) < 0.8:
        return None, None
    try:
        prods = rxnav.products(generic, timeout=min(4.0, _remaining(deadline)))
        tracker.ok(rxnav.SOURCE)
    except net.SourceUnavailable as exc:
        tracker.fail(rxnav.SOURCE, str(exc))
        return None, None
    num = re.sub(r"[^0-9.]", "", str(dose))
    if not num:
        return None, None
    needle = f"{num} {str(unit).upper()}"
    scd = [p for p in prods if (p.get("tty") == "SCD") and needle in (p.get("name") or "")]
    if not scd:
        return None, None
    best = sorted(scd, key=lambda p: ("Oral Tablet" not in p["name"], len(p["name"])))[0]
    return best["name"], best["rxcui"]


def match_row(row: dict, *, chart_generics: set[str], problems_text: str, tracker: net.SourceTracker,
              deadline: float | None, use_network: bool = True) -> dict:
    k = kb()
    raw = (row.get("name") or row.get("as_spoken") or "").strip()
    result = {"status": "unmatched", "rxcui": None, "ingredient": None, "display": None, "candidates": [],
              "sound_alike": [], "source": "local", "reason": None}
    if not raw:
        result["reason"] = "No medicine name."
        return result

    generic = k.lookup(raw)
    how = "exact"
    if not generic:
        fz = fuzzy_local(raw)
        if fz and fz[0][1] >= 0.9 and (len(fz) == 1 or fz[0][1] - fz[1][1] >= 0.05):
            generic, how = fz[0][0], "spelling"

    rx_candidates: list[dict] = []
    if not generic and use_network and _remaining(deadline) > 1.0:
        try:
            cands = rxnav.approximate_term(strip_dose_and_form(raw) or raw, timeout=min(5.0, _remaining(deadline)))
            tracker.ok(rxnav.SOURCE)
            for c in cands[:5]:
                name = c.get("name")
                if not name and _remaining(deadline) > 0.8:
                    props = rxnav.properties(c["rxcui"], timeout=min(3.0, _remaining(deadline)))
                    name = (props or {}).get("name")
                if not name:
                    continue
                rx_candidates.append({"rxcui": c["rxcui"], "name": name, "score": c["score"], "local": k.lookup(name)})
        except net.SourceUnavailable as exc:
            tracker.fail(rxnav.SOURCE, str(exc))
        if rx_candidates:
            top = rx_candidates[0]
            q = strip_dose_and_form(raw) or norm(raw)
            base = strip_dose_and_form(top["name"])
            close = q == base or base.startswith(q + " ") or difflib.SequenceMatcher(None, q, base).ratio() >= 0.85
            if close:
                generic = top["local"]
                result.update({"status": "matched", "source": "RxNorm", "rxcui": top["rxcui"], "display": top["name"],
                               "ingredient": generic or base})
                how = "rxnorm"
            result["candidates"] = [{"rxcui": c["rxcui"], "name": c["name"], "score": c["score"]} for c in rx_candidates]

    if not generic and result["status"] != "matched":
        fz = fuzzy_local(raw, cutoff=0.75)
        if fz:
            result["status"] = "ambiguous"
            result["reason"] = "Not an exact match; choose the intended medicine."
            if not result["candidates"]:
                result["candidates"] = [{"rxcui": (k.get(g).rxcui if k.get(g) else None), "name": g, "score": round(r, 2)} for g, r in fz]
        elif result["candidates"]:
            result["status"] = "ambiguous"
            result["reason"] = "RxNorm returned only approximate matches; choose the intended medicine."
        else:
            result["reason"] = "Not found in the curated list" + (" or RxNorm." if use_network else ".")
        return result

    drug = k.get(generic)
    if drug is None:  # matched via RxNorm to a drug outside the curated list
        return result
    result["ingredient"] = drug.generic
    if result["status"] != "matched":
        result["status"] = "matched"
        result["source"] = "local"
    if how == "spelling":
        result["reason"] = f"Matched by spelling to {drug.generic}."
    if not result["rxcui"]:
        # RxNorm is authoritative when reachable (cached 7 days); the curated RxCUI is the offline fallback.
        live = None
        if use_network and len(drug.ingredients) == 1 and _remaining(deadline) > 1.0:
            try:
                live = rxnav.rxcui_by_name(drug.generic, timeout=min(4.0, _remaining(deadline)))
                tracker.ok(rxnav.SOURCE)
            except net.SourceUnavailable as exc:
                tracker.fail(rxnav.SOURCE, str(exc))
        result["rxcui"] = live or drug.rxcui
        if live:
            result["source"] = "RxNorm"
    if use_network and not result["display"] and len(drug.ingredients) == 1:
        display, _ = _display_from_products(drug.generic, row.get("dose"), row.get("unit"), tracker, deadline)
        if display:
            result["display"] = display
            result["source"] = "RxNorm"

    result["sound_alike"] = [sa["drug"] for sa in drug.sound_alike]
    reasons = sound_alike_check(drug, indication=str(row.get("indication") or ""), problems_text=problems_text,
                                chart_generics=chart_generics)
    if reasons:
        result["status"] = "ambiguous"
        result["reason"] = " ".join(reasons)
        partners = [k.get(sa["drug"]) for sa in drug.sound_alike]
        result["candidates"] = [{"rxcui": drug.rxcui, "name": drug.generic, "score": None}] + [
            {"rxcui": p.rxcui, "name": p.generic, "score": None} for p in partners if p]
    return result
