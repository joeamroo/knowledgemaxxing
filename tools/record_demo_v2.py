"""Structured 1080p demo video with narrated callout bubbles.

Runs against the synthetic demo DB (never real data), which must be
embedded first so semantic Ask works:
    .venv/bin/python tools/seed_demo.py
    KM_DB=data/demo.db .venv/bin/python -m km.cli embed
    KM_DB=data/demo.db .venv/bin/python -m km.cli ui --port 8890
    .venv/bin/python tools/record_demo_v2.py

Scenes: search -> provenance drawer -> semantic Ask -> Essays ->
Aphorisms -> Stats -> Today -> end card. A callout bubble pops next to
each thing the cursor is about to do.
Output: exports/km-demo.mp4 (1920x1080 h264).
"""
from __future__ import annotations

import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8890"
OUT_DIR = Path(__file__).resolve().parent.parent / "exports"
VIDEO_DIR = OUT_DIR / "demo-video-raw"
W, H = 1920, 1080

STAGE_JS = """
() => {
  // cursor
  const c = document.createElement('div');
  c.id = '__km_cursor';
  c.innerHTML = `<svg width="34" height="34" viewBox="0 0 24 24">
    <path d="M5 3 L19 12 L12 13.5 L9.5 20 Z" fill="#fff" stroke="#000" stroke-width="1.3"/></svg>`;
  Object.assign(c.style, {position:'fixed', left:'960px', top:'620px', zIndex:99999,
    pointerEvents:'none',
    transition:'left 0.7s cubic-bezier(.25,.6,.3,1), top 0.7s cubic-bezier(.25,.6,.3,1)',
    filter:'drop-shadow(0 3px 7px rgba(0,0,0,.6))'});
  document.body.appendChild(c);
  window.__moveCursor = (x, y, ms) => {
    c.style.transitionDuration = ms + 'ms, ' + ms + 'ms';
    c.style.left = x + 'px'; c.style.top = y + 'px';
  };
  window.__ripple = (x, y) => {
    const r = document.createElement('div');
    Object.assign(r.style, {position:'fixed', left:(x-5)+'px', top:(y-5)+'px', width:'10px',
      height:'10px', borderRadius:'50%', border:'3px solid #e3b04b', zIndex:99998,
      pointerEvents:'none', opacity:'0.95', transition:'all .5s ease-out'});
    document.body.appendChild(r);
    requestAnimationFrame(() => { r.style.transform='scale(5)'; r.style.opacity='0'; });
    setTimeout(() => r.remove(), 600);
  };

  // narrated bubbles
  const style = document.createElement('style');
  style.textContent = `
    .__km_bubble{position:fixed;z-index:99997;max-width:430px;background:#241d15;
      border:1px solid #4a3a22;border-left:4px solid #e3b04b;border-radius:12px;
      padding:16px 20px;color:#efe6d5;font-size:19px;line-height:1.45;
      font-family:'IBM Plex Sans',system-ui,sans-serif;
      box-shadow:0 18px 50px rgba(0,0,0,.65);
      opacity:0;transform:scale(.85) translateY(8px);
      transition:opacity .35s ease, transform .38s cubic-bezier(.34,1.4,.5,1)}
    .__km_bubble.on{opacity:1;transform:scale(1) translateY(0)}
    .__km_bubble b{color:#e3b04b;font-weight:600}
  `;
  document.head.appendChild(style);
  window.__bubble = (x, y, html) => {
    window.__hideBubbles();
    const b = document.createElement('div');
    b.className = '__km_bubble';
    b.innerHTML = html;
    b.style.left = x + 'px'; b.style.top = y + 'px';
    document.body.appendChild(b);
    requestAnimationFrame(() => b.classList.add('on'));
  };
  window.__hideBubbles = () => {
    document.querySelectorAll('.__km_bubble').forEach(b => {
      b.classList.remove('on'); setTimeout(() => b.remove(), 380);
    });
  };

  // zoom stage
  const root = document.getElementById('root') || document.body.firstElementChild;
  root.style.transition = 'transform 1s cubic-bezier(.4,.05,.2,1)';
  window.__zoom = (scale, ox, oy) => {
    root.style.transformOrigin = ox + 'px ' + oy + 'px';
    root.style.transform = scale === 1 ? 'none' : 'scale(' + scale + ')';
  };

  // end card
  window.__endCard = () => {
    window.__hideBubbles();
    const e = document.createElement('div');
    Object.assign(e.style, {position:'fixed', inset:'0', zIndex:100000, display:'flex',
      flexDirection:'column', alignItems:'center', justifyContent:'center', gap:'18px',
      background:'#171310', opacity:'0', transition:'opacity 1s ease'});
    e.innerHTML = `
      <div style="font-family:Georgia,'Times New Roman',serif;font-weight:700;
        font-size:110px;color:#e8ddcb;letter-spacing:-2px">km<span style="color:#e3b04b">.</span></div>
      <div style="font-family:'IBM Plex Sans',sans-serif;font-size:30px;color:#a89880">
        your whole digital life, searchable, locally</div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:21px;color:#6f6252;margin-top:14px">
        montroselabs.ai/km &nbsp;·&nbsp; github.com/joeamroo/knowledgemaxxing</div>`;
    document.body.appendChild(e);
    requestAnimationFrame(() => e.style.opacity = '1');
  };
}
"""


class Director:
    def __init__(self, page):
        self.page = page
        page.evaluate(STAGE_JS)

    def pause(self, seconds: float):
        time.sleep(seconds)

    def say(self, x, y, html, hold=0.4):
        self.page.evaluate("([x,y,h]) => window.__bubble(x,y,h)", [x, y, html])
        self.pause(hold)

    def hush(self):
        self.page.evaluate("() => window.__hideBubbles()")
        self.pause(0.35)

    def glide(self, locator, ms=750):
        box = locator.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.evaluate("([x,y,ms]) => window.__moveCursor(x,y,ms)", [x, y, ms])
        self.pause(ms / 1000 + 0.15)
        return x, y

    def click(self, locator, ms=750, settle=0.8):
        x, y = self.glide(locator, ms)
        self.page.evaluate("([x,y]) => window.__ripple(x,y)", [x, y])
        self.pause(0.18)
        self.page.mouse.click(x, y)
        self.pause(settle)

    def zoom(self, scale, x, y, hold=1.6):
        self.page.evaluate("([s,x,y]) => window.__zoom(s,x,y)", [scale, x, y])
        self.pause(1.05 + hold)

    def unzoom(self, hold=0.5):
        self.page.evaluate("() => window.__zoom(1, 0, 0)")
        self.pause(1.05 + hold)


def main() -> None:
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    try:  # warm search + ask so nothing spins on camera
        urllib.request.urlopen(BASE + "/api/items?q=warmup&mode=hybrid", timeout=90).read()
    except OSError:
        pass
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=1,
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": W, "height": H},
        )
        page = ctx.new_page()
        page.goto(BASE)
        page.wait_for_selector("text=km.")
        page.wait_for_timeout(1600)
        d = Director(page)

        # scene 0: what this is
        d.say(700, 170, "<b>This is km.</b> Your searches, saves, tweets, notes, and AI chats. One local archive.", hold=2.8)
        d.hush()

        # scene 1: search
        search = page.get_by_placeholder("Search the archive...")
        d.say(360, 110, "Search <b>everything you ever saved</b>...", hold=0.4)
        d.click(search, ms=800)
        page.keyboard.type("chesterton", delay=90)
        page.keyboard.press("Enter")
        d.pause(1.4)
        d.hush()
        row = page.locator("text=Chesterton's fence").first
        d.say(760, 300, "Found in your <b>X bookmarks</b>. Open it.", hold=0.5)
        d.click(row, ms=700, settle=1.0)
        d.hush()
        d.say(620, 260, "Every item keeps its <b>provenance</b>: where, when, and how you saved it.", hold=0.4)
        d.zoom(1.3, 1500, 330, hold=2.4)
        d.unzoom(0.2)
        d.hush()
        page.keyboard.press("Escape")
        d.pause(0.4)
        d.click(search, ms=500)
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        page.keyboard.press("Escape")
        d.pause(0.5)

        # scene 2: semantic ask
        ask_btn = page.locator("button", has_text="Ask ✦").first
        d.say(1330, 120, "Half-remember something? <b>Ask in your own words.</b>", hold=0.5)
        d.click(ask_btn, ms=700, settle=0.9)
        d.hush()
        page.keyboard.type("the tweet about machine guns and birds in australia", delay=52)
        d.pause(0.3)
        rerank = page.locator("text=Claude re-rank")
        if rerank.count():
            d.click(rerank.first, ms=450, settle=0.3)
        ask_go = page.locator("button", has_text="Ask ✦").last
        d.click(ask_go, ms=500, settle=0.4)
        page.wait_for_selector("text=hybrid results", timeout=30000)
        d.pause(1.0)
        d.say(1280, 560, "<b>Local embeddings</b> find it. No cloud, no API.", hold=2.6)
        d.hush()
        page.keyboard.press("Escape")
        d.pause(0.5)

        # scene 3: essays
        d.click(page.locator("text=Essays").first, ms=700, settle=0.9)
        d.say(330, 300, "The <b>essays you saved and forgot</b> live here.", hold=2.2)
        d.hush()
        d.click(page.locator("text=Everything").first, ms=450, settle=0.5)

        # scene 4: wisdom
        d.click(page.locator("text=Aphorisms").first, ms=600, settle=0.9)
        d.say(330, 360, "<b>Aphorisms and laws</b>, mined from your likes.", hold=2.2)
        d.hush()

        # scene 5: stats
        d.say(180, 560, "And it <b>reads you back</b>.", hold=0.4)
        d.click(page.locator("text=Stats").first, ms=700, settle=1.6)
        d.hush()
        d.say(1210, 250, "Every day you left a trace, <b>year by year</b>.", hold=0.4)
        d.zoom(1.22, 700, 380, hold=2.2)
        d.unzoom(0.2)
        d.hush()
        page.evaluate("([x,y,ms]) => window.__moveCursor(x,y,ms)", [980, 560, 500])
        page.mouse.move(980, 560)
        d.pause(0.7)
        page.mouse.wheel(0, 620)
        d.pause(0.9)
        d.say(1180, 330, "Hour by hour. <b>Yes, you are nocturnal.</b>", hold=2.3)
        d.hush()

        # scene 6: today
        d.say(180, 900, "<b>On this day</b>, across all your years.", hold=0.5)
        d.click(page.locator("text=Today").first, ms=700, settle=2.3)
        d.hush()

        # end card
        page.evaluate("() => window.__endCard()")
        d.pause(3.4)

        ctx.close()
        video_path = page.video.path()
        browser.close()

    out = OUT_DIR / "km-demo.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-c:v", "libx264", "-preset", "slow", "-crf", "19",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    ], check=True, capture_output=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
