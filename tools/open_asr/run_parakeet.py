"""Transcribe the five voiced sample visits with Parakeet-TDT-0.6B-v2 (int8, sherpa-onnx, CPU).

Runs plain greedy decoding, then (if the runtime supports it for this model) beam search
with the same drug keyterms the AssemblyAI final pass gets, as hotwords.

usage: python run_parakeet.py <audio_dir> <samples.json> <model_dir> <out.json> [--hotwords-only]

Setup (a separate venv; nothing here is imported by the app):
  python3 -m venv asr-venv && asr-venv/bin/pip install sherpa-onnx numpy
  curl -L -o m.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2
  tar -xjf m.tar.bz2
samples.json comes from export_samples.py; score with score.py.
"""
import json
import re
import subprocess
import sys
import time

import numpy as np
import sherpa_onnx

audio_dir, samples_path, model_dir, out_path = sys.argv[1:5]
samples = json.load(open(samples_path))


def load(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def recognizer(**extra):
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=f"{model_dir}/encoder.int8.onnx", decoder=f"{model_dir}/decoder.int8.onnx",
        joiner=f"{model_dir}/joiner.int8.onnx", tokens=f"{model_dir}/tokens.txt",
        num_threads=2, model_type="nemo_transducer", **extra)


def decode(rec, audio: np.ndarray, hotwords: str | None = None) -> tuple[str, float]:
    t0 = time.time()
    st = rec.create_stream(hotwords) if hotwords is not None else rec.create_stream()
    st.accept_waveform(16000, audio)
    rec.decode_stream(st)
    return st.result.text.strip(), time.time() - t0


# Hotwords need a BPE vocabulary file; sherpa's Parakeet export ships only tokens.txt, so one is
# written from it (score = -id approximates the merge order; NeMo's own boosting may do better).
vocab_path = f"{model_dir}/bpe.vocab"
try:
    open(vocab_path).close()
except OSError:
    with open(vocab_path, "w", encoding="utf-8") as f:
        for line in open(f"{model_dir}/tokens.txt", encoding="utf-8"):
            if line.strip():
                tok, idx = line.rstrip("\n").rsplit(" ", 1)
                f.write(f"{tok}\t{-float(idx)}\n")


results = {"model": "parakeet-tdt-0.6b-v2 int8 (sherpa-onnx, CPU, 2 threads)", "rows": []}
ONLY_HOTWORDS = len(sys.argv) > 5 and sys.argv[5] == "--hotwords-only"
greedy = None if ONLY_HOTWORDS else recognizer(decoding_method="greedy_search")
try:
    beam = recognizer(decoding_method="modified_beam_search", max_active_paths=4, hotwords_score=2.0,
                      modeling_unit="bpe", bpe_vocab=f"{model_dir}/bpe.vocab")
    results["hotwords"] = "modified_beam_search with per-visit hotwords"
except Exception as exc:  # noqa: BLE001
    beam = None
    results["hotwords"] = f"not supported: {exc}"

for s in samples:
    audio = load(f"{audio_dir}/{s['id']}.mp3")
    seconds = len(audio) / 16000
    row = {"id": s["id"], "audio_s": round(seconds, 1)}
    if greedy is not None:
        text, took = decode(greedy, audio)
        row.update({"greedy": text, "greedy_s": round(took, 1)})
    if beam is not None:
        hw = [k.lower() for k in s["keyterms_final"] if re.fullmatch(r"[a-z0-9 .'-]+", k.lower())]
        try:
            text_b, took_b = decode(beam, audio, "/".join(hw))
            row.update({"hotwords": text_b, "hotwords_s": round(took_b, 1), "hotword_count": len(hw)})
        except Exception as exc:  # noqa: BLE001
            row["hotwords_error"] = str(exc)
    results["rows"].append(row)
    print(s["id"], row["audio_s"], "s audio;", row.get("greedy_s"), "s greedy;", row.get("hotwords_s"), "s hotwords", flush=True)

json.dump(results, open(out_path, "w"), indent=1)
