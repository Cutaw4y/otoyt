import json, os, sys, time, base64, requests
sys.path.insert(0, ".")
import ai
h = ai._vertex_headers(); proj = ai._vx["proj"]
for m in ["veo-3.1-fast-generate-001", "veo-3.1-generate-001", "veo-3.0-fast-generate-001", "veo-3.1-fast-generate-preview", "veo-3.0-generate-001"]:
    u = f"https://us-central1-aiplatform.googleapis.com/v1/projects/{proj}/locations/us-central1/publishers/google/models/{m}:predictLongRunning"
    r = requests.post(u, headers=h, timeout=60, json={"instances": [{"prompt": "a candle flame flickering in a dark room"}],
                      "parameters": {"aspectRatio": "16:9", "durationSeconds": 4, "sampleCount": 1, "generateAudio": False}})
    print(f"::notice title=veo {m}::{r.status_code} {r.text[:200].replace(chr(10),' ')}")
    if r.ok:
        break
