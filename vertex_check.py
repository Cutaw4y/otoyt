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
