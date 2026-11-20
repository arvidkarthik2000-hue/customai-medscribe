"""Record a visit with a phone.

Some screens can't give the page a microphone. Constant Media's wallboard player, for one,
shows the demo in an iframe overlay that doesn't pass the microphone through. So the screen
shows a QR code, and the doctor's phone does the recording:
- the phone streams audio through our relay;
- the screen shows the live transcript as it comes in;
- when the phone stops, it sends the recording for the final pass, and the screen moves on to
  the note.

The pairing code is the phone's only credential. It is good for one visit, for a limited
time, and only for streaming and sending that visit's audio. It reads no chart data.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/O, 1/I/L
CODE_LEN = 6
CODE_TTL_S = 15 * 60          # to scan the code and press Start
ACTIVE_GRACE_S = 120          # while recording, the code lives on as long as the phone checks in
LOOKUP_FAILS_PER_10_MIN = 30  # per client address, against guessing codes

STATES = ("waiting", "connected", "recording", "paused", "sending", "processing", "failed", "cancelled")


@dataclass
class Pairing:
    code: str
    visit_id: str
    expires: float
    max_seconds: int
    state: str = "waiting"
    elapsed_s: float = 0.0
    live: str = "off"            # off | connecting | live | unavailable
    medical: bool = False
    message: str = ""
    session: int = 0
    turns: dict = field(default_factory=dict)      # (session, order) -> turn
    offsets_ms: dict = field(default_factory=dict)  # session -> ms into the visit when it began
    version: int = 0
    last_seen: float = 0.0


_lock = threading.Lock()
_pairs: dict[str, Pairing] = {}
_by_visit: dict[str, str] = {}
_fails: dict[str, list[float]] = {}


def normalise(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch in ALPHABET)[:CODE_LEN]


def pretty(code: str) -> str:
    return f"{code[:3]} {code[3:]}"


def _sweep(now: float) -> None:
    for code, p in list(_pairs.items()):
        if p.expires < now:
            _pairs.pop(code, None)
            if _by_visit.get(p.visit_id) == code:
                _by_visit.pop(p.visit_id, None)


def create(visit_id: str, max_seconds: int) -> Pairing:
    """A new code for this visit; any earlier code for it stops working."""
    now = time.time()
    with _lock:
        _sweep(now)
        old = _by_visit.pop(visit_id, None)
        if old:
            _pairs.pop(old, None)
        while True:
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
            if code not in _pairs:
                break
        p = Pairing(code=code, visit_id=visit_id, expires=now + CODE_TTL_S, max_seconds=max_seconds)
        _pairs[code] = p
        _by_visit[visit_id] = code
        return p


def for_visit(visit_id: str) -> Pairing | None:
    with _lock:
        _sweep(time.time())
        code = _by_visit.get(visit_id)
        return _pairs.get(code) if code else None


def lookup(code: str, client: str = "") -> Pairing | None:
    """The phone's view. Counts misses per client address, so codes can't be guessed."""
    now = time.time()
    with _lock:
        _sweep(now)
        recent = [t for t in _fails.get(client, []) if t > now - 600]
        if len(recent) >= LOOKUP_FAILS_PER_10_MIN:
            _fails[client] = recent
            raise PermissionError("Too many wrong codes. Wait a few minutes and scan the code again.")
        p = _pairs.get(normalise(code))
        if not p or p.state in ("cancelled", "processing", "failed"):
            recent.append(now)
            _fails[client] = recent
            return None
        p.last_seen = now
        if p.state in ("recording", "paused"):
            p.expires = max(p.expires, now + ACTIVE_GRACE_S)
        return p


def cancel(visit_id: str) -> None:
    with _lock:
        code = _by_visit.pop(visit_id, None)
        p = _pairs.pop(code, None) if code else None
        if p:
            p.state = "cancelled"


def set_state(p: Pairing, state: str, message: str = "", elapsed_s: float | None = None) -> None:
    with _lock:
        if state in STATES:
            p.state = state
        p.message = message
        if elapsed_s is not None:
            p.elapsed_s = max(0.0, min(float(elapsed_s), p.max_seconds + 60.0))
        if state in ("recording", "paused"):
            p.expires = max(p.expires, time.time() + ACTIVE_GRACE_S)
        if state in ("processing",):
            p.expires = time.time() + 60  # long enough for the screen to see it
        p.version += 1


def new_session(p: Pairing) -> int:
    with _lock:
        p.session += 1
        p.offsets_ms[p.session] = int(p.elapsed_s * 1000)
        p.live = "connecting"
        p.version += 1
        return p.session


def _unassigned(spk) -> bool:
    return not spk or spk in ("PENDING", "UNKNOWN")


def on_message(p: Pairing, session: int, raw: str) -> None:
    """Live transcript messages (already in our own terms), copied for the screen."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return
    if not isinstance(msg, dict):
        return
    kind = msg.get("type")
    with _lock:
        if kind == "Begin":
            p.live = "live"
            p.medical = bool((msg.get("configuration") or {}).get("medical"))
        elif kind == "Turn":
            order = int(msg.get("turn_order") or 0)
            key = (session, order)
            prev = p.turns.get(key, {})
            words = msg.get("words") or []
            off = p.offsets_ms.get(session, 0)
            text = msg.get("transcript") or " ".join(str(w.get("text", "")) for w in words if isinstance(w, dict))
            first = words[0] if words and isinstance(words[0], dict) else {}
            last = words[-1] if words and isinstance(words[-1], dict) else {}
            p.turns[key] = {
                "text": str(text)[:4000],
                "final": bool(msg.get("end_of_turn")),
                "speaker": msg.get("speaker_label") if not _unassigned(msg.get("speaker_label")) else prev.get("speaker", ""),
                "start_ms": first.get("start") + off if isinstance(first.get("start"), (int, float)) else prev.get("start_ms"),
                "end_ms": last.get("end") + off if isinstance(last.get("end"), (int, float)) else prev.get("end_ms"),
            }
        elif kind == "SpeakerRevision":
            for r in msg.get("revisions") or []:
                t = p.turns.get((session, int(r.get("turn_order") or 0)))
                if t is not None and not _unassigned(r.get("speaker_label")):
                    t["speaker"] = r.get("speaker_label")
        elif kind == "Error":
            p.live = "unavailable"
        else:
            return
        p.version += 1


def live_unavailable(p: Pairing) -> None:
    """The phone could not open a live session: it records on, and the screen says so."""
    with _lock:
        if p.live != "live":
            p.live = "unavailable"
            p.version += 1


def session_closed(p: Pairing, session: int) -> None:
    with _lock:
        if p.session == session and p.live in ("connecting", "live"):
            p.live = "off" if p.state in ("sending", "processing") else "unavailable"
            p.version += 1


def turns(p: Pairing) -> list[dict]:
    """In speaking order, in the shape the visit's live transcript is saved in."""
    with _lock:
        items = sorted(p.turns.items())
        out = []
        for i, (_key, t) in enumerate(items, 1):
            if not (t.get("text") or "").strip():
                continue
            out.append({"id": f"t{i}", "speaker": t.get("speaker") or "", "text": t["text"].strip(),
                        "start_ms": t.get("start_ms"), "end_ms": t.get("end_ms"), "final": t.get("final", False)})
        return out


def view(p: Pairing) -> dict:
    """What the screen polls."""
    return {
        "code": p.code, "code_display": pretty(p.code), "state": p.state, "live": p.live, "medical": p.medical,
        "elapsed_s": round(p.elapsed_s, 1), "expires_in": max(0, int(p.expires - time.time())),
        "message": p.message, "version": p.version, "turns": turns(p),
    }


def reset_for_tests() -> None:
    with _lock:
        _pairs.clear()
        _by_visit.clear()
        _fails.clear()
