// Record with a phone: for screens that can't use a microphone themselves, such as wallboard
// players that show the demo in an iframe overlay. This screen shows a QR code. The doctor's
// phone records and streams, and this screen shows the live transcript, then moves on to the
// note. A clinical screen: no sponsored units.
import { api, el, mount, fmt } from "/js/common.js";

export async function render(root, { visitId }) {
  const visit = await api(`/api/visits/${visitId}`);
  if (visit.status !== "draft") { location.hash = `#/visit/${visitId}/${visit.status === "processing" ? "processing" : "review"}`; return; }
  const chart = await api(`/api/patients/${visit.patient_id}`);

  let pollId = null;
  let destroyed = false;
  let failures = 0;
  let lastVersion = -1;
  let pairing = null;

  const qrImg = el("img", { class: "phone-qr", alt: "QR code: scan it with a phone to record this visit", width: "264", height: "264" });
  const codeEl = el("div", { class: "phone-code mono" });
  const urlEl = el("div", { class: "muted small phone-url" });
  const expiresEl = el("div", { class: "muted small" });
  const stateEl = el("div", { class: "phone-state" });
  const timerEl = el("div", { class: "rec-timer mono hidden" }, "0:00");
  const transcriptEl = el("div", { class: "transcript live", "aria-live": "polite" });
  const newCodeBtn = el("button", { class: "btn hidden", onclick: () => pair() }, "Show a new code");
  const pairBox = el("div", { class: "phone-pair" }, qrImg, codeEl, urlEl, expiresEl);

  mount(root,
    el("div", { class: "page-title" },
      el("div", {},
        el("div", { class: "eyebrow" }, "Record with a phone"),
        el("h1", {}, chart.name),
        el("div", { class: "muted" }, `${chart.age} ${chart.sex} · ${visit.template.toUpperCase()} · ${visit.style}`)),
      el("div", { class: "row" },
        el("a", { class: "btn btn-ghost", href: `#/visit/${visitId}/record` }, "Record on this screen instead"),
        el("button", { class: "btn btn-ghost", onclick: discard }, "Discard"))),
    el("div", { class: "rec-grid phone-grid" },
      el("section", { class: "card rec-panel" },
        el("div", { class: "card-head" }, el("h3", {}, "Scan with the phone's camera")),
        el("div", { class: "card-body stack" }, pairBox, stateEl, timerEl, newCodeBtn)),
      el("section", { class: "card" },
        el("div", { class: "card-head" }, el("h3", {}, "Live transcript"), el("span", { class: "muted small" }, "Draft quality; a final pass runs when the phone sends the recording")),
        el("div", { class: "card-body transcript-scroll" }, transcriptEl))));

  function say(kind, text) {
    const cls = { live: "banner-ok", info: "banner-info", warn: "banner-warning", bad: "banner-critical" }[kind] || "";
    mount(stateEl, el("div", { class: `banner ${cls}` }, text));
  }

  function renderTurns(turns) {
    if (!turns.length) {
      mount(transcriptEl, el("p", { class: "muted" }, "The transcript appears here while the phone records."));
      return;
    }
    mount(transcriptEl, turns.map((t) => el("div", { class: `turn ${t.final ? "" : "partial"}` },
      el("span", { class: `spk spk-${(t.speaker || "U").replace(/[^A-Z]/g, "") || "U"}` }, t.speaker ? `Speaker ${t.speaker}` : "Speaker …"),
      el("span", { class: "turn-text" }, t.text))));
    const scroller = transcriptEl.parentElement;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }

  function update(s) {
    const st = s.state;
    const recording = st === "recording" || st === "paused" || st === "sending";
    pairBox.classList.toggle("hidden", recording || st === "processing");
    timerEl.classList.toggle("hidden", !recording);
    newCodeBtn.classList.toggle("hidden", !(st === "none" || st === "failed" || st === "cancelled"));
    if (recording) timerEl.textContent = fmt.dur(s.elapsed_s || 0);
    if (s.expires_in != null && (st === "waiting" || st === "connected")) expiresEl.textContent = `Code valid for ${Math.ceil(s.expires_in / 60)} more min`;
    if (st === "waiting") say("info", "Waiting for the phone: scan the code. The phone records; this screen shows the transcript and the note.");
    else if (st === "connected") say("info", "Phone connected. Press Start recording on the phone.");
    else if (st === "recording") {
      if (s.live === "live") say("live", s.medical ? "Recording on the phone · live transcript · Salient medical speech" : "Recording on the phone · live transcript");
      else if (s.live === "unavailable") say("warn", "Recording on the phone. The live transcript is unavailable; the note is drafted from the recording when the phone sends it.");
      else say("info", "Recording on the phone · connecting the live transcript…");
    } else if (st === "paused") say("warn", "Paused on the phone: not recording");
    else if (st === "sending") say("info", "The phone is sending the recording…");
    else if (st === "processing") { stop(); location.hash = `#/visit/${visitId}/processing`; return; }
    else if (st === "failed") say("bad", s.message || "The phone recording failed.");
    else say("warn", "This code has expired. Show a new code to record with a phone.");
    if (s.version !== lastVersion && s.turns) { lastVersion = s.version; renderTurns(s.turns); }
  }

  async function poll() {
    if (destroyed) return;
    try {
      update(await api(`/api/visits/${visitId}/phone`));
      failures = 0;
    } catch (e) {
      failures += 1;
      if (failures === 3) say("warn", `Can't reach the server (${e.message}); still trying…`);
    }
  }

  async function pair() {
    stop();
    newCodeBtn.classList.add("hidden");
    say("info", "Making a code…");
    try {
      pairing = await api(`/api/visits/${visitId}/phone`, { method: "POST" });
    } catch (e) {
      say("bad", e.message);
      newCodeBtn.classList.remove("hidden");
      return;
    }
    qrImg.src = pairing.qr;
    mount(codeEl, pairing.code_display);
    mount(urlEl, "or open ", el("strong", {}, pairing.url.replace(/^https?:\/\//, "").replace(/\?c=.*$/, "")), " on the phone and enter the code");
    lastVersion = -1;
    update(pairing);
    pollId = setInterval(poll, 1000);
  }

  function stop() { clearInterval(pollId); pollId = null; }

  async function discard() {
    stop();
    try { await api(`/api/visits/${visitId}/phone`, { method: "DELETE" }); } catch { /* ignore */ }
    location.hash = `#/patient/${visit.patient_id}`;
  }

  renderTurns([]);
  await pair();

  return () => { destroyed = true; stop(); };
}
