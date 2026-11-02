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
    out = pipeline.fetch_real_photo("Dyatlov Pass", __import__("pathlib").Path("/tmp/real.jpg"),
                                    "The hikers' tent was found on the slope of Kholat Syakhl")
    print(f"::notice title=Gerçek fotoğraf::{'bulundu' if out else 'bulunamadı'} | {pipeline.REAL_CREDITS[:1]}")
except Exception as e:
    print(f"::error title=Gerçek fotoğraf::{str(e)[:300]}")
