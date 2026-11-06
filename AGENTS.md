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

## Görev dağılımı (Furkan'ın kararı, 06.11.2026)
- **Claude = ana geliştirici.** Büyük değişiklikleri dal + PR olarak açar, PR'a `@codex review` yazar.
- **ChatGPT/Codex = ikinci göz.** Claude'un PR'larını inceler; hata, risk ve eksikleri PR yorumu olarak yazar.
  Kendisi değişiklik yapacaksa da main'e doğrudan değil, PR açarak yapar (yalnız küçük metin/yorum düzeltmeleri hariç).
- Görüş ayrılığında iki taraf gerekçesini PR'a yazar; son karar Furkan'ındır.
- Acil üretim hatası (video bugün çıkmayacaksa) doğrudan main'e düzeltilebilir; NOTLAR.md'ye `ACİL` diye yazılır.

## İçerik ana kuralı (Furkan, 07.11.2026)
- İzleyici abartıyı, sıra dışını, doğaüstü bilinmezliği sever: anlatım sinematik/dramatik, her bölümde yeni şaşırtıcı
  ayrıntı + cevabı saklanan soru; ilk 15 sn en ürpertici anla başlar. Abartı yalnız anlatımda — uydurma bilgi yok.
- Başlık + küçük resim EN önemli parça: gerçek yer/nesne + yasak/ölümcül/açıklanamayan iddia + cevapsız soru;
  küçük resimde 2-4 kelimelik şok cümle (ör. "İÇİNE GİREN ÖLÜYOR").
- İzleyici geri bildirimleri (06.11) kalıcı kural: alakasız görsel yok (fok/basketbol/trafik), aynı görsel tekrarı yok,
  seste tekrar/sürçme yok, yavaş giriş yok. Bunları bozan değişiklik testlerden geçemez.

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
- **Görseller (Furkan kuralı, 06.11):** anlatılan şeyin önce GERÇEK görüntüsü kullanılır, üretilmez:
  1) serbest lisanslı gerçek arşiv videosu (Wikimedia Commons, Internet Archive), 2) gerçek fotoğraf (Commons/Openverse),
  3) stok video (Pixabay/Pexels), 4) en son yapay zekâ görseli. Aramalar İngilizce yapılır; her aday yapay zekâyla
  alaka kontrolünden geçer (`strict_visuals: true`), kaynaklar açıklamaya yazılır. Aynı görsel videoyu dolduramaz.
- **Gizli bilgiler** yalnız GitHub Secrets'ta; depo herkese açık — günlüklere anahtar, kimlik, kişisel veri yazma.
- `tests/test_regressions.py` geçmeden hiçbir değişiklik gönderilmez; bir test başarısızsa testi değil kodu düzelt
  (testi değiştirmek gerekiyorsa sebebini NOTLAR.md'ye yaz).
- Testler canlı kayıt dosyalarındaki (history.json vb.) belirli video kimliklerine/sayılarına bağlanmamalı.
