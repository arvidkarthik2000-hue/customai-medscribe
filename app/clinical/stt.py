"""AssemblyAI: browser streaming tokens and the final (pre-recorded) pass.

Live:  the server mints a temporary token; the browser opens the WebSocket itself
       (Universal-3.6 Pro Realtime, Medical Mode, speaker labels, keyterms).
Final: the server uploads the visit recording and transcribes it with
       Universal-3.5 Pro, Medical Mode, speaker labels and up to 1,000 keyterms,
       then deletes the transcript at AssemblyAI and the local audio file.

The API key is sent only in the Authorization header and never appears in a
message, log line or response.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .. import config
from . import drugs_bridge

log = logging.getLogger("medscribe.stt")

LIVE_KEYTERMS = 100
FINAL_KEYTERMS = 1000
TOKEN_TTL = 600

# Tests inject an httpx.MockTransport here.
transport: httpx.AsyncBaseTransport | None = None


class SttError(Exception):
    """A plain-words problem with speech-to-text."""


class SttNotConfigured(SttError):
    pass


def _settings():
    return config.settings


def hosts() -> dict[str, str]:
    s = _settings()
    base = (getattr(s, "assemblyai_test_base", "") or "").rstrip("/")
    if base:  # tests: a local fake AssemblyAI server
        ws_base = re.sub(r"^http", "ws", base)
        return {"token": f"{base}/v1/token", "legacy": f"{base}/v3/token", "ws": f"{ws_base}/v3/ws", "api": base}
    region = (s.assemblyai_region or "global").lower()
    if region == "us":
        return {"token": "https://token.us.assemblyai.com/v1/token",
                "legacy": "https://streaming.us.assemblyai.com/v3/token",
                "ws": "wss://streaming.us.assemblyai.com/v3/ws", "api": "https://api.assemblyai.com"}
    if region == "eu":
        return {"token": "https://token.eu.assemblyai.com/v1/token",
                "legacy": "https://streaming.eu.assemblyai.com/v3/token",
                "ws": "wss://streaming.eu.assemblyai.com/v3/ws", "api": "https://api.eu.assemblyai.com"}
    return {"token": "https://token.assemblyai.com/v1/token",
            "legacy": "https://streaming.assemblyai.com/v3/token",
            "ws": "wss://streaming.assemblyai.com/v3/ws", "api": "https://api.assemblyai.com"}


def _client(timeout: float | httpx.Timeout = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=transport)


def _key() -> str:
    key = _settings().assemblyai_api_key
    if not key:
        raise SttNotConfigured("Speech is not configured on this server (ASSEMBLYAI_API_KEY is not set).")
    return key


def _short(text: str, n: int = 200) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:n]


def _err_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        msg = data.get("error") or data.get("detail") or data.get("message") or ""
        if isinstance(msg, (list, dict)):
            msg = json.dumps(msg)[:200]
    except ValueError:
        msg = resp.text
    return f"HTTP {resp.status_code}{': ' + _short(str(msg)) if msg else ''}"


# ---------------------------------------------------------------- keyterms


def clean_keyterms(terms: list[str], limit: int) -> list[str]:
    out, seen = [], set()
    for t in terms:
        t = re.sub(r"\s+", " ", str(t or "")).strip(" ,.;")
        if not t or len(t) > 50 or len(t.split()) > 6:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def build_keyterms(chart_terms: list[str], specialty: str) -> dict[str, list[str]]:
    """The patient's medicines, allergies and problems first, then the specialty's drugs."""
    pool = list(chart_terms) + drugs_bridge.brand_and_generic_terms(specialty)
    return {"live": clean_keyterms(pool, LIVE_KEYTERMS), "final": clean_keyterms(pool, FINAL_KEYTERMS)}


# ---------------------------------------------------------------- live streaming


def ws_url(keyterms: list[str]) -> str:
    s = _settings()
    params = {
        "speech_model": s.live_speech_model,
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "min_turn_silence": 800,
        "max_turn_silence": 3600,
        "inactivity_timeout": 300,
    }
    terms = clean_keyterms(keyterms, LIVE_KEYTERMS)
    if terms:
        params["keyterms_prompt"] = json.dumps(terms)
    return f"{hosts()['ws']}?{urlencode(params)}"


async def mint_token(max_session_seconds: int) -> tuple[str, int]:
    key = _key()
    h = hosts()
    max_session_seconds = max(60, int(max_session_seconds))
    first = ""
    async with _client(15.0) as c:
        try:
            r = await c.post(
                h["token"],
                headers={"Authorization": key, "Content-Type": "application/json"},
                json={"product": "streaming", "expires_in_seconds": TOKEN_TTL,
                      "max_session_duration_seconds": max_session_seconds},
            )
            if r.status_code == 200 and (r.json() or {}).get("token"):
                data = r.json()
                return data["token"], int(data.get("expires_in_seconds") or TOKEN_TTL)
            first = _err_detail(r)
        except (httpx.HTTPError, ValueError) as exc:
            first = exc.__class__.__name__
        try:
            r = await c.get(
                h["legacy"], headers={"Authorization": key},
                params={"expires_in_seconds": TOKEN_TTL, "max_session_duration_seconds": max_session_seconds},
            )
            if r.status_code == 200 and (r.json() or {}).get("token"):
                data = r.json()
                return data["token"], int(data.get("expires_in_seconds") or TOKEN_TTL)
            second = _err_detail(r)
        except (httpx.HTTPError, ValueError) as exc:
            second = exc.__class__.__name__
    if "401" in first or "401" in second:
        raise SttError("AssemblyAI rejected the API key (401). Check ASSEMBLYAI_API_KEY on the server.")
    raise SttError(f"AssemblyAI would not issue a streaming token ({first}; fallback {second}).")


# ---------------------------------------------------------------- final pass


async def _file_chunks(path: Path, size: int = 1 << 20):
    with open(path, "rb") as f:
        while True:
            block = await asyncio.to_thread(f.read, size)
            if not block:
                break
            yield block


def utterances_to_turns(data: dict) -> tuple[list[dict], list[str]]:
    utts = data.get("utterances") or []
    if not utts and (data.get("text") or "").strip():
        words = data.get("words") or []
        utts = [{"speaker": "A", "text": data["text"],
                 "start": (words[0].get("start") if words else 0) or 0,
                 "end": (words[-1].get("end") if words else 0) or 0,
                 "confidence": data.get("confidence")}]
    turns, removed_all = [], []
    for u in utts:
        raw = (u.get("text") or "").strip()
        if not raw:
            continue
        clean, removed = drugs_bridge.strip_added_brands(raw)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
        for b in removed:
            if b not in removed_all:
                removed_all.append(b)
        turn = {
            "id": f"t{len(turns) + 1}",
            "speaker": str(u.get("speaker") or "A"),
            "role": "unknown",
            "text": clean,
            "start_ms": int(u.get("start") or 0),
            "end_ms": int(u.get("end") or 0),
        }
        if clean != raw:
            turn["raw_text"] = raw
        if u.get("confidence") is not None:
            turn["confidence"] = round(float(u["confidence"]), 3)
        turns.append(turn)
    return turns, removed_all


async def create_and_poll(c: httpx.AsyncClient, key: str, body: dict, *, timeout_s: float = 900.0,
                          sleep=asyncio.sleep) -> tuple[dict, list[str]]:
    """POST /v2/transcript (retrying without a rejected option, with a warning), then poll."""
    api = hosts()["api"]
    headers = {"Authorization": key, "Content-Type": "application/json"}
    warnings: list[str] = []
    body = dict(body)
    for _ in range(4):
        r = await c.post(f"{api}/v2/transcript", headers=headers, json=body)
        if r.status_code in (200, 201):
            break
        if r.status_code == 401:
            raise SttError("AssemblyAI rejected the API key (401). Check ASSEMBLYAI_API_KEY on the server.")
        detail = _err_detail(r)
        low = detail.lower()
        if r.status_code in (400, 422):
            if "keyterm" in low and "keyterms_prompt" in body:
                body.pop("keyterms_prompt")
                warnings.append(f"AssemblyAI rejected the keyterms, so they were not used ({detail}).")
                continue
            if ("speech_model" in low or "model" in low) and "speech_models" in body:
                body.pop("speech_models")
                warnings.append(f"AssemblyAI rejected the requested model, so its default model was used ({detail}).")
                continue
            if "domain" in low and "domain" in body:
                body.pop("domain")
                warnings.append(f"Medical Mode was not applied: AssemblyAI rejected it ({detail}).")
                continue
            if "language" in low and "language_code" in body:
                body.pop("language_code")
                body["language_detection"] = True
                warnings.append(f"Language code rejected; language detection used instead ({detail}).")
                continue
        raise SttError(f"AssemblyAI would not start the transcription ({detail}).")
    else:
        raise SttError("AssemblyAI would not start the transcription after several attempts.")
    tid = (r.json() or {}).get("id")
    if not tid:
        raise SttError("AssemblyAI did not return a transcript id.")
    delay, deadline = 1.0, time.monotonic() + timeout_s
    while True:
        p = await c.get(f"{api}/v2/transcript/{tid}", headers={"Authorization": key})
        if p.status_code != 200:
            raise SttError(f"AssemblyAI status check failed ({_err_detail(p)}).")
        data = p.json() or {}
        status = data.get("status")
        if status == "completed":
            data["_body"] = body
            return data, warnings
        if status == "error":
            raise SttError(f"AssemblyAI could not transcribe the audio: {_short(str(data.get('error') or 'unknown error'))}.")
        if time.monotonic() > deadline:
            raise SttError("AssemblyAI took too long to transcribe the audio (timed out).")
        await sleep(delay)
        delay = min(delay * 1.5, 5.0)


async def delete_transcript(c: httpx.AsyncClient, key: str, transcript_id: str) -> bool:
    try:
        r = await c.delete(f"{hosts()['api']}/v2/transcript/{transcript_id}", headers={"Authorization": key})
        return r.status_code in (200, 204)
    except httpx.HTTPError:
        return False


async def transcribe_file(path: str | os.PathLike, keyterms: list[str], *, sleep=asyncio.sleep) -> dict:
    """Upload, transcribe with Medical Mode, map to turns, delete at AssemblyAI."""
    key = _key()
    s = _settings()
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise SttError("The visit recording is missing or empty.")
    api = hosts()["api"]
    terms = clean_keyterms(keyterms, FINAL_KEYTERMS)
    async with _client(httpx.Timeout(60.0, read=300.0, write=300.0)) as c:
        try:
            up = await c.post(
                f"{api}/v2/upload",
                headers={"Authorization": key, "Content-Type": "application/octet-stream"},
                content=_file_chunks(path),
            )
        except httpx.HTTPError as exc:
            raise SttError(f"Could not upload the recording to AssemblyAI ({exc.__class__.__name__}).") from exc
        if up.status_code == 401:
            raise SttError("AssemblyAI rejected the API key (401). Check ASSEMBLYAI_API_KEY on the server.")
        if up.status_code not in (200, 201) or not (up.json() or {}).get("upload_url"):
            raise SttError(f"Could not upload the recording to AssemblyAI ({_err_detail(up)}).")
        body = {
            "audio_url": up.json()["upload_url"],
            "speech_models": [s.final_speech_model],
            "domain": "medical-v1",
            "speaker_labels": True,
            "language_code": "en_us",
        }
        if terms:
            body["keyterms_prompt"] = terms
        try:
            data, warnings = await create_and_poll(c, key, body, sleep=sleep)
        except httpx.HTTPError as exc:
            raise SttError(f"Lost the connection to AssemblyAI ({exc.__class__.__name__}).") from exc
        sent = data.pop("_body", body)
        turns, removed = utterances_to_turns(data)
        tid = data.get("id")
        deleted = await delete_transcript(c, key, tid) if tid else False
    if not deleted:
        warnings.append("Could not delete the transcript at AssemblyAI; it will expire on their retention schedule.")
    if not turns:
        raise SttError("AssemblyAI returned no speech for this recording.")
    return {
        "turns": turns,
        "removed_brands": removed,
        "meta": {
            "transcript_id": tid,
            "speech_model_used": data.get("speech_model_used") or (sent.get("speech_models") or ["default"])[0],
            "audio_duration": data.get("audio_duration"),
            "medical_mode": sent.get("domain") == "medical-v1",
            "keyterms_used": len(sent.get("keyterms_prompt") or []),
            "warnings": warnings,
        },
    }


def normalise_live_turns(turns: list[dict]) -> list[dict]:
    """Live turns from the browser, cleaned; brand names added in brackets removed."""
    out = []
    for t in turns or []:
        raw = re.sub(r"\s+", " ", str(t.get("text") or "")).strip()
        if not raw:
            continue
        clean, _removed = drugs_bridge.strip_added_brands(raw)
        clean = re.sub(r"\s{2,}", " ", clean).strip()
        speaker = str(t.get("speaker") or "").strip()
        if not speaker or speaker.upper() in ("PENDING", "UNKNOWN", "NONE"):
            speaker = "U"
        turn = {
            "id": str(t.get("id") or f"t{len(out) + 1}"),
            "speaker": speaker,
            "role": t.get("role") or "unknown",
            "text": clean,
            "start_ms": int(t.get("start_ms") or 0),
            "end_ms": int(t.get("end_ms") or 0),
        }
        if clean != raw:
            turn["raw_text"] = raw
        out.append(turn)
    return out


def renumber(turns: list[dict]) -> list[dict]:
    """Stable ids t1..tn in time order (the note cites these)."""
    ordered = sorted(turns, key=lambda t: (t.get("start_ms") or 0, t.get("end_ms") or 0))
    out = []
    for i, t in enumerate(ordered, 1):
        t = dict(t)
        t["id"] = f"t{i}"
        out.append(t)
    return out
