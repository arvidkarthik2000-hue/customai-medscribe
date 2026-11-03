"""A local stand-in for AssemblyAI, for end-to-end browser tests only.

Run:  uvicorn tests.e2e.fake_assemblyai:app --port 9900
Then start the app with ASSEMBLYAI_TEST_BASE=http://127.0.0.1:9900 and any
non-empty ASSEMBLYAI_API_KEY. It mimics the documented message shapes:
streaming (Begin, Turn, SpeakerRevision, Termination), temporary tokens, upload,
transcript create/poll/delete. Recognition is scripted, not real.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect

app = FastAPI()

SCRIPT = [
    ("A", "Good morning, what brings you in today?"),
    ("B", "My knee has been hurting for about two weeks, mostly when I climb stairs."),
    ("A", "Are you still taking warfarin 5 milligrams every evening?"),
    ("B", "Yes, and I've been taking ibuprofen 400 milligrams twice a day for the pain."),
    ("A", "Let's stop the ibuprofen because of the bleeding risk with warfarin. Use acetaminophen 500 milligrams up to three times a day instead."),
    ("B", "Okay, that sounds fine."),
]

STATE: dict = {"uploads": {}, "transcripts": {}, "sessions": 0, "last_ws_params": {}}


@app.post("/v1/token")
async def token_v1(request: Request):
    body = await request.json()
    assert request.headers.get("authorization"), "missing Authorization header"
    return {"token": f"fake-{uuid.uuid4().hex[:8]}", "expires_in_seconds": body.get("expires_in_seconds", 60)}


@app.get("/v3/token")
async def token_v3(request: Request):
    return {"token": f"fake-{uuid.uuid4().hex[:8]}"}


@app.get("/_state")
async def state():
    return {"sessions": STATE["sessions"], "last_ws_params": STATE["last_ws_params"], "uploads": len(STATE["uploads"])}


@app.websocket("/v3/ws")
async def ws(websocket: WebSocket):
    params = dict(websocket.query_params)
    STATE["last_ws_params"] = {k: v for k, v in params.items() if k != "token"}
    if not params.get("token"):
        await websocket.close(code=1008, reason="Missing token")
        return
    await websocket.accept()
    STATE["sessions"] += 1
    await websocket.send_text(json.dumps({
        "type": "Begin", "id": uuid.uuid4().hex, "expires_at": int(time.time()) + 600,
        "configuration": {"model": params.get("speech_model", "universal-3-6-pro"), "domain": params.get("domain")},
    }))
    received_ms = 0.0
    next_turn_at = 1500.0
    order = 0
    started = time.time()
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                received_ms += len(msg["bytes"]) / 32.0  # 16 kHz * 2 bytes = 32 bytes per ms
                if received_ms >= next_turn_at and order < len(SCRIPT):
                    spk, text = SCRIPT[order]
                    words = text.split()
                    start = int(next_turn_at - 1500)
                    half = " ".join(words[: max(1, len(words) // 2)])
                    for partial, final in ((half, False), (text, True)):
                        await websocket.send_text(json.dumps({
                            "type": "Turn", "turn_order": order, "turn_is_formatted": final, "end_of_turn": final,
                            "transcript": partial, "speaker_label": spk, "speaker_confidence": 0.93,
                            "words": [{"text": w, "start": start + i * 300, "end": start + i * 300 + 250, "confidence": 0.98, "word_is_final": final, "speaker": spk} for i, w in enumerate(partial.split())],
                        }))
                        await asyncio.sleep(0.05)
                    order += 1
                    next_turn_at += 1500
            elif msg.get("text"):
                data = json.loads(msg["text"])
                if data.get("type") == "Terminate":
                    await websocket.send_text(json.dumps({"type": "Termination", "audio_duration_seconds": round(received_ms / 1000, 2), "session_duration_seconds": round(time.time() - started, 2)}))
                    await websocket.close(code=1000)
                    break
    except WebSocketDisconnect:
        pass


@app.post("/v2/upload")
async def upload(request: Request):
    data = await request.body()
    fid = uuid.uuid4().hex
    STATE["uploads"][fid] = len(data)
    return {"upload_url": f"https://cdn.fake.assemblyai.local/{fid}"}


@app.post("/v2/transcript")
async def create(request: Request):
    body = await request.json()
    tid = uuid.uuid4().hex
    STATE["transcripts"][tid] = {"created": time.time(), "body": body, "polls": 0}
    return {"id": tid, "status": "queued"}


@app.get("/v2/transcript/{tid}")
async def get(tid: str):
    t = STATE["transcripts"].get(tid)
    if not t:
        return {"id": tid, "status": "error", "error": "not found"}
    t["polls"] += 1
    if t["polls"] < 2:
        return {"id": tid, "status": "processing"}
    utterances = []
    start = 0
    for spk, text in SCRIPT:
        words = text.split()
        end = start + len(words) * 300
        utterances.append({"speaker": spk, "text": text, "start": start, "end": end, "confidence": 0.97,
                           "words": [{"text": w, "start": start + i * 300, "end": start + i * 300 + 250, "confidence": 0.98, "speaker": spk} for i, w in enumerate(words)]})
        start = end + 400
    return {"id": tid, "status": "completed", "text": " ".join(u["text"] for u in utterances), "utterances": utterances,
            "speech_model_used": (t["body"].get("speech_models") or ["universal-3-5-pro"])[0], "audio_duration": start / 1000,
            "language_code": "en_us"}


@app.delete("/v2/transcript/{tid}")
async def delete(tid: str):
    STATE["transcripts"].pop(tid, None)
    return {"id": tid, "status": "completed"}
