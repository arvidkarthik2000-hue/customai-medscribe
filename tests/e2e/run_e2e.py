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
import math
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
                expect(page.locator("text=Today's schedule")).to_be_visible(timeout=10000)
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
