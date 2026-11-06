"""
Gölgedefter marka öğeleri (bir kez üretilir, assets/golgedefter/ altına kaydedilir):
  intro.mp4, outro.png, golgedefter_logo.png, golgedefter_banner.jpg, music.mp3, whoosh.wav
İşaret: açık bir defterin ortasından yükselen gölge alevi (kenarı mum ışığı gibi parlar).
Çalıştır:  python brand_golgedefter.py [--force]
"""
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from brand import FPS, SR, H, W, ease, impact, whoosh, write_wav, _lowpass

ROOT = Path(__file__).parent
A = ROOT / "assets" / "golgedefter"
INK, INK2 = (13, 11, 15), (32, 25, 29)
PARCH, PARCH_D = (234, 216, 180), (176, 150, 112)
AMBER, AMBER_L = (232, 158, 68), (255, 205, 130)
CRIMSON = (150, 32, 38)
SMOKE = (170, 158, 150)
SERIF = "/usr/share/fonts/truetype/crosextra/Caladea-Bold.ttf"
INTER = "/usr/share/fonts/opentype/inter/"
NAME, TAG = "GÖLGEDEFTER", "Açıklanamayanların defteri."
DAYS = "Salı · Perşembe · Cumartesi"


def font(path, size):
    for f in (path, INTER + "InterDisplay-Bold.otf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def spaced(d, xy, text, f, fill, track=0.12, anchor_center=False, width=None):
    """Harf aralığı açık yazı (sinematik başlık görünümü)."""
    size = f.size
    widths = [d.textlength(ch, font=f) for ch in text]
    total = sum(widths) + track * size * (len(text) - 1)
    x, y = xy
    if anchor_center:
        x = ((width or W) - total) / 2
    for ch, w in zip(text, widths):
        d.text((x, y), ch, font=f, fill=fill)
        x += w + track * size
    return total


# ------------------------------------------------------------------ işaret
def _bez(p0, p1, p2, p3, n=40):
    out = []
    for k in range(n + 1):
        t = k / n
        out.append(tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * e
                         for a, b, c, e in zip(p0, p1, p2, p3)))
    return out


def _flame(cx, base, top, w):
    """Alev / duman biçimi: tabanda geniş, ucu hafif kıvrık."""
    h = base - top
    left = _bez((cx, base), (cx - w * 1.15, base - h * .22), (cx - w * .55, base - h * .62), (cx + w * .08, top))
    right = _bez((cx + w * .08, top), (cx + w * .05, base - h * .55), (cx + w * 1.05, base - h * .30), (cx, base))
    return left + right[1:]


def emblem(size, rise=1.0, S=3):
    """Açık defter + ortasından yükselen gölge alevi. rise: 0 = alev yok, 1 = tam."""
    Wd = size * S
    im = Image.new("RGBA", (Wd, Wd), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = lambda x, y: (x * Wd, y * Wd)
    cx = .5
    # defter: alt kalınlık (sayfa yığını) + iki sayfa
    for side in (-1, 1):
        top = _bez(u(cx, .52), u(cx + side * .12, .45), u(cx + side * .28, .44), u(cx + side * .40, .47))
        bot = _bez(u(cx + side * .40, .74), u(cx + side * .28, .74), u(cx + side * .12, .76), u(cx, .80))
        d.polygon(bot + [u(cx, .85), u(cx + side * .40, .785)], fill=PARCH_D)
        d.polygon(top + bot, fill=PARCH)
        for k in range(4):  # yazı satırları
            y0 = .53 + k * .045
            x0, x1 = (cx + side * .07, cx + side * .33)
            d.line([u(x0, y0 + .012), u(x1, y0)], fill=PARCH_D, width=max(1, int(Wd * .008)))
    d.line([u(cx, .52), u(cx, .80)], fill=PARCH_D, width=max(1, int(Wd * .01)))
    # gölge alevi: önce kehribar kenar (parıltılı), sonra koyu gövde
    t = max(0.0, min(1.0, rise))
    if t > 0.01:
        base, top = .56, .56 - .48 * t
        glow = Image.new("RGBA", (Wd, Wd), (0, 0, 0, 0))
        ImageDraw.Draw(glow).polygon(_flame(cx * Wd, base * Wd, (top - .02) * Wd, .145 * Wd), fill=AMBER + (210,))
        glow = glow.filter(ImageFilter.GaussianBlur(Wd * .02))
        im.alpha_composite(glow)
        d = ImageDraw.Draw(im)
        d.polygon(_flame(cx * Wd, base * Wd, (top - .012) * Wd, .132 * Wd), fill=AMBER_L)
        d.polygon(_flame(cx * Wd, base * Wd, top * Wd, .118 * Wd), fill=INK)
        # alevin içinde küçük kızıl göz/kıvılcım
        r = .022 * Wd * t
        ey = (base - (base - top) * .42) * Wd
        d.ellipse([cx * Wd - r, ey - r, cx * Wd + r, ey + r], fill=CRIMSON)
    return im.resize((size, size), Image.LANCZOS)


def background(w=W, h=H, grain=True, seed=3):
    im = Image.new("RGB", (w, h), INK)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = abs(y - h * .45) / (h * .6)
        t = min(1, t)
        d.line([(0, y), (w, y)], fill=tuple(int(INK2[i] * (1 - t) + INK[i] * t) for i in range(3)))
    if grain:
        rng = np.random.default_rng(seed)
        n = (rng.standard_normal((h, w)) * 6).astype(np.int16)
        a = np.asarray(im).astype(np.int16) + n[..., None]
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    # kenar kararması
    v = Image.new("L", (w, h), 0)
    ImageDraw.Draw(v).ellipse([-w * .15, -h * .25, w * 1.15, h * 1.25], fill=255)
    v = v.filter(ImageFilter.GaussianBlur(min(w, h) * .18))
    return Image.composite(im, Image.new("RGB", (w, h), (4, 3, 5)), v)


def _candle_glow(im, c, r, alpha):
    g = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ImageDraw.Draw(g).ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=(200, 120, 50, int(alpha)))
    g = g.filter(ImageFilter.GaussianBlur(r * .45))
    im.paste(g, (0, 0), g)


# ------------------------------------------------------------------ ses
def drone(dur, seed=11):
    """Karanlık, gizemli müzik yatağı: re minör pedal + yavaş akorlar + uzak çan + rüzgâr."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n, np.float32)
    f0 = 440 * 2 ** ((38 - 69) / 12)  # D2
    lfo = 1 + 0.004 * np.sin(2 * np.pi * 0.07 * t)
    out += (np.sin(2 * np.pi * f0 * lfo * t) + 0.5 * np.sin(2 * np.pi * f0 * 1.5 * t)) * 0.10
    chords = [(62, 65, 69), (58, 62, 65), (60, 64, 67), (57, 61, 64)]  # Dm Bb C A
    bar = 8.0
    for i in range(int(dur / bar) + 1):
        s, e = int(i * bar * SR), min(n, int((i + 1.3) * bar * SR))
        if s >= n:
            break
        tt = t[s:e] - i * bar
        env = np.minimum(tt / 3.0, 1) * np.exp(-np.maximum(tt - bar, 0) * 1.5)
        for m in chords[i % 4]:
            f = 440 * 2 ** ((m - 69) / 12)
            out[s:e] += (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(2 * np.pi * f * 2.003 * tt)) * env * 0.045
        # uzak çan, her iki ölçüde bir
        if i % 2 == 1:
            bs = s + int(bar * .5 * SR)
            be = min(n, bs + int(5 * SR))
            if bs < n:
                bt = t[bs:be] - t[bs]
                fb = 440 * 2 ** ((chords[i % 4][0] + 12 - 69) / 12)
                out[bs:be] += sum(np.sin(2 * np.pi * fb * k * bt) / k for k in (1, 2.76, 5.4)) * np.exp(-bt * 1.1) * 0.05
    wind = _lowpass(rng.standard_normal(n).astype(np.float32), 0.008)
    wind *= (0.6 + 0.4 * np.sin(2 * np.pi * 0.05 * t)).astype(np.float32)
    out += wind / (np.abs(wind).max() + 1e-9) * 0.06
    for dly, g in ((0.31, 0.35), (0.57, 0.25), (0.93, 0.18)):
        k = int(dly * SR)
        out[k:] += out[:-k] * g
    fade = int(3 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return (out / (np.abs(out).max() + 1e-9) * 0.6).astype(np.float32)


# ------------------------------------------------------------------ intro
def make_intro(out, dur=3.0):
    frames = int(dur * FPS)
    bg = background()
    title_f, tag_f = font(SERIF, 150), font(INTER + "InterDisplay-Medium.otf", 48)
    proc = subprocess.Popen(["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                             "-pix_fmt", "yuv420p", str(out) + ".v.mp4"],
                            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    cache, rng = {}, np.random.default_rng(5)
    for i in range(frames):
        t = i / FPS
        im = bg.copy()
        flick = 0.85 + 0.15 * math.sin(t * 23) * math.sin(t * 7.3) + rng.uniform(-.04, .04)
        _candle_glow(im, (W // 2, 420), 340, 90 * ease(t / 0.9) * flick)
        rise = ease((t - 0.35) / 0.9)
        size = 440
        key = round(rise, 2)
        if key not in cache:
            cache[key] = emblem(size, rise)
        e = cache[key].copy()
        e.putalpha(e.getchannel("A").point(lambda v: int(v * min(1, t / 0.4))))
        im.paste(e, (W // 2 - size // 2, 140), e)
        d = ImageDraw.Draw(im)
        tt = ease((t - 1.0) / 0.7)
        if tt > 0:
            col = tuple(int(INK[k] + (PARCH[k] - INK[k]) * tt) for k in range(3))
            spaced(d, (0, 610), NAME, title_f, col, track=0.10 + 0.06 * (1 - tt), anchor_center=True)
        tg = ease((t - 1.5) / 0.5)
        if tg > 0:
            col = tuple(int(INK[k] + (AMBER[k] - INK[k]) * tg) for k in range(3))
            w = d.textlength(TAG, font=tag_f)
            d.text(((W - w) / 2, 800), TAG, font=tag_f, fill=col)
        if t > dur - 0.35:
            k = (t - (dur - 0.35)) / 0.35
            im = Image.blend(im, Image.new("RGB", (W, H), INK), k * 0.7)
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    proc.wait()
    n = int(dur * SR)
    a = np.zeros(n, np.float32)
    w = whoosh(1.1, seed=4)
    a[:len(w)] += w * 0.6
    hit = impact(2.2) * 0.9
    s = int(0.95 * SR)
    a[s:s + len(hit)] += hit[:max(0, n - s)]
    pad = drone(dur + 1, seed=2)[:n] * 0.5
    a += pad
    a[-int(0.4 * SR):] *= np.linspace(1, 0, int(0.4 * SR))
    write_wav(str(out) + ".a.wav", a / (np.abs(a).max() + 1e-9) * 0.8)
    subprocess.run(["ffmpeg", "-y", "-i", str(out) + ".v.mp4", "-i", str(out) + ".a.wav", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)], check=True, stderr=subprocess.DEVNULL)
    Path(str(out) + ".v.mp4").unlink()
    Path(str(out) + ".a.wav").unlink()
    return out


# ------------------------------------------------------------------ kartlar
def make_outro_card(out):
    im = background()
    _candle_glow(im, (W // 2, 330), 380, 80)
    d = ImageDraw.Draw(im)
    e = emblem(360)
    im.paste(e, (W // 2 - 180, 70), e)

    def center(text, f, y, fill):
        w = d.textlength(text, font=f)
        d.text(((W - w) / 2, y), text, font=f, fill=fill)
    spaced(d, (0, 450), NAME, font(SERIF, 120), PARCH, track=0.10, anchor_center=True)
    center(TAG, font(INTER + "InterDisplay-Medium.otf", 44), 605, AMBER)
    bf = font(INTER + "InterDisplay-Bold.otf", 44)
    bw = d.textlength("ABONE OL", font=bf) + 110
    d.rounded_rectangle([(W - bw) / 2, 710, (W + bw) / 2, 795], 42, fill=(196, 30, 30))
    center("ABONE OL", bf, 726, (250, 244, 236))
    center(f"Yeni sayfalar her {DAYS}", font(INTER + "InterDisplay-Medium.otf", 34), 845, SMOKE)
    im.save(out, quality=95)
    return out


def make_avatar(out, size=1024):
    S = 2
    Wd = size * S
    im = background(Wd, Wd, grain=False)
    _candle_glow(im, (Wd // 2, int(Wd * .42)), int(Wd * .36), 110)
    e = emblem(int(Wd * .92))
    im.paste(e, (int(Wd * .04), int(Wd * .05)), e)
    im.resize((size, size), Image.LANCZOS).save(out)
    return out


def make_banner(out):
    """2560x1440; yazı/logo tüm cihazlarda görünen güvenli alanda (1546x423 ortada)."""
    Wb, Hb = 2560, 1440
    im = background(Wb, Hb)
    for (x, y, s, a) in [(-300, 700, 1100, 14), (1900, -380, 1000, 11)]:
        e = emblem(s)
        e.putalpha(e.getchannel("A").point(lambda v: v * a // 255))
        im.paste(e, (x, y), e)
    _candle_glow(im, (720, 720), 300, 70)
    e = emblem(420)
    im.paste(e, (510, 505), e)
    d = ImageDraw.Draw(im)
    spaced(d, (960, 570), NAME, font(SERIF, 140), PARCH, track=0.08)  # güvenli alan: x ≤ 2053
    d.text((966, 765), TAG, font=font(INTER + "InterDisplay-Medium.otf", 60), fill=AMBER)
    d.text((966, 842), "YENİ SAYFALAR · SALI · CUMA · CUMARTESİ · PAZAR",
           font=font(INTER + "InterDisplay-Bold.otf", 30), fill=SMOKE)
    d.text((966, 884), "SALI & CUMA 19:00  ·  HAFTA SONU 16:00",
           font=font(INTER + "InterDisplay-Bold.otf", 30), fill=AMBER)
    im.save(out, quality=92)
    return out


def build_all(force=False):
    A.mkdir(parents=True, exist_ok=True)
    jobs = [("intro.mp4", make_intro), ("outro.png", make_outro_card),
            ("golgedefter_logo.png", make_avatar), ("golgedefter_banner.jpg", make_banner)]
    for name, fn in jobs:
        if force or not (A / name).exists():
            fn(A / name)
            print("✓", name)
    if force or not (A / "whoosh.wav").exists():
        write_wav(A / "whoosh.wav", whoosh(0.7, seed=9) * 0.8)
        print("✓ whoosh.wav")
    if force or not (A / "music.mp3").exists():
        write_wav(A / "music_tmp.wav", drone(128.0))
        subprocess.run(["ffmpeg", "-y", "-i", str(A / "music_tmp.wav"), "-c:a", "libmp3lame", "-b:a", "160k",
                        str(A / "music.mp3")], check=True, stderr=subprocess.DEVNULL)
        (A / "music_tmp.wav").unlink()
        print("✓ music.mp3")


if __name__ == "__main__":
    import sys
    build_all(force="--force" in sys.argv)
