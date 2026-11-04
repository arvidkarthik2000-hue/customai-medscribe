"""PriMock57 speech benchmark, run on the server (which holds the AssemblyAI key).

55 mock primary-care consultations (UK English, real human voices; PriMock57, CC BY 4.0,
Papadopoulos Korfiatis et al., ACL 2022), prepared as single-stream audio by the Omi Health
medical STT benchmark (MIT). Our AssemblyAI set-ups are scored with that benchmark's own
medical-term rules next to the published transcripts of other models, plus checks that
matter for us:

- drug-name errors per drug mention, and drug-for-drug swaps (a real drug heard as another);
- negations before a medical term ("no chest pain") lost;
- a paired test on the same drug mentions against each comparison model;
- speaker-attributed WER (cpWER) against PriMock57's doctor and patient transcripts.

Each audio file is downloaded once and uploaded to AssemblyAI once; every set-up reuses the
upload. Runs at most once per deploy, in the background. Results contain no secrets.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import itertools
import json
import logging
import math
import re
import tempfile
import time
from pathlib import Path

import httpx
from rapidfuzz.distance import Levenshtein

from .. import config
from ..clinical import stt
from .omi_eval import medical_wer as mw

log = logging.getLogger("medscribe.bench")

OMI_COMMIT = "c0e6c11528ec150f5ff98627f8e86c2d6e731c33"
PRIMOCK_COMMIT = "cd2ac707ad03cb4d2531f4ec6b90c659bf4357c5"
OMI_RAW = f"https://raw.githubusercontent.com/Omi-Health/medical-STT-eval/{OMI_COMMIT}"
AUDIO_URL = f"https://media.githubusercontent.com/media/Omi-Health/medical-STT-eval/{OMI_COMMIT}/data/raw_audio/{{name}}_conversation.wav"
TEXTGRID_URL = f"https://raw.githubusercontent.com/babylonhealth/primock57/{PRIMOCK_COMMIT}/transcripts/{{name}}_{{role}}.TextGrid"

FILES = (
    "day1_consultation01 day1_consultation02 day1_consultation03 day1_consultation04 day1_consultation05 "
    "day1_consultation06 day1_consultation08 day1_consultation09 day1_consultation10 day1_consultation11 "
    "day1_consultation12 day1_consultation13 day1_consultation14 day1_consultation15 day2_consultation01 "
    "day2_consultation02 day2_consultation03 day2_consultation04 day2_consultation05 day2_consultation06 "
    "day2_consultation07 day2_consultation08 day2_consultation09 day2_consultation10 day3_consultation01 "
    "day3_consultation02 day3_consultation04 day3_consultation05 day3_consultation06 day3_consultation07 "
    "day3_consultation08 day3_consultation09 day3_consultation10 day4_consultation01 day4_consultation02 "
    "day4_consultation03 day4_consultation04 day4_consultation05 day4_consultation06 day4_consultation07 "
    "day4_consultation08 day4_consultation09 day4_consultation10 day5_consultation01 day5_consultation02 "
    "day5_consultation03 day5_consultation04 day5_consultation05 day5_consultation06 day5_consultation07 "
    "day5_consultation08 day5_consultation09 day5_consultation10 day5_consultation11 day5_consultation12"
).split()

# Published transcripts scored alongside ours (same files, same scorer).
PUBLISHED = (
    "assemblyai-universal-3-pro-medical", "vibevoice-asr-9b", "qwen3-asr-1.7b", "google-gemini-2.5-pro",
    "elevenlabs-scribe_v2", "deepgram-nova-3-medical", "soniox-stt-async-v4", "groq-whisper-large-v3",
    "parakeet-tdt-1.1b", "parakeet-parakeet-tdt-0.6b-v2", "qwen3-asr-0.6b", "google-medasr",
)
BASELINE = "assemblyai-universal-3-pro-medical"


def _configs() -> dict[str, dict]:
    generic = stt.build_keyterms([], "family_medicine")["final"]  # what a patient without a chart gets
    base = {"speech_models": [config.settings.final_speech_model], "domain": "medical-v1",
            "speaker_labels": True, "language_code": "en"}
    return {
        "assemblyai-u35-medical (ours)": base,
        "assemblyai-u35-medical + drug list (ours)": {**base, "keyterms_prompt": generic},
    }


NEG = {"no", "not", "never", "nothing", "none", "nor", "without", "denies", "denied", "neither", "nobody", "nowhere"}
RESULTS_NAME = "bench_primock57.json"
CONCURRENCY = 5
_task: asyncio.Task | None = None


# ---------------------------------------------------------------- scoring

def align(ref: list[str], hyp: list[str]) -> list[tuple[str, str, str]]:
    """A minimum edit alignment in the benchmark's format, computed in C."""
    ops: list[tuple[str, str, str]] = []
    for tag, i1, i2, j1, j2 in Levenshtein.opcodes(ref, hyp):
        if tag == "equal":
            ops += [("correct", ref[k], hyp[j1 + k - i1]) for k in range(i1, i2)]
        elif tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                r = ref[i1 + k] if i1 + k < i2 else ""
                h = hyp[j1 + k] if j1 + k < j2 else ""
                ops.append(("substitution" if r and h else ("deletion" if r else "insertion"), r, h))
        elif tag == "delete":
            ops += [("deletion", ref[k], "") for k in range(i1, i2)]
        else:
            ops += [("insertion", "", hyp[k]) for k in range(j1, j2)]
    return ops


mw._align_words = align  # same rules as the benchmark, faster


def _drug_lookup():
    try:
        from ..drugs.kb import kb

        k = kb()
        return k.lookup
    except Exception:  # noqa: BLE001
        return lambda _w: None


def score_file(ref: str, hyp: str, lookup) -> dict:
    terms = mw.get_medical_terms()
    m = mw.calculate_medical_metrics(ref, hyp)
    rw, hw = mw._normalize(ref), mw._normalize(hyp)
    r = mw.canonicalize_medical_tokens(rw, side="reference")
    h = mw.canonicalize_medical_tokens(hw, side="hypothesis")
    occ, swaps, misses, i = {}, [], [], 0
    for op, ref_w, hyp_w in align(r, h):
        if op == "insertion":
            continue
        if ref_w and mw._is_medical(ref_w, terms) and mw.is_high_risk(mw._norm_key(ref_w)):
            ok = op == "correct"
            occ[i] = ok
            if not ok:
                rk, hk = mw._norm_key(ref_w), mw._norm_key(hyp_w) if hyp_w else ""
                other = hk and hk != rk and hk not in ("medication", "medicine") and (
                    mw.is_high_risk(hk) or (lookup(hk) and lookup(hk) != lookup(rk)))
                (swaps if other else misses).append(f"{ref_w} -> {hyp_w or '(dropped)'}")
        i += 1
    neg_total = neg_kept = 0
    j = 0
    ops = align(rw, hw)
    for op, ref_w, _ in ops:
        if op == "insertion":
            continue
        if ref_w in NEG and any(mw._is_medical(x, terms) for x in rw[j + 1:j + 5]):
            neg_total += 1
            neg_kept += op == "correct"
        j += 1
    errors = sum(1 for op, _, _ in ops if op != "correct")
    return {"ref_words": len(rw), "errors": errors,
            "med_ref": m["total_medical_ref_words"], "med_err": m["medical_substitutions"] + m["medical_deletions"],
            "drug_occ": occ, "drug_swaps": swaps, "drug_misses": misses,
            "neg_total": neg_total, "neg_kept": neg_kept}


def _edits(a: list[str], b: list[str]) -> int:
    return Levenshtein.distance(a, b)


def cpwer(ref_by_role: dict[str, list[str]], utterances: list[dict]) -> float | None:
    """Speaker-attributed WER: each hypothesis speaker's words, in time order, against the best
    one-to-one match with the doctor and patient references; extra speakers count as insertions."""
    if not utterances or not all(ref_by_role.values()):
        return None
    norm = lambda t: mw._normalize(t)  # noqa: E731
    by_spk: dict[str, list[str]] = {}
    for u in sorted(utterances, key=lambda u: u.get("start") or 0):
        by_spk.setdefault(str(u.get("speaker")), []).extend(norm(u.get("text") or ""))
    roles = list(ref_by_role)
    spks = list(by_spk) + [None] * max(0, len(roles) - len(by_spk))
    total_ref = sum(len(v) for v in ref_by_role.values())
    best = None
    for perm in itertools.permutations(spks, len(roles)):
        errs = sum(_edits(ref_by_role[r], by_spk.get(s, []) if s else []) for r, s in zip(roles, perm))
        errs += sum(len(by_spk[s]) for s in by_spk if s not in perm)
        best = errs if best is None else min(best, errs)
    return round(best / total_ref, 4) if total_ref else None


def parse_textgrid(text: str) -> str:
    out = []
    for m in re.finditer(r'text = "(.*?)"\s*$', text, flags=re.M):
        t = m.group(1).replace('""', '"').strip()
        if t:
            out.append(t)
    return " ".join(out)


def mcnemar(base: dict, other: dict) -> dict:
    keys = set(base) & set(other)
    b = sum(1 for k in keys if base[k] and not other[k])  # baseline right, other wrong
    c = sum(1 for k in keys if not base[k] and other[k])
    n = b + c
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)
    return {"baseline_right_other_wrong": b, "other_right_baseline_wrong": c, "p": round(p, 4)}


def summarise(per_file: dict[str, dict], cp: list[float] | None = None) -> dict:
    agg = {"ref_words": 0, "errors": 0, "med_ref": 0, "med_err": 0, "neg_total": 0, "neg_kept": 0}
    occ, swaps, misses = {}, [], []
    for name, s in per_file.items():
        for k in agg:
            agg[k] += s[k]
        occ.update({f"{name}:{i}": ok for i, ok in s["drug_occ"].items()})
        swaps += s["drug_swaps"]
        misses += s["drug_misses"]
    drug_err = sum(1 for ok in occ.values() if not ok)
    out = {
        "files": len(per_file),
        "wer": round(agg["errors"] / max(1, agg["ref_words"]), 4),
        "medical_term_error": round(agg["med_err"] / max(1, agg["med_ref"]), 4),
        "drug_errors": drug_err, "drug_mentions": len(occ),
        "drug_error_rate": round(drug_err / max(1, len(occ)), 4),
        "drug_for_drug_swaps": swaps, "other_drug_errors": misses[:40],
        "negations_before_medical_terms": agg["neg_total"],
        "negations_lost": agg["neg_total"] - agg["neg_kept"],
        "_occ": occ,
    }
    if cp:
        vals = [v for v in cp if v is not None]
        out["speaker_attributed_wer"] = round(sum(vals) / len(vals), 4) if vals else None
    return out


# ---------------------------------------------------------------- run

async def _get_text(c: httpx.AsyncClient, url: str) -> str | None:
    for _ in range(3):
        try:
            r = await c.get(url, timeout=30.0)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                return None
        except httpx.HTTPError:
            await asyncio.sleep(1)
    return None


async def _upload_one(c: httpx.AsyncClient, key: str, name: str, tmp: Path) -> str:
    """Download the consultation once from GitHub and upload it to AssemblyAI."""
    path = tmp / f"{name}.wav"
    async with c.stream("GET", AUDIO_URL.format(name=name), timeout=httpx.Timeout(30.0, read=120.0)) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            async for chunk in r.aiter_bytes(1 << 20):
                f.write(chunk)
    try:
        url, problem = await stt._upload(c, key, stt.hosts()["api"], path)
    finally:
        path.unlink(missing_ok=True)
    if not url:
        raise stt.SttError(f"AssemblyAI {problem}")
    return url


async def _transcribe(c: httpx.AsyncClient, key: str, upload_url: str, body: dict) -> dict:
    b = {**body, "audio_url": upload_url}
    data, warnings = await stt.create_and_poll(c, key, b, timeout_s=900)
    tid = data.get("id")
    if tid:
        await stt.delete_transcript(c, key, tid)
    return {"text": data.get("text") or "",
            "utterances": [{"speaker": u.get("speaker"), "text": u.get("text"), "start": u.get("start")}
                           for u in data.get("utterances") or []],
            "model": data.get("speech_model_used"), "domain": data.get("domain", b.get("domain")),
            "keyterms": len((data.get("_body") or b).get("keyterms_prompt") or []), "warnings": warnings}


async def run_all() -> dict:
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    key = config.settings.assemblyai_api_key
    configs = _configs()
    ours: dict[str, dict[str, dict]] = {k: {} for k in configs}
    errors: list[str] = []
    sem = asyncio.Semaphore(CONCURRENCY)
    tmp = Path(tempfile.mkdtemp(prefix="bench-"))
    t0 = time.monotonic()
    async with stt._client(httpx.Timeout(60.0, read=300.0, write=300.0)) as c:
        async def one(name: str) -> None:
            async with sem:
                try:
                    url = await _upload_one(c, key, name, tmp)
                    for cname, body in configs.items():
                        ours[cname][name] = await _transcribe(c, key, url, body)
                except Exception as exc:  # noqa: BLE001 - recorded per file
                    errors.append(f"{name}: {exc.__class__.__name__}: {exc}"[:300])

        await asyncio.gather(*(one(n) for n in FILES))
        transcribe_s = round(time.monotonic() - t0, 1)

        async def fetch(url: str):
            async with sem:
                return await _get_text(c, url)

        names = list(FILES)
        refs = await asyncio.gather(*(fetch(f"{OMI_RAW}/data/cleaned_transcripts/{n}_pure_text.txt") for n in names))
        grids = await asyncio.gather(*(fetch(TEXTGRID_URL.format(name=n, role=role)) for n in names for role in ("doctor", "patient")))
        published: dict[str, dict[str, str]] = {}
        for model in PUBLISHED:
            texts = await asyncio.gather(*(fetch(f"{OMI_RAW}/results/transcripts/{model}/{n}_conversation_transcript.txt") for n in names))
            published[model] = {n: t for n, t in zip(names, texts) if t is not None}

    ref = {n: t for n, t in zip(names, refs) if t}
    by_role = {}
    for idx, n in enumerate(names):
        d, p = grids[2 * idx], grids[2 * idx + 1]
        if d and p:
            by_role[n] = {"doctor": mw._normalize(parse_textgrid(d)), "patient": mw._normalize(parse_textgrid(p))}

    def score_everything() -> dict:
        lookup = _drug_lookup()
        models: dict[str, dict] = {}
        for model, texts in published.items():
            per = {n: score_file(ref[n], texts[n], lookup) for n in ref if n in texts}
            models[model] = {"source": "published transcript (Omi benchmark)", **summarise(per)}
        for cname, rows in ours.items():
            per = {n: score_file(ref[n], rows[n]["text"], lookup) for n in ref if n in rows}
            cp = [cpwer(by_role[n], rows[n]["utterances"]) for n in rows if n in by_role]
            any_row = next(iter(rows.values()), {})
            models[cname] = {"source": "run on this server", "model_used": any_row.get("model"),
                             "medical_mode": any_row.get("domain") == "medical-v1",
                             "keyterms": any_row.get("keyterms"), **summarise(per, cp)}
        base = models.get(BASELINE, {}).get("_occ") or {}
        for name, m in models.items():
            if name != BASELINE and base:
                m["vs_assemblyai_u3_pro"] = mcnemar(base, m["_occ"])
        ours_main = next(iter(ours), None)
        if ours_main and ours_main in models:
            o = models[ours_main]["_occ"]
            for name, m in models.items():
                if name != ours_main:
                    m["vs_ours"] = mcnemar(o, m["_occ"])
        for m in models.values():
            m.pop("_occ", None)
        return models

    models = await asyncio.to_thread(score_everything)
    result = {
        "status": "done", "build": config.build_id(), "started_at": started,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "transcribe_seconds": transcribe_s, "files": len(ref), "errors": errors,
        "dataset": "PriMock57 (CC BY 4.0, Papadopoulos Korfiatis et al., ACL 2022), single-stream audio and "
                   f"medical-term scorer from github.com/Omi-Health/medical-STT-eval@{OMI_COMMIT[:7]} (MIT)",
        "models": dict(sorted(models.items(), key=lambda kv: kv[1].get("drug_error_rate", 1))),
    }
    (config.settings.data_dir / RESULTS_NAME).write_text(json.dumps(result, indent=1))
    return result


def last_results() -> dict | None:
    p = config.settings.data_dir / RESULTS_NAME
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def status() -> dict:
    """The last results; starts a run in the background once per deploy."""
    global _task
    last = last_results()
    if _task is not None and not _task.done():
        return {"status": "running", "detail": "Transcribing 55 consultations twice; about 10 minutes."}
    if not config.settings.speech_ready:
        return {"status": "disabled", "detail": "No AssemblyAI key on the server."}
    if last and last.get("build") == config.build_id():
        return last
    _task = asyncio.get_running_loop().create_task(run_all())
    return {"status": "started", "detail": "Transcribing 55 PriMock57 consultations with our AssemblyAI set-ups; "
                                            "check back in about 10 minutes.", "previous": bool(last)}
