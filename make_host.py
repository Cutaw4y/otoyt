"""Gölgedefter anlatıcı karakteri 'Gölge' için gerçekçi görsel adayları üretir (Vertex / Gemini görsel)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")
import ai

ai.LOG = print
OUT = Path("assets/golgedefter/host")
OUT.mkdir(parents=True, exist_ok=True)
BASE = ("Photorealistic cinematic film still, 35mm, shallow depth of field. A mysterious hooded narrator, "
        "face COMPLETELY hidden in deep shadow under a heavy dark wool hood (no eyes, no face visible, only darkness "
        "inside the hood), dark robe, {scene}. Single warm candle light from the side, amber highlights, cold blue "
        "moonlight rim, light fog, old stone and parchment textures, muted desaturated colors, moody, 16:9, "
        "the figure on the RIGHT third, empty dark space on the left. No text, no logo, no watermark.")
SCENES = [
    "seated at an old wooden desk writing in a large open leather-bound book with a black feather quill",
    "standing beside a stone lectern in an ancient library, one gloved hand resting on an open old book",
    "seated in a dark stone chamber, an old book open on his knees, candles on the floor around him",
]
r = ai._vertex_generate("gemini-3.1-flash-image", {"contents": [{"role": "user", "parts": [{"text": "a candle"}]}],
                        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}}})
print(f"::notice title=vertex görsel ham::{r.status_code} {r.text[:600].replace(chr(10), " ")}")
for i, s in enumerate(SCENES, 1):
    try:
        p = ai.gemini_image(BASE.format(scene=s), OUT / f"host_{i}.jpg")
        print(f"::notice title=Gölge {i}::{p}")
    except Exception as e:
        print(f"::error title=Gölge {i}::{str(e)[:300]}")
    import time; time.sleep(15)
