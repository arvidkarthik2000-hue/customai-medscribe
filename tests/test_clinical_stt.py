"""AssemblyAI client: tokens, WebSocket URL, final pass, brand stripping. Offline (httpx.MockTransport)."""
import asyncio
import dataclasses
import json
import os
import tempfile
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-test-"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import config  # noqa: E402
from app.clinical import drugs_bridge, stt  # noqa: E402

KEY = "aai-test-key-123"


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key=KEY,
                                                                 assemblyai_region="global", assemblyai_test_base=""))
    yield
    stt.transport = None


def run(coro):
    return asyncio.run(coro)


async def _nosleep(_s):
    return None


def test_mint_token_new_endpoint():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"token": "tmp-tok", "expires_in_seconds": 600})

    stt.transport = httpx.MockTransport(handler)
    token, exp = run(stt.mint_token(1200))
    assert token == "tmp-tok" and exp == 600
    assert seen["url"] == "https://token.assemblyai.com/v1/token"
    assert seen["auth"] == KEY  # raw key, no "Bearer"
    assert seen["body"] == {"product": "streaming", "expires_in_seconds": 600, "max_session_duration_seconds": 1200}


def test_mint_token_falls_back_to_legacy():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.host, req.url.path, dict(req.url.params)))
        if req.url.host == "token.assemblyai.com":
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={"token": "legacy-tok"})

    stt.transport = httpx.MockTransport(handler)
    token, _ = run(stt.mint_token(600))
    assert token == "legacy-tok"
    assert calls[1][:3] == ("GET", "streaming.assemblyai.com", "/v3/token")
    assert calls[1][3]["expires_in_seconds"] == "600"


def test_mint_token_bad_key_is_plain_and_secret_free():
    stt.transport = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": "Invalid API key"}))
    with pytest.raises(stt.SttError) as e:
        run(stt.mint_token(600))
    assert "401" in str(e.value) and KEY not in str(e.value)


def test_mint_token_not_configured(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key=""))
    with pytest.raises(stt.SttNotConfigured):
        run(stt.mint_token(600))


def test_ws_url_params_and_keyterm_limit():
    terms = [f"drug{i}" for i in range(150)] + ["warfarin", "Warfarin"]
    url = stt.ws_url(["warfarin"] + terms)
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "wss://streaming.assemblyai.com/v3/ws"
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert q["speech_model"] == "universal-3-6-pro"
    assert q["sample_rate"] == "16000" and q["encoding"] == "pcm_s16le"
    assert q["domain"] == "medical-v1" and q["speaker_labels"] == "true"
    assert q["min_turn_silence"] == "800" and q["max_turn_silence"] == "3600"
    assert "inactivity_timeout" not in q  # a pause must not close the session
    assert "token" not in q
    kt = json.loads(q["keyterms_prompt"])
    assert len(kt) == 100 and kt[0] == "warfarin"
    assert len({t.lower() for t in kt}) == 100


def test_regions_and_test_base(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_region="us"))
    h = stt.hosts()
    assert h["token"] == "https://token.us.assemblyai.com/v1/token"
    assert h["ws"] == "wss://streaming.us.assemblyai.com/v3/ws"
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_test_base="http://127.0.0.1:9900/"))
    h = stt.hosts()
    assert h == {"token": "http://127.0.0.1:9900/v1/token", "legacy": "http://127.0.0.1:9900/v3/token",
                 "ws": "ws://127.0.0.1:9900/v3/ws", "api": "http://127.0.0.1:9900"}
    assert stt.ws_url([]).startswith("ws://127.0.0.1:9900/v3/ws?")


def test_clean_keyterms():
    out = stt.clean_keyterms(["  Lisinopril ", "lisinopril", "", "x" * 60, "one two three four five six seven", "Metformin"], 10)
    assert out == ["Lisinopril", "Metformin"]


def _transcribe_handler(state, *, first_transcript_status=200, first_error=None, poll=("queued", "processing", "completed")):
    polls = list(poll)

    def handler(req: httpx.Request):
        assert req.headers.get("authorization") == KEY
        path = req.url.path
        if req.method == "POST" and path == "/v2/upload":
            state["uploaded"] = len(req.read())
            return httpx.Response(200, json={"upload_url": "https://cdn.assemblyai.com/upload/abc"})
        if req.method == "POST" and path == "/v2/transcript":
            body = json.loads(req.content)
            state.setdefault("bodies", []).append(body)
            if len(state["bodies"]) == 1 and first_transcript_status != 200:
                return httpx.Response(first_transcript_status, json={"error": first_error})
            return httpx.Response(200, json={"id": "tr_1", "status": "queued"})
        if req.method == "GET" and path == "/v2/transcript/tr_1":
            st = polls.pop(0) if polls else "completed"
            if st != "completed":
                return httpx.Response(200, json={"id": "tr_1", "status": st, **({"error": "Audio file is corrupt"} if st == "error" else {})})
            return httpx.Response(200, json={
                "id": "tr_1", "status": "completed", "speech_model_used": "universal-3-5-pro", "audio_duration": 95,
                "utterances": [
                    {"speaker": "A", "text": "Let's switch you to Lispro (Humalog), 6 units before meals.", "start": 0, "end": 4200, "confidence": 0.93},
                    {"speaker": "B", "text": "Okay, six units.", "start": 4800, "end": 6000, "confidence": 0.97},
                ],
            })
        if req.method == "DELETE" and path == "/v2/transcript/tr_1":
            state["deleted"] = True
            return httpx.Response(200, json={"id": "tr_1"})
        return httpx.Response(404, json={"error": f"unexpected {req.method} {path}"})

    return handler


def _audio_file():
    fd, path = tempfile.mkstemp(suffix=".webm")
    os.write(fd, b"\x1a\x45\xdf\xa3" + b"0" * 4000)
    os.close(fd)
    return path


def test_transcribe_file_maps_turns_and_strips_brands():
    state = {}
    stt.transport = httpx.MockTransport(_transcribe_handler(state))
    res = run(stt.transcribe_file(_audio_file(), ["insulin lispro", "metformin"], sleep=_nosleep))
    body = state["bodies"][0]
    assert body["speech_models"] == ["universal-3-5-pro"]
    assert body["domain"] == "medical-v1" and body["speaker_labels"] is True and body["language_code"] == "en"
    assert body["keyterms_prompt"] == ["insulin lispro", "metformin"]
    assert body["audio_url"] == "https://cdn.assemblyai.com/upload/abc"
    assert state["uploaded"] > 4000 and state["deleted"]
    turns = res["turns"]
    assert [t["id"] for t in turns] == ["t1", "t2"]
    assert "Humalog" not in turns[0]["text"] and turns[0]["text"].startswith("Let's switch you to Lispro")
    assert turns[0]["raw_text"].endswith("Lispro (Humalog), 6 units before meals.")
    assert res["removed_brands"] == ["Humalog"]
    assert res["meta"]["speech_model_used"] == "universal-3-5-pro" and res["meta"]["medical_mode"] is True
    assert res["meta"]["audio_duration"] == 95 and res["meta"]["warnings"] == []


def test_transcribe_retries_without_domain_and_says_so():
    state = {}
    stt.transport = httpx.MockTransport(_transcribe_handler(state, first_transcript_status=400, first_error="Invalid domain: medical-v1"))
    res = run(stt.transcribe_file(_audio_file(), [], sleep=_nosleep))
    assert "domain" in state["bodies"][0] and "domain" not in state["bodies"][1]
    assert res["meta"]["medical_mode"] is False
    assert any("Medical Mode was not applied" in w for w in res["meta"]["warnings"])


def test_transcribe_error_status_is_plain():
    state = {}
    stt.transport = httpx.MockTransport(_transcribe_handler(state, poll=("processing", "error")))
    with pytest.raises(stt.SttError) as e:
        run(stt.transcribe_file(_audio_file(), [], sleep=_nosleep))
    assert "could not transcribe" in str(e.value) and "corrupt" in str(e.value)


def test_transcribe_missing_file():
    with pytest.raises(stt.SttError):
        run(stt.transcribe_file("/nonexistent/visit.webm", []))


def test_brand_stripping_fallback_and_bridge():
    clean, removed = drugs_bridge._fallback_strip("Start Lispro (Humalog) and keep the metformin (Glucophage).")
    assert clean == "Start Lispro and keep the metformin." and removed == ["Humalog", "Glucophage"]
    keep, removed2 = drugs_bridge._fallback_strip("Blood pressure (BP) is fine.")
    assert keep == "Blood pressure (BP) is fine." and removed2 == []
    clean3, removed3 = drugs_bridge.strip_added_brands("Lispro (Humalog), 6 units")
    assert "Humalog" not in clean3 and removed3


def test_live_turn_normalisation_and_renumber():
    turns = stt.normalise_live_turns([
        {"id": "x2", "speaker": "B", "text": "  Taking  Eliquis twice daily ", "start_ms": 5000, "end_ms": 6000},
        {"id": "x1", "speaker": "PENDING", "text": "Any bleeding?", "start_ms": 1000, "end_ms": 2000},
        {"id": "x3", "speaker": "A", "text": "   ", "start_ms": 7000},
    ])
    assert len(turns) == 2 and turns[1]["speaker"] == "U"
    ordered = stt.renumber(turns)
    assert [t["id"] for t in ordered] == ["t1", "t2"] and ordered[0]["text"] == "Any bleeding?"
