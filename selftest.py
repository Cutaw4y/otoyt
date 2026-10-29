"""
API anahtarı GEREKTİRMEYEN uçtan uca render testi.
Sahte stok video, sahte diyagram ve sahte ses üretir; gerçek hattı (görsel seçimi,
Ken Burns, altyazı, müzik, Short, thumbnail) çalıştırıp sonuçları doğrular.
Çalıştır:  python selftest.py   → çıktılar selftest_out/ klasörüne
"""
import json, shutil, sys
from pathlib import Path

from PIL import Image, ImageDraw

import media
import pipeline

OUTDIR = Path(__file__).parent / "selftest_out"


def probe(path):
    out = media.sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                    "stream=width,height", "-of", "json", path])
    s = json.loads(out)["streams"][0]
    return s["width"], s["height"], media.duration(path)


def fake_image(path, color, label):
    im = Image.new("RGB", (1536, 1024), color)
    d = ImageDraw.Draw(im)
    for i in range(0, 1536, 96):
        d.line([(i, 0), (i, 1024)], fill=(255, 255, 255), width=2)
    d.ellipse([568, 312, 968, 712], outline=(0, 255, 255), width=12)
    d.text((40, 40), label, fill="white", font=media._font(60))
    im.save(path)
    return path


def main():
    if OUTDIR.exists():
        shutil.rmtree(OUTDIR)
    w = OUTDIR / "work"
    w.mkdir(parents=True)

    # --- sahte stok video (720p, 5 sn — sahneden kısa: döngü testi)
    clip = w / "stock.mp4"
    media.sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "5",
              "-c:v", "libx264", "-preset", "ultrafast", clip])

    # --- sahte senaryo: 7 sahne, karışık görsel tipleri
    durs = [7.5, 9.0, 8.0, 10.0, 12.0, 11.0, 9.5]
    script = {"title": "How a Jet Engine Actually Works", "scenes": [
        {"narration": f"Scene {i}", "visual": v, "broll_query": "q", "image_prompt": "p", "duration": d}
        for i, (v, d) in enumerate(zip(["footage", "diagram", "footage", "footage", "diagram",
                                        "footage", "footage"], durs))]}

    # --- dış servisleri sahtele
    calls = {"pexels": 0, "image": 0}

    def fake_pexels(query, out, ledger):
        calls["pexels"] += 1
        if calls["pexels"] == 3:      # bir sahnede stok bulunamasın → diyagrama düşmeli
            return None
        if calls["pexels"] == 4:
            return None
        shutil.copy(clip, out)
        ledger.add(1000 + calls["pexels"])
        return out

    def fake_gen_image(prompt, out, fallback_query=None, ledger=None):
        calls["image"] += 1
        return fake_image(out, (20, 30, 70), f"diagram {calls['image']}")

    pipeline.fetch_stock = fake_pexels
    pipeline.gen_image = fake_gen_image
    pipeline.CFG["min_diagram_ratio"] = 0.4

    # --- ses: gerçek voice.narrate akışı, Kokoro yerine sahte sentezleyici
    import numpy as np
    import voice as voice_mod

    def fake_synth(text, v, lang, speed):  # ~14 karakter/sn konuşma + baş/son sessizlik
        n = int(voice_mod.SR * max(len(text) / 14, 0.5))
        tone = 0.3 * np.sin(np.linspace(0, 2 * np.pi * 220 * n / voice_mod.SR, n)).astype(np.float32)
        sil = np.zeros(int(0.3 * voice_mod.SR), dtype=np.float32)
        return np.concatenate([sil, tone, sil])

    sentence = "Air is pulled into the front of the engine and squeezed hard. "
    for i, d in enumerate(durs):
        script["scenes"][i]["narration"] = (sentence * max(1, round(d / 4.6))).strip()
    pipeline.CFG["voice"] = {"name": "af_heart", "lang": "a", "speed": 1.0}
    _orig_narrate = voice_mod.narrate
    voice_mod.narrate = lambda scenes, wd, **kw: _orig_narrate(scenes, wd, synth=fake_synth, **kw)
    voice, words = pipeline.narrate(script, w)
    total = media.duration(voice)
    music = w / "music.mp3"
    media.sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=20", music])

    # --- gerçek hattı çalıştır
    ledger = media.AssetLedger(w / "assets_used.json")
    segs = pipeline.build_segments(script, w, ledger)
    ledger.save()
    srt = media.write_srt(words, w / "subs.srt")
    silent = media.concat(segs, w / "silent.mp4", w, "segs")
    final = media.mux_final(silent, voice, srt, OUTDIR / "final.mp4", music=music)
    short, slen = media.make_short(segs, [s["duration"] for s in script["scenes"]], voice, words,
                                   OUTDIR / "short.mp4", w, music=music)
    thumbs = []
    for i, txt in enumerate(["JET ENGINE SECRET", "This Is How Planes Really Fly Through The Sky", "WHY?"]):
        raw = fake_image(w / f"t{i}.png", (60 + 50 * i, 40, 20), "")
        thumbs.append(media.compose_thumbnail(raw, txt, OUTDIR / f"thumb_{i}.jpg"))
    media.contact_sheet(thumbs, OUTDIR / "thumbs_sheet.jpg")

    # --- doğrulamalar
    fails = []
    fw, fh, fd = probe(final)
    sw, shh, sd = probe(short)
    diag = sum(1 for s in script["scenes"] if s["visual"] == "diagram")
    checks = [
        ("Ana video 1920x1080", (fw, fh) == (1920, 1080)),
        (f"Ana video süresi sesle eşit ({fd:.2f} ≈ {total:.2f})", abs(fd - total) < 0.3),
        ("Short 1080x1920", (sw, shh) == (1080, 1920)),
        (f"Short ≤ 58 sn ({sd:.2f})", sd <= 58.5),
        (f"Diyagram oranı ≥ %40 ({diag}/7)", diag >= 3),
        (f"Stok bulunamayınca AI görsele düştü (görsel çağrısı: {calls['image']})", calls["image"] >= diag + 1),
        ("Kanal geneli tekrar kaydı yazıldı", len(json.loads((w / "assets_used.json").read_text())) >= 1),
        ("3 thumbnail 1280x720", all(Image.open(t).size == (1280, 720) for t in thumbs)),
        (f"Altyazı kelimeleri sırayla ve ses içinde ({len(words)} kelime)",
         all(a["start"] <= b["start"] for a, b in zip(words, words[1:])) and words[-1]["end"] <= total + 0.05),
        ("Sahne süreleri toplamı = ses süresi",
         abs(sum(s["duration"] for s in script["scenes"]) - total) < 0.1),
    ]
    for name, ok in checks:
        print(("✅ " if ok else "❌ ") + name)
        if not ok:
            fails.append(name)

    # inceleme için kareler
    for name, src, ts in [("frame_footage", final, 2), ("frame_diagram", final, 10), ("frame_short", short, 12)]:
        media.sh(["ffmpeg", "-y", "-ss", str(ts), "-i", src, "-frames:v", "1", OUTDIR / f"{name}.jpg"])

    print("\nSONUÇ:", "TÜM TESTLER GEÇTİ" if not fails else f"{len(fails)} TEST BAŞARISIZ")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
