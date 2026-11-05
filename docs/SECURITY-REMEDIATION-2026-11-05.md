# Security remediation and pilot acceptance — 5 November 2026

## Implemented

Clinical routes now require the doctor role. Empty/unconfigured demo codes fail;
the shared code grants doctor access only, with separate optional role credentials.
Sessions cannot promote themselves to another role unless the explicit synthetic
walkthrough switch `MEDSCRIBE_ALLOW_ROLE_SWITCH=1` is enabled. Startup accepts only
`MEDSCRIBE_MODE=demo` and `MEDSCRIBE_DATA_CLASSIFICATION=synthetic`.

The complete 271-test suite passed after the authentication/clinical changes; the Python scan
reports no advisories. Sponsor access to actual clinical routes was tested and denied.
Set unique credentials in the secret store; the example contains none. Keep role
switching disabled. Live clinical providers were not exercised.

## Clinical pilot boundary

This remains a synthetic demonstration. These changes do not establish named clinical
identity, practice/patient isolation, clinical authorization, consent, audit/retention
or medical accuracy. Build those boundaries and review advertising/clinical separation
before any patient data. Clinicians must assess transcription, note omissions and
medication-check errors against a reviewed reference set; a successful API response
is not a clinical validation.

Changes are prepared locally on `codex/audit-remediation-2026-11-05`.
No production deployment or customer-data migration was performed.
