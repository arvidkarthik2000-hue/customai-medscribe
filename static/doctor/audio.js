// Microphone capture for a visit.
// - PCM16 at 16 kHz goes to AssemblyAI's streaming WebSocket for the live transcript.
// - The same stream is recorded locally (Opus) for the higher-accuracy final pass.
// If the live connection drops, local recording continues, so nothing is lost.

export class MicCapture {
  constructor({ onPcm, onLevel }) {
    this.onPcm = onPcm;
    this.onLevel = onLevel;
    this.stream = null;
    this.ctx = null;
    this.node = null;
    this.sink = null;
    this.recorder = null;
    this.chunks = [];
    this.paused = false;
    this.mimeType = "";
  }

  async start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("This browser cannot record audio. Use a recent Chrome, Edge, Safari or Firefox over HTTPS.");
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.ctx = new Ctx();
    if (this.ctx.state === "suspended") { try { await this.ctx.resume(); } catch { /* resumed on next gesture */ } }
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.sink = this.ctx.createGain();
    this.sink.gain.value = 0; // keeps the graph pulled without playing anything back
    this.sink.connect(this.ctx.destination);

    if (this.ctx.audioWorklet && window.AudioWorkletNode) {
      await this.ctx.audioWorklet.addModule("/doctor/pcm-worklet.js");
      this.node = new AudioWorkletNode(this.ctx, "pcm-worklet", { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
      this.node.port.onmessage = (e) => {
        if (this.paused) return;
        const { pcm, level } = e.data;
        this.onLevel && this.onLevel(level);
        this.onPcm && this.onPcm(pcm);
      };
      source.connect(this.node);
      this.node.connect(this.sink);
    } else {
      this._scriptFallback(source);
    }
    this._startRecorder();
  }

  _scriptFallback(source) {
    // Older Safari: ScriptProcessor with the same resampling as the worklet.
    const proc = this.ctx.createScriptProcessor(4096, 1, 1);
    const ratio = this.ctx.sampleRate / 16000;
    let phase = 0, acc = 0, accN = 0, buf = new Int16Array(1600), idx = 0;
    proc.onaudioprocess = (ev) => {
      if (this.paused) return;
      const ch = ev.inputBuffer.getChannelData(0);
      let sq = 0;
      for (let i = 0; i < ch.length; i++) {
        const s = ch[i]; acc += s; accN++; sq += s * s; phase += 1;
        if (phase >= ratio) {
          phase -= ratio; let v = acc / accN; acc = 0; accN = 0;
          v = Math.max(-1, Math.min(1, v));
          buf[idx++] = v < 0 ? v * 0x8000 : v * 0x7fff;
          if (idx === buf.length) { this.onPcm && this.onPcm(buf.buffer); buf = new Int16Array(1600); idx = 0; }
        }
      }
      this.onLevel && this.onLevel(Math.sqrt(sq / ch.length));
    };
    source.connect(proc);
    proc.connect(this.sink);
    this.node = proc;
  }

  _startRecorder() {
    if (!window.MediaRecorder) return;
    const types = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4", "audio/webm"];
    this.mimeType = types.find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || "";
    try {
      this.recorder = new MediaRecorder(this.stream, this.mimeType ? { mimeType: this.mimeType, audioBitsPerSecond: 48000 } : undefined);
    } catch {
      this.recorder = new MediaRecorder(this.stream);
    }
    this.mimeType = this.recorder.mimeType || this.mimeType || "audio/webm";
    this.chunks = [];
    this.recorder.ondataavailable = (e) => { if (e.data && e.data.size) this.chunks.push(e.data); };
    this.recorder.start(1000);
  }

  pause() {
    this.paused = true;
    if (this.recorder && this.recorder.state === "recording") { try { this.recorder.pause(); } catch { /* ignore */ } }
  }

  resume() {
    this.paused = false;
    if (this.recorder && this.recorder.state === "paused") { try { this.recorder.resume(); } catch { /* ignore */ } }
  }

  // Stops everything and resolves with the local recording (or null).
  async stop() {
    this.paused = true;
    let blob = null;
    if (this.recorder && this.recorder.state !== "inactive") {
      blob = await new Promise((resolve) => {
        this.recorder.onstop = () => resolve(new Blob(this.chunks, { type: this.mimeType || "audio/webm" }));
        try { this.recorder.stop(); } catch { resolve(null); }
      });
    }
    this.release();
    return blob;
  }

  release() {
    try { this.node && this.node.disconnect(); } catch { /* ignore */ }
    try { this.sink && this.sink.disconnect(); } catch { /* ignore */ }
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.ctx && this.ctx.state !== "closed") { this.ctx.close().catch(() => {}); }
    this.stream = null; this.ctx = null; this.node = null;
  }

  extension() {
    if (/ogg/.test(this.mimeType)) return "ogg";
    if (/mp4/.test(this.mimeType)) return "m4a";
    return "webm";
  }
}

// One AssemblyAI streaming session. Calls onTurn(sessionIndex, msg) for every
// Turn message and onRevision for SpeakerRevision messages.
export class LiveTranscriber {
  constructor({ getToken, onTurn, onRevision, onStatus }) {
    this.getToken = getToken;
    this.onTurn = onTurn;
    this.onRevision = onRevision;
    this.onStatus = onStatus;
    this.ws = null;
    this.session = 0;
    this.ready = false;
    this.closing = false;
    this.reconnects = 0;
    this.terminated = null;
    this.model = "";
  }

  async connect() {
    const { token, ws_url } = await this.getToken();
    this.session += 1;
    const session = this.session;
    this.ready = false;
    const url = `${ws_url}${ws_url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    this.onStatus && this.onStatus("connecting");
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("The live transcript service did not answer in time.")), 10000);
      ws.onmessage = (ev) => {
        let msg; try { msg = JSON.parse(ev.data); } catch { return; }
        if (msg.type === "Begin") {
          clearTimeout(timer);
          this.ready = true;
          this.model = (msg.configuration && (msg.configuration.model || msg.configuration.speech_model)) || "";
          this.onStatus && this.onStatus("live", msg);
          resolve();
        } else if (msg.type === "Turn") {
          this.onTurn && this.onTurn(session, msg);
        } else if (msg.type === "SpeakerRevision") {
          this.onRevision && this.onRevision(session, msg);
        } else if (msg.type === "Termination") {
          this.terminated && this.terminated();
        } else if (msg.type === "Error" || msg.error) {
          this.onStatus && this.onStatus("error", msg);
        }
      };
      ws.onerror = () => { clearTimeout(timer); if (!this.ready) reject(new Error("Could not connect to the live transcript service.")); };
      ws.onclose = (ev) => {
        clearTimeout(timer);
        this.ready = false;
        if (!this.closing) {
          this.onStatus && this.onStatus("dropped", { code: ev.code, reason: ev.reason });
        }
        if (this.terminated) this.terminated();
        if (!this.closing && this.ready === false && ev.code !== 1000) reject(new Error(ev.reason || `Live transcript closed (${ev.code}).`));
      };
    });
  }

  send(pcm) {
    if (this.ws && this.ready && this.ws.readyState === WebSocket.OPEN) this.ws.send(pcm);
  }

  async terminate() {
    this.closing = true;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    await new Promise((resolve) => {
      const t = setTimeout(resolve, 4000);
      this.terminated = () => { clearTimeout(t); resolve(); };
      try { this.ws.send(JSON.stringify({ type: "Terminate" })); } catch { clearTimeout(t); resolve(); }
    });
    try { this.ws.close(1000); } catch { /* ignore */ }
  }

  hardClose() {
    this.closing = true;
    try { this.ws && this.ws.close(1000); } catch { /* ignore */ }
  }
}

// Upload with progress (fetch has no upload progress).
export function uploadWithProgress(url, blob, filename, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null; try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error((data && data.detail) || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload failed: network error."));
    const form = new FormData();
    form.append("file", blob, filename);
    xhr.send(form);
  });
}
