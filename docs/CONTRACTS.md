# Build contracts

Four builders work in parallel on one repo. This file is the source of truth for
who owns what and how the parts talk. If you need a change outside your area,
say so in your final report; do not edit another builder's files.

## Ownership

| Area | Owner | Files |
|---|---|---|
| Skeleton, shared UI, deploy | lead | `app/main.py`, `app/config.py`, `app/auth.py`, `app/storage.py`, `static/index.html`, `static/css/app.css`, `static/css/landing.css`, `static/js/common.js`, `static/js/landing.js`, `render.yaml`, `requirements.txt`, `README.md`, `docs/CONTRACTS.md` |
| Doctor app UI | lead | `static/doctor/**` |
| Clinical backend | clinical | `app/clinical/**`, `tests/test_clinical*.py` |
| Drug safety + formulary + pharmacy UI | drugs | `app/drugs/**`, `static/pharmacy/**`, `tests/test_drugs*.py` |
| Ad zone + console + sponsor report | ads | `app/ads/**`, `static/console/**`, `static/report/**`, `static/ads/**`, `tests/test_ads*.py` |

New Python dependencies: add them to your final report (the lead edits
`requirements.txt`). Prefer the standard library, `httpx` and `pydantic`.

## Global rules (all builders)

1. **No third-party code or requests from the browser.** CSP is `script-src 'self'`:
   no inline `<script>` blocks, no `onclick=""` attributes, no CDNs. Vendor any
   library into `static/vendor/` (npm registry is reachable from the build box).
   The only external browser connection allowed is the AssemblyAI WebSocket.
2. **Use the design system** in `static/css/app.css` (tokens, `.card`, `.btn`,
   `.badge-*`, `.table`, `.cds-*`, `.banner-*`, `.ad-unit`). Page-specific CSS goes
   in your own folder. Every page calls `renderHeader(role, {...})` from
   `/js/common.js` and `requireSession()`.
3. **Synthetic data only.** Fictional patients, prescribers (NPIs start with `9`),
   practices and brands. Every console/report screen shows the simulated strip.
4. **Secrets** come only from `app.config.settings`. Never log or return them.
5. **Graceful degradation.** If AssemblyAI, Claude, RxNav or openFDA is not
   configured or not reachable, the UI says so plainly and the rest still works.
   External calls have timeouts (≤ 8 s each for RxNav/openFDA) and are cached.
6. **Never say "safe".** An empty interaction result reads "No interaction found in
   these sources" followed by the sources checked.
7. The build box can only reach PyPI, npm, GitHub and api.anthropic.com. RxNav,
   openFDA, CMS and AssemblyAI are reachable from Render, not from here, so write
   their clients against the documented shapes and test with recorded fixtures.
8. Tests: `pytest -q` from the repo root must pass offline. Use FastAPI's
   `TestClient`; mock outbound HTTP.

## Session and prescriber

`GET /api/me` → `{role, prescriber: {id, name, npi, practice, specialty, state, is_test}, speech_ready, notes_ready}`.
The demo prescriber id is `DEMO-001` (test account). Specialty values:
`family_medicine | internal_medicine | cardiology | endocrinology | pulmonology`.
States: two-letter codes. Auth dependency: `app.auth.require` (any signed-in role).

## Clinical API (owner: clinical; consumer: doctor UI)

All JSON. Times are ISO 8601 UTC strings. Transcript turn:
`{id: "t1", speaker: "A", role: "clinician|patient|caregiver|other|unknown", text, start_ms, end_ms}`.

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/schedule` | – | `{date, appointments: [{appt_id, time, patient_id, name, age, sex, reason, visit_type, status: "scheduled|in_progress|ready_to_sign|signed", visit_id}]}` |
| `GET /api/patients/{patient_id}` | – | chart: `{id, name, dob, age, sex, mrn, insurance: {type: "medicare_part_d|commercial|medicaid", plan_key, plan_name, member_id}, problems: [{name, icd10, since}], medications: [{name, dose, unit, route, frequency, indication}], allergies: [{substance, reaction, severity}], vitals: {...}, labs: [{name, value, unit, date, flag}], egfr, last_visit: {date, summary}, care_gaps: [{name, detail, source}], social: {...}}` |
| `GET /api/samples` | – | `{samples: [{id, title, patient_id, minutes, highlights: [str]}]}` |
| `POST /api/visits` | `{patient_id, template: "soap|progress|hp|consult", style: "concise|detailed", engine: "sonnet|haiku", specialty?}` | `{visit_id}` |
| `POST /api/visits/{id}/consent` | `{consented: true, attestation: "roleplay_no_real_patient"}` | `{ok, consent_at}` (logged in the audit table; never written into the note) |
| `GET /api/visits/{id}/keyterms` | – | `{live: [≤100 str], final: [≤1000 str]}` |
| `POST /api/stt/token` | `{visit_id}` | `{token, ws_url, expires_in}` where `ws_url` already holds every query param except `token` (browser appends `&token=`). 409 if daily audio cap reached; 503 if speech not configured |
| `POST /api/visits/{id}/live-turns` | `{turns: [turn...], audio_seconds}` | `{ok}` (persist live transcript; called every ~10 s and at stop) |
| `POST /api/visits/{id}/audio` | multipart `file` (webm/ogg/mp4/wav/mp3) | `{ok, bytes}` then the pipeline starts with a final pass |
| `POST /api/visits/{id}/process` | `{source: "final|live|sample", sample_id?}` | `{ok}` starts the pipeline (async). `final` requires uploaded audio; `live` uses the live turns; `sample` loads a sample transcript |
| `GET /api/visits/{id}/status` | – | `{stage: "queued|final_transcript|note|verify|meds|done|error", stages: [{id, label, state: "pending|active|done|skipped|error", detail}], error}` |
| `GET /api/visits/{id}` | – | full visit (below) |
| `PATCH /api/visits/{id}/note` | `{item_id, text}` or `{section_id, items}` | updated visit (audit logged) |
| `POST /api/visits/{id}/ask` | `{section_id, instruction}` | `{section}` rewritten from the transcript only |
| `POST /api/visits/{id}/meds/{med_id}/confirm` | `{confirmed: bool, dose_confirmed?: bool, corrected?: {name?, dose?, unit?, frequency?}}` | updated `medication_check` |
| `POST /api/visits/{id}/cards/{card_id}/ack` | `{reason}` | `{ok}` (override/acknowledge, audit logged) |
| `POST /api/visits/{id}/sign` | – | `{ok, signed_at}` or 409 `{detail, blockers: [str]}` |
| `GET /api/visits/{id}/export.txt` | – | plain-text note |
| `GET /api/health`, `GET /api/selftest` | – | dependency checks (selftest needs a session; rate-limited) |

Full visit object:

```json
{
  "id": "v_...", "patient_id": "p1", "status": "draft|processing|ready|signed|error",
  "template": "soap", "style": "concise", "engine": "sonnet", "specialty": "family_medicine",
  "created_at": "...", "consent_at": "...", "signed_at": null,
  "source": "final|live|sample", "audio_seconds": 312,
  "transcript": [turn...],
  "speaker_roles": {"A": "clinician", "B": "patient"},
  "note": {
    "visit_summary": "...",
    "sections": [{"id": "hpi", "title": "History of present illness",
                  "items": [{"id": "hpi-1", "text": "...", "evidence": ["t3", "t4"],
                             "status": "documented|negative|not_discussed",
                             "verdict": "supported|partial|unsupported|contradicted|unchecked",
                             "verdict_reason": "...", "edited": false}]}],
    "medications": [{"id": "m1", "name": "lisinopril", "as_spoken": "...", "dose": "20", "unit": "mg",
                     "route": "oral", "frequency": "once daily", "duration": "", "action": "continue|start|stop|change|hold|discussed",
                     "indication": "", "evidence": ["t12"]}],
    "allergies": [{"substance": "...", "reaction": "...", "evidence": []}],
    "orders": [{"type": "lab|imaging|referral|procedure|vaccine|other", "description": "...", "evidence": []}],
    "diagnoses": [{"description": "...", "icd10": "I10", "icd10_valid": true, "icd10_title": "...", "evidence": []}],
    "follow_up": {"text": "...", "evidence": []},
    "patient_instructions": "plain language, about 8th-grade level",
    "not_discussed": ["..."],
    "uncertain": [{"text": "...", "evidence": []}],
    "removed_brands": ["Humalog"]
  },
  "medication_check": { "...": "see Drugs contract: check_visit() output" },
  "cards_ack": {"card_id": {"reason": "...", "at": "..."}},
  "blockers": ["Confirm 2 medicines", "Resolve 1 unsupported line"],
  "timings": {"final_transcript_s": 12.1, "note_s": 18.4, "verify_s": 6.0, "meds_s": 2.2},
  "engine_info": {"live_model": "universal-3-6-pro", "final_model": "universal-3-5-pro", "note_model": "claude-sonnet-5-5"}
}
```

Sign blockers: every medicine row confirmed (high-risk rows also need
`dose_confirmed`), every `unsupported`/`contradicted` line edited or removed, every
`critical` card acknowledged with a reason.

## Drugs contract (owner: drugs; consumers: clinical backend, pharmacy UI)

Python, importable without network:

```python
from app.drugs.engine import check_visit, strip_added_brands

check_visit(medications: list[dict], patient: dict, *, timeout_s: float = 8.0) -> dict
# medications: note medication rows (shape above). patient: the chart dict from
# GET /api/patients/{id} (age, sex, egfr, problems, allergies, medications,
# insurance.plan_key, pregnant optional). Visit medicines are checked against each
# other AND against the chart's current medicines.
# Returns:
# {
#   "medications": [{...input row, "match": {"status": "matched|ambiguous|unmatched",
#        "rxcui": "29046"|None, "ingredient": "lisinopril", "display": "lisinopril 20 MG Oral Tablet"|None,
#        "candidates": [{"rxcui", "name", "score"}], "sound_alike": ["..."], "source": "RxNorm|local"},
#      "high_risk": bool, "high_risk_reason": "anticoagulant|insulin|opioid|...",
#      "schedule": "CII|CIII|CIV|CV|None", "formulary": {...formulary_status...},
#      "dose_check": {"status": "ok|above_max|below_usual|unknown", "detail", "source"},
#      "confirmed": false, "dose_confirmed": false}],
#   "cards": [{"id": "stable-hash", "indicator": "critical|warning|info",
#      "category": "interaction|allergy|condition|dose|duplicate|renal|older_adult|qt|formulary|prevention|calculator",
#      "summary": "short line", "detail": "what happens / what to do",
#      "meds": ["warfarin", "ibuprofen"],
#      "source": {"label": "FDA label: warfarin, section 7", "url": "https://...", "date": "2026-..."},
#      "override_reasons": ["Benefit outweighs risk", "Will monitor", "Patient already tolerating", "Other"]}],
#   "sources_checked": [{"name": "RxNorm", "status": "ok|unavailable", "detail"}],
#   "checked_at": "ISO"
# }

strip_added_brands(text: str) -> tuple[str, list[str]]
# Removes brand names AssemblyAI Medical Mode adds in parentheses after a generic,
# e.g. "Lispro (Humalog)" -> "Lispro", returns the removed brands.

brand_and_generic_terms(specialty: str) -> list[str]
# Common drug names for the specialty, for speech keyterms (generic names first).
```

HTTP routes (prefix `/api`): `POST /drugs/check` `{medications: [{name, dose?, unit?, frequency?}], patient?: {age, sex, egfr, allergies, problems, plan_key}}` → same shape as `check_visit`;
`GET /drugs/search?q=` → `{results: [{rxcui, name, tty}]}`;
`GET /formulary/plans` → `{plans: [{plan_key, name, type, contract_id, plan_id, year, source}]}`;
`GET /formulary/lookup?plan_key=&drug=` → `{plan, drug, status: {on_formulary, tier, tier_label, prior_auth, step_therapy, quantity_limit, ql_detail}, alternatives: [...], source}`.

Plan keys used by the patient fixtures: `PDP-A` (a standalone Part D plan),
`MAPD-B` (a Medicare Advantage plan with drug coverage), `COMMERCIAL` (not public:
say "Commercial formulary not available in the demo; production uses real-time
benefit checks through an e-prescribing partner").

## Ad zone (owner: ads; consumers: doctor UI)

Browser SDK `static/ads/slot.js` (ES module):

```js
import { mountSlot, unmountAllSlots, openResourcesTray } from "/ads/slot.js";
const handle = await mountSlot(containerEl, { slot: "schedule_card" | "drafting_wait" | "post_sign" ,
                                              prescriberId, specialty, state });
handle.unmount();          // when leaving the screen (records time in view)
unmountAllSlots();         // safety net before any clinical screen renders
openResourcesTray({ prescriberId, specialty, state });   // doctor-initiated drawer
```

The SDK sends only `{slot, prescriber_id, specialty, state, hour}` to the ad zone
(`/adzone/serve`), renders the unit with "Sponsored", "Sample ad, not a real
product", a prescribing-information link, "Why am I seeing this?" and Hide, and
reports `rendered`, `viewable` (≥50% in view for 1 s, tab visible), `click`,
`isi_open`, `hide` events to `/adzone/events`. If nothing is eligible it renders
nothing (no empty box). The doctor UI calls `unmountAllSlots()` before rendering
any chart, recording, review, pharmacy or CDS screen.

Console page `/console/`, sponsor report `/report/` (list) and
`/report/?share=<token>` (read-only, works without the access code).
