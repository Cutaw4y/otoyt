"""
Ücretsiz seslendirme: Kokoro-82M (açık kaynak, Apache-2.0), GitHub sunucusunun CPU'sunda çalışır.
Cümle cümle seslendirir; böylece altyazı zamanlaması da ayrı bir servise gerek kalmadan çıkar.
"""
import difflib
import os
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
            sentence_gap=0.28, scene_gap=0.45, synth=None, align_lang=None):
    """Her sahne için wav üretir. Döner: (sahne_dosyaları, sahne_süreleri, kelime_zamanları)."""
    synth = synth or synth_sentence
    workdir = Path(workdir)
    files, durs, words, offset = [], [], [], 0.0
    for i, sc in enumerate(scenes):
        pieces, t, sw = [], 0.0, []
        sents = split_sentences(sc["narration"])
        for j, s in enumerate(sents):
            a = _trim(synth(s, voice, lang, speed))
            d = a.size / SR
            sw.append((s, t, d))
            pieces.append(a)
            t += d
            gap = scene_gap if j == len(sents) - 1 else sentence_gap
            pieces.append(np.zeros(int(gap * SR), dtype=np.float32))
            t += gap
        audio = np.concatenate(pieces)
        al = align_sentences(audio, [x[0] for x in sw], align_lang) if align_lang and sw else None
        ALIGN_STATS["total"] += len(sw)
        ALIGN_STATS["ok"] += len(sw) if al else 0
        for k, (s, st, d) in enumerate(sw):  # gerçek kelime zamanları; olmazsa cümle içi tahmin
            if al and len(al[k]) == len(s.split()):
                words += [{"word": w, "start": round(offset + w0, 3), "end": round(offset + w1, 3)}
                          for w, (w0, w1) in zip(s.split(), al[k])]
            else:
                words += word_timings(s, offset + st, d)
        f = write_wav(audio, workdir / f"vo_{i:02d}.wav")
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


# ---------------------------------------------------------------- gerçek kelime zamanları (hizalama)
_WHISPER = None
ALIGN_STATS = {"ok": 0, "total": 0}   # bildirim için: kaç cümlede kelime zamanı sesten ölçüldü


def _whisper():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel(os.getenv("ALIGN_MODEL", "small"), device="cpu", compute_type="int8")
    return _WHISPER


def _tok(w):
    w = w.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^0-9a-zçğıöşüâîû]", "", w)


def _fill_gaps(times, total):
    """Eşleşmeyen kelimelere komşularının arasındaki süreyi eşit dağıtır; zamanları sıraya sokar."""
    n, i = len(times), 0
    while i < n:
        if times[i] is None:
            j = i
            while j < n and times[j] is None:
                j += 1
            t0 = times[i - 1][1] if i > 0 else 0.0
            t1 = times[j][0] if j < n else total
            t1 = max(t1, t0 + 0.12 * (j - i))
            step = (t1 - t0) / (j - i)
            for k in range(i, j):
                times[k] = (t0 + step * (k - i), t0 + step * (k - i + 1))
            i = j
        else:
            i += 1
    out, last = [], 0.0
    for a, b in times:
        a = max(a, last)
        b = max(b, a + 0.05)
        out.append((a, b))
        last = a
    return out


def align_from_hyp(sentences, hyp, total):
    """Senaryodaki kelimeleri, konuşma tanımanın bulduğu kelimelerle eşler.
    hyp: [(kelime, başlangıç, bitiş)]. Dönüş: cümle başına [(başlangıç, bitiş)] listesi, ya da güvenilmezse None."""
    sw = [w for s in sentences for w in s.split()]
    st = [_tok(w) for w in sw]
    hw = [(_tok(w), a, b) for w, a, b in hyp if _tok(w)]
    if not sw or not hw:
        return None
    times, matched = [None] * len(sw), 0
    sm = difflib.SequenceMatcher(None, st, [h[0] for h in hw], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (hw[j1 + k][1], hw[j1 + k][2])
            matched += i2 - i1
        elif tag == "replace":  # ör. "bin dokuz yüz" ↔ "1900": aynı süreyi harf sayısına göre paylaştır
            t0, t1 = hw[j1][1], hw[j2 - 1][2]
            L = [len(sw[i]) + 1 for i in range(i1, i2)]
            tot, t = sum(L), t0
            for k, i in enumerate(range(i1, i2)):
                d = (t1 - t0) * L[k] / tot
                times[i] = (t, t + d)
                t += d
    if matched < 0.5 * len(sw):
        return None
    times = _fill_gaps(times, total)
    out, k = [], 0
    for s in sentences:
        n = len(s.split())
        out.append(times[k:k + n])
        k += n
    return out


def align_sentences(a, sentences, lang="tr", sr=SR):
    """Ses kaydındaki her kelimenin gerçekte söylendiği anı bulur (faster-whisper, CPU, ücretsiz)."""
    if os.getenv("ALIGN_OFF"):
        return None
    try:
        x = a.astype(np.float32)
        if sr != 16000:
            n = int(x.size * 16000 / sr)
            x = np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x).astype(np.float32)
        segs, _ = _whisper().transcribe(x, language=lang, word_timestamps=True, beam_size=1,
                                        condition_on_previous_text=False, vad_filter=False)
        hyp = [(w.word, w.start, w.end) for sg in segs for w in (sg.words or [])]
        return align_from_hyp(sentences, hyp, a.size / sr)
    except Exception as e:
        print(f"kelime hizalama yapılamadı: {str(e)[:200]}")
        return None


def bounds_from_alignment(al, total):
    """Cümle sınırları: iki cümle arasındaki sessizliğin ortası (ses kırpılmaz, boşluk kalmaz)."""
    edges = [0.0]
    for k in range(len(al) - 1):
        end, nxt = al[k][-1][1], al[k + 1][0][0]
        edges.append((end + nxt) / 2 if nxt > end else nxt)
    edges.append(total)
    return [(edges[i], edges[i + 1]) for i in range(len(al))]


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
                   scenes_per_request=6, max_chars=1800, lang="tr"):
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
        al = align_sentences(a, flat, lang) if len(flat) > 0 else None
        ALIGN_STATS["total"] += len(flat)
        ALIGN_STATS["ok"] += len(flat) if al else 0
        rels = [None] * len(flat)
        if al:  # gerçek kelime zamanları: cümle sınırları ve altyazı birebir sesle aynı
            bounds = bounds_from_alignment(al, a.size / SR)
            rels = [[(w0 - b0, w1 - b0) for w0, w1 in ws] for ws, (b0, _) in zip(al, bounds)]
            print(f"kelime hizalama: {len(flat)} cümle, gerçek zamanlar kullanılıyor")
        else:
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
            per_scene[i] = (seg, ss, local, rels[k - len(ss):k])
    files, durs, words, offset = [], [], [], 0.0
    for i in range(len(scenes)):
        seg, ss, local, rl = per_scene[i]
        for x, (b0, b1), r in zip(ss, local, rl):
            if r and len(r) == len(x.split()):
                words += [{"word": w, "start": round(offset + b0 + r0, 3), "end": round(offset + b0 + r1, 3)}
                          for w, (r0, r1) in zip(x.split(), r)]
            else:
                words += word_timings(x, offset + b0, max(b1 - b0, 0.2))
        full = np.concatenate([seg, np.zeros(int(scene_gap * SR), dtype=np.float32)])
        f = write_wav(full, workdir / f"vo_{i:02d}.wav")
        dur = full.size / SR
        files.append(f)
        durs.append(dur)
        offset += dur
    return files, durs, words
