"""Note engine: schema rules, prompt, Claude call parameters and retries, verifier, deterministic checks."""
import asyncio
import copy
import dataclasses
import json
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-test-"))

import pytest  # noqa: E402

from app import config  # noqa: E402
from app.clinical import fixtures, icd, notemodel, notes, schema, verify  # noqa: E402
from app.clinical.templates import TEMPLATES  # noqa: E402

FORBIDDEN = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
             "maxItems", "pattern", "minProperties", "maxProperties"}


def run(coro):
    return asyncio.run(coro)


def _walk(node, path, stats):
    if isinstance(node, dict):
        for k in node:
            assert k not in FORBIDDEN, f"{path}: unsupported keyword {k}"
        if node.get("minItems", 0) > 1:
            raise AssertionError(f"{path}: minItems above 1")
        if "anyOf" in node:
            stats["anyOf"] += 1
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, f"{path}: additionalProperties must be false"
            props = node.get("properties", {})
            assert set(node.get("required", [])) == set(props), f"{path}: every property must be required"
            stats["objects"] += 1
        if "$ref" in node:
            stats["refs"].append(node["$ref"])
        for k, v in node.items():
            _walk(v, f"{path}.{k}", stats)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk(v, f"{path}[{i}]", stats)


@pytest.mark.parametrize("schema_fn", [*(lambda t=t: schema.note_schema(t) for t in TEMPLATES),
                                       schema.verifier_schema, schema.section_rewrite_schema])
def test_schemas_follow_structured_output_rules(schema_fn):
    sch = schema_fn()
    stats = {"anyOf": 0, "objects": 0, "refs": []}
    _walk(sch, "$", stats)
    assert stats["anyOf"] <= 16
    defs = sch.get("$defs", {})
    for ref in stats["refs"]:
        assert ref.startswith("#/$defs/"), "only internal refs"
        name = ref.split("/")[-1]
        assert name in defs
        inner = {"anyOf": 0, "objects": 0, "refs": []}
        _walk(defs[name], "def", inner)
        assert not inner["refs"], "no recursion"
    json.dumps(sch)  # serialisable


def test_note_schema_has_template_sections():
    for t, cfg in TEMPLATES.items():
        props = schema.note_schema(t)["properties"]["sections"]["properties"]
        assert list(props) == [sid for sid, _ in cfg["sections"]]


def test_prompt_keeps_chart_separate():
    s = fixtures.sample("s1-knee-warfarin")
    chart = fixtures.patient("p1")
    prompt = notes.build_note_prompt(s["turns"], chart, "soap", "family_medicine", "concise", "2026-11-03")
    assert "<chart_context>" in prompt and "</chart_context>" in prompt
    assert "NOT part of the visit" in prompt
    assert prompt.index("</chart_context>") < prompt.index("<transcript>")
    assert "[t1] A: Good morning, Mr. Okafor." in prompt
    assert "warfarin 5 mg once daily in the evening" in prompt  # chart meds, labelled as chart context
    assert "Chief complaint" in prompt and "hpi" in prompt
    assert "Never add a brand name" in notes.SYSTEM and "Retractions win" in notes.SYSTEM


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        text, stop = reply if isinstance(reply, tuple) else (reply, "end_turn")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=900))


class FakeClient:
    def __init__(self, replies):
        self.messages = FakeMessages(replies)


class FakeAPIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status
        self.message = message


@pytest.fixture
def fake(monkeypatch):
    holder = {}

    def install(replies):
        client = FakeClient(replies)
        holder["client"] = client
        monkeypatch.setattr(notes, "client_factory", lambda: client)
        return client

    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, anthropic_api_key="sk-test",
                                                                 note_effort="medium"))
    yield install
    notes.client_factory = None


def _s1_model_output():
    return json.dumps(fixtures.sample("s1-knee-warfarin")["saved_note"])


def test_generate_note_sonnet_parameters(fake):
    client = fake([_s1_model_output()])
    s = fixtures.sample("s1-knee-warfarin")
    note, meta = run(notes.generate_note(s["turns"], fixtures.patient("p1"), template="soap", specialty="family_medicine",
                                         style="concise", engine="sonnet", visit_date="2026-11-03"))
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["thinking"] == {"type": "between_tools"}
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["output_config"]["format"]["schema"]["properties"]["sections"]
    assert [sec["id"] for sec in note["sections"]] == [sid for sid, _ in TEMPLATES["soap"]["sections"]]
    assert note["sections"][0]["title"] == "Chief complaint"
    assert note["medications"][1]["as_spoken"] == "Advil" and note["medications"][1]["name"] == "ibuprofen"
    assert all(d["icd10_valid"] for d in note["diagnoses"])
    assert meta["model"] == "claude-sonnet-5-5" and meta["input_tokens"] == 1200


def test_haiku_disables_thinking_and_clamps_effort(fake, monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, note_effort="max"))
    client = fake([_s1_model_output()])
    s = fixtures.sample("s1-knee-warfarin")
    run(notes.generate_note(s["turns"], None, template="soap", specialty="family_medicine", style="concise",
                            engine="haiku", visit_date="2026-11-03"))
    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["thinking"] == {"type": "disabled"}
    assert call["output_config"]["effort"] == "high"


def test_refusal_is_reported(fake):
    fake([("{}", "refusal")])
    with pytest.raises(notes.NoteError) as e:
        run(notes.generate_note([{"id": "t1", "speaker": "A", "text": "hi"}], None, template="soap",
                                specialty="family_medicine", style="concise", engine="sonnet", visit_date="2026-11-03"))
    assert "declined" in str(e.value)


def test_max_tokens_retries_with_more_room(fake):
    client = fake([("{", "max_tokens"), _s1_model_output()])
    s = fixtures.sample("s1-knee-warfarin")
    note, meta = run(notes.generate_note(s["turns"], None, template="soap", specialty="family_medicine",
                                         style="concise", engine="sonnet", visit_date="2026-11-03"))
    assert client.messages.calls[1]["max_tokens"] == 24000 > client.messages.calls[0]["max_tokens"]
    assert any("more space" in w for w in meta["warnings"])


def test_rejected_thinking_setting_retries_without_it(fake):
    client = fake([FakeAPIError(400, "thinking.type between_tools is not supported"), _s1_model_output()])
    s = fixtures.sample("s1-knee-warfarin")
    _note, meta = run(notes.generate_note(s["turns"], None, template="soap", specialty="family_medicine",
                                          style="concise", engine="sonnet", visit_date="2026-11-03"))
    assert "thinking" in client.messages.calls[0] and "thinking" not in client.messages.calls[1]
    assert meta["warnings"]


def test_bad_key_message(fake):
    fake([FakeAPIError(401, "invalid x-api-key")])
    with pytest.raises(notes.NoteError) as e:
        run(notes.generate_note([{"id": "t1", "speaker": "A", "text": "hi"}], None, template="soap",
                                specialty="family_medicine", style="concise", engine="sonnet", visit_date="2026-11-03"))
    assert "401" in str(e.value) and "sk-test" not in str(e.value)


def test_verifier_maps_results(fake):
    s = fixtures.sample("s3-strep-penicillin")
    note = notemodel.normalise(s["saved_note"], s["template"], s["turns"])
    results = {"results": [
        {"item_id": "hpi-1", "verdict": "Supported", "reason": ""},
        {"item_id": "plan-2", "verdict": "partial", "reason": "Duration stated, frequency implied."},
        {"item_id": "nonexistent", "verdict": "unsupported", "reason": "x"},
        {"item_id": "m2", "verdict": "supported", "reason": ""},
    ]}
    client = fake([json.dumps(results)])
    verdicts, meta = run(notes.verify_note(note, s["turns"]))
    assert verdicts == {"hpi-1": ("supported", ""), "plan-2": ("partial", "Duration stated, frequency implied."),
                        "m2": ("supported", "")}
    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["thinking"] == {"type": "disabled"}
    assert meta["lines"] > 10


def _tx():
    return [
        {"id": "t1", "speaker": "A", "text": "Your blood pressure is 128/78 today."},
        {"id": "t2", "speaker": "B", "text": "I take lisinopril 20 mg every morning."},
        {"id": "t3", "speaker": "A", "text": "Your potassium was 5.1 last month."},
    ]


def _note_with(items, meds=()):
    return {"sections": [{"id": "plan", "title": "Plan", "items": items}], "medications": list(meds)}


def test_deterministic_checks():
    items = [
        {"id": "a", "text": "BP 128/78.", "evidence": ["t1"], "status": "documented"},
        {"id": "b", "text": "BP 130/78.", "evidence": ["t1"], "status": "documented"},
        {"id": "c", "text": "Potassium 5.1.", "evidence": ["t1"], "status": "documented"},
        {"id": "d", "text": "Lisinopril continued.", "evidence": ["t9"], "status": "documented"},
        {"id": "e", "text": "Denies cough.", "evidence": [], "status": "negative"},
        {"id": "f", "text": "Not discussed", "evidence": [], "status": "not_discussed"},
        {"id": "g", "text": "A 66-year-old man.", "evidence": ["t1"], "status": "documented"},
    ]
    meds = [
        {"id": "m1", "name": "lisinopril", "as_spoken": "lisinopril", "dose": "20", "evidence": ["t2"]},
        {"id": "m2", "name": "lisinopril", "as_spoken": "lisinopril", "dose": "40", "evidence": ["t2"]},
        {"id": "m3", "name": "lisinopril", "as_spoken": "lisinopril", "dose": "20", "evidence": ["t1"]},
        {"id": "m4", "name": "metoprolol", "as_spoken": "metoprolol", "dose": "", "evidence": ["t1"]},
    ]
    issues = verify.run(_note_with(items, meds), _tx(), {"age": 66})
    assert "a" not in issues and "f" not in issues and "g" not in issues and "m1" not in issues
    assert issues["b"][0] == "unsupported" and "130" in issues["b"][1]
    assert issues["c"][0] == "partial"
    assert issues["d"][0] == "unsupported"
    assert issues["e"][0] == "unsupported"
    assert issues["m2"][0] == "unsupported" and "40" in issues["m2"][1]
    assert issues["m3"][0] == "partial"
    assert issues["m4"][0] == "unsupported"


def test_saved_sample_notes_pass_deterministic_checks():
    for s in fixtures.samples():
        note = notemodel.normalise(s["saved_note"], s["template"], s["turns"])
        assert verify.run(note, s["turns"], fixtures.patient(s["patient_id"])) == {}, s["id"]
        assert all(d["icd10_valid"] for d in note["diagnoses"]), s["id"]
        assert note["medications"], s["id"]


def test_apply_verdicts_takes_the_worse():
    note = {"sections": [{"id": "plan", "title": "Plan", "items": [
        {"id": "x", "text": "t", "evidence": ["t1"], "status": "documented", "edited": False},
        {"id": "y", "text": "t", "evidence": ["t1"], "status": "documented", "edited": False},
        {"id": "z", "text": "Not discussed", "evidence": [], "status": "not_discussed", "edited": False},
    ]}], "medications": [{"id": "m1"}]}
    notemodel.apply_verdicts(note, {"x": ("supported", ""), "y": ("partial", "model")},
                             {"y": ("unsupported", "number not said"), "m1": ("partial", "dose elsewhere")})
    items = {i["id"]: i for i in note["sections"][0]["items"]}
    assert items["x"]["verdict"] == "supported"
    assert items["y"]["verdict"] == "unsupported" and items["y"]["verdict_reason"] == "number not said"
    assert items["z"]["verdict"] == "unchecked"
    assert note["medications"][0]["verdict"] == "partial"


def test_normalise_fills_and_cleans():
    raw = {"sections": {"hpi": {"items": [
        {"id": "hpi-1", "text": "One.", "evidence": ["[t1]", "t1", "t2"], "status": "Documented"},
        {"id": "hpi-1", "text": "Two.", "evidence": [], "status": "weird"},
        {"id": "", "text": "", "evidence": [], "status": "documented"},
    ]}}, "medications": [{"id": "m1", "name": "warfarin", "action": "CONTINUE"}, {"id": "m1", "name": "aspirin", "action": "x"}],
        "diagnoses": [{"description": "HTN", "icd10": "i10", "evidence": []}], "speaker_roles": [{"speaker": "A", "role": "Clinician"}]}
    note = notemodel.normalise(raw, "soap")
    hpi = next(s for s in note["sections"] if s["id"] == "hpi")
    assert [i["id"] for i in hpi["items"]] == ["hpi-1", "hpi-2"]
    assert hpi["items"][0]["evidence"] == ["t1", "t2"] and hpi["items"][0]["status"] == "documented"
    assert hpi["items"][1]["status"] == "documented"
    cc = next(s for s in note["sections"] if s["id"] == "chief_complaint")
    assert cc["items"][0]["status"] == "not_discussed"
    assert [m["id"] for m in note["medications"]] == ["m1", "m2"]
    assert note["medications"][0]["action"] == "continue" and note["medications"][1]["action"] == "discussed"
    assert note["diagnoses"][0]["icd10"] == "I10" and note["diagnoses"][0]["icd10_valid"]
    assert note["speaker_roles"] == {"A": "clinician"}


def test_icd_validation():
    assert icd.validate("E119")["icd10"] == "E11.9" and icd.validate("E119")["icd10_valid"]
    assert icd.validate("S39.012A")["icd10_billable"]
    assert not icd.validate("XYZ")["icd10_valid"]
    assert not icd.validate("")["icd10_valid"]


def test_blockers_rules():
    note = {"sections": [{"id": "plan", "title": "Plan", "items": [
        {"id": "p1", "text": "x", "verdict": "unsupported", "edited": False},
        {"id": "p2", "text": "y", "verdict": "contradicted", "edited": True},
        {"id": "p3", "text": "z", "verdict": "partial", "edited": False}]}], "medications": []}
    mc = {"medications": [
        {"id": "m1", "action": "continue", "high_risk": True, "confirmed": True, "dose_confirmed": False},
        {"id": "m2", "action": "discussed", "high_risk": True, "confirmed": True, "dose_confirmed": False},
        {"id": "m3", "action": "start", "high_risk": False, "confirmed": False}],
        "cards": [{"id": "c1", "indicator": "critical"}, {"id": "c2", "indicator": "critical"}, {"id": "c3", "indicator": "warning"}]}
    visit = {"status": "ready", "note": note, "medication_check": mc, "cards_ack": {"c1": {"reason": "ok"}}}
    assert notemodel.blockers(visit) == ["Confirm 1 medicine", "Confirm the dose of 1 high-risk medicine",
                                         "Resolve 1 unsupported line", "Acknowledge 1 critical alert"]
    assert notemodel.blockers({**visit, "status": "signed"}) == []
    assert notemodel.blockers({"status": "processing"}) == ["The note is not ready yet."]
