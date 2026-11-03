"""Dose checks against label maximums. Flags; never computes or recommends a dose."""
from __future__ import annotations

import re

from .kb import kb

_FREQ = [
    (r"\b(every other day|qod|alternate days)\b", 0.5),
    (r"\b(twice (a|per) week|twice weekly|2 times (a|per) week)\b", 2 / 7),
    (r"\b(once (a|per) week|weekly|every week|q ?week|qwk|once weekly)\b", 1 / 7),
    (r"\b(monthly|once (a|per) month|every month)\b", 1 / 30),
    (r"\b(four times (a|per) day|four times daily|qid|q\.i\.d)\b", 4),
    (r"\b(three times (a|per) day|three times daily|tid|t\.i\.d)\b", 3),
    (r"\b(twice (a|per) day|twice daily|two times (a|per) day|bid|b\.i\.d)\b", 2),
    (r"\bevery 4\s*(?:-|to)\s*6 hours\b", 6),
    (r"\bevery 6\s*(?:-|to)\s*8 hours\b", 4),
    (r"\b(every|q) ?4 ?h(ours?|rs?)?\b", 6),
    (r"\b(every|q) ?6 ?h(ours?|rs?)?\b", 4),
    (r"\b(every|q) ?8 ?h(ours?|rs?)?\b", 3),
    (r"\b(every|q) ?12 ?h(ours?|rs?)?\b", 2),
    (r"\b(every|q) ?24 ?h(ours?|rs?)?\b", 1),
    (r"\b(once (a|per) day|once daily|daily|every day|qd|q\.d|every morning|every evening|in the morning|at bedtime|nightly|qhs|qam|qpm|each day|a day)\b", 1),
]
_TIMES = re.compile(r"\b(\d+)\s*(?:x|times)\s*(?:a|per)\s*day\b")
_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")


def per_day(frequency: str | None) -> float | None:
    f = (frequency or "").lower().strip()
    if not f:
        return None
    m = _TIMES.search(f)
    if m:
        return float(m.group(1))
    for pattern, n in _FREQ:
        if re.search(pattern, f):
            return n
    return None


def dose_mg(dose, unit: str | None) -> float | None:
    """Parse a single dose to mg. '20', '0.5', '1,000' with unit mg/mcg/g. None if not a mass."""
    if dose is None:
        return None
    text = f"{dose} {unit or ''}".lower()
    m = _NUM.search(text.replace(",", ""))
    if not m:
        return None
    value = float(m.group(1))
    u = (unit or "").lower().strip()
    if not u:
        um = re.search(r"\d\s*(mg|mcg|µg|ug|g)\b", text)
        u = um.group(1) if um else ""
    if u in ("mg", "milligram", "milligrams"):
        return value
    if u in ("mcg", "µg", "ug", "microgram", "micrograms"):
        return value / 1000.0
    if u in ("g", "gram", "grams"):
        return value * 1000.0
    return None


def _fmt(n: float) -> str:
    if n >= 100:
        return f"{n:,.0f}"
    return f"{n:g}"


def check(generic: str | None, dose, unit: str | None, frequency: str | None) -> tuple[dict, dict | None]:
    """Returns (dose_check, card_or_None). Card: {indicator, summary, detail, source}."""
    k = kb()
    if not generic:
        return {"status": "unknown", "detail": "Medicine not matched; dose not checked.", "source": None}, None
    special = k.doses["special"].get(generic)
    freq = per_day(frequency)
    if special and special.get("weekly_only"):
        f = (frequency or "").lower()
        daily = freq is not None and freq >= 0.9 or bool(re.search(r"\b(daily|every day|qd|bid|tid|twice a day)\b", f))
        if daily:
            detail = special["detail"]
            return ({"status": "above_max", "detail": f"Recorded as {frequency}. {detail}", "source": special["source"]},
                    {"indicator": "critical", "summary": "Methotrexate recorded with daily dosing", "detail": f"Recorded as \"{frequency}\". {detail} Confirm the schedule is once weekly.", "source": special["source"]})
        return {"status": "ok" if freq is not None else "unknown", "detail": f"Usual: {special['usual']}.", "source": special["source"]}, None

    entry = k.doses["doses"].get(generic)
    if not entry:
        return {"status": "unknown", "detail": "No curated dose range for this medicine.", "source": None}, None
    mg = dose_mg(dose, unit)
    usual = f"Usual adult dose: {entry['usual']}; label maximum {_fmt(entry['max_mg_per_day'])} mg/day."
    if entry.get("note"):
        usual += f" {entry['note']}"
    if mg is None:
        return {"status": "unknown", "detail": f"Dose not recorded in mg. {usual}", "source": entry["source"]}, None
    maximum = float(entry["max_mg_per_day"])
    if mg > maximum + 1e-9:
        detail = f"A single dose of {_fmt(mg)} mg exceeds the label maximum of {_fmt(maximum)} mg/day. {usual}"
        return ({"status": "above_max", "detail": detail, "source": entry["source"]},
                {"indicator": "warning", "summary": f"{generic}: dose above the label maximum", "detail": detail, "source": entry["source"]})
    if freq is None:
        return {"status": "unknown", "detail": f"{_fmt(mg)} mg per dose; frequency unclear. {usual}", "source": entry["source"]}, None
    daily_total = mg * freq
    if daily_total > maximum + 1e-9:
        detail = f"{_fmt(mg)} mg × {frequency} ≈ {_fmt(daily_total)} mg/day, above the label maximum of {_fmt(maximum)} mg/day. {usual}"
        return ({"status": "above_max", "detail": detail, "source": entry["source"]},
                {"indicator": "warning", "summary": f"{generic}: daily dose above the label maximum", "detail": detail, "source": entry["source"]})
    return {"status": "ok", "detail": f"{_fmt(daily_total)} mg/day is within the label maximum. {usual}", "source": entry["source"]}, None


def daily_mg(dose, unit, frequency) -> float | None:
    mg = dose_mg(dose, unit)
    f = per_day(frequency)
    if mg is None or f is None:
        return None
    return mg * f
