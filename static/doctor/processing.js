// The wait while the final pass and note run. This screen shows no patient data,
// so it is the one place a "note-drafting wait" sponsored card may appear; it is
// removed the moment the draft is ready.
import { api, el, mount, sleep } from "/js/common.js";
import { ads } from "/doctor/adsbridge.js";

const DEFAULT_STAGES = [
  { id: "final_transcript", label: "Final transcript (medical speech model)" },
  { id: "note", label: "Drafting the note" },
  { id: "verify", label: "Checking every line against the transcript" },
  { id: "meds", label: "Medication safety and formulary" },
];

export async function render(root, { visitId }, ctx) {
  let alive = true;
  const started = Date.now();
  const stepsEl = el("ol", { class: "stepper" });
  const errorEl = el("div");
  const elapsedEl = el("span", { class: "muted small mono" }, "0 s");
  const adBox = el("div", { class: "processing-ad" });

  mount(root,
    el("div", { class: "processing" },
      el("section", { class: "card processing-card" },
        el("div", { class: "card-body stack-lg" },
          el("div", { class: "row between" }, el("h2", {}, "Drafting your note"), elapsedEl),
          stepsEl,
          errorEl)),
      adBox));

  ads.mount(adBox, "drafting_wait", ctx.me);

  const draw = (status) => {
    const stages = (status.stages && status.stages.length ? status.stages : DEFAULT_STAGES.map((s) => ({ ...s, state: "pending" })));
    mount(stepsEl, stages.map((s) => el("li", { class: `step step-${s.state || "pending"}` },
      el("span", { class: "step-icon", "aria-hidden": "true" }, s.state === "done" ? "✓" : s.state === "error" ? "!" : s.state === "skipped" ? "–" : s.state === "active" ? el("span", { class: "spinner" }) : ""),
      el("div", {}, el("div", { class: "step-label" }, s.label), s.detail ? el("div", { class: "muted small" }, s.detail) : null))));
  };

  draw({ stages: [] });

  (async () => {
    let failures = 0;
    while (alive) {
      try {
        const status = await api(`/api/visits/${visitId}/status`);
        failures = 0;
        draw(status);
        elapsedEl.textContent = `${Math.round((Date.now() - started) / 1000)} s`;
        if (status.stage === "done") {
          ads.unmountAll();
          await sleep(250);
          if (alive) location.hash = `#/visit/${visitId}/review`;
          return;
        }
        if (status.stage === "error") {
          ads.unmountAll();
          mount(errorEl, el("div", { class: "banner banner-critical" },
            el("div", { class: "grow" }, el("strong", {}, "Drafting stopped. "), status.error || "Unknown error."),
            el("div", { class: "row" },
              el("button", { class: "btn btn-sm", onclick: () => retry("final") }, "Retry"),
              el("button", { class: "btn btn-sm", onclick: () => retry("live") }, "Use live transcript"))));
          return;
        }
      } catch (e) {
        failures += 1;
        if (failures > 5) { mount(errorEl, el("div", { class: "banner banner-warning" }, `Lost contact with the server (${e.message}). Retrying…`)); }
      }
      await sleep(1000);
    }
  })();

  async function retry(source) {
    mount(errorEl);
    try {
      const v = await api(`/api/visits/${visitId}`).catch(() => ({}));
      const body = v.source === "sample" ? { source: "sample", sample_id: v.sample_id } : { source: source === "final" && v.source === "live" ? "live" : source };
      await api(`/api/visits/${visitId}/process`, { method: "POST", body });
      location.reload();
    } catch (e) {
      mount(errorEl, el("div", { class: "banner banner-critical" }, e.message));
    }
  }

  return () => { alive = false; };
}
