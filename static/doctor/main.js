// Doctor app: a tiny hash router over view modules.
// Every route change first removes all sponsored units, so no ad can ever be
// left on a screen that shows a chart, transcript, note or decision support.
import { api, renderHeader, el, mount, toast } from "/js/common.js";
import { ads } from "/doctor/adsbridge.js";

const app = document.getElementById("app");
const ctx = { me: null, config: null };

const routes = [
  { re: /^#?\/?$/, load: () => import("/doctor/home.js") },
  { re: /^#\/patient\/([\w-]+)$/, load: () => import("/doctor/chart.js"), keys: ["patientId"] },
  { re: /^#\/visit\/([\w-]+)\/record$/, load: () => import("/doctor/record.js"), keys: ["visitId"] },
  { re: /^#\/visit\/([\w-]+)\/replay\/([\w-]+)$/, load: () => import("/doctor/replay.js"), keys: ["visitId", "sampleId"] },
  { re: /^#\/visit\/([\w-]+)\/processing$/, load: () => import("/doctor/processing.js"), keys: ["visitId"] },
  { re: /^#\/visit\/([\w-]+)\/review$/, load: () => import("/doctor/review.js"), keys: ["visitId"] },
  { re: /^#\/visit\/([\w-]+)\/signed$/, load: () => import("/doctor/signed.js"), keys: ["visitId"] },
];

let cleanup = null;
let navToken = 0;

async function route() {
  const hash = location.hash || "#/";
  const match = routes.map((r) => ({ r, m: hash.match(r.re) })).find((x) => x.m);
  const token = ++navToken;
  if (cleanup) {
    try { await cleanup(); } catch (e) { console.warn(e); }
    cleanup = null;
  }
  ads.unmountAll();
  window.scrollTo(0, 0);
  if (!match) { location.hash = "#/"; return; }
  const params = {};
  (match.r.keys || []).forEach((k, i) => (params[k] = match.m[i + 1]));
  try {
    const mod = await match.r.load();
    if (token !== navToken) return;
    mount(app);
    const result = await mod.render(app, params, ctx);
    if (token !== navToken) { if (typeof result === "function") result(); return; }
    cleanup = typeof result === "function" ? result : null;
  } catch (err) {
    console.error(err);
    mount(app, el("div", { class: "card card-pad" },
      el("h2", {}, "Something went wrong"),
      el("p", { class: "muted" }, err.message || String(err)),
      el("a", { class: "btn", href: "#/" }, "Back to schedule")));
  }
}

export function go(hash) {
  if (location.hash === hash) route(); else location.hash = hash;
}

// Leaving a live recording by accident is costly: warn first.
window.addEventListener("beforeunload", (e) => {
  if (window.__msRecording) { e.preventDefault(); e.returnValue = ""; }
});

async function boot() {
  try {
    ctx.me = await api("/api/me");
    ctx.config = await api("/api/config");
  } catch (err) {
    if (err.status !== 401) toast(err.message, "error");
    return;
  }
  renderHeader("doctor", { subtitle: ctx.me.prescriber.practice });
  window.addEventListener("hashchange", route);
  route();
}

boot();
