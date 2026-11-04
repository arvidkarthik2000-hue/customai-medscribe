"""Write samples.json for the open-ASR comparison: each voiced sample visit's script, its
hand-checked medicines, and the same keyterms the AssemblyAI final pass gets.

usage (app venv, repo root): PYTHONPATH=. DRUGS_OFFLINE=1 python tools/open_asr/export_samples.py samples.json
"""
import json
import sys

from app.clinical import fixtures, stt

out = []
for s in fixtures.samples():
    chart = fixtures.patient(s["patient_id"]) or {}
    out.append({"id": s["id"], "script": " ".join(t["text"] for t in s["turns"]),
                "meds": [{"name": m.get("name"), "as_spoken": m.get("as_spoken")} for m in s["saved_note"].get("medications") or []],
                "keyterms_final": stt.build_keyterms(fixtures.keyterm_seed(chart), "family_medicine")["final"]})
json.dump(out, open(sys.argv[1], "w"), indent=1)
print(f"wrote {len(out)} samples to {sys.argv[1]}")
