"""Deterministic checks that every note line rests on what was said.

These run on every note, with or without the model verifier:
- every cited turn id exists;
- every number in a line was said (in the cited lines, or at least somewhere);
- each medicine's name and dose numbers appear in its cited lines.
"""
from __future__ import annotations

import re

from .numbers import norm, numbers_in

RANK = {"unchecked": 0, "supported": 1, "partial": 2, "unsupported": 3, "contradicted": 4}
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"and", "with", "oral", "tablet", "tablets", "extended", "release", "succinate", "tartrate",
         "hydrochloride", "sodium", "potassium", "pill", "the", "for"}


def worst(a: str | None, b: str | None) -> str:
    a = a or "unchecked"
    b = b or "unchecked"
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b


class Transcript:
    def __init__(self, turns: list[dict]):
        self.turns = turns or []
        self.by_id = {t["id"]: t for t in self.turns if t.get("id")}
        self.all_text = " \n".join(self._text(t) for t in self.turns)
        self.all_numbers = numbers_in(self.all_text)

    @staticmethod
    def _text(t: dict) -> str:
        text = t.get("text", "") or ""
        raw = t.get("raw_text") or ""
        return f"{text} {raw}" if raw and raw != text else text

    def cited(self, evidence: list[str]) -> tuple[list[str], list[str], str]:
        valid = [e for e in evidence or [] if e in self.by_id]
        invalid = [e for e in evidence or [] if e not in self.by_id]
        text = " \n".join(self._text(self.by_id[e]) for e in valid)
        return valid, invalid, text


def _evidence_issue(valid: list[str], invalid: list[str]) -> tuple[str | None, str]:
    if not valid and not invalid:
        return "unsupported", "No transcript lines are cited for this line."
    if not valid:
        return "unsupported", f"The cited lines ({', '.join(invalid[:4])}) are not in the transcript."
    if invalid:
        return "partial", f"Some cited lines ({', '.join(invalid[:4])}) are not in the transcript."
    return None, ""


def check_numbers(text: str, cited_text: str, tx: Transcript, allowed: set[str]) -> tuple[str | None, str]:
    cited = numbers_in(cited_text)
    verdict, reasons = None, []
    for n in sorted(numbers_in(text, include_special=False)):
        if n in allowed or n in cited:
            continue
        if n in tx.all_numbers:
            verdict = worst(verdict, "partial")
            reasons.append(f"{n} appears in the transcript but not in the cited lines")
        else:
            verdict = worst(verdict, "unsupported")
            reasons.append(f"{n} was not said in the visit")
    return verdict, "; ".join(reasons)


def check_item(item: dict, tx: Transcript, allowed: set[str]) -> tuple[str | None, str]:
    if item.get("status") == "not_discussed":
        return None, ""
    valid, invalid, cited_text = tx.cited(item.get("evidence") or [])
    verdict, reason = _evidence_issue(valid, invalid)
    if valid:
        v2, r2 = check_numbers(item.get("text", ""), cited_text, tx, allowed)
        if v2:
            verdict = worst(verdict, v2)
            reason = "; ".join(x for x in (reason, r2) if x)
    return verdict, reason


def _significant_tokens(name: str) -> list[str]:
    toks = [t for t in _TOKEN.findall((name or "").lower()) if len(t) >= 4 and t not in _STOP]
    return toks or [t for t in _TOKEN.findall((name or "").lower()) if len(t) >= 3]


def _named_in(name: str, text: str) -> bool:
    if not name:
        return False
    low = text.lower()
    if name.lower() in low:
        return True
    toks = _significant_tokens(name)
    return bool(toks) and any(re.search(rf"\b{re.escape(t)}", low) for t in toks)


def check_medication(med: dict, tx: Transcript) -> tuple[str | None, str]:
    valid, invalid, cited_text = tx.cited(med.get("evidence") or [])
    verdict, reason = _evidence_issue(valid, invalid)
    if not valid:
        return verdict, reason
    reasons = [reason] if reason else []
    names = [med.get("as_spoken") or "", med.get("name") or ""]
    if not any(_named_in(n, cited_text) for n in names if n):
        if any(_named_in(n, tx.all_text) for n in names if n):
            verdict = worst(verdict, "partial")
            reasons.append("the medicine is named elsewhere in the visit, not in the cited lines")
        else:
            verdict = worst(verdict, "unsupported")
            reasons.append("this medicine name was not found in the visit transcript")
    cited_nums = numbers_in(cited_text)
    for n in sorted(numbers_in(med.get("dose") or "", include_special=False)):
        if n in cited_nums:
            continue
        if n in tx.all_numbers:
            verdict = worst(verdict, "partial")
            reasons.append(f"dose {n} appears in the transcript but not in the cited lines")
        else:
            verdict = worst(verdict, "unsupported")
            reasons.append(f"dose {n} was not said in the visit")
    return verdict, "; ".join(r for r in reasons if r)


def allowed_numbers(chart: dict | None) -> set[str]:
    allowed = {"1"}
    if chart and chart.get("age") is not None:
        n = norm(chart["age"])
        if n:
            allowed.add(n)
    return allowed


def run(note: dict, turns: list[dict], chart: dict | None) -> dict[str, tuple[str, str]]:
    """item or medication id -> (verdict, reason) for every line with a problem."""
    tx = Transcript(turns)
    allowed = allowed_numbers(chart)
    issues: dict[str, tuple[str, str]] = {}
    for section in note.get("sections", []):
        for item in section.get("items", []):
            v, r = check_item(item, tx, allowed)
            if v:
                issues[item["id"]] = (v, r)
    for med in note.get("medications", []):
        v, r = check_medication(med, tx)
        if v:
            issues[med["id"]] = (v, r)
    return issues
