"""Record a cursor-choreographed demo video of the km UI for social posts.

Runs against the synthetic demo DB (never real data):
    .venv/bin/python tools/seed_demo.py
    KM_DB=data/demo.db .venv/bin/python -m km.cli ui --port 8890
    .venv/bin/python tools/record_demo.py

Produces exports/km-demo.mp4 (1280x720, h264). A fake cursor is injected
and animated between targets; zoom-ins are CSS scale transforms on the
app root with the focus point as transform-origin.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8890"
OUT_DIR = Path(__file__).resolve().parent.parent / "exports"
VIDEO_DIR = OUT_DIR / "demo-video-raw"

CURSOR_JS = """
() => {
  const c = document.createElement('div');
  c.id = '__km_cursor';
  c.innerHTML = `<svg width="26" height="26" viewBox="0 0 24 24">
    <path d="M5 3 L19 12 L12 13.5 L9.5 20 Z" fill="#fff" stroke="#000" stroke-width="1.4"/></svg>`;
  Object.assign(c.style, {position:'fixed', left:'640px', top:'400px', zIndex: 99999,
    pointerEvents:'none', transition:'left 0.65s cubic-bezier(.25,.6,.3,1), top 0.65s cubic-bezier(.25,.6,.3,1)',
    filter:'drop-shadow(0 2px 5px rgba(0,0,0,.55))'});
  document.body.appendChild(c);
  window.__moveCursor = (x, y, ms) => {
    c.style.transitionDuration = ms + 'ms, ' + ms + 'ms';
    c.style.left = x + 'px'; c.style.top = y + 'px';
  };
  window.__ripple = (x, y) => {
    const r = document.createElement('div');
    Object.assign(r.style, {position:'fixed', left:(x-4)+'px', top:(y-4)+'px', width:'8px',
      height:'8px', borderRadius:'50%', border:'2.5px solid #e3b04b', zIndex:99998,
      pointerEvents:'none', opacity:'0.95', transition:'all .5s ease-out'});
    document.body.appendChild(r);
    requestAnimationFrame(() => { r.style.transform='scale(5)'; r.style.opacity='0'; });
    setTimeout(() => r.remove(), 600);
  };
  const root = document.getElementById('root') || document.body.firstElementChild;
  root.style.transition = 'transform 0.9s cubic-bezier(.4,.05,.2,1)';
  window.__zoom = (scale, ox, oy) => {
    root.style.transformOrigin = ox + 'px ' + oy + 'px';
    root.style.transform = scale === 1 ? 'none' : 'scale(' + scale + ')';
  };
}
"""


class Director:
    def __init__(self, page):
        self.page = page
        page.evaluate(CURSOR_JS)

    def pause(self, seconds: float):
        time.sleep(seconds)

    def glide(self, locator, ms=700):
        box = locator.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.evaluate("([x,y,ms]) => window.__moveCursor(x,y,ms)", [x, y, ms])
        self.pause(ms / 1000 + 0.15)
        return x, y

    def click(self, locator, ms=700, settle=0.7):
        x, y = self.glide(locator, ms)
        self.page.evaluate("([x,y]) => window.__ripple(x,y)", [x, y])
        self.pause(0.18)
        self.page.mouse.click(x, y)
        self.pause(settle)

    def zoom(self, scale, x, y, hold=1.6):
        self.page.evaluate("([s,x,y]) => window.__zoom(s,x,y)", [scale, x, y])
        self.pause(0.95 + hold)

    def unzoom(self, hold=0.5):
        self.page.evaluate("() => window.__zoom(1, 0, 0)")
        self.pause(0.95 + hold)


def main() -> None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    # warm the server's search path (embedding model load) so the recording
    # never shows a loading spinner
    import urllib.request
    try:
        urllib.request.urlopen(BASE + "/api/items?q=warmup&mode=hybrid", timeout=60).read()
    except OSError:
        pass
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1280, "height": 720},
        )
        page = ctx.new_page()
        page.goto(BASE)
        page.wait_for_selector("text=km.")
        page.wait_for_timeout(1500)
        d = Director(page)
        d.pause(1.2)

        # 1. search: glide to the bar, type a query, results filter live
        search = page.get_by_placeholder("Search the archive...")
        d.click(search, ms=800)
        page.keyboard.type("chesterton", delay=95)
        page.keyboard.press("Enter")
        d.pause(1.6)

        # 2. open the first matching entry in the drawer, zoom into it
        row = page.locator("text=Chesterton's fence").first
        d.click(row, ms=750, settle=1.0)
        d.zoom(1.35, 880, 300, hold=1.8)
        d.unzoom(0.3)
        page.keyboard.press("Escape")
        d.pause(0.5)

        # clear the search
        d.click(search, ms=550)
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        page.keyboard.press("Escape")
        d.pause(0.8)

        # 3. wisdom collections
        d.click(page.locator("text=Aphorisms").first, ms=750, settle=1.4)
        d.click(page.locator("text=Natural laws").first, ms=550, settle=1.6)

        # 4. stats page with the new rhythm chart
        d.click(page.locator("text=Stats").first, ms=800, settle=1.6)
        d.zoom(1.3, 400, 150, hold=1.6)   # headline numbers
        d.unzoom(0.2)
        d.zoom(1.25, 640, 520, hold=1.7)  # hour-of-day chart
        d.unzoom(0.3)

        # 5. today: on-this-day resurfacing
        d.click(page.locator("text=Today").first, ms=750, settle=2.2)

        # 6. end on the whole archive
        d.click(page.locator("text=Everything").first, ms=750, settle=2.0)

        ctx.close()
        video_path = page.video.path()
        browser.close()

    out = OUT_DIR / "km-demo.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    ], check=True, capture_output=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
