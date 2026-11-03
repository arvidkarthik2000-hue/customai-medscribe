"""Adult prevention reminders (USPSTF A/B and CDC adult schedule), from age and sex only."""
from __future__ import annotations

from .kb import kb


def _sex(s) -> str | None:
    s = str(s or "").strip().lower()
    if s in ("f", "female", "woman"):
        return "F"
    if s in ("m", "male", "man"):
        return "M"
    return None


def reminders(age, sex, *, smoking: dict | None = None, bmi: float | None = None) -> list[dict]:
    """Eligibility reminders. smoking: {current: bool, pack_years: float, quit_years: float, ever: bool}."""
    p = kb().prevention
    try:
        age = float(age)
    except (TypeError, ValueError):
        return []
    sx = _sex(sex)
    out = []
    for item in p["items"]:
        if age < item["min_age"] or (item["max_age"] is not None and age > item["max_age"]):
            continue
        if item["sex"] and item["sex"] != sx:
            continue
        req = item.get("requires")
        if req == "smoking":
            s = smoking or {}
            ok = (s.get("pack_years") or 0) >= 20 and (s.get("current") or (s.get("quit_years") is not None and s["quit_years"] <= 15))
            if not ok:
                continue
        if req == "ever_smoked" and not (smoking or {}).get("ever") and not (smoking or {}).get("current"):
            continue
        if req == "overweight" and not (bmi is not None and bmi >= 25):
            continue
        src = p["sources"][item["source"]]
        out.append({"id": item["id"], "name": item["name"], "detail": item["detail"], "source": src})
    return out
