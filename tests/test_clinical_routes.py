"""Every clinical route, the pipeline end to end, sign gates and caps. Offline."""
import dataclasses
import json
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-test-"))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.clinical import drugs_bridge, fixtures, notes, pipeline, selftest, store, stt  # noqa: E402
from app.main import app  # noqa: E402

KEY = "aai-test-key"


def fake_check(medications, patient):
    rows = []
    for m in medications:
        rows.append({**m, "match": {"status": "matched", "rxcui": None, "ingredient": m["name"], "display": None,
                                    "candidates": [], "sound_alike": [], "source": "local"},
                     "high_risk": m["name"] == "warfarin", "high_risk_reason": "anticoagulant" if m["name"] == "warfarin" else "",
                     "schedule": None, "formulary": None, "dose_check": {"status": "unknown", "detail": "", "source": ""},
                     "confirmed": False, "dose_confirmed": False})
    names = {m["name"] for m in medications}
    cards = []
    if {"warfarin", "ibuprofen"} <= names:
        cards.append({"id": "card-warf-nsaid", "indicator": "critical", "category": "interaction",
                      "summary": "Warfarin with ibuprofen: bleeding risk", "detail": "Avoid NSAIDs.", "meds": ["warfarin", "ibuprofen"],
                      "source": {"label": "FDA label: warfarin, section 7", "url": "https://dailymed.nlm.nih.gov/", "date": "2026-01-01"},
                      "override_reasons": ["Benefit outweighs risk", "Other"]})
    cards.append({"id": "card-info", "indicator": "info", "category": "formulary", "summary": "Tier 1", "detail": "",
                  "meds": [], "source": {"label": "sample", "url": "", "date": ""}, "override_reasons": []})
    return {"medications": rows, "cards": cards, "sources_checked": [{"name": "fake", "status": "ok", "detail": ""}],
            "checked_at": "2026-11-03T00:00:00Z"}


@pytest.fixture
def client(monkeypatch):
    pipeline.INLINE = True
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key="", anthropic_api_key="",
                                                                 assemblyai_test_base="", daily_note_runs=150,
                                                                 daily_audio_minutes=240, max_upload_mb=60))

    async def _check(medications, patient, timeout_s=8.0):
        return fake_check(medications, patient), "Checked"

    monkeypatch.setattr(drugs_bridge, "check_visit", _check)
    with TestClient(app) as c:
        assert c.post("/api/session", json={"code": config.settings.demo_access_code}).status_code == 200
        c.post("/api/schedule/reset")
        store.usage_set("note_runs", 0)
        store.usage_set("audio_seconds", 0)
        yield c
    notes.client_factory = None
    stt.transport = None


def _visit(c, patient="p1", **kw):
    r = c.post("/api/visits", json={"patient_id": patient, **kw})
    assert r.status_code == 200, r.text
    return r.json()["visit_id"]


def test_requires_session():
    with TestClient(app) as anon:
        assert anon.get("/api/schedule").status_code == 401
        assert anon.get("/api/samples").status_code == 401


def test_schedule_and_chart(client):
    sch = client.get("/api/schedule").json()
    assert sch["date"] == fixtures.today_local().isoformat()
    appts = sch["appointments"]
    assert len(appts) == 8 and all(a["status"] == "scheduled" for a in appts)
    assert appts[0]["time"].endswith("Z") and {a["patient_id"] for a in appts} == {f"p{i}" for i in range(1, 9)}
    chart = client.get("/api/patients/p2").json()
    assert chart["egfr"] == 28 and chart["insurance"]["plan_key"] == "MAPD-B"
    assert 34 <= chart["age"] <= 82 + 1 and chart["labs"][0]["date"]
    ages = [client.get(f"/api/patients/p{i}").json()["age"] for i in range(1, 9)]
    assert min(ages) >= 34 and max(ages) <= 83
    assert client.get("/api/patients/nope").status_code == 404


def test_samples(client):
    lst = client.get("/api/samples").json()["samples"]
    assert len(lst) == 5 and all(s["patient_name"] and s["minutes"] >= 3 for s in lst)
    one = client.get("/api/samples/s2-diabetes-ckd").json()
    assert one["patient_name"] == "Dolores Martinez" and one["turns"][0]["start_ms"] >= 0
    assert {t["speaker"] for t in one["turns"]} == {"A", "B", "C"}
    assert client.get("/api/samples/nope").status_code == 404


def test_sample_pipeline_saved_note_then_sign(client):
    vid = _visit(client, "p1")
    assert client.post(f"/api/visits/{vid}/process", json={"source": "sample", "sample_id": "s1-knee-warfarin"}).json() == {"ok": True}
    st = client.get(f"/api/visits/{vid}/status").json()
    assert st["stage"] == "done" and st["status"] == "ready"
    states = {s["id"]: s["state"] for s in st["stages"]}
    assert states == {"final_transcript": "skipped", "note": "done", "verify": "done", "meds": "done"}
    v = client.get(f"/api/visits/{vid}").json()
    assert v["sample_id"] == "s1-knee-warfarin" and v["source"] == "sample"
    assert v["speaker_roles"] == {"A": "clinician", "B": "patient"}
    assert v["transcript"][0]["role"] == "clinician"
    assert v["engine_info"]["note_model"] == "saved sample note"
    assert any("Confirm 6 medicines" in b for b in v["blockers"])
    assert any("high-risk" in b for b in v["blockers"]) and any("critical" in b for b in v["blockers"])
    assert client.get("/api/schedule").json()["appointments"][1]["status"] == "ready_to_sign"

    r = client.post(f"/api/visits/{vid}/sign")
    assert r.status_code == 409 and r.json()["blockers"]

    for row in v["medication_check"]["medications"]:
        res = client.post(f"/api/visits/{vid}/meds/{row['id']}/confirm", json={"confirmed": True})
        assert res.status_code == 200
    v = client.get(f"/api/visits/{vid}").json()
    assert v["blockers"] == ["Confirm the dose of 1 high-risk medicine", "Acknowledge 1 critical alert"]
    client.post(f"/api/visits/{vid}/meds/m1/confirm", json={"confirmed": True, "dose_confirmed": True})
    assert client.post(f"/api/visits/{vid}/cards/nope/ack", json={"reason": "Other"}).status_code == 404
    assert client.post(f"/api/visits/{vid}/cards/card-warf-nsaid/ack", json={"reason": "Benefit outweighs risk"}).json() == {"ok": True}
    v = client.get(f"/api/visits/{vid}").json()
    assert v["blockers"] == [] and v["cards_ack"]["card-warf-nsaid"]["reason"] == "Benefit outweighs risk"

    r = client.post(f"/api/visits/{vid}/sign")
    assert r.status_code == 200 and r.json()["signed_at"]
    txt = client.get(f"/api/visits/{vid}/export.txt").text
    assert "Electronically signed" in txt and "ibuprofen (said: Advil)" in txt and "M17.11" in txt
    assert client.get("/api/schedule").json()["appointments"][1]["status"] == "signed"
    assert client.patch(f"/api/visits/{vid}/note", json={"item_id": "hpi-1", "text": "x"}).status_code == 409
    events = [a["event"] for a in store.audit_for(vid)]
    assert {"visit_created", "processing_started", "note_ready", "medication_confirmed", "alert_acknowledged", "signed"} <= set(events)


@pytest.mark.parametrize("sample_id", [s["id"] for s in fixtures.samples()])
def test_every_sample_processes_cleanly(client, sample_id):
    s = fixtures.sample(sample_id)
    vid = _visit(client, s["patient_id"])
    client.post(f"/api/visits/{vid}/process", json={"source": "sample", "sample_id": sample_id})
    v = client.get(f"/api/visits/{vid}").json()
    assert v["status"] == "ready", v["error"]
    bad = [i for sec in v["note"]["sections"] for i in sec["items"] if i["verdict"] in ("unsupported", "contradicted")]
    assert not bad
    assert v["template"] == s["template"]
    assert all(d["icd10_valid"] for d in v["note"]["diagnoses"])


def test_sample_must_match_patient_and_exist(client):
    vid = _visit(client, "p2")
    assert client.post(f"/api/visits/{vid}/process", json={"source": "sample", "sample_id": "s1-knee-warfarin"}).status_code == 400
    assert client.post(f"/api/visits/{vid}/process", json={"source": "sample", "sample_id": "nope"}).status_code == 404


def test_live_without_claude_reports_plainly(client):
    vid = _visit(client, "p6")
    assert client.post(f"/api/visits/{vid}/process", json={"source": "live"}).status_code == 409  # consent first
    assert client.post(f"/api/visits/{vid}/consent", json={"consented": True}).json()["ok"]
    assert client.post(f"/api/visits/{vid}/process", json={"source": "live"}).status_code == 409  # no turns yet
    turns = [{"id": "x1", "speaker": "A", "text": "Your blood pressure is 142/90.", "start_ms": 0, "end_ms": 2000},
             {"id": "x2", "speaker": "B", "text": "I take amlodipine 5 mg.", "start_ms": 2500, "end_ms": 4000}]
    assert client.post(f"/api/visits/{vid}/live-turns", json={"turns": turns, "audio_seconds": 42}).json() == {"ok": True, "turns": 2}
    assert store.usage_get("audio_seconds") == 42
    client.post(f"/api/visits/{vid}/live-turns", json={"turns": turns[:1], "audio_seconds": 50})
    assert store.usage_get("audio_seconds") == 50
    assert client.get(f"/api/visits/{vid}").json()["transcript"][1]["text"] == "I take amlodipine 5 mg."
    client.post(f"/api/visits/{vid}/process", json={"source": "live"})
    st = client.get(f"/api/visits/{vid}/status").json()
    assert st["status"] == "error" and "not configured" in st["error"]
    assert any(s["state"] == "error" for s in st["stages"])
    assert client.get(f"/api/visits/{vid}").json()["blockers"] == ["Processing failed; retry or start a new visit."]


def test_token_needs_consent_key_and_cap(client, monkeypatch):
    vid = _visit(client, "p1")
    assert client.post("/api/stt/token", json={"visit_id": vid}).status_code == 409
    client.post(f"/api/visits/{vid}/consent", json={"consented": True})
    assert client.post("/api/stt/token", json={"visit_id": vid}).status_code == 503
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key=KEY))
    stt.transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"token": "tmp", "expires_in_seconds": 600}))
    r = client.post("/api/stt/token", json={"visit_id": vid}).json()
    assert r["token"] == "tmp" and r["ws_url"].startswith("wss://streaming.assemblyai.com/v3/ws?")
    assert "keyterms_prompt" in r["ws_url"] and "token=" not in r["ws_url"]
    store.usage_set("audio_seconds", config.settings.daily_audio_minutes * 60)
    assert client.post("/api/stt/token", json={"visit_id": vid}).status_code == 409
    assert client.post(f"/api/visits/{vid}/consent", json={"consented": False}).status_code == 400


def _aai_handler(state):
    def handler(req):
        p = req.url.path
        if p == "/v2/upload":
            return httpx.Response(200, json={"upload_url": "https://cdn/x"})
        if p == "/v2/transcript" and req.method == "POST":
            state["body"] = json.loads(req.content)
            return httpx.Response(200, json={"id": "tr9", "status": "queued"})
        if p == "/v2/transcript/tr9" and req.method == "GET":
            return httpx.Response(200, json={"id": "tr9", "status": "completed", "speech_model_used": "universal-3-5-pro",
                                             "audio_duration": 61, "utterances": [
                {"speaker": "A", "text": "Blood pressure today is 142/90.", "start": 0, "end": 3000},
                {"speaker": "B", "text": "I take amlodipine 5 mg every morning.", "start": 3500, "end": 6000}]})
        if p == "/v2/transcript/tr9" and req.method == "DELETE":
            state["deleted"] = True
            return httpx.Response(200, json={})
        return httpx.Response(404)
    return handler


class _Fake:
    """Fake Anthropic client: note, verifier and rewrite calls by system prompt."""

    def __init__(self):
        self.messages = self
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        system = kw["system"]
        if system.startswith("You check a draft"):
            lines = json.loads(kw["messages"][0]["content"].split("<note_lines>\n")[1].split("\n</note_lines>")[0])
            body = {"results": [{"item_id": ln["id"], "verdict": "supported", "reason": ""} for ln in lines]}
        elif system.startswith("You rewrite one section"):
            body = {"items": [{"id": "plan-1", "text": "Continue amlodipine 5 mg every morning.", "evidence": ["t2"],
                               "status": "documented"}]}
        else:
            body = {
                "visit_summary": "Blood pressure follow-up.", "speaker_roles": [{"speaker": "A", "role": "clinician"},
                                                                                {"speaker": "B", "role": "patient"}],
                "sections": {"chief_complaint": {"items": [{"id": "chief_complaint-1", "text": "Blood pressure follow-up.", "evidence": ["t1"], "status": "documented"}]},
                             "exam": {"items": [{"id": "exam-1", "text": "BP 142/90.", "evidence": ["t1"], "status": "documented"}]},
                             "plan": {"items": [{"id": "plan-1", "text": "Continue amlodipine 5 mg.", "evidence": ["t2"], "status": "documented"},
                                                {"id": "plan-2", "text": "Recheck BP at 150/95.", "evidence": ["t1"], "status": "documented"}]}},
                "medications": [{"id": "m1", "name": "amlodipine", "as_spoken": "amlodipine", "dose": "5", "unit": "mg", "route": "oral",
                                 "frequency": "every morning", "duration": "", "action": "continue", "indication": "", "evidence": ["t2"]}],
                "allergies": [], "orders": [], "diagnoses": [{"description": "Essential hypertension", "icd10": "I10", "evidence": ["t1"]}],
                "follow_up": {"text": "", "evidence": []}, "patient_instructions": "Keep taking amlodipine.",
                "not_discussed": [], "uncertain": [],
            }
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(body))], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=10))


def test_upload_final_pass_and_claude_note(client, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key=KEY, anthropic_api_key="sk-test"))
    state = {}
    stt.transport = httpx.MockTransport(_aai_handler(state))
    fake = _Fake()
    notes.client_factory = lambda: fake
    vid = _visit(client, "p6", template="soap")
    files = {"file": ("visit.webm", b"\x1a\x45\xdf\xa3" + b"0" * 5000, "audio/webm")}
    assert client.post(f"/api/visits/{vid}/audio", files=files).status_code == 409  # consent first
    client.post(f"/api/visits/{vid}/consent", json={"consented": True})
    r = client.post(f"/api/visits/{vid}/audio", files=files)
    assert r.status_code == 200 and r.json()["bytes"] == 5004
    v = client.get(f"/api/visits/{vid}").json()
    assert v["status"] == "ready", v["error"]
    assert state["body"]["domain"] == "medical-v1" and state["deleted"]
    assert "amlodipine" in state["body"]["keyterms_prompt"]
    assert v["engine_info"]["final_model"] == "universal-3-5-pro" and v["engine_info"]["medical_mode"] is True
    assert v["engine_info"]["note_model"] == "claude-sonnet-5-5"
    assert v["audio_seconds"] == 61 and store.usage_get("audio_seconds") == 61
    assert not list((config.settings.data_dir / "audio").glob(f"{vid}*"))  # recording deleted after the final pass
    plan = next(s for s in v["note"]["sections"] if s["id"] == "plan")
    verdicts = {i["id"]: i["verdict"] for i in plan["items"]}
    assert verdicts["plan-1"] == "supported"
    assert verdicts["plan-2"] == "unsupported"  # "150/95" was never said
    assert "Resolve 1 unsupported line" in v["blockers"]
    # edit the unsupported line, then the Ask rewrite
    v = client.patch(f"/api/visits/{vid}/note", json={"item_id": "plan-2", "text": "Recheck blood pressure."}).json()
    assert "Resolve 1 unsupported line" not in v["blockers"]
    sec = client.post(f"/api/visits/{vid}/ask", json={"section_id": "plan", "instruction": "Make it shorter"}).json()["section"]
    assert sec["items"][0]["text"] == "Continue amlodipine 5 mg every morning." and sec["items"][0]["verdict"] == "supported"
    calls = [c["model"] for c in fake.calls]
    assert calls.count("claude-haiku-5-5") >= 2


def test_upload_limits(client, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key=KEY, max_upload_mb=0))
    vid = _visit(client, "p7")
    client.post(f"/api/visits/{vid}/consent", json={"consented": True})
    assert client.post(f"/api/visits/{vid}/audio", files={"file": ("notes.txt", b"hello", "text/plain")}).status_code == 415
    assert client.post(f"/api/visits/{vid}/audio", files={"file": ("v.webm", b"abc", "audio/webm")}).status_code == 413
    assert client.post(f"/api/visits/{vid}/process", json={"source": "final"}).status_code == 409


def test_final_pass_failure_falls_back_to_live(client, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key=KEY, anthropic_api_key="sk-test"))
    stt.transport = httpx.MockTransport(lambda req: httpx.Response(500, json={"error": "boom"}))
    fake = _Fake()
    notes.client_factory = lambda: fake
    vid = _visit(client, "p6")
    client.post(f"/api/visits/{vid}/consent", json={"consented": True})
    client.post(f"/api/visits/{vid}/live-turns", json={"turns": [
        {"id": "a", "speaker": "A", "text": "Blood pressure today is 142/90.", "start_ms": 0, "end_ms": 1000},
        {"id": "b", "speaker": "B", "text": "I take amlodipine 5 mg every morning.", "start_ms": 1500, "end_ms": 3000}], "audio_seconds": 30})
    client.post(f"/api/visits/{vid}/audio", files={"file": ("v.webm", b"0" * 100, "audio/webm")})
    v = client.get(f"/api/visits/{vid}").json()
    assert v["status"] == "ready"
    stage = next(s for s in v["stages"] if s["id"] == "final_transcript")
    assert stage["state"] == "error" and "live transcript instead" in stage["detail"]
    assert any("live transcript was used" in w for w in v["engine_info"]["warnings"])


def test_note_cap_falls_back_for_samples(client):
    store.usage_set("note_runs", 10_000)
    vid = _visit(client, "p3")
    client.post(f"/api/visits/{vid}/process", json={"source": "sample", "sample_id": "s3-strep-penicillin"})
    st = client.get(f"/api/visits/{vid}/status").json()
    assert st["status"] == "ready"
    assert "Saved note" in next(s for s in st["stages"] if s["id"] == "note")["detail"]


def test_patch_note_edit_remove_and_fields(client):
    vid = _visit(client, "p4")
    client.post(f"/api/visits/{vid}/process", json={"source": "sample", "sample_id": "s4-pneumonia-hf"})
    v = client.patch(f"/api/visits/{vid}/note", json={"item_id": "hpi-2", "text": "Right-sided pleuritic chest pain."}).json()
    _sec = next(s for s in v["note"]["sections"] if s["id"] == "hpi")
    item = next(i for i in _sec["items"] if i["id"] == "hpi-2")
    assert item["edited"] and item["text"] == "Right-sided pleuritic chest pain."
    v = client.patch(f"/api/visits/{vid}/note", json={"item_id": "hpi-2", "text": ""}).json()
    assert all(i["id"] != "hpi-2" for s in v["note"]["sections"] for i in s["items"])
    v = client.patch(f"/api/visits/{vid}/note", json={"item_id": "patient_instructions", "text": "Rest and drink fluids."}).json()
    assert v["note"]["patient_instructions"] == "Rest and drink fluids."
    v = client.patch(f"/api/visits/{vid}/note", json={"section_id": "ros", "items": [{"text": "Denies chest pain at rest."}]}).json()
    ros = next(s for s in v["note"]["sections"] if s["id"] == "ros")
    assert ros["items"][0]["edited"] and ros["items"][0]["text"] == "Denies chest pain at rest."
    assert client.patch(f"/api/visits/{vid}/note", json={"item_id": "nope", "text": "x"}).status_code == 404
    assert client.patch(f"/api/visits/{vid}/note", json={}).status_code == 400
    assert client.post(f"/api/visits/{vid}/ask", json={"section_id": "plan", "instruction": "shorter"}).status_code == 503


def test_med_correction_rechecks(client):
    vid = _visit(client, "p1")
    client.post(f"/api/visits/{vid}/process", json={"source": "sample", "sample_id": "s1-knee-warfarin"})
    mc = client.post(f"/api/visits/{vid}/meds/m2/confirm", json={"confirmed": True, "corrected": {"name": "acetaminophen", "dose": "650"}}).json()
    row = next(r for r in mc["medications"] if r["id"] == "m2")
    assert row["confirmed"] and row["name"] == "acetaminophen" and row["dose"] == "650"
    assert not any(c["id"] == "card-warf-nsaid" for c in mc["cards"])  # the interaction went away with the correction
    v = client.get(f"/api/visits/{vid}").json()
    assert next(m for m in v["note"]["medications"] if m["id"] == "m2")["name"] == "acetaminophen"
    assert client.post(f"/api/visits/{vid}/meds/m99/confirm", json={"confirmed": True}).status_code == 404


def test_meds_stage_without_engine(client, monkeypatch):
    async def _missing(medications, patient, timeout_s=8.0):
        return None, "Drug checks unavailable: the drug-safety engine is not installed on this server."

    monkeypatch.setattr(drugs_bridge, "check_visit", _missing)
    vid = _visit(client, "p5")
    client.post(f"/api/visits/{vid}/process", json={"source": "sample", "sample_id": "s5-insomnia-back-pain"})
    v = client.get(f"/api/visits/{vid}").json()
    assert v["status"] == "ready"
    assert next(s for s in v["stages"] if s["id"] == "meds")["state"] == "skipped"
    assert v["medication_check"]["sources_checked"][0]["status"] == "unavailable"
    assert any("Confirm 5 medicines" in b for b in v["blockers"])


def test_specialty_from_session_and_keyterms(client):
    client.post("/api/session/role", json={"role": "doctor", "specialty": "cardiology"})
    vid = _visit(client, "p1")
    v = client.get(f"/api/visits/{vid}").json()
    assert v["specialty"] == "cardiology"
    kt = client.get(f"/api/visits/{vid}/keyterms").json()
    assert kt["live"][0] == "warfarin" and len(kt["live"]) <= 100 and len(kt["final"]) <= 1000
    client.post("/api/session/role", json={"role": "doctor", "specialty": "family_medicine"})


def test_recover_interrupted(client):
    vid = _visit(client, "p8")
    store.update_visit(vid, status="processing", stage="note",
                       stages=[{"id": "note", "label": "Draft", "state": "active", "detail": ""}])
    assert store.recover_interrupted() >= 1
    st = client.get(f"/api/visits/{vid}/status").json()
    assert st["status"] == "error" and "Retry" in st["error"] and st["stages"][0]["state"] == "error"


def test_selftest_skips_and_caches(client, monkeypatch):
    async def fake_drug(name, module, url, params=None):
        return {"name": name, "ok": True, "detail": "Reachable.", "seconds": 0.0}

    monkeypatch.setattr(selftest, "_drug_client", fake_drug)
    store.kv_set("selftest", None)
    first = client.get("/api/selftest").json()
    names = {c["name"]: c["ok"] for c in first["checks"]}
    assert names["AssemblyAI streaming token"] is None and names["Claude note (structured output)"] is None
    assert names["RxNorm (RxNav)"] is True and first["cached"] is False and first["ok"] is False
    second = client.get("/api/selftest").json()
    assert second["cached"] is True and second["next_run_in_s"] > 0
    assert "sk-" not in json.dumps(first) and KEY not in json.dumps(first)


def test_reset_schedule(client):
    vid = _visit(client, "p6")
    assert client.get("/api/schedule").json()["appointments"][0]["visit_id"] == vid
    assert client.post("/api/schedule/reset").json()["archived"] >= 1
    assert client.get("/api/schedule").json()["appointments"][0]["status"] == "scheduled"
    assert client.get(f"/api/visits/{vid}").status_code == 200  # archived, not deleted


def test_retry_after_note_failure_reuses_final_transcript(client, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key=KEY, anthropic_api_key="sk-test"))
    state = {}
    stt.transport = httpx.MockTransport(_aai_handler(state))

    class _Down:
        def __init__(self):
            self.messages = self

        async def create(self, **kw):
            err = Exception("overloaded")
            err.status_code = 529
            raise err

    notes.client_factory = lambda: _Down()
    vid = _visit(client, "p6")
    client.post(f"/api/visits/{vid}/consent", json={"consented": True})
    client.post(f"/api/visits/{vid}/audio", files={"file": ("v.webm", b"0" * 500, "audio/webm")})
    st = client.get(f"/api/visits/{vid}/status").json()
    assert st["status"] == "error" and "temporarily unavailable" in st["error"]
    fake = _Fake()
    notes.client_factory = lambda: fake
    stt.transport = httpx.MockTransport(lambda req: httpx.Response(500))  # a second upload would fail
    assert client.post(f"/api/visits/{vid}/process", json={"source": "final"}).json() == {"ok": True}
    v = client.get(f"/api/visits/{vid}").json()
    assert v["status"] == "ready", v["error"]
    assert "Reusing the final transcript" in next(s for s in v["stages"] if s["id"] == "final_transcript")["detail"]
