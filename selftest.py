"""
API anahtarı GEREKTİRMEYEN uçtan uca test: sahte ses/görsel/servislerle gerçek kurgu hattını
(çoklu çekim, intro, kapanış, müzik, altyazı, 3 tam ekran Short, Türkçe altyazı) çalıştırır.
Çalıştır: python selftest.py  → çıktılar selftest_out/out/ altında
"""
import os, sys, json, shutil, types
REPO = str(__import__("pathlib").Path(__file__).resolve().parent); sys.path.insert(0, REPO)
class MFU:
    def __init__(s, f, **k): s._filename = f
g = types.ModuleType("googleapiclient"); h = types.ModuleType("googleapiclient.http"); h.MediaFileUpload = MFU
sys.modules["googleapiclient"] = g; sys.modules["googleapiclient.http"] = h
os.environ.update(YT_REFRESH_TOKEN="x")
import numpy as np
import pipeline, media, publish, voice
W = REPO + "/selftest_out"
shutil.rmtree(W, ignore_errors=True); os.makedirs(W)
shutil.copytree(REPO + "/assets", W + "/assets"); pipeline.ROOT = __import__("pathlib").Path(W); pipeline.HISTORY = pipeline.ROOT / "history.json"
pipeline.OUT = pipeline.ROOT / "out"; pipeline.BRAND_DONE = pipeline.ROOT / "bd.json"; pipeline.RUNLOG = pipeline.ROOT / "run.txt"
pipeline.CFG["publish_mode"] = "scheduled"; pipeline.CFG["branding"] = {}; pipeline.DRY_RUN = True; pipeline.CFG["target_words"] = 150
pipeline.notify = lambda t: print("  📨", t.splitlines()[0])
# --- içerik
scenes = [{"narration": f"This is sentence number {i} about jet engines. It explains part {i} clearly.",
           "visual": "diagram" if i % 3 == 0 else "footage", "broll_query": "jet", "image_prompt": "p",
           "chapter": {0: "The Big Question", 4: "Inside the Compressor", 8: "Fire and Thrust", 11: "Why It Matters"}.get(i, ""),
           "callout": {2: "1,400 °C", 6: "40:1 PRESSURE", 9: "10,000 RPM"}.get(i, "")} for i in range(14)]
script = {"title": "How a Jet Engine Works", "description": "Desc #a #b #c", "tags": ["jet", "engine", "science"],
          "thumbnail_concepts": [{"image_prompt": "x", "text": "JET"}] * 3, "short_title": "Jet secret",
          "shorts": [{"start_scene": 0, "end_scene": 2, "title": "Hook one"},
                     {"start_scene": 5, "end_scene": 7, "title": "Middle part"},
                     {"start_scene": 6, "end_scene": 9, "title": "OVERLAP - must be skipped"},
                     {"start_scene": 10, "end_scene": 12, "title": "Ending part"}], "scenes": scenes}
pipeline.publish.performance_report = lambda h: {"pillar_scores": {}, "best": [], "worst": [], "n": 0}
pipeline.pick_topic = lambda h, r: {"title": "t", "angle": "a", "pillar": "p"}
pipeline.write_script = lambda idea: json.loads(json.dumps(script))
pipeline.verify_loop = lambda s: (s, True)
pipeline.quality_gate = lambda s: (True, {})
def fake_llm(system, user, required=(), images=None, judge=False):
    if "Translate each line" in user:
        lines = json.loads(user.split("\n", 1)[1].rsplit("\nReturn JSON", 1)[0])["lines"]
        return {"lines": ["TR: " + l for l in lines]}
    if "short_titles" in user:
        d = json.loads(user.split("\nReturn JSON")[0])
        return {"title": "Jet motoru nasıl çalışır", "description": "Açıklama", "short_titles": ["TR " + t for t in d["short_titles"]],
                "chapters": ["TR " + c for c in d.get("chapters", [])]}
    raise AssertionError(user[:80])
pipeline.llm_json = fake_llm
def fake_synth(text, v, lang, speed):
    n = int(voice.SR * max(len(text) / 14, .5)); sil = np.zeros(int(.2 * voice.SR), np.float32)
    return np.concatenate([sil, (0.3 * np.sin(np.arange(n) / 20)).astype(np.float32), sil])
_n = voice.narrate; voice.narrate = lambda sc, wd, **k: _n(sc, wd, synth=fake_synth, **k)
clip = os.path.join(W, "stock.mp4")
media.sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", clip])
def fake_stock(q, out, led): shutil.copy(clip, out); return out
pipeline.fetch_stock = fake_stock
from PIL import Image
def fake_img(prompt, out, fq=None, led=None):
    out = __import__("pathlib").Path(out).with_suffix(".jpg"); Image.new("RGB", (1024, 1024), (20, 40, 90)).save(out); return out
pipeline.gen_image = fake_img
pipeline.make_thumbnails = lambda s, w, l: fake_img("", w / "thumb.jpg")
ups, caps = [], []
def fake_upload(video, meta, thumb=None, uploader="youtube"):
    vid = f"VID{len(ups)}xxxxxx"[:11]; ups.append((vid, meta, media.duration(video))); return vid
def fake_caption(vid, path, lang, name):
    txt = open(path, encoding="utf-8").read(); caps.append((vid, lang, txt))
    if vid != "VID0xxxxxxx": assert lang == "tr"
    return "c"
publish.upload = fake_upload; publish.upload_caption = fake_caption
pipeline.run_once()
import glob
out = pipeline.OUT
print("OUT dosyaları:", sorted(p.name for p in out.glob("*")) if out.exists() else "-")

sc = json.load(open(W + "/out/script.json"))
chs = sc.get("_chapters")
print("BÖLÜMLER:", chs)
assert chs and chs[0][0] == 0 and len(chs) >= 3
assert all(b[0] - a[0] >= 12 for a, b in zip(chs, chs[1:]))
print(pipeline.chapters_text(chs, ["TR " + c[1] for c in chs], "Bölümler"))
ass = open(W + "/captions.ass", encoding="utf-8").read() if os.path.exists(W + "/captions.ass") else ""
print("✅ selftest tamam")
