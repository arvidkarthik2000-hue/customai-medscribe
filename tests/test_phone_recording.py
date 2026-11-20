"""Record with a phone: pairing, the phone's endpoints, and the live transcript copy for the screen."""
import asyncio
import dataclasses
import json
import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config
from app.clinical import phone, pipeline, stt
from app.main import app


@pytest.fixture(autouse=True)
def _speech_on(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key="k"))
    phone.reset_for_tests()
    yield
    phone.reset_for_tests()


def _visit(c, consent=True):
    r = c.post("/api/session", json={"code": config.settings.demo_access_code, "role": "doctor"})
    assert r.status_code == 200
    v = c.post("/api/visits", json={"patient_id": "p1", "template": "soap", "style": "concise", "engine": "thorough"}).json()["visit_id"]
    if consent:
        assert c.post(f"/api/visits/{v}/consent", json={"consented": True}).status_code == 200
    return v


def test_pairing_needs_consent_and_gives_a_code_and_qr():
    with TestClient(app) as c:
        v = _visit(c, consent=False)
        assert c.post(f"/api/visits/{v}/phone").status_code == 409
        assert c.post(f"/api/visits/{v}/consent", json={"consented": True}).status_code == 200
        r = c.post(f"/api/visits/{v}/phone")
        assert r.status_code == 200, r.text
        body = r.json()
    assert len(body["code"]) == 6 and set(body["code"]) <= set(phone.ALPHABET)
    assert body["url"].endswith(f"/m/?c={body['code']}") and body["qr"].startswith("data:image/svg+xml")
    assert body["state"] == "waiting" and body["expires_in"] > 600


def test_phone_endpoints_work_without_a_session_and_show_no_chart_data(monkeypatch):
    started = []

    async def fake_start(visit_id, source, sample_id=None):
        started.append((visit_id, source))

    monkeypatch.setattr(pipeline, "start", fake_start)
    with TestClient(app) as c:
        v = _visit(c)
        code = c.post(f"/api/visits/{v}/phone").json()["code"]
        phone_client = TestClient(app)  # the phone: no sign-in
        info = phone_client.get(f"/api/phone/{code.lower()}")
        assert info.status_code == 200
        assert set(info.json()) == {"ok", "state", "code_display", "max_seconds", "elapsed_s"}
        assert c.get(f"/api/visits/{v}/phone").json()["state"] == "connected"

        # live transcript messages, as the relay copies them, reach the screen
        p = phone.for_visit(v)
        s = phone.new_session(p)
        phone.on_message(p, s, json.dumps({"type": "Begin", "configuration": {"model": "salient-live", "medical": True}}))
        phone.on_message(p, s, json.dumps({"type": "Turn", "turn_order": 0, "transcript": "Any chest pain?", "end_of_turn": True,
                                           "speaker_label": "A", "words": [{"text": "Any", "start": 100, "end": 300}]}))
        phone.on_message(p, s, json.dumps({"type": "Turn", "turn_order": 1, "transcript": "No, none", "end_of_turn": False,
                                           "speaker_label": "PENDING"}))
        phone.on_message(p, s, json.dumps({"type": "SpeakerRevision", "revisions": [{"turn_order": 1, "speaker_label": "B"}]}))
        r = phone_client.post(f"/api/phone/{code}/progress", json={"elapsed_s": 12.5, "state": "recording", "live": "live"})
        assert r.status_code == 200
        screen = c.get(f"/api/visits/{v}/phone").json()
        assert screen["state"] == "recording" and screen["live"] == "live" and screen["medical"] is True
        assert [(t["speaker"], t["text"]) for t in screen["turns"]] == [("A", "Any chest pain?"), ("B", "No, none")]
        saved = c.get(f"/api/visits/{v}").json()
        assert saved["audio_seconds"] >= 12

        # the recording goes up with the code alone, and drafting starts
        up = phone_client.post(f"/api/phone/{code}/audio", files={"file": ("visit.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 4000, "audio/webm")})
        assert up.status_code == 200, up.text
        assert started == [(v, "final")]
        assert c.get(f"/api/visits/{v}/phone").json()["state"] == "processing"
        # and the code is spent
        assert phone_client.get(f"/api/phone/{code}").status_code == 404


def test_finish_without_audio_drafts_from_the_live_transcript(monkeypatch):
    started = []

    async def fake_start(visit_id, source, sample_id=None):
        started.append((visit_id, source))

    monkeypatch.setattr(pipeline, "start", fake_start)
    with TestClient(app) as c:
        v = _visit(c)
        code = c.post(f"/api/visits/{v}/phone").json()["code"]
        pc = TestClient(app)
        pc.get(f"/api/phone/{code}")
        r = pc.post(f"/api/phone/{code}/finish")
        assert r.status_code == 409  # nothing was heard
        assert c.get(f"/api/visits/{v}/phone").json()["state"] == "failed"

        code = c.post(f"/api/visits/{v}/phone").json()["code"]  # a new code
        p = phone.for_visit(v)
        s = phone.new_session(p)
        phone.on_message(p, s, json.dumps({"type": "Turn", "turn_order": 0, "transcript": "Take metformin 500 mg twice a day.",
                                           "end_of_turn": True, "speaker_label": "A"}))
        assert pc.post(f"/api/phone/{code}/finish").status_code == 200
        assert started == [(v, "live")]


def test_wrong_codes_are_refused_and_guessing_is_limited():
    with TestClient(app) as c:
        v = _visit(c)
        code = c.post(f"/api/visits/{v}/phone").json()["code"]
        pc = TestClient(app)
        assert pc.get("/api/phone/ZZZZZZ").status_code == 404
        for _ in range(phone.LOOKUP_FAILS_PER_10_MIN):
            pc.get("/api/phone/ZZZZZZ")
        assert pc.get(f"/api/phone/{code}").status_code == 429
        phone.reset_for_tests()
        # discarding on the screen ends the phone's code
        code = c.post(f"/api/visits/{v}/phone").json()["code"]
        assert c.delete(f"/api/visits/{v}/phone").status_code == 200
        assert pc.get(f"/api/phone/{code}").status_code == 404
        assert c.get(f"/api/visits/{v}/phone").json() == {"state": "none"}


def test_phone_stream_relays_and_copies_turns_for_the_screen(monkeypatch):
    from websockets.asyncio.server import serve

    async def upstream(ws):
        await ws.send(json.dumps({"type": "Begin", "id": "x", "expires_at": 1, "configuration": {"model": "m", "domain": "medical-v1"}}))
        n = 0
        async for m in ws:
            if isinstance(m, bytes):
                n += len(m)
                if n >= 6400:
                    await ws.send(json.dumps({"type": "Turn", "transcript": "Is the rash itchy?", "end_of_turn": True,
                                              "turn_order": 0, "speaker_label": "A"}))
            elif json.loads(m).get("type") == "Terminate":
                await ws.send(json.dumps({"type": "Termination"}))
                await ws.close(1000)
                return

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()

    async def run_server():
        async with serve(upstream, "127.0.0.1", port):
            await stop.wait()

    th = threading.Thread(target=lambda: loop.run_until_complete(run_server()), daemon=True)
    th.start()
    time.sleep(0.3)

    async def fake_mint(seconds):
        return stt.StreamToken("t", 60, f"ws://127.0.0.1:{port}/v3/ws", "test")

    monkeypatch.setattr(stt, "mint_token", fake_mint)
    try:
        with TestClient(app) as c:
            v = _visit(c)
            code = c.post(f"/api/visits/{v}/phone").json()["code"]
            pc = TestClient(app)
            pc.get(f"/api/phone/{code}")
            live = pc.post(f"/api/phone/{code}/live")
            assert live.status_code == 200, live.text
            ws_url = live.json()["ws_url"]
            assert ws_url.startswith(f"/api/phone/{code}/stream?ticket=")
            with pc.websocket_connect(ws_url) as ws:
                assert json.loads(ws.receive_text())["configuration"] == {"model": "salient-live", "medical": True}
                ws.send_bytes(b"\x00" * 3200)
                ws.send_bytes(b"\x00" * 3200)
                assert json.loads(ws.receive_text())["transcript"] == "Is the rash itchy?"
                screen = c.get(f"/api/visits/{v}/phone").json()
                assert screen["live"] == "live" and screen["turns"][0]["text"] == "Is the rash itchy?"
                ws.send_text(json.dumps({"type": "Terminate"}))
                assert json.loads(ws.receive_text())["type"] == "Termination"
            # a spent ticket does not open another session
            with pc.websocket_connect(ws_url) as ws2:
                with pytest.raises(Exception):
                    ws2.receive_text()
    finally:
        loop.call_soon_threadsafe(stop.set)
        th.join(3)


def test_phone_page_is_served_and_names_no_vendor():
    import re

    banned = re.compile(r"assembly\s?ai|anthropic|claude|sonnet|haiku|universal-3", re.I)
    with TestClient(app) as c:
        page = c.get("/m/")
        assert page.status_code == 200 and "Phone microphone" in page.text
        for path in ("/m/mic.js", "/m/mic.css", "/doctor/phone.js"):
            r = c.get(path)
            assert r.status_code == 200 and not banned.search(r.text), path
