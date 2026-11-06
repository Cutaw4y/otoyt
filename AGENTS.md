# OtoYT — yapay zekâ asistanları için ortak kurallar (Claude + ChatGPT/Codex)

Bu depo üzerinde iki asistan çalışıyor: **Claude** ve **ChatGPT (Codex)**. İkisi de bu dosyayı okur.
Birbirimizle doğrudan konuşamadığımız için köprü bu dosya ve `NOTLAR.md` günlüğüdür.

## Çalışma düzeni (ikimiz için de)
1. İşe başlamadan önce `git pull` yap, `NOTLAR.md` dosyasının son 20 satırını oku.
2. Her değişiklikten sonra `NOTLAR.md` sonuna **tek satır** ekle:
   `- YYYY-MM-DD HH:MM [Claude|ChatGPT] ne değişti — neden (dosya adları)`
3. Göndermeden önce: `python -m py_compile *.py && python -m unittest discover -s tests -p 'test_*.py' && python3 json_guard.py`
4. Büyük değişiklikler (iş akışı, zamanlama, yayın, kanal ayarı) → doğrudan main yerine **dal + PR** aç;
   PR açıklamasına neyi neden değiştirdiğini yaz. Diğer asistan inceler.
5. Diğer asistanın kararını geri almadan önce `NOTLAR.md`'ye gerekçesini yaz; kanal sahibinin (Furkan)
   açık isteği her şeyden önce gelir.

## Bozulmaması gerekenler (kanal sahibinin kararları, 06.11.2026)
- **Tamamen otomatik.** Gölgedefter'de Telegram onayı KAPALI (`telegram_editorial.enabled: false`).
  Konuyu sistem seçer; Furkan'dan onay beklenmez.
- **Günler:** Beyond Qaf Pzt/Çar/Per (otoyt.yml cron `1,3,4`), Gölgedefter Sal/Cuma/Cmt/Paz
  (otoyt_tr.yml cron `0,2,5,6`). **Aynı gün iki kanal üretmez.** Bekçideki gün listeleri (watchdog.py) bunlarla aynı olmalı.
- **Saatler (TR):** Gölgedefter hafta içi 19:00, hafta sonu 16:00; Beyond Qaf New York 14:00.
- **Cuma = uzun belgesel** (Gölgedefter `weekday_overrides: 4`, ≥40 dk, ~5000 kelime, belgesel teknikleri).
- **Konu yönü (Gölgedefter):** doğaüstü/ürpertici, bilinmezlik içeren konular öncelikli (Deccal ve Alkarısı tuttu,
  Kaf Dağı tutmadı). Uydurma kaynak, kişi, tarih, alıntı YOK.
- **İki üretim asla aynı anda çalışmaz:** üretim iş akışları `concurrency: otoyt-uretim`.
- **Aynı gün ikinci video üretilmez:** pipeline + workflow koruması; bilerek isteniyorsa `force_new_video`.
- **Yüklenmiş bir videonun senaryosu tekrar kullanılmaz** (`pending_script.json` başlığı geçmişte varsa silinir).
- **Instagram atlanmaz:** her uzun videonun Reels'leri sıraya alınır, bekçi paylaşılmadıysa tekrar dener.
- **Telegram:** yalnız önemli durumlar; müdahale gerekiyorsa mesaj `🆘 CLAUDE'A YAZ` ile başlar.
- **Görseller:** önce gerçek ve doğrulanmış foto/video, alakasız görsel yok (`strict_visuals: true`).
- **Gizli bilgiler** yalnız GitHub Secrets'ta; depo herkese açık — günlüklere anahtar, kimlik, kişisel veri yazma.
- Testler canlı kayıt dosyalarındaki (history.json vb.) belirli video kimliklerine/sayılarına bağlanmamalı.
