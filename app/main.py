"""CustomAi MedScribe: one process, two zones.

- Clinical zone: /api/*  (visits, transcripts, notes, drug checks, formulary)
- Ad zone:       /adzone/* (a separate FastAPI app with its own database). It
  accepts only slot, prescriber id, declared specialty and state, and hour of day,
  and imports nothing from the clinical zone. It can be moved to its own host by
  running `uvicorn app.ads.app:app` and setting ADZONE_BASE.
- Static pages:  everything else (no third-party scripts; see the CSP below).
"""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, auth
from .config import STATIC_DIR, settings

log = logging.getLogger("medscribe")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="CustomAi MedScribe", version=__version__, docs_url=None, redoc_url=None)

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self'",
        "connect-src 'self' wss://streaming.assemblyai.com wss://streaming.us.assemblyai.com wss://streaming.eu.assemblyai.com",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
    path = request.url.path
    if path.startswith("/api/") or path.startswith("/adzone/"):
        response.headers.setdefault("Cache-Control", "no-store")
    elif path.endswith((".js", ".css", ".html", "/")):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# ---------------------------------------------------------------- session


class SessionIn(BaseModel):
    code: str
    role: str = "doctor"


class RoleIn(BaseModel):
    role: str
    specialty: str | None = None
    state: str | None = None


@app.post("/api/session")
def start_session(body: SessionIn, response: Response):
    if not auth.check_code(body.code):
        return JSONResponse({"detail": "That access code is not right."}, status_code=401)
    data = auth.issue(response, body.role)
    return {"ok": True, "role": data["role"]}


@app.post("/api/session/role")
def switch_role(body: RoleIn, response: Response, session: dict = Depends(auth.require)):
    data = auth.issue(
        response,
        body.role,
        specialty=body.specialty or session.get("specialty"),
        state=body.state or session.get("state"),
    )
    return {"ok": True, "role": data["role"], "specialty": data["specialty"], "state": data["state"]}


@app.post("/api/logout")
def logout(response: Response):
    auth.clear(response)
    return {"ok": True}


@app.get("/api/me")
def me(session: dict = Depends(auth.require)):
    prescriber = dict(auth.DEMO_PRESCRIBER)
    prescriber["specialty"] = session.get("specialty", prescriber["specialty"])
    prescriber["state"] = session.get("state", prescriber["state"])
    return {
        "role": session.get("role", "doctor"),
        "prescriber": prescriber,
        "speech_ready": settings.speech_ready,
        "notes_ready": settings.notes_ready,
    }


@app.get("/api/config")
def config():
    return {
        "version": __version__,
        "adzone_base": settings.adzone_base,
        "speech_ready": settings.speech_ready,
        "notes_ready": settings.notes_ready,
        "limits": {
            "max_visit_minutes": settings.max_visit_minutes,
            "max_upload_mb": settings.max_upload_mb,
        },
    }


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__}


# ---------------------------------------------------------------- zones

from .clinical.routes import router as clinical_router  # noqa: E402
from .drugs.routes import router as drugs_router  # noqa: E402
from .ads.app import app as ads_app  # noqa: E402

app.include_router(clinical_router, prefix="/api")
app.include_router(drugs_router, prefix="/api")
app.mount("/adzone", ads_app)

# Static pages last, so the API routes win. html=True serves index.html for folders.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
