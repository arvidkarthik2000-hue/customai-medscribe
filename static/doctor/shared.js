// Small shared pieces for doctor views.
import { el } from "/js/common.js";

export const SPECIALTIES = {
  family_medicine: "Family medicine",
  internal_medicine: "Internal medicine",
  cardiology: "Cardiology",
  endocrinology: "Endocrinology",
  pulmonology: "Pulmonology",
};

export const STATES = ["NJ", "NY", "PA", "CT", "MA", "MD", "VA", "NC", "GA", "FL", "OH", "IL", "TX", "CA"];

export const TEMPLATES = { soap: "SOAP", progress: "Progress note", hp: "History & physical", consult: "Consult" };

export const STATUS_BADGE = {
  scheduled: ["", "Scheduled"],
  in_progress: ["badge-info", "In progress"],
  ready_to_sign: ["badge-warning", "Ready to sign"],
  signed: ["badge-ok", "Signed"],
};

export function statusBadge(status) {
  const [cls, label] = STATUS_BADGE[status] || ["", status || "–"];
  return el("span", { class: `badge ${cls}` }, label);
}

export function sexAge(p) {
  const sex = p.sex === "F" ? "F" : p.sex === "M" ? "M" : p.sex || "";
  return `${p.age ?? "?"}${sex ? " " + sex : ""}`;
}

export function readinessBanner(me) {
  const missing = [];
  if (!me.speech_ready) missing.push("live speech (AssemblyAI)");
  if (!me.notes_ready) missing.push("the AI note engine (Claude)");
  if (!missing.length) return null;
  return el("div", { class: "banner banner-info" },
    el("div", {},
      el("strong", {}, "Setup in progress: "),
      `${missing.join(" and ")} ${missing.length > 1 ? "are" : "is"} not configured on this server yet. `,
      "Sample visits still run end to end with saved notes."));
}

export function pill(text, cls = "") {
  return el("span", { class: `badge ${cls}` }, text);
}

export function section(title, ...children) {
  return el("section", { class: "card" },
    el("div", { class: "card-head" }, el("h3", {}, title)),
    el("div", { class: "card-body" }, ...children));
}

export function visitDefaults() {
  try { return JSON.parse(localStorage.getItem("ms_visit_defaults") || "{}"); } catch { return {}; }
}

export function saveVisitDefaults(v) {
  try { localStorage.setItem("ms_visit_defaults", JSON.stringify(v)); } catch { /* private mode */ }
}
