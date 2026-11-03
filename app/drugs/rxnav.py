"""RxNav (RxNorm) client. Data from the U.S. National Library of Medicine.

Documented endpoints used (JSON):
  /approximateTerm.json?term=&maxEntries=&option=1
  /rxcui.json?name=&search=1
  /rxcui/{rxcui}/properties.json
  /rxcui/{rxcui}/related.json?tty=IN+MIN
  /drugs.json?name=
  /spellingsuggestions.json?name=
Terms of service: https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html (20 requests/second per IP).
"""
from __future__ import annotations

from ..config import settings
from . import net

SOURCE = "RxNorm (NLM RxNav)"
ATTRIBUTION = "This product uses publicly available data from the U.S. National Library of Medicine (NLM), National Institutes of Health, Department of Health and Human Services; NLM is not responsible for the product and does not endorse or recommend this or any other product."


def _base() -> str:
    return settings.rxnav_base.rstrip("/")


def approximate_term(term: str, max_entries: int = 8, timeout: float = 6.0) -> list[dict]:
    """[{rxcui, name|None, score: float, rank: int, source}] best first."""
    term = term.strip()
    if not term:
        return []
    data = net.get_json(f"{_base()}/approximateTerm.json", {"term": term, "maxEntries": max_entries, "option": 1},
                        cache_key=f"rxnav:approx:{term.lower()}:{max_entries}", timeout=timeout)
    out = []
    for c in ((data or {}).get("approximateGroup") or {}).get("candidate") or []:
        try:
            score = float(c.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            rank = int(c.get("rank") or 99)
        except (TypeError, ValueError):
            rank = 99
        if not c.get("rxcui"):
            continue
        out.append({"rxcui": str(c["rxcui"]), "name": c.get("name"), "score": score, "rank": rank, "source": c.get("source", "RXNORM")})
    out.sort(key=lambda c: (c["rank"], -c["score"]))
    # one row per rxcui
    seen, uniq = set(), []
    for c in out:
        if c["rxcui"] in seen:
            continue
        seen.add(c["rxcui"])
        uniq.append(c)
    return uniq


def rxcui_by_name(name: str, timeout: float = 6.0) -> str | None:
    data = net.get_json(f"{_base()}/rxcui.json", {"name": name, "search": 1},
                        cache_key=f"rxnav:rxcui:{name.lower()}", timeout=timeout)
    ids = ((data or {}).get("idGroup") or {}).get("rxnormId") or []
    return str(ids[0]) if ids else None


def properties(rxcui: str, timeout: float = 6.0) -> dict | None:
    data = net.get_json(f"{_base()}/rxcui/{rxcui}/properties.json", None,
                        cache_key=f"rxnav:props:{rxcui}", timeout=timeout, not_found_ok=True)
    props = (data or {}).get("properties")
    if not props:
        return None
    return {"rxcui": str(props.get("rxcui", rxcui)), "name": props.get("name"), "tty": props.get("tty")}


def ingredients(rxcui: str, timeout: float = 6.0) -> list[dict]:
    """Ingredient concepts (IN or MIN) related to any RxNorm concept."""
    data = net.get_json(f"{_base()}/rxcui/{rxcui}/related.json", {"tty": "IN+MIN"},
                        cache_key=f"rxnav:related:{rxcui}:IN+MIN", timeout=timeout, not_found_ok=True)
    out = []
    for group in ((data or {}).get("relatedGroup") or {}).get("conceptGroup") or []:
        for p in group.get("conceptProperties") or []:
            out.append({"rxcui": str(p.get("rxcui")), "name": p.get("name"), "tty": p.get("tty") or group.get("tty")})
    return out


def products(name: str, timeout: float = 6.0) -> list[dict]:
    """Clinical (SCD) and branded (SBD) products for a name: [{rxcui, name, tty}]."""
    data = net.get_json(f"{_base()}/drugs.json", {"name": name}, cache_key=f"rxnav:drugs:{name.lower()}", timeout=timeout)
    out = []
    for group in ((data or {}).get("drugGroup") or {}).get("conceptGroup") or []:
        tty = group.get("tty")
        for p in group.get("conceptProperties") or []:
            out.append({"rxcui": str(p.get("rxcui")), "name": p.get("name"), "tty": p.get("tty") or tty})
    return out


def spelling_suggestions(name: str, timeout: float = 4.0) -> list[str]:
    data = net.get_json(f"{_base()}/spellingsuggestions.json", {"name": name},
                        cache_key=f"rxnav:spell:{name.lower()}", timeout=timeout)
    lst = (((data or {}).get("suggestionGroup") or {}).get("suggestionList") or {}).get("suggestion") or []
    return [s for s in lst if isinstance(s, str)]
