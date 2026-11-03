import { api, el, mount, fmt, toast, renderHeader, requireSession, debounce } from "/js/common.js";
import { columnChart, barList, pacingBullets, chartCard, compact } from "/console/charts.js";

const A = (window.MEDSCRIBE_ADZONE_BASE || "/adzone").replace(/\/$/, "");
const TABS = [
  ["overview", "Overview"], ["campaigns", "Campaigns"], ["creative", "Creative and compliance"],
  ["delivery", "Delivery and pacing"], ["audience", "Audience"], ["engagement", "Engagement"],
  ["verification", "Verification"], ["sharing", "Sharing"],
];
const SCOPED = new Set(["overview", "audience", "engagement"]);
const state = { tab: (location.hash.slice(1) || "overview"), days: 30, scope: "network", meta: null, live: { after: 0, items: [] }, timer: null };

const view = document.getElementById("view");
const tabsEl = document.getElementById("tabs");
const filtersEl = document.getElementById("filters");

// ---------------------------------------------------------------- helpers
const pct = (v, d = 0) => fmt.pct(v, d);
const money2 = (v) => fmt.money(v, 2);
const dayLabel = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const dateLong = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

function stat(label, value, sub) {
  return el("div", { class: "card stat" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value), sub ? el("div", { class: "sub" }, sub) : null);
}
function badge(text, kind = "") { return el("span", { class: `badge ${kind ? `badge-${kind}` : ""}` }, text); }
const STATUS = { active: ["Active", "ok"], scheduled: ["Scheduled", "info"], ended: ["Ended", ""], paused: ["Paused", "warning"], draft: ["Draft", ""] };
const PACE = { on_pace: ["On pace", "ok"], behind: ["Behind", "warning"], ahead: ["Ahead", "info"], complete: ["Delivered", "ok"],
  flat_fee: ["Flat fee", "brand"], scheduled: ["Not started", "info"], not_started: ["Not started", "info"] };
const statusBadge = (s) => badge(...(STATUS[s] || [s, ""]));
const paceBadge = (p) => badge(...(PACE[p.state] || [p.state, ""]));
const contract = (c) => (c.contract_type === "cpm" ? `CPM ${money2(c.cpm)}` : `${money2(c.fee_per_prescriber_month)} per prescriber a month`);
const booked = (c) => (c.contract_type === "cpm" ? `${fmt.int(c.booked_viewable)} viewable` : `${fmt.int(c.sponsored_prescribers)} prescribers`);
const section = (title, sub, ...right) => el("div", { class: "section-title" }, el("div", {}, el("h2", {}, title), sub ? el("p", { class: "muted small" }, sub) : null), el("div", { class: "toolbar" }, right));

function dailyCard(rep, title = "Viewable impressions per day") {
  const rows = rep.daily;
  return chartCard({
    title,
    subtitle: "Valid traffic only. Hover a day for served, viewable, invalid and test counts.",
    render: (body) => columnChart(body, {
      valueLabel: "viewable impressions",
      every: rows.length > 40 ? 10 : rows.length > 14 ? 5 : 2,
      data: rows.map((d) => ({
        label: dayLabel(d.date), title: dateLong(d.date), value: d.viewable,
        tip: [["viewable", fmt.int(d.viewable)], ["served", fmt.int(d.served)],
          ["viewability", d.served ? pct(d.viewable / d.served, 1) : "–"], ["invalid removed", fmt.int(d.ivt_removed)],
          ["test traffic", fmt.int(d.test_excluded)], ["spend", money2(d.spend)]],
      })),
    }),
    table: () => ({
      columns: ["Date", "Served", "Viewable", "Viewability", "Invalid removed", "Test", "Spend"],
      rows: rows.map((d) => [d.date, fmt.int(d.served), fmt.int(d.viewable), d.served ? pct(d.viewable / d.served, 1) : "–", fmt.int(d.ivt_removed), fmt.int(d.test_excluded), money2(d.spend)]),
    }),
  });
}

// ---------------------------------------------------------------- data
async function meta() {
  if (!state.meta) state.meta = await api(`${A}/api/campaigns`);
  return state.meta;
}
async function scopedReport() {
  if (state.scope === "network") return api(`${A}/api/overview?days=${state.days}`);
  return api(`${A}/api/campaigns/${encodeURIComponent(state.scope)}`);
}

// ---------------------------------------------------------------- shell
function renderTabs() {
  mount(tabsEl, TABS.map(([id, label]) => el("button", {
    type: "button", class: state.tab === id ? "active" : "", "aria-current": state.tab === id ? "page" : null,
    onclick: () => { state.tab = id; history.replaceState(null, "", `#${id}`); go(); },
  }, label)));
}

async function renderFilters() {
  if (!SCOPED.has(state.tab)) { filtersEl.hidden = true; return; }
  filtersEl.hidden = false;
  const m = await meta();
  const scope = el("select", { class: "select", id: "scope", onchange: (e) => { state.scope = e.target.value; go(); } },
    el("option", { value: "network" }, "Whole network"),
    m.campaigns.filter((c) => c.effective_status !== "scheduled").map((c) => el("option", { value: c.id, selected: state.scope === c.id }, `${c.brand_name}: ${c.name}`)));
  scope.value = state.scope;
  const period = el("select", { class: "select", id: "period", disabled: state.scope !== "network", onchange: (e) => { state.days = Number(e.target.value); go(); } },
    [7, 30, 90].map((d) => el("option", { value: d, selected: state.days === d }, `Last ${d} days`)));
  mount(filtersEl,
    el("div", { class: "field" }, el("label", { for: "period" }, "Period"), period),
    el("div", { class: "field" }, el("label", { for: "scope" }, "Scope"), scope),
    state.scope !== "network" ? el("span", { class: "note" }, "Campaign scope shows the flight to date.") : null);
}

async function go() {
  stopLive();
  renderTabs();
  await renderFilters();
  view.style.opacity = "0.55";
  try {
    const fn = { overview, campaigns, creative, delivery, audience, engagement, verification, sharing }[state.tab] || overview;
    await fn();
  } catch (err) {
    mount(view, el("div", { class: "banner banner-critical" }, `Could not load this view: ${err.message}`));
  } finally {
    view.style.opacity = "";
  }
}

// ---------------------------------------------------------------- overview
async function overview() {
  const rep = await scopedReport();
  const t = rep.totals, a = rep.audience, p = rep.practices;
  const isNet = state.scope === "network";
  const kpis = el("div", { class: "kpis" },
    stat("Viewable impressions", compact(t.viewable), `${fmt.int(t.served)} served · valid traffic`),
    stat("Viewability", pct(t.viewability, 1), "50% in view for 1 second"),
    stat("Prescribers reached", fmt.int(a.reach), `of ${fmt.int(a.network_prescribers)} in the network`),
    stat("Average frequency", a.frequency_avg ? a.frequency_avg.toFixed(1) : "–", isNet ? "viewable per prescriber, all brands" : "viewable per prescriber"),
    stat("Spend", fmt.money(t.spend), "at contracted rates (simulated)"),
    stat("Effective CPM", money2(t.ecpm), "spend per 1,000 viewable"),
    stat("Active practices", `${fmt.int(p.active_practices)} of ${fmt.int(p.network_offices)}`, `${pct(p.share_of_open_days_played)} of open days had delivery`),
    stat("Invalid traffic removed", fmt.int(t.ivt_removed), `${pct(t.ivt_rate, 1)} of rendered units`),
    isNet ? stat("Test traffic today", fmt.int(rep.live_today.test.served), "demo logins, never billed") : stat("Test traffic", fmt.int(t.test_excluded), "demo logins, never billed"));
  const blocks = [kpis, section(isNet ? "Delivery" : `Delivery: ${rep.campaign.brand_name}`, isNet ? `${dateLong(rep.period.start)} to ${dateLong(rep.period.end)}` : `Flight to date from ${dateLong(rep.period.start)}`), dailyCard(rep)];
  if (isNet) {
    blocks.push(el("div", { class: "cols-2", style: { marginTop: "16px" } }, adLoadCard(rep.inventory, rep.policy), liveCard()));
  } else {
    blocks.push(el("div", { class: "cols-2", style: { marginTop: "16px" } }, pacingCard([{ ...rep.campaign, pacing: rep.pacing, totals: rep.totals }]), liveCard()));
  }
  mount(view, ...blocks);
  startLive();
}

function adLoadCard(inv, policy) {
  const slotLabels = { schedule_card: "Schedule card", drafting_wait: "Note-drafting wait", post_sign: "Post-sign card" };
  const checks = Object.entries(slotLabels).map(([k, label]) => el("label", { class: "checkbox" },
    el("input", { type: "checkbox", value: k, checked: policy.enabled_slots.includes(k), class: "slot-toggle" }), label));
  const cap = el("select", { class: "select", style: { width: "auto" } }, [1, 2, 3, 4, 5].map((n) => el("option", { value: n, selected: policy.default_cap === n }, `${n} a week`)));
  const per = inv.per_prescriber_month;
  return el("section", { class: "card" },
    el("div", { class: "card-head" }, el("div", {}, el("h3", {}, "Ad load and sellable inventory"), el("div", { class: "muted small" }, "Per prescriber per month, across the network"))),
    el("div", { class: "card-body stack-lg" },
      el("div", { class: "grid grid-3" },
        el("div", {}, el("div", { class: "label" }, "Slot opportunities"), el("div", { class: "counter" }, el("strong", {}, fmt.int(per.opportunities))), el("div", { class: "note" }, "visits x enabled slots")),
        el("div", {}, el("div", { class: "label" }, "Sellable under caps"), el("div", { class: "counter" }, el("strong", {}, fmt.int(per.sellable))), el("div", { class: "note" }, `${inv.categories_live} live categories x cap`)),
        el("div", {}, el("div", { class: "label" }, "Sold (last 30 days)"), el("div", { class: "counter" }, el("strong", {}, fmt.int(per.sold))), el("div", { class: "note" }, `${pct(inv.sell_through)} sell-through`))),
      el("p", { class: "note" }, `Caps bind long before slots run out: ${pct(inv.utilization, 1)} of slot opportunities carry a sold unit. ${inv.note}`),
      el("div", { class: "stack" }, el("div", { class: "label" }, "Slots enabled across the network"), el("div", { class: "row-wrap" }, checks)),
      el("div", { class: "row-wrap" }, el("span", { class: "label" }, "Default cap per brand per prescriber"), cap,
        el("button", { class: "btn btn-primary btn-sm", onclick: async () => {
          const enabled = Array.from(document.querySelectorAll(".slot-toggle")).filter((c) => c.checked).map((c) => c.value);
          try {
            await api(`${A}/api/network`, { method: "POST", body: { enabled_slots: enabled, default_cap: Number(cap.value) } });
            toast("Ad load updated. The doctor app follows it on its next request.", "ok");
            go();
          } catch (e) { toast(e.message, "error"); }
        } }, "Apply")),
      el("p", { class: "note" }, "Switching a slot off here stops it in the doctor app immediately. Ads never appear on clinical screens, whatever this setting says.")));
}

// ---------------------------------------------------------------- live feed (test and invalid traffic)
function liveCard(full = false) {
  const counters = el("div", { class: "row-wrap", id: "live-counters" });
  const body = el("tbody", { id: "live-body" });
  return el("section", { class: "card" },
    el("div", { class: "card-head" },
      el("div", {}, el("h3", {}, "Live feed: test and invalid traffic"), el("div", { class: "muted small" }, "Demo logins appear here within seconds. Valid delivery is reported as day-level counts only.")),
      el("button", { class: "btn btn-sm", onclick: async () => {
        await api(`${A}/api/test/reset-caps`, { method: "POST" });
        toast("Test-account caps and hides cleared. Events stay in the log.", "ok");
      } }, "Reset test caps")),
    el("div", { class: "card-body stack" }, counters,
      el("div", { class: "table-wrap" }, el("table", { class: "table live-table" },
        el("thead", {}, el("tr", {}, ["Time (UTC)", "Event", "Slot", "Brand", "Traffic", "Why"].map((h) => el("th", {}, h)))), body)),
      el("p", { class: "note" }, full ? "Operator view. Sponsors never see this feed; their reports and exports carry day-level counts." : "Operator view only.")));
}

function renderLive() {
  const body = document.getElementById("live-body");
  if (!body) return;
  const items = state.live.items.slice(0, 30);
  mount(body, items.length ? items.map((i) => el("tr", {},
    el("td", { class: "mono" }, i.time), el("td", {}, i.type), el("td", {}, i.slot), el("td", {}, i.brand || "–"),
    el("td", {}, i.traffic === "test" ? badge("Test", "info") : badge("Invalid", "warning")),
    el("td", { class: "muted" }, (i.reason || "").replace(/_/g, " ")))) :
    el("tr", {}, el("td", { colspan: 6, class: "muted" }, "No test or invalid traffic yet. Open the Doctor view in another tab and walk a visit.")));
  const c = document.getElementById("live-counters");
  if (c && state.live.today) {
    const t = state.live.today;
    mount(c,
      el("span", { class: "counter" }, el("strong", {}, fmt.int(t.valid.served)), el("span", { class: "muted small" }, "valid served today")),
      el("span", { class: "counter" }, el("strong", {}, fmt.int(t.test.served)), el("span", { class: "muted small" }, "test served today")),
      el("span", { class: "counter" }, el("strong", {}, fmt.int(t.ivt.served)), el("span", { class: "muted small" }, "invalid today")));
  }
}

async function pollLive() {
  try {
    const r = await api(`${A}/api/live?after=${state.live.after}&limit=60`);
    if (r.items.length) state.live.items = [...r.items, ...state.live.items].slice(0, 200);
    state.live.after = Math.max(state.live.after, r.head_seq);
    state.live.today = r.today;
    renderLive();
  } catch { /* keep the frame */ }
}
function startLive() { pollLive(); state.timer = setInterval(pollLive, 4000); }
function stopLive() { if (state.timer) clearInterval(state.timer); state.timer = null; }

// ---------------------------------------------------------------- campaigns
async function campaigns() {
  state.meta = null;
  const m = await meta();
  const formSlot = el("div", { id: "form-slot" });
  const rows = [];
  for (const c of m.campaigns) {
    const detail = el("tr", { class: "hidden" }, el("td", { colspan: 9, class: "detail-panel" }, el("div", { class: "inner" }, el("div", { class: "muted" }, "Loading…"))));
    const row = el("tr", { class: "clickable", tabindex: 0, onclick: () => toggleDetail(c, detail), onkeydown: (e) => { if (e.key === "Enter") toggleDetail(c, detail); } },
      el("td", {}, el("strong", {}, c.name), el("div", { class: "sub" }, `${c.brand_name} · ${c.category}`)),
      el("td", {}, contract(c)),
      el("td", { class: "nowrap" }, `${dayLabel(c.start_date)} – ${dayLabel(c.end_date)}`),
      el("td", {}, statusBadge(c.effective_status)),
      el("td", { class: "num" }, fmt.int(c.totals.viewable), el("div", { class: "sub" }, `of ${booked(c)}`)),
      el("td", {}, paceBadge(c.pacing), c.pacing.ratio ? el("div", { class: "sub" }, pct(c.pacing.ratio)) : null),
      el("td", { class: "num" }, pct(c.totals.viewability, 1)),
      el("td", { class: "num" }, fmt.money(c.totals.spend)),
      el("td", { class: "num" }, money2(c.totals.ecpm)));
    rows.push(row, detail);
  }
  mount(view,
    section("Campaigns", "Flight to date. Click a campaign for pacing, slots and its report link.",
      el("button", { class: "btn btn-primary", onclick: () => { formSlot.firstChild ? mount(formSlot) : mount(formSlot, campaignForm(m)); } }, "New campaign")),
    formSlot,
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["Campaign", "Contract", "Flight", "Status", "Delivered", "Pacing", "Viewability", "Spend", "eCPM"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows)))));
}

async function toggleDetail(c, detailRow) {
  if (!detailRow.classList.contains("hidden")) { detailRow.classList.add("hidden"); return; }
  detailRow.classList.remove("hidden");
  const inner = detailRow.querySelector(".inner");
  const rep = await api(`${A}/api/campaigns/${encodeURIComponent(c.id)}`);
  const shareUrl = `${location.origin}/report/?share=${encodeURIComponent(rep.share_token)}`;
  mount(inner,
    el("div", { class: "toolbar" },
      el("a", { class: "btn btn-sm", href: shareUrl, target: "_blank", rel: "noopener" }, "Open sponsor report"),
      el("a", { class: "btn btn-sm", href: `${A}/api/export.csv?campaign_id=${encodeURIComponent(c.id)}` }, "Day-level CSV"),
      c.effective_status === "active" ? el("button", { class: "btn btn-sm", onclick: async () => { await api(`${A}/api/campaigns/${c.id}`, { method: "PATCH", body: { status: "paused" } }); toast("Campaign paused.", "ok"); campaigns(); } }, "Pause") : null,
      c.effective_status === "paused" ? el("button", { class: "btn btn-sm", onclick: async () => { await api(`${A}/api/campaigns/${c.id}`, { method: "PATCH", body: { status: "active" } }); toast("Campaign resumed.", "ok"); campaigns(); } }, "Resume") : null,
      el("span", { class: "note" }, `Targeting: ${(c.specialty_labels || []).join(", ")} · ${c.states.length === 12 ? "all 12 states" : c.states.join(", ")} · ${c.slot_labels.join(", ")} · cap ${c.freq_cap_week} a week`)),
    el("div", { class: "cols-2" }, dailyCard(rep), el("div", { class: "stack-lg" },
      chartCard({ title: "Viewable impressions by slot", render: (b) => barList(b, { valueLabel: "viewable", data: rep.audience.by_slot.map((s) => ({ label: s.label, value: s.viewable })) }),
        table: () => ({ columns: ["Slot", "Served", "Viewable", "Reach"], rows: rep.audience.by_slot.map((s) => [s.label, fmt.int(s.served), fmt.int(s.viewable), fmt.int(s.reach)]) }) }),
      el("div", { class: "card card-pad" }, el("dl", { class: "kv" },
        el("dt", {}, "Prescribers reached"), el("dd", {}, fmt.int(rep.audience.reach)),
        el("dt", {}, "Average frequency"), el("dd", {}, rep.audience.frequency_avg ? rep.audience.frequency_avg.toFixed(1) : "–"),
        el("dt", {}, "Safety-information opens"), el("dd", {}, fmt.int(rep.totals.isi_opens)),
        el("dt", {}, "Prescribing-information opens"), el("dd", {}, fmt.int(rep.totals.clicks)),
        el("dt", {}, "Hides"), el("dd", {}, fmt.int(rep.totals.hides)),
        el("dt", {}, "Creative versions"), el("dd", {}, rep.creatives.map((cr) => `v${cr.version} ${cr.format}${cr.servable ? "" : " (not served)"}`).join(", ")))))));
}

function campaignForm(m) {
  const today = new Date().toISOString().slice(0, 10);
  const in60 = new Date(Date.now() + 60 * 864e5).toISOString().slice(0, 10);
  const f = {
    brand: el("select", { class: "select", name: "brand_id" }, m.brands.map((b) => el("option", { value: b.id }, `${b.name} · ${b.category}${b.boxed_warning ? " · boxed warning" : ""}`))),
    name: el("input", { class: "input", name: "name", value: "New HCP flight" }),
    type: el("select", { class: "select" }, el("option", { value: "cpm" }, "CPM"), el("option", { value: "sponsorship" }, "Flat sponsorship")),
    cpm: el("input", { class: "input", type: "number", min: 5, max: 500, step: "0.5", value: 60 }),
    fee: el("input", { class: "input", type: "number", min: 0.5, max: 100, step: "0.25", value: 2.5 }),
    booked: el("input", { class: "input", type: "number", min: 1000, step: 500, value: 5000 }),
    start: el("input", { class: "input", type: "date", value: today }),
    end: el("input", { class: "input", type: "date", value: in60 }),
    cap: el("select", { class: "select" }, [1, 2, 3, 4, 5].map((n) => el("option", { value: n, selected: n === 3 }, `${n} a week per prescriber`))),
    excl: el("input", { type: "checkbox", checked: true }),
  };
  const specChecks = Object.entries(m.specialties).map(([k, v]) => el("label", { class: "checkbox" }, el("input", { type: "checkbox", value: k, checked: ["family_medicine", "internal_medicine"].includes(k), "data-group": "spec" }), v));
  const stateChecks = Object.entries(m.states).map(([k, v]) => el("label", { class: "checkbox", title: v }, el("input", { type: "checkbox", value: k, checked: true, "data-group": "state" }), k));
  const slotChecks = Object.entries(m.slots).map(([k, v]) => el("label", { class: "checkbox" }, el("input", { type: "checkbox", value: k, checked: k !== "post_sign", "data-group": "slot" }), v));
  const fc = el("div", { class: "stack" }, el("div", { class: "muted" }, "Forecast appears here."));
  const cpmField = el("div", { class: "field" }, el("label", {}, "CPM (USD per 1,000 viewable)"), f.cpm);
  const feeField = el("div", { class: "field hidden" }, el("label", {}, "Fee per prescriber per month (USD)"), f.fee);
  const bookedField = el("div", { class: "field" }, el("label", {}, "Booked viewable impressions"), f.booked);
  const form = el("form", { class: "card card-pad stack-lg" });

  const checked = (g) => Array.from(form.querySelectorAll(`input[data-group="${g}"]`)).filter((i) => i.checked).map((i) => i.value);
  const body = () => ({
    brand_id: f.brand.value, name: f.name.value, contract_type: f.type.value,
    cpm: f.type.value === "cpm" ? Number(f.cpm.value) : null,
    fee_per_prescriber_month: f.type.value === "sponsorship" ? Number(f.fee.value) : null,
    booked_viewable: f.type.value === "cpm" ? Number(f.booked.value) : null,
    start_date: f.start.value, end_date: f.end.value,
    specialties: checked("spec"), states: checked("state"), slots: checked("slot"),
    freq_cap_week: Number(f.cap.value), exclusivity: f.excl.checked,
  });
  const refresh = debounce(async () => {
    const b = body();
    if (!b.specialties.length || !b.states.length || !b.slots.length || !b.start_date || !b.end_date) { mount(fc, el("div", { class: "muted" }, "Pick at least one specialty, state and slot.")); return; }
    try {
      const r = await api(`${A}/api/forecast`, { method: "POST", body: {
        brand_id: b.brand_id, contract_type: b.contract_type, cpm: b.cpm, fee_per_prescriber_month: b.fee_per_prescriber_month,
        start_date: b.start_date, end_date: b.end_date, specialties: b.specialties, states: b.states, slots: b.slots, freq_cap_week: b.freq_cap_week } });
      mount(fc,
        el("div", {}, el("div", { class: "label" }, "Projected viewable impressions"), el("div", { class: "big" }, fmt.int(r.projected_viewable))),
        el("div", {}, el("div", { class: "label" }, "Projected revenue"), el("div", { class: "big" }, fmt.money(r.projected_revenue)), el("div", { class: "note" }, `Effective CPM ${money2(r.effective_cpm)}`)),
        el("dl", { class: "kv" },
          el("dt", {}, "Eligible prescribers"), el("dd", {}, `${fmt.int(r.eligible_prescribers)} in ${fmt.int(r.eligible_offices)} offices`),
          el("dt", {}, "Held by another brand"), el("dd", {}, r.held_by_other_brand ? `${fmt.int(r.held_by_other_brand)} prescribers (category exclusivity)` : "None"),
          el("dt", {}, "Slot opportunities"), el("dd", {}, fmt.int(r.slot_opportunities)),
          el("dt", {}, "Sellable under the cap"), el("dd", {}, `${fmt.int(r.sellable_impressions)} (${pct(r.utilization, 1)} of opportunities)`),
          el("dt", {}, "Projected served"), el("dd", {}, fmt.int(r.projected_served)),
          el("dt", {}, "Flight"), el("dd", {}, `${r.flight_days} days`)),
        f.type.value === "cpm" && Number(f.booked.value) > r.projected_viewable ? el("div", { class: "banner banner-warning" }, "Booked volume is above what the caps allow in this flight. Widen targeting, lengthen the flight or lower the booking.") : null,
        el("p", { class: "note" }, r.assumptions.note));
    } catch (e) { mount(fc, el("div", { class: "banner banner-critical" }, e.message)); }
  }, 300);
  f.type.addEventListener("change", () => {
    const cpm = f.type.value === "cpm";
    cpmField.classList.toggle("hidden", !cpm); bookedField.classList.toggle("hidden", !cpm); feeField.classList.toggle("hidden", cpm);
  });
  form.addEventListener("input", refresh);
  form.addEventListener("change", refresh);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api(`${A}/api/campaigns`, { method: "POST", body: body() });
      toast(r.warnings.length ? `Campaign created. ${r.warnings.join(". ")}.` : "Campaign created and live for matching practices.", "ok");
      campaigns();
    } catch (err) { toast(err.message, "error"); }
  });
  mount(form,
    el("div", { class: "row between" }, el("h2", {}, "New campaign"), el("span", { class: "badge badge-sim" }, "Fictional brands only")),
    el("div", { class: "cols-3-1" },
      el("div", { class: "form-grid" },
        el("div", { class: "field span-2" }, el("label", {}, "Brand"), f.brand),
        el("div", { class: "field span-2" }, el("label", {}, "Campaign name"), f.name),
        el("div", { class: "field" }, el("label", {}, "Contract"), f.type),
        cpmField, feeField, bookedField,
        el("div", { class: "field" }, el("label", {}, "Flight starts"), f.start),
        el("div", { class: "field" }, el("label", {}, "Flight ends"), f.end),
        el("div", { class: "field span-2" }, el("label", {}, "Practice specialty"), el("div", { class: "check-grid" }, specChecks)),
        el("div", { class: "field span-2" }, el("label", {}, "States"), el("div", { class: "check-grid states" }, stateChecks)),
        el("div", { class: "field span-2" }, el("label", {}, "Slots"), el("div", { class: "check-grid" }, slotChecks)),
        el("div", { class: "field" }, el("label", {}, "Frequency cap"), f.cap),
        el("div", { class: "field" }, el("label", {}, "Category exclusivity"), el("label", { class: "checkbox" }, f.excl, "One brand per therapeutic category per office")),
        el("div", { class: "field span-2" }, el("label", {}, "NPI target list"),
          el("input", { class: "input", type: "file", disabled: true }),
          el("span", { class: "note" }, "Pending counsel: if cleared, a sponsor's NPI list is used to measure reach only, never to choose who sees an ad.")),
        el("div", { class: "span-2 row" }, el("button", { class: "btn btn-primary", type: "submit" }, "Create campaign"),
          el("span", { class: "note" }, "Targeting uses only the practice's declared specialty and state. Nothing from a visit is ever used."))),
      el("aside", { class: "card card-pad forecast" }, el("h3", {}, "Forecast"), fc)));
  refresh();
  return form;
}

// ---------------------------------------------------------------- creative and compliance
async function creative() {
  const [cr, log] = await Promise.all([api(`${A}/api/creatives`), api(`${A}/api/changelog?limit=60`)]);
  const statusOf = (c) => c.status === "approved" ? badge("Approved", "ok") : c.status === "blocked_boxed_warning" ? badge("Blocked: boxed warning", "critical") : c.status === "expired" ? badge("MLR expired", "warning") : badge(c.status);
  const expiry = (c) => c.mlr_days_left < 0 ? el("span", { class: "muted" }, `${c.mlr_expires} (expired)`) : c.mlr_days_left <= 14 ? el("span", {}, `${c.mlr_expires} `, badge(`${c.mlr_days_left} days left`, "warning")) : c.mlr_expires;
  mount(view,
    section("Creative and compliance", "Every version, its MLR approval and the rules the ad server enforces."),
    el("div", { class: "banner banner-info", style: { marginBottom: "12px" } }, el("ul", { style: { margin: 0, paddingLeft: "18px" } }, cr.rules.map((r) => el("li", {}, r)))),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["Brand", "Version", "Format", "Slots", "MLR approval", "Expires", "Status", "Safety information", "Attestation"].map((h) => el("th", {}, h)))),
      el("tbody", {}, cr.creatives.map((c) => el("tr", {},
        el("td", {}, el("strong", {}, c.brand_name), el("div", { class: "sub" }, c.category)),
        el("td", {}, `v${c.version}`),
        el("td", {}, c.format),
        el("td", {}, c.slots.map((s) => s.replace(/_/g, " ")).join(", ")),
        el("td", { class: "mono nowrap" }, c.mlr_id),
        el("td", { class: "nowrap" }, expiry(c)),
        el("td", {}, statusOf(c)),
        el("td", {}, el("a", { href: `${A}${c.pi_path}`, target: "_blank", rel: "noopener" }, "Prescribing information")),
        el("td", {}, el("div", { class: "sub" }, `${c.approver}, ${c.attested_at}`),
          el("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await api(`${A}/api/creatives/${c.id}/attest`, { method: "POST", body: {} }); toast("Attestation recorded in the change log.", "ok"); creative(); } }, "Record attestation")))))))),
    section("Change log", "Campaign, creative and network changes, newest first."),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["When (UTC)", "Who", "What", "Item", "Detail"].map((h) => el("th", {}, h)))),
      el("tbody", {}, log.entries.map((e) => el("tr", {},
        el("td", { class: "nowrap mono" }, e.at.replace("T", " ").slice(0, 16)), el("td", {}, e.actor), el("td", {}, e.action.replace(/_/g, " ")),
        el("td", {}, `${e.entity} ${e.entity_id}`), el("td", { class: "muted" }, Object.entries(e.detail).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join("/") : v}`).join(" · ")))))))));
}

// ---------------------------------------------------------------- delivery and pacing
function pacingCard(list) {
  const cpm = list.filter((c) => c.contract_type === "cpm" && c.effective_status !== "scheduled");
  return chartCard({
    title: "Pacing: delivered against booked",
    subtitle: "Bar: viewable impressions delivered as a share of booked. Tick: where the campaign should be today.",
    render: (b) => pacingBullets(b, { rows: cpm.map((c) => ({ label: c.name, delivered: c.pacing.delivered, booked: c.pacing.booked, expected: c.pacing.expected_to_date,
      note: ["complete", "ended"].includes(c.pacing.state) || c.effective_status === "ended"
        ? `${pct(c.pacing.delivered_share)} of booked`
        : c.pacing.ratio ? `${pct(c.pacing.ratio)} of expected` : (PACE[c.pacing.state] || [c.pacing.state])[0] })) }),
    table: () => ({ columns: ["Campaign", "Delivered", "Expected to date", "Booked", "Pacing"], rows: cpm.map((c) => [c.name, fmt.int(c.pacing.delivered), fmt.int(c.pacing.expected_to_date), fmt.int(c.pacing.booked), c.pacing.ratio ? pct(c.pacing.ratio) : c.pacing.state]) }),
  });
}

async function delivery() {
  state.meta = null;
  const m = await meta();
  const spons = m.campaigns.filter((c) => c.contract_type === "sponsorship");
  mount(view,
    section("Delivery and pacing", "Flight to date. Pacing uses complete days; today's delivery is still arriving."),
    pacingCard(m.campaigns),
    el("div", { class: "cols-2", style: { marginTop: "16px" } },
      el("section", { class: "card" },
        el("div", { class: "card-head" }, el("div", {}, el("h3", {}, "Flat sponsorships"), el("div", { class: "muted small" }, "Billed per prescriber per month and shown as an effective CPM"))),
        el("div", { class: "table-wrap" }, el("table", { class: "table" },
          el("thead", {}, el("tr", {}, ["Sponsor", "Prescribers", "Fee", "Flight elapsed", "Viewable", "eCPM"].map((h) => el("th", {}, h)))),
          el("tbody", {}, spons.map((c) => el("tr", {},
            el("td", {}, el("strong", {}, c.brand_name), el("div", { class: "sub" }, c.name)),
            el("td", { class: "num" }, fmt.int(c.sponsored_prescribers)),
            el("td", { class: "num" }, money2(c.fee_per_prescriber_month)),
            el("td", { class: "num" }, pct(c.pacing.elapsed_share)),
            el("td", { class: "num" }, fmt.int(c.totals.viewable)),
            el("td", { class: "num" }, money2(c.totals.ecpm)))))))),
      liveCard(true)));
  startLive();
}

// ---------------------------------------------------------------- audience
async function audience() {
  const rep = await scopedReport();
  const a = rep.audience;
  const distCard = chartCard({
    title: "Frequency distribution",
    subtitle: state.scope === "network" ? "Prescribers by viewable impressions received, all brands combined" : "Prescribers by viewable impressions of this brand (cap: 3 a week)",
    render: (b) => columnChart(b, { valueLabel: "prescribers", every: 1, height: 200, data: a.frequency_distribution.map((d) => ({ label: d.bucket, title: `${d.bucket} impressions`, value: d.prescribers, tip: [["prescribers", fmt.int(d.prescribers)]] })) }),
    table: () => ({ columns: ["Viewable impressions", "Prescribers"], rows: a.frequency_distribution.map((d) => [d.bucket, fmt.int(d.prescribers)]) }),
  });
  const split = (title, rows) => chartCard({
    title, subtitle: "Viewable impressions; hover for reach",
    render: (b) => barList(b, { valueLabel: "viewable", data: rows.map((r) => ({ label: r.label, value: r.viewable, tip: [["viewable", fmt.int(r.viewable)], ["served", fmt.int(r.served)], ["prescribers reached", fmt.int(r.reach)]] })) }),
    table: () => ({ columns: [title.split(" ").pop(), "Served", "Viewable", "Reach"], rows: rows.map((r) => [r.label, fmt.int(r.served), fmt.int(r.viewable), fmt.int(r.reach)]) }),
  });
  mount(view,
    el("div", { class: "kpis" },
      stat("Unique prescribers reached", fmt.int(a.reach), `of ${fmt.int(a.network_prescribers)} active, verified prescribers`),
      stat("Average frequency", a.frequency_avg ? a.frequency_avg.toFixed(1) : "–", "viewable impressions per reached prescriber"),
      stat("Target-list reach", "Pending", "counsel decides; measurement only, never targeting")),
    el("div", { class: "cols-2", style: { marginTop: "16px" } }, distCard, split("By practice specialty", a.by_specialty)),
    el("div", { class: "cols-2", style: { marginTop: "16px" } }, split("By state", a.by_state), el("section", { class: "card card-pad stack" },
      el("h3", {}, "Target-list reach (NPI lists)"),
      el("p", {}, "Pending counsel. If cleared, a sponsor uploads its NPI list and we report the share reached, counting only active, verified prescribers."),
      el("p", { class: "note" }, "The list is used for measurement only. It never decides who sees an ad, and no prescriber-level exposure file leaves the network until counsel approves one."))));
}

// ---------------------------------------------------------------- engagement
async function engagement() {
  const rep = await scopedReport();
  const t = rep.totals;
  const list = state.scope === "network" ? rep.campaigns.filter((c) => c.totals.served > 0) : null;
  mount(view,
    el("div", { class: "kpis" },
      stat("Safety-information opens", fmt.int(t.isi_opens), "important safety information expanded"),
      stat("Prescribing-information opens", fmt.int(t.clicks), "link clicks"),
      stat("Time in view", t.time_in_view_s ? `${t.time_in_view_s.toFixed(1)} s` : "–", "average per viewable unit"),
      stat("Hides", fmt.int(t.hides), "reported to the sponsor"),
      stat("Resource opens", fmt.int(t.resource_opens), "from the doctor-opened tray")),
    el("p", { class: "note", style: { margin: "10px 0 0" } }, `Click-through rate ${pct(t.ctr, 2)}, shown for completeness. It misleads for clinician media, so the console does not headline it.`),
    list ? el("section", { class: "card", style: { marginTop: "16px" } }, el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["Campaign", "Viewable", "Safety info opens", "PI opens", "Time in view", "Hides", "CTR"].map((h) => el("th", {}, h)))),
      el("tbody", {}, list.map((c) => el("tr", {},
        el("td", {}, el("strong", {}, c.brand_name), el("div", { class: "sub" }, c.name)),
        el("td", { class: "num" }, fmt.int(c.totals.viewable)), el("td", { class: "num" }, fmt.int(c.totals.isi_opens)),
        el("td", { class: "num" }, fmt.int(c.totals.clicks)),
        el("td", { class: "num" }, c.totals.time_in_view_s ? `${c.totals.time_in_view_s.toFixed(1)} s` : "–"),
        el("td", { class: "num" }, fmt.int(c.totals.hides)), el("td", { class: "num" }, pct(c.totals.ctr, 2)))))))) : null,
    el("section", { class: "card card-pad stack", style: { marginTop: "16px" } },
      el("h3", {}, "Outcomes"),
      el("p", {}, "No prescription-lift panel. Partner-run lift studies are common in clinician media, but tying ad value to prescribing is the Practice Fusion risk, so US healthcare counsel decides whether and how they ever appear.")));
}

// ---------------------------------------------------------------- verification
async function verification() {
  const v = await api(`${A}/api/verification`);
  const result = el("div", { id: "verify-result" }, v.last_verification ? verifyResult(v.last_verification) : el("p", { class: "muted" }, "Not verified yet in this session."));
  mount(view,
    section("Verification", "How every number is counted, and proof the event log has not been edited."),
    el("div", { class: "cols-2" },
      el("section", { class: "card card-pad stack" },
        el("h3", {}, "Event-log fingerprint"),
        el("dl", { class: "kv" },
          el("dt", {}, "Events logged"), el("dd", {}, fmt.int(v.fingerprint.events)),
          el("dt", {}, "First event"), el("dd", {}, v.fingerprint.first_at || "–"),
          el("dt", {}, "Latest event"), el("dd", {}, v.fingerprint.last_at || "–"),
          el("dt", {}, "Method"), el("dd", {}, v.fingerprint.algorithm)),
        el("div", { class: "label" }, "Head hash"), el("div", { class: "hash" }, v.fingerprint.head_hash),
        el("div", { class: "row" }, el("button", { class: "btn btn-primary", onclick: async () => {
          mount(result, el("span", { class: "spinner" }));
          mount(result, verifyResult(await api(`${A}/api/verify-chain`, { method: "POST" })));
        } }, "Verify the chain now")),
        result),
      el("section", { class: "card card-pad stack" },
        el("h3", {}, "Audit status"),
        el("table", { class: "table" }, el("tbody", {}, v.audit_status.map((a) => el("tr", {}, el("td", {}, a.name), el("td", {}, a.status))))),
        el("p", { class: "note" }, v.history_note),
        el("h3", { style: { marginTop: "8px" } }, "Privacy firewall"),
        el("ul", { style: { margin: 0, paddingLeft: "18px" } }, v.privacy.map((p) => el("li", {}, p))))),
    section("Metric dictionary", "One definition for each number, used by the console, the sponsor report and every export."),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, el("th", {}, "Metric"), el("th", {}, "How it is counted"))),
      el("tbody", {}, v.method.map(([, label, def]) => el("tr", {}, el("td", { class: "nowrap" }, el("strong", {}, label)), el("td", {}, def))))))));
}

function verifyResult(r) {
  const chain = r.chain.ok
    ? el("div", { class: "banner banner-ok" }, `Chain intact: ${fmt.int(r.chain.checked)} events checked, each linked to the one before.`)
    : el("div", { class: "banner banner-critical" }, `Chain broken at event ${r.chain.broken_at}: ${r.chain.reason}. ${fmt.int(r.chain.checked)} events before it verified.`);
  const rec = r.reconcile.ok
    ? el("div", { class: "banner banner-ok" }, `Live roll-ups match the log (${fmt.int(r.reconcile.rows_checked)} rows).`)
    : el("div", { class: "banner banner-warning" }, `${fmt.int(r.reconcile.mismatched)} roll-up rows differ from the log.`);
  return el("div", { class: "stack" }, chain, rec, el("p", { class: "note" }, `Checked ${r.at}`));
}

// ---------------------------------------------------------------- sharing
async function sharing() {
  state.meta = null;
  const m = await meta();
  mount(view,
    section("Sharing", "Read-only report links for each sponsor, day-level CSV and print-to-PDF.",
      el("a", { class: "btn", href: `${A}/api/export.csv?days=${state.days}` }, "Network CSV (day level)")),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["Campaign", "Read-only link", "", "Exports"].map((h) => el("th", {}, h)))),
      el("tbody", {}, m.campaigns.filter((c) => c.effective_status !== "scheduled").map((c) => {
        const url = `${location.origin}/report/?share=${encodeURIComponent(c.share_token)}`;
        return el("tr", {},
          el("td", {}, el("strong", {}, c.name), el("div", { class: "sub" }, `${c.brand_name} · ${c.company}`)),
          el("td", { class: "mono", style: { maxWidth: "320px" } }, url),
          el("td", { class: "nowrap" },
            el("button", { class: "btn btn-sm", onclick: async () => { try { await navigator.clipboard.writeText(url); toast("Link copied.", "ok"); } catch { toast("Copy failed; select the link instead.", "error"); } } }, "Copy"),
            el("a", { class: "btn btn-sm btn-ghost", href: url, target: "_blank", rel: "noopener" }, "Open"),
            el("button", { class: "btn btn-sm btn-ghost", onclick: async () => { await api(`${A}/api/campaigns/${c.id}/share/rotate`, { method: "POST" }); toast("New link issued; the old one stops working.", "ok"); sharing(); } }, "Revoke and reissue")),
          el("td", { class: "nowrap" },
            el("a", { class: "btn btn-sm", href: `${A}/api/export.csv?campaign_id=${encodeURIComponent(c.id)}` }, "CSV"),
            el("a", { class: "btn btn-sm btn-ghost", href: `${url}&print=1`, target: "_blank", rel: "noopener" }, "PDF")));
      }))))),
    el("section", { class: "card card-pad stack", style: { marginTop: "16px" } },
      el("h3", {}, "NPI exposure files"),
      el("p", {}, "Pending counsel. Exposure files feed prescription-lift studies, so none is produced until US healthcare counsel approves the design."),
      el("p", { class: "note" }, "Every export carries day-level counts only: no times of day, no visit identifiers, no prescriber identifiers.")));
}

// ---------------------------------------------------------------- start
(async function start() {
  try { await requireSession(); } catch { return; }
  renderHeader("network", { simulated: true, subtitle: "Network console" });
  window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (t && t !== state.tab) { state.tab = t; go(); } });
  go();
})();
