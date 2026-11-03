"""Imagen testi (Vertex): hangi Imagen modelleri bu projede açık? Veo deneme iş akışından çalıştırılır."""
import ai
ai.LOG = print
for m in ["imagen-4.0-fast-generate-001", "imagen-4.0-fast-generate-preview-06-06", "imagen-4.0-generate-001",
          "imagen-4.0-ultra-generate-001", "imagen-3.0-fast-generate-001", "imagen-3.0-generate-002"]:
    try:
        p = ai.imagen_image("An ancient stone island in a stormy night sea, a lone wooden ship, cinematic", f"t_{m}.jpg", model=m)
        print(f"::notice title=Imagen {m}::ÇALIŞIYOR")
    except Exception as e:
        print(f"::warning title=Imagen {m}::{str(e)[:160]}")
