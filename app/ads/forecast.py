"""Sellable inventory under the chosen ad load, and campaign forecasts.

The binding constraint is the frequency cap, not slot supply: a prescriber offers
about 420 visits x 3 slots a month, but each brand may show at most N times a week
to that prescriber, and each office shows one brand per therapeutic category.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import catalog
from .metrics import MONTH_DAYS, today
from .serving import effective_status, policy


def _network_viewability(conn) -> float:
    start = (today() - timedelta(days=29)).isoformat()
    r = conn.execute(
        "SELECT SUM(rendered) r, SUM(viewable) v FROM delivery_daily WHERE traffic='valid' AND slot!='resources_tray' AND day>=?",
        (start,),
    ).fetchone()
    return (r["v"] / r["r"]) if r and r["r"] else 0.8


def _prescribers(conn, specialties: list[str], states: list[str]):
    rows = conn.execute("SELECT * FROM prescribers WHERE is_test=0 AND active=1 AND verified=1").fetchall()
    return [p for p in rows if (not specialties or p["specialty"] in specialties) and (not states or p["state"] in states)]


def forecast(conn, *, specialties: list[str], states: list[str], slots: list[str], freq_cap_week: int,
             contract_type: str, cpm: float | None, fee_per_prescriber_month: float | None,
             start_date: str, end_date: str, brand_id: str | None = None) -> dict:
    pol = policy(conn)
    enabled = [s for s in slots if s in pol["enabled_slots"] and catalog.SLOTS.get(s, {}).get("counted")]
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    days = max(1, (end - start).days + 1)
    weeks = days / 7
    pres = _prescribers(conn, specialties, states)

    held_by_other = 0
    if brand_id:
        brand = conn.execute("SELECT * FROM brands WHERE id=?", (brand_id,)).fetchone()
        if brand:
            locked = {
                r["office_id"] for r in conn.execute(
                    "SELECT office_id FROM category_locks WHERE category=? AND brand_id!=? AND until>=?",
                    (brand["category"], brand_id, start.isoformat()),
                )
            }
            before = len(pres)
            pres = [p for p in pres if p["office_id"] not in locked]
            held_by_other = before - len(pres)

    cap = max(1, min(7, int(freq_cap_week or 3)))
    opportunities = sum(p["visits_per_month"] / catalog.WEEKS_PER_MONTH * len(enabled) for p in pres) * weeks
    sellable = sum(min(cap, p["visits_per_month"] / catalog.WEEKS_PER_MONTH * len(enabled)) for p in pres) * weeks
    viewability = _network_viewability(conn)
    rendered = sellable * catalog.FORECAST_RENDER_RATE
    viewable = rendered * viewability
    if contract_type == "sponsorship":
        revenue = (fee_per_prescriber_month or 0) * len(pres) * days / MONTH_DAYS
    else:
        revenue = viewable / 1000 * (cpm or 0)
    return {
        "eligible_prescribers": len(pres),
        "eligible_offices": len({p["office_id"] for p in pres}),
        "held_by_other_brand": held_by_other,
        "flight_days": days,
        "slot_opportunities": round(opportunities),
        "sellable_impressions": round(sellable),
        "projected_served": round(rendered),
        "projected_viewable": round(viewable),
        "projected_revenue": round(revenue, 2),
        "effective_cpm": round(revenue / viewable * 1000, 2) if viewable else None,
        "utilization": sellable / opportunities if opportunities else None,
        "assumptions": {
            "render_rate": catalog.FORECAST_RENDER_RATE,
            "viewability": round(viewability, 3),
            "freq_cap_week": cap,
            "slots": enabled,
            "note": (f"Assumes the doctor reaches {int(catalog.FORECAST_RENDER_RATE * 100)}% of capped slot moments and "
                     f"the network's last-30-day viewability of {viewability:.0%}. Simulated network; not a delivery guarantee."),
        },
    }


def inventory(conn) -> dict:
    """Network ad load: opportunities vs sellable (cap-limited) vs sold, per prescriber per month."""
    pol = policy(conn)
    enabled = [s for s in pol["enabled_slots"] if catalog.SLOTS.get(s, {}).get("counted")]
    pres = _prescribers(conn, [], [])
    t = today()
    camps = [c for c in conn.execute("SELECT c.*, b.category FROM campaigns c JOIN brands b ON b.id=c.brand_id")
             if effective_status(c, t) == "active"]
    start = (t - timedelta(days=29)).isoformat()
    sold = {
        r["prescriber_id"]: r["n"] for r in conn.execute(
            "SELECT prescriber_id, SUM(rendered) n FROM delivery_daily WHERE traffic='valid' AND slot!='resources_tray' "
            "AND day>=? GROUP BY prescriber_id", (start,),
        )
    }
    tot_opp = tot_sell = tot_sold = 0.0
    for p in pres:
        cats = {c["category"] for c in camps
                if (not json.loads(c["specialties"]) or p["specialty"] in json.loads(c["specialties"]))
                and (not json.loads(c["states"]) or p["state"] in json.loads(c["states"]))
                and set(json.loads(c["slots"])) & set(enabled)}
        opp = p["visits_per_month"] * len(enabled)
        tot_opp += opp
        tot_sell += min(opp, len(cats) * pol["default_cap"] * catalog.WEEKS_PER_MONTH)
        tot_sold += sold.get(p["id"], 0)
    n = max(1, len(pres))
    return {
        "prescribers": len(pres),
        "enabled_slots": enabled,
        "default_cap": pol["default_cap"],
        "categories_live": len({c["category"] for c in camps}),
        "per_prescriber_month": {
            "opportunities": round(tot_opp / n),
            "sellable": round(tot_sell / n),
            "sold": round(tot_sold / n),
        },
        "sell_through": tot_sold / tot_sell if tot_sell else None,
        "utilization": tot_sold / tot_opp if tot_opp else None,
        "note": "Opportunities = visits x enabled slots. Sellable = live categories x weekly cap per brand. "
                "Sold = valid served impressions in the last 30 days.",
    }
