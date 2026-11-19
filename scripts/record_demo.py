"""Record the portfolio video (demo.mp4) and 3 screenshots from the local emulator.

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
W, H = 1440, 900

CURSOR_JS = """
(() => {
  const c = document.createElement('div');
  c.id = '__cursor';
  Object.assign(c.style, {position:'fixed', left:'-40px', top:'-40px', width:'22px', height:'22px', borderRadius:'50%',
    background:'rgba(255,255,255,.85)', border:'2px solid rgba(0,0,0,.55)', zIndex: 99999, pointerEvents:'none',
    transform:'translate(-50%,-50%)', transition:'width .12s, height .12s, background .12s',
    boxShadow:'0 2px 8px rgba(0,0,0,.35)'});
  const add = () => document.body && !c.isConnected && document.body.appendChild(c);
  document.addEventListener('DOMContentLoaded', add); add();
  window.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; });
  window.addEventListener('mousedown', () => { c.style.width = c.style.height = '16px'; c.style.background = 'rgba(91,108,255,.9)'; });
  window.addEventListener('mouseup', () => { c.style.width = c.style.height = '22px'; c.style.background = 'rgba(255,255,255,.85)'; });
})();
"""


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
            browser = p.chromium.launch(channel="chrome", headless=True)
            ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1)
            ctx.add_init_script(CURSOR_JS)
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
            page.goto(url)
            page.wait_for_function("window.demoReady === true")
            page.wait_for_timeout(400)
            cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": W, "maxHeight": H,
                                              "everyNthFrame": 1})
            page.mouse.move(W / 2, H - 60)
            page.wait_for_timeout(900)
            page.set_default_timeout(10000)

            def shot(name):
                page.evaluate("document.getElementById('__cursor').style.display='none'")
                page.screenshot(path=str(ROOT / name))
                page.evaluate("document.getElementById('__cursor').style.display=''")

            def step(n):
                page.evaluate(f"window.demo.setStep({n})")

            def idle(extra=0):
                page.wait_for_timeout(150)
                page.evaluate("window.demo.idle()")
                if extra:
                    page.wait_for_timeout(extra)

            def click(sel_or_loc, pause=250):
                loc = page.locator(sel_or_loc) if isinstance(sel_or_loc, str) else sel_or_loc
                box = loc.bounding_box()
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=16)
                page.wait_for_timeout(110)
                page.mouse.down()
                page.wait_for_timeout(70)
                page.mouse.up()
                page.wait_for_timeout(pause)

            def type_send(input_sel, button_sel, text, extra=900):
                click(input_sel, 120)
                page.locator(input_sel).type(text, delay=27)
                page.wait_for_timeout(200)
                click(button_sel, 80)
                idle(extra)

            def say(text, extra=900):
                type_send("#cmsg", "#csend", text, extra)

            def qr(label, extra=900):
                click(page.locator("#cqr .qr", has_text=label).first, 80)
                idle(extra)

            # 1. comment PRICE -> private reply DM + public reply
            step(1)
            type_send("#cin", "#cpost", "PRICE", 1500)
            shot("screenshot-1-comment-dm.png")
            click(".tab[data-v=dm]", 1000)

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
            page.mouse.move(W - 30, H - 30, steps=10)
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
            page.mouse.move(W - 30, H - 30, steps=10)
            shot("screenshot-3-handover.png")
            page.wait_for_timeout(1800)
            cdp.send("Page.stopScreencast")
            page.wait_for_timeout(300)
            ctx.close()
            browser.close()
    finally:
        server.terminate()

    # screencast frames arrive only when the page changes -> concat with real durations, resample to 30 fps
    lst = tmp / "frames.txt"
    with lst.open("w") as f:
        for i, (ts, path) in enumerate(frames):
            dur = (frames[i + 1][0] - ts) if i + 1 < len(frames) else 1.5
            f.write(f"file '{path}'\nduration {max(dur, 0.001):.4f}\n")
        f.write(f"file '{frames[-1][1]}'\n")
    out = ROOT / "demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", f"fps=30,scale={W}:{H}:flags=lanczos,format=yuv420p", "-c:v", "libx264",
                    "-preset", "slow", "-crf", "18", "-movflags", "+faststart", "-an", str(out)], check=True)
    span = frames[-1][0] - frames[0][0] + 1.5
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"saved {out} (~{span:.1f} s, {len(frames)} frames)")


if __name__ == "__main__":
    sys.exit(main())
