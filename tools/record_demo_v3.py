"""Polished 1080p60 demo: the full product story with narrated bubbles.

Scenes: archive -> search + provenance + more-like-this -> semantic Ask
-> reading Feed -> Tasks -> Companion (therapist) -> Stats -> end card.

Production notes:
- device_scale_factor=2 renders the page at 2x and Playwright downsamples
  into the 1920x1080 recording, so text is crisp.
- ffmpeg minterpolate lifts 25fps capture to 60fps for smooth cursor
  motion, then encodes high-bitrate h264.

Run against the synthetic demo DB only:
    KM_DB=data/demo.db .venv/bin/python -m km.cli ui --port 8890
    .venv/bin/python tools/record_demo_v3.py
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
  const style = document.createElement('style');
  style.textContent = `
    .__km_bubble{position:fixed;z-index:99997;max-width:440px;background:#241d15;
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
  const root = document.getElementById('root') || document.body.firstElementChild;
  root.style.transition = 'transform 1s cubic-bezier(.4,.05,.2,1)';
  window.__zoom = (scale, ox, oy) => {
    root.style.transformOrigin = ox + 'px ' + oy + 'px';
    root.style.transform = scale === 1 ? 'none' : 'scale(' + scale + ')';
  };
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
        every rabbit hole you ever went down, searchable</div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:21px;color:#6f6252;margin-top:14px">
        montroselabs.ai/km &nbsp;·&nbsp; free and open source</div>`;
    document.body.appendChild(e);
    requestAnimationFrame(() => e.style.opacity = '1');
  };
}
"""


class Director:
    def __init__(self, page):
        self.page = page
        page.evaluate(STAGE_JS)

    def pause(self, s):
        time.sleep(s)

    def say(self, x, y, html, hold=0.4):
        self.page.evaluate("([x,y,h]) => window.__bubble(x,y,h)", [x, y, html])
        self.pause(hold)

    def hush(self):
        self.page.evaluate("() => window.__hideBubbles()")
        self.pause(0.35)

    def glide(self, locator, ms=700):
        box = locator.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.evaluate("([x,y,ms]) => window.__moveCursor(x,y,ms)", [x, y, ms])
        self.pause(ms / 1000 + 0.15)
        return x, y

    def click(self, locator, ms=700, settle=0.8):
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
    try:
        urllib.request.urlopen(BASE + "/api/items?q=warmup&mode=hybrid", timeout=90).read()
    except OSError:
        pass
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            device_scale_factor=2,
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": W, "height": H},
        )
        page = ctx.new_page()
        page.goto(BASE)
        page.wait_for_selector("text=km.")
        page.wait_for_timeout(1600)
        d = Director(page)

        # 1. what this is
        d.say(700, 170, "<b>This is km.</b> Every search, save, note, and chat you ever made. One archive, on your machine.", hold=2.8)
        d.hush()

        # 2. search + provenance + more-like-this
        search = page.get_by_placeholder("Search the archive...")
        d.click(search, ms=800)
        page.keyboard.type("chesterton", delay=90)
        page.keyboard.press("Enter")
        d.pause(1.3)
        row = page.locator("text=Chesterton's fence").first
        d.click(row, ms=700, settle=1.1)
        d.say(620, 200, "Every item keeps its <b>provenance</b>, and the archive suggests <b>more like it</b>.", hold=0.4)
        d.zoom(1.28, 1520, 420, hold=2.6)
        d.unzoom(0.2)
        d.hush()
        page.keyboard.press("Escape")
        d.pause(0.3)
        d.click(search, ms=450)
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        page.keyboard.press("Escape")
        d.pause(0.4)

        # 3. semantic ask
        ask_btn = page.locator("button", has_text="Ask ✦").first
        d.say(1330, 120, "Half-remember something? <b>Ask in your own words.</b>", hold=0.5)
        d.click(ask_btn, ms=650, settle=0.9)
        d.hush()
        page.keyboard.type("the tweet about machine guns and birds in australia", delay=48)
        d.pause(0.3)
        rerank = page.locator("text=Claude re-rank")
        if rerank.count():
            d.click(rerank.first, ms=420, settle=0.3)
        ask_go = page.locator("button", has_text="Ask ✦").last
        d.click(ask_go, ms=480, settle=0.4)
        page.wait_for_selector("text=hybrid results", timeout=30000)
        d.pause(0.9)
        d.say(1280, 560, "<b>Local embeddings</b> find it. No cloud, no API.", hold=2.4)
        d.hush()
        page.keyboard.press("Escape")
        d.pause(0.4)

        # 4. the reading feed
        feed_tab = page.locator("aside button", has_text="Feed").first
        d.say(330, 150, "Every morning: a <b>reading feed</b> built from your own trail.", hold=0.5)
        d.click(feed_tab, ms=650, settle=1.3)
        d.hush()
        d.say(1180, 300, "Fresh posts from blogs you read, <b>plus what you saved and forgot</b>.", hold=2.2)
        first_check = page.locator("input[type=checkbox]").first
        if first_check.count():
            d.click(first_check, ms=600, settle=0.9)
        d.hush()

        # 5. tasks
        tasks_btn = page.locator("text=Tasks").first
        d.say(300, 900, "It also <b>keeps score</b> of what you said you'd do.", hold=0.5)
        d.click(tasks_btn, ms=650, settle=1.2)
        d.hush()
        d.say(760, 300, "Overdue first. Harvested from <b>your own notes</b>. The secretary persona sees all of it.", hold=2.4)
        d.hush()
        page.keyboard.press("Escape")
        d.pause(0.4)

        # 6. the companion
        comp_tab = page.locator("aside button", has_text="Companion").first
        d.say(330, 150, "And the point of it all: <b>an AI that has actually read you.</b>", hold=0.5)
        d.click(comp_tab, ms=650, settle=1.3)
        d.hush()
        d.say(1200, 260, "A <b>therapist</b> with your whole archive, that remembers every session.", hold=2.0)
        box = page.locator("textarea").first
        d.click(box, ms=600, settle=0.3)
        page.keyboard.type("I keep saying this year was a waste.", delay=55)
        d.pause(2.2)
        d.hush()

        # 7. stats, briefly
        d.click(page.locator("text=Stats").first, ms=650, settle=1.5)
        page.evaluate("([x,y,ms]) => window.__moveCursor(x,y,ms)", [980, 560, 500])
        page.mouse.move(980, 560)
        d.pause(0.6)
        page.mouse.wheel(0, 620)
        d.pause(0.8)
        d.say(1180, 330, "Hour by hour, year by year. <b>Yes, you are nocturnal.</b>", hold=2.2)
        d.hush()

        # 8. end
        page.evaluate("() => window.__endCard()")
        d.pause(3.4)

        ctx.close()
        video_path = page.video.path()
        browser.close()

    out = OUT_DIR / "km-demo.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", "minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:vsbmc=1",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    ], check=True, capture_output=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
