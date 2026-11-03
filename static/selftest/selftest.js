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

requireSession().then(() => {
  renderHeader("", { subtitle: "Self-test" });
  runBtn.addEventListener("click", run);
});
