# OtoYT v3 — Tamamen Ücretsiz Otonom Eğitim Kanalı

Haftada 3 kez kendi kendine: konu seçer → senaryo yazar → **Google'da doğrular** → seslendirir →
**stok video + AI diyagram** ile render alır → **Short** keser → **3 thumbnail'dan en iyisini** seçer →
yükler → **hangi videonun tuttuğunu ölçüp** sonraki konuları ona göre seçer.

## Kullanılan servisler — aylık maliyet: 0 TL
| İş | Servis | Ücretsiz sınır |
|---|---|---|
| Fikir, senaryo, editör, thumbnail hakemi | Google Gemini (AI Studio) | Video başına ~15 istek; ücretsiz katman yetiyor |
| Bilgi doğrulama (Google araması) | Gemini + Google Search | Ayda ~5.000 aramalı istek ücretsiz |
| Seslendirme | Kokoro (açık kaynak, Apache-2.0) | Sınırsız — GitHub sunucusunda çalışır |
| AI diyagram + thumbnail | Cloudflare Workers AI (FLUX.1 schnell) | Günde ~170 görsel (video başına ~13 lazım) |
| Stok video / yedek fotoğraf | Pixabay (+ Pexels, varsa) | Ücretsiz |
| Render + zamanlama | GitHub Actions | Gizli repoda ayda 2.000 dk (kullanım ~500 dk) |
| Yükleme + analitik | YouTube Data/Analytics API | Ücretsiz |
| Bildirim | Telegram | Ücretsiz |

## GitHub Secrets
Zorunlu: `GEMINI_API_KEY`, `PIXABAY_API_KEY`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`,
`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`
İsteğe bağlı: `PEXELS_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `IG_ACCESS_TOKEN`, `IG_USER_ID`

## Dosyalar
| Dosya | Görevi |
|---|---|
| `config.yaml` | Kanal kimliği, modeller, ses, kalite eşikleri, yayın ayarları (tek düzenlenecek dosya) |
| `pipeline.py` | Ana akış |
| `ai.py` | Gemini + Cloudflare bağlantısı |
| `voice.py` | Kokoro seslendirme + altyazı zamanlaması |
| `media.py` | Render, Short, thumbnail |
| `publish.py` | YouTube / Instagram yükleme + Analytics |
| `selftest.py` | API'siz render testi |
| `get_token.bat` / `ig_token.bat` | Windows'ta çift tık → izin anahtarları |

## Devreye alma sırası
1. **Test modu:** Actions → OtoYT → Run workflow → "Test modu" kutusu işaretli.
   YouTube'a dokunmaz; video, Short ve thumbnail çalışmanın **Artifacts** bölümünden indirilir.
2. **Gizli yükleme:** `publish_mode: "private"` (varsayılan).
3. **Canlı:** YouTube API denetimi onaylanınca `publish_mode: "scheduled"`.

## Güvenlik sigortaları
- Google'da doğrulanamayan video **üretilmez**.
- Editör puanı 7 altıysa 2 kez yeniden yazılır, olmazsa **iptal**.
- Gemini kotası dolarsa bekler / yedek modele geçer; Cloudflare kotası dolarsa Pexels fotoğrafına geçer.
- Yükleme başarısız olursa video kaybolmaz → Artifacts (7 gün).
- Yayın 6 saat gecikmeli: Telegram linkinden bakıp istemezsen Studio'dan silebilirsin.

## Bilinmesi gerekenler
- Ücretsiz katmanların sınırları servis sahiplerince değiştirilebilir.
- Gemini ücretsiz katmanında Google girdileri ürün geliştirmede kullanabilir; burada gönderilen her şey
  zaten herkese açık yayınlanacak içerik olduğu için sorun değildir — kişisel veri gönderme.
