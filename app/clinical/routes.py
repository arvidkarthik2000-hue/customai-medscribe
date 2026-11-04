"""Clinical API routes (see docs/CONTRACTS.md, "Clinical API")."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import auth, config
from . import drugs_bridge, evaluate, export, fixtures, notemodel, notes, pipeline, selftest, store, stt, verify
from .templates import SPECIALTIES

log = logging.getLogger("medscribe.clinical")
router = APIRouter(tags=["clinical"])

store.init()
store.recover_interrupted()


def _cleanup_audio(max_age_h: float = 24.0) -> None:
    """Recordings are deleted after the final pass or at sign-off; this catches abandoned visits."""
    import time

    folder = config.settings.data_dir / "audio"
    if not folder.exists():
        return
    cutoff = time.time() - max_age_h * 3600
    for f in folder.iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


_cleanup_audio()

AUDIO_EXT = {".webm", ".ogg", ".oga", ".opus", ".mp4", ".m4a", ".wav", ".mp3", ".mpeg", ".aac", ".flac"}
MIME_EXT = {
    "audio/webm": ".webm", "video/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".mp4", "audio/x-m4a": ".m4a",
    "audio/m4a": ".m4a", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3", "audio/aac": ".aac", "audio/flac": ".flac",
}

Template = Literal["soap", "progress", "hp", "consult"]


# ---------------------------------------------------------------- models


class VisitIn(BaseModel):
    patient_id: str
    template: Template = "soap"
    style: Literal["concise", "detailed"] = "concise"
    engine: Literal["sonnet", "haiku"] = "sonnet"
    specialty: str | None = None


class ConsentIn(BaseModel):
    consented: bool
    attestation: str = "roleplay_no_real_patient"


class TokenIn(BaseModel):
    visit_id: str


class LiveTurnsIn(BaseModel):
    turns: list[dict] = Field(default_factory=list)
    audio_seconds: float = 0


class ProcessIn(BaseModel):
    source: Literal["final", "live", "sample"]
    sample_id: str | None = None


class NotePatch(BaseModel):
    item_id: str | None = None
    text: str | None = None
    section_id: str | None = None
    items: list[dict] | None = None


class AskIn(BaseModel):
    section_id: str
    instruction: str = Field(min_length=2, max_length=600)


class ConfirmIn(BaseModel):
    confirmed: bool
    dose_confirmed: bool | None = None
    corrected: dict | None = None


class AckIn(BaseModel):
    reason: str = Field(min_length=2, max_length=300)


# ---------------------------------------------------------------- helpers


def _visit_or_404(visit_id: str) -> dict:
    v = store.get_visit(visit_id)
    if not v:
        raise HTTPException(404, "Visit not found.")
    return v


def _not_signed(v: dict) -> None:
    if v.get("status") == "signed":
        raise HTTPException(409, "This note is signed and can't be changed.")


def _specialty(session: dict, requested: str | None) -> str:
    for s in (requested, session.get("specialty")):
        if s in SPECIALTIES:
            return s
    return "family_medicine"


def visit_view(v: dict) -> dict:
    transcript = v.get("transcript")
    if transcript is None:
        transcript = stt.normalise_live_turns(v.get("live_turns") or [])
    out = {
        "id": v["id"],
        "patient_id": v["patient_id"],
        "appt_id": v.get("appt_id"),
        "status": v["status"],
        "template": v.get("template"),
        "style": v.get("style"),
        "engine": v.get("engine"),
        "specialty": v.get("specialty"),
        "created_at": v.get("created_at"),
        "consent_at": v.get("consent_at"),
        "signed_at": v.get("signed_at"),
        "source": v.get("source"),
        "sample_id": v.get("sample_id"),
        "audio_seconds": round(float(v.get("audio_seconds") or 0), 1),
        "transcript": transcript,
        "speaker_roles": v.get("speaker_roles") or {},
        "note": v.get("note"),
        "medication_check": v.get("medication_check"),
        "cards_ack": v.get("cards_ack") or {},
        "blockers": notemodel.blockers(v),
        "timings": v.get("timings") or {},
        "engine_info": v.get("engine_info") or {},
        "stage": v.get("stage"),
        "stages": v.get("stages") or pipeline.initial_stages(),
        "error": v.get("error"),
    }
    return out


def _schedule_status(v: dict | None) -> str:
    if not v:
        return "scheduled"
    return {"signed": "signed", "ready": "ready_to_sign"}.get(v["status"], "in_progress")


# ---------------------------------------------------------------- schedule, charts, samples


@router.get("/schedule")
def schedule(_s: dict = Depends(auth.require)):
    today = fixtures.today_local()
    latest: dict[str, dict] = {}
    for v in store.visits_for_day(today.isoformat()):
        latest[v["patient_id"]] = v  # ordered by created_at, so the last one wins
    appts = []
    for a in fixtures.schedule_template():
        p = fixtures.patient(a["patient_id"])
        v = latest.get(a["patient_id"])
        appts.append({
            "appt_id": a["appt_id"],
            "time": fixtures.appointment_time_iso(a["time"], today),
            "patient_id": a["patient_id"],
            "name": p["name"],
            "age": p["age"],
            "sex": p["sex"],
            "reason": a["reason"],
            "visit_type": a["visit_type"],
            "status": _schedule_status(v),
            "visit_id": v["id"] if v else None,
        })
    return {"date": today.isoformat(), "timezone": fixtures.practice().get("timezone"), "appointments": appts}


@router.post("/schedule/reset")
def reset_schedule(_s: dict = Depends(auth.require)):
    n = store.archive_day(fixtures.today_local().isoformat())
    store.audit(None, "schedule_reset", {"archived": n})
    return {"ok": True, "archived": n}


@router.get("/patients/{patient_id}")
def get_patient(patient_id: str, _s: dict = Depends(auth.require)):
    p = fixtures.patient(patient_id)
    if not p:
        raise HTTPException(404, "Patient not found.")
    return p


def _sample_summary(s: dict) -> dict:
    p = fixtures.patient(s["patient_id"]) or {}
    return {"id": s["id"], "title": s["title"], "patient_id": s["patient_id"], "patient_name": p.get("name"),
            "minutes": s["minutes"], "highlights": s.get("highlights", []), "template": s.get("template")}


@router.get("/samples")
def list_samples(_s: dict = Depends(auth.require)):
    return {"samples": [_sample_summary(s) for s in fixtures.samples()]}


@router.get("/samples/{sample_id}")
def get_sample(sample_id: str, _s: dict = Depends(auth.require)):
    s = fixtures.sample(sample_id)
    if not s:
        raise HTTPException(404, "Sample not found.")
    out = _sample_summary(s)
    out["speaker_roles"] = s.get("speaker_roles", {})
    out["turns"] = [{"id": t["id"], "speaker": t["speaker"], "text": t["text"], "start_ms": t["start_ms"],
                     "end_ms": t["end_ms"]} for t in s["turns"]]
    return out


# ---------------------------------------------------------------- visits


@router.post("/visits")
def create_visit(body: VisitIn, session: dict = Depends(auth.require)):
    if not fixtures.patient(body.patient_id):
        raise HTTPException(404, "Patient not found.")
    v = store.create_visit(patient_id=body.patient_id, template=body.template, style=body.style,
                           engine=body.engine, specialty=_specialty(session, body.specialty))
    store.audit(v["id"], "visit_created", {"patient_id": body.patient_id, "template": body.template,
                                           "engine": body.engine})
    return {"visit_id": v["id"]}


@router.get("/visits/{visit_id}")
def get_visit(visit_id: str, _s: dict = Depends(auth.require)):
    return visit_view(_visit_or_404(visit_id))


@router.post("/visits/{visit_id}/consent")
def consent(visit_id: str, body: ConsentIn, _s: dict = Depends(auth.require)):
    v = _visit_or_404(visit_id)
    _not_signed(v)
    if not body.consented:
        raise HTTPException(400, "Recording needs consent from everyone present.")
    at = store.now_iso()
    store.update_visit(visit_id, consent_at=at)
    # Consent lives in the audit log only; it is never written into the note.
    store.audit(visit_id, "consent_logged", {"attestation": body.attestation})
    return {"ok": True, "consent_at": at}


@router.get("/visits/{visit_id}/keyterms")
def keyterms(visit_id: str, _s: dict = Depends(auth.require)):
    v = _visit_or_404(visit_id)
    chart = fixtures.patient(v["patient_id"]) or {}
    return stt.build_keyterms(fixtures.keyterm_seed(chart), v.get("specialty") or "family_medicine")


def _audio_cap_reached() -> bool:
    return store.usage_get("audio_seconds") >= config.settings.daily_audio_minutes * 60


@router.post("/stt/token")
async def stt_token(body: TokenIn, _s: dict = Depends(auth.require)):
    v = _visit_or_404(body.visit_id)
    _not_signed(v)
    if not v.get("consent_at"):
        raise HTTPException(409, "Log consent before recording.")
    if not config.settings.speech_ready:
        raise HTTPException(503, "Speech is not configured on this server (no AssemblyAI API key). Try a sample visit instead.")
    if _audio_cap_reached():
        raise HTTPException(409, "The demo's daily recording limit has been reached. It resets at midnight UTC; sample visits still work.")
    chart = fixtures.patient(v["patient_id"]) or {}
    terms = stt.build_keyterms(fixtures.keyterm_seed(chart), v.get("specialty") or "family_medicine")
    try:
        tok = await stt.mint_token(config.settings.max_visit_minutes * 60)
    except stt.SttError as exc:
        # The full reason is for operators (logs and /selftest/), not for the doctor's screen.
        log.warning("live token not issued: %s", exc)
        raise HTTPException(502, "The live transcript service is unavailable right now.")
    store.audit(v["id"], "stt_token_issued", {"keyterms": len(terms["live"]), "zone": tok.zone}, actor="system")
    return {"token": tok.token, "ws_url": stt.ws_url(terms["live"], tok.ws), "expires_in": tok.expires_in,
            "zone": tok.zone, "max_session_seconds": config.settings.max_visit_minutes * 60}


@router.get("/stt/audio/{link_id}")
def stt_audio(link_id: str):
    """A one-time link AssemblyAI fetches a recording from, only if it refused the upload.
    No session: the unguessable id is the credential, it expires, and it closes when the pass ends."""
    found = stt.linked_file(link_id)
    if not found:
        raise HTTPException(404, "Not found.")
    path, media_type = found
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.post("/visits/{visit_id}/live-turns")
def live_turns(visit_id: str, body: LiveTurnsIn, _s: dict = Depends(auth.require)):
    with store.visit_lock(visit_id):
        v = _visit_or_404(visit_id)
        _not_signed(v)
        existing = {t.get("id"): t for t in (v.get("live_turns") or []) if t.get("id")}
        order = list(existing)
        for t in body.turns[:2000]:
            tid = str(t.get("id") or f"lt{len(order) + 1}")
            if tid not in existing:
                order.append(tid)
            existing[tid] = {
                "id": tid, "speaker": str(t.get("speaker") or ""), "text": str(t.get("text") or "")[:4000],
                "start_ms": int(t.get("start_ms") or 0), "end_ms": int(t.get("end_ms") or 0),
            }
        merged = [existing[k] for k in order]
        prior = float(v.get("audio_seconds") or 0)
        new = max(prior, min(float(body.audio_seconds or 0), config.settings.max_visit_minutes * 60 + 60))
        if new > prior:
            store.usage_add("audio_seconds", new - prior)
        store.update_visit(visit_id, live_turns=merged, audio_seconds=new)
    return {"ok": True, "turns": len(merged)}


@router.post("/visits/{visit_id}/audio")
async def upload_audio(visit_id: str, file: UploadFile = File(...), _s: dict = Depends(auth.require)):
    v = _visit_or_404(visit_id)
    _not_signed(v)
    if not v.get("consent_at"):
        raise HTTPException(409, "Log consent before uploading a recording.")
    if not config.settings.speech_ready:
        raise HTTPException(503, "Speech is not configured on this server (no AssemblyAI API key).")
    if _audio_cap_reached():
        raise HTTPException(409, "The demo's daily recording limit has been reached. It resets at midnight UTC.")
    if pipeline.is_running(visit_id):
        raise HTTPException(409, "This visit is already processing.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXT:
        ctype = (file.content_type or "").split(";")[0].strip().lower()
        ext = MIME_EXT.get(ctype, "")
    if ext not in AUDIO_EXT:
        raise HTTPException(415, "Upload an audio file (webm, ogg, mp4/m4a, wav, mp3, aac or flac).")
    limit = config.settings.max_upload_mb * 1024 * 1024
    folder = config.settings.data_dir / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{visit_id}{ext}"
    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"The recording is larger than {config.settings.max_upload_mb} MB.")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "The recording is empty.")
    store.update_visit(visit_id, audio_path=str(dest))
    store.audit(visit_id, "audio_uploaded", {"bytes": size, "type": ext})
    await pipeline.start(visit_id, "final")
    return {"ok": True, "bytes": size}


@router.post("/visits/{visit_id}/process")
async def process(visit_id: str, body: ProcessIn, _s: dict = Depends(auth.require)):
    v = _visit_or_404(visit_id)
    _not_signed(v)
    if pipeline.is_running(visit_id):
        return {"ok": True, "already_running": True}
    sample_id = None
    if body.source == "final":
        if not v.get("consent_at"):
            raise HTTPException(409, "Log consent before processing a recording.")
        has_audio = bool(v.get("audio_path")) and Path(v["audio_path"]).exists()
        has_final = bool(v.get("transcript")) and bool((v.get("engine_info") or {}).get("final_model"))
        if not has_audio and not has_final:
            raise HTTPException(409, "Upload the recording first.")
    elif body.source == "live":
        if not v.get("consent_at"):
            raise HTTPException(409, "Log consent before processing a recording.")
        if not stt.normalise_live_turns(v.get("live_turns") or []):
            raise HTTPException(409, "No live transcript has been captured for this visit yet.")
    else:
        sample_id = body.sample_id or v.get("sample_id")
        s = fixtures.sample(sample_id) if sample_id else None
        if not s:
            raise HTTPException(404, "That sample visit does not exist.")
        if s["patient_id"] != v["patient_id"]:
            raise HTTPException(400, "That sample belongs to a different patient.")
    await pipeline.start(visit_id, body.source, sample_id)
    return {"ok": True}


@router.get("/visits/{visit_id}/status")
def status(visit_id: str, _s: dict = Depends(auth.require)):
    v = _visit_or_404(visit_id)
    stage = v.get("stage") or ("done" if v["status"] in ("ready", "signed") else "queued")
    return {"stage": stage, "stages": v.get("stages") or pipeline.initial_stages(), "error": v.get("error"),
            "status": v["status"]}


# ---------------------------------------------------------------- review and edits


@router.patch("/visits/{visit_id}/note")
def patch_note(visit_id: str, body: NotePatch, _s: dict = Depends(auth.require)):
    with store.visit_lock(visit_id):
        v = _visit_or_404(visit_id)
        _not_signed(v)
        note = v.get("note")
        if not note or v.get("status") != "ready":
            raise HTTPException(409, "The note is still being prepared; edit it once it is ready.")
        if body.item_id in ("visit_summary", "patient_instructions"):
            note[body.item_id] = (body.text or "").strip()
            store.audit(visit_id, "note_field_edited", {"field": body.item_id})
        elif body.item_id == "follow_up":
            note["follow_up"] = {"text": (body.text or "").strip(), "evidence": (note.get("follow_up") or {}).get("evidence", [])}
            store.audit(visit_id, "note_field_edited", {"field": "follow_up"})
        elif body.item_id:
            section, item = notemodel.find_item(note, body.item_id)
            if not item:
                raise HTTPException(404, "That line is not in the note.")
            text = (body.text or "").strip()
            if not text:
                section["items"] = [i for i in section["items"] if i["id"] != body.item_id]
                if not section["items"]:
                    section["items"] = [{"id": f"{section['id']}-nd", "text": "Not discussed", "evidence": [],
                                         "status": "not_discussed", "verdict": "unchecked", "verdict_reason": "",
                                         "edited": True}]
                store.audit(visit_id, "note_line_removed", {"item_id": body.item_id, "verdict": item.get("verdict")})
            else:
                item["verdict_before_edit"] = item.get("verdict")
                item["text"] = text
                item["edited"] = True
                item["verdict"] = "unchecked"
                item["verdict_reason"] = "Edited by the clinician."
                store.audit(visit_id, "note_line_edited", {"item_id": body.item_id})
        elif body.section_id and body.items is not None:
            section = notemodel.section_by_id(note, body.section_id)
            if not section:
                raise HTTPException(404, "That section is not in the note.")
            new_items = []
            for n, it in enumerate(body.items, 1):
                text = str(it.get("text") or "").strip()
                if not text:
                    continue
                new_items.append({
                    "id": str(it.get("id") or f"{section['id']}-e{n}"), "text": text,
                    "evidence": [str(e) for e in it.get("evidence") or []],
                    "status": it.get("status") if it.get("status") in ("documented", "negative", "not_discussed") else "documented",
                    "verdict": "unchecked", "verdict_reason": "Edited by the clinician.", "edited": True,
                })
            section["items"] = new_items or [{"id": f"{section['id']}-nd", "text": "Not discussed", "evidence": [],
                                              "status": "not_discussed", "verdict": "unchecked",
                                              "verdict_reason": "", "edited": True}]
            store.audit(visit_id, "note_section_edited", {"section_id": body.section_id})
        else:
            raise HTTPException(400, "Send {item_id, text} or {section_id, items}.")
        v = store.update_visit(visit_id, note=note)
    return visit_view(v)


@router.post("/visits/{visit_id}/ask")
async def ask(visit_id: str, body: AskIn, _s: dict = Depends(auth.require)):
    v = _visit_or_404(visit_id)
    _not_signed(v)
    note = v.get("note")
    if not note or v.get("status") != "ready":
        raise HTTPException(409, "The note is still being prepared; rewrite it once it is ready.")
    if not config.settings.notes_ready:
        raise HTTPException(503, "Rewriting needs the Claude API key on the server. You can still edit lines by hand.")
    section = notemodel.section_by_id(note, body.section_id)
    if not section:
        raise HTTPException(404, "That section is not in the note.")
    if store.usage_get("note_runs") >= config.settings.daily_note_runs:
        raise HTTPException(409, "The demo's daily note limit has been reached. It resets at midnight UTC.")
    turns = v.get("transcript") or []
    store.usage_add("note_runs", 0.25)
    try:
        raw_items, _meta = await notes.rewrite_section(turns, section, body.instruction, v.get("engine") or "sonnet")
    except notes.NoteError as exc:
        raise HTTPException(502, str(exc))
    template = v.get("template") or "soap"
    temp = notemodel.normalise({"sections": {section["id"]: {"items": raw_items}}}, template, turns)
    new_section = notemodel.section_by_id(temp, section["id"])
    chart = fixtures.patient(v["patient_id"])
    det = verify.run({"sections": [new_section], "medications": []}, turns, chart)
    model_verdicts = None
    try:
        model_verdicts, _vm = await notes.verify_note({"sections": [new_section], "medications": []}, turns)
    except notes.NoteError:
        model_verdicts = None
    notemodel.apply_verdicts({"sections": [new_section], "medications": []}, model_verdicts, det,
                             model_note="" if model_verdicts is not None else "Verifier unavailable; deterministic checks only.")
    with store.visit_lock(visit_id):
        v = _visit_or_404(visit_id)
        _not_signed(v)
        note = v["note"]
        for i, sec in enumerate(note["sections"]):
            if sec["id"] == section["id"]:
                note["sections"][i] = new_section
        store.update_visit(visit_id, note=note)
    store.audit(visit_id, "section_rewritten", {"section_id": section["id"], "instruction": body.instruction[:200]})
    return {"section": new_section}


@router.post("/visits/{visit_id}/meds/{med_id}/confirm")
async def confirm_med(visit_id: str, med_id: str, body: ConfirmIn, _s: dict = Depends(auth.require)):
    v = _visit_or_404(visit_id)
    _not_signed(v)
    if v.get("status") != "ready" or not v.get("note"):
        raise HTTPException(409, "The note is not ready for review yet.")
    note = v["note"]
    mc = v.get("medication_check") or drugs_bridge.fallback_medication_check(note.get("medications", []), "Not checked")
    if not any(r.get("id") == med_id for r in mc.get("medications", [])):
        raise HTTPException(404, "That medicine is not in this visit.")
    corrected = {k: str(val).strip() for k, val in (body.corrected or {}).items()
                 if k in ("name", "dose", "unit", "frequency") and val is not None}
    if corrected:
        for m in note["medications"]:
            if m["id"] == med_id:
                m.update(corrected)
                m["edited"] = True
                m["verdict"] = "unchecked"
                m["verdict_reason"] = "Corrected by the clinician."
        v["note"] = note
        v["medication_check"] = mc
        mc = await pipeline.recheck_medications(v)
        store.audit(visit_id, "medication_corrected", {"med_id": med_id, "fields": sorted(corrected)})
    with store.visit_lock(visit_id):
        fresh = _visit_or_404(visit_id)
        _not_signed(fresh)
        if not corrected:
            mc = fresh.get("medication_check") or mc
        for row in mc["medications"]:
            if row.get("id") == med_id:
                row["confirmed"] = bool(body.confirmed)
                if body.dose_confirmed is not None:
                    row["dose_confirmed"] = bool(body.dose_confirmed)
                elif not body.confirmed:
                    row["dose_confirmed"] = False
                if corrected:
                    row.update({k: val for k, val in corrected.items()})
        store.update_visit(visit_id, medication_check=mc, **({"note": note} if corrected else {}))
    store.audit(visit_id, "medication_confirmed", {"med_id": med_id, "confirmed": body.confirmed,
                                                   "dose_confirmed": body.dose_confirmed})
    return mc


@router.post("/visits/{visit_id}/cards/{card_id}/ack")
def ack_card(visit_id: str, card_id: str, body: AckIn, _s: dict = Depends(auth.require)):
    with store.visit_lock(visit_id):
        v = _visit_or_404(visit_id)
        _not_signed(v)
        cards = (v.get("medication_check") or {}).get("cards") or []
        card = next((c for c in cards if c.get("id") == card_id), None)
        if not card:
            raise HTTPException(404, "That alert is not in this visit.")
        acks = v.get("cards_ack") or {}
        acks[card_id] = {"reason": body.reason.strip(), "at": store.now_iso()}
        store.update_visit(visit_id, cards_ack=acks)
    store.audit(visit_id, "alert_acknowledged", {"card_id": card_id, "indicator": card.get("indicator"),
                                                 "summary": card.get("summary"), "reason": body.reason.strip()})
    return {"ok": True}


@router.post("/visits/{visit_id}/sign")
def sign(visit_id: str, _s: dict = Depends(auth.require)):
    with store.visit_lock(visit_id):
        v = _visit_or_404(visit_id)
        if v.get("status") == "signed":
            return {"ok": True, "signed_at": v.get("signed_at")}
        blockers = notemodel.blockers(v)
        if blockers:
            return JSONResponse({"detail": "Resolve these before signing.", "blockers": blockers}, status_code=409)
        at = store.now_iso()
        audio = v.get("audio_path")
        store.update_visit(visit_id, status="signed", signed_at=at, stage="done", audio_path=None)
    if audio:
        try:
            Path(audio).unlink(missing_ok=True)
        except OSError:
            pass
    store.audit(visit_id, "signed", {"by": auth.DEMO_PRESCRIBER["id"]})
    return {"ok": True, "signed_at": at}


@router.get("/visits/{visit_id}/export.txt")
def export_txt(visit_id: str, _s: dict = Depends(auth.require)):
    v = _visit_or_404(visit_id)
    if not v.get("note"):
        raise HTTPException(409, "There is no note to export yet.")
    text = export.render(v, fixtures.patient(v["patient_id"]))
    name = re.sub(r"[^A-Za-z0-9_-]", "", v["id"])
    return PlainTextResponse(text, headers={"Content-Disposition": f'inline; filename="note-{name}.txt"'})


# ---------------------------------------------------------------- self-test


@router.get("/selftest")
async def run_selftest(_s: dict = Depends(auth.require)):
    return await selftest.selftest()


# Public, rate-limited checks: no session needed, nothing secret or real in them.
# They let the deployment be verified without anyone typing credentials.

@router.get("/selftest/public")
async def public_selftest():
    return await selftest.selftest(force_cached_window=3600)


@router.get("/eval/public")
async def public_eval():
    """End-to-end accuracy run on the five voiced sample visits (at most every 6 hours)."""
    return evaluate.status()
