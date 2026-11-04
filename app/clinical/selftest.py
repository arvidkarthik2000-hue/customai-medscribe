"""GET /api/selftest: check every live dependency once, report plainly, never leak secrets."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from pathlib import Path

import httpx

from .. import config
from . import fixtures, notes, store, stt

# The voiced version of sample visit 1 (synthetic voices), sent the way a real recording is.
SAMPLE_ID = "s1-knee-warfarin"
SAMPLE_FILE = Path(__file__).parent / "data" / "eval_audio" / f"{SAMPLE_ID}.mp3"
MIN_INTERVAL_S = 600
TINY_TURNS = [
    {"id": "t1", "speaker": "A", "text": "How's the blood pressure been at home?"},
    {"id": "t2", "speaker": "B", "text": "Mostly around 128 over 80. I take my lisinopril 10 mg every morning."},
    {"id": "t3", "speaker": "A", "text": "Good. Any cough or swelling of the lips?"},
    {"id": "t4", "speaker": "B", "text": "No, nothing like that."},
    {"id": "t5", "speaker": "A", "text": "Today it's 126/78. Let's continue lisinopril 10 mg once daily."},
    {"id": "t6", "speaker": "A", "text": "Come back in 6 months, and keep checking it at home."},
]


def _result(name: str, ok: bool | None, detail: str, t0: float) -> dict:
    return {"name": name, "ok": ok, "detail": detail, "seconds": round(time.monotonic() - t0, 2)}


async def _token() -> dict:
    t0 = time.monotonic()
    if not config.settings.assemblyai_api_key:
        return _result("AssemblyAI streaming token", None, "Skipped: ASSEMBLYAI_API_KEY is not set.", t0)
    try:
        tok = await stt.mint_token(120)
    except stt.SttError as exc:
        return _result("AssemblyAI streaming token", False, str(exc), t0)
    where = {"us": "US data zone", "eu": "EU data zone", "edge": "edge routing (nearest region)"}.get(tok.zone, tok.zone)
    detail = f"Token issued for the live stream ({where}, {tok.ws.split('/')[2]})."
    if tok.refused:
        detail += " Refused first: " + "; ".join(tok.refused) + "."
    return _result("AssemblyAI streaming token", True, detail, t0)


async def _final_pass() -> dict:
    """The recorded-visit path exactly as a visit uses it: upload, Medical Mode, keyterms, delete."""
    t0 = time.monotonic()
    name = "AssemblyAI final pass (upload + Medical Mode)"
    if not config.settings.assemblyai_api_key:
        return _result(name, None, "Skipped: ASSEMBLYAI_API_KEY is not set.", t0)
    if not SAMPLE_FILE.exists():
        return _result(name, None, "Skipped: the voiced sample visit is missing from this build.", t0)
    sample = next((x for x in fixtures.samples() if x.get("id") == SAMPLE_ID), None) or {}
    chart = fixtures.patient(sample.get("patient_id")) if sample.get("patient_id") else None
    terms = stt.build_keyterms(fixtures.keyterm_seed(chart or {}), "family_medicine")["final"]
    try:
        tr = await stt.transcribe_file(SAMPLE_FILE, terms)
    except (stt.SttError, httpx.HTTPError) as exc:
        return _result(name, False, str(exc) or exc.__class__.__name__, t0)
    meta = tr["meta"]
    words = sum(len(t["text"].split()) for t in tr["turns"])
    speakers = len({t["speaker"] for t in tr["turns"]})
    mm = "Medical Mode on" if meta.get("medical_mode") else "Medical Mode NOT applied"
    via = "uploaded" if meta.get("audio_via") == "upload" else "fetched from a one-time link"
    detail = (f"{meta.get('audio_duration')} s visit {via}, transcribed with {meta.get('speech_model_used')}, {mm}, "
              f"{meta.get('keyterms_used')} keyterms, {words} words, {speakers} speakers.")
    if meta.get("warnings"):
        detail += " " + " ".join(meta["warnings"])
    return _result(name, bool(meta.get("medical_mode")), detail, t0)


async def _claude() -> list[dict]:
    t0 = time.monotonic()
    s = config.settings
    if not s.notes_ready:
        return [_result("Claude note (structured output)", None, "Skipped: no Claude API key is set.", t0),
                _result("Claude verifier", None, "Skipped: no Claude API key is set.", t0)]
    try:
        note, meta = await notes.generate_note(TINY_TURNS, None, template="soap", specialty="family_medicine",
                                               style="concise", engine="sonnet", visit_date="2026-01-01", effort="low")
        meds = [m["name"].lower() for m in note.get("medications", [])]
        ok = any("lisinopril" in m for m in meds)
        first = _result("Claude note (structured output)", ok,
                        f"{meta['model']} returned a valid note in {meta['seconds']} s"
                        + ("" if ok else " but missed the medicine (lisinopril)") + ".", t0)
    except notes.NoteError as exc:
        return [_result("Claude note (structured output)", False, str(exc), t0),
                _result("Claude verifier", None, "Skipped because the note call failed.", t0)]
    t1 = time.monotonic()
    try:
        verdicts, vmeta = await notes.verify_note(note, TINY_TURNS)
        second = _result("Claude verifier", bool(verdicts),
                         f"{vmeta.get('model')} graded {vmeta.get('graded', 0)} of {vmeta.get('lines', 0)} lines.", t1)
    except notes.NoteError as exc:
        second = _result("Claude verifier", False, str(exc), t1)
    return [first, second]


async def _drug_client(name: str, module: str, fallback_url: str, params: dict | None = None) -> dict:
    t0 = time.monotonic()
    try:
        mod = importlib.import_module(module)
        for fn_name in ("selftest", "ping", "health"):
            fn = getattr(mod, fn_name, None)
            if fn:
                res = fn()
                if inspect.isawaitable(res):
                    res = await asyncio.wait_for(res, 15)
                ok = bool(res.get("ok")) if isinstance(res, dict) else bool(res)
                detail = res.get("detail", "") if isinstance(res, dict) else ""
                return _result(name, ok, detail or ("Reachable." if ok else "Not reachable."), t0)
    except Exception:
        pass  # fall through to a direct request
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(fallback_url, params=params)
        ok = r.status_code == 200
        return _result(name, ok, "Reachable." if ok else f"HTTP {r.status_code}.", t0)
    except httpx.HTTPError as exc:
        return _result(name, False, f"Not reachable ({exc.__class__.__name__}).", t0)


async def run_checks() -> list[dict]:
    s = config.settings
    fda_params = {"search": 'openfda.generic_name:"lisinopril"', "limit": 1}
    if s.openfda_api_key:
        fda_params["api_key"] = s.openfda_api_key
    results = await asyncio.gather(
        _token(),
        _final_pass(),
        _claude(),
        _drug_client("RxNorm (RxNav)", "app.drugs.rxnav", f"{s.rxnav_base}/version.json"),
        _drug_client("openFDA drug labels", "app.drugs.openfda", f"{s.openfda_base}/drug/label.json", fda_params),
    )
    flat: list[dict] = []
    for r in results:
        flat.extend(r if isinstance(r, list) else [r])
    return flat


async def selftest(force_cached_window: int = MIN_INTERVAL_S) -> dict:
    cached, at = store.kv_get("selftest")
    if cached and cached.get("build") != config.build_id():
        cached = None  # a new deploy always gets a fresh run
    if cached and at:
        from datetime import datetime, timezone

        try:
            ran = datetime.fromisoformat(at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ran).total_seconds()
        except ValueError:
            age = force_cached_window + 1
        if age < force_cached_window:
            return {**cached, "cached": True, "next_run_in_s": int(force_cached_window - age)}
    checks = await run_checks()
    failed = [c for c in checks if c["ok"] is False]
    skipped = [c for c in checks if c["ok"] is None]
    out = {
        "ok": not failed and not skipped,
        "summary": ("All checks passed." if not failed and not skipped else
                    f"{len(failed)} failed, {len(skipped)} skipped."),
        "checks": checks,
        "ran_at": store.now_iso(),
        "build": config.build_id(),
        "cached": False,
    }
    store.kv_set("selftest", out)
    return out
