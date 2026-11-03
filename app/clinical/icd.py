"""ICD-10-CM validation with the bundled CMS code set (simple_icd_10_cm)."""
from __future__ import annotations

import re

try:
    import simple_icd_10_cm as _icd
except Exception:  # pragma: no cover - dependency missing on the host
    _icd = None

_CODE = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](?:\.?[0-9A-Z]{1,4})?$")


def normalise(code: str) -> str:
    c = (code or "").strip().upper().replace(" ", "")
    if len(c) > 3 and "." not in c:
        c = c[:3] + "." + c[3:]
    return c


def validate(code: str) -> dict:
    c = normalise(code)
    out = {"icd10": c, "icd10_valid": False, "icd10_title": "", "icd10_billable": False}
    if not c or not _CODE.match(c):
        return out
    if _icd is None:
        out["icd10_title"] = "ICD-10-CM code set not installed; not validated"
        return out
    try:
        if _icd.is_valid_item(c):
            out["icd10_valid"] = True
            out["icd10_title"] = _icd.get_description(c)
            out["icd10_billable"] = bool(_icd.is_leaf(c))
    except Exception:
        pass
    return out
