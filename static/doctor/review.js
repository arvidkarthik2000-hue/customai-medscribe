// Note review: evidence-linked note, verifier flags, medicine confirmation,
// decision-support cards and sign-off. A clinical screen: no sponsored units.
import { api, el, mount, toast, modal, fmt, $ , $$ } from "/js/common.js";
import { TEMPLATES } from "/doctor/shared.js";

const VERDICT = {
  supported: ["badge-ok", "Supported"],
  partial: ["badge-warning", "Partly supported"],
  unsupported: ["badge-critical", "Not in transcript"],
  contradicted: ["badge-critical", "Contradicted"],
  unchecked: ["", "Not checked"],
};
const ROLE_LABEL = { clinician: "Clinician", patient: "Patient", caregiver: "Caregiver", other: "Other", unknown: "Speaker" };
const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 };
const ACTION_BADGE = { start: "badge-info", stop: "badge-warning", change: "badge-warning", hold: "badge-warning", continue: "", discussed: "" };

export async function render(root, { visitId }, ctx) {
  let visit = await api(`/api/visits/${visitId}`);
  if (visit.status === "processing") { location.hash = `#/visit/${visitId}/processing`; return; }
  if (visit.status === "draft") { location.hash = `#/patient/${visit.patient_id}`; return; }
  const chart = await api(`/api/patients/${visit.patient_id}`);
  const signed = () => visit.status === "signed";
  let asideTab = "safety";
  let highlighted = new Set();

  const headerEl = el("div");
  const blockersEl = el("div");
  const noteEl = el("div", { class: "stack-lg" });
  const asideEl = el("aside", { class: "review-aside" });
  const footEl = el("div", { class: "muted small review-foot" });

  mount(root, headerEl, blockersEl,
    el("div", { class: "review-grid" }, el("div", { class: "review-main" }, noteEl, footEl), asideEl));

  // ---------------------------------------------------------------- header
  function drawHeader() {
    const items = allItems();
    const counts = { supported: 0, partial: 0, unsupported: 0, contradicted: 0 };
    items.forEach((it) => { if (counts[it.verdict] !== undefined) counts[it.verdict] += 1; });
    const meds = (visit.medication_check && visit.medication_check.medications) || [];
    const toConfirm = meds.filter((m) => !m.confirmed || (m.high_risk && !m.dose_confirmed)).length;
    mount(headerEl, el("div", { class: "page-title" },
      el("div", {},
        el("a", { href: "#/", class: "small" }, "← Today's schedule"),
        el("h1", { style: { marginTop: "6px" } }, chart.name),
        el("div", { class: "row-wrap muted", style: { marginTop: "4px" } },
          el("span", {}, `${chart.age} ${chart.sex}`), el("span", {}, "·"),
          el("span", {}, TEMPLATES[visit.template] || visit.template), el("span", {}, "·"),
          el("span", {}, visit.style), el("span", {}, "·"),
          el("span", {}, visit.source === "sample" ? "Scripted sample visit" : visit.source === "live" ? "Live transcript" : "Final-pass transcript"),
          visit.audio_seconds ? el("span", {}, `· ${fmt.dur(visit.audio_seconds)}`) : null),
        el("div", { class: "row-wrap", style: { marginTop: "8px" } },
          counts.supported || counts.partial || counts.unsupported || counts.contradicted
            ? el("span", { class: "badge badge-ok" }, `${counts.supported} of ${items.length} lines verified`)
            : el("span", { class: "badge", title: "The line-by-line check needs the AI engine; evidence links and number checks still apply." }, "Line-by-line check not run"),
          counts.partial ? el("span", { class: "badge badge-warning" }, `${counts.partial} partly supported`) : null,
          counts.unsupported + counts.contradicted ? el("span", { class: "badge badge-critical" }, `${counts.unsupported + counts.contradicted} to fix`) : null,
          meds.length ? el("span", { class: `badge ${toConfirm ? "badge-warning" : "badge-ok"}` }, toConfirm ? `${toConfirm} medicine${toConfirm > 1 ? "s" : ""} to confirm` : "Medicines confirmed") : null)),
      el("div", { class: "row-wrap no-print" },
        el("button", { class: "btn", onclick: copyNote }, "Copy note"),
        el("a", { class: "btn", href: `/api/visits/${visitId}/export.txt`, download: `note-${visitId}.txt` }, "Export .txt"),
        el("button", { class: "btn", onclick: () => window.print() }, "Print / PDF"),
        signed()
          ? el("span", { class: "badge badge-ok", style: { height: "36px", padding: "0 14px" } }, `Signed ${fmt.time(visit.signed_at)}`)
          : el("button", { class: "btn btn-primary", onclick: sign }, "Sign note"))));
    drawBlockers();
  }

  function drawBlockers() {
    const b = visit.blockers || [];
    if (signed()) {
      mount(blockersEl, el("div", { class: "banner banner-ok no-print", style: { marginBottom: "12px" } }, `Signed by ${ctx.me.prescriber.name} on ${fmt.date(visit.signed_at)} at ${fmt.time(visit.signed_at)}. The note is read-only.`));
    } else if (b.length) {
      mount(blockersEl, el("div", { class: "banner banner-warning no-print", style: { marginBottom: "12px" } },
        el("div", {}, el("strong", {}, "Before signing: "), b.join(" · "))));
    } else {
      mount(blockersEl, el("div", { class: "banner banner-ok no-print", style: { marginBottom: "12px" } }, "Everything is reviewed. Ready to sign."));
    }
  }

  // ---------------------------------------------------------------- note
  function allItems() {
    return ((visit.note && visit.note.sections) || []).flatMap((s) => s.items || []);
  }

  function evidenceChips(ids) {
    if (!ids || !ids.length) return null;
    return el("span", { class: "ev-chips" }, ids.slice(0, 6).map((id) => el("button", {
      class: "ev-chip", title: "Show the words behind this line", type: "button",
      onclick: (e) => { e.stopPropagation(); showEvidence(ids); },
    }, id.replace(/^t/, "#"))));
  }

  function verdictBadge(item) {
    const v = item.verdict || "unchecked";
    if (v === "supported" || v === "unchecked") return null; // quiet when fine or when the verifier did not run
    const [cls, label] = VERDICT[v] || VERDICT.unchecked;
    return el("span", { class: `badge ${cls}`, title: item.verdict_reason || "" }, item.edited ? "Edited" : label);
  }

  function itemNode(section, item) {
    const statusTag = item.status === "negative" ? el("span", { class: "badge", title: "Explicitly denied or negative" }, "Negative") :
      item.status === "not_discussed" ? el("span", { class: "badge", title: "Not discussed in this visit" }, "Not discussed") : null;
    const flagged = ["unsupported", "contradicted"].includes(item.verdict) && !item.edited;
    const node = el("li", { class: `note-item ${flagged ? "flagged" : item.verdict === "partial" && !item.edited ? "partial" : ""}`, dataset: { item: item.id } },
      el("span", { class: "note-text", tabindex: signed() ? undefined : "0", title: signed() ? "" : "Click to edit", onclick: () => !signed() && editItem(section, item, node), onkeydown: (e) => { if (e.key === "Enter" && !signed()) { e.preventDefault(); editItem(section, item, node); } } }, item.text),
      " ", statusTag, " ", verdictBadge(item), " ", evidenceChips(item.evidence),
      flagged && item.verdict_reason ? el("div", { class: "verdict-reason" }, item.verdict_reason) : null);
    return node;
  }

  function editItem(section, item, node) {
    const ta = el("textarea", { class: "textarea", rows: 2 }, item.text);
    const save = async () => {
      const text = ta.value.trim();
      if (text === item.text) { node.replaceWith(itemNode(section, item)); return; }
      try {
        visit = await api(`/api/visits/${visitId}/note`, { method: "PATCH", body: { item_id: item.id, text } });
        drawAll();
      } catch (e) { toast(e.message, "error"); node.replaceWith(itemNode(section, item)); }
    };
    const remove = async () => {
      try {
        const items = section.items.filter((i) => i.id !== item.id);
        visit = await api(`/api/visits/${visitId}/note`, { method: "PATCH", body: { section_id: section.id, items } });
        drawAll();
      } catch (e) { toast(e.message, "error"); }
    };
    const editor = el("li", { class: "note-item editing" }, ta,
      el("div", { class: "row", style: { marginTop: "6px" } },
        el("button", { class: "btn btn-sm btn-primary", onclick: save }, "Save"),
        el("button", { class: "btn btn-sm", onclick: () => editor.replaceWith(itemNode(section, item)) }, "Cancel"),
        el("div", { class: "grow" }),
        el("button", { class: "btn btn-sm btn-ghost", onclick: remove }, "Remove line")));
    node.replaceWith(editor);
    ta.focus();
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") editor.replaceWith(itemNode(section, item)); });
  }

  function askSection(section) {
    const input = el("textarea", { class: "textarea", rows: 3, placeholder: "e.g. Make this more concise, or add the onset and duration from the transcript" });
    const quick = ["More concise", "More detail from the transcript", "Bullet points", "Plain language"];
    modal({
      title: `Rewrite: ${section.title}`,
      body: el("div", { class: "stack" },
        el("p", { class: "muted small" }, "The rewrite uses only what was said in this visit, with evidence links kept."),
        el("div", { class: "row-wrap" }, quick.map((q) => el("button", { class: "btn btn-sm", type: "button", onclick: () => { input.value = q; } }, q))),
        input),
      actions: [{ label: "Cancel" }, { label: "Rewrite", primary: true, onClick: async () => {
        const instruction = input.value.trim();
        if (!instruction) { toast("Say how to rewrite it.", "error"); return true; }
        try {
          toast("Rewriting…");
          const res = await api(`/api/visits/${visitId}/ask`, { method: "POST", body: { section_id: section.id, instruction } });
          visit = await api(`/api/visits/${visitId}/note`, { method: "PATCH", body: { section_id: section.id, items: res.section.items } });
          drawAll();
          toast("Section rewritten.", "ok");
        } catch (e) { toast(e.message, "error"); return true; }
      } }],
    });
  }

  function drawNote() {
    const note = visit.note || {};
    const sections = note.sections || [];
    const blocks = [];
    if (note.visit_summary) {
      blocks.push(el("section", { class: "card card-pad note-summary" }, el("div", { class: "label" }, "Visit summary"), el("p", { style: { margin: "4px 0 0" } }, note.visit_summary)));
    }
    blocks.push(el("section", { class: "card note-card" },
      sections.map((s) => el("div", { class: "note-section" },
        el("div", { class: "row between note-section-head" },
          el("h3", {}, s.title),
          signed() ? null : el("button", { class: "btn btn-sm btn-ghost no-print", onclick: () => askSection(s) }, "Ask to rewrite")),
        (s.items || []).length ? el("ul", { class: "note-items" }, s.items.map((it) => itemNode(s, it))) : el("p", { class: "muted small" }, "Nothing documented.")))));
    blocks.push(medicationsCard());
    blocks.push(diagnosesCard(note));
    if ((note.orders || []).length || (note.follow_up && note.follow_up.text)) blocks.push(ordersCard(note));
    if (note.patient_instructions) blocks.push(instructionsCard(note));
    if ((note.not_discussed || []).length || (note.uncertain || []).length) blocks.push(gapsCard(note));
    mount(noteEl, blocks);
  }

  // ---------------------------------------------------------------- medicines
  function medicationsCard() {
    const mc = visit.medication_check || {};
    const meds = mc.medications || (visit.note && visit.note.medications) || [];
    const body = !meds.length ? el("p", { class: "muted" }, "No medicines were discussed.") : el("div", { class: "table-wrap" }, el("table", { class: "table meds" },
      el("thead", {}, el("tr", {}, el("th", {}, "Medicine"), el("th", {}, "Dose and frequency"), el("th", {}, "Checks"), el("th", { class: "num" }, "Confirm"))),
      el("tbody", {}, meds.map(medRow))));
    return el("section", { class: "card" },
      el("div", { class: "card-head" },
        el("div", {}, el("h3", {}, "Medicines"), el("p", { class: "muted small", style: { margin: "2px 0 0" } }, "Confirm every medicine before signing. High-risk medicines also need the dose confirmed.")),
        mc.sources_checked ? el("span", { class: "muted small" }, sourcesLine(mc.sources_checked)) : null),
      el("div", { class: "card-body", style: { padding: "0" } }, body));
  }

  function medRow(m) {
    const match = m.match || {};
    const nameCell = el("td", {},
      el("div", { class: "row-wrap" }, el("strong", {}, m.name), m.action ? el("span", { class: `badge ${ACTION_BADGE[m.action] || ""}` }, m.action) : null,
        m.high_risk ? el("span", { class: "badge badge-critical", title: m.high_risk_reason || "" }, "High risk") : null,
        m.schedule && m.schedule !== "None" ? el("span", { class: "badge" }, m.schedule) : null,
        ["unsupported", "contradicted"].includes(m.verdict) ? el("span", { class: "badge badge-critical", title: m.verdict_reason || "" }, "Check against transcript") : m.verdict === "partial" ? el("span", { class: "badge badge-warning", title: m.verdict_reason || "" }, "Partly supported") : null),
      match.status === "matched" ? el("div", { class: "muted small" }, match.source === "RxNorm"
        ? `RxNorm${match.rxcui ? " RxCUI " + match.rxcui : ""}${match.display ? " · " + match.display : ""}`
        : `Matched on the local drug list${match.rxcui ? " (RxCUI " + match.rxcui + ", not yet confirmed with RxNorm)" : ""}`) : null,
      match.reason && match.status !== "matched" ? el("div", { class: "muted small" }, match.reason) : null,
      match.status === "ambiguous" ? ambiguousPicker(m) : null,
      match.status === "unmatched" ? el("div", { class: "small", style: { color: "var(--critical)" } }, "Not found in RxNorm: check the name") : null,
      m.as_spoken && m.as_spoken.toLowerCase() !== (m.name || "").toLowerCase() ? el("div", { class: "muted small" }, `Heard as “${m.as_spoken}”`) : null,
      evidenceChips(m.evidence));
    const doseText = [m.dose && `${m.dose}${m.unit ? " " + m.unit : ""}`, m.route, m.frequency, m.duration].filter(Boolean).join(" · ") || "Not stated";
    const dc = m.dose_check || {};
    const checks = el("td", {}, el("div", { class: "stack", style: { gap: "4px", alignItems: "flex-start" } },
      dc.status === "above_max" ? el("span", { class: "badge badge-critical", title: dc.detail || "" }, "Above label maximum") : null,
      dc.status === "below_usual" ? el("span", { class: "badge badge-warning", title: dc.detail || "" }, "Below usual dose") : null,
      formularyBadge(m.formulary),
      dc.detail && dc.status !== "ok" && dc.status !== "unknown" ? el("div", { class: "muted small" }, dc.detail) : null));
    const confirm = el("input", { type: "checkbox", checked: !!m.confirmed, disabled: signed(), "aria-label": `Confirm ${m.name}`, onchange: () => confirmMed(m, { confirmed: confirm.checked, dose_confirmed: doseBox ? doseBox.checked : undefined }) });
    const doseBox = m.high_risk ? el("input", { type: "checkbox", checked: !!m.dose_confirmed, disabled: signed(), "aria-label": `Confirm dose of ${m.name}`, onchange: () => confirmMed(m, { confirmed: confirm.checked || doseBox.checked, dose_confirmed: doseBox.checked }) }) : null;
    return el("tr", { class: m.confirmed && (!m.high_risk || m.dose_confirmed) ? "confirmed" : "" },
      nameCell,
      el("td", {}, doseText),
      checks,
      el("td", { class: "num" },
        el("label", { class: "checkbox", style: { justifyContent: "flex-end" } }, confirm, el("span", {}, "Medicine")),
        doseBox ? el("label", { class: "checkbox", style: { justifyContent: "flex-end", marginTop: "6px" } }, doseBox, el("span", {}, "Dose")) : null));
  }

  function ambiguousPicker(m) {
    const cands = [...new Set([...(m.match.sound_alike || []), ...((m.match.candidates || []).map((c) => c.name))])].filter(Boolean).slice(0, 5);
    return el("div", { class: "banner banner-warning", style: { marginTop: "6px", padding: "6px 10px" } },
      el("div", { class: "stack", style: { gap: "4px" } },
        el("span", { class: "small" }, el("strong", {}, "Sound-alike: "), "check which medicine was meant."),
        el("div", { class: "row-wrap" },
          [m.name, ...cands.filter((c) => c.toLowerCase() !== (m.name || "").toLowerCase())].map((c) => el("button", { class: "btn btn-sm", disabled: signed(), onclick: () => confirmMed(m, { confirmed: true, corrected: { name: c } }) }, c)))));
  }

  function formularyBadge(f) {
    if (!f) return null;
    if (f.available === false || f.on_formulary === undefined && !f.tier) {
      return f.note ? el("span", { class: "muted small" }, f.note) : null;
    }
    if (f.on_formulary === false) return el("span", { class: "badge badge-warning" }, "Not on formulary");
    const bits = [f.tier_text || f.tier_label || (f.tier ? `Tier ${f.tier}` : "On formulary")];
    if (f.prior_auth) bits.push("PA");
    if (f.step_therapy) bits.push("ST");
    if (f.quantity_limit) bits.push("QL");
    const restricted = f.prior_auth || f.step_therapy || f.quantity_limit;
    return el("span", { class: `badge ${restricted ? "badge-warning" : "badge-info"}`, title: f.source ? `Source: ${f.source}` : "" }, bits.join(" · "));
  }

  async function confirmMed(m, body) {
    try {
      const mc = await api(`/api/visits/${visitId}/meds/${encodeURIComponent(m.id)}/confirm`, { method: "POST", body });
      visit.medication_check = mc.medication_check || mc;
      visit = await api(`/api/visits/${visitId}`);
      drawAll();
    } catch (e) { toast(e.message, "error"); drawAll(); }
  }

  function sourcesLine(sources) {
    return sources.map((s) => `${s.name}${s.status === "ok" ? "" : s.status === "not_used" ? " (not needed)" : " (unavailable)"}`).join(" · ");
  }

  // ---------------------------------------------------------------- other cards
  function diagnosesCard(note) {
    const dx = note.diagnoses || [];
    return el("section", { class: "card" },
      el("div", { class: "card-head" }, el("h3", {}, "Diagnoses (ICD-10-CM suggestions)"), el("span", { class: "muted small" }, "Suggestions only; the clinician decides")),
      el("div", { class: "card-body" }, !dx.length ? el("p", { class: "muted", style: { margin: 0 } }, "None suggested.") :
        el("ul", { class: "plain-list" }, dx.map((d) => el("li", { class: "row-wrap" },
          el("span", { class: `badge ${d.icd10_valid === false ? "badge-warning" : "badge-brand"} mono` }, d.icd10 || "—"),
          el("span", {}, d.description),
          d.icd10_valid === false ? el("span", { class: "muted small" }, "Code not found in ICD-10-CM; check it") : d.icd10_title && d.icd10_title.toLowerCase() !== (d.description || "").toLowerCase() ? el("span", { class: "muted small" }, d.icd10_title) : null,
          evidenceChips(d.evidence))))));
  }

  function ordersCard(note) {
    return el("section", { class: "card" },
      el("div", { class: "card-head" }, el("h3", {}, "Orders and follow-up (drafts)"), el("span", { class: "muted small" }, "Never sent automatically")),
      el("div", { class: "card-body stack" },
        (note.orders || []).length ? el("ul", { class: "plain-list" }, note.orders.map((o) => el("li", { class: "row-wrap" }, el("span", { class: "badge" }, o.type), el("span", {}, o.description), evidenceChips(o.evidence)))) : null,
        note.follow_up && note.follow_up.text ? el("div", { class: "row-wrap" }, el("strong", {}, "Follow-up:"), el("span", {}, note.follow_up.text), evidenceChips(note.follow_up.evidence)) : null));
  }

  function instructionsCard(note) {
    return el("section", { class: "card instructions-card" },
      el("div", { class: "card-head" }, el("h3", {}, "Patient instructions"),
        el("button", { class: "btn btn-sm no-print", onclick: printInstructions }, "Print for patient")),
      el("div", { class: "card-body" }, String(note.patient_instructions).split(/\n+/).map((p) => el("p", {}, p))));
  }

  function gapsCard(note) {
    return el("section", { class: "card" },
      el("div", { class: "card-head" }, el("h3", {}, "Not discussed and uncertain")),
      el("div", { class: "card-body stack" },
        (note.not_discussed || []).length ? el("div", {}, el("div", { class: "label" }, "Not discussed in this visit"), el("div", { class: "row-wrap", style: { marginTop: "6px" } }, note.not_discussed.map((n) => el("span", { class: "badge" }, n)))) : null,
        (note.uncertain || []).length ? el("div", {}, el("div", { class: "label" }, "Unclear in the audio: check these"), el("ul", { class: "plain-list" }, note.uncertain.map((u) => el("li", {}, u.text, " ", evidenceChips(u.evidence))))) : null,
        (note.removed_brands || []).length ? el("p", { class: "muted small", style: { margin: 0 } }, `Brand names the speech engine added in brackets were removed: ${note.removed_brands.join(", ")}.`) : null));
  }

  // ---------------------------------------------------------------- aside
  function drawAside() {
    const cards = sortedCards();
    const critical = cards.filter((c) => c.indicator === "critical").length;
    const tabs = el("div", { class: "tabs no-print" },
      tabBtn("safety", `Safety alerts${cards.length ? ` (${cards.length})` : ""}`, critical ? "critical" : ""),
      tabBtn("transcript", "Transcript"),
      tabBtn("chart", "Chart"));
    let body;
    if (asideTab === "safety") body = safetyPanel(cards);
    else if (asideTab === "transcript") body = transcriptPanel();
    else body = chartPanel();
    mount(asideEl, el("div", { class: "card review-aside-card" }, el("div", { class: "card-body" }, tabs, body)));
  }

  function tabBtn(id, label, tone) {
    return el("button", { class: `${asideTab === id ? "active" : ""} ${tone === "critical" ? "tab-critical" : ""}`, onclick: () => { asideTab = id; drawAside(); } }, label);
  }

  function sortedCards() {
    const cards = ((visit.medication_check && visit.medication_check.cards) || []).slice();
    return cards.sort((a, b) => (SEVERITY_ORDER[a.indicator] ?? 3) - (SEVERITY_ORDER[b.indicator] ?? 3));
  }

  function safetyPanel(cards) {
    const mc = visit.medication_check || {};
    if (!mc.cards && !mc.medications) {
      return el("div", { class: "empty" }, "Medication checks did not run for this visit.");
    }
    const acks = visit.cards_ack || {};
    return el("div", { class: "stack" },
      !cards.length ? el("div", { class: "banner" }, el("div", {}, el("strong", {}, "No interaction found in these sources. "), "Absence in these sources is not proof of safety.")) : null,
      cards.map((c) => cdsCard(c, acks[c.id])),
      mc.sources_checked ? el("div", { class: "muted small" }, el("strong", {}, "Sources checked: "), sourcesLine(mc.sources_checked)) : null,
      el("p", { class: "muted small", style: { margin: 0 } }, "Alerts come from FDA labels, RxNorm and published rules. No sponsor has any input into alerts, rules or formulary ranking."));
  }

  function cdsCard(c, ack) {
    const reasons = c.override_reasons && c.override_reasons.length ? c.override_reasons : ["Benefit outweighs risk", "Will monitor", "Plan changed", "Other"];
    const required = c.requires_ack === true || (c.requires_ack === undefined && c.indicator === "critical");
    const sel = el("select", { class: "select", style: { height: "30px", fontSize: "12.5px" } }, el("option", { value: "" }, required ? "Reason (required to sign)" : "Reason (optional)"), reasons.map((r) => el("option", { value: r }, r)));
    const ackBtn = el("button", { class: "btn btn-sm", onclick: async () => {
      if (required && !sel.value) { toast("Choose a reason to acknowledge this alert.", "error"); return; }
      try {
        await api(`/api/visits/${visitId}/cards/${encodeURIComponent(c.id)}/ack`, { method: "POST", body: { reason: sel.value || "Acknowledged" } });
        visit = await api(`/api/visits/${visitId}`);
        drawAll();
      } catch (e) { toast(e.message, "error"); }
    } }, "Acknowledge");
    const label = { interaction: "Interaction", allergy: "Allergy", condition: "Condition", dose: "Dose", duplicate: "Duplicate therapy", renal: "Kidney function", older_adult: "Older adult", qt: "QT", formulary: "Formulary", prevention: "Prevention", calculator: "Score", name_check: "Look-alike names" }[c.category] || c.category;
    return el("div", { class: `cds cds-${c.indicator}` },
      el("div", { class: "row between" },
        el("div", { class: "row-wrap" }, el("span", { class: `badge badge-${c.indicator === "critical" ? "critical" : c.indicator === "warning" ? "warning" : "info"}` }, c.indicator), el("span", { class: "muted small" }, label)),
        ack ? el("span", { class: "badge badge-ok", title: ack.reason }, "Acknowledged") : null),
      el("div", { class: "cds-summary", style: { marginTop: "4px" } }, c.summary),
      c.detail ? el("div", { class: "small ink-2", style: { marginTop: "4px" } }, c.detail) : null,
      valuesLine(c),
      c.items && c.items.length ? el("ul", { class: "plain-list small", style: { marginTop: "6px" } }, c.items.map((it) => el("li", {}, el("strong", {}, it.name), it.detail ? ` · ${it.detail}` : ""))) : null,
      c.context === "existing" ? el("div", { class: "muted small", style: { marginTop: "4px" } }, `The patient was already taking these before this visit.${required ? " Review it before signing." : ""}`) : null,
      c.source ? el("div", { class: "cds-source" }, "Source: ", c.source.url ? el("a", { href: c.source.url, target: "_blank", rel: "noopener noreferrer" }, c.source.label) : c.source.label,
        c.source.date ? ` · ${c.source.date_type === "label_effective" ? "label effective" : c.source.date_type === "reviewed" ? "reviewed" : ""} ${c.source.date}` : "",
        c.grading ? el("span", { class: "muted" }, ` · ${c.grading}`) : null) : null,
      !ack && !signed() && (required || c.indicator === "critical" || c.indicator === "warning") ? el("div", { class: "row", style: { marginTop: "8px" } }, sel, ackBtn) : null);
  }

  function valuesLine(c) {
    const bits = [];
    if (c.potassium != null) bits.push(`Potassium ${c.potassium} mmol/L`);
    if (c.sodium != null) bits.push(`Sodium ${c.sodium} mmol/L`);
    if (c.egfr != null) bits.push(`eGFR ${c.egfr}`);
    if (c.crcl != null) bits.push(`CrCl about ${c.crcl} mL/min${c.crcl_basis ? ` (${c.crcl_basis})` : ""}`);
    if (Array.isArray(c.criteria_met) && c.criteria_met.length) bits.push(`Label criteria met: ${c.criteria_met.join(", ")}`);
    if (c.condition) bits.push(`Chart: ${c.condition}`);
    return bits.length ? el("div", { class: "small", style: { marginTop: "4px", color: "var(--ink-2)" } }, bits.join(" · ")) : null;
  }

  function transcriptPanel() {
    const roles = visit.speaker_roles || {};
    const turns = visit.transcript || [];
    if (!turns.length) return el("div", { class: "empty" }, "No transcript.");
    return el("div", { class: "transcript review-transcript" }, turns.map((t) => {
      const role = t.role && t.role !== "unknown" ? t.role : roles[t.speaker] || "unknown";
      return el("div", { class: `turn ${highlighted.has(t.id) ? "hl" : ""}`, id: `turn-${t.id}` },
        el("span", { class: `spk spk-role-${role}` }, `${ROLE_LABEL[role] || "Speaker"}${role === "unknown" ? " " + t.speaker : ""}`),
        el("span", { class: "turn-id mono" }, t.id.replace(/^t/, "#")),
        el("span", { class: "turn-text" }, t.text));
    }));
  }

  function chartPanel() {
    return el("div", { class: "stack" },
      el("div", {}, el("div", { class: "label" }, "Problems"), el("ul", { class: "plain-list" }, (chart.problems || []).map((p) => el("li", {}, p.name, " ", el("span", { class: "mono muted" }, p.icd10 || ""))))),
      el("div", {}, el("div", { class: "label" }, "Medicines before this visit"), el("ul", { class: "plain-list" }, (chart.medications || []).map((m) => el("li", {}, `${m.name} ${m.dose || ""}${m.unit ? " " + m.unit : ""} ${m.frequency || ""}`)))),
      el("div", {}, el("div", { class: "label" }, "Allergies"), el("ul", { class: "plain-list" }, (chart.allergies || []).length ? chart.allergies.map((a) => el("li", {}, `${a.substance}${a.reaction ? " (" + a.reaction + ")" : ""}`)) : el("li", { class: "muted" }, "No known allergies"))),
      chart.egfr != null ? el("div", {}, el("div", { class: "label" }, "eGFR"), el("p", {}, `${chart.egfr} mL/min/1.73m²`)) : null);
  }

  function showEvidence(ids) {
    highlighted = new Set(ids);
    asideTab = "transcript";
    drawAside();
    const first = document.getElementById(`turn-${ids[0]}`);
    if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  function drawFoot() {
    const t = visit.timings || {};
    const e = visit.engine_info || {};
    const bits = [];
    if (e.final_model && visit.source === "final") bits.push(`Final pass: ${e.final_model} (Medical Mode)`);
    if (e.live_model && visit.source === "live") bits.push(`Live: ${e.live_model}`);
    if (e.note_model) bits.push(`Note: ${e.note_model}`);
    if (e.saved_note) bits.push("Saved sample note (AI engine not configured)");
    const secs = ["final_transcript_s", "note_s", "verify_s", "meds_s"].map((k) => t[k]).filter((x) => typeof x === "number");
    if (secs.length) bits.push(`Processing ${secs.reduce((a, b) => a + b, 0).toFixed(1)} s`);
    mount(footEl, bits.join(" · "));
  }

  function drawAll() {
    drawHeader();
    drawNote();
    drawAside();
    drawFoot();
  }

  // ---------------------------------------------------------------- actions
  async function copyNote() {
    try {
      const res = await api(`/api/visits/${visitId}/export.txt`, { raw: true });
      const text = await res.text();
      await navigator.clipboard.writeText(text);
      toast("Note copied. Paste it into the EHR.", "ok");
    } catch (e) { toast(`Could not copy: ${e.message}`, "error"); }
  }

  function printInstructions() {
    document.body.classList.add("print-instructions");
    const done = () => { document.body.classList.remove("print-instructions"); window.removeEventListener("afterprint", done); };
    window.addEventListener("afterprint", done);
    window.print();
    setTimeout(done, 2000);
  }

  async function sign() {
    try {
      const res = await api(`/api/visits/${visitId}/sign`, { method: "POST" });
      toast("Note signed.", "ok");
      location.hash = `#/visit/${visitId}/signed`;
      return res;
    } catch (e) {
      const blockers = (e.data && e.data.blockers) || [];
      modal({
        title: "Not ready to sign",
        body: blockers.length ? el("ul", { class: "plain-list" }, blockers.map((b) => el("li", {}, b))) : el("p", {}, e.message),
        actions: [{ label: "Back to review", primary: true }],
      });
      try { visit = await api(`/api/visits/${visitId}`); drawAll(); } catch { /* ignore */ }
    }
  }

  drawAll();
}
