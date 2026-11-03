"""
Smokeless Archive marka öğeleri (eski Cutaway kanalı; 03.11.2026'dan itibaren cinler ve görünmeyen âlem).
Gölgedefter'in kardeş kanalı: aynı defter işareti, ama alev "dumansız ateş" gibi soğuk mavi-beyaz parlar.
Üretir (assets/smokeless/): intro.mp4, outro.png, smokeless_logo.png, smokeless_banner.jpg, music.mp3, whoosh.wav
Çalıştır:  python brand_smokeless.py [--force]
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

import brand_golgedefter as g
from brand import W, write_wav, whoosh

ROOT = Path(__file__).parent
g.A = A = ROOT / "assets" / "smokeless"
g.NAME, g.TAG = "SMOKELESS ARCHIVE", "Records of the unseen."
g.AMBER, g.AMBER_L = (96, 156, 255), (214, 232, 255)   # dumansız ateş: mavi-beyaz kenar
g.PARCH, g.PARCH_D = (226, 222, 210), (150, 146, 136)
g.SMOKE = (150, 160, 178)


def _cold_glow(im, c, r, alpha):
    gl = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(gl).ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=(60, 110, 220, int(alpha)))
    from PIL import ImageFilter
    gl = gl.filter(ImageFilter.GaussianBlur(r * .45))
    base = im.convert("RGBA")
    base.alpha_composite(gl)
    im.paste(base.convert("RGB"))


g._candle_glow = _cold_glow
_font = g.font


def _intro_font(path, size):  # uzun ad: girişteki 150 pt başlık ekrana sığsın
    return _font(path, 100 if size == 150 else size)


def make_outro_card(out):
    im = g.background()
    _cold_glow(im, (W // 2, 330), 380, 80)
    d = ImageDraw.Draw(im)
    e = g.emblem(360)
    im.paste(e, (W // 2 - 180, 70), e)

    def center(text, f, y, fill):
        w = d.textlength(text, font=f)
        d.text(((W - w) / 2, y), text, font=f, fill=fill)
    g.spaced(d, (0, 465), g.NAME, g.font(g.SERIF, 96), g.PARCH, track=0.08, anchor_center=True)
    center(g.TAG, g.font(g.INTER + "InterDisplay-Medium.otf", 44), 605, g.AMBER)
    bf = g.font(g.INTER + "InterDisplay-Bold.otf", 44)
    bw = d.textlength("SUBSCRIBE", font=bf) + 110
    d.rounded_rectangle([(W - bw) / 2, 710, (W + bw) / 2, 795], 42, fill=(196, 30, 30))
    center("SUBSCRIBE", bf, 726, (250, 244, 236))
    center("A new record every Monday, Wednesday and Friday", g.font(g.INTER + "InterDisplay-Medium.otf", 34),
           845, g.SMOKE)
    im.save(out, quality=95)
    return out


def make_banner(out):
    """2560x1440; yazı/logo tüm cihazlarda görünen güvenli alanda (ortadaki 1546x423)."""
    Wb, Hb = 2560, 1440
    im = g.background(Wb, Hb)
    for (x, y, s, a) in [(-300, 700, 1100, 14), (1900, -380, 1000, 11)]:
        e = g.emblem(s)
        e.putalpha(e.getchannel("A").point(lambda v: v * a // 255))
        im.paste(e, (x, y), e)
    _cold_glow(im, (720, 720), 300, 70)
    e = g.emblem(420)
    im.paste(e, (510, 505), e)
    d = ImageDraw.Draw(im)
    g.spaced(d, (960, 585), "SMOKELESS", g.font(g.SERIF, 118), g.PARCH, track=0.08)
    g.spaced(d, (962, 715), "ARCHIVE", g.font(g.SERIF, 64), g.AMBER, track=0.32)
    d.text((966, 815), g.TAG, font=g.font(g.INTER + "InterDisplay-Medium.otf", 50), fill=g.PARCH_D)
    d.text((966, 892), "NEW RECORDS  ·  MON  ·  WED  ·  FRI",
           font=g.font(g.INTER + "InterDisplay-Bold.otf", 34), fill=g.SMOKE)
    im.save(out, quality=92)
    return out


def build_all(force=False):
    A.mkdir(parents=True, exist_ok=True)
    def intro(out):
        g.font = _intro_font
        try:
            return g.make_intro(out)
        finally:
            g.font = _font
    jobs = [("intro.mp4", intro), ("outro.png", make_outro_card),
            ("smokeless_logo.png", g.make_avatar), ("smokeless_banner.jpg", make_banner)]
    for name, fn in jobs:
        if force or not (A / name).exists():
            fn(A / name)
            print("✓", name)
    if force or not (A / "whoosh.wav").exists():
        write_wav(A / "whoosh.wav", whoosh(0.7, seed=11) * 0.8)
        print("✓ whoosh.wav")
    if force or not (A / "music.mp3").exists():
        write_wav(A / "music_tmp.wav", g.drone(128.0, seed=7))
        subprocess.run(["ffmpeg", "-y", "-i", str(A / "music_tmp.wav"), "-c:a", "libmp3lame", "-b:a", "160k",
                        str(A / "music.mp3")], check=True, stderr=subprocess.DEVNULL)
        (A / "music_tmp.wav").unlink()
        print("✓ music.mp3")


if __name__ == "__main__":
    import sys
    build_all(force="--force" in sys.argv)
