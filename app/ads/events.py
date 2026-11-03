"""Append-only, hash-chained event log and the invalid-traffic rules.

Every event row stores prev_hash and hash = sha256(prev_hash + canonical JSON of
the row's fields). Changing, deleting or reordering any row breaks the chain from
that row on, which verify_chain() reports.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import db

EVENT_TYPES = ("rendered", "viewable", "click", "isi_open", "hide", "dwell")
ONCE_PER_IMPRESSION = {"rendered", "viewable", "isi_open", "hide", "dwell"}
GENESIS = "0" * 64
HASH_FIELDS = (
    "event_id", "at", "day", "type", "impression_id", "campaign_id", "creative_id", "brand_id", "slot",
    "prescriber_id", "office_id", "specialty", "state", "hour", "dwell_ms", "traffic", "reason",
)
BOT_UA = re.compile(r"bot|crawler|spider|headless|phantom|selenium|puppeteer|playwright|python-requests|curl|wget|httpx|go-http", re.I)
STALE_AFTER = timedelta(hours=6)
MAX_DWELL_MS = 10 * 60 * 1000

COLUMN_FOR = {"rendered": "rendered", "viewable": "viewable", "click": "clicks", "isi_open": "isi_opens", "hide": "hides"}


def canonical(fields: dict) -> str:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, fields: dict) -> str:
    return hashlib.sha256((prev_hash + canonical(fields)).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _exists(conn, impression_id: str, etype: str, traffic: str | None = None) -> bool:
    q = "SELECT 1 FROM events WHERE impression_id=? AND type=?"
    params: list = [impression_id, etype]
    if traffic:
        q += " AND traffic=?"
        params.append(traffic)
    return conn.execute(q + " LIMIT 1", params).fetchone() is not None


def classify(conn, decision, etype: str, *, visible: bool | None, webdriver: bool, user_agent: str, now: datetime):
    """Return (traffic, reason, counted). traffic is valid | test | ivt.

    Test accounts (every demo login) are always 'test' and never billed. Duplicate
    test events are logged but not counted, so test totals stay meaningful."""
    duplicate = etype in ONCE_PER_IMPRESSION and _exists(conn, decision["impression_id"], etype)
    if decision["is_test"]:
        return ("test", "test_account; duplicate" if duplicate else "test_account", not duplicate)
    if webdriver or not user_agent or BOT_UA.search(user_agent):
        return ("ivt", "automated_client", True)
    if etype in ("rendered", "viewable") and visible is False:
        return ("ivt", "background_tab", True)
    if duplicate:
        return ("ivt", "duplicate", True)
    if etype == "viewable" and not _exists(conn, decision["impression_id"], "rendered", "valid"):
        return ("ivt", "viewable_without_render", True)
    if etype == "click":
        recent = conn.execute(
            "SELECT 1 FROM events WHERE impression_id=? AND type='click' AND at>=? LIMIT 1",
            (decision["impression_id"], (now - timedelta(seconds=2)).isoformat(timespec="milliseconds")),
        ).fetchone()
        if recent:
            return ("ivt", "duplicate_click", True)
    if now - datetime.fromisoformat(decision["at"]) > STALE_AFTER:
        return ("ivt", "stale_impression", True)
    return ("valid", "", True)


def record(impression_id: str, etype: str, *, dwell_ms: int | None = None, visible: bool | None = None,
           webdriver: bool = False, user_agent: str = "") -> dict:
    """Validate, classify, chain and roll up one event. Raises LookupError for an unknown impression."""
    if etype not in EVENT_TYPES:
        raise ValueError("unknown event type")
    with db.chain_lock():
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            decision = conn.execute("SELECT * FROM decisions WHERE impression_id=?", (impression_id,)).fetchone()
            if decision is None:
                raise LookupError("unknown impression")
            now = _now()
            traffic, reason, counted = classify(
                conn, decision, etype, visible=visible, webdriver=webdriver, user_agent=user_agent, now=now,
            )
            dwell = max(0, min(int(dwell_ms or 0), MAX_DWELL_MS)) if etype == "dwell" else 0
            fields = {
                "event_id": f"ev_{uuid.uuid4().hex}",
                "at": now.isoformat(timespec="milliseconds"),
                "day": now.date().isoformat(),
                "type": etype,
                "impression_id": impression_id,
                "campaign_id": decision["campaign_id"],
                "creative_id": decision["creative_id"],
                "brand_id": decision["brand_id"],
                "slot": decision["slot"],
                "prescriber_id": decision["prescriber_id"],
                "office_id": decision["office_id"],
                "specialty": decision["specialty"],
                "state": decision["state"],
                "hour": decision["hour"],
                "dwell_ms": dwell,
                "traffic": traffic,
                "reason": reason,
            }
            head = conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = head["hash"] if head else GENESIS
            digest = compute_hash(prev, fields)
            conn.execute(
                f"INSERT INTO events ({', '.join(HASH_FIELDS)}, prev_hash, hash) VALUES ({', '.join('?' * (len(HASH_FIELDS) + 2))})",
                [fields[k] for k in HASH_FIELDS] + [prev, digest],
            )
            if counted:
                _roll_up(conn, fields, etype, dwell)
            if etype == "hide" and traffic in ("valid", "test"):
                conn.execute(
                    "INSERT INTO hides VALUES (?,?,?,?,?) ON CONFLICT(prescriber_id, brand_id) DO UPDATE SET "
                    "creative_id=excluded.creative_id, at=excluded.at, until=excluded.until",
                    (decision["prescriber_id"], decision["brand_id"], decision["creative_id"], fields["at"],
                     (now + timedelta(days=30)).isoformat(timespec="seconds")),
                )
            conn.commit()
            return {"ok": True, "traffic": traffic, "reason": reason, "seq": conn.execute("SELECT MAX(seq) FROM events").fetchone()[0]}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _roll_up(conn, f: dict, etype: str, dwell: int) -> None:
    cols = {"rendered": 0, "viewable": 0, "clicks": 0, "isi_opens": 0, "hides": 0, "dwell_ms": dwell}
    if etype in COLUMN_FOR:
        cols[COLUMN_FOR[etype]] = 1
    conn.execute(
        "INSERT INTO delivery_daily (day, campaign_id, creative_id, brand_id, prescriber_id, office_id, specialty, state, slot, "
        "source, traffic, rendered, viewable, clicks, isi_opens, hides, dwell_ms) VALUES (?,?,?,?,?,?,?,?,?,'live',?,?,?,?,?,?,?) "
        "ON CONFLICT(day, campaign_id, creative_id, prescriber_id, slot, source, traffic) DO UPDATE SET "
        "rendered=rendered+excluded.rendered, viewable=viewable+excluded.viewable, clicks=clicks+excluded.clicks, "
        "isi_opens=isi_opens+excluded.isi_opens, hides=hides+excluded.hides, dwell_ms=dwell_ms+excluded.dwell_ms",
        (f["day"], f["campaign_id"], f["creative_id"], f["brand_id"], f["prescriber_id"], f["office_id"], f["specialty"],
         f["state"], f["slot"], f["traffic"], cols["rendered"], cols["viewable"], cols["clicks"], cols["isi_opens"],
         cols["hides"], cols["dwell_ms"]),
    )


def verify_chain(conn) -> dict:
    prev = GENESIS
    checked = 0
    for row in conn.execute("SELECT * FROM events ORDER BY seq"):
        if row["prev_hash"] != prev:
            return {"ok": False, "checked": checked, "broken_at": row["seq"], "reason": "Link to the previous event does not match"}
        digest = compute_hash(prev, {k: row[k] for k in HASH_FIELDS})
        if digest != row["hash"]:
            return {"ok": False, "checked": checked, "broken_at": row["seq"], "reason": "Event contents were changed after logging"}
        prev = digest
        checked += 1
    return {"ok": True, "checked": checked, "head": prev}


def reconcile(conn) -> dict:
    """Recompute live roll-ups from the event log and compare with the stored roll-ups."""
    from_log: dict[tuple, list[int]] = {}
    for e in conn.execute("SELECT * FROM events"):
        if e["traffic"] == "test" and "duplicate" in (e["reason"] or ""):
            continue
        key = (e["day"], e["campaign_id"], e["creative_id"], e["prescriber_id"], e["slot"], e["traffic"])
        cur = from_log.setdefault(key, [0, 0, 0, 0, 0, 0])
        idx = {"rendered": 0, "viewable": 1, "click": 2, "isi_open": 3, "hide": 4}.get(e["type"])
        if idx is not None:
            cur[idx] += 1
        cur[5] += e["dwell_ms"] or 0
    stored = {}
    for r in conn.execute("SELECT * FROM delivery_daily WHERE source='live'"):
        key = (r["day"], r["campaign_id"], r["creative_id"], r["prescriber_id"], r["slot"], r["traffic"])
        stored[key] = [r["rendered"], r["viewable"], r["clicks"], r["isi_opens"], r["hides"], r["dwell_ms"]]
    mismatched = [k for k in set(from_log) | set(stored) if from_log.get(k, [0] * 6) != stored.get(k, [0] * 6)]
    return {"ok": not mismatched, "rows_checked": len(stored), "mismatched": len(mismatched)}


def fingerprint(conn) -> dict:
    row = conn.execute("SELECT COUNT(*) n, MIN(at) first_at, MAX(at) last_at FROM events").fetchone()
    head = conn.execute("SELECT hash, seq FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    return {
        "events": row["n"],
        "first_at": row["first_at"],
        "last_at": row["last_at"],
        "head_hash": head["hash"] if head else GENESIS,
        "head_seq": head["seq"] if head else 0,
        "algorithm": "SHA-256 over previous hash + canonical JSON of each event",
    }
