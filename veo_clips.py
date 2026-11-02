"""Gölge anlatıcı için gerçek hareketli klipler (Veo 3.1, sesli). Bir kez üretilir, her videoda tekrar kullanılır."""
import base64, os, sys, time
from pathlib import Path
import requests
sys.path.insert(0, ".")
import ai

MODEL = os.getenv("VEO_MODEL", "veo-3.1-generate-001")
OUT = Path("assets/golgedefter/host/clips"); OUT.mkdir(parents=True, exist_ok=True)
IMG = base64.b64encode(Path("assets/golgedefter/host/host_1.jpg").read_bytes()).decode()
KEEP = ("The figure's face stays COMPLETELY hidden in the deep black shadow of the hood at all times. "
        "Realistic, cinematic, subtle natural motion, 24fps film look. No text, no letters, no music.")
CLIPS = {
    "write": ("The hooded figure writes slowly and steadily with the black feather quill in the open book, his hand and "
              "arm moving naturally across the page, the quill tip leaving ink. The candle flame flickers softly, faint "
              "fog drifts. Very slow camera push-in. Sound: quill scratching on parchment, quiet stone room ambience, "
              "soft candle crackle. " + KEEP, True),
    "look": ("The hooded figure pauses writing, slowly raises his head toward the camera as if about to tell a story "
             "(face remains pure darkness), then lowers it and continues writing. Candle flickers, fog drifts. "
             "Sound: quill scratching, a soft breath, room ambience. " + KEEP, True),
    "page": ("The hooded figure turns a page of the old book with his gloved hand, then dips the quill and starts writing "
             "again. Candlelight flickers on the paper. Sound: parchment page rustle, quill scratching. " + KEEP, True),
    "close": ("Extreme close-up, top-down view of an aged blank parchment page of an open old book on a wooden desk. "
              "A dark-gloved hand holding a black feather quill enters and writes a line of elegant handwriting from "
              "left to right across the middle of the page, the arm moving with each stroke. Warm candlelight, deep "
              "shadows. Sound: clear quill scratching on paper, soft candle crackle. Realistic, cinematic. No music.", False),
    "title_test": ("Extreme close-up, top-down view of an aged blank parchment page of an open old book on a wooden desk. "
                   "A dark-gloved hand holding a black feather quill writes the words \"Dyatlov Geçidi\" in large, "
                   "elegant, clearly legible calligraphy across the middle of the page, letter by letter from left to "
                   "right; the arm and hand move with each stroke and the ink appears exactly under the quill tip. "
                   "Nothing else is written on the page. Warm candlelight, deep shadows. Sound: quill scratching on "
                   "paper, soft candle crackle. Realistic, cinematic. No music.", False),
    "intro_write": ("Side view, slightly low angle. The hooded figure leans over the open book and writes with the black "
                    "feather quill, his arm moving steadily; the pages are seen at a steep angle so the writing is NOT "
                    "readable. He pauses, lifts his head toward the camera as if beginning to tell an old story (face "
                    "remains pure black shadow inside the hood), then continues writing. Candle flame flickers, fog "
                    "drifts in the moonlight from the window. Slow cinematic dolly-in. Sound: quill scratching on "
                    "parchment, a low distant wind, soft candle crackle, quiet tense ambience. " + KEEP, True),
    "page_blank": ("Locked-off static camera, perfectly top-down view directly above an open old book on a dark wooden "
                   "desk. The right-hand page is aged, clean, completely blank parchment, filling the center of the frame, "
                   "flat and evenly lit. Warm candlelight flickers gently across the paper, faint fog drifts at the edges. "
                   "No hands, no quill in frame, no camera movement, no text. Sound: soft candle crackle, quiet room "
                   "ambience. Realistic, cinematic.", False),
}


def run(name, prompt, with_image):
    inst = {"prompt": prompt}
    if with_image:
        inst["image"] = {"bytesBase64Encoded": IMG, "mimeType": "image/jpeg"}
    base = (f"https://us-central1-aiplatform.googleapis.com/v1/projects/{ai._vx['proj'] or ''}"
            f"/locations/us-central1/publishers/google/models/{MODEL}")
    h = ai._vertex_headers()
    base = base.replace("projects//", f"projects/{ai._vx['proj']}/")
    r = requests.post(base + ":predictLongRunning", headers=h, timeout=120, json={
        "instances": [inst], "parameters": {"aspectRatio": "16:9", "durationSeconds": 8, "sampleCount": 1,
                                            "generateAudio": True, "resolution": "1080p",
                                            "personGeneration": "allow_adult",
                                            "negativePrompt": "visible face, eyes, skin of face, text, letters, watermark, music"}})
    if not r.ok:
        print(f"::error title=klip {name}::{r.status_code} {r.text[:300]}")
        return
    op = r.json()["name"]
    for _ in range(80):
        time.sleep(10)
        q = requests.post(base + ":fetchPredictOperation", headers=ai._vertex_headers(), timeout=120,
                          json={"operationName": op}).json()
        if q.get("done"):
            vids = (q.get("response") or {}).get("videos") or []
            if not vids:
                print(f"::error title=klip {name}::{str(q)[:300]}")
                return
            (OUT / f"{name}.mp4").write_bytes(base64.b64decode(vids[0]["bytesBase64Encoded"]))
            print(f"::notice title=klip {name}::hazır")
            return
    print(f"::error title=klip {name}::zaman aşımı")


ai.LOG = print
only = [x for x in os.getenv("CLIPS", "").split(",") if x] or list(CLIPS)
for n in only:
    p, img = CLIPS[n]
    run(n, p, img)
