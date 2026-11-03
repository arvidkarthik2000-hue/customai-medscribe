// "Note complete": shows no patient data, so the post-sign sponsored card may
// appear here, for the clinician only.
import { api, el, mount, fmt } from "/js/common.js";
import { ads } from "/doctor/adsbridge.js";
import { apptTime } from "/doctor/home.js";

export async function render(root, { visitId }, ctx) {
  const [visit, schedule] = await Promise.all([
    api(`/api/visits/${visitId}`),
    api("/api/schedule").catch(() => ({ appointments: [] })),
  ]);
  if (visit.status !== "signed") { location.hash = `#/visit/${visitId}/review`; return; }
  const next = (schedule.appointments || []).find((a) => a.status === "scheduled");
  const t = visit.timings || {};
  const secs = ["final_transcript_s", "note_s", "verify_s", "meds_s"].map((k) => t[k]).filter((x) => typeof x === "number");
  const adBox = el("div", { class: "signed-ad" });

  mount(root,
    el("div", { class: "signed" },
      el("section", { class: "card signed-card" },
        el("div", { class: "card-body stack-lg", style: { textAlign: "center" } },
          el("div", { class: "signed-check", "aria-hidden": "true" }, "✓"),
          el("h1", {}, "Note complete"),
          el("p", { class: "muted" }, `Signed at ${fmt.time(visit.signed_at)}${secs.length ? ` · drafted and checked in ${secs.reduce((a, b) => a + b, 0).toFixed(0)} s` : ""}`),
          el("div", { class: "row", style: { justifyContent: "center" } },
            el("a", { class: "btn", href: `#/visit/${visitId}/review` }, "View signed note"),
            next ? el("a", { class: "btn btn-primary", href: `#/patient/${next.patient_id}` }, `Next patient (${apptTime(next.time, schedule.timezone || "America/New_York")})`) : el("a", { class: "btn btn-primary", href: "#/" }, "Back to schedule")))),
      adBox));

  ads.mount(adBox, "post_sign", ctx.me);
}
