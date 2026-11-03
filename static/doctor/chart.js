// Pre-visit summary and visit setup. A clinical screen: no sponsored units.
import { api, el, mount, modal, toast, fmt } from "/js/common.js";
import { TEMPLATES, sexAge, readinessBanner, visitDefaults, saveVisitDefaults, SPECIALTIES } from "/doctor/shared.js";

export async function render(root, { patientId }, ctx) {
  const [chart, samples] = await Promise.all([
    api(`/api/patients/${patientId}`),
    api("/api/samples").catch(() => ({ samples: [] })),
  ]);
  const mySamples = (samples.samples || []).filter((s) => s.patient_id === patientId);
  const defaults = { template: "soap", style: "concise", engine: "sonnet", ...visitDefaults() };
  const state = { ...defaults };

  const seg = (key, options) => {
    const wrap = el("div", { class: "seg", role: "group" });
    const draw = () => mount(wrap, Object.entries(options).map(([k, label]) =>
      el("button", { type: "button", class: state[key] === k ? "active" : "", onclick: () => { state[key] = k; saveVisitDefaults(state); draw(); } }, label)));
    draw();
    return wrap;
  };

  const speechReady = ctx.me.speech_ready;

  const startBtn = el("button", { class: "btn btn-primary btn-lg btn-block", disabled: !speechReady, onclick: () => startLive() },
    el("span", { class: "dot", style: { background: "#fff" } }), "Start recording");

  async function createVisit() {
    const { visit_id } = await api("/api/visits", { method: "POST", body: { patient_id: patientId, template: state.template, style: state.style, engine: state.engine, specialty: ctx.me.prescriber.specialty } });
    return visit_id;
  }

  function consentModal({ title, extra, confirmLabel, onConfirm }) {
    const c1 = el("input", { type: "checkbox" });
    const c2 = el("input", { type: "checkbox" });
    const m = modal({
      title,
      body: el("div", { class: "stack" },
        el("p", {}, "Recording stays off until consent is logged for this visit. Consent is kept in the audit log and is never written into the note."),
        el("label", { class: "checkbox" }, c1, el("span", {}, "Everyone in the room has agreed to this conversation being recorded and transcribed.")),
        el("label", { class: "checkbox" }, c2, el("span", {}, "This is a role-play. No real patient information will be used.")),
        extra || null),
      actions: [
        { label: "Cancel" },
        { label: confirmLabel, primary: true, onClick: async () => {
          if (!c1.checked || !c2.checked) { toast("Please confirm both statements.", "error"); return true; }
          try { await onConfirm(); } catch (e) { toast(e.message, "error"); return true; }
        } },
      ],
    });
    return m;
  }

  function startLive() {
    consentModal({
      title: "Start recording",
      confirmLabel: "Consent logged, start",
      onConfirm: async () => {
        const vid = await createVisit();
        await api(`/api/visits/${vid}/consent`, { method: "POST", body: { consented: true, attestation: "roleplay_no_real_patient" } });
        location.hash = `#/visit/${vid}/record`;
      },
    });
  }

  function uploadFlow() {
    const input = el("input", { type: "file", accept: "audio/*,.webm,.ogg,.mp3,.m4a,.wav,.mp4", class: "input", style: { height: "auto", padding: "8px" } });
    consentModal({
      title: "Upload a recording",
      extra: el("div", { class: "field" }, el("label", {}, `Audio file (up to ${ctx.config.limits.max_upload_mb} MB)`), input),
      confirmLabel: "Upload and process",
      onConfirm: async () => {
        const file = input.files && input.files[0];
        if (!file) throw new Error("Choose an audio file first.");
        if (file.size > ctx.config.limits.max_upload_mb * 1024 * 1024) throw new Error("That file is too large for the demo.");
        const vid = await createVisit();
        await api(`/api/visits/${vid}/consent`, { method: "POST", body: { consented: true, attestation: "roleplay_no_real_patient" } });
        const form = new FormData();
        form.append("file", file, file.name);
        toast("Uploading…");
        await api(`/api/visits/${vid}/audio`, { method: "POST", form });
        location.hash = `#/visit/${vid}/processing`;
      },
    });
  }

  async function playSample(sample) {
    try {
      const vid = await createVisit();
      location.hash = `#/visit/${vid}/replay/${sample.id}`;
    } catch (e) { toast(e.message, "error"); }
  }

  const egfr = chart.egfr ?? (chart.labs || []).find((l) => /egfr/i.test(l.name))?.value;

  mount(root,
    el("div", { class: "page-title" },
      el("div", {},
        el("a", { href: "#/", class: "small" }, "← Today's schedule"),
        el("h1", { style: { marginTop: "6px" } }, chart.name),
        el("div", { class: "row-wrap muted", style: { marginTop: "4px" } },
          el("span", {}, sexAge(chart)), el("span", {}, "·"), el("span", {}, `DOB ${chart.dob}`), el("span", {}, "·"),
          el("span", { class: "mono" }, `MRN ${chart.mrn}`), el("span", {}, "·"),
          el("span", {}, chart.insurance ? chart.insurance.plan_name : "No insurance on file"))),
      el("div", { class: "row-wrap" },
        (chart.allergies || []).length
          ? (chart.allergies || []).map((a) => el("span", { class: "badge badge-critical" }, `Allergy: ${a.substance}`))
          : el("span", { class: "badge" }, "No known allergies"))),
    readinessBanner(ctx.me),
    el("div", { class: "split", style: { marginTop: "12px" } },
      el("div", { class: "stack-lg" },
        el("div", { class: "grid grid-2" },
          card("Problems", list(chart.problems, (p) => el("div", { class: "row between" }, el("span", {}, p.name), el("span", { class: "mono muted" }, p.icd10 || "")))),
          card("Current medicines", list(chart.medications, (m) => el("div", {}, el("strong", {}, m.name), " ", el("span", { class: "muted" }, [m.dose && `${m.dose}${m.unit ? " " + m.unit : ""}`, m.route, m.frequency].filter(Boolean).join(" · ")))))),
        el("div", { class: "grid grid-2" },
          card("Vitals", vitals(chart.vitals)),
          card("Recent labs", el("div", { class: "stack" },
            egfr !== undefined && egfr !== null ? el("div", { class: `banner ${egfr < 30 ? "banner-critical" : egfr < 60 ? "banner-warning" : ""}` }, `eGFR ${egfr} mL/min/1.73m²`) : null,
            list(chart.labs, (l) => el("div", { class: "row between" }, el("span", {}, l.name), el("span", { class: l.flag ? "strong" : "" }, `${l.value}${l.unit ? " " + l.unit : ""}`, l.flag ? el("span", { class: "badge badge-warning", style: { marginLeft: "6px" } }, l.flag) : null, el("span", { class: "muted small", style: { marginLeft: "6px" } }, l.date ? fmt.date(l.date) : ""))))))),
        card("Last visit", chart.last_visit ? el("div", {}, el("div", { class: "muted small" }, fmt.date(chart.last_visit.date)), el("p", {}, chart.last_visit.summary)) : el("p", { class: "muted" }, "No prior visit.")),
        card("Care gaps", list(chart.care_gaps, (g) => el("div", {}, el("strong", {}, g.name), " ", el("span", { class: "muted" }, g.detail || ""), g.source ? el("div", { class: "muted small" }, g.source) : null)))),
      el("aside", { class: "stack-lg sticky-aside" },
        el("section", { class: "card" },
          el("div", { class: "card-head" }, el("h3", {}, "Visit setup")),
          el("div", { class: "card-body stack-lg" },
            el("div", { class: "field" }, el("label", {}, "Note format"), seg("template", TEMPLATES)),
            el("div", { class: "row-wrap" },
              el("div", { class: "field" }, el("label", {}, "Style"), seg("style", { concise: "Concise", detailed: "Detailed" })),
              el("div", { class: "field" }, el("label", {}, "Note engine"), seg("engine", { sonnet: "Claude Sonnet", haiku: "Claude Haiku" }))),
            el("p", { class: "muted small", style: { margin: 0 } }, `Specialty template: ${SPECIALTIES[ctx.me.prescriber.specialty] || ctx.me.prescriber.specialty}`),
            startBtn,
            speechReady ? null : el("p", { class: "muted small", style: { margin: 0 } }, "Live recording needs the speech service, which isn't configured on this server yet."),
            el("div", { class: "row" },
              el("button", { class: "btn grow", disabled: !speechReady, onclick: uploadFlow }, "Upload a recording")),
            mySamples.length ? el("div", { class: "stack" },
              el("div", { class: "divider" }),
              el("div", { class: "label" }, "No microphone? Replay a scripted visit"),
              mySamples.map((s) => el("button", { class: "btn btn-block", onclick: () => playSample(s) }, `▶ ${s.title} (${s.minutes} min)`))) : null)),
        el("p", { class: "muted small" }, "The transcript, note and checks stay in the clinical zone. Nothing from this chart reaches sponsors."))));
}

function card(title, body) {
  return el("section", { class: "card" }, el("div", { class: "card-head" }, el("h3", {}, title)), el("div", { class: "card-body" }, body));
}

function list(items, renderItem) {
  if (!items || !items.length) return el("p", { class: "muted", style: { margin: 0 } }, "None recorded.");
  return el("div", { class: "stack" }, items.map(renderItem));
}

function vitals(v) {
  if (!v || !Object.keys(v).length) return el("p", { class: "muted", style: { margin: 0 } }, "No vitals recorded.");
  const labels = { bp: "Blood pressure", hr: "Heart rate", temp_f: "Temperature (°F)", rr: "Respiratory rate", spo2: "SpO₂", weight_kg: "Weight (kg)", height_cm: "Height (cm)", bmi: "BMI", date: "Taken" };
  return el("dl", { class: "kv" }, Object.entries(v).map(([k, val]) => [el("dt", {}, labels[k] || k.replace(/_/g, " ")), el("dd", {}, k === "date" ? fmt.date(val) : String(val))]));
}
