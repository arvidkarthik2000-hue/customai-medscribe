"""Plain-text note for copy-paste into an EHR."""
from __future__ import annotations

import textwrap

from .. import auth
from .templates import TEMPLATES

WIDTH = 92


def _wrap(text: str, indent: str = "") -> str:
    return textwrap.fill(text, width=WIDTH, initial_indent=indent, subsequent_indent=indent + "  ")


def render(visit: dict, chart: dict | None) -> str:
    note = visit.get("note") or {}
    t = TEMPLATES.get(visit.get("template") or "soap", TEMPLATES["soap"])
    lines: list[str] = []
    status = "SIGNED" if visit.get("status") == "signed" else "DRAFT - NOT SIGNED"
    lines.append(f"{t['label'].upper()}  [{status}]")
    if chart:
        lines.append(f"Patient: {chart.get('name')}  DOB {chart.get('dob')}  MRN {chart.get('mrn')}  "
                     f"({chart.get('age')} y, {chart.get('sex')})")
    lines.append(f"Clinician: {auth.DEMO_PRESCRIBER['name']}, {auth.DEMO_PRESCRIBER['practice']}")
    lines.append(f"Visit: {visit.get('created_at', '')[:10]}   Note ID: {visit.get('id')}")
    if note.get("visit_summary"):
        lines += ["", _wrap(f"Summary: {note['visit_summary']}")]

    for section in note.get("sections", []):
        lines += ["", section["title"].upper()]
        items = section.get("items", [])
        if all(i.get("status") == "not_discussed" for i in items):
            lines.append("  Not discussed.")
            continue
        for it in items:
            if it.get("status") == "not_discussed":
                continue
            lines.append(_wrap(f"- {it['text']}", "  "))

    meds = note.get("medications") or []
    if meds:
        lines += ["", "MEDICATIONS"]
        for m in meds:
            dose = " ".join(x for x in (m.get("dose"), m.get("unit")) if x)
            bits = [x for x in (dose, m.get("route"), m.get("frequency"), m.get("duration")) if x]
            spoken = f" (said: {m['as_spoken']})" if m.get("as_spoken") and m["as_spoken"].lower() != m["name"].lower() else ""
            lines.append(_wrap(f"- {m['name']}{spoken}: {', '.join(bits) or 'details not stated'} [{m.get('action', '')}]", "  "))

    allergies = note.get("allergies") or []
    lines += ["", "ALLERGIES DISCUSSED"]
    lines += [_wrap(f"- {a['substance']}: {a.get('reaction') or 'reaction not stated'}", "  ") for a in allergies] or ["  None discussed in this visit."]

    orders = note.get("orders") or []
    if orders:
        lines += ["", "ORDERS (DRAFTS, NOT SUBMITTED)"]
        lines += [_wrap(f"- {o['type'].capitalize()}: {o['description']}", "  ") for o in orders]

    dx = note.get("diagnoses") or []
    if dx:
        lines += ["", "DIAGNOSES (ICD-10-CM)"]
        for d in dx:
            code = d.get("icd10") or "no code"
            flag = "" if d.get("icd10_valid") else " (code not validated)"
            lines.append(_wrap(f"- {code}{flag}  {d['description']}", "  "))

    fu = (note.get("follow_up") or {}).get("text")
    if fu:
        lines += ["", "FOLLOW-UP", _wrap(fu, "  ")]

    if note.get("patient_instructions"):
        lines += ["", "PATIENT INSTRUCTIONS", _wrap(note["patient_instructions"], "  ")]

    if note.get("not_discussed"):
        lines += ["", "NOT DISCUSSED: " + "; ".join(note["not_discussed"])]

    lines.append("")
    if visit.get("status") == "signed":
        lines.append(f"Electronically signed by {auth.DEMO_PRESCRIBER['name']} at {visit.get('signed_at')}.")
    else:
        lines.append("Draft. Review and sign in CustomAi MedScribe before use.")
    lines.append("Drafted with CustomAi MedScribe (AI-assisted); reviewed and signed by the clinician. Demo: synthetic patient.")
    return "\n".join(lines) + "\n"
