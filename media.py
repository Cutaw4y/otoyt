"""
Görsel ve render katmanı: stok video, AI diyagram, Ken Burns hareketi,
16:9 ana video, 9:16 Shorts, thumbnail yazı yerleşimi.
Bu dosyadaki fonksiyonlar API çağırmaz (ffmpeg + Pillow); selftest.py ile çevrimdışı test edilir.
"""
import json
import random
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
LANG = "en"              # pipeline kanal diline göre ayarlar
CHAPTER_LABEL = "CHAPTER"
# Altyazı katmanı renkleri (ASS biçimi &HAABBGGRR); kanal config'i "caption_theme" ile değiştirir
THEME = {"chap_num": "&H00F0E05E", "chap_box": "&H00201B0B", "callout_box": "&H003CA0FF",
         "callout_text": "&H00201B0B"}


def up(text):
    """Dile uygun BÜYÜK HARF (Türkçede i→İ, ı→I)."""
    if LANG == "tr":
        text = text.replace("i", "İ").replace("ı", "I")
    return text.upper()
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
FONTS_DIR = Path(__file__).parent / "assets" / "fonts"


# Farklı kaynaklardan gelen stok görüntüye kanalın tek tip "görünüşü":
# hafif kontrast, biraz düşük doygunluk, serin gölgeler, kenar gölgesi (sinematik, tutarlı)
LOOK = ("eq=contrast=1.06:saturation=0.90:gamma=0.98,"
        "colorbalance=bs=0.04:bm=0.01:bh=-0.03:rh=0.02,vignette=angle=PI/5")


def render_footage(clip, frames, out, start=0.0):
    """Stok klipten tam 'frames' karelik, ekranı dolduran (16:9) çekim."""
    sh(["ffmpeg", "-y", "-ss", f"{max(start, 0):.2f}", "-stream_loop", "-1", "-i", clip,
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},{LOOK},format=yuv420p",
        "-frames:v", int(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out])
    return out


def render_still(image, frames, out, move="in", focus=(0.5, 0.5)):
    """Durağan görsele hareket: in/out (yakınlaş/uzaklaş), left/right (kaydır), detail (bir bölgeye yakın plan)."""
    n = max(1, int(frames))
    fx, fy = focus
    if move == "detail":
        z = f"1.32+0.10*on/{n}"
        x = f"max(0,min(iw-iw/zoom,iw*{fx}-iw/zoom/2))"
        y = f"max(0,min(ih-ih/zoom,ih*{fy}-ih/zoom/2))"
    elif move == "out":
        z, x, y = f"1.12-0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif move == "left":
        z, x, y = "1.10", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"
    elif move == "right":
        z, x, y = "1.10", f"(iw-iw/zoom)*(on/{n})", "ih/2-(ih/zoom/2)"
    else:  # in
        z, x, y = f"1.0+0.10*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},format=yuv420p")
    sh(["ffmpeg", "-y", "-i", image, "-vf", vf, "-frames:v", n,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out])
    return out


def split_frames(total, target_sec=4.5, min_sec=2.5):
    """Bir sahneyi ~target_sec'lik çekimlere böler (kare sayısı olarak, toplam birebir korunur)."""
    n = max(1, round(total / FPS / target_sec))
    while n > 1 and total / n < min_sec * FPS:
        n -= 1
    base = total // n
    parts = [base] * n
    parts[-1] += total - base * n
    return parts


# ---------------------------------------------------------------- altyazı
def _ts(t):
    ms = max(0, int(round(t * 1000)))
    return f"{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}"


def srt_blocks(words, per_line=6, until=None, gap=0.6, max_chars=42):
    """Kelime zamanlarından altyazı blokları: [(başlangıç, bitiş, metin)].
    Blok; kelime sınırına ulaşınca, cümle bitince ya da konuşmada boşluk (intro vb.) olunca kapanır."""
    ws = [w for w in words if until is None or w["end"] <= until + 0.05]
    out, cur = [], []
    for i, w in enumerate(ws):
        cur.append(w)
        nxt = ws[i + 1] if i + 1 < len(ws) else None
        sentence_end = (w["word"].strip()[-1:] in ".!?" and len(cur) >= 3
                        and not re.fullmatch(r"\d+\.", w["word"].strip()))
        pause = nxt is not None and nxt["start"] - w["end"] > gap
        too_long = nxt is not None and len(" ".join(x["word"].strip() for x in cur + [nxt])) > max_chars
        full = len(cur) >= per_line and not (  # cümlenin son kelimesini tek başına alt satıra bırakma
            len(cur) == per_line and nxt is not None and nxt["word"].strip()[-1:] in ".!?" and not too_long)
        if full or sentence_end or pause or too_long or nxt is None:
            out.append((cur[0]["start"], cur[-1]["end"], " ".join(x["word"].strip() for x in cur)))
            cur = []
    return out


def write_srt_blocks(blocks, out):
    lines = [f"{n}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for n, (a, b, t) in enumerate(blocks, 1)]
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    return out


def read_srt_blocks(path_or_text):
    """SRT → [(başlangıç sn, bitiş sn, metin)]."""
    import re as _re
    txt = path_or_text if "\n" in str(path_or_text) else Path(path_or_text).read_text(encoding="utf-8-sig")
    def sec(s):
        h, mi, rest = s.strip().replace(".", ",").split(":")
        se, ms = rest.split(",")
        return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms) / 1000
    out = []
    for blk in _re.split(r"\n\s*\n", txt.replace("\r", "").strip()):
        ls = [l for l in blk.split("\n") if l.strip()]
        i = next((k for k, l in enumerate(ls) if "-->" in l), None)
        if i is None:
            continue
        a, b = ls[i].split("-->")
        out.append((sec(a), sec(b.split()[0]), " ".join(ls[i + 1:]).strip()))
    return out


def write_srt(words, out, per_line=6, until=None):
    """words: [{'word','start','end'}]"""
    return write_srt_blocks(srt_blocks(words, per_line, until), out)


def _ass_time(t):
    cs = max(0, int(round(t * 100)))
    return f"{cs//360000}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}"


def _ass_esc(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass_long(words, out, per_line=7, chapters=(), callouts=(), highlight=None):
    """Uzun video katmanları (hep aynı görsel kural):
    - Alt yazı: altta, beyaz, ince kontur
    - Bölüm kartı: sol üstte, 'BÖLÜM n' + başlık, kayarak gelir (3.2 sn)
    - Bilgi kartı: sağ üstte, turuncu çerçeveli kısa etiket (sayı/terim), 3 sn"""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Inter Display,58,&H00FFFFFF,&H00FFFFFF,&H00101010,&HA0000000,-1,0,0,0,100,100,0,0,1,3.5,2,2,160,160,56,1
Style: ChapNum,Inter Display,26,{THEME['chap_num']},&H00FFFFFF,&H80000000,&H00000000,-1,0,0,0,100,100,7,0,1,3,0,7,96,90,84,1
Style: ChapTitle,Caladea,66,&H00E8F0F4,&H00FFFFFF,&H70000000,&H00000000,-1,0,0,0,100,100,0.5,0,1,5,0,7,96,90,122,1
Style: Callout,Caladea,46,&H00E8F0F4,&H00FFFFFF,&H70000000,&H00000000,-1,0,0,0,100,100,0.5,0,1,5,0,9,90,100,86,1
Style: Accent,Inter Display,10,{THEME['chap_num']},&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    blocks = srt_blocks(words, per_line)
    k = 0  # blokların kelimeleri sırayla words listesinden gelir
    for n, (a, b, t) in enumerate(blocks):
        nxt = blocks[n + 1][0] if n + 1 < len(blocks) else None
        end = b + 0.15 if nxt is None else min(b + 0.15, nxt)  # iki satır asla aynı anda görünmez
        bw = words[k:k + len(t.split())]
        k += len(t.split())
        if not highlight or len(bw) != len(t.split()):
            ev.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(end)},Cap,,0,0,0,,{_ass_esc(t)}")
            continue
        # kelime kelime: o an söylenen kelime renkli, satır sesle birlikte ilerler
        for j, w in enumerate(bw):
            w0 = a if j == 0 else w["start"]
            w1 = bw[j + 1]["start"] if j + 1 < len(bw) else end
            if w1 - w0 < 0.02:
                continue
            parts = [("{\\c" + highlight + "}" + _ass_esc(x["word"].strip()) + "{\\r}") if i == j
                     else _ass_esc(x["word"].strip()) for i, x in enumerate(bw)]
            ev.append(f"Dialogue: 0,{_ass_time(w0)},{_ass_time(w1)},Cap,,0,0,0,,{' '.join(parts)}")
    # belgesel künyesi görünümü: kutu yok; zarif serif yazı, yumuşak gölge, ince renkli çizgi, yavaş beliriş
    for t, title, n in chapters:
        a, b = _ass_time(t + 0.2), _ass_time(t + 3.6)
        ev.append(f"Dialogue: 2,{a},{b},ChapNum,,0,0,0,,{{\\fad(400,400)\\blur6\\move(80,84,96,84)}}{CHAPTER_LABEL} {n}")
        ev.append(f"Dialogue: 2,{a},{b},Accent,,0,0,0,,{{\\fad(400,400)\\pos(96,118)\\p1}}m 0 0 l 70 0 70 3 0 3{{\\p0}}")
        ev.append(f"Dialogue: 2,{a},{b},ChapTitle,,0,0,0,,{{\\fad(450,400)\\blur8\\move(80,128,96,128)}}"
                  f"{_ass_esc(title)}")
    for t, text in callouts:
        a, b = _ass_time(t), _ass_time(t + 3.2)
        ev.append(f"Dialogue: 1,{a},{b},Callout,,0,0,0,,{{\\fad(350,350)\\blur8\\move({W - 84},86,{W - 100},86)}}"
                  f"{_ass_esc(text)}")
        ev.append(f"Dialogue: 1,{a},{b},Accent,,0,0,0,,{{\\fad(350,350)\\pos({W - 160},150)\\p1}}m 0 0 l 60 0 60 3 0 3{{\\p0}}")
    Path(out).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return out


def write_ass_short(words, out, per_chunk=3):
    """Shorts: büyük, ortada, konuşulan kelime sarı vurgulu (profesyonel Shorts stili)."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {SW}
PlayResY: {SH}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Big,Inter Display,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,7,3,2,70,70,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    chunks, cur = [], []
    for w in words:  # cümle sonunda yeni grup başlat
        cur.append(w)
        if len(cur) >= per_chunk or w["word"].strip()[-1:] in ".!?":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    for chunk in chunks:
        for j, w in enumerate(chunk):
            a = w["start"]
            b = chunk[j + 1]["start"] if j + 1 < len(chunk) else w["end"] + 0.1
            parts = [("{\\c&H00D7FF&\\fscx108\\fscy108}" + _ass_esc(up(x["word"].strip())) + "{\\r}")
                     if k == j else _ass_esc(up(x["word"].strip())) for k, x in enumerate(chunk)]
            ev.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(b)},Big,,0,0,0,,{' '.join(parts)}")
    Path(out).write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return out


def _ass_filter(ass):
    p = str(Path(ass).resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    fd = str(FONTS_DIR.resolve()).replace("\\", "/").replace(":", "\\:")
    return f"ass='{p}':fontsdir='{fd}'"


# ---------------------------------------------------------------- ses
def to_wav(src, out, sr=24000, frames=None):
    """Sesi mono WAV'a çevirir; 'frames' verilirse tam o video süresine kırpar/uzatır."""
    args = ["ffmpeg", "-y", "-i", src, "-ac", "1", "-ar", sr]
    if frames is not None:
        args += ["-af", f"apad=whole_dur={frames / FPS:.4f}", "-t", f"{frames / FPS:.4f}"]
    sh(args + [out])
    return out


# ---------------------------------------------------------------- final montaj
def mux_master(video, narration, out, ass=None, music=None, music_db=-21.0, fade_out=1.5, j_cut=0.0, sfx=None):
    """Görüntü + anlatım + (konuşurken kısılan) müzik + altyazı → yayın kalitesinde çıktı.
    j_cut: görüntü sesin bu kadar gerisinden gelir → yeni sahnenin sesi görüntü değişmeden önce başlar
    (kurgucuların J-cut geçişi; slayt gösterisi hissini kırar)."""
    total = duration(video)
    ins = ["-i", video, "-i", narration]
    vf = f"[0:v]tpad=start_duration={j_cut:.3f}:start_mode=clone," if j_cut > 0 else "[0:v]"
    vf += f"{_ass_filter(ass)}," if ass else ""
    vf += f"fade=t=out:st={max(total - fade_out, 0):.2f}:d={fade_out}[v]"
    sfx_chain, sfx_lbl = "", ""
    if music and Path(music).exists():
        ins += ["-stream_loop", "-1", "-i", music]
        if sfx and sfx[1]:  # bölüm geçişlerinde hafif "vınn" (dikkati tazeler)
            ins += ["-i", sfx[0]]
            n = len(sfx[1])
            sfx_chain = f"[3:a]aresample=48000,volume=0.35,asplit={n}" + "".join(f"[s{i}]" for i in range(n)) + ";"
            sfx_chain += "".join(f"[s{i}]adelay={int(max(t - 0.35, 0) * 1000)}|{int(max(t - 0.35, 0) * 1000)}[d{i}];"
                                 for i, t in enumerate(sfx[1]))
            sfx_chain += "".join(f"[d{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0[sfx];"
            sfx_lbl = "[sfx]"
        af = (f"[1:a]aresample=48000,asplit=2[nar][key];"
              f"[2:a]aresample=48000,volume={music_db}dB[mus];"
              f"[mus][key]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=450[duck];"
              + sfx_chain +
              f"[nar][duck]{sfx_lbl}amix=inputs={3 if sfx_lbl else 2}:duration=first:normalize=0,"
              f"loudnorm=I=-15:TP=-1.5:LRA=11,afade=t=out:st={max(total - fade_out, 0):.2f}:d={fade_out}[a]")
    else:
        af = (f"[1:a]aresample=48000,loudnorm=I=-15:TP=-1.5:LRA=11,"
              f"afade=t=out:st={max(total - fade_out, 0):.2f}:d={fade_out}[a]")
    sh(["ffmpeg", "-y", *ins, "-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
        "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "faster", "-crf", "21", "-maxrate", "6M", "-bufsize", "12M",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-map_metadata", "-1", "-movflags", "+faststart", out])
    return out


def make_short(parts, out, workdir, music=None, tag="short", max_len=58.0):
    """parts: [(sahne_videosu, sahne_sesi, sahne_kelimeleri)] → tam ekran 9:16 Short (≤58 sn).
    Intro/outro içermez; konuşulan kelimeyi vurgulayan büyük altyazı."""
    workdir = Path(workdir)
    vids, auds, words, t = [], [], [], 0.0
    for v, a, ws in parts:
        d = duration(a)
        if t + d > max_len and vids:
            break
        vids.append(v)
        auds.append(a)
        words += [{"word": w["word"], "start": w["start"] + t, "end": w["end"] + t} for w in ws]
        t += d
    total = min(t, max_len)
    vjoin = concat(vids, workdir / f"{tag}_v.mp4", workdir, f"{tag}_v")
    ajoin = workdir / f"{tag}_a.wav"
    lst = workdir / f"{tag}_a.txt"
    lst.write_text("".join(f"file '{Path(a).resolve()}'\n" for a in auds))
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", ajoin])
    ass = write_ass_short([w for w in words if w["end"] <= total + 0.05], workdir / f"{tag}.ass")
    vf = (f"[0:v]scale=-2:{SH},crop={SW}:{SH},eq=contrast=1.04:saturation=1.08,{_ass_filter(ass)},"
          f"fade=t=out:st={max(total - 0.4, 0):.2f}:d=0.4[v]")
    ins = ["-i", vjoin, "-i", ajoin]
    if music and Path(music).exists():
        ins += ["-stream_loop", "-1", "-i", music]
        af = ("[1:a]aresample=48000,asplit=2[nar][key];[2:a]aresample=48000,volume=-20dB[mus];"
              "[mus][key]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=400[duck];"
              "[nar][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5[a]")
    else:
        af = "[1:a]aresample=48000,loudnorm=I=-14:TP=-1.5[a]"
    sh(["ffmpeg", "-y", *ins, "-filter_complex", vf + ";" + af, "-map", "[v]", "-map", "[a]",
        "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "faster", "-crf", "21", "-maxrate", "6M", "-bufsize", "12M",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-map_metadata", "-1", "-movflags", "+faststart", out])
    return out, total


# ---------------------------------------------------------------- thumbnail
def _font(size):
    for f in (str(FONTS_DIR / "InterDisplay-Black.otf"), FONT_BOLD,
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
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


def _busy(im, box):
    """Bir bölgedeki ayrıntı yoğunluğu (kenar miktarı): yazı sakin tarafa konur."""
    g = im.crop(box).convert("L").resize((160, 180)).filter(ImageFilter.FIND_EDGES)
    return sum(g.getdata()) / (160 * 180)


def compose_thumbnail(image, text, out, accent=(255, 168, 60), logo=None, side=None):
    """Tıklatan küçük resim: canlı renk + keskinlik, konunun karşı tarafında 1-3 satır dev yazı
    (kalın kontur + gölge), o tarafta yumuşak karartma, kenar kararması, küçük kanal rozeti."""
    from PIL import ImageEnhance
    im = Image.open(image).convert("RGB")
    im = im.resize((1280, int(im.height * 1280 / im.width)))
    if im.height < 720:
        im = im.resize((int(im.width * 720 / im.height), 720))
    left, top = (im.width - 1280) // 2, (im.height - 720) // 2
    im = im.crop((left, top, left + 1280, top + 720))
    # canlılık: telefonda soluk görünmesin
    im = ImageEnhance.Contrast(im).enhance(1.18)
    im = ImageEnhance.Color(im).enhance(1.25)
    im = ImageEnhance.Sharpness(im).enhance(1.6)
    # yazı hangi tarafa: istenmediyse ayrıntısı az olan yarı
    if side not in ("left", "right"):
        side = "left" if _busy(im, (0, 0, 560, 720)) <= _busy(im, (720, 0, 1280, 720)) else "right"
    # yazı tarafına yumuşak karartma + genel kenar kararması
    shade = Image.new("L", (1280, 720), 0)
    sd = ImageDraw.Draw(shade)
    for x in range(1280):
        t = (1 - x / 760) if side == "left" else (x - 520) / 760
        sd.line([(x, 0), (x, 720)], fill=int(205 * max(0.0, min(1.0, t)) ** 1.3))
    vig = Image.new("L", (1280, 720), 0)
    ImageDraw.Draw(vig).ellipse([-260, -200, 1540, 920], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(140))
    shade = Image.composite(shade, Image.new("L", (1280, 720), 120), vig)
    im = Image.composite(Image.new("RGB", im.size, (0, 0, 0)), im, shade)

    d = ImageDraw.Draw(im)
    text = up(text).strip()
    box_w = 570
    size = 168
    while size >= 60:
        f = _font(size)
        lines = _wrap(d, text, f, box_w)
        if len(lines) <= 3 and all(d.textlength(l, font=f) <= box_w for l in lines):
            break
        size -= 6
    lh = int(size * 1.02)
    y0 = (720 - lh * len(lines)) // 2 + 40
    x0 = 56 if side == "left" else 1280 - 56 - max(d.textlength(l, font=f) for l in lines)
    # gölge katmanı (yazıyı zeminden koparır)
    sh_ = Image.new("L", (1280, 720), 0)
    sdr = ImageDraw.Draw(sh_)
    for i, line in enumerate(lines):
        sdr.text((x0 + 8, y0 + i * lh + 10), line, font=f, fill=230, stroke_width=max(6, size // 10), stroke_fill=230)
    sh_ = sh_.filter(ImageFilter.GaussianBlur(10))
    im = Image.composite(Image.new("RGB", im.size, (0, 0, 0)), im, sh_)
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        fill = accent if (len(lines) > 1 and i == len(lines) - 1) else (255, 255, 255)  # son satır vurgu rengi
        d.text((x0, y0 + i * lh), line, font=f, fill=fill, stroke_width=max(5, size // 13), stroke_fill=(0, 0, 0))
    if len(lines) == 1:  # tek satır: altına vurgu çizgisi
        w_ = d.textlength(lines[0], font=f)
        d.rectangle([x0, y0 + lh + 6, x0 + w_ * 0.55, y0 + lh + 20], fill=accent)
    if logo and Path(logo).exists():  # yazının karşı üst köşesinde küçük kanal rozeti
        lg = Image.open(logo).convert("RGB").resize((96, 96), Image.LANCZOS)
        mk = Image.new("L", (96, 96), 0)
        ImageDraw.Draw(mk).ellipse([0, 0, 95, 95], fill=255)
        ring = Image.new("L", (106, 106), 0)
        ImageDraw.Draw(ring).ellipse([0, 0, 105, 105], fill=255)
        bx = 1280 - 136 if side == "left" else 30
        im.paste((255, 255, 255), (bx, 28), ring)
        im.paste(lg, (bx + 5, 33), mk)
    im.save(out, quality=94)
    return out


def contact_sheet(images, out):
    """Hakem modele tek görsel: üstte adaylar (640x360), altta telefondaki gerçek boyutları (246x138)."""
    ims = [Image.open(i).convert("RGB") for i in images]
    sheet = Image.new("RGB", (640 * len(ims), 600), "white")
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate(ims):
        sheet.paste(im.resize((640, 360)), (640 * i, 40))
        sheet.paste(im.resize((246, 138)), (640 * i + 197, 430))
        d.text((640 * i + 10, 5), f"#{i+1}", font=_font(30), fill="black")
    d.text((10, 405), "phone size:", font=_font(22), fill="black")
    sheet.save(out, quality=85)
    return out




def playlist_cover(title, out, logo=None, accent=(255, 168, 60), size=1280, bg_color=(14, 17, 24)):
    """Oynatma listesi kapağı (kare). YouTube listede 16:9 kırpabildiği için her şey ortadaki 16:9 bantta:
    koyu zemin + vurgu rengi ışıltı, üstte logo, altında büyük liste adı."""
    band_top = (size - size * 9 // 16) // 2
    band_h = size * 9 // 16
    im = Image.new("RGB", (size, size), bg_color)
    glow = Image.new("RGB", (size, size), tuple(accent))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((size * 0.05, size * 0.15, size * 0.95, size * 0.85), fill=70)
    im = Image.composite(glow, im, mask.filter(ImageFilter.GaussianBlur(size // 6))).convert("RGBA")
    d = ImageDraw.Draw(im)
    y = band_top + int(band_h * 0.08)
    if logo and Path(logo).exists():
        lg = Image.open(logo).convert("RGBA")
        lw = int(band_h * 0.26)
        lg = lg.resize((lw, int(lg.height * lw / lg.width)))
        im.alpha_composite(lg, ((size - lw) // 2, y))
        y += lg.height + int(band_h * 0.05)
    bar_w = int(size * 0.14)
    d.rectangle(((size - bar_w) // 2, y, (size + bar_w) // 2, y + 10), fill=tuple(accent) + (255,))
    y += int(band_h * 0.06)
    text = up(title)
    room = band_top + band_h - int(band_h * 0.06) - y
    fs = int(size * 0.095)
    while fs > 36:
        f = _font(fs)
        lines = _wrap(d, text, f, size * 0.86)
        if int(fs * 1.12) * len(lines) <= room and max(d.textlength(l, font=f) for l in lines) <= size * 0.9:
            break
        fs -= 4
    lh = int(fs * 1.12)
    y += (room - lh * len(lines)) // 2
    for ln in lines:
        w = d.textlength(ln, font=f)
        d.text(((size - w) / 2, y), ln, font=f, fill=(255, 255, 255, 255),
               stroke_width=max(2, fs // 22), stroke_fill=(0, 0, 0, 255))
        y += lh
    im.convert("RGB").save(out, quality=92)
    return out


def title_card(text, out, seconds=3.8, accent=(232, 158, 68), logo=None):
    """Gölgedefter açılışı: karanlıkta mum ışığıyla defter açılır, gölge kalem günün konusunu el yazısıyla
    sağ sayfaya yazar; sol sayfada soluk kanal amblemi."""
    import numpy as np
    n = int(seconds * FPS)
    font_p = FONTS_DIR / "TeXGyreChorus.otf"
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:H, 0:W]
    glow = np.exp(-(((xx - W * 0.5) / (W * 0.42)) ** 2 + ((yy - H * 0.5) / (H * 0.5)) ** 2))
    bg = np.zeros((H, W, 3), np.float32)
    for c, (b, a) in enumerate(zip((10, 8, 6), accent)):
        bg[..., c] = b + glow * a * 0.25
    bg = Image.fromarray(bg.clip(0, 255).astype(np.uint8))
    pw, ph = int(W * 0.36), int(H * 0.70)
    cx, cy = W // 2, int(H * 0.52)
    # parşömen sayfa dokusu (sırta doğru kararan, kenarları yanık)
    py_, px_ = np.mgrid[0:ph, 0:pw]
    base = np.array([222, 204, 166], np.float32)
    shade = 0.78 + 0.22 * (px_ / pw) ** 0.5                     # sırta yakın koyu
    edge = np.minimum.reduce([px_, pw - 1 - px_, py_, ph - 1 - py_]).astype(np.float32)
    burn = np.clip(edge / 40, 0, 1) ** 0.6
    noise = rng.normal(0, 6, (ph, pw, 1))
    page = (base * (shade * (0.55 + 0.45 * burn))[..., None] + noise).clip(0, 255).astype(np.uint8)
    right_page = Image.fromarray(page)
    left_page = right_page.transpose(Image.FLIP_LEFT_RIGHT)
    if logo and Path(logo).exists():  # sol sayfada kanal logosu: yuvarlak rozet, ince kehribar halka
        d_ = int(pw * 0.62)
        lg = Image.open(logo).convert("RGB").resize((d_, d_))
        mask = Image.new("L", (d_, d_), 0)
        ImageDraw.Draw(mask).ellipse([4, 4, d_ - 4, d_ - 4], fill=255)
        lp = left_page.copy()
        ox, oy = (pw - d_) // 2, (ph - d_) // 2
        sh_ = Image.new("L", (d_ + 40, d_ + 40), 0)
        ImageDraw.Draw(sh_).ellipse([20, 28, d_ + 20, d_ + 28], fill=110)
        lp.paste((60, 42, 25), (ox - 20, oy - 20), sh_.filter(ImageFilter.GaussianBlur(14)))
        lp.paste(lg, (ox, oy), mask)
        ImageDraw.Draw(lp).ellipse([ox + 2, oy + 2, ox + d_ - 2, oy + d_ - 2], outline=tuple(accent), width=5)
        left_page = lp
    # yazı: sağ sayfaya 1-3 satır
    words, size = text.split(), 170
    while size > 54:
        f = ImageFont.truetype(str(font_p), size)
        lines, cur = [], ""
        for w_ in words:
            t = (cur + " " + w_).strip()
            if f.getlength(t) <= pw * 0.8 or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w_
        lines.append(cur)
        if len(lines) <= 3 and max(f.getlength(l) for l in lines) <= pw * 0.84:
            break
        size -= 8
    lh = int(size * 1.12)
    ink = Image.new("L", (W, H), 0)
    di = ImageDraw.Draw(ink)
    rx = cx + pw // 2
    ty = cy - lh * len(lines) // 2 - int(size * 0.12)
    spans = []
    for k, l in enumerate(lines):
        lw = f.getlength(l)
        x0 = rx - lw / 2
        di.text((x0, ty + k * lh), l, font=f, fill=255)
        spans.append((x0, x0 + lw, ty + k * lh))
    total = sum(b - a for a, b, _ in spans)
    pen = Image.new("RGBA", (120, 360), (0, 0, 0, 0))
    dp = ImageDraw.Draw(pen)
    dp.polygon([(60, 355), (42, 210), (24, 90), (56, 0), (88, 80), (80, 210)], fill=(8, 6, 5, 240))
    dp.line([(60, 355), (58, 25)], fill=(*accent, 170), width=3)
    pen = pen.rotate(-30, expand=True, resample=Image.BICUBIC)
    pen_sh = Image.new("RGBA", pen.size, (0, 0, 0, 0))
    pen_sh.putalpha(pen.getchannel("A").point(lambda v: int(v * 0.35)))
    proc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                             "-t", f"{n / FPS:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], stdin=subprocess.PIPE)
    t_open, t_write = int(n * 0.2), int(n * 0.55)
    for i in range(n):
        im = bg.copy()
        o = min(1.0, i / max(1, t_open))
        o = 1 - (1 - o) ** 3
        half = max(8, int(pw * o))
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([cx - half + 18, cy - ph // 2 + 24, cx + half + 18, cy + ph // 2 + 24],
                                             16, fill=(0, 0, 0, 150))
        im.paste(sh, (0, 0), sh)
        im.paste(left_page.resize((half, ph)), (cx - half, cy - ph // 2))
        im.paste(right_page.resize((half, ph)), (cx, cy - ph // 2))
        ImageDraw.Draw(im).rectangle([cx - 2, cy - ph // 2, cx + 2, cy + ph // 2], fill=(90, 70, 46))
        if i >= t_open:
            prog = min(1.0, (i - t_open) / max(1, t_write))
            left = prog * total
            mask = Image.new("L", (W, H), 0)
            dm = ImageDraw.Draw(mask)
            px, py = None, None
            for a, b, y in spans:
                seg = min(b - a, max(0.0, left))
                if seg > 0:
                    dm.rectangle([a - 25, y - 20, a + seg, y + lh], fill=255)
                    px, py = a + seg, y + lh * 0.6
                left -= (b - a)
            im.paste((40, 25, 14), (0, 0), Image.composite(ink, Image.new("L", (W, H), 0), mask))
            if px is not None and prog < 1.0:
                pos = (int(px) - 30, int(py) - pen.height + 30)
                im.paste(pen_sh, (pos[0] + 14, pos[1] + 18), pen_sh)
                im.paste(pen, pos, pen)
        fade = min(1.0, i / 10) * min(1.0, (n - 1 - i) / 8 + 0.2)
        arr = (np.asarray(im, np.float32) * fade).clip(0, 255).astype(np.uint8)
        proc.stdin.write(arr.tobytes())
    proc.stdin.close()
    proc.wait()
    return out


def host_shot(image, frames, out, seed=0):
    """Anlatıcı (Gölge) çekimi: yavaş yakınlaşma + mum ışığı titremesi + süzülen sis. Gerçekçi görsel canlanır."""
    import numpy as np
    from PIL import ImageFilter as _F
    rng = np.random.default_rng(seed)
    src = Image.open(image).convert("RGB")
    src = src.resize((int(W * 1.12), int(H * 1.12)), Image.LANCZOS)
    base = np.asarray(src, np.float32)
    # sıcak ışık maskesi (görüntünün parlak-turuncu bölgeleri: mum alevi ve çevresi)
    r, g, b = base[..., 0], base[..., 1], base[..., 2]
    warm = np.clip((r - b) / 120.0, 0, 1) * np.clip((r + g) / 400.0, 0, 1)
    warm = np.asarray(Image.fromarray((warm * 255).astype(np.uint8)).filter(_F.GaussianBlur(25)), np.float32) / 255
    fog = np.asarray(Image.fromarray((rng.random((90, 160)) * 255).astype(np.uint8))
                     .resize((int(W * 1.8), int(H * 1.12) + 2), Image.BICUBIC).filter(_F.GaussianBlur(40)), np.float32) / 255
    n = max(1, int(frames))
    flick = np.cumsum(rng.normal(0, 0.035, n))
    flick = 1 + 0.07 * np.tanh(flick - np.convolve(flick, np.ones(9) / 9, mode="same"))
    proc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                             "-pix_fmt", "yuv420p", "-r", str(FPS), str(out)], stdin=subprocess.PIPE)
    bh, bw = base.shape[:2]
    for i in range(n):
        t = i / max(1, n - 1)
        z = 1.0 + 0.08 * t                                  # yavaş yakınlaşma (figüre doğru)
        cw, ch = int(bw / z), int(bh / z)
        x0 = int((bw - cw) * (0.62 + 0.05 * t))
        y0 = int((bh - ch) * 0.45)
        img = base * (1 + (flick[i] - 1) * warm[..., None])
        fx = int((fog.shape[1] - bw) * t)
        img = img + fog[:bh, fx:fx + bw, None] * 26 * np.array([0.8, 0.85, 1.0])
        crop = Image.fromarray(img.clip(0, 255).astype(np.uint8)).crop((x0, y0, x0 + cw, y0 + ch)).resize((W, H))
        proc.stdin.write(np.asarray(crop).tobytes())
    proc.stdin.close()
    proc.wait()
    return out


# Defter çekimini açılıştaki taş oda ışığına (soğuk ay ışığı gölgeler + sıcak mum) uydurur
PAGE_GRADE = ("colorbalance=rs=-0.06:gs=-0.02:bs=0.12:rm=-0.02:bm=0.03,"
              "eq=saturation=0.82:contrast=1.08:brightness=-0.04,vignette=angle=PI/3.6")


def opening(a, b, out, xf=0.9, tail=0.6):
    """İki açılış klibini yumuşak geçişle (çözülme + ses çapraz geçişi) tek klip yapar; sonunda karaya iner."""
    da, db = duration(a), duration(b)
    tot = da + db - xf
    fc = (f"[0:v]scale={W}:{H},fps={FPS},format=yuv420p,setpts=PTS-STARTPTS[v0];"
          f"[1:v]scale={W}:{H},fps={FPS},format=yuv420p,setpts=PTS-STARTPTS[v1];"
          f"[v0][v1]xfade=transition=fade:duration={xf}:offset={da - xf:.3f},"
          f"fade=t=out:st={tot - tail:.3f}:d={tail}[v];"
          f"[0:a]aresample=48000,aformat=channel_layouts=stereo[a0];[1:a]aresample=48000,aformat=channel_layouts=stereo[a1];"
          f"[a0][a1]acrossfade=d={xf},afade=t=out:st={tot - tail:.3f}:d={tail}[a]")
    sh(["ffmpeg", "-y", "-v", "error", "-i", a, "-i", b, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", out])
    return out


def page_title(clip, text, out, seconds=4.0, start=1.5, box=(965, 110, 1566, 847), logo=None, left_box=(452, 110, 950, 847), reveal=(0.9, 2.3)):
    """Gerçek (Veo) defter sayfası klibinin üstüne günün konusu mürekkeple yazılmış olarak; sol sayfada soluk kanal
    mührü. Yavaş yakınlaşma eklenir. Ses klipten (mum çıtırtısı)."""
    import numpy as np
    n = int(seconds * FPS)
    font_p = FONTS_DIR / "TeXGyreChorus.otf"
    x0, y0, x1, y1 = box
    pw, ph = x1 - x0, y1 - y0
    words, size = text.split(), 150
    while size > 50:
        f = ImageFont.truetype(str(font_p), size)
        lines, cur = [], ""
        for w_ in words:
            t = (cur + " " + w_).strip()
            if f.getlength(t) <= pw * 0.8 or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w_
        lines.append(cur)
        if len(lines) <= 3 and max(f.getlength(l) for l in lines) <= pw * 0.84:
            break
        size -= 6
    ink = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(ink)
    lh = int(size * 1.15)
    ty = (y0 + y1) // 2 - lh * len(lines) // 2 - int(size * 0.1)
    for k, l in enumerate(lines):
        d.text(((x0 + x1) / 2 - f.getlength(l) / 2, ty + k * lh), l, font=f, fill=235)
    # el yazısı hissi: hafif bulanık, kenarı yumuşak mürekkep
    ink = ink.filter(ImageFilter.GaussianBlur(0.8))
    stamp = None
    if logo and Path(logo).exists():  # sol sayfada kanal amblemi: sayfaya basılmış mürekkep baskı gibi
        lx0, ly0, lx1, ly1 = left_box
        dd = int(min(lx1 - lx0, ly1 - ly0) * 0.62)
        lg = np.asarray(Image.open(logo).convert("L").resize((dd, dd)), np.float32) / 255.0
        mk = Image.new("L", (dd, dd), 0)
        ImageDraw.Draw(mk).ellipse([3, 3, dd - 3, dd - 3], fill=255)
        mk = np.asarray(mk.filter(ImageFilter.GaussianBlur(3)), np.float32) / 255.0
        stamp = np.zeros((H, W, 1), np.float32)
        px, py = (lx0 + lx1) // 2 - dd // 2, (ly0 + ly1) // 2 - dd // 2
        # koyu zemin → koyu mürekkep, açık çizgiler → kâğıt görünür (baskı/negatif etkisi)
        stamp[py:py + dd, px:px + dd, 0] = (1 - lg) * mk * 0.55
    inkA = np.asarray(ink, np.float32)[..., None] / 255.0
    stA = stamp
    ink_col = np.array([38, 24, 14], np.float32)
    seal_col = np.array([46, 30, 18], np.float32)
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{start}", "-i", str(clip), "-t", f"{seconds}",
                            "-vf", f"scale={W}:{H},fps={FPS}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-ss", f"{start}", "-t", f"{seconds}", "-i", str(clip),
                            "-map", "0:v", "-map", "1:a?", "-vf", PAGE_GRADE, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], stdin=subprocess.PIPE)
    sz = W * H * 3
    xs = np.arange(W, dtype=np.float32)
    for i in range(n):
        buf = dec.stdout.read(sz)
        if len(buf) < sz:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(H, W, 3).astype(np.float32)
        # geçiş bitince mürekkep soldan sağa "yazılır", mühür yavaşça belirir
        t = i / FPS
        pr = min(1.0, max(0.0, (t - reveal[0]) / (reveal[1] - reveal[0])))
        edge = x0 + (pw + 120) * pr
        wipe = np.clip((edge - xs) / 120.0, 0, 1)[None, :, None]
        a_ = inkA * wipe * 0.92
        fr = fr * (1 - a_) + ink_col * a_
        if stA is not None:
            sa = stA * pr
            fr = fr * (1 - sa) + seal_col * sa
        z = 1.0 + 0.05 * i / max(1, n - 1)
        im = Image.fromarray(fr.clip(0, 255).astype(np.uint8))
        cw, ch = int(W / z), int(H / z)
        cx0 = int(((x0 + x1) / 2) - cw / 2 * 1.0 - ((x0 + x1) / 2 - W / 2) * (1 - (z - 1) * 4))
        cx0 = max(0, min(W - cw, int(W / 2 - cw / 2 + (z - 1) * 6 * ((x0 + x1) / 2 - W / 2))))
        cy0 = (H - ch) // 2
        enc.stdin.write(np.asarray(im.crop((cx0, cy0, cx0 + cw, cy0 + ch)).resize((W, H))).tobytes())
    enc.stdin.close()
    enc.wait()
    dec.stdout.close()
    dec.wait()
    return out
