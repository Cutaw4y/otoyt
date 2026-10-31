"""Kelime hizalama testi: ücretsiz Edge sesiyle Türkçe paragraf üretir, gerçek kelime zamanlarını bulur."""
import sys
import time

import numpy as np

import voice

sents = ["Bin dokuz yüz elli dokuz yılının şubat ayında dokuz genç dağa tırmandı.",
         "Babil kil tabletlerinde Lamaştu adında dişi bir iblis anlatılır.",
         "Anlatılana göre lohusa kadınların yastığının altına paslı bir makas konurdu!",
         "Peki bu korkunun arkasındaki gerçek neydi?"]
parts = []
for s in sents:
    parts += [voice._trim(voice.synth_edge(s, "tr-TR-AhmetNeural")), np.zeros(int(0.4 * voice.SR), np.float32)]
a = np.concatenate(parts)
t = time.time()
al = voice.align_sentences(a, sents, "tr")
if not al:
    print("::error title=Hizalama::başarısız (None)")
    sys.exit(1)
for s, ws in zip(sents, al):
    print(f"::notice title=Hizalama::" + " ".join(f"{w}[{x:.2f}-{y:.2f}]" for w, (x, y) in zip(s.split(), ws)))
print(f"::notice title=Hizalama süresi::{time.time() - t:.1f} sn, ses {a.size / voice.SR:.1f} sn")
