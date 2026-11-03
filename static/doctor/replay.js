// Sample visit replay: a scripted transcript appears as if live, then the visit
// goes through the same pipeline as a real recording (note, verify, medicines).
import { api, el, mount, toast, fmt } from "/js/common.js";

export async function render(root, { visitId, sampleId }, ctx) {
  const [visit, sample] = await Promise.all([
    api(`/api/visits/${visitId}`),
    api(`/api/samples/${sampleId}`),
  ]);
  if (visit.status !== "draft") { location.hash = `#/visit/${visitId}/${visit.status === "processing" ? "processing" : "review"}`; return; }
  const chart = await api(`/api/patients/${visit.patient_id}`);
  const turns = sample.turns || [];
  const speed = 3; // replay at 3x
  let i = 0; let timer = null; let elapsed = 0; let stopped = false;

  const timerEl = el("span", { class: "rec-timer mono" }, "0:00");
  const transcriptEl = el("div", { class: "transcript live" });
  const skipBtn = el("button", { class: "btn btn-lg", onclick: () => finish(true) }, "Skip to end");
  const endBtn = el("button", { class: "btn btn-primary btn-lg", onclick: () => finish(true) }, "End visit");

  mount(root,
    el("div", { class: "page-title" },
      el("div", {},
        el("div", { class: "eyebrow" }, "Sample visit · scripted role-play"),
        el("h1", {}, chart.name),
        el("div", { class: "muted" }, `${sample.title} · replaying at ${speed}× speed`))),
    el("div", { class: "rec-grid" },
      el("section", { class: "card rec-panel" },
        el("div", { class: "rec-head" }, el("div", { class: "row" }, el("span", { class: "dot dot-live" }), timerEl), el("span", { class: "badge badge-info" }, "Replay: no microphone")),
        el("div", { class: "rec-actions" }, skipBtn, endBtn),
        el("p", { class: "muted small", style: { margin: "10px 0 0", textAlign: "center" } }, "The note, verification and medication checks that follow are generated live from this transcript.")),
      el("section", { class: "card" },
        el("div", { class: "card-head" }, el("h3", {}, "Transcript")),
        el("div", { class: "card-body transcript-scroll" }, transcriptEl)),
      (sample.highlights || []).length ? el("section", { class: "card" },
        el("div", { class: "card-head" }, el("h3", {}, "What this visit tests")),
        el("div", { class: "card-body row-wrap" }, sample.highlights.map((h) => el("span", { class: "badge badge-brand" }, h)))) : null));

  const shown = [];
  function draw() {
    mount(transcriptEl, shown.map((t) => el("div", { class: "turn" },
      el("span", { class: `spk spk-${t.speaker}` }, `Speaker ${t.speaker}`),
      el("span", { class: "turn-text" }, t.text))));
    const s = transcriptEl.parentElement; if (s) s.scrollTop = s.scrollHeight;
  }

  function next() {
    if (stopped) return;
    if (i >= turns.length) { finish(false); return; }
    const t = turns[i++];
    shown.push(t);
    elapsed = (t.end_ms || t.start_ms || elapsed * 1000) / 1000;
    timerEl.textContent = fmt.dur(elapsed);
    draw();
    const nextT = turns[i];
    const gap = nextT && nextT.start_ms != null && t.start_ms != null ? (nextT.start_ms - t.start_ms) / speed : 1200;
    timer = setTimeout(next, Math.max(350, Math.min(2600, gap)));
  }

  async function finish(skipped) {
    if (stopped) return;
    stopped = true; clearTimeout(timer);
    if (skipped) { shown.splice(0, shown.length, ...turns); draw(); }
    skipBtn.disabled = true; endBtn.disabled = true;
    try {
      await api(`/api/visits/${visitId}/consent`, { method: "POST", body: { consented: true, attestation: "roleplay_no_real_patient" } }).catch(() => null);
      await api(`/api/visits/${visitId}/process`, { method: "POST", body: { source: "sample", sample_id: sampleId } });
      location.hash = `#/visit/${visitId}/processing`;
    } catch (e) {
      toast(e.message, "error", 6000);
      skipBtn.disabled = false; endBtn.disabled = false; stopped = false;
    }
  }

  next();
  return () => { stopped = true; clearTimeout(timer); };
}
