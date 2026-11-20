// Phone microphone: records a visit for the screen in the room (a wallboard that can't use a
// microphone itself). The pairing code from the screen is this page's only credential.
// Audio streams to the server for the live transcript, which the screen shows; when the doctor
// stops, the phone sends its recording for the final pass, and the screen moves on to the note.
import { MicCapture, LiveTranscriber, uploadWithProgress } from "/doctor/audio.js";

window.__msBoot = true; // read by /js/boot-check.js

const box = document.getElementById("pm");
const params = new URLSearchParams(location.search);
let code = (params.get("c") || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
let info = null;
let mic = null, live = null, wakeLock = null;
let phase = "idle"; // idle | recording | paused | stopping | done | error
let elapsed = 0, lastTick = 0, tickId = null, progressId = null, liveState = "off";
const turns = new Map();

function h(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return n;
}
function show(...kids) { box.replaceChildren(...kids); }
function fmt(s) { s = Math.max(0, Math.floor(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }

async function call(path, body) {
  const r = await fetch(path, {
    method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null; try { data = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) { const e = new Error((data && data.detail) || `The server answered ${r.status}.`); e.status = r.status; throw e; }
  return data;
}

function failed(message, retry) {
  phase = "error";
  show(h("div", { class: "banner banner-critical" }, message),
    retry ? h("button", { class: "btn btn-lg", style: "margin-top:12px", onclick: retry }, "Try again") : null);
}

// ---------- 1. the code
function askForCode(message) {
  const input = h("input", { class: "input pm-code", inputmode: "text", autocomplete: "off", maxlength: "8", placeholder: "CODE", "aria-label": "Code from the screen" });
  const go = () => { code = input.value.toUpperCase().replace(/[^0-9A-Z]/g, ""); if (code.length >= 6) connect(); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  show(message ? h("div", { class: "banner banner-warning", style: "margin-bottom:12px" }, message) : null,
    h("div", { class: "field" }, h("label", {}, "Code shown on the screen"), input),
    h("button", { class: "btn btn-primary btn-lg", style: "margin-top:12px", onclick: go }, "Connect"));
  input.focus();
}

async function connect() {
  show(h("div", { class: "empty" }, h("span", { class: "spinner" })));
  try {
    info = await call(`/api/phone/${code}`);
  } catch (e) {
    if (e.status === 404 || e.status === 429) return askForCode(e.message);
    return failed(`Could not reach the server (${e.message}).`, connect);
  }
  history.replaceState(null, "", `/m/?c=${code}`);
  ready();
}

// ---------- 2. ready
function ready(note) {
  phase = "idle";
  show(h("p", { class: "muted", style: "margin-top:0;text-align:center" }, `Paired with the screen · code ${info.code_display}`),
    note ? h("div", { class: "banner banner-warning", style: "margin-bottom:12px" }, note) : null,
    h("button", { class: "btn btn-primary btn-lg", onclick: start }, "Start recording"),
    h("p", { class: "muted small", style: "margin-bottom:0;text-align:center" }, "Your browser will ask to use the microphone. Everyone in the room has to agree to the recording."));
}

// ---------- 3. recording
const timerEl = h("div", { class: "pm-timer mono" }, "0:00");
const levelEl = h("span");
const statusEl = h("div", { class: "pm-status" });
const captionEl = h("p", { class: "pm-caption", "aria-live": "polite" });
const pauseBtn = h("button", { class: "btn", onclick: togglePause }, "Pause");
const stopBtn = h("button", { class: "btn btn-primary", onclick: stopAndSend }, "Stop and send");

function setStatus(kind, text) {
  const cls = { live: "badge-ok", info: "badge-info", warn: "badge-warning", bad: "badge-critical" }[kind] || "";
  statusEl.replaceChildren(h("span", { class: `badge ${cls}` }, text));
}

function renderCaption() {
  const last = Array.from(turns.values()).sort((a, b) => a.k - b.k).slice(-2).map((t) => t.text).filter(Boolean);
  captionEl.textContent = last.length ? last.join(" ") : phase === "paused" ? "" : "Listening… the transcript appears on the screen.";
}

async function start() {
  show(h("div", { class: "empty" }, h("span", { class: "spinner" })));
  mic = new MicCapture({
    onPcm: (pcm) => { if (live) live.send(pcm); },
    onLevel: (lvl) => { levelEl.style.width = `${Math.min(100, Math.round(lvl * 400))}%`; },
  });
  try {
    await mic.start();
  } catch (e) {
    const name = e && e.name;
    const why = name === "NotAllowedError" || name === "SecurityError"
      ? "The microphone was blocked. Allow it for this site in the browser (the icon next to the address), then try again."
      : name === "NotFoundError" ? "This phone has no microphone the browser can use." : `Could not start the microphone: ${(e && e.message) || e}`;
    return ready(why);
  }
  phase = "recording";
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { wakeLock = null; }
  lastTick = performance.now();
  tickId = setInterval(tick, 250);
  progressId = setInterval(report, 2000);
  show(timerEl, h("div", { class: "pm-level" }, levelEl), statusEl, captionEl, h("div", { class: "pm-row" }, pauseBtn, stopBtn));
  renderCaption();
  setStatus("info", "Connecting the live transcript…");
  report();
  connectLive(0);
}

let sessionNo = 0;
const recording = () => phase === "recording" || phase === "paused";

function liveUnavailable() {
  liveState = "unavailable";
  setStatus("warn", "Recording on this phone · the screen gets the transcript when you send it");
  report();
}

// Live transcript through the server. If it never opens, the phone records on and the final
// pass transcribes everything; if it drops after working, it reconnects up to twice.
function connectLive(attempt) {
  const sess = ++sessionNo;
  liveState = "connecting";
  live = new LiveTranscriber({
    getToken: () => call(`/api/phone/${code}/live`, {}),
    onTurn: (_s, msg) => { turns.set(`${sess}:${msg.turn_order ?? 0}`, { k: sess * 1e6 + (msg.turn_order ?? 0), text: msg.transcript || "" }); renderCaption(); },
    onRevision: () => {},
    onStatus: (kind) => {
      if (kind === "live") { liveState = "live"; setStatus("live", "Recording · live transcript on the screen"); }
      else if (kind === "dropped" && liveState === "live" && recording()) {
        liveState = "connecting";
        if (attempt < 2) setTimeout(() => { if (recording()) connectLive(attempt + 1); }, 1000);
        else liveUnavailable();
      }
    },
  });
  live.connect().catch(() => { if (recording() && liveState !== "live") liveUnavailable(); });
}

function tick() {
  const now = performance.now();
  if (phase === "recording") elapsed += (now - lastTick) / 1000;
  lastTick = now;
  timerEl.textContent = fmt(elapsed);
  if (info && elapsed >= info.max_seconds && phase === "recording") stopAndSend();
}

async function report(state) {
  try { await call(`/api/phone/${code}/progress`, { elapsed_s: Math.round(elapsed * 10) / 10, state: state || (phase === "paused" ? "paused" : "recording"), live: liveState }); }
  catch (e) { if (e.status === 404) { stopEverything(); failed("The screen ended this recording, or its code expired. Show a new code on the screen and scan it again."); } }
}

function togglePause() {
  if (phase === "recording") { phase = "paused"; mic.pause(); pauseBtn.textContent = "Resume"; setStatus("warn", "Paused · not recording"); }
  else if (phase === "paused") { phase = "recording"; mic.resume(); lastTick = performance.now(); pauseBtn.textContent = "Pause"; setStatus(liveState === "live" ? "live" : "warn", liveState === "live" ? "Recording · live transcript on the screen" : "Recording on this phone"); }
  report();
}

function stopEverything() {
  clearInterval(tickId); clearInterval(progressId);
  try { live && live.hardClose(); } catch { /* ignore */ }
  try { mic && mic.release(); } catch { /* ignore */ }
  try { wakeLock && wakeLock.release(); } catch { /* ignore */ }
}

async function stopAndSend() {
  if (phase !== "recording" && phase !== "paused") return;
  phase = "stopping";
  clearInterval(tickId); clearInterval(progressId);
  pauseBtn.disabled = true; stopBtn.disabled = true;
  setStatus("info", "Finishing…");
  await report("sending");
  try { if (live) await live.terminate(); } catch { /* ignore */ }
  let blob = null;
  try { blob = mic ? await mic.stop() : null; } catch { blob = null; }
  try { wakeLock && wakeLock.release(); } catch { /* ignore */ }
  const bar = h("span");
  show(h("p", { style: "margin-top:0" }, "Sending the recording…"), h("div", { class: "pm-progress" }, bar));
  if (blob && blob.size > 2000) {
    try {
      await uploadWithProgress(`/api/phone/${code}/audio`, blob, `visit.${mic.extension()}`, (p) => { bar.style.width = `${Math.round(p * 100)}%`; });
      return done();
    } catch (e) {
      console.warn("upload failed", e);
    }
  }
  try {
    await call(`/api/phone/${code}/finish`, {});
    done("The recording couldn't be sent, so the note is drafted from the live transcript.");
  } catch (e) {
    failed(e.message || "Could not finish the visit.");
  }
}

function done(note) {
  phase = "done";
  show(h("div", { class: "pm-done" },
    h("div", { class: "pm-tick", "aria-hidden": "true" }, "✓"),
    h("h2", {}, "Sent"),
    h("p", { class: "muted" }, "The note is being drafted on the screen. You can close this page."),
    note ? h("p", { class: "muted small" }, note) : null));
}

window.addEventListener("beforeunload", (e) => { if (phase === "recording" || phase === "paused") { e.preventDefault(); e.returnValue = ""; } });

if (code.length >= 6) connect(); else askForCode();
