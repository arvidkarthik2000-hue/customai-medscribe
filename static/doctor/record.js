// Live visit: microphone -> live transcript, local recording for the final pass.
// A clinical screen: no sponsored units while recording.
import { api, el, mount, toast, fmt, modal } from "/js/common.js";
import { MicCapture, LiveTranscriber, uploadWithProgress } from "/doctor/audio.js";

export async function render(root, { visitId }, ctx) {
  const visit = await api(`/api/visits/${visitId}`);
  if (visit.status !== "draft") { location.hash = `#/visit/${visitId}/${visit.status === "processing" ? "processing" : "review"}`; return; }
  const [chart, keyterms] = await Promise.all([
    api(`/api/patients/${visit.patient_id}`),
    api(`/api/visits/${visitId}/keyterms`).catch(() => ({ live: [] })),
  ]);
  const maxSeconds = (ctx.config.limits.max_visit_minutes || 20) * 60;

  // ---------- state
  const turns = new Map(); // key "session:order" -> {key, session, order, text, final, speaker, start_ms, end_ms}
  // Each reconnect starts a new AssemblyAI stream whose word times restart at 0,
  // so every session's times are shifted by when that session began.
  const sessionOffsetMs = new Map();
  const unassigned = (spk) => !spk || spk === "PENDING" || spk === "UNKNOWN";
  let pendingReconnect = null;
  let phase = "starting"; // starting | live | paused | stopping | error
  let elapsed = 0; let lastTick = 0; let timerId = null;
  let liveStatus = "connecting";
  let warnedLimit = false;
  let saveTimer = null;
  let mic = null; let live = null;
  let destroyed = false;

  // ---------- UI
  const timerEl = el("div", { class: "rec-timer mono" }, "0:00");
  const levelBar = el("div", { class: "rec-level-bar" });
  const statusEl = el("div", { class: "rec-status" });
  const transcriptEl = el("div", { class: "transcript live", "aria-live": "polite" });
  const pauseBtn = el("button", { class: "btn btn-lg", disabled: true, onclick: togglePause }, "Pause");
  const endBtn = el("button", { class: "btn btn-primary btn-lg", disabled: true, onclick: endVisit }, "End visit");
  const cancelBtn = el("button", { class: "btn btn-ghost", onclick: cancelVisit }, "Discard");
  const startBtn = el("button", { class: "btn btn-primary btn-lg hidden", onclick: () => start() }, "Allow microphone and start");
  const banner = el("div");

  mount(root,
    el("div", { class: "page-title" },
      el("div", {},
        el("div", { class: "eyebrow" }, "Recording visit"),
        el("h1", {}, chart.name),
        el("div", { class: "muted" }, `${chart.age} ${chart.sex} · ${visit.template.toUpperCase()} · ${visit.style}`)),
      el("div", { class: "row" }, cancelBtn)),
    banner,
    el("div", { class: "rec-grid" },
      el("section", { class: "card rec-panel" },
        el("div", { class: "rec-head" },
          el("div", { class: "row" }, el("span", { class: "dot dot-live", id: "rec-dot" }), timerEl),
          el("div", { class: "rec-level" }, levelBar),
          statusEl),
        el("div", { class: "rec-actions" }, startBtn, pauseBtn, endBtn),
        el("p", { class: "muted small", style: { margin: "10px 0 0", textAlign: "center" } }, "Nothing sponsored appears while recording or on any clinical screen.")),
      el("section", { class: "card" },
        el("div", { class: "card-head" }, el("h3", {}, "Live transcript"), el("span", { class: "muted small" }, "Draft quality; a final pass runs when you end the visit")),
        el("div", { class: "card-body transcript-scroll" }, transcriptEl)),
      el("section", { class: "card" },
        el("div", { class: "card-head" }, el("h3", {}, "Listening for")),
        el("div", { class: "card-body" },
          el("p", { class: "muted small", style: { marginTop: 0 } }, "This patient's medicines, allergies and problems are sent as key terms so drug names are heard correctly."),
          el("div", { class: "row-wrap" }, (keyterms.live || []).slice(0, 18).map((k) => el("span", { class: "badge" }, k)),
            (keyterms.live || []).length > 18 ? el("span", { class: "muted small" }, `+${keyterms.live.length - 18} more`) : null)))));

  setStatus("connecting", "Connecting…");

  // ---------- transcript rendering
  function renderTranscript() {
    const items = Array.from(turns.values()).sort((a, b) => a.session - b.session || a.order - b.order);
    if (!items.length) {
      mount(transcriptEl, el("p", { class: "muted" }, phase === "live" ? "Listening… start speaking." : "Waiting for audio…"));
      return;
    }
    mount(transcriptEl, items.map((t) => el("div", { class: `turn ${t.final ? "" : "partial"}` },
      el("span", { class: `spk spk-${(t.speaker || "U").replace(/[^A-Z]/g, "") || "U"}` }, !unassigned(t.speaker) ? `Speaker ${t.speaker}` : "Speaker …"),
      el("span", { class: "turn-text" }, t.text))));
    const scroller = transcriptEl.parentElement;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }

  function onTurn(session, msg) {
    const order = msg.turn_order ?? 0;
    const key = `${session}:${order}`;
    const words = msg.words || [];
    const prev = turns.get(key) || {};
    const off = sessionOffsetMs.get(session) || 0;
    turns.set(key, {
      key, session, order,
      text: msg.transcript || (words.map((w) => w.text).join(" ")),
      final: !!msg.end_of_turn,
      speaker: !unassigned(msg.speaker_label) ? msg.speaker_label : prev.speaker || "",
      start_ms: words.length && words[0].start != null ? words[0].start + off : prev.start_ms,
      end_ms: words.length && words[words.length - 1].end != null ? words[words.length - 1].end + off : prev.end_ms,
    });
    renderTranscript();
  }

  function onRevision(session, msg) {
    for (const r of msg.revisions || []) {
      const t = turns.get(`${session}:${r.turn_order}`);
      if (t && !unassigned(r.speaker_label)) t.speaker = r.speaker_label;
    }
    renderTranscript();
  }

  function liveTurnsPayload() {
    return Array.from(turns.values())
      .filter((t) => t.text && t.text.trim())
      .sort((a, b) => a.session - b.session || a.order - b.order)
      .map((t, i) => ({ id: `t${i + 1}`, speaker: unassigned(t.speaker) ? "" : t.speaker, role: "unknown", text: t.text.trim(), start_ms: t.start_ms ?? null, end_ms: t.end_ms ?? null, final: t.final }));
  }

  async function saveTurns() {
    try { await api(`/api/visits/${visitId}/live-turns`, { method: "POST", body: { turns: liveTurnsPayload(), audio_seconds: Math.round(elapsed) } }); } catch { /* retried next tick */ }
  }

  // ---------- status
  function setStatus(kind, text) {
    liveStatus = kind;
    const cls = { live: "badge-ok", connecting: "badge-info", dropped: "badge-warning", error: "badge-critical", paused: "badge-warning", local: "badge-warning" }[kind] || "";
    mount(statusEl, el("span", { class: `badge ${cls}` }, text));
  }

  function showBanner(kind, text, action) {
    mount(banner, el("div", { class: `banner banner-${kind}`, style: { marginBottom: "12px" } }, el("div", { class: "grow" }, text), action || null));
  }

  // ---------- timer
  function tick() {
    const now = performance.now();
    if (phase === "live") elapsed += (now - lastTick) / 1000;
    lastTick = now;
    timerEl.textContent = fmt.dur(elapsed);
    if (!warnedLimit && elapsed > maxSeconds - 60) { warnedLimit = true; toast("One minute left before this demo's visit length limit.", "info", 5000); }
    if (elapsed >= maxSeconds && phase === "live") { toast("Visit length limit reached; ending the visit.", "info", 5000); endVisit(); }
  }

  // ---------- lifecycle
  async function connectLive() {
    live = new LiveTranscriber({
      getToken: () => api("/api/stt/token", { method: "POST", body: { visit_id: visitId } }),
      onTurn, onRevision,
      onStatus: (kind, info) => {
        if (destroyed) return;
        if (kind === "live") {
          sessionOffsetMs.set(live.session, Math.round(elapsed * 1000));
          const model = live && live.model ? ` · ${live.model}` : "";
          const mm = live && live.domain === "medical-v1" ? " · Medical Mode" : live && live.domain ? "" : " · Medical Mode requested";
          setStatus("live", `Live transcript${model}${mm}`);
          banner.textContent = "";
        } else if (kind === "dropped" && phase !== "stopping") {
          if (info && info.code === 1008) {
            setStatus("local", "Recording locally");
            showBanner("warning", `The live transcript service refused the session (${info.reason || "unauthorized"}). Recording continues on this device; the final pass will transcribe everything.`);
            return;
          }
          if (phase === "paused") { pendingReconnect = info || {}; setStatus("paused", "Paused: not recording"); return; }
          setStatus("dropped", "Live transcript paused");
          reconnect(info);
        } else if (kind === "error") {
          setStatus("error", "Live transcript error");
          console.warn("AssemblyAI error", info);
        }
      },
    });
    await live.connect();
  }

  async function reconnect(info) {
    if (!live || live.reconnects >= 2 || phase === "stopping" || destroyed) {
      setStatus("local", "Recording locally");
      showBanner("warning", "The live transcript connection dropped. Recording continues on this device, and the final pass will transcribe the whole visit when you end it.");
      return;
    }
    live.reconnects += 1;
    const attempts = live.reconnects;
    try {
      live.closing = false;
      await live.connect();
      live.reconnects = attempts;
    } catch (e) {
      setStatus("local", "Recording locally");
      showBanner("warning", `Live transcript unavailable (${(info && info.reason) || e.message}). Recording continues; the final pass will transcribe everything.`);
    }
  }

  async function start() {
    startBtn.classList.add("hidden");
    try {
      mic = new MicCapture({
        onPcm: (pcm) => { if (live) live.send(pcm); },
        onLevel: (lvl) => { levelBar.style.width = `${Math.min(100, Math.round(lvl * 400))}%`; },
      });
      await mic.start();
    } catch (e) {
      phase = "error";
      showBanner("critical", micError(e));
      startBtn.classList.remove("hidden");
      startBtn.textContent = "Try again";
      return;
    }
    phase = "live";
    window.__msRecording = true;
    lastTick = performance.now();
    timerId = setInterval(tick, 250);
    pauseBtn.disabled = false;
    endBtn.disabled = false;
    renderTranscript();
    saveTimer = setInterval(saveTurns, 10000);
    try {
      await connectLive();
    } catch (e) {
      if (e && e.status === 409) {
        setStatus("local", "Recording locally");
        showBanner("warning", `${e.message} Recording continues on this device; the final pass will still run.`);
      } else if (e && e.status === 503) {
        setStatus("local", "Recording locally");
        showBanner("warning", "Live transcription isn't configured on this server. Recording continues locally.");
      } else {
        setStatus("local", "Recording locally");
        showBanner("warning", `Live transcript unavailable: ${e.message} Recording continues; the final pass will transcribe everything.`);
      }
    }
  }

  function togglePause() {
    if (phase === "live") {
      phase = "paused"; mic && mic.pause(); pauseBtn.textContent = "Resume"; setStatus("paused", "Paused: not recording");
      document.getElementById("rec-dot")?.classList.remove("dot-live");
    } else if (phase === "paused") {
      phase = "live"; mic && mic.resume(); lastTick = performance.now(); pauseBtn.textContent = "Pause";
      if (pendingReconnect) { const info = pendingReconnect; pendingReconnect = null; reconnect(info); }
      setStatus(live && live.ready ? "live" : "local", live && live.ready ? "Live transcript · Medical Mode" : "Recording locally");
      document.getElementById("rec-dot")?.classList.add("dot-live");
    }
  }

  async function endVisit() {
    if (phase === "stopping") return;
    const wasPhase = phase;
    phase = "stopping";
    pauseBtn.disabled = true; endBtn.disabled = true; cancelBtn.disabled = true;
    clearInterval(timerId); clearInterval(saveTimer);
    setStatus("connecting", "Finishing…");
    try { if (live) await live.terminate(); } catch { /* ignore */ }
    let blob = null;
    try { blob = mic ? await mic.stop() : null; } catch { blob = null; }
    window.__msRecording = false;
    await saveTurns();

    const progress = el("div", { class: "upload-progress" }, el("div", { class: "upload-bar" }));
    const sizeMb = blob ? blob.size / (1024 * 1024) : 0;
    if (blob && blob.size > 2000 && sizeMb <= ctx.config.limits.max_upload_mb) {
      showBanner("info", "Uploading the recording for the final pass…", progress);
      try {
        await uploadWithProgress(`/api/visits/${visitId}/audio`, blob, `visit.${mic.extension()}`, (p) => { progress.firstChild.style.width = `${Math.round(p * 100)}%`; });
        location.hash = `#/visit/${visitId}/processing`;
        return;
      } catch (e) {
        toast(`Upload failed (${e.message}); using the live transcript instead.`, "error", 6000);
      }
    }
    if (liveTurnsPayload().length === 0) {
      phase = wasPhase === "paused" ? "paused" : "error";
      showBanner("critical", "No speech was captured, so there is nothing to draft. Check the microphone and try again.",
        el("a", { class: "btn btn-sm", href: `#/patient/${visit.patient_id}` }, "Back to chart"));
      return;
    }
    try {
      await api(`/api/visits/${visitId}/process`, { method: "POST", body: { source: "live" } });
      location.hash = `#/visit/${visitId}/processing`;
    } catch (e) {
      showBanner("critical", `Could not start drafting: ${e.message}`);
    }
  }

  function cancelVisit() {
    modal({
      title: "Discard this recording?",
      body: el("p", {}, "The audio and transcript from this visit will be thrown away."),
      actions: [{ label: "Keep recording" }, { label: "Discard", danger: true, onClick: async () => {
        destroyed = true; window.__msRecording = false;
        try { live && live.hardClose(); } catch { /* ignore */ }
        try { mic && mic.release(); } catch { /* ignore */ }
        location.hash = `#/patient/${visit.patient_id}`;
      } }],
    });
  }

  // Start right away (the consent modal click counts as the user gesture).
  start();

  return async () => {
    destroyed = true;
    clearInterval(timerId); clearInterval(saveTimer);
    if (phase === "live" || phase === "paused") {
      try { await saveTurns(); } catch { /* ignore */ }
    }
    try { live && live.hardClose(); } catch { /* ignore */ }
    try { if (phase !== "stopping") mic && mic.release(); } catch { /* ignore */ }
    window.__msRecording = false;
  };
}

function micError(e) {
  const name = e && e.name;
  if (name === "NotAllowedError" || name === "SecurityError") return "Microphone access was blocked. Allow the microphone for this site in the browser's address bar, then try again.";
  if (name === "NotFoundError") return "No microphone was found on this device.";
  if (name === "NotReadableError") return "The microphone is in use by another app.";
  return `Could not start the microphone: ${(e && e.message) || e}`;
}
