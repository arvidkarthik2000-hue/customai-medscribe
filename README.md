# CustomAi MedScribe (demo)

An ambient scribe for US clinics with medication safety, a Medicare formulary
lookup, firewalled sponsored placements and a metrics console for Constant Media.
Demo build: synthetic patients, prescribers, brands and ad delivery only.

| View | Path | For |
|---|---|---|
| Doctor | `/doctor/` | Schedule, pre-visit summary, live scribe, evidence-linked note, medicine checks, sign |
| Pharmacy tools | `/pharmacy/` | Interaction checker, formulary lookup, calculators |
| Network console | `/console/` | Constant Media: inventory, campaigns, delivery, pacing, verification |
| Sponsor report | `/report/` | A pharma brand team's read-only report (share links) |
| Self-test | `/selftest/` | Checks AssemblyAI, Claude, RxNav and openFDA after a deploy |

## How it works

- **Speech:** the browser streams 16 kHz audio to AssemblyAI (Universal-3.6 Pro
  Realtime, Medical Mode, speaker labels, the patient's medicines as key terms)
  with a short-lived token from our server. The same audio is recorded locally and
  sent for a final pass (Universal-3.5 Pro, Medical Mode, up to 1,000 key terms).
  Brand names the engine adds in brackets are removed.
- **Notes:** Claude Sonnet 5.5 (or Haiku 5.5) writes a structured note where every
  line cites the transcript turns behind it; a second pass grades each line, and
  code checks medicines, doses and numbers against the cited words.
- **Medicines:** RxNorm matching with a sound-alike guard, curated interaction,
  allergy, condition, dose, renal, duplicate and older-adult rules, FDA label text
  via openFDA, and formulary status. The clinician confirms every medicine before
  signing; high-risk medicines also need the dose confirmed.
- **Two zones:** the clinical zone (`/api`) and the ad zone (`/adzone`) are
  separate apps with separate databases. The ad zone receives only the slot, a
  prescriber id, the practice's declared specialty and state, and the hour. Ads
  never appear on screens with a chart, transcript, note, instructions or
  decision support. The browser loads no third-party code (strict CSP).

## Deploy on Render (about 10 minutes)

1. Render dashboard → **New → Blueprint** → connect GitHub if asked → choose
   `arvidkarthik2000-hue/customai-medscribe`. Render reads `render.yaml`.
2. Render asks for two values; paste them there, never in chat or the repo:
   - `ASSEMBLYAI_API_KEY`: from an AssemblyAI account with a card added.
   - `ANTHROPIC_API_KEY`: from a Console workspace with a monthly spend limit.

   The demo access code is set in `render.yaml` (`DEMO_ACCESS_CODE`); change it
   there and push.
3. **Apply.** The first build takes a few minutes. The service gets a 1 GB disk
   for its SQLite files, so data survives restarts.
4. Open the service URL, enter the access code, then go to `/selftest/` and
   press **Run self-test**. Every line should pass.
5. Send Constant Media the URL, the access code and `docs/TESTER_GUIDE.md`.

Every push to `main` redeploys automatically. Change keys or the access code in
the service's **Environment** tab.

### Limits that protect the API bills

`MAX_VISIT_MINUTES` (20), `DAILY_AUDIO_MINUTES` (240), `DAILY_NOTE_RUNS` (150) and
`MAX_UPLOAD_MB` (60) can be changed in the Environment tab.

Optional: add a free `OPENFDA_API_KEY` (from open.fda.gov) in the Environment tab.
Without one, openFDA allows 1,000 requests a day per IP address, and Render's
outbound addresses may be shared.

### Between testers

*Reset today* on the doctor's schedule archives the day's visits, and *Reset test
caps* on the console's Overview clears the demo account's frequency caps.

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in your keys in your editor
set -a && . ./.env && set +a
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000. The microphone works on localhost without HTTPS.
Without keys, the scripted sample visits still run end to end with saved notes.

## Tests

```bash
pytest                         # offline unit and API tests
python3 tests/e2e/run_e2e.py   # browser walkthrough with a fake mic and a fake AssemblyAI
```

## Live checks still to run

This workspace could not reach AssemblyAI, RxNav, openFDA or CMS, so those
clients were built from their documentation and tested with fakes. After the
Render deploy, `/selftest/` exercises each one. `docs/HANDOVER.md` describes a
fuller live check for a Claude Code session with internet access: voiced test
visits, saved real responses as test fixtures, and an optional switch from the
sample formulary to real Medicare Part D plan data.

## Production gaps (not in this demo)

A HIPAA-ready host and BAAs (AssemblyAI, Anthropic, hosting), a licensed drug
database, real Part D and commercial formulary data through an e-prescribing
partner, single sign-on and per-office accounts, US healthcare counsel review of
the ad model, and third-party audit of ad measurement.
