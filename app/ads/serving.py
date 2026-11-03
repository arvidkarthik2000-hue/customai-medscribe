"""Choosing a unit for a slot. Inputs are only: slot, prescriber id, declared
specialty and state, and hour of day. Nothing else exists in this zone."""
from __future__ import annotations

import json
import random
import uuid
from datetime import date, datetime, timedelta, timezone

from . import catalog, db
from .creative_art import brand_art

PENDING_WINDOW = timedelta(minutes=5)


def today() -> date:
    return datetime.now(timezone.utc).date()


def effective_status(c, t: date | None = None) -> str:
    t = t or today()
    if c["status"] in ("paused", "draft"):
        return c["status"]
    start, end = date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"])
    if t < start:
        return "scheduled"
    if t > end:
        return "ended"
    return "active"


def policy(conn) -> dict:
    out = {"enabled_slots": ["schedule_card", "drafting_wait", "post_sign"], "default_cap": 3}
    for r in conn.execute("SELECT key, value FROM network_policy"):
        out[r["key"]] = json.loads(r["value"])
    return out


def get_or_register_prescriber(conn, prescriber_id: str, specialty: str, state: str):
    p = conn.execute("SELECT * FROM prescribers WHERE id=?", (prescriber_id,)).fetchone()
    if p is None and prescriber_id.startswith("DEMO-"):
        # Any demo login is a test account in the test office, so it is never billed.
        conn.execute(
            "INSERT OR IGNORE INTO prescribers VALUES (?,?,?,?,?,?,?,?,?)",
            (prescriber_id, None, catalog.TEST_OFFICE_ID, specialty, state, 420, 1, 1, 1),
        )
        p = conn.execute("SELECT * FROM prescribers WHERE id=?", (prescriber_id,)).fetchone()
    return p


def cap_used(conn, prescriber, brand_id: str, now: datetime) -> int:
    """Rendered units of this brand for this prescriber in the last 7 days, plus
    decisions in the last few minutes that have not rendered yet."""
    week_ago = now - timedelta(days=7)
    if prescriber["is_test"]:
        reset = db.meta_get(conn, "test_cap_reset_at") or "1970-01-01T00:00:00+00:00"
        since = max(week_ago, datetime.fromisoformat(reset)).isoformat(timespec="milliseconds")
        used = conn.execute(
            "SELECT COUNT(*) FROM events WHERE prescriber_id=? AND brand_id=? AND type='rendered' AND at>=? "
            "AND traffic != 'ivt' AND reason NOT LIKE '%duplicate%'",
            (prescriber["id"], brand_id, since),
        ).fetchone()[0]
    else:
        since_day = (now.date() - timedelta(days=6)).isoformat()
        used = conn.execute(
            "SELECT COALESCE(SUM(rendered),0) FROM delivery_daily WHERE prescriber_id=? AND brand_id=? AND day>=? "
            "AND traffic IN ('valid','test')",
            (prescriber["id"], brand_id, since_day),
        ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM decisions d WHERE d.prescriber_id=? AND d.brand_id=? AND d.at>=? AND d.slot!='resources_tray' "
        "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.impression_id=d.impression_id AND e.type='rendered')",
        (prescriber["id"], brand_id, (now - PENDING_WINDOW).isoformat(timespec="milliseconds")),
    ).fetchone()[0]
    return int(used) + int(pending)


def delivered_viewable(conn, campaign_id: str) -> int:
    return int(conn.execute(
        "SELECT COALESCE(SUM(viewable),0) FROM delivery_daily WHERE campaign_id=? AND traffic='valid'", (campaign_id,)
    ).fetchone()[0])


def pacing_need(conn, c, t: date) -> float:
    if c["contract_type"] == "sponsorship" or not c["booked_viewable"]:
        return 1.0
    start, end = date.fromisoformat(c["start_date"]), date.fromisoformat(c["end_date"])
    total = (end - start).days + 1
    elapsed = max(1, min(total, (t - start).days))
    expected = c["booked_viewable"] * elapsed / total
    return max(0.2, min(3.0, expected / max(1.0, delivered_viewable(conn, c["id"]))))


def eligible_creatives(conn, brand, slot: str, t: date) -> list:
    out = []
    for cr in conn.execute("SELECT * FROM creatives WHERE brand_id=? AND status='approved'", (brand["id"],)):
        if slot not in json.loads(cr["slots"]):
            continue
        if cr["mlr_expires"] < t.isoformat():
            continue
        if cr["format"] not in catalog.SLOTS[slot]["formats"]:
            continue
        if cr["format"] == "reminder" and brand["boxed_warning"]:
            continue  # reminder ads are not allowed for drugs with a boxed warning
        out.append(cr)
    return out


def unit_payload(brand, cr, slot: str, specialty: str, state: str) -> dict:
    return {
        "creative_id": cr["id"],
        "version": cr["version"],
        "format": cr["format"],
        "brand": brand["name"],
        "generic": brand["generic"],
        "company": f"{brand['company']} (fictional)",
        "category": brand["category"],
        "headline": cr["headline"],
        "body": cr["body"],
        "risk_text": cr["risk_text"],
        "boxed_warning": bool(brand["boxed_warning"]),
        "cta_label": cr["cta_label"],
        "pi_path": f"/pi/{brand['id']}",
        "art_svg": brand_art(brand["name"], brand["generic"], brand["color"], brand["motif"]),
        "color": brand["color"],
        "mlr_id": cr["mlr_id"],
        "sponsored_label": catalog.SPONSORED_LABEL,
        "sample_label": catalog.SAMPLE_LABEL,
        "why": {
            "slot": catalog.SLOTS[slot]["label"],
            "specialty": catalog.specialty_label(specialty),
            "state": state,
            "text": catalog.WHY_TEXT,
        },
    }


def _candidates(conn, prescriber, slot: str, specialty: str, state: str, now: datetime) -> list:
    t = now.date()
    out = []
    rows = conn.execute("SELECT c.*, b.category, b.boxed_warning FROM campaigns c JOIN brands b ON b.id=c.brand_id").fetchall()
    for c in rows:
        if effective_status(c, t) != "active":
            continue
        if slot != "resources_tray" and slot not in json.loads(c["slots"]):
            continue  # the tray lists every active, matching brand's clinician resources
        specs, states = json.loads(c["specialties"] or "[]"), json.loads(c["states"] or "[]")
        if specs and specialty not in specs:
            continue
        if states and state not in states:
            continue
        hidden = conn.execute(
            "SELECT 1 FROM hides WHERE prescriber_id=? AND brand_id=? AND until>=?",
            (prescriber["id"], c["brand_id"], now.isoformat(timespec="seconds")),
        ).fetchone()
        if hidden:
            continue
        lock = conn.execute(
            "SELECT * FROM category_locks WHERE office_id=? AND category=?", (prescriber["office_id"], c["category"])
        ).fetchone()
        if lock and lock["brand_id"] != c["brand_id"] and lock["until"] >= t.isoformat():
            continue  # one brand per therapeutic category per office at a time
        if slot != "resources_tray":
            if cap_used(conn, prescriber, c["brand_id"], now) >= (c["freq_cap_week"] or 3):
                continue
            if c["contract_type"] == "cpm" and c["booked_viewable"] and delivered_viewable(conn, c["id"]) >= c["booked_viewable"]:
                continue  # fully delivered
        brand = conn.execute("SELECT * FROM brands WHERE id=?", (c["brand_id"],)).fetchone()
        creatives = eligible_creatives(conn, brand, slot, t)
        if not creatives:
            continue
        out.append((c, brand, creatives))
    return out


def _record_decision(conn, impression_id, now, slot, c, brand, cr, prescriber, specialty, state, hour) -> None:
    conn.execute(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (impression_id, now.isoformat(timespec="milliseconds"), now.date().isoformat(), slot, c["id"], cr["id"],
         brand["id"], brand["category"], prescriber["id"], prescriber["office_id"], specialty, state, hour,
         int(prescriber["is_test"])),
    )


def serve(slot: str, prescriber_id: str, specialty: str, state: str, hour: int) -> tuple[dict | None, str]:
    """Return (response, reason). response is None when nothing is eligible."""
    now = datetime.now(timezone.utc)
    with db.chain_lock(), db.tx() as conn:
        pol = policy(conn)
        if slot not in pol["enabled_slots"]:
            return None, "slot_disabled"
        prescriber = get_or_register_prescriber(conn, prescriber_id, specialty, state)
        if prescriber is None or not prescriber["active"]:
            return None, "unknown_prescriber"
        cands = _candidates(conn, prescriber, slot, specialty, state, now)
        if not cands:
            return None, "no_eligible_unit"
        weights = [pacing_need(conn, c, now.date()) for c, _, _ in cands]
        c, brand, creatives = random.choices(cands, weights)[0]
        cr = random.choice(creatives)
        lock = conn.execute(
            "SELECT * FROM category_locks WHERE office_id=? AND category=?", (prescriber["office_id"], brand["category"])
        ).fetchone()
        if lock is None or lock["until"] < now.date().isoformat():
            conn.execute(
                "INSERT INTO category_locks VALUES (?,?,?,?,?) ON CONFLICT(office_id, category) DO UPDATE SET "
                "brand_id=excluded.brand_id, campaign_id=excluded.campaign_id, until=excluded.until",
                (prescriber["office_id"], brand["category"], brand["id"], c["id"], c["end_date"]),
            )
        impression_id = f"imp_{uuid.uuid4().hex}"
        _record_decision(conn, impression_id, now, slot, c, brand, cr, prescriber, specialty, state, hour)
        return {
            "impression_id": impression_id,
            "slot": slot,
            "campaign_id": c["id"],
            "unit": unit_payload(brand, cr, slot, specialty, state),
            "events_path": "/events",
        }, "ok"


def tray(prescriber_id: str, specialty: str, state: str, hour: int) -> dict:
    """Doctor-initiated resources: one item per eligible brand. Not counted as served inventory."""
    now = datetime.now(timezone.utc)
    items = []
    with db.chain_lock(), db.tx() as conn:
        prescriber = get_or_register_prescriber(conn, prescriber_id, specialty, state)
        if prescriber is None:
            return {"items": [], "note": catalog.WHY_TEXT}
        for c, brand, creatives in _candidates(conn, prescriber, "resources_tray", specialty, state, now):
            cr = creatives[0]
            impression_id = f"imp_{uuid.uuid4().hex}"
            _record_decision(conn, impression_id, now, "resources_tray", c, brand, cr, prescriber, specialty, state, hour)
            items.append({
                "impression_id": impression_id,
                "brand": brand["name"],
                "generic": brand["generic"],
                "company": f"{brand['company']} (fictional)",
                "category": brand["category"],
                "title": cr["headline"],
                "description": cr["body"],
                "risk_text": cr["risk_text"],
                "pi_path": f"/pi/{brand['id']}",
                "color": brand["color"],
            })
    items.sort(key=lambda i: i["category"])
    return {
        "items": items,
        "note": catalog.WHY_TEXT,
        "sponsored_label": catalog.SPONSORED_LABEL,
        "sample_label": catalog.SAMPLE_LABEL,
    }
