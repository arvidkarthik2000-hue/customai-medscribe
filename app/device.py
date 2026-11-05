"""Screen check (/check/): small public probes a wallboard or kiosk browser can run.

They answer "does this browser keep a cookie, can it POST, can it open a WebSocket to this
server". Nothing here needs a session or touches visit data.
"""
from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect

from .config import settings

router = APIRouter()
COOKIE = "ms_check"


@router.post("/device/cookie")
def set_probe_cookie(response: Response):
    value = secrets.token_urlsafe(8)
    response.set_cookie(COOKIE, value, max_age=600, httponly=True, samesite="lax",
                        secure=settings.secure_cookies, path="/api/device")
    return {"ok": True, "value": value}


@router.get("/device/cookie")
def read_probe_cookie(request: Request, value: str = ""):
    got = request.cookies.get(COOKIE, "")
    return {"ok": True, "kept": bool(got) and (not value or secrets.compare_digest(got, value[:64]))}


@router.post("/device/echo")
async def echo(request: Request):
    body = await request.body()
    return {"ok": True, "bytes": len(body[:4096])}


@router.websocket("/device/ws")
async def ws_probe(ws: WebSocket):
    await ws.accept()
    try:
        for _ in range(3):
            msg = await asyncio.wait_for(ws.receive_text(), timeout=10)
            await ws.send_text("pong" if msg[:16] == "ping" else "?")
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass
    finally:
        try:
            await ws.close()
        except RuntimeError:
            pass
