"""Ad zone storage: its own SQLite file, never shared with the clinical zone."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .. import storage

# Tests point this at a throwaway database name.
DB = "ads"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS offices (
  id TEXT PRIMARY KEY, label TEXT, specialty TEXT, state TEXT, city TEXT,
  devices INTEGER, open_days INTEGER, is_test INTEGER DEFAULT 0, active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS prescribers (
  id TEXT PRIMARY KEY, npi TEXT UNIQUE, office_id TEXT, specialty TEXT, state TEXT,
  visits_per_month INTEGER, is_test INTEGER DEFAULT 0, verified INTEGER DEFAULT 1, active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_presc_office ON prescribers(office_id);

CREATE TABLE IF NOT EXISTS brands (
  id TEXT PRIMARY KEY, name TEXT, generic TEXT, category TEXT, company TEXT,
  boxed_warning INTEGER, color TEXT, motif TEXT, indication TEXT, claim TEXT, risks TEXT
);

CREATE TABLE IF NOT EXISTS creatives (
  id TEXT PRIMARY KEY, brand_id TEXT, version INTEGER, format TEXT, slots TEXT,
  headline TEXT, body TEXT, risk_text TEXT, cta_label TEXT,
  mlr_id TEXT, mlr_expires TEXT, approver TEXT, attested_at TEXT, status TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_creative_brand ON creatives(brand_id);

CREATE TABLE IF NOT EXISTS campaigns (
  id TEXT PRIMARY KEY, brand_id TEXT, name TEXT, contract_type TEXT,
  cpm REAL, fee_per_prescriber_month REAL, booked_viewable INTEGER, budget REAL,
  start_date TEXT, end_date TEXT, specialties TEXT, states TEXT, slots TEXT,
  freq_cap_week INTEGER, exclusivity INTEGER, status TEXT, share_token TEXT UNIQUE,
  sponsored_prescribers INTEGER, npi_list_status TEXT, created_at TEXT, created_by TEXT
);

CREATE TABLE IF NOT EXISTS changelog (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, actor TEXT, entity TEXT, entity_id TEXT,
  action TEXT, detail TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
  impression_id TEXT PRIMARY KEY, at TEXT, day TEXT, slot TEXT, campaign_id TEXT, creative_id TEXT,
  brand_id TEXT, category TEXT, prescriber_id TEXT, office_id TEXT, specialty TEXT, state TEXT,
  hour INTEGER, is_test INTEGER
);
CREATE INDEX IF NOT EXISTS ix_dec_presc ON decisions(prescriber_id, brand_id, at);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, at TEXT, day TEXT, type TEXT,
  impression_id TEXT, campaign_id TEXT, creative_id TEXT, brand_id TEXT, slot TEXT,
  prescriber_id TEXT, office_id TEXT, specialty TEXT, state TEXT, hour INTEGER,
  dwell_ms INTEGER, traffic TEXT, reason TEXT, prev_hash TEXT, hash TEXT
);
CREATE INDEX IF NOT EXISTS ix_ev_imp ON events(impression_id, type);
CREATE INDEX IF NOT EXISTS ix_ev_presc ON events(prescriber_id, brand_id, type, at);

CREATE TABLE IF NOT EXISTS delivery_daily (
  day TEXT, campaign_id TEXT, creative_id TEXT, brand_id TEXT, prescriber_id TEXT, office_id TEXT,
  specialty TEXT, state TEXT, slot TEXT, source TEXT, traffic TEXT,
  rendered INTEGER DEFAULT 0, viewable INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0,
  isi_opens INTEGER DEFAULT 0, hides INTEGER DEFAULT 0, dwell_ms INTEGER DEFAULT 0,
  PRIMARY KEY (day, campaign_id, creative_id, prescriber_id, slot, source, traffic)
);
CREATE INDEX IF NOT EXISTS ix_dd_campaign ON delivery_daily(campaign_id, day);
CREATE INDEX IF NOT EXISTS ix_dd_presc ON delivery_daily(prescriber_id, brand_id, day);

CREATE TABLE IF NOT EXISTS hides (
  prescriber_id TEXT, brand_id TEXT, creative_id TEXT, at TEXT, until TEXT,
  PRIMARY KEY (prescriber_id, brand_id)
);

CREATE TABLE IF NOT EXISTS category_locks (
  office_id TEXT, category TEXT, brand_id TEXT, campaign_id TEXT, until TEXT,
  PRIMARY KEY (office_id, category)
);

CREATE TABLE IF NOT EXISTS network_policy (key TEXT PRIMARY KEY, value TEXT);
"""

_lock = threading.RLock()       # serialises seeding and event-chain appends
_ready_for: set[str] = set()


def connect() -> sqlite3.Connection:
    return storage.connect(DB)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with storage.tx(DB) as conn:
        yield conn


def chain_lock() -> threading.RLock:
    return _lock


def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def ensure_ready() -> None:
    """Create tables, seed the simulated network once, and roll history forward to yesterday.

    Mounted sub-apps do not get startup events, so this runs on the first request
    (and is cheap afterwards)."""
    if DB in _ready_for:
        return
    with _lock:
        if DB in _ready_for:
            return
        with tx() as conn:
            conn.executescript(SCHEMA)
        from . import seed  # local import: seed imports this module

        seed.ensure_seeded()
        _ready_for.add(DB)


def reset_for_tests(name: str) -> None:
    """Point the ad zone at a fresh database (tests only)."""
    global DB
    DB = name
    _ready_for.discard(name)
