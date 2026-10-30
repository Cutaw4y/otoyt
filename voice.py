"""
Ücretsiz seslendirme: Kokoro-82M (açık kaynak, Apache-2.0), GitHub sunucusunun CPU'sunda çalışır.
Cümle cümle seslendirir; böylece altyazı zamanlaması da ayrı bir servise gerek kalmadan çıkar.
"""
import re
import wave
from pathlib import Path

import numpy as np

SR = 24000
_pipe = {}


def _pipeline(lang):
    if lang not in _pipe:
        from kokoro import KPipeline
        _pipe[lang] = KPipeline(lang_code=lang)
    return _pipe[lang]


def split_sentences(text):
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p.strip()]
    out = []
    for p in parts:  # Türkçe sıra sayıları ("3. gece", "19. yüzyıl") cümle sonu değildir
        if out and re.search(r"(^|\s)\d+\.$", out[-1]) and p[:1].islower():
            out[-1] += " " + p
        else:
            out.append(p)
    return out


def _to_np(audio):
    if audio is None:
        return None
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return np.asarray(audio, dtype=np.float32).reshape(-1)


def synth_sentence(text, voice, lang, speed):
    chunks = []
    for item in _pipeline(lang)(text, voice=voice, speed=speed, split_pattern=None):
        audio = item[2] if isinstance(item, tuple) else getattr(item, "audio", None)
        a = _to_np(audio)
        if a is not None and a.size:
            chunks.append(a)
    if not chunks:
        raise RuntimeError(f"Kokoro ses üretemedi: {text[:80]}")
    return np.concatenate(chunks)


def synth_edge(text, voice, lang=None, speed=1.0):
    """Ücretsiz yedek ses (Microsoft Edge sinir ağı sesleri, anahtar gerektirmez). Gemini ses hakkı
    bittiğinde Türkçe kanal için kullanılır. lang kullanılmaz; speed → konuşma hızı yüzdesi."""
    import asyncio, subprocess, tempfile
    import edge_tts
    rate = f"{int(round((speed - 1.0) * 100)):+d}%"
    mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
    for attempt in range(3):
        try:
            asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(mp3)))
            if mp3.stat().st_size > 1000:
                break
        except Exception:
            if attempt == 2:
                raise
            import time
            time.sleep(5 * (attempt + 1))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp3), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, check=True).stdout
    mp3.unlink(missing_ok=True)
    return np.frombuffer(raw, dtype=np.float32).copy()


def _trim(a, thr=0.01, pad=0.04):
    """Cümle başı/sonundaki sessizliği kırpar (daha akıcı anlatım, daha doğru altyazı)."""
    idx = np.where(np.abs(a) > thr)[0]
    if not idx.size:
        return a
    p = int(pad * SR)
    return a[max(idx[0] - p, 0): min(idx[-1] + p, a.size)]


def word_timings(sentence, start, dur):
    """Cümle içindeki kelimelere süreyi harf sayısına göre dağıtır (cümle ~3 sn → hata payı çok küçük)."""
    words = sentence.split()
    weights = [len(w) + 1.5 for w in words]
    total, t, out = sum(weights), start, []
    for w, wt in zip(words, weights):
        d = dur * wt / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    return out


def write_wav(a, path):
    pcm = (np.clip(a, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def narrate(scenes, workdir, voice="af_heart", lang="a", speed=1.0,
            sentence_gap=0.28, scene_gap=0.45, synth=None):
    """Her sahne için wav üretir. Döner: (sahne_dosyaları, sahne_süreleri, kelime_zamanları)."""
    synth = synth or synth_sentence
    workdir = Path(workdir)
    files, durs, words, offset = [], [], [], 0.0
    for i, sc in enumerate(scenes):
        pieces, t = [], 0.0
        sents = split_sentences(sc["narration"])
        for j, s in enumerate(sents):
            a = _trim(synth(s, voice, lang, speed))
            d = a.size / SR
            words += word_timings(s, offset + t, d)
            pieces.append(a)
            t += d
            gap = scene_gap if j == len(sents) - 1 else sentence_gap
            pieces.append(np.zeros(int(gap * SR), dtype=np.float32))
            t += gap
        f = write_wav(np.concatenate(pieces), workdir / f"vo_{i:02d}.wav")
        dur = sum(p.size for p in pieces) / SR
        files.append(f)
        durs.append(dur)
        offset += dur
    return files, durs, words


# ---------------------------------------------------------------- Gemini (Türkçe dahil) seslendirme
def sentence_bounds(a, n, sr=SR, min_gap=0.16, thr=0.012):
    """Sahne sesinde n cümlenin sınırlarını, cümle arası sessizliklerden bulur.
    Dönüş: [(başlangıç_sn, bitiş_sn)] × n. Sessizlik yetmezse metin uzunluğuna göre bölmeye bırakır (None)."""
    if n <= 1:
        return [(0.0, a.size / sr)]
    win = int(0.01 * sr)
    k = a.size // win
    if k < 5:
        return None
    energy = np.sqrt((a[:k * win].reshape(k, win) ** 2).mean(axis=1))
    silent = energy < thr
    gaps, i = [], 0
    while i < k:
        if silent[i]:
            j = i
            while j < k and silent[j]:
                j += 1
            if (j - i) * 0.01 >= min_gap and i > 0 and j < k:
                gaps.append(((j - i), (i + j) / 2 * 0.01))
            i = j
        else:
            i += 1
    if len(gaps) < n - 1:
        return None
    cuts = sorted(c for _, c in sorted(gaps, reverse=True)[:n - 1])
    edges = [0.0] + cuts + [a.size / sr]
    return [(edges[i], edges[i + 1]) for i in range(n)]


def _pad_sentences(a, bounds, min_gap, sr=SR):
    """Cümle aralarındaki sessizliği en az min_gap saniyeye tamamlar (belgesel ağırlığı)."""
    if not bounds or len(bounds) < 2 or min_gap <= 0:
        return a, bounds
    pieces, new, shift, last = [], [], 0.0, 0
    for k, (b0, b1) in enumerate(bounds):
        if k < len(bounds) - 1:
            cut = int(b1 * sr)
            seg = a[last:cut]
            # cut noktası sessizliğin ortasında: mevcut sessizliği ölç
            hop = int(0.01 * sr)
            quiet, j = 0, cut
            while j - hop >= 0 and np.sqrt((a[j - hop:j] ** 2).mean()) < 0.012:
                j -= hop
                quiet += hop
            j = cut
            while j + hop <= a.size and np.sqrt((a[j:j + hop] ** 2).mean()) < 0.012:
                j += hop
                quiet += hop
            quiet /= sr
            extra = max(0.0, min_gap - quiet)
            pieces += [seg, np.zeros(int(extra * sr), dtype=np.float32)]
            new.append((b0 + shift, b1 + shift))
            shift += extra
            last = cut
        else:
            pieces.append(a[last:])
            new.append((b0 + shift, b1 + shift))
    return np.concatenate(pieces), new


def _groups(scenes, per_request, max_chars):
    g, cur, n = [], [], 0
    for i, sc in enumerate(scenes):
        L = len(sc["narration"])
        if cur and (len(cur) >= per_request or n + L > max_chars):
            g.append(cur)
            cur, n = [], 0
        cur.append(i)
        n += L
    if cur:
        g.append(cur)
    return g


def narrate_gemini(scenes, workdir, voice="Charon", style=None, models=None, scene_gap=0.45, sentence_gap=0.0,
                   scenes_per_request=6, max_chars=1800):
    """Birkaç sahneyi tek istekte seslendirir (ücretsiz katmanın günlük istek sınırına sığmak ve sesin
    sahneler arasında doğal akması için), cümle ve sahne sınırlarını sessizliklerden bulur."""
    import ai
    workdir = Path(workdir)
    per_scene = {}
    for grp in _groups(scenes, max(1, scenes_per_request), max_chars):
        sents = [split_sentences(scenes[i]["narration"]) for i in grp]
        flat = [x for ss in sents for x in ss]
        a = _trim(ai.gemini_tts(" ".join(scenes[i]["narration"] for i in grp), voice=voice, style=style,
                                models=models or ("gemini-3.1-flash-tts-preview",)))
        bounds = sentence_bounds(a, len(flat))
        if bounds is None:  # sessizlik bulunamazsa metin uzunluğuna göre orantıla
            lens = [len(x) + 4 for x in flat]
            tot, t, bounds = sum(lens), 0.0, []
            d = a.size / SR
            for L in lens:
                bounds.append((t, t + d * L / tot))
                t += d * L / tot
        k = 0
        for i, ss in zip(grp, sents):
            b = bounds[k:k + len(ss)]
            k += len(ss)
            s0, s1 = b[0][0], b[-1][1]
            seg = a[int(s0 * SR):int(s1 * SR)]
            local = [(x0 - s0, x1 - s0) for x0, x1 in b]
            seg, local = _pad_sentences(seg, local, sentence_gap)
            per_scene[i] = (seg, ss, local)
    files, durs, words, offset = [], [], [], 0.0
    for i in range(len(scenes)):
        seg, ss, local = per_scene[i]
        for x, (b0, b1) in zip(ss, local):
            words += word_timings(x, offset + b0, max(b1 - b0, 0.2))
        full = np.concatenate([seg, np.zeros(int(scene_gap * SR), dtype=np.float32)])
        f = write_wav(full, workdir / f"vo_{i:02d}.wav")
        dur = full.size / SR
        files.append(f)
        durs.append(dur)
        offset += dur
    return files, durs, words
