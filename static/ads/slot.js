// MedScribe ad-zone SDK. ES module, no dependencies, no third-party requests.
//
//   import { mountSlot, unmountAllSlots, openResourcesTray } from "/ads/slot.js";
//   const h = await mountSlot(el, { slot: "schedule_card" | "drafting_wait" | "post_sign",
//                                   prescriberId, specialty, state });
//   h.unmount();                 // when leaving the screen (sends time in view)
//   unmountAllSlots();           // before rendering ANY clinical screen
//   openResourcesTray({ prescriberId, specialty, state });   // doctor-initiated drawer
//
// The only data sent to the ad zone: slot, prescriber_id, specialty, state, hour.
// Counting follows MRC rules: "rendered" when the unit is drawn, "viewable" when at
// least 50% of it is on screen for 1 continuous second with the tab visible.

const BASE = (window.MEDSCRIBE_ADZONE_BASE || "/adzone").replace(/\/$/, "");
// The page's session header, for browsers that don't keep cookies (set by /js/common.js).
const sessionHeaders = () => { try { return (window.MEDSCRIBE_AUTH && window.MEDSCRIBE_AUTH()) || {}; } catch (e) { return {}; } };
const SENT_KEY = "ms_ads_sent_v1";
const CACHE_KEY = "ms_ads_schedule_v1";
const CACHE_MS = 10 * 60 * 1000; // the schedule card refreshes at most every 10 minutes
const handles = new Set();
let tray = null;

function store(key, fallback) {
  try { return JSON.parse(sessionStorage.getItem(key) || "null") ?? fallback; } catch { return fallback; }
}
function save(key, value) {
  try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: fine */ }
}
function alreadySent(imp, type) { return !!store(SENT_KEY, {})[`${imp}:${type}`]; }
function markSent(imp, type) {
  const s = store(SENT_KEY, {});
  s[`${imp}:${type}`] = 1;
  const keys = Object.keys(s);
  if (keys.length > 400) for (const k of keys.slice(0, 200)) delete s[k];
  save(SENT_KEY, s);
}

function ensureCss() {
  if (document.querySelector('link[data-ms-ads]')) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "/ads/slot.css";
  link.dataset.msAds = "1";
  document.head.appendChild(link);
}

function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return n;
}

async function send(imp, type, extra = {}) {
  const once = type !== "click";
  if (once && alreadySent(imp, type)) return;
  if (once) markSent(imp, type);
  try {
    await fetch(`${BASE}/events`, {
      method: "POST", credentials: "same-origin", keepalive: true,
      headers: { "Content-Type": "application/json", ...sessionHeaders() },
      body: JSON.stringify({ impression_id: imp, type, visible: document.visibilityState === "visible", webdriver: !!navigator.webdriver, ...extra }),
    });
  } catch { /* the ad zone being down must never affect the doctor */ }
}

function cacheGet(key) {
  const c = store(CACHE_KEY, null);
  return c && c.key === key && Date.now() - c.at < CACHE_MS ? c.data : null;
}

function svgUrl(svg) { return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`; }

function piUrl(path) { return `${BASE}${path}`; }

export async function mountSlot(container, { slot, prescriberId, specialty, state }) {
  ensureCss();
  for (const other of Array.from(handles)) if (other.container === container) unmount(other); // one unit per container
  const handle = { slot, container, unmounted: false, empty: false, impressionId: null, brand: null, unmount: () => unmount(handle) };
  handles.add(handle);
  const key = `${slot}|${prescriberId}|${specialty}|${state}`;
  let data = slot === "schedule_card" ? cacheGet(key) : null;
  if (!data) {
    let res;
    try {
      res = await fetch(`${BASE}/serve`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", ...sessionHeaders() },
        body: JSON.stringify({ slot, prescriber_id: prescriberId, specialty, state, hour: new Date().getHours() }),
      });
    } catch { handle.empty = true; return handle; }
    if (res.status !== 200) { handle.empty = true; return handle; }
    data = await res.json();
    if (slot === "schedule_card") save(CACHE_KEY, { key, at: Date.now(), data });
  }
  if (handle.unmounted) return handle; // the screen changed while we were loading
  render(handle, data);
  return handle;
}

function render(handle, data) {
  const u = data.unit;
  const imp = data.impression_id;
  handle.impressionId = imp;
  handle.brand = u.brand;
  const isReminder = u.format === "reminder";
  const hasRisk = !isReminder && u.risk_text;

  const why = h("div", { class: "ms-ad-why", hidden: true, role: "note" },
    h("strong", {}, "Why am I seeing this?"),
    h("p", {}, `Slot: ${u.why.slot} · Practice specialty: ${u.why.specialty} · State: ${u.why.state}.`),
    h("p", {}, u.why.text),
    h("p", { class: "ms-ad-muted" }, "At most 3 units a week per brand. One brand per therapeutic category in your office."));

  const isi = hasRisk ? h("div", { class: "ms-ad-isi-full", hidden: true },
    h("strong", {}, u.boxed_warning ? "Boxed warning and important safety information (fictional)" : "Important safety information (fictional)"),
    h("p", {}, u.risk_text),
    h("a", { href: piUrl(u.pi_path), target: "_blank", rel: "noopener", onclick: () => send(imp, "click") }, "Full prescribing information")) : null;

  const confirmHide = h("div", { class: "ms-ad-confirm", hidden: true },
    h("span", {}, `Hide ${u.brand} for 30 days?`),
    h("button", { type: "button", class: "btn btn-sm", onclick: () => doHide() }, "Hide"),
    h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => { confirmHide.hidden = true; } }, "Cancel"));

  const art = h("img", { class: "ms-ad-art", alt: `${u.brand} sample artwork`, src: svgUrl(u.art_svg), width: "320", height: "120" });

  const node = h("aside", { class: "ad-unit ms-ad", "aria-label": `Sponsored: ${u.brand}`, "data-format": u.format },
    h("div", { class: "ad-label" },
      h("span", {}, u.sponsored_label),
      h("span", { class: "ad-sample" }, u.sample_label)),
    h("p", { class: "ms-ad-sponsor" }, `From ${u.company}`),
    art,
    isReminder ? null : h("p", { class: "ms-ad-headline" }, u.headline),
    u.body ? h("p", { class: "ms-ad-body" }, u.body) : null,
    hasRisk ? h("div", { class: "ms-ad-risk" }, u.boxed_warning ? h("strong", {}, "Boxed warning. ") : null, u.risk_text) : null,
    h("div", { class: "ms-ad-actions" },
      h("a", { class: "ms-ad-link", href: piUrl(u.pi_path), target: "_blank", rel: "noopener", onclick: () => send(imp, "click") }, u.cta_label || "Prescribing information"),
      hasRisk ? h("button", { type: "button", class: "ms-ad-linkbtn", onclick: () => { isi.hidden = !isi.hidden; if (!isi.hidden) send(imp, "isi_open"); } }, "Safety information") : null,
      h("button", { type: "button", class: "ms-ad-linkbtn", "aria-expanded": "false", onclick: (e) => { why.hidden = !why.hidden; e.currentTarget.setAttribute("aria-expanded", String(!why.hidden)); } }, "Why am I seeing this?"),
      h("button", { type: "button", class: "ms-ad-linkbtn", onclick: () => { confirmHide.hidden = false; } }, "Hide")),
    confirmHide, isi, why);

  function doHide() {
    send(imp, "hide");
    try { sessionStorage.removeItem(CACHE_KEY); } catch { /* ignore */ }
    stopTracking();
    const note = h("p", { class: "ms-ad-hidden-note" }, `Hidden. You won't see ${u.brand} units for 30 days.`);
    node.replaceWith(note);
    handle.node = note; // so unmount removes the note too
  }

  handle.node = node;
  handle.container.appendChild(node);

  // ----- MRC-style tracking
  let inView = false, viewTimer = null, since = null, dwell = 0;
  const visibleNow = () => document.visibilityState === "visible";
  const tick = () => {
    const counting = inView && visibleNow();
    if (counting && since === null) since = performance.now();
    if (!counting && since !== null) { dwell += performance.now() - since; since = null; }
    if (counting && !viewTimer && !alreadySent(imp, "viewable")) {
      viewTimer = setTimeout(() => { viewTimer = null; if (inView && visibleNow()) send(imp, "viewable"); }, 1000);
    }
    if (!counting && viewTimer) { clearTimeout(viewTimer); viewTimer = null; }
  };
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) inView = e.isIntersecting && e.intersectionRatio >= 0.5;
    tick();
  }, { threshold: [0, 0.5, 1] });
  const onVis = () => tick();
  document.addEventListener("visibilitychange", onVis);

  function startTracking() {
    if (handle.unmounted) return;
    send(imp, "rendered");
    io.observe(node);
  }
  function stopTracking() {
    io.disconnect();
    document.removeEventListener("visibilitychange", onVis);
    if (viewTimer) { clearTimeout(viewTimer); viewTimer = null; }
    if (since !== null) { dwell += performance.now() - since; since = null; }
    if (dwell > 0) send(imp, "dwell", { dwell_ms: Math.round(dwell) });
  }
  handle.stopTracking = stopTracking;
  if (art.complete) startTracking(); else { art.addEventListener("load", startTracking, { once: true }); art.addEventListener("error", startTracking, { once: true }); }
}

function unmount(handle) {
  if (handle.unmounted) return;
  handle.unmounted = true;
  handles.delete(handle);
  if (handle.stopTracking) handle.stopTracking();
  if (handle.node && handle.node.isConnected) handle.node.remove();
}

export function unmountAllSlots() {
  for (const hd of Array.from(handles)) unmount(hd);
  closeTray();
}

function closeTray() {
  if (tray) { tray.remove(); tray = null; }
}

export async function openResourcesTray({ prescriberId, specialty, state }) {
  ensureCss();
  closeTray();
  const body = h("div", { class: "drawer-body" }, h("p", { class: "ms-ad-muted" }, "Loading resources…"));
  tray = h("aside", { class: "drawer ms-tray", role: "dialog", "aria-label": "Clinician resources" },
    h("div", { class: "drawer-head" },
      h("div", {}, h("h3", {}, "Clinician resources"), h("p", { class: "ms-ad-muted" }, "Sponsored · for the clinician only")),
      h("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: closeTray, "aria-label": "Close" }, "Close")),
    body);
  document.body.appendChild(tray);
  let data = { items: [] };
  try {
    const res = await fetch(`${BASE}/tray`, {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", ...sessionHeaders() },
      body: JSON.stringify({ prescriber_id: prescriberId, specialty, state, hour: new Date().getHours() }),
    });
    if (res.ok) data = await res.json();
  } catch { /* show empty state */ }
  if (!tray) return;
  body.replaceChildren(
    h("div", { class: "banner banner-sim" }, "Every brand here is fictional: sample ads, not real products."),
    ...(data.items.length ? data.items.map((it) => h("div", { class: "ad-unit ms-tray-item" },
      h("div", { class: "ad-label" }, h("span", {}, "Sponsored"), h("span", { class: "ad-sample" }, "Sample ad, not a real product")),
      h("p", { class: "ms-ad-sponsor" }, `From ${it.company}`),
      h("p", { class: "ms-ad-headline" }, `${it.brand} (${it.generic})`),
      h("p", { class: "ms-ad-body" }, it.description),
      it.risk_text ? h("div", { class: "ms-ad-risk" }, it.risk_text) : null,
      h("a", { class: "ms-ad-link", href: piUrl(it.pi_path), target: "_blank", rel: "noopener", onclick: () => send(it.impression_id, "click") }, "Prescribing information")))
      : [h("p", { class: "ms-ad-muted" }, "No sponsored resources for your practice right now.")]),
    h("p", { class: "ms-ad-muted" }, data.note || ""));
  return { close: closeTray };
}
