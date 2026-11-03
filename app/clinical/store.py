"""clinical.db: visits, the audit log, daily usage counters and a small key-value table.

Nothing here leaves the clinical zone. JSON columns are decoded on read.
"""
from __future__ import annotations

import datetime as dt
import secrets
import threading
from collections import defaultdict
from typing import Any

from .. import storage
from . import fixtures

DB = "clinical"

DDL = """
CREATE TABLE IF NOT EXISTS visits (
  id TEXT PRIMARY KEY,
  patient_id TEXT NOT NULL,
  appt_id TEXT,
  day TEXT NOT NULL,
  status TEXT NOT NULL,
  template TEXT, style TEXT, engine TEXT, specialty TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  consent_at TEXT, signed_at TEXT,
  source TEXT, sample_id TEXT,
  audio_seconds REAL NOT NULL DEFAULT 0,
  audio_path TEXT,
  live_turns TEXT, transcript TEXT, speaker_roles TEXT,
  note TEXT, medication_check TEXT, cards_ack TEXT,
  stage TEXT, stages TEXT, error TEXT,
  timings TEXT, engine_info TEXT,
  archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS visits_day ON visits(day, patient_id);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  visit_id TEXT, at TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_visit ON audit(visit_id);
CREATE TABLE IF NOT EXISTS usage (
  day TEXT NOT NULL, kind TEXT NOT NULL, amount REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (day, kind)
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, at TEXT);
"""

JSON_FIELDS = {
    "live_turns", "transcript", "speaker_roles", "note", "medication_check",
    "cards_ack", "stages", "timings", "engine_info",
}
COLUMNS = {
    "patient_id", "appt_id", "day", "status", "template", "style", "engine", "specialty",
    "created_at", "updated_at", "consent_at", "signed_at", "source", "sample_id",
    "audio_seconds", "audio_path", "stage", "error", "archived",
} | JSON_FIELDS

_locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
_locks_guard = threading.Lock()


def visit_lock(visit_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks[visit_id]


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_day() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def init() -> None:
    storage.init_schema(DB, DDL)


def _decode(row) -> dict:
    d = dict(row)
    for k in JSON_FIELDS:
        d[k] = storage.loads(d.get(k), None)
    d["archived"] = bool(d.get("archived"))
    return d


def create_visit(*, patient_id: str, template: str, style: str, engine: str, specialty: str,
                 sample_id: str | None = None) -> dict:
    init()
    appt = fixtures.appointment_for(patient_id)
    vid = "v_" + secrets.token_hex(6)
    now = now_iso()
    with storage.tx(DB) as conn:
        conn.execute(
            "INSERT INTO visits (id, patient_id, appt_id, day, status, template, style, engine, specialty,"
            " created_at, updated_at, sample_id, cards_ack, timings, engine_info)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, patient_id, appt["appt_id"] if appt else None, fixtures.today_local().isoformat(), "draft",
             template, style, engine, specialty, now, now, sample_id, "{}", "{}", "{}"),
        )
    return get_visit(vid)


def get_visit(visit_id: str) -> dict | None:
    init()
    with storage.tx(DB) as conn:
        row = conn.execute("SELECT * FROM visits WHERE id=?", (visit_id,)).fetchone()
    return _decode(row) if row else None


def update_visit(visit_id: str, **fields: Any) -> dict | None:
    if not fields:
        return get_visit(visit_id)
    unknown = set(fields) - COLUMNS
    if unknown:
        raise ValueError(f"unknown visit fields: {sorted(unknown)}")
    sets, values = [], []
    for k, v in fields.items():
        sets.append(f"{k}=?")
        values.append(storage.dumps(v) if k in JSON_FIELDS and v is not None else v)
    sets.append("updated_at=?")
    values.append(now_iso())
    values.append(visit_id)
    with storage.tx(DB) as conn:
        conn.execute(f"UPDATE visits SET {', '.join(sets)} WHERE id=?", values)
    return get_visit(visit_id)


def visits_for_day(day: str) -> list[dict]:
    init()
    with storage.tx(DB) as conn:
        rows = conn.execute(
            "SELECT * FROM visits WHERE day=? AND archived=0 ORDER BY created_at", (day,)
        ).fetchall()
    return [_decode(r) for r in rows]


def archive_day(day: str) -> int:
    init()
    with storage.tx(DB) as conn:
        cur = conn.execute("UPDATE visits SET archived=1, updated_at=? WHERE day=? AND archived=0", (now_iso(), day))
        return cur.rowcount


def recover_interrupted() -> int:
    """Visits left 'processing' by a restart become retryable errors."""
    init()
    msg = "The server restarted while this visit was processing. Press Retry to run it again."
    with storage.tx(DB) as conn:
        rows = conn.execute("SELECT id, stages FROM visits WHERE status='processing'").fetchall()
        for r in rows:
            stages = storage.loads(r["stages"], []) or []
            for s in stages:
                if s.get("state") == "active":
                    s["state"] = "error"
                    s["detail"] = msg
            conn.execute(
                "UPDATE visits SET status='error', stage='error', error=?, stages=?, updated_at=? WHERE id=?",
                (msg, storage.dumps(stages), now_iso(), r["id"]),
            )
    return len(rows)


# ---------------------------------------------------------------- audit


def audit(visit_id: str | None, event: str, detail: dict | None = None, actor: str = "clinician") -> None:
    init()
    with storage.tx(DB) as conn:
        conn.execute(
            "INSERT INTO audit (visit_id, at, actor, event, detail) VALUES (?,?,?,?,?)",
            (visit_id, now_iso(), actor, event, storage.dumps(detail or {})),
        )


def audit_for(visit_id: str) -> list[dict]:
    init()
    with storage.tx(DB) as conn:
        rows = conn.execute("SELECT * FROM audit WHERE visit_id=? ORDER BY id", (visit_id,)).fetchall()
    return [{**dict(r), "detail": storage.loads(r["detail"], {})} for r in rows]


# ---------------------------------------------------------------- usage counters (UTC day)


def usage_get(kind: str, day: str | None = None) -> float:
    init()
    with storage.tx(DB) as conn:
        row = conn.execute("SELECT amount FROM usage WHERE day=? AND kind=?", (day or utc_day(), kind)).fetchone()
    return float(row["amount"]) if row else 0.0


def usage_add(kind: str, amount: float, day: str | None = None) -> float:
    init()
    day = day or utc_day()
    with storage.tx(DB) as conn:
        conn.execute(
            "INSERT INTO usage (day, kind, amount) VALUES (?,?,?)"
            " ON CONFLICT(day, kind) DO UPDATE SET amount = amount + excluded.amount",
            (day, kind, float(amount)),
        )
        row = conn.execute("SELECT amount FROM usage WHERE day=? AND kind=?", (day, kind)).fetchone()
    return float(row["amount"])


def usage_set(kind: str, amount: float, day: str | None = None) -> None:
    init()
    with storage.tx(DB) as conn:
        conn.execute(
            "INSERT INTO usage (day, kind, amount) VALUES (?,?,?)"
            " ON CONFLICT(day, kind) DO UPDATE SET amount = excluded.amount",
            (day or utc_day(), kind, float(amount)),
        )


# ---------------------------------------------------------------- key-value


def kv_get(key: str) -> tuple[Any, str | None]:
    init()
    with storage.tx(DB) as conn:
        row = conn.execute("SELECT v, at FROM kv WHERE k=?", (key,)).fetchone()
    if not row:
        return None, None
    return storage.loads(row["v"], None), row["at"]


def kv_set(key: str, value: Any) -> None:
    init()
    with storage.tx(DB) as conn:
        conn.execute(
            "INSERT INTO kv (k, v, at) VALUES (?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, at=excluded.at",
            (key, storage.dumps(value), now_iso()),
        )
