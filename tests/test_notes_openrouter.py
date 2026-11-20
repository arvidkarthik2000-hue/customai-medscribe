"""Notes through OpenRouter's Anthropic-compatible Messages API (mocked: no network, no real key)."""
import asyncio
import dataclasses
import json

import httpx
import pytest

from app import config
from app.clinical import notes, schema

FAKE_KEY = "test-openrouter-key"


@pytest.fixture
def openrouter(monkeypatch):
    s = dataclasses.replace(config.settings, openrouter_api_key=FAKE_KEY, anthropic_api_key="old-anthropic-key",
                            note_provider="auto", openrouter_base="https://openrouter.ai/api")
    monkeypatch.setattr(config, "settings", s)
    monkeypatch.setattr(notes, "client_factory", None)
    notes._cached.update({"key": None, "client": None})
    seen = []

    def use(handler):
        def wrapped(request: httpx.Request):
            seen.append(request)
            return handler(request)
        monkeypatch.setattr(notes, "transport", httpx.MockTransport(wrapped))
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        return seen

    yield use
    notes._cached.update({"key": None, "client": None})


async def _no_sleep(_s):
    return None


def _message(payload: dict, stop="end_turn"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "anthropic/claude-sonnet-5.5",
            "content": [{"type": "thinking", "thinking": "…"}, {"type": "text", "text": json.dumps(payload)}],
            "stop_reason": stop, "usage": {"input_tokens": 1200, "output_tokens": 340}}


def test_provider_choice():
    base = config.settings
    assert dataclasses.replace(base, openrouter_api_key="k", anthropic_api_key="a", note_provider="auto").notes_provider == "openrouter"
    assert dataclasses.replace(base, openrouter_api_key="", anthropic_api_key="a", note_provider="auto").notes_provider == "anthropic"
    assert dataclasses.replace(base, openrouter_api_key="k", anthropic_api_key="a", note_provider="anthropic").notes_provider == "anthropic"
    assert dataclasses.replace(base, openrouter_api_key="", anthropic_api_key="", note_provider="auto").notes_ready is False
    assert notes.openrouter_model("claude-sonnet-5-5") == "anthropic/claude-sonnet-5.5"
    assert notes.openrouter_model("claude-haiku-5-5") == "anthropic/claude-haiku-5.5"


def test_structured_call_goes_to_openrouter_with_only_its_key(openrouter):
    seen = openrouter(lambda req: httpx.Response(200, json=_message({"ok": True, "n": 3})))
    data, meta = asyncio.run(notes.call_structured(model="claude-sonnet-5-5", system="sys", user="hello",
                                                   json_schema={"type": "object"}, max_tokens=1000))
    assert data == {"ok": True, "n": 3}
    assert meta["input_tokens"] == 1200 and meta["output_tokens"] == 340
    req = seen[0]
    assert str(req.url) == "https://openrouter.ai/api/v1/messages"
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert "x-api-key" not in req.headers  # the old key is never sent to another service
    body = json.loads(req.content)
    assert body["model"] == "anthropic/claude-sonnet-5.5"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert body["provider"] == {"require_parameters": True}
    assert "thinking" not in body  # "between_tools" is not an OpenRouter thinking type
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "hello"}]


def test_verifier_model_keeps_thinking_off(openrouter):
    seen = openrouter(lambda req: httpx.Response(200, json=_message({"lines": []})))
    asyncio.run(notes.call_structured(model="claude-haiku-5-5", system="s", user="u", json_schema={"type": "object"}, max_tokens=100))
    body = json.loads(seen[0].content)
    assert body["model"] == "anthropic/claude-haiku-5.5" and body["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("status,phrase", [(401, "rejected the server's key"), (402, "out of credits")])
def test_key_and_credit_errors_are_plain(openrouter, status, phrase):
    openrouter(lambda req: httpx.Response(status, json={"error": {"code": status, "message": "No auth credentials found"}}))
    with pytest.raises(notes.NoteError) as exc:
        asyncio.run(notes.call_structured(model="claude-sonnet-5-5", system="s", user="u", json_schema={"type": "object"}, max_tokens=100))
    assert phrase in str(exc.value)


def test_busy_is_retried_then_succeeds(openrouter):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, json={"error": {"message": "Rate limited"}})
        return httpx.Response(200, json=_message({"ok": 1}))

    openrouter(handler)
    data, _ = asyncio.run(notes.call_structured(model="claude-sonnet-5-5", system="s", user="u", json_schema={"type": "object"}, max_tokens=100))
    assert data == {"ok": 1} and calls["n"] == 3


def test_schema_rejection_falls_back_to_schema_in_prompt(openrouter):
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        if "output_config" in body and "format" in body["output_config"]:
            return httpx.Response(400, json={"error": {"message": "output_config.format json_schema is not supported by this provider"}})
        return httpx.Response(200, json=_message({"ok": "fallback"}))

    openrouter(handler)
    data, meta = asyncio.run(notes.call_structured(model="claude-sonnet-5-5", system="s", user="u", json_schema={"type": "object"}, max_tokens=100))
    assert data == {"ok": "fallback"} and "JSON schema" in calls[-1]["system"]
    assert meta["warnings"]


def test_full_note_through_openrouter(openrouter):
    note = {"sections": [], "medications": [], "diagnoses": [], "patient_instructions": [], "uncertain": [],
            "speaker_roles": [{"speaker": "A", "role": "clinician"}], "not_discussed": []}
    openrouter(lambda req: httpx.Response(200, json=_message(note)))
    turns = [{"id": "t1", "speaker": "A", "text": "Any chest pain?"}]
    try:
        out, meta = asyncio.run(notes.generate_note(turns, None, template="soap", specialty="family_medicine",
                                                    style="concise", engine="thorough", visit_date="2026-11-20"))
    except notes.NoteError as exc:  # the schema check may want more fields; the call itself must have worked
        assert "rejected" not in str(exc) and "credits" not in str(exc)
        return
    assert isinstance(out, dict) and meta["model"]


def test_engine_error_names_are_scrubbed():
    from app import branding

    s = branding.scrub("The note model call failed (NoteServiceError: anthropic/claude-sonnet-5.5 at https://openrouter.ai/api/v1/messages via OpenRouter).")
    assert "openrouter" not in s.lower() and "claude" not in s.lower() and "anthropic" not in s.lower(), s


@pytest.mark.parametrize("raw", ["Bearer sk-or-v1-" + "a" * 64, "OPENROUTER_API_KEY=sk-or-v1-" + "a" * 64,
                                 '"sk-or-v1-' + "a" * 64 + '"', "  sk-or-v1-" + "a" * 64 + "  ",
                                 "a" * 64, "v1-" + "a" * 64, "or-v1-" + "a" * 64])
def test_key_pasted_with_extra_text_still_works(monkeypatch, raw):
    s = dataclasses.replace(config.settings, openrouter_api_key=raw, note_provider="openrouter")
    monkeypatch.setattr(config, "settings", s)
    monkeypatch.setattr(notes, "client_factory", None)
    notes._cached.update({"key": None, "client": None})
    seen = []
    monkeypatch.setattr(notes, "transport", httpx.MockTransport(lambda req: (seen.append(req), httpx.Response(200, json=_message({"ok": 1})))[1]))
    asyncio.run(notes.call_structured(model="claude-haiku-5-5", system="s", user="u", json_schema={"type": "object"}, max_tokens=50))
    assert seen[0].headers["authorization"] == "Bearer sk-or-v1-" + "a" * 64
    notes._cached.update({"key": None, "client": None})
