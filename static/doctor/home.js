// Today: the schedule, practice profile, sample visits and the one sponsored
// schedule card. The card sits beside the schedule list only; opening a chart
// navigates away and the router removes it.
import { api, el, mount, toast, modal } from "/js/common.js";
import { ads } from "/doctor/adsbridge.js";
import { SPECIALTIES, STATES, statusBadge, sexAge, readinessBanner } from "/doctor/shared.js";

export async function render(root, _params, ctx) {
  const [schedule, samples] = await Promise.all([
    api("/api/schedule"),
    api("/api/samples").catch(() => ({ samples: [] })),
  ]);
  const me = ctx.me;
  const appts = schedule.appointments || [];
  const tz = schedule.timezone || "America/New_York";
  const done = appts.filter((a) => a.status === "signed").length;

  const adBox = el("div", { class: "home-ad" });
  const profileCard = renderProfile(me, async (specialty, state) => {
    try {
      await api("/api/session/role", { method: "POST", body: { role: "doctor", specialty, state } });
      ctx.me = await api("/api/me");
      toast(`Practice profile: ${SPECIALTIES[specialty]}, ${state}`, "ok");
      ads.unmountAll();
      ads.mount(adBox, "schedule_card", ctx.me);
    } catch (e) { toast(e.message, "error"); }
  });

  const dateLabel = new Date().toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });

  mount(root,
    el("div", { class: "page-title" },
      el("div", {},
        el("div", { class: "eyebrow" }, dateLabel),
        el("h1", {}, `Good ${greeting()}, ${me.prescriber.name.replace(/,.*$/, "")}`),
        el("p", { class: "muted", style: { margin: "4px 0 0" } }, `${appts.length} visits today · ${done} signed`)),
      el("div", { class: "row" },
        el("button", { class: "btn btn-ghost", title: "Archive today's visits and start the schedule fresh", onclick: () => resetDay() }, "Reset today"),
        el("button", { class: "btn", onclick: () => ads.openTray(ctx.me) }, "Resources"))),
    readinessBanner(me),
    el("div", { class: "home-grid" },
      el("div", { class: "stack-lg" },
        el("section", { class: "card" },
          el("div", { class: "card-head" }, el("h2", {}, "Today's schedule"), el("span", { class: "muted small" }, "Synthetic patients")),
          scheduleTable(appts, tz)),
        samplesCard(samples.samples || [])),
      el("aside", { class: "stack-lg" },
        adBox,
        profileCard,
        guideCard())));

  ads.mount(adBox, "schedule_card", me);

  function resetDay() {
    modal({
      title: "Start today's schedule fresh?",
      body: el("p", {}, "Today's demo visits are archived and every appointment goes back to Scheduled. Useful between testers."),
      actions: [{ label: "Cancel" }, { label: "Reset", primary: true, onClick: async () => {
        try { await api("/api/schedule/reset", { method: "POST" }); toast("Schedule reset.", "ok"); render(root, _params, ctx); }
        catch (e) { toast(e.message, "error"); return true; }
      } }],
    });
  }
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "morning" : h < 17 ? "afternoon" : "evening";
}

export function apptTime(iso, tz) {
  if (!iso) return "";
  if (!/T/.test(iso)) return iso;
  try { return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz }); } catch { return iso; }
}

function scheduleTable(appts, tz) {
  if (!appts.length) return el("div", { class: "empty" }, "No visits scheduled.");
  return el("div", { class: "table-wrap" },
    el("table", { class: "table schedule" },
      el("thead", {}, el("tr", {}, el("th", {}, "Time"), el("th", {}, "Patient"), el("th", {}, "Reason"), el("th", {}, "Status"), el("th", {}, ""))),
      el("tbody", {}, appts.map((a) => {
        const target = a.visit_id && (a.status === "ready_to_sign" || a.status === "signed" || a.status === "in_progress")
          ? (a.status === "signed" ? `#/visit/${a.visit_id}/review` : `#/visit/${a.visit_id}/review`)
          : `#/patient/${a.patient_id}`;
        return el("tr", { class: "clickable", onclick: () => (location.hash = target) },
          el("td", { class: "nowrap mono" }, apptTime(a.time, tz)),
          el("td", {}, el("div", { class: "strong" }, a.name), el("div", { class: "muted small" }, `${sexAge(a)} · ${a.visit_type || "Office visit"}`)),
          el("td", {}, a.reason),
          el("td", {}, statusBadge(a.status)),
          el("td", { class: "num" }, el("span", { class: "btn btn-sm" }, a.status === "signed" ? "View" : a.status === "ready_to_sign" ? "Review" : "Open")));
      }))));
}

function samplesCard(samples) {
  if (!samples.length) return null;
  return el("section", { class: "card" },
    el("div", { class: "card-head" },
      el("div", {}, el("h2", {}, "Try a sample visit"), el("p", { class: "muted small", style: { margin: "2px 0 0" } }, "No microphone needed: a scripted visit replays as if live, then runs through the full pipeline.")),
    ),
    el("div", { class: "samples" }, samples.map((s) =>
      el("a", { class: "sample", href: `#/patient/${s.patient_id}` },
        el("div", { class: "row between" }, el("strong", {}, s.title), el("span", { class: "badge" }, `${s.minutes} min`)),
        s.patient_name ? el("div", { class: "muted small" }, s.patient_name) : null,
        el("div", { class: "row-wrap", style: { marginTop: "6px" } }, (s.highlights || []).slice(0, 3).map((h) => el("span", { class: "badge badge-brand" }, h)))))));
}

function renderProfile(me, onChange) {
  const p = me.prescriber;
  const spec = el("select", { class: "select", "aria-label": "Specialty" }, Object.entries(SPECIALTIES).map(([k, v]) => el("option", { value: k, selected: k === p.specialty }, v)));
  const st = el("select", { class: "select", "aria-label": "State" }, STATES.map((s) => el("option", { value: s, selected: s === p.state }, s)));
  const apply = () => onChange(spec.value, st.value);
  spec.addEventListener("change", apply);
  st.addEventListener("change", apply);
  return el("section", { class: "card" },
    el("div", { class: "card-head" }, el("h3", {}, "Practice profile")),
    el("div", { class: "card-body stack" },
      el("div", { class: "kv" },
        el("dt", {}, "Clinician"), el("dd", {}, p.name),
        el("dt", {}, "Practice"), el("dd", {}, p.practice),
        el("dt", {}, "NPI"), el("dd", { class: "mono" }, `${p.npi} (fictional)`)),
      el("div", { class: "grid grid-2 profile-grid" },
        el("div", { class: "field" }, el("label", {}, "Declared specialty"), spec),
        el("div", { class: "field" }, el("label", {}, "State"), st)),
      el("p", { class: "muted small", style: { margin: 0 } }, "Sponsored units are chosen by declared specialty and state only, never by anything said in a visit.")));
}

function guideCard() {
  return el("section", { class: "card" },
    el("div", { class: "card-head" }, el("h3", {}, "Demo guide")),
    el("div", { class: "card-body" },
      el("ol", { class: "guide" },
        el("li", {}, "Open a patient, review the pre-visit summary, then record a role-play or play the sample visit."),
        el("li", {}, "Watch the live transcript; end the visit to get the final pass and the drafted note."),
        el("li", {}, "Click any evidence chip to see the exact words behind a line. Confirm each medicine, review the safety alerts, then sign."),
        el("li", {}, "Switch to the Network console to see the impressions you just generated, marked as test traffic.")),
      el("p", { class: "muted small", style: { margin: "8px 0 0" } }, "Role-play only. Do not use real patient information.")));
}
