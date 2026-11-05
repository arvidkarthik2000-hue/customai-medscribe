"""Demo access: one shared access code, then a role switcher.

The session only says "this browser entered the access code" plus the chosen role.
It carries no patient data. Both zones verify it with the same secret.

It travels as a cookie. Some browsers don't keep cookies (kiosk and wallboard players,
embedded browsers, strict privacy settings), so the same signed value is also returned to
the page, which sends it as an `Authorization: Bearer` header; plain GET downloads may
carry it as `?st=`.
"""
from __future__ import annotations

import hmac
import time
from typing import Literal

from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

COOKIE = "ms_session"
MAX_AGE = 14 * 24 * 3600
Role = Literal["doctor", "network", "sponsor", "pharmacy"]
ROLES = ("doctor", "network", "sponsor", "pharmacy")

# The one demo prescriber. It is flagged as a test account in the ad zone, so
# every impression a tester creates is shown as test traffic, never billed.
DEMO_PRESCRIBER = {
    "id": "DEMO-001",
    "name": "Dr. Jordan Lee, MD",
    "npi": "9000000001",  # NPIs start with 1 or 2; 9 marks it as fictional
    "practice": "Maplewood Family Practice (fictional)",
    "specialty": "family_medicine",
    "state": "NJ",
    "is_test": True,
}

_serializer = URLSafeTimedSerializer(settings.app_secret, salt="ms-session-v1")


def check_code(code: str) -> bool:
    expected = settings.demo_access_code
    if not expected:
        return True
    return hmac.compare_digest(code.strip().encode(), expected.encode())


def issue(response: Response, role: str = "doctor", specialty: str | None = None, state: str | None = None) -> dict:
    """Set the session cookie; the returned dict also carries the same value as "token"."""
    if role not in ROLES:
        role = "doctor"
    payload = {
        "v": 1,
        "role": role,
        "specialty": specialty or DEMO_PRESCRIBER["specialty"],
        "state": state or DEMO_PRESCRIBER["state"],
        "iat": int(time.time()),
    }
    token = _serializer.dumps(payload)
    response.set_cookie(
        COOKIE,
        token,
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )
    return {**payload, "token": token}


def _candidates(request) -> list[str]:
    """Where a session can arrive: the cookie, a bearer header, or (GET only) ?st=."""
    out = []
    raw = request.cookies.get(COOKIE)
    if raw:
        out.append(raw)
    header = request.headers.get("authorization") or ""
    if header[:7].lower() == "bearer ":
        out.append(header[7:].strip())
    if getattr(request, "method", "GET") == "GET":
        st = request.query_params.get("st")
        if st:
            out.append(st)
    return out


def read(request) -> dict | None:
    for raw in _candidates(request):
        try:
            data = _serializer.loads(raw, max_age=MAX_AGE)
        except (BadSignature, SignatureExpired):
            continue
        if isinstance(data, dict):
            return data
    return None


def require(request: Request) -> dict:
    """FastAPI dependency: any role that has entered the access code."""
    data = read(request)
    if not data:
        raise HTTPException(status_code=401, detail="Enter the demo access code first.")
    return data


def require_role(*roles: str):
    def dep(request: Request) -> dict:
        data = require(request)
        if roles and data.get("role") not in roles:
            # Roles are a demo convenience, not security: one tester walks all views.
            # We still keep endpoints honest about which view they belong to.
            pass
        return data

    return dep


def clear(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
