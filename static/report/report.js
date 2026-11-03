import { el, mount, fmt, renderHeader, api } from "/js/common.js";
import { columnChart, barList, chartCard, compact } from "/console/charts.js";

const A = (window.MEDSCRIBE_ADZONE_BASE || "/adzone").replace(/\/$/, "");
const app = document.getElementById("app");
const params = new URLSearchParams(location.search);
const share = params.get("share");
const pct = (v, d = 0) => fmt.pct(v, d);
const money2 = (v) => fmt.money(v, 2);
const dayLabel = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const dateLong = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

function stat(label, value, sub) {
  return el("div", { class: "card stat" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value), sub ? el("div", { class: "sub" }, sub) : null);
}

// A share link is read-only and needs no access code, so it never calls the session API
// (which would bounce a pharma viewer to the access-code page).
function shareHeader() {
  document.body.prepend(
    el("header", { class: "share-header no-print" },
      el("span", { class: "wordmark" }, el("span", { class: "mark", "aria-hidden": "true" }, "M"), el("span", {}, "CustomAi MedScribe")),
      el("span", { class: "badge" }, "Read-only sponsor report"),
      el("span", { class: "header-spacer" }),
      el("span", { class: "badge badge-sim" }, "Simulated data")));
  document.body.insertBefore(el("div", { class: "sim-strip" }, "Simulated data: fictional brand, synthetic offices and prescribers. Not real campaign results."), document.querySelector("header").nextSibling);
}

function campaignView(rep, { shared }) {
  const c = rep.campaign, t = rep.totals, p = rep.pacing, a = rep.audience, pr = rep.practices;
  const cpm = c.contract_type === "cpm";
  const csvHref = shared ? `${A}/share/${encodeURIComponent(share)}/export.csv` : null;
  const daily = rep.daily;
  const head = el("div", { class: "report-head" },
    el("div", {},
      el("div", { class: "eyebrow muted small" }, `${c.brand_name} · ${c.company} · ${c.category}`),
      el("h1", {}, c.name),
      el("div", { class: "meta" },
        el("span", {}, `Flight ${dateLong(c.start_date)} to ${dateLong(c.end_date)}`),
        el("span", {}, cpm ? `CPM ${money2(c.cpm)} on viewable impressions` : `Flat sponsorship, ${money2(c.fee_per_prescriber_month)} per prescriber a month`),
        el("span", {}, `Report through ${dateLong(rep.period.end)}`))),
    el("div", { class: "toolbar no-print" },
      csvHref ? el("a", { class: "btn", href: csvHref }, "Day-level CSV") : null,
      el("button", { class: "btn btn-primary", onclick: () => window.print() }, "Print or save as PDF")));
  const kpis = el("div", { class: "kpis" },
    cpm
      ? stat("Delivered vs booked", `${compact(p.delivered)} of ${compact(p.booked)}`, `viewable impressions · ${pct(p.delivered_share)} of booking`)
      : stat("Sponsored prescribers", fmt.int(c.sponsored_prescribers), `${pct(p.elapsed_share)} of the flight elapsed`),
    cpm ? stat("Pacing", p.ratio ? pct(p.ratio) : "–", "of the volume expected by today") : stat("Viewable impressions", compact(t.viewable), `${fmt.int(t.served)} served`),
    stat("Viewability", pct(t.viewability, 1), "50% in view for 1 second"),
    stat("Unique prescribers reached", fmt.int(a.reach), "active, verified prescribers"),
    stat("Average frequency", a.frequency_avg ? a.frequency_avg.toFixed(1) : "–", "capped at 3 a week"),
    stat("Spend", fmt.money(t.spend), cpm ? "capped at the booked volume" : "flat fee, pro-rated by day"),
    stat(cpm ? "CPM" : "Effective CPM", money2(t.ecpm), "per 1,000 viewable impressions"),
    stat("Safety-information opens", fmt.int(t.isi_opens), `${fmt.int(t.clicks)} prescribing-information opens`),
    stat("Time in view", t.time_in_view_s ? `${t.time_in_view_s.toFixed(1)} s` : "–", `${fmt.int(t.hides)} hides by doctors`));
  const dailyCard = chartCard({
    title: "Viewable impressions per day",
    subtitle: "Valid traffic only; test and invalid traffic removed",
    render: (b) => columnChart(b, { valueLabel: "viewable impressions", every: daily.length > 40 ? 10 : daily.length > 14 ? 5 : 2,
      data: daily.map((d) => ({ label: dayLabel(d.date), title: dateLong(d.date), value: d.viewable,
        tip: [["viewable", fmt.int(d.viewable)], ["served", fmt.int(d.served)], ["viewability", d.served ? pct(d.viewable / d.served, 1) : "–"], ["spend", money2(d.spend)]] })) }),
    table: () => ({ columns: ["Date", "Served", "Viewable", "Viewability", "Spend"], rows: daily.map((d) => [d.date, fmt.int(d.served), fmt.int(d.viewable), d.served ? pct(d.viewable / d.served, 1) : "–", money2(d.spend)]) }),
  });
  const freqCard = chartCard({
    title: "Frequency distribution",
    subtitle: "Prescribers by viewable impressions received",
    render: (b) => columnChart(b, { valueLabel: "prescribers", every: 1, height: 200,
      data: a.frequency_distribution.map((d) => ({ label: d.bucket, title: `${d.bucket} impressions`, value: d.prescribers, tip: [["prescribers", fmt.int(d.prescribers)]] })) }),
    table: () => ({ columns: ["Viewable impressions", "Prescribers"], rows: a.frequency_distribution.map((d) => [d.bucket, fmt.int(d.prescribers)]) }),
  });
  const split = (title, rows, col) => chartCard({
    title, subtitle: "Viewable impressions; hover for reach",
    render: (b) => barList(b, { valueLabel: "viewable", data: rows.map((r) => ({ label: r.label, value: r.viewable, tip: [["viewable", fmt.int(r.viewable)], ["prescribers reached", fmt.int(r.reach)]] })) }),
    table: () => ({ columns: [col, "Served", "Viewable", "Prescribers reached"], rows: rows.map((r) => [r.label, fmt.int(r.served), fmt.int(r.viewable), fmt.int(r.reach)]) }),
  });
  return [
    head, kpis,
    el("div", { class: "section-title" }, el("h2", {}, "Delivery")),
    dailyCard,
    el("div", { class: "cols-2", style: { marginTop: "16px" } }, freqCard, split("By practice specialty", a.by_specialty, "Specialty")),
    el("div", { class: "cols-2", style: { marginTop: "16px" } }, split("By state", a.by_state, "State"), split("By slot", a.by_slot, "Slot")),
    el("div", { class: "cols-2", style: { marginTop: "16px" } },
      el("section", { class: "card card-pad stack" },
        el("h3", {}, "Practices"),
        el("dl", { class: "kv" },
          el("dt", {}, "Practices with delivery"), el("dd", {}, `${fmt.int(pr.active_practices)} of ${fmt.int(pr.network_offices)} in the network`),
          el("dt", {}, "Devices"), el("dd", {}, fmt.int(pr.devices)),
          el("dt", {}, "Practice-days with delivery"), el("dd", {}, fmt.int(pr.days_with_delivery)),
          el("dt", {}, "Share of open days played"), el("dd", {}, pct(pr.share_of_open_days_played))),
        el("h3", { style: { marginTop: "8px" } }, "Target-list reach"),
        el("p", { class: "muted" }, "Pending counsel. If cleared, reach against your NPI list is reported here, counting active, verified prescribers only.")),
      el("section", { class: "card card-pad stack" },
        el("h3", {}, "Invalid and test traffic"),
        el("dl", { class: "kv" },
          el("dt", {}, "Invalid traffic removed"), el("dd", {}, `${fmt.int(t.ivt_removed)} (${pct(t.ivt_rate, 1)})`),
          el("dt", {}, "Test traffic excluded"), el("dd", {}, fmt.int(t.test_excluded))),
        el("p", { class: "note" }, "Removed before any number on this page: duplicates, background tabs, automated clients, viewable without a render, test and staff accounts."),
        el("p", { class: "note" }, "This report carries day-level counts only. It never shows times of day, visit details or patient information, because the ad network never receives them."))),
    el("div", { class: "section-title" }, el("h2", {}, "Method")),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", { class: "table method" },
      el("tbody", {}, Object.values(rep.definitions).map((d) => el("tr", {}, el("td", {}, el("strong", {}, d.label)), el("td", {}, d.definition))))))),
    el("p", { class: "footer-note" }, "Simulated data for a product demo. The brand, company, offices and prescribers are fictional. Audit status: POCMA/AAM not yet audited."),
  ];
}

async function sponsorList() {
  const data = await api(`${A}/api/sponsor/campaigns`);
  const pick = el("select", { class: "select", style: { width: "auto" } }, el("option", { value: "" }, "All sponsors"), data.companies.map((c) => el("option", { value: c }, c)));
  const grid = el("div", { class: "campaign-tiles" });
  const draw = () => {
    const list = data.campaigns.filter((c) => !pick.value || c.company === pick.value);
    mount(grid, list.map((c) => el("section", { class: "card campaign-tile" },
      el("div", {}, el("div", { class: "muted small" }, `${c.company} · ${c.category}`), el("h3", {}, `${c.brand_name}: ${c.name}`)),
      el("div", { class: "row-kpis" },
        el("div", {}, el("div", { class: "v" }, compact(c.totals.viewable)), el("div", { class: "l" }, "viewable")),
        el("div", {}, el("div", { class: "v" }, pct(c.totals.viewability, 1)), el("div", { class: "l" }, "viewability")),
        el("div", {}, el("div", { class: "v" }, money2(c.totals.ecpm)), el("div", { class: "l" }, c.contract_type === "cpm" ? "CPM" : "effective CPM"))),
      el("div", { class: "row between" },
        el("span", { class: "muted small" }, `${dayLabel(c.start_date)} – ${dayLabel(c.end_date)}`),
        el("a", { class: "btn btn-sm btn-primary", href: `/report/?share=${encodeURIComponent(c.share_token)}` }, "Open report")))));
  };
  pick.addEventListener("change", draw);
  draw();
  mount(app,
    el("div", { class: "page-title" },
      el("div", {}, el("div", { class: "eyebrow" }, "What a pharma brand team sees"), el("h1", {}, "Sponsor reports")),
      el("div", { class: "row" }, el("label", { class: "label" }, "Viewing as"), pick)),
    el("p", { class: "muted" }, "Each report also has a read-only link a sponsor can open without the demo access code."),
    grid);
}

(async function start() {
  if (share) {
    shareHeader();
    try {
      const r = await fetch(`${A}/share/${encodeURIComponent(share)}`);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "This report link is not valid.");
      const rep = await r.json();
      document.title = `${rep.campaign.brand_name} report · CustomAi MedScribe`;
      mount(app, ...campaignView(rep, { shared: true }));
      if (params.get("print") === "1") setTimeout(() => window.print(), 600);
    } catch (err) {
      mount(app, el("div", { class: "banner banner-critical" }, err.message));
    }
    return;
  }
  try { await api("/api/me"); } catch { return; }
  renderHeader("sponsor", { simulated: true, subtitle: "Sponsor report" });
  try { await sponsorList(); } catch (err) { mount(app, el("div", { class: "banner banner-critical" }, err.message)); }
})();
