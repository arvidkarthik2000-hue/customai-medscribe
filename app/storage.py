"""Tiny SQLite helper. Each zone owns its own database file; nothing is shared.

clinical.db  - visits, transcripts, notes, audit log   (app.clinical)
ads.db       - ad zone: inventory, campaigns, events   (app.ads)
cache.db     - drug lookups cached from RxNav/openFDA  (app.drugs)
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings

_init_lock = threading.Lock()
_initialised: set[str] = set()


def db_path(name: str):
    return settings.data_dir / f"{name}.db"


def connect(name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(name), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def tx(name: str) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success, roll back on error, always close."""
    conn = connect(name)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(name: str, ddl: str) -> None:
    """Run CREATE TABLE IF NOT EXISTS statements once per process."""
    key = f"{name}:{hash(ddl)}"
    with _init_lock:
        if key in _initialised:
            return
        with tx(name) as conn:
            conn.executescript(ddl)
        _initialised.add(key)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default: Any = None) -> Any:
    if text in (None, ""):
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default
