"""Imagen 4 Fast testi (Vertex): bir görsel üretir, boyutunu yazar. Veo deneme iş akışından çalıştırılır."""
import ai
ai.LOG = print
p = ai.imagen_image("An ancient stone island in a stormy night sea, a lone wooden ship approaching, cinematic, "
                    "moody blue light", "imagen_test.jpg")
from PIL import Image
print("::notice title=Imagen testi::BAŞARILI", Image.open(p).size)
