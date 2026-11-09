"""End-to-end browser walkthrough with a fake microphone and a fake AssemblyAI.

    python3 tests/e2e/run_e2e.py [--shots DIR] [--headed]

Starts the fake AssemblyAI (port 9900) and the app (port 8099) with a temporary
data directory, then drives Chromium through: sign in, schedule, chart, sample
replay -> review -> confirm -> sign, live recording with the fake mic, pharmacy
check, console and sponsor report. Exits non-zero on the first failed step.
Needs the system Playwright (python3 -m playwright) and the repo venv for uvicorn.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
VENV_PY = ROOT / ".venv" / "bin" / "python"
APP = "http://127.0.0.1:8099"
CODE = "e2e-code"
# Testers must never see a third-party AI vendor or model name: scan every response and frame.
BANNED = re.compile(r"assembly\s?ai|anthropic|claude|sonnet|haiku|universal-3|medical-v1|medical mode|gemini|openai|"
                    r"whisper|deepgram|elevenlabs|parakeet|qwen|vibevoice|pyannote", re.I)
TEXT_TYPES = ("json", "javascript", "html", "css", "text/")
SEEN_BANNED: list[str] = []


def watch_branding(page) -> None:
    def on_response(resp):
        if not resp.url.startswith(APP):
            return
        ctype = resp.headers.get("content-type", "")
        if not any(k in ctype for k in TEXT_TYPES):
            return
        try:
            body = resp.text()
        except Exception:  # redirects and aborted loads have no body
            return
        m = BANNED.search(body)
        if m:
            SEEN_BANNED.append(f"{resp.url}: ...{body[max(0, m.start() - 60):m.end() + 60]}...")
    page.on("response", on_response)

    def on_ws(ws):
        def frame(payload):
            s = payload if isinstance(payload, str) else ""
            m = BANNED.search(s)
            if m:
                SEEN_BANNED.append(f"websocket {ws.url}: {s[:160]}")
        ws.on("framereceived", frame)
    page.on("websocket", on_ws)


def no_cookie_route(route, frameable: bool = False):
    """Fetch the request outside the browser and hand back the answer without Set-Cookie:
    a browser that never keeps cookies (some wallboard and kiosk players). With frameable,
    also drop CSP frame-ancestors: Constant Media's Android player shows the demo in its
    iframe overlay without enforcing it, and Chromium here would otherwise refuse the frame."""
    import urllib.error
    import urllib.request as ur

    class NoRedirect(ur.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    req = route.request
    headers = {k: v for k, v in req.headers.items() if k.lower() not in ("cookie", "accept-encoding", "host")}
    headers["Accept-Encoding"] = "identity"
    r = ur.Request(req.url, data=req.post_data_buffer, headers=headers, method=req.method)
    opener = ur.build_opener(NoRedirect)
    try:
        resp = opener.open(r, timeout=60)
        status, hdrs, body = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        status, hdrs, body = e.code, e.headers, e.read()
    out = {k: v for k, v in hdrs.items() if k.lower() not in ("set-cookie", "content-length", "transfer-encoding", "connection")}
    if frameable:
        for k in list(out):
            if k.lower() == "content-security-policy":
                out[k] = "; ".join(d for d in out[k].split("; ") if not d.startswith("frame-ancestors"))
    route.fulfill(status=status, headers=out, body=body)


STORAGE_BLOCKED = """
(() => {
  const deny = { configurable: true, get() { throw new DOMException("storage blocked", "SecurityError"); } };
  try { Object.defineProperty(window, "sessionStorage", deny); Object.defineProperty(window, "localStorage", deny); } catch (e) {}
})();
"""


def wait_http(url: str, timeout: float = 20.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"timeout waiting for {url}")


def make_wav(path: Path, seconds: int = 20) -> None:
    fr = 48000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(fr)
        buf = bytearray()
        for i in range(fr * seconds):
            t = i / fr
            v = 0.2 * (0.5 + 0.5 * math.sin(2 * math.pi * 0.7 * t)) * math.sin(2 * math.pi * 220 * t) + 0.02 * (random.random() - 0.5)
            buf += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
        w.writeframes(bytes(buf))


class Step:
    def __init__(self, shots: Path):
        self.shots = shots
        self.n = 0

    def shot(self, page, name):
        self.n += 1
        page.screenshot(path=str(self.shots / f"{self.n:02d}-{name}.png"), full_page=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(Path(tempfile.gettempdir()) / "medscribe-shots"))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep servers running at the end")
    args = ap.parse_args()
    shots = Path(args.shots); shutil.rmtree(shots, ignore_errors=True); shots.mkdir(parents=True)
    data_dir = Path(tempfile.mkdtemp(prefix="medscribe-e2e-"))
    wav = data_dir / "fake_mic.wav"; make_wav(wav)

    env = dict(os.environ, DATA_DIR=str(data_dir), DEMO_ACCESS_CODE=CODE, ASSEMBLYAI_API_KEY="fake-key",
               ASSEMBLYAI_TEST_BASE="http://127.0.0.1:9900", APP_SECRET="e2e-secret", PYTHONUNBUFFERED="1")
    env.pop("ANTHROPIC_API_KEY", None)
    logs = open(data_dir / "servers.log", "w")
    fake = subprocess.Popen([str(VENV_PY), "-m", "uvicorn", "tests.e2e.fake_assemblyai:app", "--port", "9900"], cwd=ROOT, env=env, stdout=logs, stderr=subprocess.STDOUT)
    app = subprocess.Popen([str(VENV_PY), "-m", "uvicorn", "app.main:app", "--port", "8099"], cwd=ROOT, env=env, stdout=logs, stderr=subprocess.STDOUT)
    failures = []
    try:
        wait_http("http://127.0.0.1:9900/_state")
        wait_http(f"{APP}/api/health")
        step = Step(shots)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required"])
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, permissions=["microphone"])
            page = ctx.new_page()
            watch_branding(page)
            console_errors = []
            page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}") if m.type in ("error",) else None)
            page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))

            def check(name, fn):
                try:
                    fn()
                    print(f"PASS {name}")
                except Exception as e:  # noqa: BLE001
                    print(f"FAIL {name}: {e}")
                    failures.append(name)
                    try: step.shot(page, f"FAIL-{name.replace(' ', '_')}")
                    except Exception: pass

            # 1. sign in
            def sign_in():
                page.goto(APP + "/")
                page.fill("#code", CODE)
                page.click("button[type=submit]")
                expect(page.locator("#views")).to_be_visible()
                step.shot(page, "views")
                page.click("a[data-role=doctor]")
                page.wait_for_url("**/doctor/**", timeout=10000)  # the tile itself says "Today's schedule"
                expect(page.locator("table.schedule")).to_be_visible(timeout=10000)
                page.wait_for_timeout(1200)
                step.shot(page, "home")
            check("sign in and schedule", sign_in)

            # 2. sample visit end to end
            def sample_flow():
                page.locator(".sample").first.click()
                expect(page.locator("text=Visit setup")).to_be_visible(timeout=10000)
                assert page.locator(".ad-unit").count() == 0, "ad visible on chart"
                step.shot(page, "chart")
                page.locator("button:has-text('▶')").first.click()
                expect(page.locator("text=Sample visit")).to_be_visible(timeout=10000)
                page.wait_for_timeout(2500)
                step.shot(page, "replay")
                page.click("button:has-text('Skip to end')")
                page.wait_for_url("**/processing", timeout=15000)
                page.wait_for_timeout(600)
                step.shot(page, "processing")
                page.wait_for_url("**/review", timeout=90000)
                expect(page.locator(".note-section").first).to_be_visible(timeout=15000)
                assert page.locator(".ad-unit").count() == 0, "ad visible on review"
                page.wait_for_timeout(500)
                step.shot(page, "review")
                chip = page.locator(".ev-chip").first
                if chip.count():
                    chip.click(); page.wait_for_timeout(500)
                    assert page.locator(".turn.hl").count() >= 1, "evidence highlight missing"
                    step.shot(page, "evidence")
                page.locator(".tabs button", has_text="Safety").click()
                page.wait_for_timeout(300)
                step.shot(page, "safety")
            check("sample visit to review", sample_flow)

            def confirm_and_sign():
                # pick the first candidate on any sound-alike row
                for _ in range(3):
                    amb = page.locator(".meds .banner-warning button")
                    if amb.count() == 0: break
                    amb.first.click(); page.wait_for_timeout(800)
                for _ in range(12):
                    boxes = page.locator(".meds input[type=checkbox]:not(:checked)")
                    if boxes.count() == 0: break
                    boxes.first.check(); page.wait_for_timeout(600)
                page.locator(".tabs button", has_text="Safety").click()
                for _ in range(12):
                    sel = page.locator(".cds select")
                    if sel.count() == 0: break
                    sel.first.select_option(index=1)
                    page.locator(".cds button:has-text('Acknowledge')").first.click()
                    page.wait_for_timeout(700)
                # resolve flagged lines by editing them
                for _ in range(10):
                    flagged = page.locator(".note-item.flagged .note-text")
                    if flagged.count() == 0: break
                    flagged.first.click()
                    page.locator(".note-item.editing button:has-text('Remove line')").click()
                    page.wait_for_timeout(700)
                step.shot(page, "ready-to-sign")
                page.click("button:has-text('Sign note')")
                page.wait_for_url("**/signed", timeout=15000)
                page.wait_for_timeout(1200)
                step.shot(page, "signed")
            check("confirm medicines and sign", confirm_and_sign)

            # 3. live recording with the fake mic and fake AssemblyAI
            def live_flow():
                page.goto(APP + "/doctor/#/")
                expect(page.locator("text=Today's schedule")).to_be_visible(timeout=10000)
                page.locator("table.schedule tbody tr", has=page.locator("span.badge", has_text="Scheduled")).first.click()
                expect(page.locator("text=Visit setup")).to_be_visible(timeout=10000)
                page.click("button:has-text('Start recording')")
                for cb in page.locator(".modal input[type=checkbox]").all(): cb.check()
                page.click(".modal button:has-text('start')")
                expect(page.locator("text=Live transcript").first).to_be_visible(timeout=10000)
                page.wait_for_timeout(9000)
                step.shot(page, "recording")
                assert page.locator(".turn").count() >= 2, "no live turns arrived"
                page.click("button:has-text('End visit')")
                page.wait_for_url("**/processing", timeout=30000)
                page.wait_for_timeout(3000)
                step.shot(page, "live-processing")
            check("live recording", live_flow)

            # 4. pharmacy tools
            def pharmacy():
                page.goto(APP + "/pharmacy/")
                page.wait_for_timeout(1500)
                step.shot(page, "pharmacy")
            check("pharmacy page loads", pharmacy)

            # 5. console and report
            def console():
                page.goto(APP + "/console/")
                page.wait_for_timeout(2500)
                step.shot(page, "console")
                page.goto(APP + "/report/")
                page.wait_for_timeout(2000)
                step.shot(page, "report")
            check("console and report load", console)

            # 6. screen check page (what a wallboard team runs first)
            def screen_check():
                page.goto(APP + "/check/")
                page.wait_for_timeout(2500)
                badges = page.locator("#results .badge").all_inner_texts()
                assert "Testing" not in badges, badges
                detail = page.locator("#results").inner_text()
                assert "Problem" not in badges, detail
                assert "This browser keeps the sign-in cookie" in detail, detail
                page.click("#mic-test")
                page.wait_for_timeout(6500)
                assert "Works: heard sound" in page.locator("#results").inner_text(), page.locator("#results").inner_text()
                page.click("#rerun")
                page.wait_for_url("**/check/?hop=*", timeout=10000)
                page.wait_for_timeout(2500)
                hop = page.locator("tr", has_text="Opening another page").inner_text()
                assert "OK" in hop, hop
                step.shot(page, "screen-check")
                assert page.locator("#boot-fail").count() == 0
            check("screen check", screen_check)

            # 7. a wallboard browser that keeps no cookies and no page storage, used by touch
            def wallboard_without_cookies():
                c2 = browser.new_context(viewport={"width": 1080, "height": 1920}, has_touch=True, permissions=["microphone"])
                c2.add_init_script(STORAGE_BLOCKED)
                c2.route(re.compile(r"^http://127\.0\.0\.1:8099/.*"), no_cookie_route)
                p2 = c2.new_page()
                watch_branding(p2)
                ad_calls = []
                p2.on("response", lambda r: ad_calls.append(r.status) if "/adzone/api/serve" in r.url else None)
                p2.goto(APP + "/")
                p2.fill("#code", CODE)
                p2.tap("button[type=submit]")
                expect(p2.locator("#views")).to_be_visible()
                p2.tap("a[data-role=doctor]")
                p2.wait_for_url("**/doctor/**", timeout=10000)
                expect(p2.locator("table.schedule")).to_be_visible(timeout=10000)
                p2.wait_for_timeout(300)
                assert "st=" not in p2.url and p2.url.endswith("/doctor/"), p2.url
                step.shot(p2, "wallboard-doctor")
                p2.locator(".sample").first.tap()
                expect(p2.locator("text=Visit setup")).to_be_visible(timeout=10000)
                p2.locator("button:has-text('▶')").first.tap()
                expect(p2.locator("text=Sample visit")).to_be_visible(timeout=10000)
                p2.tap("button:has-text('Skip to end')")
                p2.wait_for_url("**/review", timeout=90000)
                expect(p2.locator(".note-section").first).to_be_visible(timeout=15000)
                step.shot(p2, "wallboard-review")
                # header switch to another view
                p2.locator("nav.role-switch a", has_text="Pharmacy tools").tap()
                p2.wait_for_url("**/pharmacy/**", timeout=10000)
                p2.wait_for_timeout(1500)
                assert p2.locator("text=Interaction").count() >= 1, "pharmacy did not load"
                p2.locator("nav.role-switch a", has_text="Network console").tap()
                p2.wait_for_url("**/console/**", timeout=10000)
                p2.wait_for_timeout(2000)
                assert p2.locator(".app-header").count() == 1
                step.shot(p2, "wallboard-console")
                p2.goto(APP + "/check/")
                p2.wait_for_timeout(2500)
                txt = p2.locator("#results").inner_text()
                assert "keeps neither cookies nor page storage" in txt, txt
                assert c2.cookies() == [], c2.cookies()
                assert all(s == 200 for s in ad_calls), ad_calls
                c2.close()
            check("wallboard without cookies or storage", wallboard_without_cookies)

            # 8. nothing covers the four tiles at wallboard, TV, tablet and phone sizes
            def tiles_clickable_everywhere():
                for w, h in ((1920, 1080), (1080, 1920), (3840, 2160), (1280, 800), (800, 1280), (390, 844)):
                    c3 = browser.new_context(viewport={"width": w, "height": h}, has_touch=True)
                    p3 = c3.new_page()
                    p3.goto(APP + "/")
                    p3.fill("#code", CODE)
                    p3.click("button[type=submit]")
                    expect(p3.locator("#views")).to_be_visible()
                    blocked = p3.evaluate("""() => [...document.querySelectorAll('.view-tile')].map((t) => {
                        t.scrollIntoView({block: 'center'});
                        const r = t.getBoundingClientRect();
                        const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                        return at && t.contains(at) ? null : (t.dataset.role + ' covered by ' + (at ? at.outerHTML.slice(0, 80) : 'nothing'));
                    }).filter(Boolean)""")
                    assert not blocked, f"{w}x{h}: {blocked}"
                    p3.locator("a[data-role=pharmacy]").tap()
                    p3.wait_for_url("**/pharmacy/**", timeout=10000)
                    c3.close()
            check("tiles reachable at every screen size", tiles_clickable_everywhere)

            # 9. a browser that cannot run the page's scripts gets a message, not a dead page
            def watchdog():
                c4 = browser.new_context()
                c4.route("**/js/common.js", lambda route: route.fulfill(status=200, content_type="text/javascript", body="export const broken = ;"))
                p4 = c4.new_page()
                p4.goto(APP + "/")
                expect(p4.locator("#boot-fail")).to_be_visible(timeout=12000)
                assert "screen check" in p4.locator("#boot-fail").inner_text().lower()
                step.shot(p4, "watchdog")
                c4.close()
            check("start-up watchdog", watchdog)

            # 10. inside a partner's iframe overlay, as Constant Media's wallboard player opens it:
            #     another origin frames the app, the frame keeps no cookies, and the player passes
            #     the microphone through only when its iframe says allow="microphone"
            def partner_iframe_overlay():
                overlay = "http://localhost:8099/__overlay"  # localhost is another origin than 127.0.0.1

                def overlay_page(route):
                    u = route.request.url
                    allow = "microphone" if "mic=1" in u else ""
                    path = u.split("path=", 1)[1].split("&")[0] if "path=" in u else "/"
                    route.fulfill(status=200, content_type="text/html", body=(
                        '<!doctype html><html><body style="margin:0;background:#111">'
                        '<button style="position:fixed;top:8px;right:8px;z-index:9">X</button>'
                        f'<iframe id="ov" src="{APP}{path}" allow="{allow}" style="border:0;width:1080px;height:1900px"></iframe>'
                        '</body></html>'))

                c5 = browser.new_context(viewport={"width": 1080, "height": 1920}, has_touch=True, permissions=["microphone"])
                c5.route(re.compile(r"^http://localhost:8099/__overlay.*"), overlay_page)
                c5.route(re.compile(r"^http://127\.0\.0\.1:8099/.*"), lambda r: no_cookie_route(r, frameable=True))
                p5 = c5.new_page()
                watch_branding(p5)
                p5.goto(overlay + "?mic=1")
                f = p5.frame_locator("#ov")
                f.locator("#code").fill(CODE)
                f.locator("button[type=submit]").tap()
                expect(f.locator("#views")).to_be_visible()
                f.locator("a[data-role=doctor]").tap()
                expect(f.locator("table.schedule")).to_be_visible(timeout=15000)
                # a sponsored card's link opens inside the frame with a Back button; the player page stays
                ad = f.locator(".ms-ad-link").first
                expect(ad).to_be_visible(timeout=10000)
                ad.tap()
                expect(f.locator(".frame-back")).to_be_visible(timeout=10000)
                assert p5.url.startswith(overlay), p5.url
                step.shot(p5, "overlay-ad-link")
                f.locator(".frame-back").tap()
                expect(f.locator("table.schedule")).to_be_visible(timeout=15000)
                # a sample visit through to the note, inside the frame
                f.locator(".sample").first.tap()
                expect(f.locator("text=Visit setup")).to_be_visible(timeout=10000)
                f.locator("button:has-text('\u25b6')").first.tap()
                expect(f.locator("text=Sample visit")).to_be_visible(timeout=10000)
                f.locator("button:has-text('Skip to end')").tap()
                expect(f.locator(".note-section").first).to_be_visible(timeout=90000)
                step.shot(p5, "overlay-review")
                assert c5.cookies() == [], c5.cookies()
                # the screen check inside the overlay, microphone passed through
                p5.goto(overlay + "?mic=1&path=/check/")
                p5.wait_for_timeout(2500)
                res = f.locator("#results")
                txt = res.inner_text()
                # Pages the test hands over through route.fulfill count as public pages to Chromium,
                # whose local-network checks then block their WebSocket to 127.0.0.1. On the real
                # site page and server are both public; live captions are covered by steps 4 and 6.
                problems = [r for r in f.locator("#results tr").all_inner_texts() if "Problem" in r and not r.startswith("Live connection")]
                assert "Shown inside another app" in txt and not problems, txt
                f.locator("#mic-test").tap()
                p5.wait_for_timeout(6500)
                assert "Works: heard sound" in res.inner_text(), res.inner_text()
                step.shot(p5, "overlay-check-mic")
                # and a player whose iframe does not pass the microphone through
                p5.goto(overlay + "?path=/check/")
                p5.wait_for_timeout(2500)
                txt = f.locator("#results").inner_text()
                assert "blocks the microphone" in txt and 'allow="microphone"' in txt, txt
                f.locator("#mic-test").tap()
                p5.wait_for_timeout(2500)
                assert "blocks the microphone" in f.locator("#results").inner_text()
                step.shot(p5, "overlay-check-no-mic")
                c5.close()
            check("inside a partner's iframe overlay", partner_iframe_overlay)

            # 11. no vendor or model name reached the browser
            def branding_scan():
                assert not SEEN_BANNED, "\n".join(SEEN_BANNED[:10])
            check("no vendor names in anything the browser received", branding_scan)

            if console_errors:
                print("BROWSER ERRORS:\n  " + "\n  ".join(console_errors[:30]))
            browser.close()
        st = urllib.request.urlopen("http://127.0.0.1:9900/_state").read().decode()
        print("fake AssemblyAI state:", st)
    finally:
        if not args.keep:
            app.terminate(); fake.terminate()
        logs.close()
        print("servers log:", data_dir / "servers.log")
        print("screenshots:", shots)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
