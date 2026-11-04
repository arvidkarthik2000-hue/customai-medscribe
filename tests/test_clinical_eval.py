"""Offline tests for the end-to-end evaluator's scoring (the live run happens on the server)."""
import copy

from app.clinical import evaluate, fixtures


def test_wer_basic():
    assert evaluate.wer("take warfarin 5 mg daily", "take warfarin 5 mg daily") == 0.0
    assert abs(evaluate.wer("take warfarin 5 mg daily", "take wafarin 5 mg daily") - 0.2) < 1e-9
    assert evaluate.wer("", "") == 0.0


def test_score_perfect_transcript_and_saved_note():
    sample = fixtures.sample("s1-knee-warfarin")
    asr = [{"speaker": t["speaker"], "text": t["text"]} for t in sample["turns"]]
    note = copy.deepcopy(sample["saved_note"])
    roles = sample["speaker_roles"]
    s = evaluate.score(sample, asr, note, roles, {"cards": []})
    assert s["transcript_wer"] == 0.0
    n, d = map(int, s["drug_names_heard"].split("/"))
    assert n == d and d >= 2
    assert s["note_medicine_recall"] == 1.0
    assert s["note_medicines_extra"] == []
    assert s["dose_and_action_accuracy"] == 1.0
    assert s["speaker_agreement"] == 1.0
    assert s["speaker_roles_correct"].split("/")[0] == s["speaker_roles_correct"].split("/")[1]


def test_score_catches_wrong_drug_and_dose():
    sample = fixtures.sample("s1-knee-warfarin")
    asr = [{"speaker": t["speaker"], "text": t["text"].replace("ibuprofen", "I be profen")} for t in sample["turns"]]
    note = copy.deepcopy(sample["saved_note"])
    for m in note["medications"]:
        if m["name"] == "warfarin":
            m["dose"] = "7.5"
    note["medications"].append({"name": "naproxen", "dose": "500", "unit": "mg", "action": "start"})
    s = evaluate.score(sample, asr, note, sample["speaker_roles"], None)
    assert "naproxen" in s["note_medicines_extra"]
    assert s["dose_and_action_accuracy"] < 1.0
    assert any(m["medicine"] == "warfarin" for m in s["dose_or_action_mismatches"])


def test_every_sample_has_test_audio():
    for sample in fixtures.samples():
        assert (evaluate.AUDIO_DIR / f"{sample['id']}.mp3").exists(), sample["id"]


def test_status_disabled_without_keys(monkeypatch):
    class S:
        speech_ready = False
        notes_ready = False
        data_dir = evaluate.config.settings.data_dir

    monkeypatch.setattr(evaluate.config, "settings", S())
    assert evaluate.status()["status"] == "disabled"
