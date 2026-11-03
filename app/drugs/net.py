"""Outbound HTTP for drug sources: cached, time-boxed, with a per-host circuit breaker.

- Responses are cached in cache.db (7 days; empty answers 1 day).
- A host that times out or refuses is skipped for 2 minutes, so one unreachable
  source never adds more than one timeout to a check.
- DRUGS_OFFLINE=1 disables the network entirely (tests, offline demos).
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import storage

TTL_OK = 7 * 24 * 3600
TTL_EMPTY = 24 * 3600
BREAKER_S = 120

_DDL = """
CREATE TABLE IF NOT EXISTS drug_cache (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  fetched_at REAL NOT NULL,
  ttl REAL NOT NULL
);
"""

_lock = threading.Lock()
_down_until: dict[str, float] = {}
_client: httpx.Client | None = None
# Tests can inject a transport (httpx.MockTransport) here.
transport: httpx.BaseTransport | None = None


class SourceUnavailable(Exception):
    """The source could not be reached (network, timeout, 5xx, breaker open, offline)."""


def offline() -> bool:
    return os.environ.get("DRUGS_OFFLINE", "") == "1"


def _get_client() -> httpx.Client:
    global _client
    with _lock:
        if _client is None or (transport is not None and getattr(_client, "_ms_transport", None) is not transport):
            kwargs: dict[str, Any] = {"timeout": 8.0, "headers": {"User-Agent": "CustomAi-MedScribe-demo/0.1", "Accept": "application/json"}, "follow_redirects": True}
            if transport is not None:
                kwargs["transport"] = transport
            _client = httpx.Client(**kwargs)
            _client._ms_transport = transport  # type: ignore[attr-defined]
        return _client


def reset() -> None:
    """Forget breaker state and the shared client (tests)."""
    global _client
    with _lock:
        _down_until.clear()
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None


def clear_cache() -> None:
    storage.init_schema("cache", _DDL)
    with storage.tx("cache") as conn:
        conn.execute("DELETE FROM drug_cache")


def _cache_get(key: str) -> Any:
    storage.init_schema("cache", _DDL)
    with storage.tx("cache") as conn:
        row = conn.execute("SELECT value, fetched_at, ttl FROM drug_cache WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    if time.time() - row["fetched_at"] > row["ttl"]:
        return None
    return json.loads(row["value"])


def _cache_put(key: str, value: Any, ttl: float) -> None:
    storage.init_schema("cache", _DDL)
    with storage.tx("cache") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO drug_cache(key, value, fetched_at, ttl) VALUES (?,?,?,?)",
            (key, json.dumps(value), time.time(), ttl),
        )


def get_json(url: str, params: dict | None = None, *, cache_key: str, timeout: float = 8.0, not_found_ok: bool = False) -> Any:
    """GET JSON with cache + breaker. Returns parsed JSON, or {} for a cached/real 404 when not_found_ok.

    Raises SourceUnavailable when the source cannot be used right now.
    """
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    if offline():
        raise SourceUnavailable("offline mode")
    host = urlsplit(url).netloc
    if _down_until.get(host, 0) > time.monotonic():
        raise SourceUnavailable(f"{host} recently unreachable")
    timeout = max(0.5, min(8.0, timeout))
    try:
        resp = _get_client().get(url, params=params, timeout=timeout)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        _down_until[host] = time.monotonic() + BREAKER_S
        raise SourceUnavailable(f"{host}: {type(exc).__name__}") from exc
    if resp.status_code == 404 and not_found_ok:
        _cache_put(cache_key, {}, TTL_EMPTY)
        return {}
    if resp.status_code == 429 or resp.status_code >= 500:
        _down_until[host] = time.monotonic() + BREAKER_S
        raise SourceUnavailable(f"{host}: HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise SourceUnavailable(f"{host}: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise SourceUnavailable(f"{host}: not JSON") from exc
    _cache_put(cache_key, data, TTL_OK if data else TTL_EMPTY)
    return data


class SourceTracker:
    """Collects per-source status during one check, for sources_checked."""

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}
        self._lock = threading.Lock()

    def ok(self, name: str) -> None:
        with self._lock:
            s = self._state.setdefault(name, {"ok": 0, "fail": 0, "detail": ""})
            s["ok"] += 1

    def fail(self, name: str, detail: str) -> None:
        with self._lock:
            s = self._state.setdefault(name, {"ok": 0, "fail": 0, "detail": ""})
            s["fail"] += 1
            s["detail"] = detail

    def status(self, name: str) -> str | None:
        s = self._state.get(name)
        if not s:
            return None
        if s["ok"] and not s["fail"]:
            return "ok"
        if s["ok"]:
            return "partial"
        return "unavailable"

    def detail(self, name: str) -> str:
        s = self._state.get(name)
        return s["detail"] if s else ""
