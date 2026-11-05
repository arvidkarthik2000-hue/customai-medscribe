import { api, el, mount, renderHeader, requireSession, $ } from "/js/common.js";

const results = $("#results");
const runBtn = $("#run");

function row(check) {
  const ok = check.ok === true || check.status === "pass" || check.status === "ok";
  const skipped = check.status === "skipped";
  const cls = skipped ? "" : ok ? "badge-ok" : "badge-critical";
  return el("div", { class: "card card-pad row between", style: { alignItems: "flex-start" } },
    el("div", { class: "grow" },
      el("div", { class: "row-wrap" }, el("strong", {}, check.name || check.id), el("span", { class: `badge ${cls}` }, skipped ? "skipped" : ok ? "pass" : "fail")),
      check.detail ? el("div", { class: "muted small", style: { marginTop: "4px", whiteSpace: "pre-wrap" } }, typeof check.detail === "string" ? check.detail : JSON.stringify(check.detail)) : null),
    typeof check.seconds === "number" ? el("span", { class: "mono muted small" }, `${check.seconds.toFixed(1)} s`) : null);
}

async function run() {
  runBtn.disabled = true;
  mount(results, el("div", { class: "card card-pad row" }, el("span", { class: "spinner" }), el("span", {}, "Running checks… this can take up to a minute.")));
  try {
    const res = await api("/api/selftest");
    const checks = Array.isArray(res) ? res : res.checks || [];
    mount(results,
      el("div", { class: `banner ${checks.every((c) => c.ok === true || c.status === "pass" || c.status === "ok" || c.status === "skipped") ? "banner-ok" : "banner-warning"}` },
        res.summary || (checks.every((c) => c.ok === true || c.status === "pass" || c.status === "ok") ? "All checks passed." : "Some checks need attention.")),
      checks.map(row));
  } catch (e) {
    mount(results, el("div", { class: "banner banner-critical" }, e.message));
  } finally {
    runBtn.disabled = false;
  }
}

const evalBox = $("#eval");
const evalBtn = $("#run-eval");

function pct(x) { return typeof x === "number" ? `${Math.round(x * 100)}%` : "–"; }

function renderEval(res) {
  const data = res.status === "running" || res.status === "started" ? res.previous : res;
  const head = res.status === "running" ? el("div", { class: "banner banner-info" }, "Running now; refresh in a few minutes.")
    : res.status === "started" ? el("div", { class: "banner banner-info" }, res.detail)
    : res.status === "disabled" ? el("div", { class: "banner banner-warning" }, res.detail) : null;
  if (!data || !data.summary) { mount(evalBox, head || el("p", { class: "muted" }, "No results yet.")); return; }
  const s = data.summary;
  const g = s.gates || {};
  const gate = (ok, label) => el("span", { class: `badge ${ok ? "badge-ok" : "badge-critical"}` }, `${ok ? "✓" : "✗"} ${label}`);
  mount(evalBox, head,
    el("div", { class: "card card-pad stack" },
      el("div", { class: "row-wrap" },
        gate(g.drug_recall_at_least_95, "Medicine recall ≥ 95%"), gate(g.no_unexpected_medicines, "No unexpected medicines"),
        gate(g.dose_accuracy_at_least_95, "Dose and action ≥ 95%"), gate(g.processing_under_60s, "Processed in under 60 s")),
      el("dl", { class: "kv" },
        el("dt", {}, "Visits scored"), el("dd", {}, `${s.completed} of ${s.of}`),
        el("dt", {}, "Transcript word error rate"), el("dd", {}, pct(s.mean_transcript_wer)),
        el("dt", {}, "Medicine names heard"), el("dd", {}, s.drug_names_heard),
        el("dt", {}, "Medicine recall in notes"), el("dd", {}, pct(s.note_medicine_recall)),
        el("dt", {}, "Dose and action accuracy"), el("dd", {}, pct(s.dose_and_action_accuracy)),
        el("dt", {}, "Lines flagged by the verifier"), el("dd", {}, String(s.lines_flagged)),
        el("dt", {}, "Slowest processing after audio"), el("dd", {}, `${s.slowest_processing_s} s`),
        el("dt", {}, "Finished"), el("dd", {}, data.finished_at || "–")),
      el("p", { class: "muted small", style: { margin: 0 } }, s.caveat || "")),
    (data.results || []).map((r) => el("div", { class: "card card-pad stack" },
      el("div", { class: "row between" }, el("strong", {}, r.title || r.sample), r.error ? el("span", { class: "badge badge-critical" }, "error") : el("span", { class: "muted small mono" }, `${r.after_audio_s} s`)),
      r.error ? el("p", { class: "small", style: { color: "var(--critical)", margin: 0 } }, r.error) : el("div", { class: "small stack" },
        el("div", {}, `Speech: Salient medical speech · medical vocabulary ${r.speech?.medical_mode ? "on" : "off"} · ${r.speech?.keyterms} drug terms · WER ${pct(r.scores.transcript_wer)}`),
        el("div", {}, `Medicines heard ${r.scores.drug_names_heard} · recall ${pct(r.scores.note_medicine_recall)} · dose and action ${pct(r.scores.dose_and_action_accuracy)} · speakers ${pct(r.scores.speaker_agreement)} · roles ${r.scores.speaker_roles_correct}`),
        r.scores.note_medicines_missing.length ? el("div", { style: { color: "var(--critical)" } }, `Missing: ${r.scores.note_medicines_missing.join(", ")}`) : null,
        r.scores.note_medicines_extra.length ? el("div", { style: { color: "var(--warning)" } }, `Unexpected: ${r.scores.note_medicines_extra.join(", ")}`) : null,
        r.scores.dose_or_action_mismatches.map((m) => el("div", { style: { color: "var(--warning)" } }, `${m.medicine}: expected ${m.expected}, got ${m.got}`)),
        r.scores.drug_names_missed.map((m) => el("div", { style: { color: "var(--warning)" } }, `Not heard: ${m.as_spoken}`)),
        el("div", { class: "muted" }, `Alerts: ${(r.scores.alerts || []).map((c) => `${c.indicator} ${c.category}`).join(", ") || "none"}`)))));
}

async function loadEval() {
  evalBtn.disabled = true;
  try { renderEval(await api("/api/eval/public")); } catch (e) { mount(evalBox, el("div", { class: "banner banner-critical" }, e.message)); }
  finally { evalBtn.disabled = false; }
}

requireSession().then(() => {
  renderHeader("", { subtitle: "Self-test" });
  runBtn.addEventListener("click", run);
  evalBtn.addEventListener("click", loadEval);
});
