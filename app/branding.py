"""What testers see calls every AI part "Salient". Real vendor and model names stay in the logs.

scrub() rewrites vendor names, model ids and vendor host names in metadata text: stage
details, errors, warnings, self-test and accuracy-run output. It is never applied to
transcripts or notes, where a word like "Claude" could be a person's name.
"""
from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern, str]] = [
    # host names and URLs first, so the vendor name inside them is not rewritten twice
    (re.compile(r"(?:wss?|https?)://[\w.-]*(?:assemblyai|anthropic)\.com[^\s'\"),]*", re.I), "the service"),
    (re.compile(r"[\w.-]*(?:assemblyai|anthropic)\.com", re.I), "the service"),
    # model ids
    (re.compile(r"\bclaude-[\w.-]*haiku[\w.-]*", re.I), "salient-fast"),
    (re.compile(r"\bclaude-[\w.-]+", re.I), "salient-thorough"),
    (re.compile(r"\buniversal-[\w.-]+", re.I), "salient-medical-speech"),
    (re.compile(r"\bu3-rt-pro\b|\bslam-1\b", re.I), "salient-medical-speech"),
    (re.compile(r"\bmedical-v1\b", re.I), "medical vocabulary"),
    (re.compile(r"\bmedical mode\b", re.I), "medical vocabulary"),
    (re.compile(r"\bkeyterms?(?:_prompt)?\b", re.I), "drug terms"),
    # vendor and product names
    (re.compile(r"\bassembly\s?ai(?:'s)?\b", re.I), "the speech service"),
    (re.compile(r"\banthropic(?:'s)?\b", re.I), "the note service"),
    (re.compile(r"\bclaude\b", re.I), "Salient"),
    (re.compile(r"\bsonnet\b", re.I), "thorough"),
    (re.compile(r"\bhaiku\b", re.I), "fast"),
]

_MODEL_FIELDS = {
    "live_model": "salient-live",
    "final_model": "salient-medical-speech",
    "verifier_model": "salient-verifier",
}


def scrub(text):
    if not isinstance(text, str) or not text:
        return text
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


def scrub_obj(obj):
    """scrub() every string value in a JSON-like value (keys are left alone: pages read them)."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {k: scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(v) for v in obj]
    return obj


def engine(name: str | None) -> str | None:
    """Visits saved before the rename carry sonnet/haiku."""
    if name in ("haiku", "fast"):
        return "fast"
    if name in ("sonnet", "thorough"):
        return "thorough"
    return name


def engine_info(info: dict | None, visit_engine: str | None = None) -> dict:
    """Older visits saved real model ids; show the neutral names."""
    out = dict(info or {})
    for key, neutral in _MODEL_FIELDS.items():
        if out.get(key):
            out[key] = neutral if scrub(str(out[key])) != str(out[key]) else out[key]
    note = out.get("note_model")
    if note and scrub(str(note)) != str(note):
        fast = "haiku" in str(note).lower()
        out["note_model"] = "salient-fast" if fast else "salient-" + (engine(visit_engine) or "thorough")
    if "warnings" in out:
        out["warnings"] = scrub_obj(out["warnings"])
    return scrub_obj(out)


def stages(items: list[dict] | None, labels: dict[str, str]) -> list[dict] | None:
    if items is None:
        return None
    out = []
    for st in items:
        st = dict(st)
        if st.get("id") in labels:
            st["label"] = labels[st["id"]]
        st["label"] = scrub(st.get("label"))
        st["detail"] = scrub(st.get("detail"))
        out.append(st)
    return out
