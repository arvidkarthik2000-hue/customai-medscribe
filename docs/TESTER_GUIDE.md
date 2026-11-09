# CustomAi MedScribe: tester guide

This is a demo with synthetic patients, prescribers, brands and ad delivery.
**Do not use real patient information.** Role-play only, with everyone present
agreeing to be recorded.

## Open it

1. Open the link and enter the access code you were given.
2. Pick a view. You can switch at any time from the bar at the top:
   - **Doctor**: the scribe as a clinician sees it.
   - **Pharmacy tools**: interaction checker, formulary lookup, calculators.
   - **Network console**: Constant Media's view of inventory, campaigns and delivery.
   - **Sponsor report**: what a pharma brand team receives.

Use Chrome, Edge or Safari on a laptop, and allow the microphone when asked.

**On a wallboard, kiosk or other shared screen,** open `/check/` on the demo
link first (for example `https://customai-medscribe.onrender.com/check/`). It
needs no access code and tests, in that screen's browser, everything the demo
relies on: an up-to-date browser, the connection, sign-in, live captions and
the microphone. If a row is red, send a photo of the page to your CustomAi
contact. Screens without a microphone can still run every view; use *Replay a
scripted visit* instead of recording. If the screen's player only allows one
address, allow every page on the demo's address.

**Players that open the demo inside an iframe overlay** run every view as is.
To record a visit from such a screen, the player has to pass the microphone
through:

- the iframe needs `allow="microphone"`, for example
  `<iframe src="https://customai-medscribe.onrender.com/" allow="microphone">`;
- on an Android player, the app needs the `RECORD_AUDIO` permission (granted on
  the device), and its `WebChromeClient.onPermissionRequest` must grant
  `PermissionRequest.RESOURCE_AUDIO_CAPTURE` for the demo's address. Without
  that, the web view refuses every microphone request;
- the screen needs a microphone, built in or USB.

The screen check shows which of these is missing.

## A 10-minute walkthrough

1. **Doctor → Today's schedule.** Note the one sponsored card beside the
   schedule. It is chosen only by the practice's declared specialty and state;
   change them under *Practice profile* and the card changes.
2. **Open a patient.** The pre-visit summary shows problems, medicines,
   allergies, labs and care gaps. The sponsored card is gone: nothing sponsored
   appears on any screen with patient data.
3. **Record a visit.** Click *Start recording*, confirm consent, and role-play a
   short visit (2–5 minutes). Mention medicines, doses and an allergy. The live
   transcript appears as you speak. Or click *Replay a scripted visit* to run a
   prepared visit without a microphone.
4. **End the visit.** A final, more accurate transcription runs, then the note
   is drafted, checked line by line, and every medicine is checked.
5. **Review.** Click any blue evidence chip to see the exact words behind a
   line. Lines the checker could not find in the transcript are highlighted.
   Confirm each medicine (high-risk ones need the dose confirmed too), read the
   safety alerts, acknowledge critical ones with a reason, then *Sign note*.
6. **Network console.** The impressions you just generated appear within
   seconds as *test traffic*, kept out of billable totals.
7. **Sponsor report.** Open a campaign's read-only report: delivered vs
   booked, viewable impressions, prescribers reached, frequency, CPM and spend.

## Good to know

- Every brand and ad is fictional and marked "Sample ad, not a real product".
- Console and report numbers are simulated across a fictional network of about
  500 offices, apart from your own test impressions.
- Each visit is limited to 20 minutes, and the link has a daily limit on live
  audio so it can stay open for testing.
- Formulary answers come from CMS's Medicare Part D public use file for two New
  Jersey plans (one standalone drug plan, one Medicare Advantage plan). The
  screen names the release. Coverage changes during the year, so check a real
  prescription against the plan itself.
- This is not a medical device. The clinician reviews and signs every note.
