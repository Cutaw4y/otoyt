"""
Cutaway marka öğeleri (bir kez üretilir, assets/ altına kaydedilir):
  intro.mp4    – 2.6 sn logo animasyonu (kesit dilimi açılır + isim) ve ses
  outro.png    – kapanış kartı (abone ol + "sıradaki video" alanları)
  music.mp3    – telifsiz, kodla üretilmiş sakin ambient müzik yatağı
  whoosh.wav   – sahne geçiş efekti
Çalıştır:  python brand.py
"""
import math
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).parent
A = ROOT / "assets"
W, H, FPS, SR = 1920, 1080, 30, 48000
INTER = "/usr/share/fonts/opentype/inter/"
NAVY, NAVY2 = (11, 18, 32), (19, 32, 54)
CYAN, CYAN_D = (94, 224, 240), (40, 140, 170)
ORANGE, ORANGE_D, ORANGE_L = (255, 160, 60), (220, 110, 30), (255, 205, 120)
WHITE = (238, 244, 250)


def font(name, size):
    for f in (INTER + name, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ------------------------------------------------------------------ çizim
def emblem(size, open_frac=1.0, S=3):
    """Katmanlı çekirdek; sağ üst çeyrek open_frac oranında dışarı çekilir (0=kapalı, 1=açık)."""
    Wd = size * S
    im = Image.new("RGBA", (Wd, Wd), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c, R = Wd / 2, Wd * 0.36
    layers = [(1.00, NAVY2, ORANGE_D), (0.80, (28, 48, 78), ORANGE), (0.60, (36, 64, 100), ORANGE_L),
              (0.38, CYAN_D, (255, 236, 190)), (0.18, CYAN, WHITE)]
    a0, a1 = -90, 0
    for k, body, _ in layers:
        r = R * k
        d.pieslice([c - r, c - r, c + r, c + r], a1, a0 + 360, fill=body)
    d.arc([c - R, c - R, c + R, c + R], a1, a0 + 360, fill=CYAN, width=int(Wd * 0.018))
    mid = math.radians((a0 + a1) / 2)
    off = Wd * 0.075 * open_frac
    ox, oy = c + off * math.cos(mid), c + off * math.sin(mid)
    for k, body, cut in layers:
        r = R * k
        col = tuple(int(body[i] + (cut[i] - body[i]) * min(1, open_frac * 1.2)) for i in range(3))
        d.pieslice([ox - r, oy - r, ox + r, oy + r], a0, a1, fill=col)
    if open_frac > 0.05:
        d.arc([ox - R, oy - R, ox + R, oy + R], a0, a1, fill=ORANGE_L, width=int(Wd * 0.012))
    return im.resize((size, size), Image.LANCZOS)


def background(grid_alpha=14):
    im = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = abs(y - H / 2) / (H / 2)
        d.line([(0, y), (W, y)], fill=tuple(int(NAVY2[i] * (1 - t) + NAVY[i] * t) for i in range(3)))
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    for x in range(0, W, 60):
        gd.line([(x, 0), (x, H)], fill=(94, 224, 240, grid_alpha))
    for y in range(0, H, 60):
        gd.line([(0, y), (W, y)], fill=(94, 224, 240, grid_alpha))
    im.paste(g, (0, 0), g)
    return im


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


# ------------------------------------------------------------------ ses
def write_wav(path, x, sr=SR):
    x = np.clip(x, -1, 1)
    st = np.stack([x, x], 1) if x.ndim == 1 else x
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((st * 32767).astype(np.int16).tobytes())


def _lowpass(x, a):
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += a * (v - acc)
        y[i] = acc
    return y


def whoosh(dur=0.55, seed=1):
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    noise = rng.standard_normal(n).astype(np.float32)
    t = np.linspace(0, 1, n)
    # zamanla açılıp kapanan filtre → "vınn" hissi
    out, acc = np.empty(n, np.float32), 0.0
    for i in range(n):
        a = 0.02 + 0.35 * math.sin(math.pi * t[i]) ** 2
        acc += a * (noise[i] - acc)
        out[i] = acc
    env = np.sin(np.pi * t) ** 1.5
    return (out * env / (np.abs(out).max() + 1e-9) * 0.5).astype(np.float32)


def impact(dur=1.4):
    n = int(dur * SR)
    t = np.arange(n) / SR
    boom = np.sin(2 * np.pi * (55 + 30 * np.exp(-t * 8)) * t) * np.exp(-t * 3.5)
    shimmer = sum(np.sin(2 * np.pi * f * t) for f in (880, 1320, 1760)) / 3 * np.exp(-t * 2.2) * 0.12
    return ((boom * 0.7 + shimmer) * 0.6).astype(np.float32)


def ambient(dur=96.0, seed=7):
    """Sakin, belgesel tarzı müzik yatağı: yavaş akorlar + yumuşak arp + nefes gibi doku."""
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n, np.float32)
    chords = [(57, 60, 64, 69), (53, 57, 60, 65), (48, 52, 55, 60), (55, 59, 62, 67)]  # Am F C G
    bar = 6.0
    for i in range(int(dur / bar) + 1):
        notes = chords[i % 4]
        s, e = int(i * bar * SR), min(n, int((i + 1.25) * bar * SR))
        if s >= n:
            break
        tt = t[s:e] - i * bar
        env = np.minimum(tt / 2.0, 1) * np.exp(-np.maximum(tt - bar, 0) * 2)
        for m in notes:
            f = 440 * 2 ** ((m - 69) / 12)
            out[s:e] += (np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(2 * np.pi * f * 2.001 * tt)) * env * 0.08
        bass = 440 * 2 ** ((notes[0] - 12 - 69) / 12)
        out[s:e] += np.sin(2 * np.pi * bass * tt) * env * 0.10
        # yumuşak arp
        for k in range(8):
            ps = s + int(k * bar / 8 * SR)
            pe = min(n, ps + int(1.6 * SR))
            if ps >= n:
                break
            pt = t[ps:pe] - t[ps]
            f = 440 * 2 ** ((notes[k % 4] + 12 - 69) / 12)
            out[ps:pe] += np.sin(2 * np.pi * f * pt) * np.exp(-pt * 3) * 0.035
    air = _lowpass(rng.standard_normal(n).astype(np.float32), 0.02) * 0.05
    out = out + air
    # basit yankı
    for dly, g in ((0.23, 0.35), (0.41, 0.25), (0.67, 0.15)):
        k = int(dly * SR)
        out[k:] += out[:-k] * g
    # döngü noktasında tık olmasın
    fade = int(2 * SR)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return (out / (np.abs(out).max() + 1e-9) * 0.6).astype(np.float32)


# ------------------------------------------------------------------ intro
def make_intro(out, dur=2.6):
    frames = int(dur * FPS)
    bg = background()
    title_f, tag_f = font("InterDisplay-Black.otf", 150), font("InterDisplay-Medium.otf", 52)
    proc = subprocess.Popen(["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                             "-pix_fmt", "yuv420p", str(out) + ".v.mp4"],
                            stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    cache = {}
    for i in range(frames):
        t = i / FPS
        im = bg.copy()
        # parıltı
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gr = int(260 + 60 * ease(t / 0.8))
        gd.ellipse([560 - gr, 540 - gr, 560 + gr, 540 + gr], fill=(40, 140, 180, int(70 * ease(t / 0.6))))
        glow = glow.filter(ImageFilter.GaussianBlur(80))
        im.paste(glow, (0, 0), glow)
        # amblem: büyüyerek gelir, sonra kesit açılır
        sc = 0.72 + 0.28 * ease(t / 0.55)
        op = ease((t - 0.45) / 0.45)
        size = int(420 * sc)
        key = (size, round(op, 2))
        if key not in cache:
            cache[key] = emblem(size, op)
        e = cache[key].copy()
        e.putalpha(e.getchannel("A").point(lambda v: int(v * min(1, t / 0.25))))
        im.paste(e, (560 - size // 2, 540 - size // 2), e)
        # yazı: sağdan kayarak gelir
        d = ImageDraw.Draw(im)
        tt = ease((t - 0.7) / 0.5)
        if tt > 0:
            x = int(830 + 60 * (1 - tt))
            col = tuple(int(NAVY[k] + (WHITE[k] - NAVY[k]) * tt) for k in range(3))
            d.text((x, 395), "CUTAWAY", font=title_f, fill=col)
        tg = ease((t - 1.05) / 0.45)
        if tg > 0:
            col = tuple(int(NAVY[k] + (CYAN[k] - NAVY[k]) * tg) for k in range(3))
            d.text((838, 580), "How things actually work.", font=tag_f, fill=col)
        # son 0.25 sn: içeriğe yumuşak geçiş için hafif kararma
        if t > dur - 0.25:
            k = (t - (dur - 0.25)) / 0.25
            im = Image.blend(im, Image.new("RGB", (W, H), NAVY), k * 0.6)
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    proc.wait()
    # ses: whoosh + kesit açılırken vuruş
    n = int(dur * SR)
    a = np.zeros(n, np.float32)
    w = whoosh(0.6)
    a[:len(w)] += w * 0.8
    hit = impact()
    s = int(0.55 * SR)
    a[s:s + len(hit)] += hit[:max(0, n - s)]
    a[-int(0.3 * SR):] *= np.linspace(1, 0, int(0.3 * SR))
    write_wav(str(out) + ".a.wav", a)
    subprocess.run(["ffmpeg", "-y", "-i", str(out) + ".v.mp4", "-i", str(out) + ".a.wav", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)], check=True,
                   stderr=subprocess.DEVNULL)
    Path(str(out) + ".v.mp4").unlink()
    Path(str(out) + ".a.wav").unlink()
    return out


# ------------------------------------------------------------------ outro
def _rounded(d, box, r, **kw):
    d.rounded_rectangle(box, r, **kw)


def make_outro_card(out):
    """Sade, ortalanmış kapanış kartı (bitiş ekranı öğeleri API ile eklenemediği için boş kutu yok)."""
    im = background(grid_alpha=10)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W // 2 - 420, 120, W // 2 + 420, 760], fill=(40, 130, 170, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(110))
    im.paste(glow, (0, 0), glow)
    d = ImageDraw.Draw(im)
    e = emblem(330)
    im.paste(e, (W // 2 - 165, 110), e)

    def center(text, f, y, fill):
        w = d.textlength(text, font=f)
        d.text(((W - w) / 2, y), text, font=f, fill=fill)
    center("CUTAWAY", font("InterDisplay-Black.otf", 120), 470, WHITE)
    center("How things actually work.", font("InterDisplay-Medium.otf", 46), 615, CYAN)
    bf = font("InterDisplay-Bold.otf", 44)
    bw = d.textlength("SUBSCRIBE", font=bf) + 110
    _rounded(d, [(W - bw) / 2, 715, (W + bw) / 2, 800], 42, fill=(204, 32, 32))
    center("SUBSCRIBE", bf, 731, WHITE)
    center("New videos every Monday · Wednesday · Friday", font("InterDisplay-Medium.otf", 34), 845,
           (170, 185, 200))
    im.save(out, quality=95)
    return out


def build_all(force=False):
    A.mkdir(exist_ok=True)
    jobs = [("intro.mp4", make_intro), ("outro.png", make_outro_card)]
    for name, fn in jobs:
        if force or not (A / name).exists():
            fn(A / name)
            print("✓", name)
    if force or not (A / "whoosh.wav").exists():
        write_wav(A / "whoosh.wav", whoosh())
        print("✓ whoosh.wav")
    if force or not (A / "music.mp3").exists():
        write_wav(A / "music_tmp.wav", ambient())
        subprocess.run(["ffmpeg", "-y", "-i", str(A / "music_tmp.wav"), "-c:a", "libmp3lame", "-b:a", "160k",
                        str(A / "music.mp3")], check=True, stderr=subprocess.DEVNULL)
        (A / "music_tmp.wav").unlink()
        print("✓ music.mp3")


if __name__ == "__main__":
    import sys
    build_all(force="--force" in sys.argv)
