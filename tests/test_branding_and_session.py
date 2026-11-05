"""Partner-facing pages name no third-party AI vendor or model; the session works without cookies;
live audio goes through our relay."""
import asyncio
import dataclasses
import json
import os
import re
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-test-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.clinical import stt  # noqa: E402
from app.main import app  # noqa: E402

BANNED = re.compile(r"assembly\s?ai|anthropic|claude|sonnet|haiku|universal-3|medical-v1|medical mode|gemini|openai|"
                    r"whisper|deepgram|elevenlabs|parakeet|qwen|vibevoice|pyannote", re.I)
STATIC = Path(__file__).resolve().parent.parent / "static"


def test_no_vendor_names_in_any_page_script_or_style():
    hits = []
    for f in STATIC.rglob("*"):
        if f.suffix in (".js", ".html", ".css", ".json", ".svg", ".txt") and f.is_file():
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if BANNED.search(line):
                    hits.append(f"{f.relative_to(STATIC)}:{n}: {line.strip()[:120]}")
    assert not hits, "\n".join(hits)


def test_partner_docs_name_no_vendor():
    for name in ("TESTER_GUIDE.md", "DEMO_SCRIPT.md"):
        text = (STATIC.parent / "docs" / name).read_text(encoding="utf-8")
        assert not BANNED.search(text), name


def test_csp_allows_only_this_server():
    with TestClient(app) as c:
        csp = c.get("/").headers["content-security-policy"]
    assert "assemblyai" not in csp and "connect-src 'self' ws://testserver" in csp


def _sign_in(c):
    code = config.settings.demo_access_code
    r = c.post("/api/session", json={"code": code, "role": "doctor"})
    assert r.status_code == 200
    return r.json()["token"]


def test_session_works_without_cookies():
    with TestClient(app) as c:
        token = _sign_in(c)
        c.cookies.clear()  # a browser that does not keep cookies
        assert c.get("/api/me").status_code == 401
        h = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/me", headers=h).status_code == 200
        r = c.post("/api/session/role", json={"role": "pharmacy"}, headers=h)
        assert r.status_code == 200 and r.json()["role"] == "pharmacy" and r.json()["token"]
        c.cookies.clear()
        me = c.get("/api/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()
        assert me["role"] == "pharmacy"
        # the ad zone accepts it too
        assert c.get("/adzone/api/inventory", headers=h).status_code == 200
        # downloads carry it as ?st= on GET only
        assert c.get(f"/api/me?st={token}").status_code == 200
        assert c.post(f"/api/session/role?st={token}", json={"role": "doctor"}).status_code == 401
        assert c.get("/api/me", headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


def test_live_token_points_at_our_relay(monkeypatch):
    import httpx

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key="k"))
    stt.transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"token": "upstream-secret", "expires_in_seconds": 600}))
    try:
        with TestClient(app) as c:
            _sign_in(c)
            v = c.post("/api/visits", json={"patient_id": "p1", "template": "soap", "style": "concise", "engine": "thorough"}).json()["visit_id"]
            assert c.post(f"/api/visits/{v}/consent", json={"consented": True}).status_code == 200
            r = c.post("/api/stt/token", json={"visit_id": v})
            assert r.status_code == 200, r.text
            body = r.json()
    finally:
        stt.transport = None
    assert body["token"] is None and body["ws_url"].startswith("/api/stt/stream?ticket=")
    assert "upstream-secret" not in json.dumps(body) and "assemblyai" not in json.dumps(body).lower()
    ticket = body["ws_url"].split("ticket=")[1]
    upstream = stt.take_ticket(ticket)
    assert upstream and "token=upstream-secret" in upstream
    assert stt.take_ticket(ticket) is None  # one use


def test_relay_rewrites_begin_and_passes_audio_and_turns(monkeypatch):
    import socket
    import threading
    import time as _t

    import websockets
    from websockets.asyncio.server import serve

    got = {"audio": 0, "terminate": False}

    async def upstream(ws):
        await ws.send(json.dumps({"type": "Begin", "id": "x", "expires_at": 1,
                                  "configuration": {"model": "vendor-model-9", "domain": "medical-v1"}}))
        async for m in ws:
            if isinstance(m, bytes):
                got["audio"] += len(m)
                if got["audio"] >= 6400:
                    await ws.send(json.dumps({"type": "Turn", "transcript": "Take warfarin 5 mg.", "end_of_turn": True,
                                              "turn_order": 0, "speaker_label": "A"}))
            elif json.loads(m).get("type") == "Terminate":
                got["terminate"] = True
                await ws.send(json.dumps({"type": "Termination", "audio_duration_seconds": 1}))
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
    _t.sleep(0.3)
    try:
        tok = stt.StreamToken("t", 60, f"ws://127.0.0.1:{port}/v3/ws", "test")
        ticket = stt.issue_ticket(tok, f"ws://127.0.0.1:{port}/v3/ws?sample_rate=16000")
        with TestClient(app) as c:
            with c.websocket_connect(f"/api/stt/stream?ticket={ticket}") as ws:
                begin = json.loads(ws.receive_text())
                assert begin == {"type": "Begin", "id": "x", "expires_at": 1,
                                 "configuration": {"model": "salient-live", "medical": True}}
                ws.send_bytes(b"\x00" * 3200)
                ws.send_bytes(b"\x00" * 3200)
                turn = json.loads(ws.receive_text())
                assert turn["transcript"] == "Take warfarin 5 mg." and turn["speaker_label"] == "A"
                ws.send_text(json.dumps({"type": "Terminate"}))
                assert json.loads(ws.receive_text())["type"] == "Termination"
            with c.websocket_connect("/api/stt/stream?ticket=expired") as ws2:
                with pytest.raises(Exception):
                    ws2.receive_text()
    finally:
        loop.call_soon_threadsafe(stop.set)
        th.join(3)
    assert got["audio"] >= 6400 and got["terminate"]


def test_scrub_rewrites_vendor_names_and_model_ids():
    from app import branding

    s = branding.scrub("AssemblyAI said 401 for https://api.assemblyai.com/v2/upload; claude-sonnet-5-5 and "
                       "claude-haiku-5-5 and universal-3-5-pro with Medical Mode (keyterms_prompt too long). Anthropic's API.")
    assert not BANNED.search(s), s
    assert "salient-thorough" in s and "salient-fast" in s and "salient-medical-speech" in s
    assert branding.scrub(None) is None and branding.scrub("Take warfarin 5 mg") == "Take warfarin 5 mg"


def test_visits_saved_before_the_rename_show_neutral_names():
    from app.clinical import store

    with TestClient(app) as c:
        _sign_in(c)
        v = c.post("/api/visits", json={"patient_id": "p1", "template": "soap", "style": "concise", "engine": "sonnet"}).json()["visit_id"]
        # what an older build wrote for a visit
        store.update_visit(v, engine="sonnet", error="AssemblyAI refused the upload (401).",
                           engine_info={"live_model": "universal-3-6-pro", "final_model": "universal-3-5-pro",
                                        "note_model": "claude-sonnet-5-5", "verifier_model": "claude-haiku-5-5",
                                        "medical_mode": True, "keyterms_used": 120,
                                        "warnings": ["AssemblyAI Medical Mode was not applied."]},
                           stages=[{"id": "final_transcript", "label": "Final transcript (AssemblyAI Universal-3.5 Pro)",
                                    "state": "error", "detail": "AssemblyAI said 401"},
                                   {"id": "note", "label": "Draft note (Claude)", "state": "pending", "detail": ""}])
        body = c.get(f"/api/visits/{v}").json()
        st = c.get(f"/api/visits/{v}/status").json()
    text = json.dumps({k: body[k] for k in ("engine", "engine_info", "stages", "error")}) + json.dumps(st)
    assert not BANNED.search(text), text
    assert body["engine"] == "thorough"
    assert body["engine_info"]["note_model"] == "salient-thorough" and body["engine_info"]["verifier_model"] == "salient-verifier"
    assert body["engine_info"]["keyterms_used"] == 120  # keys the pages read are kept
    assert body["stages"][0]["label"] == "Final transcript (Salient medical speech)"


def test_screen_check_probes():
    with TestClient(app) as c:
        r = c.post("/api/device/cookie")
        value = r.json()["value"]
        assert "ms_check" in r.headers.get("set-cookie", "")
        assert c.get(f"/api/device/cookie?value={value}").json()["kept"] is True
        c.cookies.clear()
        assert c.get(f"/api/device/cookie?value={value}").json()["kept"] is False
        assert c.post("/api/device/echo", json={"probe": 1}).json()["ok"] is True
        with c.websocket_connect("/api/device/ws") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"
        page = c.get("/check/")
        assert page.status_code == 200 and "Screen check" in page.text
        assert "boot-check.js" in c.get("/").text
