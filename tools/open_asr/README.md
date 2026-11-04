# Open ASR vs AssemblyAI on the voiced sample visits

A head-to-head on the same five voiced visits the server's accuracy run uses
(`/api/eval/public`), scored the same way: transcript WER against the script, and
whether each medicine in the hand-checked note appears in the transcript.

1. `PYTHONPATH=. DRUGS_OFFLINE=1 .venv/bin/python tools/open_asr/export_samples.py samples.json`
2. In a separate venv with sherpa-onnx (see the header of `run_parakeet.py`):
   `python run_parakeet.py app/clinical/data/eval_audio samples.json <model_dir> results.json`
3. `PYTHONPATH=. DRUGS_OFFLINE=1 .venv/bin/python tools/open_asr/score.py samples.json results.json`

Result on 4 Nov 2026 (Parakeet-TDT-0.6B-v2, int8, CPU; synthetic voices, 5 visits):

| | WER | Drug names heard |
|---|---|---|
| AssemblyAI Universal-3.5 Pro, Medical Mode + keyterms | 2.6% | 32/32 |
| Parakeet, plain | 3.8% | 14/32 |
| Parakeet, same ~560 drug terms as hotwords | 5.0% | 27/32 |

Misses included metoprolol heard as "metapril" / "medaprilol" and hydralazine as
"hydroxy..." (its sound-alike, hydroxyzine). Caveats: int8 CPU build, approximate
hotword vocabulary, clean synthetic voices, five visits.
