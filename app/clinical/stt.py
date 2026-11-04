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
import mimetypes
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from .. import config
from . import drugs_bridge

log = logging.getLogger("medscribe.stt")

LIVE_KEYTERMS = 100
FINAL_KEYTERMS = 1000       # AssemblyAI's cap on keyterm phrases for a pre-recorded file
FINAL_KEYTERM_WORDS = 600   # keeps the prompt well under the model's limit (about 2,700 text tokens)
TOKEN_TTL = 600

# Tests inject an httpx.MockTransport here.
transport: httpx.AsyncBaseTransport | None = None


class SttError(Exception):
    """A plain-words problem with speech-to-text."""


class SttNotConfigured(SttError):
    pass


class SttRefused(SttError):
    """AssemblyAI answered 401 or 403."""


def _settings():
    return config.settings


_ZONES = {  # token service, older /v3/token service, WebSocket
    "us": ("https://token.us.assemblyai.com/v1/token", "https://streaming.us.assemblyai.com/v3/token",
           "wss://streaming.us.assemblyai.com/v3/ws"),
    "eu": ("https://token.eu.assemblyai.com/v1/token", "https://streaming.eu.assemblyai.com/v3/token",
           "wss://streaming.eu.assemblyai.com/v3/ws"),
    "edge": ("https://token.assemblyai.com/v1/token", "https://streaming.assemblyai.com/v3/token",
             "wss://streaming.assemblyai.com/v3/ws"),
}


def hosts() -> dict[str, str]:
    s = _settings()
    base = (getattr(s, "assemblyai_test_base", "") or "").rstrip("/")
    if base:  # tests: a local fake AssemblyAI server
        ws_base = re.sub(r"^http", "ws", base)
        return {"token": f"{base}/v1/token", "legacy": f"{base}/v3/token", "ws": f"{ws_base}/v3/ws", "api": base}
    region = (s.assemblyai_region or "global").lower()
    zone = region if region in ("us", "eu") else "edge"
    token, legacy, ws = _ZONES[zone]
    return {"token": token, "legacy": legacy, "ws": ws,
            "api": "https://api.eu.assemblyai.com" if zone == "eu" else "https://api.assemblyai.com"}


def token_routes() -> list[dict[str, str]]:
    """Where to mint a live-streaming token, in order. Each route names the WebSocket its tokens open.

    The configured data zone comes first (the token service, then the older /v3/token service).
    If the account cannot use that zone, edge routing (nearest region) follows, unless
    ASSEMBLYAI_STRICT_ZONE=1.
    """
    s = _settings()
    if (getattr(s, "assemblyai_test_base", "") or "").strip():
        h = hosts()
        return [{"zone": "test", "method": "POST", "url": h["token"], "ws": h["ws"]},
                {"zone": "test", "method": "GET", "url": h["legacy"], "ws": h["ws"]}]
    region = (s.assemblyai_region or "global").lower()
    zones = [region] if region in ("us", "eu") else []
    if not zones or not getattr(s, "assemblyai_strict_zone", False):
        zones.append("edge")
    routes = []
    for z in zones:
        token, legacy, ws = _ZONES[z]
        routes.append({"zone": z, "method": "POST", "url": token, "ws": ws})
        routes.append({"zone": z, "method": "GET", "url": legacy, "ws": ws})
    return routes


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


def clean_keyterms(terms: list[str], limit: int, word_budget: int | None = None) -> list[str]:
    out, seen, words = [], set(), 0
    for t in terms:
        t = re.sub(r"\s+", " ", str(t or "")).strip(" ,.;")
        if not t or len(t) > 50 or len(t.split()) > 6:
            continue
        k = t.lower()
        if k in seen:
            continue
        n = len(t.split())
        if word_budget is not None and words + n > word_budget:
            break
        seen.add(k)
        out.append(t)
        words += n
        if len(out) >= limit:
            break
    return out


def build_keyterms(chart_terms: list[str], specialty: str) -> dict[str, list[str]]:
    """The patient's medicines, allergies and problems first, then the specialty's drugs."""
    pool = list(chart_terms) + drugs_bridge.brand_and_generic_terms(specialty)
    return {"live": clean_keyterms(pool, LIVE_KEYTERMS),
            "final": clean_keyterms(pool, FINAL_KEYTERMS, word_budget=FINAL_KEYTERM_WORDS)}


# ---------------------------------------------------------------- live streaming


def ws_url(keyterms: list[str], base: str | None = None) -> str:
    """The browser's WebSocket URL (without the token). `base` is the WebSocket the token opens."""
    s = _settings()
    params = {
        "speech_model": s.live_speech_model,
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "domain": "medical-v1",
        "speaker_labels": "true",
        "min_turn_silence": 800,
        "max_turn_silence": 3600,
    }
    terms = clean_keyterms(keyterms, LIVE_KEYTERMS)
    if terms:
        params["keyterms_prompt"] = json.dumps(terms)
    return f"{base or hosts()['ws']}?{urlencode(params)}"


@dataclass
class StreamToken:
    token: str
    expires_in: int
    ws: str        # the WebSocket this token opens
    zone: str      # us, eu, edge or test
    refused: list[str] = field(default_factory=list)  # routes tried first, with AssemblyAI's answer


def key_shape(key: str) -> str:
    """The format of a credential, never its value: enough to tell an API key from a token."""
    key = key or ""
    if re.fullmatch(r"[0-9a-fA-F]{32}", key):
        return "32 hexadecimal characters (the format of an AssemblyAI API key)"
    parts = key.split(".")
    if len(parts) == 3 and all(parts):
        exp = None
        try:
            import base64

            pad = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(pad.encode()))
            exp = claims.get("exp") if isinstance(claims, dict) else None
        except (ValueError, TypeError):
            exp = None
        when = ""
        if isinstance(exp, (int, float)):
            import datetime as _dt

            when = ", expiring " + _dt.datetime.fromtimestamp(exp, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"a {len(key)}-character signed token in three parts (the format of a temporary token{when})"
    return f"{len(key)} characters, not the 32-character format of an AssemblyAI API key"


def _where(url: str) -> str:
    u = urlparse(url)
    return f"{u.netloc}{u.path}"


async def mint_token(max_session_seconds: int) -> StreamToken:
    """A short-lived token the browser uses to open the live WebSocket (the API key stays here)."""
    key = _key()
    max_session_seconds = max(60, int(max_session_seconds))
    tried: list[str] = []
    async with _client(10.0) as c:
        for route in token_routes():
            try:
                if route["method"] == "POST":
                    r = await c.post(route["url"], headers={"Authorization": key, "Content-Type": "application/json"},
                                     json={"product": "streaming", "expires_in_seconds": TOKEN_TTL,
                                           "max_session_duration_seconds": max_session_seconds})
                else:
                    r = await c.get(route["url"], headers={"Authorization": key},
                                    params={"expires_in_seconds": TOKEN_TTL,
                                            "max_session_duration_seconds": max_session_seconds})
                data = r.json() if r.status_code == 200 else None
                if isinstance(data, dict) and data.get("token"):
                    return StreamToken(str(data["token"]), int(data.get("expires_in_seconds") or TOKEN_TTL),
                                       route["ws"], route["zone"], tried)
                tried.append(f"{_where(route['url'])}: {_err_detail(r)}")
            except (httpx.HTTPError, ValueError) as exc:
                tried.append(f"{_where(route['url'])}: {exc.__class__.__name__}")
    if tried and all(re.search(r"HTTP 40[13]\b", t) for t in tried):
        if any("temp token" in t.lower() for t in tried):
            raise SttRefused("AssemblyAI says ASSEMBLYAI_API_KEY holds a temporary token, not the account's API key, "
                             f"and a token cannot mint another token. The value is {key_shape(key)}. Put the API key "
                             "from the AssemblyAI dashboard (API Keys) in ASSEMBLYAI_API_KEY. (" + "; ".join(tried) + ")")
        raise SttRefused("AssemblyAI did not accept this API key for live streaming ("
                         + "; ".join(tried) + "). Check that the key is current and that the account can use streaming.")
    raise SttError("AssemblyAI would not issue a streaming token (" + "; ".join(tried) + ").")


# ---------------------------------------------------------------- one-time links
# If AssemblyAI refuses the upload, it can fetch the recording from this server instead,
# through an unguessable link that works for LINK_TTL_S and is closed when the pass ends.

LINK_TTL_S = 1800
_links: dict[str, tuple[Path, float]] = {}


def open_link(path: Path) -> tuple[str, str] | None:
    base = (getattr(_settings(), "public_base_url", "") or "").rstrip("/")
    if not base:
        return None
    now = time.time()
    for k, (_p, exp) in list(_links.items()):
        if exp < now:
            _links.pop(k, None)
    link_id = secrets.token_urlsafe(24)
    _links[link_id] = (Path(path), now + LINK_TTL_S)
    return link_id, f"{base}/api/stt/audio/{link_id}"


def linked_file(link_id: str) -> tuple[Path, str] | None:
    item = _links.get(link_id or "")
    if not item:
        return None
    path, exp = item
    if exp < time.time() or not path.exists():
        _links.pop(link_id, None)
        return None
    return path, (mimetypes.guess_type(path.name)[0] or "application/octet-stream")


def close_link(link_id: str | None) -> None:
    if link_id:
        _links.pop(link_id, None)


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


_TOO_LONG = re.compile(r"prompt[^.]*\b(too long|exceed|limit)", re.I)


def _shrink_keyterms(body: dict, message: str, warnings: list[str]) -> bool:
    """AssemblyAI said the prompt is too long: keep the first (most specific) keyterms that fit."""
    terms = body.get("keyterms_prompt") or []
    if not terms or not _TOO_LONG.search(message or ""):
        return False
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]{2,}", message)]
    n = len(terms)
    if len(nums) >= 2 and max(nums) > 0:
        keep = int(n * min(nums) / max(nums) * 0.8)
    else:
        keep = int(n * 0.5)
    keep = min(keep, n - 1)
    if keep < 5:
        body.pop("keyterms_prompt", None)
        warnings.append(f"AssemblyAI's prompt limit was exceeded, so keyterms were not used ({_short(message, 160)}).")
    else:
        body["keyterms_prompt"] = terms[:keep]
        warnings.append(f"Keyterms trimmed from {n} to {keep} to fit AssemblyAI's prompt limit.")
    return True


async def create_and_poll(c: httpx.AsyncClient, key: str, body: dict, *, timeout_s: float = 900.0,
                          sleep=asyncio.sleep) -> tuple[dict, list[str]]:
    """POST /v2/transcript, then poll. A rejected option is dropped (or keyterms trimmed) and the
    request retried, with a warning saying so."""
    api = hosts()["api"]
    headers = {"Authorization": key, "Content-Type": "application/json"}
    warnings: list[str] = []
    body = dict(body)
    for _ in range(6):
        r = await c.post(f"{api}/v2/transcript", headers=headers, json=body)
        if r.status_code not in (200, 201):
            detail = _err_detail(r)
            low = detail.lower()
            if r.status_code in (401, 403):
                if "keyterms_prompt" in body:
                    body.pop("keyterms_prompt")
                    warnings.append(f"AssemblyAI refused the request with keyterms ({detail}), so it ran without them.")
                    continue
                raise SttRefused(f"AssemblyAI refused the transcription request ({detail}).")
            if r.status_code in (400, 422):
                if _shrink_keyterms(body, detail, warnings):
                    continue
                if "keyterm" in low and "keyterms_prompt" in body:
                    body.pop("keyterms_prompt")
                    warnings.append(f"AssemblyAI rejected the keyterms, so they were not used ({detail}).")
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
                if ("speech_model" in low or "model" in low) and "speech_models" in body:
                    body.pop("speech_models")
                    warnings.append(f"AssemblyAI rejected the requested model, so its default model was used ({detail}).")
                    continue
            raise SttError(f"AssemblyAI would not start the transcription ({detail}).")
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
                await delete_transcript(c, key, tid)
                message = str(data.get("error") or "unknown error")
                if _shrink_keyterms(body, message, warnings):
                    break  # start again with fewer keyterms
                raise SttError(f"AssemblyAI could not transcribe the audio: {_short(message)}.")
            if time.monotonic() > deadline:
                await delete_transcript(c, key, tid)
                raise SttError("AssemblyAI took too long to transcribe the audio (timed out).")
            await sleep(delay)
            delay = min(delay * 1.5, 5.0)
    raise SttError("AssemblyAI would not start the transcription after several attempts.")


async def delete_transcript(c: httpx.AsyncClient, key: str, transcript_id: str) -> bool:
    try:
        r = await c.delete(f"{hosts()['api']}/v2/transcript/{transcript_id}", headers={"Authorization": key})
        return r.status_code in (200, 204)
    except httpx.HTTPError:
        return False


async def _upload(c: httpx.AsyncClient, key: str, api: str, path: Path) -> tuple[str | None, str]:
    """POST the file to /v2/upload with its length set (no chunked encoding). Returns (upload_url, problem)."""
    try:
        up = await c.post(
            f"{api}/v2/upload",
            headers={"Authorization": key, "Content-Type": "application/octet-stream",
                     "Content-Length": str(path.stat().st_size)},
            content=_file_chunks(path),
        )
    except httpx.HTTPError as exc:
        return None, f"upload failed ({exc.__class__.__name__})"
    if up.status_code in (200, 201):
        try:
            url = (up.json() or {}).get("upload_url")
        except ValueError:
            url = None
        if url:
            return url, ""
    return None, f"upload was refused ({_err_detail(up)})"


async def transcribe_file(path: str | os.PathLike, keyterms: list[str], *, sleep=asyncio.sleep) -> dict:
    """Upload, transcribe with Medical Mode, map to turns, delete at AssemblyAI."""
    key = _key()
    s = _settings()
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise SttError("The visit recording is missing or empty.")
    api = hosts()["api"]
    terms = clean_keyterms(keyterms, FINAL_KEYTERMS, word_budget=FINAL_KEYTERM_WORDS)
    warnings: list[str] = []
    link_id: str | None = None
    try:
        async with _client(httpx.Timeout(60.0, read=300.0, write=300.0)) as c:
            audio_url, problem = await _upload(c, key, api, path)
            if not audio_url:
                link = open_link(path)
                if not link:
                    raise SttError(f"Could not send the recording to AssemblyAI: the {problem}.")
                link_id, audio_url = link
                warnings.append(f"AssemblyAI's {problem}, so it fetched the recording from a one-time link instead.")
            body = {
                "audio_url": audio_url,
                "speech_models": [s.final_speech_model],
                "domain": "medical-v1",
                "speaker_labels": True,
                "language_code": "en",
            }
            if terms:
                body["keyterms_prompt"] = terms
            try:
                try:
                    data, more = await create_and_poll(c, key, body, sleep=sleep)
                except SttRefused as exc:
                    link = None if link_id else open_link(path)
                    if not link:
                        raise
                    link_id, body["audio_url"] = link
                    data, more = await create_and_poll(c, key, body, sleep=sleep)
                    more.insert(0, f"{exc} It worked when AssemblyAI fetched the recording from a one-time link.")
            except httpx.HTTPError as exc:
                raise SttError(f"Lost the connection to AssemblyAI ({exc.__class__.__name__}).") from exc
            warnings += more
            sent = data.pop("_body", body)
            turns, removed = utterances_to_turns(data)
            tid = data.get("id")
            deleted = await delete_transcript(c, key, tid) if tid else False
    finally:
        close_link(link_id)
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
            "medical_mode": data.get("domain", sent.get("domain")) == "medical-v1",
            "keyterms_used": len(sent.get("keyterms_prompt") or []),
            "audio_via": "link" if link_id else "upload",
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


def renumber_in_order(turns: list[dict]) -> list[dict]:
    """Stable ids t1..tn keeping the given order (live turns arrive in spoken order,
    but each reconnect restarts AssemblyAI's word times at 0, so sorting would mix sessions)."""
    return [{**t, "id": f"t{i}"} for i, t in enumerate(turns, 1)]


def renumber(turns: list[dict]) -> list[dict]:
    """Stable ids t1..tn in time order (the note cites these)."""
    ordered = sorted(turns, key=lambda t: (t.get("start_ms") or 0, t.get("end_ms") or 0))
    out = []
    for i, t in enumerate(ordered, 1):
        t = dict(t)
        t["id"] = f"t{i}"
        out.append(t)
    return out
