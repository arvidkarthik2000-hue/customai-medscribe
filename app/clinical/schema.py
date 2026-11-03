"""JSON schemas for Claude structured outputs (output_config.format).

Rules the API enforces, which these schemas follow: every object has
additionalProperties false; no numeric or string length constraints; no
recursion; few anyOf. Every property is required (empty string or empty list
when absent), so there are no optional parameters at all.
"""
from __future__ import annotations

from .templates import TEMPLATES

ITEM_STATUSES = ["documented", "negative", "not_discussed"]
MED_ACTIONS = ["start", "stop", "change", "continue", "hold", "discussed"]
ORDER_TYPES = ["lab", "imaging", "referral", "procedure", "vaccine", "other"]
ROLES = ["clinician", "patient", "caregiver", "other", "unknown"]
VERDICTS = ["supported", "partial", "unsupported", "contradicted"]


def _s() -> dict:
    return {"type": "string"}


def _enum(values: list[str]) -> dict:
    return {"type": "string", "enum": list(values)}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


def item_schema() -> dict:
    return _obj({"id": _s(), "text": _s(), "evidence": _arr(_s()), "status": _enum(ITEM_STATUSES)})


def note_schema(template: str) -> dict:
    sections = _obj({sid: _obj({"items": _arr({"$ref": "#/$defs/item"})}) for sid, _ in TEMPLATES[template]["sections"]})
    schema = _obj({
        "visit_summary": _s(),
        "speaker_roles": _arr(_obj({"speaker": _s(), "role": _enum(ROLES)})),
        "sections": sections,
        "medications": _arr(_obj({
            "id": _s(), "name": _s(), "as_spoken": _s(), "dose": _s(), "unit": _s(), "route": _s(),
            "frequency": _s(), "duration": _s(), "action": _enum(MED_ACTIONS), "indication": _s(),
            "evidence": _arr(_s()),
        })),
        "allergies": _arr(_obj({"substance": _s(), "reaction": _s(), "evidence": _arr(_s())})),
        "orders": _arr(_obj({"type": _enum(ORDER_TYPES), "description": _s(), "evidence": _arr(_s())})),
        "diagnoses": _arr(_obj({"description": _s(), "icd10": _s(), "evidence": _arr(_s())})),
        "follow_up": _obj({"text": _s(), "evidence": _arr(_s())}),
        "patient_instructions": _s(),
        "not_discussed": _arr(_s()),
        "uncertain": _arr(_obj({"text": _s(), "evidence": _arr(_s())})),
    })
    schema["$defs"] = {"item": item_schema()}
    return schema


def verifier_schema() -> dict:
    return _obj({"results": _arr(_obj({"item_id": _s(), "verdict": _enum(VERDICTS), "reason": _s()}))})


def section_rewrite_schema() -> dict:
    schema = _obj({"items": _arr({"$ref": "#/$defs/item"})})
    schema["$defs"] = {"item": item_schema()}
    return schema
