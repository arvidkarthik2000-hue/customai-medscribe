// Pharmacy tools: interaction checker, Medicare formulary lookup, calculators.
// No sponsored content appears anywhere on this page.
import { api, el, mount, $, $$, toast, renderHeader, requireSession, debounce } from "/js/common.js";

const SEVERITY_LABEL = { critical: "Critical", warning: "Warning", info: "Info" };
const SEVERITY_BADGE = { critical: "badge-critical", warning: "badge-warning", info: "badge-info" };
const CATEGORY_LABEL = {
  interaction: "Interaction", qt: "QT", allergy: "Allergy", condition: "Condition", renal: "Kidney function",
  dose: "Dose", duplicate: "Duplicate therapy", older_adult: "Older adult", formulary: "Formulary",
  calculator: "Calculator", prevention: "Prevention", name_check: "Name check",
};
const FREQUENCIES = ["", "once daily", "twice daily", "three times daily", "four times daily", "every 6 hours as needed", "at bedtime", "once weekly"];
const UNITS = ["mg", "mcg", "g", "units", "mL", "tablet"];

const PRESETS = [
  { label: "Warfarin + ibuprofen", meds: [{ name: "warfarin", dose: "5", unit: "mg", frequency: "once daily" }, { name: "ibuprofen", dose: "600", unit: "mg", frequency: "three times daily" }], ctx: { age: 71, sex: "F", problems: "atrial fibrillation, hypertension", plan_key: "PDP-A" } },
  { label: "Simvastatin + clarithromycin", meds: [{ name: "simvastatin", dose: "40", unit: "mg", frequency: "at bedtime" }, { name: "clarithromycin", dose: "500", unit: "mg", frequency: "twice daily" }], ctx: { age: 59, sex: "M" } },
  { label: "Sertraline + tramadol", meds: [{ name: "sertraline", dose: "100", unit: "mg", frequency: "once daily" }, { name: "tramadol", dose: "50", unit: "mg", frequency: "every 6 hours as needed" }], ctx: { age: 52, sex: "F" } },
  { label: "Metformin at eGFR 28", meds: [{ name: "metformin", dose: "1000", unit: "mg", frequency: "twice daily" }], ctx: { age: 68, sex: "M", egfr: 28, problems: "type 2 diabetes, chronic kidney disease", plan_key: "MAPD-B" } },
  { label: "Sleep aid at 82", meds: [{ name: "diphenhydramine", dose: "50", unit: "mg", frequency: "at bedtime" }, { name: "lorazepam", dose: "0.5", unit: "mg", frequency: "at bedtime" }], ctx: { age: 82, sex: "F", plan_key: "PDP-A" } },
  { label: "Apixaban at 82, 58 kg", meds: [{ name: "apixaban", dose: "5", unit: "mg", frequency: "twice daily" }], ctx: { age: 82, sex: "F", weight_kg: 58, creatinine: 0.9, problems: "atrial fibrillation", plan_key: "PDP-A" } },
  { label: "Penicillin allergy", meds: [{ name: "amoxicillin-clavulanate", dose: "875", unit: "mg", frequency: "twice daily" }], ctx: { age: 34, sex: "F", allergies: "penicillin (hives)" } },
];

const state = { meds: [], plans: [], result: null, checking: false, seq: 0, attribution: "" };
const show = (g) => (g ? String(g).replace(/-/g, "/") : g); // combination generics are stored as "a-b"

// ------------------------------------------------------------------ search-as-you-type
function attachSearch(input, onPick) {
  const wrap = input.parentElement;
  let box = null, items = [], active = -1, lastQ = "";
  const close = () => { if (box) { box.remove(); box = null; } items = []; active = -1; };
  const render = () => {
    if (!items.length) { close(); return; }
    if (!box) { box = el("div", { class: "ph-suggest", role: "listbox" }); wrap.appendChild(box); }
    mount(box, items.map((it, i) => el("button", {
      type: "button", role: "option", class: i === active ? "active" : "",
      onmousedown: (e) => { e.preventDefault(); pick(i); },
    }, el("span", {}, it.label || it.name), el("span", { class: "tty" }, it.tty === "BN" ? "Brand" : it.source === "RxNorm" ? "RxNorm" : "Generic"))));
  };
  const pick = (i) => { const it = items[i]; close(); if (it) onPick(it); };
  const fetchSuggest = debounce(async () => {
    const q = input.value.trim();
    lastQ = q;
    if (q.length < 2) { close(); return; }
    try {
      const data = await api(`/api/drugs/search?q=${encodeURIComponent(q)}`);
      if (input.value.trim() !== lastQ) return;
      items = data.results || []; active = -1; render();
    } catch { close(); }
  }, 180);
  input.addEventListener("input", fetchSuggest);
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    if (!box) return;
    if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(items.length - 1, active + 1); render(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); render(); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); pick(active); }
    else if (e.key === "Escape") { close(); }
  });
  return { close };
}

// ------------------------------------------------------------------ checker
function field(label, control, cls = "") {
  return el("div", { class: `field ${cls}` }, el("label", { for: control.id }, label), control);
}
function select(id, options, value = "") {
  const s = el("select", { class: "select", id }, options.map((o) => {
    const [v, t] = Array.isArray(o) ? o : [o, o || "—"];
    return el("option", { value: v, selected: v === value }, t);
  }));
  return s;
}

function buildChecker() {
  const panel = $("#panel-check");
  const nameInput = el("input", { class: "input", id: "med-name", placeholder: "Type a medicine, e.g. warfarin or apixaban", autocomplete: "off" });
  const doseInput = el("input", { class: "input", id: "med-dose", placeholder: "Dose", inputmode: "decimal" });
  const unitSel = select("med-unit", UNITS, "mg");
  const freqSel = select("med-freq", FREQUENCIES.map((f) => [f, f || "Frequency"]));
  const addBtn = el("button", { class: "btn btn-primary", type: "submit" }, "Add");

  const addForm = el("form", { class: "ph-add", onsubmit: (e) => { e.preventDefault(); addFromInputs(); } },
    el("div", { class: "field ph-search-field" }, el("label", { for: "med-name" }, "Medicine"), el("div", { class: "ph-search" }, nameInput)),
    field("Dose", doseInput), field("Unit", unitSel), field("Frequency", freqSel, "ph-freq"), addBtn);

  const search = attachSearch(nameInput, (it) => { nameInput.value = it.name; doseInput.focus(); });

  function addFromInputs() {
    const name = nameInput.value.trim();
    if (!name) { nameInput.focus(); return; }
    state.meds.push({ name, dose: doseInput.value.trim() || null, unit: doseInput.value.trim() ? unitSel.value : null, frequency: freqSel.value || null });
    nameInput.value = ""; doseInput.value = ""; freqSel.value = "";
    search.close();
    renderChips(); scheduleCheck();
    nameInput.focus();
  }

  const ctx = {
    age: el("input", { class: "input", id: "ctx-age", type: "number", min: "0", max: "120", placeholder: "e.g. 72" }),
    sex: select("ctx-sex", [["", "Not set"], ["F", "Female"], ["M", "Male"]]),
    egfr: el("input", { class: "input", id: "ctx-egfr", type: "number", min: "1", max: "200", placeholder: "mL/min/1.73 m²" }),
    creatinine: el("input", { class: "input", id: "ctx-cr", type: "number", step: "0.01", min: "0.1", max: "30", placeholder: "mg/dL" }),
    weight: el("input", { class: "input", id: "ctx-wt", type: "number", step: "0.1", min: "1", max: "400", placeholder: "kg" }),
    potassium: el("input", { class: "input", id: "ctx-k", type: "number", step: "0.1", min: "1", max: "10", placeholder: "mmol/L" }),
    sodium: el("input", { class: "input", id: "ctx-na", type: "number", step: "1", min: "100", max: "180", placeholder: "mmol/L" }),
    allergies: el("input", { class: "input", id: "ctx-allergies", placeholder: "e.g. penicillin, sulfa" }),
    problems: el("input", { class: "input", id: "ctx-problems", placeholder: "e.g. heart failure, asthma, CKD" }),
    plan: select("ctx-plan", [["", "None"]]),
    pregnant: el("input", { type: "checkbox", id: "ctx-preg" }),
    prevention: el("input", { type: "checkbox", id: "ctx-prev" }),
  };
  state.ctx = ctx;
  for (const node of Object.values(ctx)) node.addEventListener("change", () => scheduleCheck());

  const context = el("details", { class: "ph-context", id: "ph-context" },
    el("summary", {}, "Patient details (optional)"),
    el("p", { class: "muted small", style: { margin: "8px 0 0" } }, "Adds allergy, condition, kidney, older-adult and formulary checks. eGFR is computed with CKD-EPI 2021 when only creatinine, age and sex are given; creatinine clearance (Cockcroft-Gault) needs weight too."),
    el("div", { class: "ph-context-grid" },
      field("Age", ctx.age), field("Sex", ctx.sex), field("Medicare plan", ctx.plan),
      field("eGFR", ctx.egfr), field("Creatinine", ctx.creatinine), field("Weight", ctx.weight),
      field("Potassium", ctx.potassium), field("Sodium", ctx.sodium), el("div"),
      field("Allergies (comma-separated)", ctx.allergies, "wide"),
      field("Conditions (comma-separated)", ctx.problems, "wide"),
      el("label", { class: "checkbox", for: "ctx-preg" }, ctx.pregnant, "Pregnant"),
      el("label", { class: "checkbox wide", for: "ctx-prev" }, ctx.prevention, "Include adult screening and vaccine reminders")));

  const presets = el("div", { class: "ph-presets" }, el("span", { class: "muted small" }, "Examples:"),
    PRESETS.map((p) => el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: () => loadPreset(p) }, p.label)));

  const left = el("div", { class: "card card-pad" },
    el("div", { class: "row between", style: { marginBottom: "12px" } }, el("h2", {}, "Medicines"),
      el("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: () => { state.meds = []; renderChips(); renderResults(); } }, "Clear")),
    addForm,
    el("div", { class: "ph-chips", id: "ph-chips" }),
    presets,
    context,
    el("div", { class: "row", style: { marginTop: "14px" } },
      el("button", { class: "btn btn-primary", type: "button", id: "ph-check-btn", onclick: () => runCheck() }, "Check medicines")));

  const right = el("div", { class: "card card-pad", id: "ph-results", "aria-live": "polite" });
  mount(panel, el("div", { class: "ph-layout" }, left, right));
  renderChips();
  renderResults();
}

function loadPreset(p) {
  state.meds = p.meds.map((m) => ({ ...m }));
  const c = state.ctx;
  c.age.value = p.ctx.age ?? ""; c.sex.value = p.ctx.sex ?? ""; c.egfr.value = p.ctx.egfr ?? ""; c.creatinine.value = p.ctx.creatinine ?? "";
  c.weight.value = p.ctx.weight_kg ?? ""; c.potassium.value = p.ctx.potassium ?? ""; c.sodium.value = p.ctx.sodium ?? "";
  c.allergies.value = p.ctx.allergies ?? ""; c.problems.value = p.ctx.problems ?? ""; c.plan.value = p.ctx.plan_key ?? "";
  c.pregnant.checked = false;
  if (p.ctx.age || p.ctx.allergies || p.ctx.problems) $("#ph-context").open = true;
  renderChips(); runCheck();
}

function renderChips() {
  const box = $("#ph-chips");
  if (!state.meds.length) { mount(box, el("span", { class: "ph-empty-chips" }, "No medicines yet. Add one above or try an example.")); return; }
  mount(box, state.meds.map((m, i) => el("span", { class: "ph-chip" },
    m.name,
    m.dose ? el("span", { class: "dose" }, `${m.dose} ${m.unit || ""}${m.frequency ? ", " + m.frequency : ""}`) : (m.frequency ? el("span", { class: "dose" }, m.frequency) : null),
    el("button", { type: "button", "aria-label": `Remove ${m.name}`, title: "Remove", onclick: () => { state.meds.splice(i, 1); renderChips(); scheduleCheck(); } }, "×"))));
}

function splitList(s) { return (s || "").split(/[,;\n]/).map((x) => x.trim()).filter(Boolean); }

function contextPayload() {
  const c = state.ctx;
  const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
  const p = {
    age: num(c.age.value), sex: c.sex.value || null, egfr: num(c.egfr.value), creatinine: num(c.creatinine.value),
    weight_kg: num(c.weight.value), potassium: num(c.potassium.value), sodium: num(c.sodium.value),
    allergies: splitList(c.allergies.value), problems: splitList(c.problems.value), plan_key: c.plan.value || null,
    pregnant: c.pregnant.checked,
  };
  return p;
}

const scheduleCheck = debounce(() => { if (state.meds.length) runCheck(); else { state.result = null; renderResults(); } }, 350);

async function runCheck() {
  if (!state.meds.length) { toast("Add at least one medicine."); return; }
  const seq = ++state.seq;
  state.checking = true; renderResults();
  try {
    const body = { medications: state.meds.map((m) => ({ name: m.name, dose: m.dose, unit: m.unit, frequency: m.frequency })), patient: contextPayload(), include_prevention: state.ctx.prevention.checked };
    const data = await api("/api/drugs/check", { method: "POST", body });
    if (seq !== state.seq) return;
    state.result = data;
  } catch (err) {
    if (seq !== state.seq) return;
    state.result = { error: err.message };
  } finally {
    if (seq === state.seq) { state.checking = false; renderResults(); }
  }
}

function sourceLine(src) {
  if (!src) return null;
  const when = src.date ? (src.date_type === "label_effective" ? `label effective ${src.date}` : `reviewed ${src.date}`) : "";
  const label = src.url ? el("a", { href: src.url, target: "_blank", rel: "noopener noreferrer" }, src.label) : el("span", {}, src.label);
  return el("span", {}, "Source: ", label, when ? ` · ${when}` : "");
}

function renderCard(c) {
  const [main, also] = (c.detail || "").split(" Also: ");
  const body = [];
  if (c.category === "renal") {
    body.push(el("p", { style: { margin: 0 } }, el("strong", {}, "Label guidance: "), main));
    body.push(el("p", { style: { margin: 0 } }, el("strong", {}, "What to do: "), c.management));
  } else if (c.effect && c.management) {
    if (c.condition) body.push(el("p", { class: "small ink-2", style: { margin: 0 } }, `From the patient details: ${c.condition}`));
    body.push(el("p", { style: { margin: 0 } }, el("strong", {}, "What happens: "), c.effect));
    body.push(el("p", { style: { margin: 0 } }, el("strong", {}, "What to do: "), c.management));
  } else {
    body.push(el("p", { style: { margin: 0 } }, main));
  }
  if (also) body.push(el("p", { class: "small ink-2", style: { margin: 0 } }, el("strong", {}, "Also: "), also.replace(/\.$/, "")));
  const vals = [];
  if (c.potassium != null) vals.push(`Potassium ${c.potassium} mmol/L`);
  if (c.sodium != null) vals.push(`Sodium ${c.sodium} mmol/L`);
  if (c.crcl != null) vals.push(`CrCl about ${c.crcl} mL/min${c.crcl_basis ? ` (${c.crcl_basis})` : ""}`);
  if (Array.isArray(c.criteria_met) && c.criteria_met.length) vals.push(`Label criteria met: ${c.criteria_met.join(", ")}`);
  if (vals.length) body.push(el("p", { class: "small ink-2", style: { margin: 0 } }, vals.join(" · ")));
  const alts = c.alternatives && c.alternatives.length
    ? el("div", { class: "alts" }, "Alternatives on this plan: ", c.alternatives.map((a) => `${show(a.drug)} (tier ${a.tier}${a.prior_auth ? ", PA" : ""}${a.step_therapy ? ", ST" : ""})`).join(", "))
    : null;
  return el("article", { class: `cds cds-${c.indicator} ph-card` },
    el("div", { class: "cds-top" },
      el("div", { class: "cds-summary" }, c.summary),
      el("div", { class: "cds-badges" },
        el("span", { class: `badge ${SEVERITY_BADGE[c.indicator] || ""}` }, SEVERITY_LABEL[c.indicator] || c.indicator),
        el("span", { class: "badge" }, CATEGORY_LABEL[c.category] || c.category),
        c.onc_high_priority ? el("span", { class: "badge badge-brand", title: "ONC high-priority interaction list (Phansalkar 2012)" }, "ONC high-priority") : null,
        c.context === "existing" ? el("span", { class: "badge", title: "Both medicines were already on the list before this change" }, "Existing") : null)),
    el("div", { class: "cds-body" }, body),
    alts,
    c.meds && c.meds.length ? el("div", { class: "cds-meds" }, c.meds.map((m) => el("span", {}, show(m)))) : null,
    el("div", { class: "cds-foot" }, sourceLine(c.source), c.grading ? el("span", {}, c.grading) : null));
}

function matchBadge(m) {
  const s = m.status;
  const cls = s === "matched" ? "badge-ok" : s === "ambiguous" ? "badge-warning" : "badge-critical";
  return el("span", { class: `badge ${cls}` }, s === "matched" ? "Matched" : s === "ambiguous" ? "Check name" : "Not matched");
}

function formularyCell(f) {
  if (!f) return el("span", { class: "muted" }, "No plan set");
  if (f.available === false) return el("span", { class: "muted small" }, f.note || "Not available");
  if (f.on_formulary === false) return el("span", {}, el("span", { class: "badge badge-warning" }, "Not covered"), el("span", { class: "sub" }, f.note || ""));
  if (f.on_formulary === null || f.on_formulary === undefined) return el("span", { class: "muted small" }, f.note || "Not checked");
  const flags = [f.prior_auth ? "PA" : null, f.step_therapy ? "ST" : null, f.quantity_limit ? "QL" : null].filter(Boolean);
  return el("span", {}, `Tier ${f.tier} · ${f.tier_label}`, flags.length ? el("span", { class: "sub" }, flags.join(" · ") + (f.ql_detail ? ` (${f.ql_detail})` : "")) : null);
}

function renderMedsTable(rows) {
  return el("div", { class: "table-wrap" }, el("table", { class: "table ph-meds-table" },
    el("thead", {}, el("tr", {}, ["Medicine", "Match", "Flags", "Dose", "Formulary"].map((h) => el("th", {}, h)))),
    el("tbody", {}, rows.map((r) => {
      const m = r.match || {};
      const flags = [r.high_risk ? el("span", { class: "badge badge-critical", title: r.high_risk_reason || "" }, "High risk") : null,
        r.schedule ? el("span", { class: "badge" }, r.schedule) : null,
        r.boxed_warning ? el("span", { class: "badge", title: "The FDA label carries a boxed warning" }, "Boxed warning") : null].filter(Boolean);
      const dose = r.dose_check || {};
      return el("tr", {},
        el("td", {}, el("strong", {}, show(m.ingredient) || r.name), m.ingredient && m.ingredient !== r.name ? el("span", { class: "sub" }, `entered as “${r.name}”`) : null,
          m.display ? el("span", { class: "sub" }, m.display) : null, m.rxcui ? el("span", { class: "sub mono" }, `RxCUI ${m.rxcui}`) : null),
        el("td", { "data-label": "Match" }, matchBadge(m), m.reason ? el("span", { class: "sub" }, m.reason) : null,
          m.candidates && m.candidates.length && m.status !== "matched" ? el("span", { class: "sub" }, "Options: " + m.candidates.map((c) => c.name).join(", ")) : null),
        el("td", { "data-label": "Flags" }, flags.length ? el("div", { class: "row-wrap" }, flags) : el("span", { class: "muted" }, "—")),
        el("td", { "data-label": "Dose" }, dose.status === "above_max" ? el("span", { class: "badge badge-warning" }, "Above max") : dose.status === "ok" ? el("span", { class: "badge badge-ok" }, "Within label max") : el("span", { class: "muted small" }, "Not checked"),
          dose.detail ? el("span", { class: "sub" }, dose.detail) : null),
        el("td", { "data-label": "Formulary" }, formularyCell(r.formulary)));
    }))));
}

function renderSources(list) {
  return el("ul", { class: "ph-sources" }, (list || []).map((s) => el("li", {},
    el("span", { class: `st-${s.status}` }, s.status === "ok" ? "Checked" : s.status === "not_used" ? "Not needed" : "Unavailable"),
    ` · ${s.name}`, s.detail ? el("span", { class: "muted" }, ` — ${s.detail}`) : null)));
}

function renderResults() {
  const box = $("#ph-results");
  if (!box) return;
  if (state.checking) {
    mount(box, el("div", { class: "ph-loading" }, el("span", { class: "spinner" }), "Checking curated rules, RxNorm and FDA labels…"));
    return;
  }
  const r = state.result;
  if (!r) {
    mount(box, el("div", { class: "empty" },
      el("h3", {}, "Results appear here"),
      el("p", { class: "muted" }, "Add two or more medicines to check interactions; one is enough for allergy, condition, kidney and dose checks.")));
    return;
  }
  if (r.error) { mount(box, el("div", { class: "banner banner-critical" }, r.error)); return; }
  const cards = r.cards.filter((c) => c.category !== "prevention");
  const counts = { critical: 0, warning: 0, info: 0 };
  cards.forEach((c) => { counts[c.indicator] = (counts[c.indicator] || 0) + 1; });
  const parts = [
    el("div", { class: "row between", style: { marginBottom: "10px" } }, el("h2", {}, "Findings"),
      el("span", { class: "muted small" }, `Checked ${new Date(r.checked_at).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })} · ${r.elapsed_s}s`)),
    el("div", { class: "ph-summary" },
      el("span", { class: "badge badge-critical" }, `${counts.critical} critical`),
      el("span", { class: "badge badge-warning" }, `${counts.warning} warning`),
      el("span", { class: "badge badge-info" }, `${counts.info} info`),
      r.patient_context && r.patient_context.egfr ? el("span", { class: "badge" }, `eGFR ${r.patient_context.egfr}${r.patient_context.egfr_basis && r.patient_context.egfr_basis !== "from the chart" ? " (CKD-EPI 2021)" : ""}`) : null),
  ];
  if (r.empty_message) {
    parts.push(el("div", { class: "ph-none" }, el("h3", {}, r.empty_message),
      el("p", { class: "muted small", style: { margin: 0 } }, "Absence from these sources is not proof that a combination carries no risk. Sources checked are listed below.")));
  }
  if (cards.length) parts.push(el("div", { class: "ph-cards" }, cards.map(renderCard)));
  parts.push(el("div", { class: "divider" }), el("h3", { style: { marginBottom: "8px" } }, "Medicines"), renderMedsTable(r.medications || []));
  if (r.prevention && r.prevention.length) {
    parts.push(el("div", { class: "divider" }), el("h3", { style: { marginBottom: "6px" } }, "Screening and vaccine reminders"),
      el("p", { class: "muted small" }, r.prevention_note || ""),
      el("ul", { class: "ph-sources" }, r.prevention.map((p) => el("li", {}, el("strong", {}, p.name), ` — ${p.detail} `,
        el("a", { href: p.source.url, target: "_blank", rel: "noopener noreferrer" }, p.source.label)))));
  }
  parts.push(el("div", { class: "divider" }), el("h3", { style: { marginBottom: "6px" } }, "Sources checked"), renderSources(r.sources_checked));
  mount(box, parts);
}

// ------------------------------------------------------------------ formulary
function buildFormulary() {
  const panel = $("#panel-formulary");
  const planSel = select("f-plan", state.plans.map((p) => [p.plan_key, p.name]), "PDP-A");
  const drugInput = el("input", { class: "input", id: "f-drug", placeholder: "e.g. apixaban, rosuvastatin, semaglutide", autocomplete: "off" });
  const out = el("div", { id: "f-out", style: { marginTop: "16px" } });
  const form = el("form", { class: "ph-form-row", onsubmit: (e) => { e.preventDefault(); lookup(); } },
    field("Medicare plan", planSel),
    el("div", { class: "field" }, el("label", { for: "f-drug" }, "Medicine"), el("div", { class: "ph-search" }, drugInput)),
    el("button", { class: "btn btn-primary", type: "submit" }, "Look up"));
  attachSearch(drugInput, (it) => { drugInput.value = it.name; lookup(); });

  async function lookup() {
    const drug = drugInput.value.trim();
    if (!drug) { drugInput.focus(); return; }
    mount(out, el("div", { class: "ph-loading" }, el("span", { class: "spinner" }), "Looking up…"));
    try {
      const r = await api(`/api/formulary/lookup?plan_key=${encodeURIComponent(planSel.value)}&drug=${encodeURIComponent(drug)}`);
      renderLookup(out, r);
    } catch (err) { mount(out, el("div", { class: "banner banner-critical" }, err.message)); }
  }

  mount(panel, el("div", { class: "card card-pad" },
    el("h2", { style: { marginBottom: "4px" } }, "Formulary lookup"),
    el("p", { class: "muted" }, "Tier, prior authorization, step therapy and quantity limits for the patient's Medicare drug plan, with lower-tier alternatives in the same class."),
    el("div", { class: "banner banner-info", style: { margin: "10px 0 14px" } }, "Sample formulary (structure of the CMS Part D files; demo data). Plan names, IDs and tiers are illustrative."),
    form, out));
}

function renderLookup(out, r) {
  if (r.note && !r.status) { mount(out, el("div", { class: "banner banner-warning" }, r.note)); return; }
  if (r.error) { mount(out, el("div", { class: "banner banner-warning" }, r.error)); return; }
  const s = r.status || {};
  const name = show(r.drug.generic) || r.drug.query;
  const head = el("div", { class: "row between", style: { alignItems: "flex-start" } },
    el("div", {}, el("h3", {}, name), el("p", { class: "muted small", style: { margin: 0 } }, [r.drug.group, r.drug.brands && r.drug.brands.length ? `Brands: ${r.drug.brands.join(", ")}` : null].filter(Boolean).join(" · "))),
    s.on_formulary ? el("div", { style: { textAlign: "right" } }, el("div", { class: "ph-tier" }, `Tier ${s.tier}`), el("div", { class: "muted small" }, s.tier_label)) : el("span", { class: "badge badge-warning" }, "Not covered"));
  const flags = s.on_formulary ? el("dl", { class: "ph-status" },
    el("dt", {}, "Prior authorization"), el("dd", {}, s.prior_auth ? el("span", { class: "badge badge-warning" }, "Required") : "No"),
    el("dt", {}, "Step therapy"), el("dd", {}, s.step_therapy ? el("span", { class: "badge badge-warning" }, "Applies") : "No"),
    el("dt", {}, "Quantity limit"), el("dd", {}, s.quantity_limit ? `Yes${s.ql_detail ? ` — ${s.ql_detail}` : ""}` : "No"),
    s.note ? el("dt", {}, "Note") : null, s.note ? el("dd", {}, s.note) : null) : el("p", {}, s.note || "");
  const alts = r.alternatives && r.alternatives.length ? el("div", { style: { marginTop: "14px" } },
    el("h4", { style: { marginBottom: "6px" } }, "Alternatives in the same class"),
    el("div", { class: "table-wrap" }, el("table", { class: "table" },
      el("thead", {}, el("tr", {}, ["Medicine", "Tier", "Restrictions", "Generic"].map((h) => el("th", {}, h)))),
      el("tbody", {}, r.alternatives.map((a) => el("tr", {},
        el("td", {}, show(a.drug)), el("td", {}, `${a.tier} · ${a.tier_label}`),
        el("td", {}, [a.prior_auth ? "PA" : null, a.step_therapy ? "ST" : null, a.quantity_limit ? `QL${a.ql_detail ? ` (${a.ql_detail})` : ""}` : null].filter(Boolean).join(", ") || "None"),
        el("td", {}, a.generic_available ? "Yes" : "Brand only")))))),
    el("p", { class: "muted small", style: { marginTop: "8px" } }, r.ranking || "")) : null;
  mount(out, el("div", { class: "card card-pad", style: { boxShadow: "none" } }, head, el("div", { class: "divider" }), flags, alts,
    el("p", { class: "muted small", style: { marginTop: "10px" } }, r.source, " · ", el("a", { href: r.layout_url, target: "_blank", rel: "noopener noreferrer" }, "CMS record layout"))));
}

// ------------------------------------------------------------------ calculators
function calcCard(title, subtitle, fieldsNode, onRun) {
  const result = el("div", { class: "hidden" });
  const run = async (e) => {
    e.preventDefault();
    try {
      const r = await onRun();
      mount(result, el("div", { class: "ph-result" },
        el("div", { class: "big" }, `${r.value} `, el("span", { class: "muted", style: { fontSize: "14px", fontWeight: 500 } }, r.unit)),
        el("div", {}, r.interpretation),
        r.components && r.components.length ? el("div", { class: "small ink-2", style: { marginTop: "4px" } }, r.components.map((c) => `${c.item} (+${c.points})`).join(", ")) : null,
        r.note ? el("div", { class: "small ink-2", style: { marginTop: "4px" } }, r.note) : null,
        el("div", { class: "cite" }, el("a", { href: r.citation.url, target: "_blank", rel: "noopener noreferrer" }, r.citation.label))));
      result.classList.remove("hidden");
    } catch (err) { toast(err.message, "error"); }
  };
  return el("form", { class: "card card-pad ph-calc", onsubmit: run },
    el("h3", {}, title), el("p", { class: "muted small" }, subtitle), fieldsNode,
    el("div", { class: "row", style: { marginTop: "12px" } }, el("button", { class: "btn btn-primary btn-sm", type: "submit" }, "Calculate")), result);
}
function check(id, label) { return el("label", { class: "checkbox", for: id }, el("input", { type: "checkbox", id }), label); }
function num(id, placeholder, step = "any") { return el("input", { class: "input", id, type: "number", step, placeholder, required: true }); }

function buildCalculators() {
  const panel = $("#panel-calc");
  const ckd = calcCard("eGFR (CKD-EPI 2021)", "Race-free creatinine equation. Labels often dose by creatinine clearance instead.",
    el("div", { class: "fields" }, field("Creatinine (mg/dL)", num("c-cr", "1.0", "0.01")), field("Age", num("c-age", "65", "1")), field("Sex", select("c-sex", [["F", "Female"], ["M", "Male"]]))),
    () => api("/api/drugs/calc", { method: "POST", body: { calculator: "ckd_epi_2021", inputs: { creatinine_mg_dl: Number($("#c-cr").value), age: Number($("#c-age").value), sex: $("#c-sex").value } } }));
  const cha = calcCard("CHA₂DS₂-VASc", "Stroke risk in atrial fibrillation.",
    el("div", {}, el("div", { class: "fields" }, field("Age", num("v-age", "72", "1")), field("Sex", select("v-sex", [["F", "Female"], ["M", "Male"]]))),
      el("div", { class: "checks" }, check("v-chf", "Heart failure"), check("v-htn", "Hypertension"), check("v-dm", "Diabetes"), check("v-stroke", "Prior stroke, TIA or thromboembolism"), check("v-vasc", "Vascular disease (MI, PAD, aortic plaque)"))),
    () => api("/api/drugs/calc", { method: "POST", body: { calculator: "cha2ds2_vasc", inputs: { age: Number($("#v-age").value), sex: $("#v-sex").value, chf: $("#v-chf").checked, hypertension: $("#v-htn").checked, diabetes: $("#v-dm").checked, stroke_tia: $("#v-stroke").checked, vascular: $("#v-vasc").checked } } }));
  const hb = calcCard("HAS-BLED", "One-year major bleeding risk on anticoagulation.",
    el("div", { class: "checks" }, check("h-htn", "Uncontrolled hypertension (SBP > 160)"), check("h-renal", "Abnormal renal function"), check("h-liver", "Abnormal liver function"), check("h-stroke", "Prior stroke"), check("h-bleed", "Bleeding history or predisposition"), check("h-inr", "Labile INR"), check("h-old", "Age over 65"), check("h-drugs", "Antiplatelet or NSAID use"), check("h-alc", "Alcohol (8+ drinks a week)")),
    () => api("/api/drugs/calc", { method: "POST", body: { calculator: "has_bled", inputs: { hypertension: $("#h-htn").checked, renal: $("#h-renal").checked, liver: $("#h-liver").checked, stroke: $("#h-stroke").checked, bleeding: $("#h-bleed").checked, labile_inr: $("#h-inr").checked, elderly: $("#h-old").checked, drugs: $("#h-drugs").checked, alcohol: $("#h-alc").checked } } }));
  const bmi = calcCard("BMI", "Body mass index for adults.",
    el("div", { class: "fields" }, field("Weight", num("b-w", "70")), field("Unit", select("b-wu", [["kg", "kg"], ["lb", "lb"]])), el("div"), field("Height", num("b-h", "170")), field("Unit", select("b-hu", [["cm", "cm"], ["in", "in"]]))),
    () => {
      const w = Number($("#b-w").value), h = Number($("#b-h").value);
      const inputs = {};
      if ($("#b-wu").value === "kg") inputs.weight_kg = w; else inputs.weight_lb = w;
      if ($("#b-hu").value === "cm") inputs.height_cm = h; else inputs.height_in = h;
      return api("/api/drugs/calc", { method: "POST", body: { calculator: "bmi", inputs } });
    });
  mount(panel, el("div", { class: "ph-calc-grid" }, ckd, cha, hb, bmi));
}

// ------------------------------------------------------------------ tabs and start
function setupTabs() {
  for (const btn of $$(".tabs button")) {
    btn.addEventListener("click", () => {
      for (const b of $$(".tabs button")) { b.classList.toggle("active", b === btn); b.setAttribute("aria-selected", b === btn ? "true" : "false"); }
      for (const p of $$(".tab-panel")) p.classList.toggle("hidden", p.id !== `panel-${btn.dataset.tab}`);
      try { localStorage.setItem("ph-tab", btn.dataset.tab); } catch { /* storage unavailable */ }
    });
  }
  let saved = null;
  try { saved = localStorage.getItem("ph-tab"); } catch { /* storage unavailable */ }
  const b = saved && $(`.tabs button[data-tab="${saved}"]`);
  if (b) b.click();
}

async function init() {
  renderHeader("pharmacy", { subtitle: "Pharmacy tools" });
  await requireSession();
  try {
    const [plans, sources] = await Promise.all([api("/api/formulary/plans"), api("/api/drugs/sources")]);
    state.plans = plans.plans || [];
    state.attribution = sources.rxnorm_attribution || "";
  } catch { /* shown per panel */ }
  buildChecker();
  const planSel = state.ctx.plan;
  for (const p of state.plans) planSel.appendChild(el("option", { value: p.plan_key }, p.name));
  buildFormulary();
  buildCalculators();
  setupTabs();
  $("#rx-attribution").textContent = state.attribution;
}

init();
