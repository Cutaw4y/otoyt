"""
Görsel ve render katmanı: stok video, AI diyagram, Ken Burns hareketi,
16:9 ana video, 9:16 Shorts, thumbnail yazı yerleşimi.
Bu dosyadaki fonksiyonlar API çağırmaz (ffmpeg + Pillow); selftest.py ile çevrimdışı test edilir.
"""
import json
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
SW, SH = 1080, 1920  # Shorts
FONT_BOLD = "DejaVuSans-Bold.ttf"


# ---------------------------------------------------------------- temel
def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Komut hatası: {' '.join(map(str, cmd[:6]))}...\n{r.stderr[-2000:]}")
    return r.stdout


def duration(path):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", path]).strip())


def concat(files, out, workdir, name):
    lst = Path(workdir) / f"{name}.txt"
    lst.write_text("".join(f"file '{Path(f).resolve()}'\n" for f in files))
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    return out


# ---------------------------------------------------------------- tekrar engeli
class AssetLedger:
    """Kanal genelinde kullanılan stok video ID'lerini tutar (aynı görüntü tekrar etmesin)."""

    def __init__(self, path, cap=8000):
        self.path, self.cap = Path(path), cap
        self.ids = json.loads(self.path.read_text()) if self.path.exists() else []
        self._set = set(self.ids)
        self.session = set()

    def used(self, i):
        return i in self._set or i in self.session

    def add(self, i):
        self.session.add(i)

    def save(self):
        self.ids = (self.ids + sorted(self.session - self._set, key=str))[-self.cap:]
        self.path.write_text(json.dumps(self.ids))


# ---------------------------------------------------------------- segment render
def render_footage(clip, dur, out):
    sh(["ffmpeg", "-y", "-stream_loop", "-1", "-i", clip, "-t", f"{dur:.3f}",
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", out])
    return out


def render_still(image, dur, out, move=None):
    """Durağan görsele yavaş yakınlaşma / kaydırma (Ken Burns) uygular."""
    frames = max(1, round(dur * FPS))
    move = move or random.choice(["in", "out", "left", "right"])
    z_in = "min(1+0.10*on/{n},1.10)".format(n=frames)
    z_out = "max(1.10-0.10*on/{n},1.0)".format(n=frames)
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if move == "in":
        z, x, y = z_in, cx, cy
    elif move == "out":
        z, x, y = z_out, cx, cy
    elif move == "left":
        z, x, y = "1.08", f"(iw-iw/zoom)*(1-on/{frames})", cy
    else:
        z, x, y = "1.08", f"(iw-iw/zoom)*(on/{frames})", cy
    vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p")
    sh(["ffmpeg", "-y", "-i", image, "-vf", vf, "-frames:v", frames,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", out])
    return out


# ---------------------------------------------------------------- altyazı
def _ts(t):
    ms = max(0, int(round(t * 1000)))
    return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"


def write_srt(words, out, per_line=6, until=None):
    """words: [{'word','start','end'}]"""
    ws = [w for w in words if until is None or w["end"] <= until + 0.05]
    blocks = []
    for n, i in enumerate(range(0, len(ws), per_line), 1):
        c = ws[i:i + per_line]
        text = " ".join(w["word"].strip() for w in c)
        blocks.append(f"{n}\n{_ts(c[0]['start'])} --> {_ts(c[-1]['end'])}\n{text}\n")
    Path(out).write_text("\n".join(blocks), encoding="utf-8")
    return out


def _sub_filter(srt, style):
    p = str(Path(srt).resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"subtitles='{p}':force_style='{style}'"


# ---------------------------------------------------------------- final montaj
def mux_final(video, voice, srt, out, music=None, music_volume=0.07):
    style = "FontName=DejaVu Sans,FontSize=18,Bold=1,Outline=2,Shadow=0,MarginV=40"
    ins = ["-i", video, "-i", voice]
    if music and Path(music).exists():
        ins += ["-stream_loop", "-1", "-i", music]
        fc = (f"[0:v]{_sub_filter(srt, style)}[v];"
              f"[2:a]volume={music_volume}[m];[1:a][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        fc = f"[0:v]{_sub_filter(srt, style)}[v];[1:a]anull[a]"
    sh(["ffmpeg", "-y", *ins, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", out])
    return out


def make_short(segments, scene_durs, voice, words, out, workdir, max_len=58.0, music=None):
    """Baştaki (hook) sahnelerden ≤58 sn dikey Short üretir. Ek API maliyeti yok."""
    workdir = Path(workdir)
    take, total = [], 0.0
    for seg, d in zip(segments, scene_durs):
        if total + d > max_len and take:
            break
        take.append(seg)
        total += d
    total = min(total, max_len)
    joined = concat(take, workdir / "short_join.mp4", workdir, "short_segs")
    vcut = workdir / "short_voice.m4a"
    sh(["ffmpeg", "-y", "-i", voice, "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "192k", vcut])
    srt = write_srt(words, workdir / "short.srt", per_line=3, until=total)
    style = "FontName=DejaVu Sans,FontSize=16,Bold=1,Outline=3,Shadow=0,Alignment=2,MarginV=70"
    fc = (f"[0:v]split[a][b];"
          f"[a]scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},boxblur=25:2,eq=brightness=-0.15[bg];"
          f"[b]scale={SW}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-120,{_sub_filter(srt, style)}[v]")
    sh(["ffmpeg", "-y", "-i", joined, "-i", vcut, "-filter_complex", fc,
        "-map", "[v]", "-map", "1:a", "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium",
        "-crf", "21", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    return out, total


# ---------------------------------------------------------------- thumbnail
def _font(size):
    for f in (FONT_BOLD, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def compose_thumbnail(image, text, out, accent=(255, 214, 0)):
    """Yazıyı otomatik sığdırır (en fazla 2 satır), okunabilirlik için alt gölge ekler."""
    im = Image.open(image).convert("RGB")
    im = im.resize((1280, int(im.height * 1280 / im.width)))
    if im.height < 720:
        im = im.resize((int(im.width * 720 / im.height), 720))
    left, top = (im.width - 1280) // 2, (im.height - 720) // 2
    im = im.crop((left, top, left + 1280, top + 720))

    shade = Image.new("L", (1280, 720), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(300, 720):
        sd.line([(0, y), (1280, y)], fill=int(190 * (y - 300) / 420))
    im = Image.composite(Image.new("RGB", im.size, (0, 0, 0)), im, shade)

    d = ImageDraw.Draw(im)
    text = text.upper().strip()
    size = 130
    while size >= 56:
        f = _font(size)
        lines = _wrap(d, text, f, 1160)
        if len(lines) <= 2 and all(d.textlength(l, font=f) <= 1160 for l in lines):
            break
        size -= 6
    lh = int(size * 1.08)
    y = 720 - 50 - lh * len(lines)
    for i, line in enumerate(lines):
        fill = accent if i == len(lines) - 1 else (255, 255, 255)
        d.text((60, y + i * lh), line, font=f, fill=fill, stroke_width=max(4, size // 14), stroke_fill="black")
    im.save(out, quality=90)
    return out


def contact_sheet(images, out):
    """Hakem modele tek görsel olarak göstermek için adayları yan yana dizer."""
    ims = [Image.open(i).convert("RGB").resize((640, 360)) for i in images]
    sheet = Image.new("RGB", (640 * len(ims), 400), "white")
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate(ims):
        sheet.paste(im, (640 * i, 40))
        d.text((640 * i + 10, 5), f"#{i+1}", font=_font(30), fill="black")
    sheet.save(out, quality=85)
    return out
