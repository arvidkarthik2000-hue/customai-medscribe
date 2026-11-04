"""End-to-end accuracy check against the live services, run on the server.

Voiced versions of the five scripted sample visits (synthetic text-to-speech
voices, one per speaker; app/clinical/data/eval_audio/<sample>.mp3) go through
the same steps as a real visit:

    AssemblyAI final pass (Medical Mode, keyterms) -> Claude note -> verifier
    (Haiku + deterministic checks) -> medication checks

and each result is scored against its script and its hand-checked saved note.
Synthetic voices are cleaner than clinic audio, so these numbers are an upper
bound; they catch integration and parameter problems, not real-world accuracy.

A run is started at most once per EVAL_MIN_INTERVAL_S (and once after each deploy)
and runs in the background;
GET /api/eval/public returns the last results (no secrets, synthetic data only).
"""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path

from .. import config
from . import drugs_bridge, fixtures, notemodel, notes, stt, verify

log = logging.getLogger("medscribe.eval")

AUDIO_DIR = Path(__file__).parent / "data" / "eval_audio"
EVAL_MIN_INTERVAL_S = 6 * 3600
RESULTS_NAME = "eval_results.json"

_lock = asyncio.Lock()
_task: asyncio.Task | None = None


# ---------------------------------------------------------------- scoring helpers

def _words(text: str) -> list[str]:
    text = text.lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9.' ]+", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    return [w.strip("'") for w in text.split() if w.strip("'")]


def wer(reference: str, hypothesis: str) -> float:
    r, h = _words(reference), _words(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / len(r)


def _generic(name: str | None) -> str:
    if not name:
        return ""
    try:
        from app.drugs.kb import kb  # lazy: the drug layer is optional
        g = kb().lookup(name)
        if g:
            return g
    except Exception:  # noqa: BLE001
        pass
    return re.sub(r"\s+", " ", name.lower()).strip()


def _numbers(text: str | None) -> list[str]:
    """Numbers in a dose field; "1,000" and "1000" are the same number."""
    return re.findall(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3}\b)", "", str(text or "")))


def _speaker_mapping(script_turns: list[dict], asr_turns: list[dict]) -> tuple[dict[str, str], float]:
    """Map each ASR speaker label to the script speaker it overlaps most (by shared words)."""
    script_sets = [(t["speaker"], set(_words(t["text"]))) for t in script_turns]
    votes: dict[str, dict[str, int]] = {}
    per_turn: list[tuple[str, str]] = []
    for t in asr_turns:
        words = set(_words(t.get("text", "")))
        if not words:
            continue
        best, best_score = None, 0.0
        for spk, ws in script_sets:
            if not ws:
                continue
            score = len(words & ws) / len(words | ws)
            if score > best_score:
                best, best_score = spk, score
        if best is None:
            continue
        per_turn.append((t.get("speaker", "?"), best))
        votes.setdefault(t.get("speaker", "?"), {}).setdefault(best, 0)
        votes[t.get("speaker", "?")][best] += 1
    mapping = {label: max(v, key=v.get) for label, v in votes.items()}
    agree = sum(1 for label, spk in per_turn if mapping.get(label) == spk)
    return mapping, (agree / len(per_turn) if per_turn else 0.0)


def score(sample: dict, asr_turns: list[dict], note: dict, speaker_roles: dict, mc: dict | None) -> dict:
    script_text = " ".join(t["text"] for t in sample["turns"])
    asr_text = " ".join(t.get("text", "") for t in asr_turns)
    expected = sample["saved_note"].get("medications") or []
    got = note.get("medications") or []

    # 1. drug names heard: each expected medicine, as spoken, appears in the transcript
    heard = []
    asr_words = " " + " ".join(_words(asr_text)) + " "
    for m in expected:
        spoken = m.get("as_spoken") or m.get("name") or ""
        names = {n for n in (" ".join(_words(spoken)), " ".join(_words(m.get("name") or ""))) if n}
        heard.append({"medicine": m.get("name"), "as_spoken": spoken,
                      "heard": any(f" {n} " in asr_words for n in names)})

    # 2. medicines in the note vs the script's medicines
    exp_by = {_generic(m.get("name")): m for m in expected}
    got_by = {_generic(m.get("name")): m for m in got}
    matched = [g for g in exp_by if g in got_by]
    missing = [exp_by[g].get("name") for g in exp_by if g not in got_by]
    extra = [got_by[g].get("name") for g in got_by if g not in exp_by]
    dose_ok, dose_rows = 0, []
    for g in matched:
        e, o = exp_by[g], got_by[g]
        ok = _numbers(e.get("dose")) == _numbers(o.get("dose")) and (e.get("action") or "") == (o.get("action") or "")
        dose_ok += ok
        if not ok:
            dose_rows.append({"medicine": e.get("name"), "expected": f"{e.get('dose')} {e.get('unit')} ({e.get('action')})",
                              "got": f"{o.get('dose')} {o.get('unit')} ({o.get('action')})"})

    # 3. speakers: diarization agreement and role assignment
    mapping, diar = _speaker_mapping(sample["turns"], asr_turns)
    script_roles = sample.get("speaker_roles", {})
    role_hits = [speaker_roles.get(label) == script_roles.get(spk) for label, spk in mapping.items()]

    # 4. verifier flags and alerts
    sections = note.get("sections") or []
    if isinstance(sections, dict):  # raw schema form {section_id: [items]}
        items = [it for its in sections.values() for it in (its or []) if isinstance(it, dict)]
    else:
        items = [it for s in sections for it in s.get("items", [])]
    flagged = [{"text": it.get("text"), "verdict": it.get("verdict"), "reason": it.get("verdict_reason")}
               for it in items if it.get("verdict") in ("unsupported", "contradicted")]
    cards = [{"indicator": c.get("indicator"), "category": c.get("category"), "summary": c.get("summary")}
             for c in (mc or {}).get("cards") or [] if c.get("category") != "prevention"]

    n_exp = max(1, len(exp_by))
    return {
        "transcript_wer": round(wer(script_text, asr_text), 4),
        "drug_names_heard": f"{sum(h['heard'] for h in heard)}/{len(heard)}",
        "drug_names_missed": [h for h in heard if not h["heard"]],
        "note_medicine_recall": round(len(matched) / n_exp, 3),
        "note_medicines_missing": missing,
        "note_medicines_extra": extra,
        "dose_and_action_accuracy": round(dose_ok / max(1, len(matched)), 3),
        "dose_or_action_mismatches": dose_rows,
        "speaker_agreement": round(diar, 3),
        "speaker_roles_correct": f"{sum(role_hits)}/{len(role_hits)}",
        "lines": len(items),
        "lines_flagged": flagged,
        "alerts": cards,
    }


# ---------------------------------------------------------------- run

def _results_path() -> Path:
    return config.settings.data_dir / RESULTS_NAME


def last_results() -> dict | None:
    p = _results_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


async def _one(sample: dict) -> dict:
    sid = sample["id"]
    audio = AUDIO_DIR / f"{sid}.mp3"
    out: dict = {"sample": sid, "title": sample.get("title")}
    if not audio.exists():
        return {**out, "error": "No test audio for this sample."}
    chart = fixtures.patient(sample["patient_id"]) or {}
    specialty = "family_medicine"
    timings: dict[str, float] = {}
    tmp = Path(tempfile.mkdtemp(prefix="eval-"))
    try:
        local = tmp / audio.name
        shutil.copy(audio, local)
        t0 = time.monotonic()
        keyterms = stt.build_keyterms(fixtures.keyterm_seed(chart), specialty)
        tr = await stt.transcribe_file(local, keyterms["final"])
        timings["final_transcript_s"] = round(time.monotonic() - t0, 1)
        turns = tr["turns"]
        meta = tr["meta"]
        out["speech"] = {"model": meta.get("speech_model_used"), "medical_mode": meta.get("medical_mode"), "audio_via": meta.get("audio_via"),
                         "keyterms": meta.get("keyterms_used"), "audio_s": meta.get("audio_duration"),
                         "warnings": meta.get("warnings", []), "turns": len(turns)}

        t1 = time.monotonic()
        note, nmeta = await notes.generate_note(turns, chart, template=sample.get("template") or "soap",
                                                specialty=specialty, style="concise", engine="sonnet",
                                                visit_date=fixtures.today_local().isoformat())
        timings["note_s"] = round(time.monotonic() - t1, 1)
        out["note_model"] = nmeta.get("model")
        speaker_roles = note.pop("speaker_roles", {}) or {}

        t2 = time.monotonic()
        deterministic = verify.run(note, turns, chart)
        model_verdicts, vmeta = await notes.verify_note(note, turns)
        notemodel.apply_verdicts(note, model_verdicts, deterministic, model_note="")
        timings["verify_s"] = round(time.monotonic() - t2, 1)
        out["verifier_model"] = vmeta.get("model")

        t3 = time.monotonic()
        mc, detail = await drugs_bridge.check_visit(note.get("medications") or [], copy.deepcopy(chart))
        timings["meds_s"] = round(time.monotonic() - t3, 1)
        out["meds_detail"] = detail

        out["scores"] = score(sample, turns, note, speaker_roles, mc)
        out["timings"] = timings
        out["after_audio_s"] = round(sum(v for k, v in timings.items()), 1)
    except Exception as exc:  # noqa: BLE001 - report every failure plainly
        log.warning("eval %s failed: %s", sid, exc)
        out["error"] = f"{exc.__class__.__name__}: {exc}"
        out["timings"] = timings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _summary(rows: list[dict]) -> dict:
    ok = [r for r in rows if "scores" in r]
    if not ok:
        return {"completed": 0, "of": len(rows)}
    heard = [tuple(map(int, r["scores"]["drug_names_heard"].split("/"))) for r in ok]
    h_num, h_den = sum(a for a, _ in heard), sum(b for _, b in heard)
    wrong = sum(len(r["scores"]["note_medicines_extra"]) for r in ok)
    recall = sum(r["scores"]["note_medicine_recall"] for r in ok) / len(ok)
    dose = sum(r["scores"]["dose_and_action_accuracy"] for r in ok) / len(ok)
    worst_wait = max(r.get("after_audio_s", 0) for r in ok)
    return {
        "completed": len(ok), "of": len(rows),
        "mean_transcript_wer": round(sum(r["scores"]["transcript_wer"] for r in ok) / len(ok), 4),
        "drug_names_heard": f"{h_num}/{h_den}",
        "note_medicine_recall": round(recall, 3),
        "note_medicines_extra": wrong,
        "dose_and_action_accuracy": round(dose, 3),
        "lines_flagged": sum(len(r["scores"]["lines_flagged"]) for r in ok),
        "slowest_processing_s": worst_wait,
        "gates": {
            "drug_recall_at_least_95": recall >= 0.95,
            "no_unexpected_medicines": wrong == 0,
            "dose_accuracy_at_least_95": dose >= 0.95,
            "processing_under_60s": worst_wait < 60,
        },
        "caveat": "Synthetic text-to-speech voices; real clinic audio will score lower.",
    }


async def run_all() -> dict:
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows = []
    for sample in fixtures.samples():
        rows.append(await _one(sample))
    result = {"status": "done", "build": config.build_id(), "started_at": started,
              "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "summary": _summary(rows), "results": rows}
    _results_path().write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def status() -> dict:
    """Last results, and start a new run in the background if allowed."""
    global _task
    s = config.settings
    last = last_results()
    running = _task is not None and not _task.done()
    if running:
        return {"status": "running", "previous": last}
    if not (s.speech_ready and s.notes_ready):
        return {"status": "disabled", "detail": "AssemblyAI and Claude keys are both needed.", "previous": last}
    age = None
    if last and last.get("finished_at"):
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(last["finished_at"])).total_seconds()
        except ValueError:
            age = None
    same_build = bool(last) and last.get("build") == config.build_id()
    if age is not None and age < EVAL_MIN_INTERVAL_S and same_build:
        return {**last, "next_run_after_s": int(EVAL_MIN_INTERVAL_S - age)}
    _task = asyncio.get_running_loop().create_task(run_all())
    return {"status": "started", "detail": "Running the five voiced sample visits; check back in about five minutes.",
            "previous": last}
