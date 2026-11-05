"""Bitiş ekranı (son 20 sn) arka planı: solda ÖNCEKİ VİDEO, sağda BUNU DA İZLE çerçevesi, ortada logo + ABONE OL.
YouTube Studio'daki bitiş ekranı öğeleri (video / video / abone ol) tam bu kutuların üstüne oturur.
Kutuların içine render sırasında kanalın önceki videolarının küçük resimleri basılır (endscreen_frame()).
Çalıştır: python make_endscreen.py  → assets/golgedefter/endscreen.png, assets/beyondqaf/endscreen.png"""
import importlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).parent
W, H = 1920, 1080
# YouTube bitiş ekranı ızgarasına göre kutular (16:9) ve abone ol dairesi
BOX_L = (110, 330, 110 + 656, 330 + 369)
BOX_R = (W - 110 - 656, 330, W - 110, 330 + 369)
CIRCLE = (W // 2, 515, 150)   # merkez x, y, yarıçap
LABELS = {"golgedefter": ("ÖNCEKİ VİDEO", "BUNU DA İZLE", "ABONE OL"),
          "beyondqaf": ("PREVIOUS STORY", "WATCH NEXT", "SUBSCRIBE")}


def _brand(ch):
    import brand_golgedefter as g
    g = importlib.reload(g)
    g.INTER = str(ROOT / "assets" / "fonts") + "/"
    if ch == "beyondqaf":
        import brand_beyondqaf as b
        importlib.reload(b)
        g.INTER = str(ROOT / "assets" / "fonts") + "/"
    return g


def _frame(d, box, accent):
    x0, y0, x1, y1 = box
    d.rounded_rectangle([x0 - 6, y0 - 6, x1 + 6, y1 + 6], 18, outline=accent, width=4)
    d.rounded_rectangle([x0, y0, x1, y1], 12, fill=(14, 12, 14))


def make(ch):
    g = _brand(ch)
    im = g.background()
    g._candle_glow(im, (CIRCLE[0], CIRCLE[1]), 330, 70)
    d = ImageDraw.Draw(im)
    left, right, sub = LABELS[ch]
    lf = g.font(g.INTER + "InterDisplay-Bold.otf", 40)
    for box, txt in ((BOX_L, left), (BOX_R, right)):
        _frame(d, box, g.AMBER)
        w = d.textlength(txt, font=lf)
        d.text(((box[0] + box[2] - w) / 2, box[1] - 72), txt, font=lf, fill=g.PARCH)
    cx, cy, r = CIRCLE
    d.ellipse([cx - r - 7, cy - r - 7, cx + r + 7, cy + r + 7], outline=g.AMBER, width=5)
    logo = Image.open(ROOT / "assets" / ch / f"{ch}_logo.png").convert("RGB").resize((2 * r, 2 * r), Image.LANCZOS)
    mask = Image.new("L", logo.size, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=255)
    im.paste(logo, (cx - r, cy - r), mask.filter(ImageFilter.GaussianBlur(1)))
    bf = g.font(g.INTER + "InterDisplay-Bold.otf", 42)
    bw = d.textlength(sub, font=bf) + 90
    d.rounded_rectangle([cx - bw / 2, cy + r + 34, cx + bw / 2, cy + r + 112], 39, fill=(196, 30, 30))
    d.text((cx - (bw - 90) / 2, cy + r + 48), sub, font=bf, fill=(250, 244, 236))
    g.spaced(d, (0, 120), g.NAME, g.font(g.SERIF, 72), g.PARCH, track=0.10, anchor_center=True)
    out = ROOT / "assets" / ch / "endscreen.png"
    im.save(out)
    return out


def endscreen_frame(base, thumbs, out):
    """Arka plana önceki videoların küçük resimlerini (varsa) kutulara basar; YouTube öğeleri tıklanabilir katmanı ekler."""
    im = Image.open(base).convert("RGB")
    for box, t in zip((BOX_L, BOX_R), thumbs):
        if not t or not Path(t).exists():
            continue
        th = Image.open(t).convert("RGB")
        bw, bh = box[2] - box[0], box[3] - box[1]
        s = max(bw / th.width, bh / th.height)
        th = th.resize((int(th.width * s) + 1, int(th.height * s) + 1), Image.LANCZOS)
        th = th.crop(((th.width - bw) // 2, (th.height - bh) // 2, (th.width - bw) // 2 + bw, (th.height - bh) // 2 + bh))
        m = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(m).rounded_rectangle([0, 0, bw - 1, bh - 1], 12, fill=255)
        im.paste(th, box[:2], m)
    im.save(out)
    return out


if __name__ == "__main__":
    for c in ("golgedefter", "beyondqaf"):
        print(make(c))
