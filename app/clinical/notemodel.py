"""Turn model output (or a saved sample note) into the note shape the UI uses, and
compute what still blocks signing."""
from __future__ import annotations

import copy
import re

from app.drugs.engine import requires_ack

from . import icd, verify
from .schema import ITEM_STATUSES, MED_ACTIONS, ORDER_TYPES, ROLES
from .templates import TEMPLATES, section_title

HIGH_RISK_WORDS = ("anticoagulant", "insulin", "opioid")


def _str(v) -> str:
    return "" if v is None else str(v).strip()


def _ev(v, valid_ids: set[str] | None = None) -> list[str]:
    out = []
    for e in v or []:
        e = _str(e)
        m = re.match(r"^\[?(t\d+)\]?$", e)
        if m:
            e = m.group(1)
        if e and e not in out:
            out.append(e)
    return out


def _lower_enum(v, allowed: list[str], default: str) -> str:
    s = _str(v).lower().replace(" ", "_")
    return s if s in allowed else default


def normalise(raw: dict, template: str, turns: list[dict] | None = None) -> dict:
    """raw: the structured output (sections keyed by id) or a saved note."""
    raw = copy.deepcopy(raw or {})
    template = template if template in TEMPLATES else "soap"
    raw_sections = raw.get("sections") or {}
    if isinstance(raw_sections, list):  # already normalised
        raw_sections = {s.get("id"): {"items": s.get("items", [])} for s in raw_sections}

    used_ids: set[str] = set()
    sections = []
    for sid, title in TEMPLATES[template]["sections"]:
        items_in = ((raw_sections.get(sid) or {}).get("items")) or []
        items = []
        for n, it in enumerate(items_in, 1):
            text = _str(it.get("text"))
            if not text:
                continue
            status = _lower_enum(it.get("status"), ITEM_STATUSES, "documented")
            iid = _str(it.get("id"))
            if not iid or iid in used_ids or not iid.startswith(sid):
                iid = f"{sid}-{n}"
                k = n
                while iid in used_ids:
                    k += 1
                    iid = f"{sid}-{k}"
            used_ids.add(iid)
            items.append({
                "id": iid, "text": text, "evidence": _ev(it.get("evidence")), "status": status,
                "verdict": "unchecked", "verdict_reason": "", "edited": False,
            })
        if not items:
            items = [{"id": f"{sid}-nd", "text": "Not discussed", "evidence": [], "status": "not_discussed",
                      "verdict": "unchecked", "verdict_reason": "", "edited": False}]
            used_ids.add(f"{sid}-nd")
        sections.append({"id": sid, "title": title, "items": items})

    meds, med_ids = [], set()
    for n, m in enumerate(raw.get("medications") or [], 1):
        name = _str(m.get("name"))
        if not name:
            continue
        mid = _str(m.get("id"))
        if not re.match(r"^m\d+$", mid) or mid in med_ids:
            mid = f"m{n}"
            while mid in med_ids:
                n += 1
                mid = f"m{n}"
        med_ids.add(mid)
        meds.append({
            "id": mid, "name": name, "as_spoken": _str(m.get("as_spoken")) or name,
            "dose": _str(m.get("dose")), "unit": _str(m.get("unit")), "route": _str(m.get("route")),
            "frequency": _str(m.get("frequency")), "duration": _str(m.get("duration")),
            "action": _lower_enum(m.get("action"), MED_ACTIONS, "discussed"),
            "indication": _str(m.get("indication")), "evidence": _ev(m.get("evidence")),
            "verdict": "unchecked", "verdict_reason": "",
        })

    diagnoses = []
    for d in raw.get("diagnoses") or []:
        desc = _str(d.get("description"))
        if not desc:
            continue
        entry = {"description": desc, "evidence": _ev(d.get("evidence"))}
        entry.update(icd.validate(_str(d.get("icd10"))))
        diagnoses.append(entry)

    roles = raw.get("speaker_roles") or []
    if isinstance(roles, dict):
        speaker_roles = {str(k): _lower_enum(v, ROLES, "unknown") for k, v in roles.items()}
    else:
        speaker_roles = {_str(r.get("speaker")): _lower_enum(r.get("role"), ROLES, "unknown") for r in roles if _str(r.get("speaker"))}

    follow = raw.get("follow_up") or {}
    if isinstance(follow, str):
        follow = {"text": follow, "evidence": []}

    note = {
        "visit_summary": _str(raw.get("visit_summary")),
        "sections": sections,
        "medications": meds,
        "allergies": [
            {"substance": _str(a.get("substance")), "reaction": _str(a.get("reaction")), "evidence": _ev(a.get("evidence"))}
            for a in raw.get("allergies") or [] if _str(a.get("substance"))
        ],
        "orders": [
            {"type": _lower_enum(o.get("type"), ORDER_TYPES, "other"), "description": _str(o.get("description")),
             "evidence": _ev(o.get("evidence"))}
            for o in raw.get("orders") or [] if _str(o.get("description"))
        ],
        "diagnoses": diagnoses,
        "follow_up": {"text": _str(follow.get("text")), "evidence": _ev(follow.get("evidence"))},
        "patient_instructions": _str(raw.get("patient_instructions")),
        "not_discussed": [_str(x) for x in raw.get("not_discussed") or [] if _str(x)],
        "uncertain": [
            {"text": _str(u.get("text")), "evidence": _ev(u.get("evidence"))}
            for u in raw.get("uncertain") or [] if _str(u.get("text"))
        ],
        "removed_brands": list(raw.get("removed_brands") or []),
        "speaker_roles": speaker_roles,
    }
    return note


def apply_verdicts(note: dict, model_verdicts: dict[str, tuple[str, str]] | None,
                   deterministic: dict[str, tuple[str, str]], model_note: str = "") -> dict:
    """Each line gets the worse of the model verdict and the deterministic one."""
    model_verdicts = model_verdicts or {}

    def apply(entry: dict, skip: bool = False):
        if skip:
            entry["verdict"], entry["verdict_reason"] = "unchecked", "Not discussed"
            return
        mv, mr = model_verdicts.get(entry["id"], (None, ""))
        dv, dr = deterministic.get(entry["id"], (None, ""))
        if mv is None and dv is None:
            entry["verdict"] = "unchecked"
            entry["verdict_reason"] = model_note or "Not graded by the verifier."
            return
        final = verify.worst(mv, dv)
        entry["verdict"] = final
        if dv and verify.RANK[dv] >= verify.RANK.get(mv or "unchecked", 0):
            entry["verdict_reason"] = dr or mr
        else:
            entry["verdict_reason"] = mr or dr

    for section in note.get("sections", []):
        for item in section.get("items", []):
            if item.get("edited"):
                continue
            apply(item, skip=item.get("status") == "not_discussed")
    for med in note.get("medications", []):
        apply(med)
    return note


def find_item(note: dict, item_id: str) -> tuple[dict | None, dict | None]:
    for section in note.get("sections", []):
        for item in section.get("items", []):
            if item["id"] == item_id:
                return section, item
    return None, None


def section_by_id(note: dict, section_id: str) -> dict | None:
    for section in note.get("sections", []):
        if section["id"] == section_id:
            return section
    return None


def is_high_risk(row: dict) -> bool:
    if row.get("high_risk"):
        return True
    reason = (row.get("high_risk_reason") or "").lower()
    return any(w in reason for w in HIGH_RISK_WORDS)


def blockers(visit: dict) -> list[str]:
    if visit.get("status") == "signed":
        return []
    if visit.get("status") == "error":
        return ["Processing failed; retry or start a new visit."]
    if visit.get("status") in ("draft", "processing") or not visit.get("note"):
        return ["The note is not ready yet."]
    out = []
    note = visit["note"]
    mc = visit.get("medication_check") or {}
    rows = mc.get("medications") or []
    unconfirmed = [r for r in rows if not r.get("confirmed")]
    if unconfirmed:
        out.append(f"Confirm {len(unconfirmed)} medicine{'s' if len(unconfirmed) != 1 else ''}")
    # A dose only needs confirming for a high-risk medicine the patient will take
    # (not one that was only discussed, retracted or stopped).
    dose_needed = [r for r in rows if is_high_risk(r) and not r.get("dose_confirmed")
                   and (r.get("action") or "") not in ("discussed", "stop")]
    if dose_needed:
        out.append(f"Confirm the dose of {len(dose_needed)} high-risk medicine{'s' if len(dose_needed) != 1 else ''}")
    bad = [
        it for s in note.get("sections", []) for it in s.get("items", [])
        if it.get("verdict") in ("unsupported", "contradicted") and not it.get("edited")
    ]
    if bad:
        out.append(f"Resolve {len(bad)} unsupported line{'s' if len(bad) != 1 else ''}")
    acks = visit.get("cards_ack") or {}
    # Every critical alert blocks signing until acknowledged, including one that involves only
    # medicines the patient already took (it may be dangerous now, e.g. with a new potassium result).
    # Warnings this visit creates for interactions, QT, allergies, conditions, kidney function and
    # doses block too; other warnings and all info cards are shown without blocking.
    pending = [c for c in mc.get("cards") or [] if requires_ack(c) and c.get("id") not in acks]
    if pending:
        out.append(f"Acknowledge {len(pending)} alert{'s' if len(pending) != 1 else ''}")
    return out


def section_title_for(template: str, section_id: str) -> str:
    return section_title(template, section_id)
