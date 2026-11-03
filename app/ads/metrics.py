"""One metric dictionary for every screen and export.

All numbers come from delivery_daily, which holds two sources:
- 'simulated': 30 days of generated history for the 500-office demo network
- 'live':      roll-ups written in the same transaction as each hash-chained event

Billable numbers use valid traffic only. Test accounts (every demo login) and
invalid traffic are counted separately and never billed. Spend is computed day by
day (CPM capped at the booked volume, sponsorships pro-rated per day), and every
total is the sum of its days, so totals and daily rows always reconcile.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import catalog
from .serving import effective_status

DEFINITIONS = [
    ("served", "Served impressions", "A unit actually rendered on screen, not just requested (MRC counting rules). Valid traffic only."),
    ("viewable", "Viewable impressions", "At least 50% of the unit's pixels on screen for 1 continuous second, with the app in the foreground."),
    ("viewability", "Viewability rate", "Viewable impressions divided by served impressions."),
    ("reach", "Unique prescribers reached", "Distinct prescribers (pseudo-NPIs) with at least one viewable impression. Active, verified prescribers only."),
    ("frequency", "Frequency", "Viewable impressions per reached prescriber, as an average and a distribution. Capped at 3 per brand per prescriber per week."),
    ("clicks", "Prescribing-information opens", "Clicks on the unit's prescribing-information link."),
    ("isi_opens", "Safety-information opens", "Times the important safety information inside a unit was expanded."),
    ("time_in_view", "Time in view", "Average seconds a viewable unit stayed at least 50% on screen with the tab visible."),
    ("hides", "Hides", "Times a doctor chose Hide. The brand is not shown to that prescriber for 30 days. Reported to the sponsor."),
    ("ctr", "Click-through rate", "Prescribing-information opens divided by viewable impressions. Shown for completeness; it misleads for HCP media."),
    ("ivt", "Invalid traffic removed", "Duplicates, background tabs, automated clients, viewable without a render, stale impressions. Excluded from billing."),
    ("test", "Test traffic", "Test and staff accounts, including every demo login. Logged and shown separately, never billed."),
    ("spend", "Spend", "CPM: viewable impressions / 1,000 x contracted CPM, capped at the booked volume. Sponsorship: flat fee per prescriber per month, pro-rated by day."),
    ("ecpm", "Effective CPM", "Spend divided by viewable impressions x 1,000. How a flat sponsorship compares with CPM buys."),
    ("pacing", "Pacing", "Delivered viewable impressions divided by the volume expected to date (booked x share of flight elapsed)."),
]
METRIC_DEFS = {k: {"label": label, "definition": d} for k, label, d in DEFINITIONS}
MONTH_DAYS = 30.4


def today() -> date:
    return datetime.now(timezone.utc).date()


def period(days: int | None = 30, start: str | None = None, end: str | None = None) -> tuple[str, str]:
    t = today()
    e = date.fromisoformat(end) if end else t
    s = date.fromisoformat(start) if start else e - timedelta(days=(days or 30) - 1)
    return s.isoformat(), e.isoformat()


def campaigns(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT c.*, b.name AS brand_name, b.generic, b.category, b.company, b.boxed_warning, b.color "
        "FROM campaigns c JOIN brands b ON b.id=c.brand_id ORDER BY c.start_date DESC"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["specialties"] = json.loads(d["specialties"] or "[]")
        d["states"] = json.loads(d["states"] or "[]")
        d["slots"] = json.loads(d["slots"] or "[]")
        d["effective_status"] = effective_status(r)
        out.append(d)
    return out


def _empty() -> dict:
    return {"served": 0, "viewable": 0, "clicks": 0, "isi_opens": 0, "hides": 0, "dwell_ms": 0}


def _add(acc: dict, r) -> None:
    acc["served"] += r["rendered"]
    acc["viewable"] += r["viewable"]
    acc["clicks"] += r["clicks"]
    acc["isi_opens"] += r["isi_opens"]
    acc["hides"] += r["hides"]
    acc["dwell_ms"] += r["dwell_ms"]


def daily_rows(conn, campaign: dict, start: str | None = None, end: str | None = None) -> list[dict]:
    """Per-day metrics for one campaign across its whole flight to date (spend needs the
    running total), then filtered to [start, end]."""
    t = today()
    flight_start = date.fromisoformat(campaign["start_date"])
    flight_end = min(date.fromisoformat(campaign["end_date"]), t)
    if flight_end < flight_start:
        return []
    by_day: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT day, traffic, SUM(rendered) rendered, SUM(viewable) viewable, SUM(clicks) clicks, SUM(isi_opens) isi_opens, "
        "SUM(hides) hides, SUM(dwell_ms) dwell_ms FROM delivery_daily WHERE campaign_id=? AND slot!='resources_tray' "
        "GROUP BY day, traffic",
        (campaign["id"],),
    ):
        day = by_day.setdefault(r["day"], {"valid": _empty(), "test": _empty(), "ivt": _empty()})
        _add(day[r["traffic"]], r)
    tray_clicks = {
        r["day"]: r["n"] for r in conn.execute(
            "SELECT day, SUM(clicks) n FROM delivery_daily WHERE campaign_id=? AND slot='resources_tray' AND traffic='valid' GROUP BY day",
            (campaign["id"],),
        )
    }
    rows, cum_viewable = [], 0
    booked = campaign.get("booked_viewable") or 0
    d = flight_start
    while d <= flight_end:
        key = d.isoformat()
        day = by_day.get(key, {"valid": _empty(), "test": _empty(), "ivt": _empty()})
        v = day["valid"]
        if campaign["contract_type"] == "sponsorship":
            spend = (campaign.get("fee_per_prescriber_month") or 0) * (campaign.get("sponsored_prescribers") or 0) / MONTH_DAYS
        else:
            billable = v["viewable"] if not booked else max(0, min(v["viewable"], booked - cum_viewable))
            spend = billable / 1000 * (campaign.get("cpm") or 0)
        cum_viewable += v["viewable"]
        rows.append({
            "date": key, **v, "resource_opens": tray_clicks.get(key, 0),
            "ivt_removed": day["ivt"]["served"], "test_excluded": day["test"]["served"],
            "test_viewable": day["test"]["viewable"], "spend": round(spend, 2),
        })
        d += timedelta(days=1)
    lo, hi = start or "0000-00-00", end or "9999-12-31"
    return [r for r in rows if lo <= r["date"] <= hi]


def summarize(rows: list[dict]) -> dict:
    tot = _empty()
    ivt = test = test_viewable = resource_opens = 0
    spend = 0.0
    for r in rows:
        for k in tot:
            tot[k] += r[k]
        ivt += r["ivt_removed"]
        test += r["test_excluded"]
        test_viewable += r["test_viewable"]
        resource_opens += r.get("resource_opens", 0)
        spend += r["spend"]
    served, viewable = tot["served"], tot["viewable"]
    return {
        "served": served,
        "viewable": viewable,
        "viewability": viewable / served if served else None,
        "clicks": tot["clicks"],
        "isi_opens": tot["isi_opens"],
        "hides": tot["hides"],
        "resource_opens": resource_opens,
        "time_in_view_s": tot["dwell_ms"] / viewable / 1000 if viewable else None,
        "ctr": tot["clicks"] / viewable if viewable else None,
        "ivt_removed": ivt,
        "ivt_rate": ivt / (served + ivt) if (served + ivt) else None,
        "test_excluded": test,
        "test_viewable": test_viewable,
        "spend": round(spend, 2),
        "ecpm": spend / viewable * 1000 if viewable else None,
    }


def _where_scope(campaign_ids: list[str] | None, start: str, end: str) -> tuple[str, list]:
    where = "traffic='valid' AND slot!='resources_tray' AND day>=? AND day<=?"
    params: list = [start, end]
    if campaign_ids is not None:
        where += f" AND campaign_id IN ({','.join('?' * len(campaign_ids)) or 'NULL'})"
        params += campaign_ids
    return where, params


def audience(conn, campaign_ids: list[str] | None, start: str, end: str) -> dict:
    where, params = _where_scope(campaign_ids, start, end)
    per = conn.execute(
        f"SELECT prescriber_id, SUM(viewable) v FROM delivery_daily WHERE {where} GROUP BY prescriber_id HAVING SUM(viewable)>0",
        params,
    ).fetchall()
    if campaign_ids is not None and len(campaign_ids) == 1:
        # One brand: capped at 3 a week, so about 13 a month at most.
        buckets = [("1", 1, 1), ("2", 2, 2), ("3", 3, 3), ("4–6", 4, 6), ("7–9", 7, 9), ("10–13", 10, 13), ("14+", 14, 10**9)]
    else:
        # Whole network: every brand a prescriber saw, added together.
        buckets = [("1–5", 1, 5), ("6–10", 6, 10), ("11–20", 11, 20), ("21–30", 21, 30), ("31–45", 31, 45), ("46+", 46, 10**9)]
    dist = [{"bucket": b, "prescribers": sum(1 for r in per if lo <= r["v"] <= hi)} for b, lo, hi in buckets]
    reach = len(per)
    total_v = sum(r["v"] for r in per)

    def split(col: str, label_fn) -> list[dict]:
        rows = conn.execute(
            f"SELECT {col} k, SUM(rendered) served, SUM(viewable) viewable, COUNT(DISTINCT CASE WHEN viewable>0 THEN prescriber_id END) reach "
            f"FROM delivery_daily WHERE {where} GROUP BY {col} ORDER BY viewable DESC",
            params,
        ).fetchall()
        return [{"key": r["k"], "label": label_fn(r["k"]), "served": r["served"], "viewable": r["viewable"], "reach": r["reach"]} for r in rows]

    eligible = conn.execute(
        "SELECT COUNT(*) FROM prescribers WHERE is_test=0 AND active=1 AND verified=1"
    ).fetchone()[0]
    return {
        "reach": reach,
        "frequency_avg": total_v / reach if reach else None,
        "frequency_distribution": dist,
        "by_specialty": split("specialty", catalog.specialty_label),
        "by_state": split("state", lambda s: s),
        "by_slot": split("slot", lambda s: catalog.SLOTS.get(s, {}).get("label", s)),
        "network_prescribers": eligible,
        "target_list": {"status": "pending_counsel", "note": "NPI target lists are for reach measurement only, and only if counsel clears it."},
    }


def practices(conn, campaign_ids: list[str] | None, start: str, end: str) -> dict:
    where, params = _where_scope(campaign_ids, start, end)
    rows = conn.execute(
        f"SELECT d.office_id, COUNT(DISTINCT d.day) days FROM delivery_daily d WHERE {where} AND d.rendered>0 GROUP BY d.office_id",
        params,
    ).fetchall()
    offices = {o["id"]: o for o in conn.execute("SELECT * FROM offices WHERE is_test=0")}
    active = [r for r in rows if r["office_id"] in offices]
    n_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    open_days = sum(offices[r["office_id"]]["open_days"] / 7 * n_days for r in active)
    played = sum(r["days"] for r in active)
    return {
        "network_offices": len(offices),
        "active_practices": len(active),
        "devices": sum(offices[r["office_id"]]["devices"] for r in active),
        "days_with_delivery": played,
        "share_of_open_days_played": played / open_days if open_days else None,
    }


def pacing(campaign: dict, rows_to_date: list[dict]) -> dict:
    t = today()
    start, end = date.fromisoformat(campaign["start_date"]), date.fromisoformat(campaign["end_date"])
    total_days = (end - start).days + 1
    # Complete days only: history is final through yesterday, today is still arriving.
    elapsed = max(0, min(total_days, (t - start).days))
    delivered = sum(r["viewable"] for r in rows_to_date)
    status = campaign["effective_status"]
    out = {"total_days": total_days, "elapsed_days": elapsed, "elapsed_share": elapsed / total_days,
           "delivered": delivered, "status": status}
    if campaign["contract_type"] == "sponsorship":
        out.update({"booked": None, "expected_to_date": None, "ratio": None, "state": "flat_fee"})
        return out
    booked = campaign.get("booked_viewable") or 0
    expected = booked * elapsed / total_days if booked else 0
    ratio = delivered / expected if expected else None
    if status == "scheduled":
        state = "scheduled"
    elif booked and delivered >= booked:
        state = "complete"
    elif ratio is None:
        state = "not_started"
    elif ratio < 0.95:
        state = "behind"
    elif ratio > 1.10:
        state = "ahead"
    else:
        state = "on_pace"
    out.update({"booked": booked, "expected_to_date": round(expected), "ratio": ratio,
                "delivered_share": delivered / booked if booked else None, "state": state})
    return out


def campaign_report(conn, campaign: dict, start: str | None = None, end: str | None = None) -> dict:
    """Everything a sponsor report or console campaign view shows, for one campaign.
    Default scope: the whole flight to date."""
    all_rows = daily_rows(conn, campaign)
    rows = [r for r in all_rows if (not start or r["date"] >= start) and (not end or r["date"] <= end)]
    s = start or campaign["start_date"]
    e = end or min(campaign["end_date"], today().isoformat())
    return {
        "campaign": public_campaign(campaign),
        "period": {"start": s, "end": e},
        "totals": summarize(rows),
        "pacing": pacing(campaign, all_rows),
        "daily": rows,
        "audience": audience(conn, [campaign["id"]], s, e),
        "practices": practices(conn, [campaign["id"]], s, e),
        "definitions": METRIC_DEFS,
        "simulated": True,
    }


def network_report(conn, start: str, end: str) -> dict:
    camps = campaigns(conn)
    per_campaign, by_day = [], {}
    for c in camps:
        rows = daily_rows(conn, c, start, end)
        all_rows = daily_rows(conn, c)
        per_campaign.append({**public_campaign(c), "totals": summarize(rows), "pacing": pacing(c, all_rows)})
        for r in rows:
            acc = by_day.setdefault(r["date"], {"date": r["date"], **_empty(), "resource_opens": 0, "ivt_removed": 0,
                                                "test_excluded": 0, "test_viewable": 0, "spend": 0.0})
            for k in ("served", "viewable", "clicks", "isi_opens", "hides", "dwell_ms", "resource_opens",
                      "ivt_removed", "test_excluded", "test_viewable"):
                acc[k] += r.get(k, 0)
            acc["spend"] = round(acc["spend"] + r["spend"], 2)
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    daily, d = [], s
    while d <= e:
        daily.append(by_day.get(d.isoformat(), {"date": d.isoformat(), **_empty(), "resource_opens": 0, "ivt_removed": 0,
                                                 "test_excluded": 0, "test_viewable": 0, "spend": 0.0}))
        d += timedelta(days=1)
    return {
        "period": {"start": start, "end": end},
        "totals": summarize(daily),
        "daily": daily,
        "campaigns": per_campaign,
        "audience": audience(conn, None, start, end),
        "practices": practices(conn, None, start, end),
        "definitions": METRIC_DEFS,
        "simulated": True,
    }


def public_campaign(c: dict) -> dict:
    keys = ("id", "brand_id", "brand_name", "generic", "category", "company", "name", "contract_type", "cpm",
            "fee_per_prescriber_month", "booked_viewable", "budget", "start_date", "end_date", "specialties", "states",
            "slots", "freq_cap_week", "exclusivity", "effective_status", "sponsored_prescribers", "npi_list_status", "color")
    out = {k: c.get(k) for k in keys}
    out["company"] = f"{c.get('company')} (fictional)"
    out["boxed_warning"] = bool(c.get("boxed_warning"))
    out["specialty_labels"] = [catalog.specialty_label(s) for s in c.get("specialties") or []]
    out["slot_labels"] = [catalog.SLOTS[s]["label"] for s in c.get("slots") or [] if s in catalog.SLOTS]
    return out


CSV_COLUMNS = [
    "date", "campaign_id", "campaign", "brand", "served_impressions", "viewable_impressions", "viewability_rate",
    "pi_opens", "safety_info_opens", "hides", "avg_time_in_view_s", "invalid_traffic_removed", "test_traffic_excluded",
    "spend_usd", "data_status",
]


def csv_rows(campaign: dict, rows: list[dict]) -> list[list]:
    """Day-level rows only: no times of day, no prescriber or visit identifiers."""
    out = []
    for r in rows:
        out.append([
            r["date"], campaign["id"], campaign["name"], campaign["brand_name"], r["served"], r["viewable"],
            f"{r['viewable'] / r['served']:.4f}" if r["served"] else "",
            r["clicks"], r["isi_opens"], r["hides"],
            f"{r['dwell_ms'] / r['viewable'] / 1000:.1f}" if r["viewable"] else "",
            r["ivt_removed"], r["test_excluded"], f"{r['spend']:.2f}", "SIMULATED",
        ])
    return out
