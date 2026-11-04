"""Score Parakeet transcripts the way the server's accuracy run scores AssemblyAI's.

usage (app venv, repo root): PYTHONPATH=. DRUGS_OFFLINE=1 python tools/open_asr/score.py <samples.json> <results.json>
"""
import json
import sys

from app.clinical.evaluate import _words, wer

samples = {s["id"]: s for s in json.load(open(sys.argv[1]))}
res = json.load(open(sys.argv[2]))
print(res["model"], "|", res.get("hotwords"))
totals = {}
for row in res["rows"]:
    s = samples[row["id"]]
    for mode in ("greedy", "hotwords"):
        text = row.get(mode)
        if text is None:
            continue
        words = " " + " ".join(_words(text)) + " "
        heard, missed = 0, []
        for m in s["meds"]:
            spoken = m.get("as_spoken") or m.get("name") or ""
            names = {n for n in (" ".join(_words(spoken)), " ".join(_words(m.get("name") or ""))) if n}
            if any(f" {n} " in words for n in names):
                heard += 1
            else:
                missed.append(spoken)
        w = wer(s["script"], text)
        t = totals.setdefault(mode, {"heard": 0, "of": 0, "wer": [], "secs": 0.0, "audio": 0.0})
        t["heard"] += heard
        t["of"] += len(s["meds"])
        t["wer"].append(w)
        t["secs"] += row.get(f"{mode}_s", 0)
        t["audio"] += row["audio_s"]
        print(f"{row['id']:24s} {mode:8s} WER {w:.3f}  drugs {heard}/{len(s['meds'])}  missed: {missed}")
for mode, t in totals.items():
    print(f"TOTAL {mode}: mean WER {sum(t['wer']) / len(t['wer']):.4f}, drugs heard {t['heard']}/{t['of']}, "
          f"{t['audio'] / max(t['secs'], 0.01):.1f}x real time on 2 CPU threads")
