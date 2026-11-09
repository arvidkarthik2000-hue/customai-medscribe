/* Screen check. ES5 and XMLHttpRequest on purpose: this page has to run, and say what is
   missing, on the old embedded browsers that wallboards and kiosk players sometimes use. */
(function () {
  "use strict";

  var rows = {};
  var order = [];
  var tbody = document.querySelector("#results tbody");
  var host = location.host;
  var LABEL = { ok: "OK", warn: "Check", fail: "Problem", info: "Info", pending: "Testing" };
  var BADGE = { ok: "badge-ok", warn: "badge-warning", fail: "badge-critical", info: "badge-info", pending: "" };
  var NO_MIC_WAY = "Without a microphone, use a sample visit: it runs the whole scribe.";
  var FRAME_BLOCKS_MIC = "The frame this page is shown in blocks the microphone. The player's iframe needs allow=\"microphone\". " + NO_MIC_WAY;
  var framed = false;

  function isFramed() {
    try { return window.top !== window.self; } catch (e) { return true; }
  }

  // true or false when the browser can say whether this page may use the microphone (the
  // frame's allow= list decides that for a page shown in an iframe), null when it cannot
  function micPolicy() {
    var fp = document.permissionsPolicy || document.featurePolicy;
    try { return fp && fp.allowsFeature ? !!fp.allowsFeature("microphone") : null; } catch (e) { return null; }
  }

  function text(node, s) { node.appendChild(document.createTextNode(s)); return node; }

  function row(id, name) {
    if (rows[id]) return rows[id];
    var tr = document.createElement("tr");
    var a = text(document.createElement("td"), name); a.className = "check-name";
    var b = document.createElement("td"); b.className = "check-state";
    var c = document.createElement("td"); c.className = "check-detail";
    tr.appendChild(a); tr.appendChild(b); tr.appendChild(c);
    tbody.appendChild(tr);
    rows[id] = { tr: tr, state: b, detail: c, name: name, value: "pending", note: "" };
    order.push(id);
    set(id, "pending", "");
    return rows[id];
  }

  function set(id, state, note) {
    var r = rows[id];
    r.value = state; r.note = note || "";
    while (r.state.firstChild) r.state.removeChild(r.state.firstChild);
    var badge = text(document.createElement("span"), LABEL[state]);
    badge.className = "badge " + BADGE[state];
    r.state.appendChild(badge);
    while (r.detail.firstChild) r.detail.removeChild(r.detail.firstChild);
    text(r.detail, r.note);
    summarise();
  }

  function summarise() {
    var fails = 0, warns = 0, pending = 0, lines = [];
    for (var i = 0; i < order.length; i++) {
      var r = rows[order[i]];
      if (r.value === "fail") fails++;
      if (r.value === "warn") warns++;
      if (r.value === "pending") pending++;
      lines.push(LABEL[r.value].toUpperCase() + "  " + r.name + ": " + r.note);
    }
    var s = document.getElementById("summary-line");
    while (s.firstChild) s.removeChild(s.firstChild);
    text(s, pending ? "Testing..." : fails ? fails + " problem(s) found. Send a photo of this page to your CustomAi contact."
      : warns ? "This screen can run the demo, with the notes marked Check." : "All checks passed: this screen can run the demo.");
    var raw = document.getElementById("raw");
    while (raw.firstChild) raw.removeChild(raw.firstChild);
    text(raw, lines.join("\n") + "\n\nBrowser: " + navigator.userAgent + "\nChecked: " + new Date().toString());
  }

  function xhr(method, url, body, done) {
    var req;
    try { req = new XMLHttpRequest(); } catch (e) { done(0, null, 0); return; }
    var t0 = Date.now();
    req.open(method, url, true);
    req.timeout = 15000;
    req.onreadystatechange = function () {
      if (req.readyState !== 4) return;
      var data = null;
      try { data = JSON.parse(req.responseText); } catch (e) { data = null; }
      done(req.status, data, Date.now() - t0, req);
    };
    if (body !== null) req.setRequestHeader("Content-Type", "application/json");
    try { req.send(body); } catch (e) { done(0, null, 0); }
  }

  function browserName() {
    var ua = navigator.userAgent, m;
    if ((m = /Edg\/(\d+)/.exec(ua))) return "Edge " + m[1];
    if ((m = /SamsungBrowser\/(\d+)/.exec(ua))) return "Samsung Internet " + m[1];
    if ((m = /Firefox\/(\d+)/.exec(ua))) return "Firefox " + m[1];
    if ((m = /Chrome\/(\d+)/.exec(ua))) return /; wv\)/.test(ua) ? "Android WebView (Chrome " + m[1] + ")" : "Chrome " + m[1];
    if ((m = /Version\/(\d+)[\d.]*.*Safari/.exec(ua))) return "Safari " + m[1];
    return "Unknown browser";
  }

  function run() {
    tbody.innerHTML = "";
    rows = {}; order = [];

    // 1. browser and script support
    row("browser", "Browser");
    var w = window.innerWidth || document.documentElement.clientWidth || 0;
    var h = window.innerHeight || document.documentElement.clientHeight || 0;
    set("browser", "info", browserName() +
      (w && h ? " · " + w + "×" + h + " px page" : "") +
      (window.screen && screen.width ? " · " + screen.width + "×" + screen.height + " screen" : "") +
      (window.devicePixelRatio ? " at " + window.devicePixelRatio + "x" : "") +
      ((navigator.maxTouchPoints || "ontouchstart" in window) ? " · touch screen" : ""));
    row("scripts", "Up-to-date browser");
    var modules = "noModule" in document.createElement("script");
    window.setTimeout(function () {
      if (window.__msModern === true) set("scripts", "ok", "Supports everything the demo pages use.");
      else set("scripts", "fail", (modules ? "This browser is too old for the demo pages. " : "This browser does not support modern web pages. ") +
        "Update the browser or player software (Chrome 90 or later, or a current Edge, Safari or Firefox).");
    }, 1500);

    // 2. inside another app's frame? Wallboard players open the demo in an iframe overlay,
    // which works; only the microphone needs the frame to pass it through.
    framed = isFramed();
    if (framed) {
      row("frame", "Shown inside another app (frame)");
      set("frame", "ok", "The demo works inside a frame." +
        (micPolicy() === false ? " This frame does not pass the microphone through: see Microphone below." : ""));
    }

    // 3. server, POST, cookies
    row("server", "Connection to the demo server");
    row("post", "Sending data to the server");
    row("cookies", "Sign-in kept between pages");
    xhr("GET", "/api/health", null, function (status, data, ms, req) {
      if (status === 200 && data && data.ok) {
        set("server", ms > 3000 ? "warn" : "ok", "Reached " + host + " in " + ms + " ms." + (ms > 3000 ? " Slow network: pages may take a while to open." : ""));
        var date = req && req.getResponseHeader("Date");
        var skew = date ? Math.round((Date.now() - new Date(date).getTime()) / 60000) : 0;
        if (Math.abs(skew) > 60 * 24) {
          row("clock", "Screen clock");
          set("clock", "warn", "This screen's clock is off by about " + Math.round(Math.abs(skew) / 60) + " hours. Set the date and time on the device.");
        }
      } else {
        set("server", "fail", status ? "The server answered " + status + "." : "Could not reach " + host + ". The network may block this address: allow https://" + host + " (all pages and WebSockets).");
      }
    });
    xhr("POST", "/api/device/echo", JSON.stringify({ probe: "screen-check" }), function (status, data) {
      if (status === 200 && data && data.ok) set("post", "ok", "Works.");
      else set("post", "fail", status ? "The network or browser refused it (" + status + "). Buttons in the demo would do nothing." : "Blocked. Buttons in the demo would do nothing; the network may filter this address.");
    });
    var storage = false;
    try { window.sessionStorage.setItem("ms_check", "1"); storage = window.sessionStorage.getItem("ms_check") === "1"; window.sessionStorage.removeItem("ms_check"); } catch (e) { storage = false; }
    xhr("POST", "/api/device/cookie", "{}", function (status, data) {
      if (status !== 200 || !data || !data.value) { set("cookies", "fail", "Could not test (the server did not answer)."); return; }
      xhr("GET", "/api/device/cookie?value=" + encodeURIComponent(data.value), null, function (s2, d2) {
        var kept = s2 === 200 && d2 && d2.kept;
        if (kept) set("cookies", "ok", "This browser keeps the sign-in cookie.");
        else if (storage) set("cookies", "ok", "This browser does not keep cookies; the demo uses its cookie-free sign-in instead.");
        else set("cookies", "warn", "This browser keeps neither cookies nor page storage. The demo still works in this tab, but reloading a page asks for the access code again.");
      });
    });

    // 4. WebSocket (live captions)
    row("ws", "Live connection (for live captions)");
    var wsDone = false;
    function wsResult(state, note) { if (wsDone) return; wsDone = true; set("ws", state, note); }
    if (!window.WebSocket) {
      wsResult("fail", "Not supported by this browser. Sample visits work; live recording does not.");
    } else {
      try {
        var ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + host + "/api/device/ws");
        var t = window.setTimeout(function () {
          wsResult("fail", "Blocked or timed out. The network may block WebSockets to " + host + ". Sample visits work; live captions do not.");
          try { ws.close(); } catch (e) { /* ignore */ }
        }, 8000);
        ws.onopen = function () { ws.send("ping"); };
        ws.onmessage = function (e) {
          window.clearTimeout(t);
          wsResult(e.data === "pong" ? "ok" : "warn", e.data === "pong" ? "Works." : "Unexpected answer.");
          try { ws.close(); } catch (err) { /* ignore */ }
        };
        ws.onerror = function () { window.clearTimeout(t); wsResult("fail", "Blocked. The network may block WebSockets to " + host + ". Sample visits work; live captions do not."); };
      } catch (e) {
        wsResult("fail", "Could not open one (" + e.message + ").");
      }
    }

    // 5. microphone (recording a visit)
    row("mic", "Microphone (for recording a visit)");
    var secure = window.isSecureContext !== false && location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1";
    var gum = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    var worklet = typeof window.AudioWorkletNode !== "undefined";
    if (!secure) set("mic", "fail", "Recording needs the https:// address" + (framed ? ", and so does the page that frames it." : "."));
    else if (framed && micPolicy() === false) set("mic", "fail", FRAME_BLOCKS_MIC);
    else if (!gum) set("mic", "warn", "This browser offers no microphone. " + NO_MIC_WAY);
    else if (!worklet) set("mic", "warn", "This browser cannot process live audio. " + NO_MIC_WAY);
    else set("mic", "info", "Supported. Press Test the microphone to check that one is connected and allowed.");

    // 6. page switching (arrived from the test link?)
    if (/[?&]hop=/.test(location.search)) {
      row("hop", "Opening another page");
      set("hop", "ok", "Works.");
    } else {
      row("hop", "Opening another page");
      set("hop", "info", "Press Run again to test it.");
    }
  }

  function micTest() {
    if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) return;
    set("mic", "pending", "Asking for the microphone...");
    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      var AC = window.AudioContext || window.webkitAudioContext;
      var track = stream.getAudioTracks()[0];
      var label = (track && track.label) || "microphone";
      if (!AC) { stream.getTracks().forEach(function (tr) { tr.stop(); }); set("mic", "ok", "Allowed: " + label + "."); return; }
      var ctx = new AC();
      var src = ctx.createMediaStreamSource(stream);
      var an = ctx.createAnalyser(); an.fftSize = 1024;
      src.connect(an);
      var buf = new Float32Array(an.fftSize);
      var peak = 0, ticks = 0;
      var meter = document.createElement("span"); meter.className = "check-meter";
      var fill = document.createElement("span"); meter.appendChild(fill);
      set("mic", "pending", "Listening for 5 seconds: say something. ");
      rows.mic.detail.appendChild(meter);
      var timer = window.setInterval(function () {
        an.getFloatTimeDomainData(buf);
        var sum = 0;
        for (var i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        var rms = Math.sqrt(sum / buf.length);
        peak = Math.max(peak, rms);
        fill.style.width = Math.min(100, Math.round(rms * 400)) + "%";
        if (++ticks >= 50) {
          window.clearInterval(timer);
          stream.getTracks().forEach(function (tr) { tr.stop(); });
          try { ctx.close(); } catch (e) { /* ignore */ }
          if (peak > 0.01) set("mic", "ok", "Works: heard sound on " + label + ".");
          else set("mic", "warn", "Allowed (" + label + ") but heard nothing. Check that the microphone is on and not muted.");
        }
      }, 100);
    }, function (err) {
      var name = (err && err.name) || "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        if (framed && micPolicy() === false) set("mic", "fail", FRAME_BLOCKS_MIC);
        else if (framed) set("mic", "fail", "The player app refused the microphone. It has to allow the microphone for " + host +
          ": on Android, the app needs the microphone permission and must grant it to its web view. " + NO_MIC_WAY);
        else set("mic", "fail", "Microphone access was refused. Allow it for " + host + " in the browser settings. " + NO_MIC_WAY);
      }
      else if (name === "NotFoundError") set("mic", "fail", "No microphone is connected to this screen. " + NO_MIC_WAY);
      else set("mic", "fail", "Could not open the microphone (" + (name || err) + "). " + NO_MIC_WAY);
    });
  }

  document.getElementById("mic-test").onclick = micTest;
  document.getElementById("rerun").onclick = function () {
    // a real page change, which also tests that the player lets the demo open other pages
    location.href = "/check/?hop=" + Date.now();
    window.setTimeout(function () {
      row("hop", "Opening another page");
      set("hop", "fail", "This screen did not open the next page. If the player only allows one address, allow every page on " + host + ".");
    }, 6000);
  };
  run();
})();
