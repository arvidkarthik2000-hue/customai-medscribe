"""Visit pipeline: final transcript -> note -> verify -> medicines.

Runs as a background task. Each stage reports pending/active/done/skipped/error
with a plain-words detail, so nothing fails silently. A restart mid-run leaves the
visit in "error" with a retry path (see store.recover_interrupted).
"""
from __future__ import annotations

import asyncio
import copy
import logging
import time
from pathlib import Path

from .. import config
from . import drugs_bridge, fixtures, notemodel, notes, store, stt, verify

log = logging.getLogger("medscribe.pipeline")

STAGES = [
    ("final_transcript", "Final transcript (AssemblyAI Medical Mode)"),
    ("note", "Draft note (Claude)"),
    ("verify", "Verify every line"),
    ("meds", "Check medicines"),
]

INLINE = False  # tests run the pipeline in the request
RUNNING: dict[str, asyncio.Task] = {}


class PipelineError(Exception):
    pass


def initial_stages() -> list[dict]:
    return [{"id": sid, "label": label, "state": "pending", "detail": ""} for sid, label in STAGES]


def is_running(visit_id: str) -> bool:
    task = RUNNING.get(visit_id)
    return bool(task and not task.done())


class _Run:
    def __init__(self, visit_id: str):
        self.visit_id = visit_id
        self.stages = initial_stages()
        self.timings: dict[str, float] = {}
        self._t: dict[str, float] = {}

    def _save(self, **extra):
        store.update_visit(self.visit_id, stages=self.stages, timings=self.timings, **extra)

    def set(self, sid: str, state: str, detail: str = "", **extra):
        for s in self.stages:
            if s["id"] == sid:
                s["state"] = state
                if detail or state in ("active", "done"):
                    s["detail"] = detail
        if state == "active":
            self._t[sid] = time.monotonic()
            extra.setdefault("stage", sid)
        elif sid in self._t and state in ("done", "error", "skipped"):
            self.timings[f"{sid}_s"] = round(time.monotonic() - self._t.pop(sid), 2)
        self._save(**extra)


async def start(visit_id: str, source: str, sample_id: str | None = None) -> bool:
    """Start (or restart) processing. Returns False if it is already running."""
    if is_running(visit_id):
        return False
    store.update_visit(visit_id, status="processing", stage="queued", stages=initial_stages(), error=None,
                       source=source, **({"sample_id": sample_id} if sample_id else {}))
    store.audit(visit_id, "processing_started", {"source": source, "sample_id": sample_id})
    if INLINE:
        await run(visit_id, source, sample_id)
        return True
    task = asyncio.create_task(run(visit_id, source, sample_id))
    RUNNING[visit_id] = task
    task.add_done_callback(lambda _t: RUNNING.pop(visit_id, None))
    return True


async def run(visit_id: str, source: str, sample_id: str | None) -> None:
    r = _Run(visit_id)
    try:
        await _run(r, source, sample_id)
    except PipelineError as exc:
        _fail(r, str(exc))
    except Exception as exc:  # never leave a visit stuck in "processing"
        log.exception("pipeline crashed for %s", visit_id)
        _fail(r, f"Unexpected error ({exc.__class__.__name__}). Press Retry; if it repeats, start a new visit.")


def _fail(r: _Run, message: str) -> None:
    for s in r.stages:
        if s["state"] == "active":
            s["state"] = "error"
            s["detail"] = message
    store.update_visit(r.visit_id, status="error", stage="error", error=message, stages=r.stages, timings=r.timings)
    store.audit(r.visit_id, "processing_failed", {"error": message}, actor="system")


async def _run(r: _Run, source: str, sample_id: str | None) -> None:
    s = config.settings
    visit = store.get_visit(r.visit_id)
    if not visit:
        raise PipelineError("Visit not found.")
    chart = fixtures.patient(visit["patient_id"])
    engine_info = dict(visit.get("engine_info") or {})
    engine_info.setdefault("warnings", [])
    engine_info["live_model"] = s.live_speech_model
    removed_brands: list[str] = []
    sample = fixtures.sample(sample_id) if sample_id else None

    # ------------------------------------------------ 1. transcript
    r.set("final_transcript", "active", "Getting the final transcript")
    if source == "final" and (not visit.get("audio_path") or not Path(visit["audio_path"]).exists()) \
            and visit.get("transcript") and (visit.get("engine_info") or {}).get("final_model"):
        # Retry after a later stage failed: the recording is gone, the final transcript is kept.
        transcript = visit["transcript"]
        for t in transcript:
            if t.get("raw_text"):
                removed_brands += drugs_bridge.strip_added_brands(t["raw_text"])[1]
        r.set("final_transcript", "done", "Reusing the final transcript from the previous attempt.")
    elif source == "final":
        audio_path = visit.get("audio_path")
        live = stt.normalise_live_turns(visit.get("live_turns") or [])
        try:
            if not audio_path or not Path(audio_path).exists():
                raise stt.SttError("The visit recording was not uploaded.")
            keyterms = stt.build_keyterms(fixtures.keyterm_seed(chart or {}), visit.get("specialty") or "family_medicine")
            result = await stt.transcribe_file(audio_path, keyterms["final"])
            transcript = result["turns"]
            removed_brands += result["removed_brands"]
            meta = result["meta"]
            engine_info["final_model"] = meta.get("speech_model_used")
            engine_info["medical_mode"] = meta.get("medical_mode")
            engine_info["keyterms_used"] = meta.get("keyterms_used")
            engine_info["warnings"] += meta.get("warnings", [])
            duration = float(meta.get("audio_duration") or 0)
            prior = float(visit.get("audio_seconds") or 0)
            if duration > prior:
                store.usage_add("audio_seconds", duration - prior)
            store.update_visit(r.visit_id, audio_seconds=max(duration, prior), audio_path=None)
            _delete_audio(audio_path)
            store.audit(r.visit_id, "audio_deleted", {"reason": "final pass complete"}, actor="system")
            detail = f"{len(transcript)} turns from {meta.get('speech_model_used')}" + (
                " with Medical Mode" if meta.get("medical_mode") else " (Medical Mode not applied)")
            if meta.get("warnings"):
                detail += ". " + " ".join(meta["warnings"])
            r.set("final_transcript", "done", detail)
        except stt.SttError as exc:
            if live:
                transcript = stt.renumber(live)
                r.set("final_transcript", "error", f"Final pass failed: {exc} Using the live transcript instead.")
                engine_info["warnings"].append(f"Final pass failed ({exc}); the live transcript was used.")
            else:
                raise PipelineError(f"Final pass failed: {exc}") from exc
    elif source == "live":
        live = stt.normalise_live_turns(visit.get("live_turns") or [])
        if not live:
            raise PipelineError("No live transcript was captured for this visit.")
        transcript = stt.renumber(live)
        r.set("final_transcript", "skipped", "Live transcript used (no final pass).")
    elif source == "sample":
        if not sample:
            raise PipelineError("That sample visit does not exist.")
        transcript = []
        for t in sample["turns"]:
            clean, removed = drugs_bridge.strip_added_brands(t["text"])
            removed_brands += removed
            turn = {**t, "text": clean, "role": "unknown"}
            if clean != t["text"]:
                turn["raw_text"] = t["text"]
            transcript.append(turn)
        r.set("final_transcript", "skipped", "Sample visit transcript (pre-recorded script, no audio).")
    else:
        raise PipelineError(f"Unknown source '{source}'.")
    store.update_visit(r.visit_id, transcript=transcript, engine_info=engine_info)

    # ------------------------------------------------ 2. note
    r.set("note", "active", "Drafting the note")
    template = visit.get("template") or "soap"
    note, note_detail, used_claude = None, "", False
    cap_reached = store.usage_get("note_runs") >= s.daily_note_runs
    if s.notes_ready and not cap_reached:
        try:
            store.usage_add("note_runs", 1)
            note, meta = await notes.generate_note(
                transcript, chart, template=template, specialty=visit.get("specialty") or "family_medicine",
                style=visit.get("style") or "concise", engine=visit.get("engine") or "sonnet",
                visit_date=fixtures.today_local().isoformat())
            used_claude = True
            engine_info["note_model"] = meta["model"]
            engine_info["note_tokens"] = {"input": meta.get("input_tokens"), "output": meta.get("output_tokens")}
            engine_info["warnings"] += meta.get("warnings", [])
            note_detail = f"Drafted by {meta['model']} in {meta['seconds']} s"
        except notes.NoteError as exc:
            if sample:
                note_detail = f"Claude could not draft this note ({exc}); showing the saved note for this sample."
            else:
                raise PipelineError(str(exc)) from exc
    elif not sample:
        if cap_reached:
            raise PipelineError("The demo's daily note limit has been reached. It resets at midnight UTC.")
        raise PipelineError("The note engine is not configured on this server (no Claude API key), so this recording can't be turned into a note.")
    else:
        note_detail = ("Saved note for this sample: the demo's daily note limit has been reached."
                       if cap_reached else "Saved note for this sample: Claude is not configured on this server.")
    if note is None:  # saved sample note
        template = sample["template"]
        note = notemodel.normalise(copy.deepcopy(sample["saved_note"]), template, transcript)
        engine_info["note_model"] = "saved sample note"
        store.update_visit(r.visit_id, template=template)
    note["removed_brands"] = sorted(set(removed_brands) | set(note.get("removed_brands") or []))
    speaker_roles = note.pop("speaker_roles", {}) or (sample or {}).get("speaker_roles", {})
    for t in transcript:
        t["role"] = speaker_roles.get(t.get("speaker"), "unknown")
    store.update_visit(r.visit_id, note=note, transcript=transcript, speaker_roles=speaker_roles, engine_info=engine_info)
    r.set("note", "done", note_detail)

    # ------------------------------------------------ 3. verify
    r.set("verify", "active", "Checking every line against the transcript")
    deterministic = verify.run(note, transcript, chart)
    model_verdicts, model_note = None, ""
    if s.notes_ready:
        try:
            model_verdicts, vmeta = await notes.verify_note(note, transcript)
            engine_info["verifier_model"] = vmeta.get("model")
            engine_info["warnings"] += vmeta.get("warnings", [])
            verify_detail = f"{vmeta.get('graded', 0)} of {vmeta.get('lines', 0)} lines graded by {vmeta.get('model')}; deterministic checks run"
        except notes.NoteError as exc:
            model_note = f"Verifier unavailable ({exc}); deterministic checks only."
            verify_detail = model_note
    else:
        model_note = "Model verifier not run (Claude not configured); deterministic checks only."
        verify_detail = model_note
    notemodel.apply_verdicts(note, model_verdicts, deterministic, model_note=model_note)
    flagged = sum(1 for sec in note["sections"] for it in sec["items"] if it.get("verdict") in ("unsupported", "contradicted"))
    if flagged:
        verify_detail += f". {flagged} line{'s' if flagged != 1 else ''} to resolve before signing"
    store.update_visit(r.visit_id, note=note, engine_info=engine_info)
    r.set("verify", "done", verify_detail)

    # ------------------------------------------------ 4. medicines
    r.set("meds", "active", "Checking medicines")
    patient = dict(chart or {})
    result, detail = await drugs_bridge.check_visit(note["medications"], patient)
    if result is None:
        mc = drugs_bridge.fallback_medication_check(note["medications"], detail)
        r.set("meds", "skipped" if "not installed" in detail else "error", detail)
    else:
        mc = _merge_check(note["medications"], result)
        n_cards = len(mc.get("cards") or [])
        crit = sum(1 for c in mc.get("cards") or [] if c.get("indicator") == "critical")
        r.set("meds", "done", f"{len(mc['medications'])} medicines checked; {n_cards} alert{'s' if n_cards != 1 else ''}"
              + (f", {crit} critical" if crit else ""))
    store.update_visit(r.visit_id, medication_check=mc, status="ready", stage="done", error=None,
                       engine_info=engine_info)
    store.audit(r.visit_id, "note_ready", {"claude": used_claude, "flagged_lines": flagged}, actor="system")


def _merge_check(medications: list[dict], result: dict) -> dict:
    """Make sure every note medicine has a row with confirmation flags."""
    mc = dict(result)
    rows = list(mc.get("medications") or [])
    by_id = {row.get("id"): row for row in rows if row.get("id")}
    merged = []
    for m in medications:
        row = dict(by_id.get(m["id"]) or m)
        for k, v in m.items():
            row.setdefault(k, v)
        row["confirmed"] = bool(row.get("confirmed", False))
        row["dose_confirmed"] = bool(row.get("dose_confirmed", False))
        row.setdefault("high_risk", False)
        merged.append(row)
    mc["medications"] = merged
    mc.setdefault("cards", [])
    mc.setdefault("sources_checked", [])
    return mc


def _delete_audio(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        log.warning("could not delete audio %s", path)


async def recheck_medications(visit: dict) -> dict:
    """Re-run drug checks after a correction, keeping confirmations by medicine id."""
    chart = fixtures.patient(visit["patient_id"]) or {}
    meds = visit["note"]["medications"]
    old = {r.get("id"): r for r in (visit.get("medication_check") or {}).get("medications") or []}
    result, detail = await drugs_bridge.check_visit(meds, dict(chart))
    mc = drugs_bridge.fallback_medication_check(meds, detail) if result is None else _merge_check(meds, result)
    for row in mc["medications"]:
        prev = old.get(row.get("id"))
        if prev:
            row["confirmed"] = prev.get("confirmed", False)
            row["dose_confirmed"] = prev.get("dose_confirmed", False)
    return mc
