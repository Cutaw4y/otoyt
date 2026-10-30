"""
Gölgedefter (Türkçe kanal) için anahtarsız uçtan uca test: Gemini TTS sahte sesle taklit edilir,
gerçek kurgu hattı (intro, Türkçe bölüm kartları, altyazı, Shorts, İngilizce çeviri izi) çalışır.
Çalıştır: python selftest_tr.py  → çıktılar selftest_tr_out/out/ altında
"""
import os, sys, json, shutil, types
from pathlib import Path
REPO = str(Path(__file__).resolve().parent); sys.path.insert(0, REPO)
os.environ["OTOYT_CONFIG"] = "channels/golgedefter.yaml"
class MFU:
    def __init__(s, f, **k): s._filename = f
g = types.ModuleType("googleapiclient"); h = types.ModuleType("googleapiclient.http"); h.MediaFileUpload = MFU
sys.modules["googleapiclient"] = g; sys.modules["googleapiclient.http"] = h
import numpy as np
import pipeline, media, publish, voice, ai
assert pipeline.CFG["channel_name"] == "Gölgedefter" and media.LANG == "tr" and media.CHAPTER_LABEL == "BÖLÜM"
W = Path(REPO) / "selftest_tr_out"
shutil.rmtree(W, ignore_errors=True); W.mkdir()
pipeline.ROOT = W; pipeline.HISTORY = W / "history.json"; pipeline.DATA = W
pipeline.OUT = W / "out"; pipeline.BRAND_DONE = W / "bd.json"; pipeline.RUNLOG = W / "run.txt"; pipeline.PENDING_CAPS = W / "pc"
pipeline.PENDING_COMMENTS = W / "pcm.json"; pipeline.PLAYLISTS = W / "pl.json"; pipeline.THUMB_ALT = W / "ta"
pipeline.MANUAL_DIR = W / "manual"; pipeline.MANUAL_PENDING = W / "mp.json"
pipeline.CFG["branding"] = {}; pipeline.DRY_RUN = True; pipeline.CFG["target_words"] = 120
sent = []
pipeline.notify = lambda t, **kw: (sent.append(t), print("  📨", t.splitlines()[0]))
chap = {0: "Dağdaki Çadır", 4: "Kayıtlar Ne Diyor", 8: "Açıklamalar", 11: "Hâlâ Bilinmeyen"}
scenes = [{"narration": f"Anlatılana göre {i}. gece rüzgâr şiddetliydi. Çadırın içi ışıksızdı ve sessizdi.",
           "visual": "diagram" if i % 2 == 0 else "footage", "broll_query": "snow mountain night", "image_prompt": "p",
           "chapter": chap.get(i, ""), "callout": {2: "ŞUBAT 1959", 6: "9 KİŞİ", 9: "-30 °C"}.get(i, "")} for i in range(14)]
script = {"title": "Dyatlov Geçidi: Çadırı İçeriden Kim Kesti?", "description": "Açıklama #gizem #tarih #efsane",
          "tags": ["gizem", "dyatlov", "belgesel"], "thumbnail_concepts": [{"image_prompt": "x", "text": "İÇERİDEN KESİLDİ"}] * 3,
          "short_title": "Çadır içeriden kesildi", "scenes": scenes,
          "shorts": [{"start_scene": 0, "end_scene": 2, "title": "Çadırı içeriden kim kesti?"},
                     {"start_scene": 5, "end_scene": 7, "title": "Kayıtlardaki tuhaf ayrıntı"},
                     {"start_scene": 10, "end_scene": 12, "title": "Hâlâ çözülemeyen soru"}]}
pipeline.publish.performance_report = lambda h: {"pillar_scores": {}, "best": [], "worst": [], "n": 0}
pipeline.pick_topic = lambda h, r: {"title": "t", "angle": "a", "pillar": "p"}
pipeline.write_script = lambda idea: json.loads(json.dumps(script))
pipeline.verify_loop = lambda s: (s, True)
pipeline.quality_gate = lambda s: (True, {})
def fake_llm(system, user, required=(), images=None, judge=False):
    if "Translate every value" in user:
        lines = json.loads(user.split("\n", 1)[1].rsplit("\nReturn JSON", 1)[0])["lines"]
        return {"lines": {k: "EN: " + v for k, v in lines.items()}}
    if "short_titles" in user:
        d = json.loads(user.split("\nReturn JSON")[0])
        return {"title": "Dyatlov Pass: Who Cut the Tent?", "description": "Desc", "short_titles": ["EN " + t for t in d["short_titles"]],
                "chapters": ["EN " + c for c in d.get("chapters", [])]}
    raise AssertionError(user[:80])
pipeline.llm_json = fake_llm
tts_calls = []
def fake_tts(text, voice="Charon", style=None, models=None, **k):
    """Her cümle bir ton bloğu, aralarında 0.35 sn sessizlik (gerçek Gemini çıktısına benzer)."""
    tts_calls.append((text, voice, style))
    sr = 24000; out = [np.zeros(int(.15 * sr), np.float32)]
    for s in voice_mod.split_sentences(text):
        n = int(sr * max(len(s) / 15, .5)); out += [(0.3 * np.sin(np.arange(n) / 20)).astype(np.float32), np.zeros(int(.35 * sr), np.float32)]
    return np.concatenate(out)
voice_mod = voice
ai.gemini_tts = fake_tts
clip = str(W / "stock.mp4")
media.sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", clip])
def fake_stock(q, out, led): shutil.copy(clip, out); return out
pipeline.fetch_stock = fake_stock
from PIL import Image
def fake_img(prompt, out, fq=None, led=None):
    out = Path(out).with_suffix(".jpg"); Image.new("RGB", (1024, 1024), (40, 30, 30)).save(out); return out
pipeline.gen_image = fake_img
pipeline.make_thumbnails = lambda s, w, l: (fake_img("", w / "thumb.jpg"), None)
pipeline.run_once()
outs = sorted(p.name for p in pipeline.OUT.glob("*"))
print("OUT dosyaları:", outs)
assert tts_calls and tts_calls[0][1] == "Charon" and "gizemli" in tts_calls[0][2]
assert any("Gölgedefter'in bir sonraki sayfasında" in c[0] for c in tts_calls), "kapanış cümlesi seslendirilmedi"
assert "captions_en.srt" in outs and "captions_tr.srt" in outs, outs
assert "EN: " not in (pipeline.OUT / "captions_tr.srt").read_text(encoding="utf-8")
en = (pipeline.OUT / "captions_en.srt").read_text(encoding="utf-8"); assert "EN: " in en
sc = json.load(open(pipeline.OUT / "script.json", encoding="utf-8"))
print("BÖLÜMLER:", sc.get("_chapters"))
print(pipeline.chapters_text(sc["_chapters"], header=pipeline.CFG["chapters_heading"]))
print("✅ Türkçe kanal testi tamam")
