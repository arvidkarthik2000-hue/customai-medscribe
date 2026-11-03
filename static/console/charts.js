// Small SVG chart kit for the console and sponsor report. No dependencies.
// Specs (dataviz skill): one series per chart in slot-1 blue, bars <= 24px with a
// 4px rounded data end and square baseline, 2px gaps, hairline solid grid, one
// axis, text in ink tokens, hover/focus tooltips that never gate (every chart has
// a table view), and a "Simulated" watermark on every chart.

const NS = "http://www.w3.org/2000/svg";
const SERIES = "#2a78d6";
const SERIES_HOVER = "#256abf";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";
const MUTED = "#77818c";
const INK = "#15202b";

function s(tag, attrs = {}, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}

export function compact(n) {
  if (n === null || n === undefined || isNaN(n)) return "–";
  const a = Math.abs(n);
  if (a >= 1e6) return `${(n / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e4) return `${(n / 1e3).toFixed(0)}K`;
  return Math.round(n).toLocaleString("en-US");
}

function niceMax(v) {
  if (!v || v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * exp >= v) return m * exp;
  return 10 * exp;
}

function ticks(max, n = 4) {
  const step = max / n;
  return Array.from({ length: n + 1 }, (_, i) => i * step);
}

// Rounded data end, square baseline.
function colPath(x, y, w, h, r = 4) {
  if (h <= 0) return "";
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}
function rowPath(x, y, w, h, r = 4) {
  if (w <= 0) return "";
  r = Math.min(r, h / 2, w);
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`;
}

let tip;
function tooltip() {
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "viz-tip";
    tip.setAttribute("role", "status");
    document.body.appendChild(tip);
  }
  return tip;
}
function showTip(evt, rows, title) {
  const t = tooltip();
  t.replaceChildren();
  if (title) { const h = document.createElement("div"); h.className = "viz-tip-title"; h.textContent = title; t.appendChild(h); }
  for (const [label, value] of rows) {
    const r = document.createElement("div");
    r.className = "viz-tip-row";
    const v = document.createElement("strong"); v.textContent = value;
    const l = document.createElement("span"); l.textContent = label;
    r.append(v, l);
    t.appendChild(r);
  }
  t.style.display = "block";
  const rect = evt.target.getBoundingClientRect ? evt.target.getBoundingClientRect() : { left: evt.clientX, top: evt.clientY, width: 0 };
  const x = (evt.clientX ?? rect.left + rect.width / 2) + 14;
  const y = (evt.clientY ?? rect.top) + 14;
  const w = t.offsetWidth, hh = t.offsetHeight;
  t.style.left = `${Math.min(x, window.innerWidth - w - 8)}px`;
  t.style.top = `${Math.min(y, window.innerHeight - hh - 8)}px`;
}
function hideTip() { if (tip) tip.style.display = "none"; }

function watermark(svg, width) {
  svg.appendChild(s("text", { x: width - 8, y: 14, "text-anchor": "end", "font-size": 10, "font-weight": 700, "letter-spacing": "0.08em", fill: "#a8814a" }, "SIMULATED"));
}

// Vertical columns over time. data: [{label, value, tip: [[label, value]...], title}]
export function columnChart(el, { data, height = 220, valueLabel = "", every = 5 }) {
  el.replaceChildren();
  const width = Math.max(320, el.clientWidth || 640);
  const m = { t: 22, r: 12, b: 28, l: 44 };
  const pw = width - m.l - m.r, ph = height - m.t - m.b;
  const max = niceMax(Math.max(...data.map((d) => d.value), 0));
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height, role: "img", "aria-label": valueLabel, class: "viz" });
  for (const tv of ticks(max)) {
    const y = m.t + ph - (tv / max) * ph;
    svg.appendChild(s("line", { x1: m.l, x2: width - m.r, y1: y, y2: y, stroke: tv === 0 ? AXIS : GRID, "stroke-width": 1 }));
    svg.appendChild(s("text", { x: m.l - 8, y: y + 4, "text-anchor": "end", "font-size": 11, fill: MUTED, class: "tnum" }, compact(tv)));
  }
  const band = pw / Math.max(1, data.length);
  const bw = Math.max(2, Math.min(24, band - 2));
  data.forEach((d, i) => {
    const x = m.l + i * band + (band - bw) / 2;
    const h = (d.value / max) * ph;
    const y = m.t + ph - h;
    const bar = s("path", { d: colPath(x, y, bw, h), fill: SERIES });
    const hit = s("rect", { x: m.l + i * band, y: m.t, width: band, height: ph, fill: "transparent", tabindex: 0, "aria-label": `${d.title || d.label}: ${d.value}` });
    const on = (e) => { bar.setAttribute("fill", SERIES_HOVER); showTip(e, d.tip || [[valueLabel, compact(d.value)]], d.title || d.label); };
    const off = () => { bar.setAttribute("fill", SERIES); hideTip(); };
    hit.addEventListener("pointermove", on); hit.addEventListener("pointerleave", off);
    hit.addEventListener("focus", (e) => { const r = hit.getBoundingClientRect(); on({ clientX: r.left + r.width / 2, clientY: r.top + 20, target: hit }); });
    hit.addEventListener("blur", off);
    svg.append(bar, hit);
    if (i % every === 0 || i === data.length - 1) {
      svg.appendChild(s("text", { x: m.l + i * band + band / 2, y: height - 8, "text-anchor": "middle", "font-size": 11, fill: MUTED }, d.label));
    }
  });
  watermark(svg, width);
  el.appendChild(svg);
}

// Horizontal bars. data: [{label, value, display, tip}]
export function barList(el, { data, valueLabel = "", format = compact, rowH = 30 }) {
  el.replaceChildren();
  const width = Math.max(300, el.clientWidth || 480);
  const labelW = Math.min(170, Math.max(90, width * 0.3));
  const valueW = 64;
  const m = { t: 22, r: 8, b: 6 };
  const height = m.t + data.length * rowH + m.b;
  const pw = width - labelW - valueW - m.r;
  const max = Math.max(...data.map((d) => d.value), 0) || 1;
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height, role: "img", "aria-label": valueLabel, class: "viz" });
  svg.appendChild(s("line", { x1: labelW, x2: labelW, y1: m.t - 4, y2: height - m.b, stroke: AXIS, "stroke-width": 1 }));
  data.forEach((d, i) => {
    const y = m.t + i * rowH;
    const bh = Math.min(18, rowH - 8);
    const w = (d.value / max) * pw;
    svg.appendChild(s("text", { x: labelW - 8, y: y + rowH / 2 + 4, "text-anchor": "end", "font-size": 12, fill: INK }, d.label));
    const bar = s("path", { d: rowPath(labelW, y + (rowH - bh) / 2, Math.max(w, 0), bh), fill: SERIES });
    svg.appendChild(bar);
    svg.appendChild(s("text", { x: labelW + w + 6, y: y + rowH / 2 + 4, "font-size": 12, fill: INK, class: "tnum" }, d.display ?? format(d.value)));
    const hit = s("rect", { x: 0, y, width, height: rowH, fill: "transparent", tabindex: 0, "aria-label": `${d.label}: ${d.display ?? d.value}` });
    const on = (e) => { bar.setAttribute("fill", SERIES_HOVER); showTip(e, d.tip || [[valueLabel, d.display ?? format(d.value)]], d.label); };
    const off = () => { bar.setAttribute("fill", SERIES); hideTip(); };
    hit.addEventListener("pointermove", on); hit.addEventListener("pointerleave", off);
    hit.addEventListener("focus", (e) => { const r = hit.getBoundingClientRect(); on({ clientX: r.left + 40, clientY: r.top + 10, target: hit }); });
    hit.addEventListener("blur", off);
    svg.appendChild(hit);
  });
  watermark(svg, width);
  el.appendChild(svg);
}

// Pacing bullets: bar = delivered share of booked, tick = expected to date.
// rows: [{label, delivered, booked, expected, note}]
export function pacingBullets(el, { rows }) {
  el.replaceChildren();
  const width = Math.max(320, el.clientWidth || 640);
  const labelW = Math.min(230, Math.max(120, width * 0.32));
  const valueW = 120;
  const rowH = 34, m = { t: 26, r: 10, b: 22 };
  const height = m.t + rows.length * rowH + m.b;
  const pw = width - labelW - valueW - m.r;
  const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height, role: "img", "aria-label": "Delivered against booked", class: "viz" });
  for (const pct of [0, 0.25, 0.5, 0.75, 1]) {
    const x = labelW + pct * pw;
    svg.appendChild(s("line", { x1: x, x2: x, y1: m.t - 6, y2: height - m.b, stroke: pct === 0 ? AXIS : GRID, "stroke-width": 1 }));
    svg.appendChild(s("text", { x, y: height - 6, "text-anchor": "middle", "font-size": 11, fill: MUTED }, `${Math.round(pct * 100)}%`));
  }
  rows.forEach((r, i) => {
    const y = m.t + i * rowH;
    const bh = 14;
    // Over-delivery is stated in the note; the bar stops at 100% so it never runs into the labels.
    const share = r.booked ? Math.min(1, r.delivered / r.booked) : 0;
    const exp = r.booked && r.expected !== null ? Math.min(1, r.expected / r.booked) : null;
    svg.appendChild(s("text", { x: labelW - 10, y: y + rowH / 2 + 4, "text-anchor": "end", "font-size": 12, fill: INK }, r.label));
    const bar = s("path", { d: rowPath(labelW, y + (rowH - bh) / 2, share * pw, bh), fill: SERIES });
    svg.appendChild(bar);
    if (exp !== null) {
      const x = labelW + exp * pw;
      svg.appendChild(s("line", { x1: x, x2: x, y1: y + 6, y2: y + rowH - 6, stroke: INK, "stroke-width": 2, "stroke-linecap": "round" }));
    }
    svg.appendChild(s("text", { x: width - m.r, y: y + rowH / 2 + 4, "text-anchor": "end", "font-size": 12, fill: INK, class: "tnum" }, r.note || ""));
    const hit = s("rect", { x: 0, y, width, height: rowH, fill: "transparent", tabindex: 0, "aria-label": `${r.label}: ${r.note}` });
    const on = (e) => {
      bar.setAttribute("fill", SERIES_HOVER);
      showTip(e, [["delivered (viewable)", compact(r.delivered)], ["expected to date", r.expected !== null ? compact(r.expected) : "–"], ["booked", r.booked ? compact(r.booked) : "flat fee"]], r.label);
    };
    const off = () => { bar.setAttribute("fill", SERIES); hideTip(); };
    hit.addEventListener("pointermove", on); hit.addEventListener("pointerleave", off);
    hit.addEventListener("focus", (e) => { const rc = hit.getBoundingClientRect(); on({ clientX: rc.left + 60, clientY: rc.top + 10, target: hit }); });
    hit.addEventListener("blur", off);
    svg.appendChild(hit);
  });
  // legend: two encodings
  const lg = s("g", { transform: `translate(${labelW}, 8)` });
  lg.appendChild(s("rect", { x: 0, y: 0, width: 12, height: 8, rx: 2, fill: SERIES }));
  lg.appendChild(s("text", { x: 18, y: 8, "font-size": 11, fill: MUTED }, "Delivered, share of booked"));
  lg.appendChild(s("line", { x1: 190, x2: 190, y1: -1, y2: 10, stroke: INK, "stroke-width": 2 }));
  lg.appendChild(s("text", { x: 198, y: 8, "font-size": 11, fill: MUTED }, "Expected to date"));
  svg.appendChild(lg);
  watermark(svg, width);
  el.appendChild(svg);
}

// A chart card with a chart/table toggle (the table is the accessible twin).
export function chartCard({ title, subtitle, render, table }) {
  const body = document.createElement("div");
  body.className = "viz-body";
  const tableWrap = document.createElement("div");
  tableWrap.className = "viz-table table-wrap";
  tableWrap.hidden = true;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn btn-ghost btn-sm no-print";
  btn.textContent = "Table";
  btn.addEventListener("click", () => {
    const showTable = tableWrap.hidden;
    tableWrap.hidden = !showTable;
    body.hidden = showTable;
    btn.textContent = showTable ? "Chart" : "Table";
    if (showTable && !tableWrap.firstChild) tableWrap.appendChild(buildTable(table()));
  });
  const head = document.createElement("div");
  head.className = "card-head";
  const h = document.createElement("div");
  const t = document.createElement("h3"); t.textContent = title; h.appendChild(t);
  if (subtitle) { const st = document.createElement("div"); st.className = "muted small"; st.textContent = subtitle; h.appendChild(st); }
  head.append(h, btn);
  const card = document.createElement("section");
  card.className = "card viz-card";
  const inner = document.createElement("div");
  inner.className = "card-body";
  inner.append(body, tableWrap);
  card.append(head, inner);
  requestAnimationFrame(() => render(body));
  let lastW = 0;
  const ro = new ResizeObserver(() => { const w = body.clientWidth; if (Math.abs(w - lastW) > 24 && !body.hidden) { lastW = w; render(body); } });
  ro.observe(body);
  return card;
}

function buildTable({ columns, rows }) {
  const t = document.createElement("table");
  t.className = "table";
  const thead = document.createElement("thead");
  const tr = document.createElement("tr");
  for (const c of columns) { const th = document.createElement("th"); th.textContent = c; tr.appendChild(th); }
  thead.appendChild(tr);
  const tb = document.createElement("tbody");
  for (const r of rows) {
    const row = document.createElement("tr");
    r.forEach((v, i) => { const td = document.createElement("td"); td.textContent = v; if (i > 0) td.className = "num"; row.appendChild(td); });
    tb.appendChild(row);
  }
  t.append(thead, tb);
  return t;
}
