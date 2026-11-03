# Handover: live checks for a Claude Code session with internet access

The demo was built in a workspace that could reach only PyPI, npm, GitHub and
the Claude API, and had no API keys. So AssemblyAI, RxNav, openFDA and CMS were
written against their documentation and tested with fakes. This handover is for
a Claude Code session that *can* reach them.

## Where to run it

Pick one:

- **Claude Code on the web (recommended).** Create a cloud environment with
  **Network access: Full** (or Custom with the hosts below), then start a session
  on this repo in that environment.
- **Claude Code Desktop on your PC**, in a Local session on a clone of this repo.
  Your disk only needs about 1 GB free (2 GB for the formulary step).

Hosts the session needs: `api.assemblyai.com`, `token.assemblyai.com`,
`streaming.assemblyai.com`, `api.anthropic.com`, `rxnav.nlm.nih.gov`,
`api.fda.gov`, `download.open.fda.gov`, `dailymed.nlm.nih.gov`, `data.cms.gov`,
`api.elevenlabs.io` (optional, for synthetic test audio).

## Keys

Add these as environment variables on that environment (cloud), or in a `.env`
file the agent never opens (local). Use spend-capped keys, and remove them when
you are done, since anything in the environment can be read by the session.

| Variable | Needed for |
|---|---|
| `ASSEMBLYAI_API_KEY` | live and final-pass transcription |
| `MEDSCRIBE_ANTHROPIC_KEY` | Claude notes (the app reads this name too; don't use `ANTHROPIC_API_KEY` in a Claude Code environment) |
| `DEMO_ACCESS_CODE` | any value, to sign in locally |
| `ELEVENLABS_API_KEY` | optional: voice the five sample scripts as test audio |
| `OPENFDA_API_KEY` | optional: raises openFDA's daily limit |

## Kickoff prompt

```
Run the live checks in docs/HANDOVER.md, steps 1 to 4 (step 5 only if I say so).
Work on a new branch named live-checks and open a pull request at the end.
Never open, print or paste secrets; read keys only from environment variables.
Synthetic data only. Stop and report if a step's pass check fails.
```

## Steps

1. **Set up.** `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && pytest`.
   *Pass:* all tests pass.
2. **Self-test.** Start the app (`uvicorn app.main:app --port 8000`), sign in with
   `curl -c cj -X POST localhost:8000/api/session -H 'content-type: application/json' -d "{\"code\":\"$DEMO_ACCESS_CODE\"}"`,
   then `curl -b cj localhost:8000/api/selftest`.
   *Pass:* token mint, the Medical Mode transcription, the Claude note, RxNav and
   openFDA all pass. If one fails, fix the client against the current docs and
   add a test.
3. **Voiced sample visits (needs ElevenLabs).** Voice each script in
   `app/clinical/data/samples/` with two or three distinct US voices (clinician,
   patient, caregiver), as one audio file per visit. Push each file through the
   real pipeline (`POST /api/visits`, `/consent`, `/audio`, poll `/status`). Score
   it against the script:
   - drug-name recall
   - wrong-drug matches
   - dose and frequency accuracy
   - speaker roles
   - unsupported lines
   - time from upload to ready

   Write the results to `docs/EVAL.md`.
   *Pass:* zero wrong-drug matches and drug recall of at least 95% on all five.
   If not, report which terms failed and why.
4. **Real-response fixtures.** Save sanitised real responses under
   `tests/fixtures/live/`:
   - one AssemblyAI transcript
   - one streaming session's messages
   - one Claude note and one verifier reply
   - RxNav `approximateTerm` for 10 drugs
   - openFDA labels for 5 drugs

   Then add tests that parse each one through the real client code.
   *Pass:* `pytest` passes with them.
5. **Optional: real Medicare Part D formulary.**
   1. Read the CMS Part D public use file's terms of use first, and stop if they
      bar this use.
   2. Download the latest quarterly "Prescription Drug Plan Formulary, Pharmacy
      Network, and Pricing Information" file from data.cms.gov.
   3. Extract two real plans covering New Jersey (one standalone PDP, one MA-PD),
      keeping only the drugs in `app/drugs/kb/drugs.json` (match by RXCUI).
   4. Write `app/drugs/kb/formulary_partd.json` in the same structure as the
      sample formulary, with the quarter and the source URL.
   5. Change the plan names, the formulary label and the README to the real
      source. Keep the sample as a fallback.

   *Pass:* `/api/formulary/lookup` returns real tiers for both plans, and the UI
   says which quarter.

## Already known

- The app runs one uvicorn worker on purpose: the pipeline recovers in-flight
  visits at start-up.
- Medical Mode adds brand names in brackets ("Lispro (Humalog)"); the app strips
  them and records the removal. Keep that behaviour.
- Ads must never appear on a clinical screen, and the ad zone accepts only five
  fields. Tests enforce both; keep them passing.
