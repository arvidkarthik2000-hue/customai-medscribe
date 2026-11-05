"""GET /api/selftest: check every live dependency once, report plainly, never leak secrets."""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .. import config
from . import fixtures, notes, store, stt

log = logging.getLogger("medscribe.selftest")

# The voiced version of sample visit 1 (synthetic voices), sent the way a real recording is.
SAMPLE_ID = "s1-knee-warfarin"
SAMPLE_FILE = Path(__file__).parent / "data" / "eval_audio" / f"{SAMPLE_ID}.mp3"
# Its first 24 seconds as 16 kHz mono 16-bit PCM, the format the browser streams.
LIVE_FILE = Path(__file__).parent / "data" / "eval_audio" / "s1-opening-16k.pcm"
LIVE_EXPECT = ("knee", "fall", "weeks", "stairs")  # words said in that opening
CHUNK = 3200  # 100 ms of audio
PACE_S = 0.1  # send at real-time pace, as a microphone does (tests set 0)
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
        return _result("Salient speech: live session token", None, "Skipped: the speech key is not set.", t0)
    try:
        tok = await stt.mint_token(120)
    except stt.SttError as exc:
        return _result("Salient speech: live session token", False, str(exc), t0)
    where = {"us": "US data zone", "eu": "EU data zone", "edge": "nearest region"}.get(tok.zone, tok.zone)
    detail = f"Live session token issued ({where})."
    if tok.refused:
        detail += f" The {len(tok.refused)} preferred route(s) refused it first; details are in the server log."
        log.info("self-test token: refused first: %s", "; ".join(tok.refused))
    return _result("Salient speech: live session token", True, detail, t0)


async def _final_pass() -> dict:
    """The recorded-visit path exactly as a visit uses it: upload, Medical Mode, keyterms, delete."""
    t0 = time.monotonic()
    name = "Salient speech: final pass (upload + medical vocabulary)"
    if not config.settings.assemblyai_api_key:
        return _result(name, None, "Skipped: the speech key is not set.", t0)
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
    mm = "medical vocabulary on" if meta.get("medical_mode") else "medical vocabulary NOT applied"
    via = "uploaded" if meta.get("audio_via") == "upload" else "fetched from a one-time link"
    detail = (f"{meta.get('audio_duration')} s visit {via}, transcribed by Salient medical speech, {mm}, "
              f"{meta.get('keyterms_used')} drug terms, {words} words, {speakers} speakers.")
    log.info("self-test final pass ran on %s", meta.get("speech_model_used"))
    if meta.get("warnings"):
        detail += " " + " ".join(meta["warnings"])
    return _result(name, bool(meta.get("medical_mode")), detail, t0)


def _close_reason(exc: Exception) -> str:
    rcvd = getattr(exc, "rcvd", None)
    if rcvd is not None:
        return f"closed with code {rcvd.code}{': ' + rcvd.reason if rcvd.reason else ''}"
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        return f"refused the connection with HTTP {response.status_code}"
    return exc.__class__.__name__


async def _live_stream() -> dict:
    """The live path exactly as the browser uses it: a token, the same WebSocket URL and parameters,
    100 ms chunks at real-time pace, then Terminate. Checks the model and Medical Mode AssemblyAI
    reports in Begin, and that the captions contain words said in the audio."""
    t0 = time.monotonic()
    name = "Salient speech: live stream (captions)"
    s = config.settings
    if not s.assemblyai_api_key:
        return _result(name, None, "Skipped: the speech key is not set.", t0)
    if not LIVE_FILE.exists():
        return _result(name, None, "Skipped: the live test audio is missing from this build.", t0)
    try:
        import websockets
    except ImportError:
        return _result(name, None, "Skipped: the websockets library is not installed.", t0)
    try:
        tok = await stt.mint_token(120)
    except stt.SttError as exc:
        return _result(name, False, f"No token, so no stream: {exc}", t0)
    sample = next((x for x in fixtures.samples() if x.get("id") == SAMPLE_ID), None) or {}
    chart = fixtures.patient(sample.get("patient_id")) if sample.get("patient_id") else None
    terms = stt.build_keyterms(fixtures.keyterm_seed(chart or {}), "family_medicine")["live"]
    upstream = f"{stt.ws_url(terms, tok.ws)}&token={quote(tok.token)}"
    base = (s.public_base_url or "").rstrip("/")
    if base:  # through our own relay, exactly the browser's path
        ticket = stt.issue_ticket(tok, stt.ws_url(terms, tok.ws))
        url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"/api/stt/stream?ticket={ticket}"
        via = "through this server's relay"
    else:
        url, via = upstream, "directly"
    pcm = LIVE_FILE.read_bytes()
    seconds = len(pcm) / 32000
    begin: dict = {}
    latest: dict[int, dict] = {}
    finals = 0
    termination: dict = {}
    try:
        async with websockets.connect(url, open_timeout=10, close_timeout=5, max_size=2 ** 20, proxy=None) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if first.get("type") != "Begin":
                return _result(name, False, f"The live session did not start: {str(first)[:200]}", t0)
            begin = first

            async def send() -> None:
                for i in range(0, len(pcm), CHUNK):
                    await ws.send(pcm[i:i + CHUNK])
                    await asyncio.sleep(PACE_S)
                for _ in range(15):  # 1.5 s of silence lets the last turn end
                    await ws.send(b"\x00" * CHUNK)
                    await asyncio.sleep(PACE_S)
                await ws.send(json.dumps({"type": "Terminate"}))

            async def receive() -> None:
                nonlocal finals, termination
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    msg = json.loads(raw)
                    kind = msg.get("type")
                    if kind == "Turn":
                        latest[int(msg.get("turn_order") or 0)] = msg
                        finals += bool(msg.get("end_of_turn"))
                    elif kind == "Termination":
                        termination = msg
                        return
                    elif kind == "Error" or msg.get("error"):
                        raise stt.SttError(f"The live service reported an error: {str(msg.get('error') or msg)[:200]}")

            sender = asyncio.create_task(send())
            try:
                await asyncio.wait_for(receive(), timeout=seconds + 25)
            finally:
                sender.cancel()
    except stt.SttError as exc:
        return _result(name, False, str(exc), t0)
    except asyncio.TimeoutError:
        return _result(name, False, "The live session did not finish in time (no Termination message).", t0)
    except (websockets.exceptions.WebSocketException, OSError) as exc:
        return _result(name, False, f"The live service {_close_reason(exc)}.", t0)
    cfg = begin.get("configuration") or {}
    # Through the relay, Begin says {"model": "salient-live", "medical": bool}; directly, it names the
    # provider's model and domain.
    medical = bool(cfg.get("medical")) or cfg.get("domain") == "medical-v1"
    model_ok = cfg.get("model") in ("salient-live", s.live_speech_model)
    text = " ".join((latest[k].get("transcript") or "") for k in sorted(latest)).strip()
    low = text.lower()
    heard = [w for w in LIVE_EXPECT if w in low]
    speakers = sorted({t.get("speaker_label") for t in latest.values()
                       if t.get("speaker_label") and t.get("speaker_label") != "UNKNOWN"})
    ok = model_ok and medical and len(heard) >= 2 and bool(termination)
    detail = (f"Streamed {seconds:.0f} s of the voiced sample visit {via}: "
              f"{'medical vocabulary on' if medical else 'medical vocabulary NOT applied'}, "
              f"{len(latest)} turns ({finals} final), {len(text.split())} words, speakers {', '.join(speakers) or 'none'}; "
              f"heard {len(heard)} of {len(LIVE_EXPECT)} check words. Start: \u201c{text[:90]}\u201d")
    if not model_ok:
        detail += " The live service did not run the requested model."
        log.warning("self-test live stream ran %s", cfg.get("model"))
    return _result(name, ok, detail, t0)


def _formulary() -> dict:
    """Which formulary answers coverage questions (no network call: the loader runs in the background)."""
    t0 = time.monotonic()
    from ..drugs import formulary

    st = formulary.loader_status()
    state = st.get("state")
    if st.get("source") == "cms_puf":
        ok = True
    elif state == "failed":
        ok = False
    else:  # loading, or switched off: the sample formulary answers meanwhile
        ok = None
    return _result("Medicare Part D formulary (CMS)", ok, f"{st.get('label')}. {st.get('detail') or state}", t0)


async def _claude() -> list[dict]:
    t0 = time.monotonic()
    s = config.settings
    if not s.notes_ready:
        return [_result("Salient notes: draft (structured output)", None, "Skipped: the note-engine key is not set.", t0),
                _result("Salient notes: verifier", None, "Skipped: the note-engine key is not set.", t0)]
    try:
        note, meta = await notes.generate_note(TINY_TURNS, None, template="soap", specialty="family_medicine",
                                               style="concise", engine="sonnet", visit_date="2026-01-01", effort="low")
        meds = [m["name"].lower() for m in note.get("medications", [])]
        ok = any("lisinopril" in m for m in meds)
        first = _result("Salient notes: draft (structured output)", ok,
                        f"Returned a valid note in {meta['seconds']} s"
                        + ("" if ok else " but missed the medicine (lisinopril)") + ".", t0)
    except notes.NoteError as exc:
        return [_result("Salient notes: draft (structured output)", False, str(exc), t0),
                _result("Salient notes: verifier", None, "Skipped because the note call failed.", t0)]
    t1 = time.monotonic()
    try:
        verdicts, vmeta = await notes.verify_note(note, TINY_TURNS)
        second = _result("Salient notes: verifier", bool(verdicts),
                         f"Graded {vmeta.get('graded', 0)} of {vmeta.get('lines', 0)} lines.", t1)
    except notes.NoteError as exc:
        second = _result("Salient notes: verifier", False, str(exc), t1)
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
        _live_stream(),
        _final_pass(),
        _claude(),
        _drug_client("RxNorm (RxNav)", "app.drugs.rxnav", f"{s.rxnav_base}/version.json"),
        _drug_client("openFDA drug labels", "app.drugs.openfda", f"{s.openfda_base}/drug/label.json", fda_params),
    )
    flat: list[dict] = []
    for r in results:
        flat.extend(r if isinstance(r, list) else [r])
    flat.append(_formulary())
    return flat


_lock = asyncio.Lock()
# A restart (a deploy, or a changed key in the host's settings) gets a fresh run.
_PROCESS = f"{int(time.time())}-{id(_lock)}"


async def selftest(force_cached_window: int = MIN_INTERVAL_S) -> dict:
    """Run the checks, or return the last run if it is recent and from this deploy. Callers that
    arrive during a run wait for it instead of starting another (each run costs a little)."""
    async with _lock:
        return await _selftest(force_cached_window)


async def _selftest(force_cached_window: int) -> dict:
    cached, at = store.kv_get("selftest")
    if cached and (cached.get("build") != config.build_id() or cached.get("process") != _PROCESS):
        cached = None  # a new deploy or restart always gets a fresh run
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
        "process": _PROCESS,
        "cached": False,
    }
    store.kv_set("selftest", out)
    return out
