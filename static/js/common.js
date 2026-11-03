// Shared helpers for every page. ES module; no build step, no third-party code.
// CSP is script-src 'self': never use inline <script> blocks or onclick="" attributes.

export async function api(path, { method = "GET", body, form, headers = {}, raw = false, redirect = true } = {}) {
  const opts = { method, headers: { ...headers }, credentials: "same-origin" };
  if (form) {
    opts.body = form;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (raw) return res;
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const err = new Error((data && (data.detail || data.error)) || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    if (res.status === 401 && redirect && !path.startsWith("/api/session") && location.pathname !== "/") {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = `/?next=${next}`;
    }
    throw err;
  }
  return data;
}

// Hyperscript: el("div", {class: "card", onclick: fn}, child, "text", [more])
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(node.style, v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") node.innerHTML = v; // only for trusted, static strings
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
  appendAll(node, children);
  return node;
}

function appendAll(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === undefined || c === null || c === false) continue;
    node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
export function mount(node, ...children) { clear(node); appendAll(node, children); return node; }
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let toastWrap;
export function toast(message, type = "info", ms = 3200) {
  if (!toastWrap) { toastWrap = el("div", { class: "toast-wrap", role: "status", "aria-live": "polite" }); document.body.appendChild(toastWrap); }
  const t = el("div", { class: `toast ${type === "error" ? "toast-error" : type === "ok" ? "toast-ok" : ""}` }, message);
  toastWrap.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

export function modal({ title, body, actions = [], onClose, wide = false }) {
  const close = () => { backdrop.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const footer = el("div", { class: "modal-foot" },
    actions.map((a) => el("button", { class: `btn ${a.primary ? "btn-primary" : a.danger ? "btn-danger" : ""}`, disabled: a.disabled, onclick: async (e) => { if (a.onClick) { const keep = await a.onClick(e, close); if (keep === true) return; } close(); } }, a.label)));
  const box = el("div", { class: "modal", role: "dialog", "aria-modal": "true", style: wide ? { width: "min(820px, 100%)" } : undefined },
    el("div", { class: "modal-head" }, el("h2", {}, title)),
    el("div", { class: "modal-body" }, body),
    actions.length ? footer : null);
  const backdrop = el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === backdrop) close(); } }, box);
  document.body.appendChild(backdrop);
  document.addEventListener("keydown", onKey);
  return { close, box, footer };
}

// ---------- formatting
export const fmt = {
  int: (n) => (n === null || n === undefined || isNaN(n) ? "–" : Math.round(n).toLocaleString("en-US")),
  money: (n, d = 0) => (n === null || n === undefined || isNaN(n) ? "–" : n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: d, maximumFractionDigits: d })),
  pct: (n, d = 0) => (n === null || n === undefined || isNaN(n) ? "–" : `${(n * 100).toFixed(d)}%`),
  date: (s) => (s ? new Date(s).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "–"),
  time: (s) => (s ? new Date(s).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "–"),
  dur: (sec) => { sec = Math.max(0, Math.round(sec || 0)); const m = Math.floor(sec / 60), s = sec % 60; return `${m}:${String(s).padStart(2, "0")}`; },
};

// ---------- header with role switcher
const VIEWS = [
  { role: "doctor", label: "Doctor", href: "/doctor/" },
  { role: "pharmacy", label: "Pharmacy tools", href: "/pharmacy/" },
  { role: "network", label: "Network console", href: "/console/" },
  { role: "sponsor", label: "Sponsor report", href: "/report/" },
];

export function renderHeader(active, { subtitle = "", simulated = false } = {}) {
  const header = el("header", { class: "app-header no-print" },
    el("a", { class: "wordmark", href: "/doctor/", title: "CustomAi MedScribe" },
      el("span", { class: "mark", "aria-hidden": "true" }, "M"),
      el("span", {}, "CustomAi MedScribe"),
      subtitle ? el("span", { class: "sub" }, subtitle) : null),
    el("div", { class: "header-spacer" }),
    el("nav", { class: "role-switch", "aria-label": "Switch view" },
      VIEWS.map((v) => el("a", {
        href: v.href, class: v.role === active ? "active" : "",
        onclick: (e) => { e.preventDefault(); switchRole(v.role, v.href); },
      }, v.label))),
    el("button", { class: "btn btn-ghost btn-sm", title: "About this demo", onclick: showAbout }, "About"));
  document.body.prepend(header);
  if (simulated) document.body.insertBefore(el("div", { class: "sim-strip" }, "Simulated data: synthetic offices, prescribers, brands and delivery. Not real campaign results."), header.nextSibling);
  return header;
}

export async function switchRole(role, href) {
  try { await api("/api/session/role", { method: "POST", body: { role } }); } catch (e) { /* 401 redirects */ }
  location.href = href;
}

export async function requireSession() {
  return api("/api/me");
}

export function showAbout() {
  modal({
    title: "About this demo",
    body: el("div", { class: "stack" },
      el("p", {}, "CustomAi MedScribe is an ambient scribe for US clinics. It is free to practices and funded by sponsored placements sold through Constant Media."),
      el("p", {}, "Speech: AssemblyAI Medical Mode (live and final pass). Notes: Claude by Anthropic, with every sentence linked to the transcript and a second verification pass. Drug data: RxNorm (U.S. National Library of Medicine), FDA drug labels via openFDA, and curated rules; Medicare Part D formulary structure from CMS."),
      el("p", {}, "Firewall: sponsors never see visit content or patient data, and never influence notes, alerts, rules or formulary ranking. Ads never appear on screens that show a chart, transcript, note, patient instructions or decision support."),
      el("p", { class: "muted small" }, "Demo only: synthetic patients, prescribers, brands and ad delivery. Do not enter real patient information. Not a medical device; clinicians review and sign every note.")),
    actions: [{ label: "Close", primary: true }],
  });
}

export function debounce(fn, ms = 250) {
  let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
