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
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


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
