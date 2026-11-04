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
   - `ASSEMBLYAI_API_KEY`: the API key from the AssemblyAI dashboard's API Keys
     page (32 characters), from an account with a card added. Not a temporary
     token: a token cannot mint the live-stream tokens the browser needs, and it
     expires.
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

### Speech routing

`ASSEMBLYAI_REGION=us` mints live tokens in AssemblyAI's US data zone. If the
account can't use the zone, the app falls back to edge routing (nearest region,
US or EU) and the self-test says so; set `ASSEMBLYAI_STRICT_ZONE=1` to forbid
that. If AssemblyAI ever refuses an upload, it fetches the recording from a
one-time link on this server (`RENDER_EXTERNAL_URL`, or `PUBLIC_BASE_URL`),
which closes when the final pass ends.

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

## Live checks

`/selftest/` checks every live service on the deployed server: the AssemblyAI
live token and final pass (sent the way a real recording is), the Claude note
and verifier, RxNav, openFDA and the Medicare Part D data. Results are cached
for an hour, and each deploy gets a fresh run. Anyone can read them at
`/api/selftest/public`, which shows no secrets.

`/api/eval/public` runs the five sample visits as voiced audio (synthetic
text-to-speech voices) through the whole pipeline and scores each against its
script and hand-checked note: transcript word error rate, drug names heard,
medicines and doses in the note, speakers, flagged lines, and processing time.
It runs at most every 6 hours, and once after each deploy. Synthetic voices are
cleaner than clinic audio, so treat its numbers as an upper bound.

The formulary comes from CMS's quarterly Part D public use file (the newest
release for the current contract year, found through data.cms.gov's catalog
and checked weekly). Two New Jersey plans are loaded: a standalone PDP and an
MA-PD. Until that file has loaded, the screens say they show sample data.

## Production gaps (not in this demo)

A HIPAA-ready host and BAAs (AssemblyAI, Anthropic, hosting), a licensed drug
database, real Part D and commercial formulary data through an e-prescribing
partner, single sign-on and per-office accounts, US healthcare counsel review of
the ad model, and third-party audit of ad measurement.
