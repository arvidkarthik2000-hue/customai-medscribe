"""PriMock57 benchmark scoring (offline)."""
import asyncio
import dataclasses
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-test-"))

from app import config  # noqa: E402
from app.bench import primock as pb  # noqa: E402


def test_score_file_counts_drug_errors_swaps_and_negations():
    lookup = pb._drug_lookup()
    ref = "I take bisoprolol every morning and amoxicillin. No chest pain at all."
    hyp = "I take metoprolol every morning and amoxicillin. Chest pain at all."
    s = pb.score_file(ref, hyp, lookup)
    summary = pb.summarise({"f1": s})
    assert summary["drug_mentions"] == 2 and summary["drug_errors"] == 1
    assert summary["drug_for_drug_swaps"] == ["bisoprolol -> metoprolol"]
    assert summary["negations_before_medical_terms"] == 1 and summary["negations_lost"] == 1


def test_cpwer_finds_the_speaker_mapping():
    ref = {"doctor": "how are you today".split(), "patient": "fine thanks".split()}
    utts = [{"speaker": "B", "text": "How are you today?", "start": 0}, {"speaker": "A", "text": "Fine, thanks.", "start": 900}]
    assert pb.cpwer(ref, utts) == 0.0
    merged = [{"speaker": "A", "text": "How are you today? Fine, thanks.", "start": 0}]
    assert pb.cpwer(ref, merged) > 0.3  # everything on one speaker


def test_parse_textgrid_and_mcnemar():
    grid = 'intervals [1]:\n text = "" \nintervals [2]:\n text = "Hello? Hi, um." \nintervals [3]:\n text = "Say ""ah""." \n'
    assert pb.parse_textgrid(grid) == 'Hello? Hi, um. Say "ah".'
    base = {i: True for i in range(20)}
    other = {i: i >= 10 for i in range(20)}  # 10 wrong where the baseline was right
    r = pb.mcnemar(base, other)
    assert r["baseline_right_other_wrong"] == 10 and r["other_right_baseline_wrong"] == 0 and r["p"] < 0.01


def test_status_runs_once_per_deploy(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, assemblyai_api_key="k", data_dir=tmp_path))
    calls = []

    async def fake_run():
        calls.append(1)
        (tmp_path / pb.RESULTS_NAME).write_text('{"status": "done", "build": "%s", "models": {}}' % config.build_id())
        return {}

    monkeypatch.setattr(pb, "run_all", fake_run)
    monkeypatch.setattr(pb, "_task", None)

    async def scenario():
        first = pb.status()
        await asyncio.sleep(0)
        await pb._task
        second = pb.status()
        return first, second

    first, second = asyncio.run(scenario())
    assert first["status"] == "started" and second["status"] == "done" and calls == [1]


def test_generic_drug_list_is_within_budget():
    body = pb._configs()["assemblyai-u35-medical + drug list (ours)"]
    words = sum(len(t.split()) for t in body["keyterms_prompt"])
    assert 0 < words <= 600 and body["domain"] == "medical-v1" and body["speaker_labels"] is True
