import os

# Sistemin kendi Vertex yolu (ai.py) ile uçtan uca deneme
try:
    import sys
    sys.path.insert(0, ".")
    import ai
    ai.LOG = print
    t = ai.gemini("Reply with the single word: ok", models=("gemini-flash-latest",))
    print(f"::notice title=Sistem yazı (Vertex)::{t[:60]}")
    ai.TTS_LANG = "tr-TR"
    a = ai.gemini_tts("Anlatılana göre o gece rüzgâr hiç durmadı.", voice="Charon",
                      style="Gizemli bir belgesel anlatıcısı gibi oku", models=("vertex:gemini-2.5-flash-tts",))
    print(f"::notice title=Sistem ses (Vertex)::{a.size / 24000:.1f} sn")
    out = ai.gemini_image("A dark misty mountain pass at night with a lone torn tent, cinematic, no text", "/tmp/t.jpg")
    print(f"::notice title=Sistem görsel (Vertex)::{out}")
except Exception as e:
    print(f"::error title=Sistem Vertex::{str(e)[:300]}")


# Gerçek fotoğraf (Wikimedia Commons) denemesi
try:
    os.environ.setdefault("OTOYT_CONFIG", "channels/golgedefter.yaml"); os.environ["DEBUG_REAL"] = "1"
    import pipeline
    import requests as _rq
    rr = _rq.get("https://commons.wikimedia.org/w/api.php", headers=pipeline.WIKI_HEADERS, timeout=30, params={
        "action": "query", "format": "json", "generator": "search", "gsrsearch": "Dyatlov Pass filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1920})
    pg = list((rr.json().get("query") or {}).get("pages", {}).values())
    print("::notice title=Commons ham::" + str(rr.status_code) + " " + " | ".join(
        f"{p['title'][5:40]} {p['imageinfo'][0].get('width')} {p['imageinfo'][0].get('mime')} "
        f"{(p['imageinfo'][0].get('extmetadata') or {}).get('LicenseShortName', {}).get('value', '')}" for p in pg[:6]))
    o2 = pipeline.fetch_openverse("Tacoma Narrows Bridge", __import__("pathlib").Path("/tmp/ov.jpg"), "the suspension bridge twisting")
    print(f"::notice title=Openverse::{'bulundu' if o2 else 'bulunamadı'} | {pipeline.REAL_CREDITS[-1:] }")
    out = pipeline.fetch_real_photo("Hoover Dam", __import__("pathlib").Path("/tmp/real.jpg"),
                                    "The giant concrete dam holds back Lake Mead on the Colorado River")
    print(f"::notice title=Gerçek fotoğraf::{'bulundu' if out else 'bulunamadı'} | {pipeline.REAL_CREDITS[:1]}")
except Exception as e:
    print(f"::error title=Gerçek fotoğraf::{str(e)[:300]}")


# Veo (video üretimi) var mı?
try:
    import requests as _rq
    H = ai._vertex_headers(); P = ai._vx["proj"]
    for m in ("veo-3.1-generate-preview", "veo-3.1-fast-generate-preview", "veo-3.0-generate-001", "veo-3.0-fast-generate-001", "veo-2.0-generate-001"):
        u = f"https://us-central1-aiplatform.googleapis.com/v1/projects/{P}/locations/us-central1/publishers/google/models/{m}"
        r = _rq.get(u, headers=H, timeout=30)
        print(f"::notice title=Veo {m}::{r.status_code} {r.text[:120].replace(chr(10), ' ')}")
except Exception as e:
    print(f"::error title=Veo::{str(e)[:200]}")
