"""Build Product Hunt gallery frames from the synthetic demo screenshots.

Composites the vetted synthetic screenshots in .github/assets/ onto a
branded 1600x1000 (16:10) charcoal canvas with a caption. No real data is
ever touched: inputs are the same synthetic-demo images shipped in the repo.

    python3 tools/make_ph_gallery.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / ".github" / "assets"
OUT = ROOT / "docs" / "launch" / "gallery"
OUT.mkdir(parents=True, exist_ok=True)

W, H = 1600, 1000
CHARCOAL = (23, 19, 16)
CREAM = (232, 221, 203)
GOLD = (227, 176, 75)
MUTED = (168, 152, 128)

FRAMES = [
    ("02-search.png", "km-shot-drawer.png", "Hybrid search with full provenance for every result"),
    ("03-feed.png", "km-shot-feed.png", "A daily reading feed built from your own taste"),
    ("04-companion.png", "km-shot-companion.png", "An AI companion that has read your entire archive"),
    ("05-stats.png", "km-stats.png", "Your reading, mapped: accumulation, heatmap, rhythms"),
]


def _font(size: int, bold: bool = False):
    for name in (
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0], img.size[1]], radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def build_frame(out_name: str, shot_name: str, caption: str) -> None:
    canvas = Image.new("RGB", (W, H), CHARCOAL)
    d = ImageDraw.Draw(canvas)

    # header: gold rule + wordmark
    d.rectangle([80, 70, 140, 74], fill=GOLD)
    wf = _font(40, bold=True)
    d.text((80, 92), "km", font=wf, fill=CREAM)
    km_w = d.textlength("km", font=wf)
    d.text((80 + km_w + 16, 106), "knowledgemaxxing", font=_font(22), fill=MUTED)

    # caption line
    d.text((80, 168), caption, font=_font(30), fill=CREAM)

    # screenshot, fit within frame
    shot = Image.open(ASSETS / shot_name).convert("RGB")
    max_w, max_h = W - 160, H - 320
    scale = min(max_w / shot.width, max_h / shot.height)
    sw, sh = int(shot.width * scale), int(shot.height * scale)
    shot = shot.resize((sw, sh), Image.LANCZOS)
    shot = _rounded(shot, 14)

    x = (W - sw) // 2
    y = 250

    # soft shadow
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x + 6, y + 12, x + sw + 6, y + sh + 12], 14, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), shadow).convert("RGB"), (0, 0))

    canvas.paste(shot, (x, y), shot)
    # thin cream border
    ImageDraw.Draw(canvas).rounded_rectangle([x, y, x + sw, y + sh], 14, outline=(232, 221, 203, 40), width=1)

    # footer url
    d = ImageDraw.Draw(canvas)
    d.text((80, H - 58), "montroselabs.ai/km", font=_font(24), fill=MUTED)
    d.text((W - 360, H - 58), "free  .  open source  .  100% local", font=_font(22), fill=GOLD)

    canvas.save(OUT / out_name)
    print("wrote", OUT / out_name, canvas.size)


def build_hero() -> None:
    card = Image.open(ASSETS / "km-card.png").convert("RGB")
    scale = min(W / card.width, H / card.height)
    cw, ch = int(card.width * scale), int(card.height * scale)
    card = card.resize((cw, ch), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), CHARCOAL)
    canvas.paste(card, ((W - cw) // 2, (H - ch) // 2))
    canvas.save(OUT / "01-hero.png")
    print("wrote", OUT / "01-hero.png", canvas.size)


if __name__ == "__main__":
    build_hero()
    for out_name, shot_name, caption in FRAMES:
        build_frame(out_name, shot_name, caption)
    print("done ->", OUT)
