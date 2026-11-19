"""Record the portfolio video (demo.mp4, 1080p60) and 4 screenshots (docs/screenshots/) from the local emulator.

    .venv/bin/python scripts/record_demo.py

Starts its own server on 127.0.0.1 with a throw-away DB/CSV in a temp folder (never data/),
drives the installed Google Chrome (channel="chrome") and records frames through the Chrome
DevTools screencast + system ffmpeg, so no Playwright browser/ffmpeg download is needed.
"""
from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
W, H = 1920, 1080
SHOTS = ROOT / "docs" / "screenshots"
THEME = "light"  # Demo Showcase kit: light or dark



def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="igdm_"))
    port = free_port()
    env = {**os.environ, "DB_PATH": str(tmp / "demo.db"), "LEADS_CSV": str(tmp / "leads.csv"), "DEMO_MODE": "1",
           "IG_ACCESS_TOKEN": "", "IG_APP_SECRET": ""}
    server = subprocess.Popen([str(ROOT / ".venv" / "bin" / "uvicorn"), "app.main:app", "--host", "127.0.0.1",
                               "--port", str(port), "--log-level", "warning"], cwd=ROOT, env=env)
    url = f"http://127.0.0.1:{port}/demo/"
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    frames: list[tuple[float, Path]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True,
                                        args=["--disable-gpu-vsync", "--disable-frame-rate-limit"])
            ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
            page = ctx.new_page()
            cdp = ctx.new_cdp_session(page)

            def on_frame(ev):
                f = tmp / f"f{len(frames):05d}.jpg"
                f.write_bytes(base64.b64decode(ev["data"]))
                frames.append((ev["metadata"]["timestamp"], f))
                try:
                    cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
                except Exception:
                    pass

            cdp.on("Page.screencastFrame", on_frame)
            page.goto(url + "?theme=" + THEME)
            page.wait_for_function("window.demoReady === true")
            page.wait_for_timeout(400)
            cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 95, "maxWidth": W, "maxHeight": H,
                                              "everyNthFrame": 1})
            page.wait_for_timeout(900)
            page.set_default_timeout(10000)
            SHOTS.mkdir(parents=True, exist_ok=True)

            def shot(name):
                page.screenshot(path=str(SHOTS / name))

            def step(n):
                page.evaluate(f"window.demo.setStep({n})")

            def idle(extra=0):
                page.wait_for_timeout(150)
                page.evaluate("window.demo.idle()")
                if extra:
                    page.wait_for_timeout(extra)

            def click(sel_or_loc, pause=250):
                """No cursor in the video: soft tap ripple on the element, then a real DOM click."""
                loc = page.locator(sel_or_loc) if isinstance(sel_or_loc, str) else sel_or_loc
                loc.evaluate("el => window.demo.tap(el)")
                page.wait_for_timeout(200)
                loc.evaluate("el => el.click()")
                page.wait_for_timeout(pause)

            def type_send(input_sel, button_sel, text, extra=900):
                page.locator(input_sel).evaluate("el => el.focus()")
                page.wait_for_timeout(120)
                page.locator(input_sel).type(text, delay=40)
                page.wait_for_timeout(200)
                click(button_sel, 80)
                idle(extra)

            def say(text, extra=900):
                type_send("#cmsg", "#csend", text, extra)

            def qr(label, extra=900):
                click(page.locator("#cqr .ds-quick__pill", has_text=label).first, 80)
                idle(extra)

            # 1. comment PRICE -> private reply DM + public reply
            step(1)
            type_send("#cin", "#cpost", "PRICE", 1500)
            shot("screenshot-1-comment-dm.png")
            click(".ig-tab[data-v=dm]", 1000)

            # 2. FAQ in English
            step(2)
            say("What sizes do you have?", 1200)
            qr("Delivery", 1300)

            # 3. other languages
            step(3)
            say("А есть в XL?", 1300)
            say("Wo ist euer Laden?", 1500)

            # 4. order request -> CSV
            step(4)
            say("Ok, I want to reserve one", 600)
            say("Anna", 500)
            say("Wool overshirt, M", 500)
            say("+49 151 2345678", 1800)
            shot("screenshot-2-faq-lead.png")

            # 5. upset customer -> human
            step(5)
            say("My last parcel still hasn't arrived, ridiculous", 2000)

            # 6. owner replies from the inbox, then hands back to the bot
            step(6)
            type_send("#omsg", "#osend", "Hi Anna, Lena here. So sorry! Your parcel arrives tomorrow.", 1300)
            say("Thank you!", 700)
            click("#resume", 100)
            idle(1600)
            shot("screenshot-3-handover.png")
            page.wait_for_timeout(1000)
            page.evaluate("window.demo.endCard()")
            page.wait_for_timeout(4000)
            shot("04-end-card.png")
            end_ts = time.time()
            cdp.send("Page.stopScreencast")
            page.wait_for_timeout(300)
            ctx.close()
            browser.close()
    finally:
        server.terminate()

    # screencast frames arrive only when the page changes, at irregular times -> resample them
    # ourselves onto an exact 60 fps grid (latest frame at each tick) and pipe to ffmpeg
    out = ROOT / "demo.mp4"
    fps = 60
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps),
                             "-c:v", "mjpeg", "-i", "-", "-vf", f"scale={W}:{H}:flags=lanczos,format=yuv420p",
                             "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-r", str(fps),
                             "-movflags", "+faststart", "-an", str(out)], stdin=subprocess.PIPE)
    t0, j = frames[0][0], 0
    for k in range(int((end_ts - t0) * fps) + 1):
        t = t0 + k / fps
        while j + 1 < len(frames) and frames[j + 1][0] <= t + 1e-4:
            j += 1
        gap = frames[j + 1][0] - frames[j][0] if j + 1 < len(frames) else 0
        if 1.5 / fps < gap < 4.5 / fps and t - frames[j][0] >= 0.5 / fps:
            # Chrome dropped frames in the middle of an animation: fill the hole with a weighted
            # blend of the neighbours instead of holding the old frame (no visible hitch)
            w = (t - frames[j][0]) / gap
            proc.stdin.write(subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(frames[j][1]), "-i", str(frames[j + 1][1]), "-filter_complex",
                 f"blend=all_expr='A*{1 - w:.3f}+B*{w:.3f}'", "-q:v", "1", "-f", "mjpeg", "-"], capture_output=True, check=True).stdout)
            continue
        proc.stdin.write(frames[j][1].read_bytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    span = end_ts - t0
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"saved {out} (~{span:.1f} s, {len(frames)} frames)")


if __name__ == "__main__":
    sys.exit(main())
