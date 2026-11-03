"""Synthetic practice data: patients, today's schedule and sample visits.

Everything here is fictional. Dates are stored as "days ago" and computed at
request time, so the demo schedule is always for today in the practice's zone.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"
SAMPLES = DATA / "samples"


def practice_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(_raw()["practice"].get("timezone", "America/New_York"))
    except Exception:  # no tz database on the host: fall back to US Eastern standard time
        return dt.timezone(dt.timedelta(hours=-5), "EST")


def now_local() -> dt.datetime:
    return dt.datetime.now(practice_tz())


def today_local() -> dt.date:
    return now_local().date()


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads((DATA / "patients.json").read_text(encoding="utf-8"))


def practice() -> dict:
    return dict(_raw()["practice"])


def _age(dob: str, on: dt.date) -> int:
    born = dt.date.fromisoformat(dob)
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def _date_ago(days: int | None, today: dt.date) -> str | None:
    if days is None:
        return None
    return (today - dt.timedelta(days=int(days))).isoformat()


def patient(patient_id: str) -> dict | None:
    """A chart in the shape of GET /api/patients/{id}."""
    for p in _raw()["patients"]:
        if p["id"] == patient_id:
            return _materialise(p)
    return None


def patients() -> list[dict]:
    return [_materialise(p) for p in _raw()["patients"]]


def _materialise(p: dict) -> dict:
    today = today_local()
    chart = copy.deepcopy(p)
    chart["age"] = _age(chart["dob"], today)
    vitals = chart.get("vitals") or {}
    if "days_ago" in vitals:
        vitals["date"] = _date_ago(vitals.pop("days_ago"), today)
    for lab in chart.get("labs", []):
        if "days_ago" in lab:
            lab["date"] = _date_ago(lab.pop("days_ago"), today)
    lv = chart.get("last_visit") or {}
    if "days_ago" in lv:
        lv["date"] = _date_ago(lv.pop("days_ago"), today)
    return chart


def schedule_template() -> list[dict]:
    return copy.deepcopy(_raw()["schedule"])


def appointment_for(patient_id: str) -> dict | None:
    for a in _raw()["schedule"]:
        if a["patient_id"] == patient_id:
            return dict(a)
    return None


def appointment_time_iso(hhmm: str, day: dt.date | None = None) -> str:
    day = day or today_local()
    hour, minute = (int(x) for x in hhmm.split(":"))
    local = dt.datetime.combine(day, dt.time(hour, minute), tzinfo=practice_tz())
    return local.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


@lru_cache(maxsize=1)
def _samples() -> dict[str, dict]:
    out = {}
    for path in sorted(SAMPLES.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out[data["id"]] = data
    return out


def samples() -> list[dict]:
    return [copy.deepcopy(s) for s in _samples().values()]


def sample(sample_id: str) -> dict | None:
    s = _samples().get(sample_id)
    return copy.deepcopy(s) if s else None


def keyterm_seed(chart: dict) -> list[str]:
    """Terms from the chart, in priority order: medicines, allergies, problems."""
    terms: list[str] = []
    for m in chart.get("medications", []):
        terms.append(m.get("name", ""))
    for a in chart.get("allergies", []):
        terms.append(a.get("substance", ""))
    for pr in chart.get("problems", []):
        terms.append(pr.get("short") or pr.get("name", ""))
    return [t for t in terms if t]
