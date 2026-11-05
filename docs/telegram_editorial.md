# Gölgedefter: Telegram üzerinden konu ve yayın onayı

Gölgedefter için haftada dört video: salı, perşembe, cumartesi ve pazar. Beyond Qaf'ın yayın sıklığı ve
ayarları değişmez. Eski videoların tarihleri bu akış tarafından değiştirilmez.

1. Yayın günlerinden bir önceki akşam Türkiye saatiyle 20:00 sonrasındaki ilk kontrolde, ertesi gün için
   dört konu, kısa özet ve ilgi çekme gerekçesi gönderilir. Doğaüstü varlıklar ve kıyamet anlatıları önceliklidir; Kaf Dağı önerilmez.
2. Bir konu seçilir, ardından **Üretime başla** onayı verilir. Seçim yapılmazsa
   üretim başlamaz. Önceki iş tamamlanmadan ikinci bir iş açılmaz.
3. Yalnız seçilen konu kaynak ve kalite kontrolünden geçirilir; başarısız konu
   otomatik başka konuyla değiştirilmez. Eski yarım senaryo kullanılmaz.
4. Tam video, Shorts ve dosya özeti GitHub **draft release** içinde tutulur.
   Telegram'a tam ön izleme bağlantısı ve **Yayınla / Düzelt / İptal** gelir.
   GitHub'da depo sahibinin oturum açması gerekir. Draft hiçbir zaman yayımlanmaz.
5. Yayınla onayı uzun videoyu ve Shorts'u YouTube'a yükler/planlar; mevcut Reels
   akışını başlatır. Geç kalınan onayda yeni videolar için uygun saat yeniden
   hesaplanır. İptal dosya/video silmez. Düzeltme notu Telegram'a yazılıp Düzelt
   seçilirse aynı konu yeniden üretilir ve tekrar son onay istenir.

## Çalışma sınırları

- Bot mevcut `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` kullanır. Chat ID pozitif
  bir özel sohbet olmalı; yalnız o sohbetin sahibi karar verebilir.
- Mevcut webhook varsa kaldırılmaz; kurulum hata verip bekler. Aynı botun başka
  bir getUpdates tüketicisiyle aynı anda çalıştırılmaması gerekir.
- Kontrol gece dahil 24 saat, yaklaşık her 5 dakikada bir çalışır. GitHub Actions zamanlayıcısı gecikebilir; düğme anlık değildir. Bir seçimden sonra Telegram'da ayrıca **Seçtin / Üretime başlansın mı?** mesajı beklenir. Üretim, ikinci düğmeye basılana kadar başlamaz.
  GitHub cron gecikebilir; üretim ve kontrol ortak sırayı kullandığından video
  üretimi sırasında karar işleme birkaç saat gecikebilir.
- GitHub Actions, Telegram ve Gemini bağlantıları canlı kontrol gerektirir.
  Çevrimdışı testler servis yetkisini veya gerçek render kalitesini kanıtlamaz.
- Haftada dört üretim AI kullanımını artırır. Kontrol işi günde en çok 32 hafif çalışma
  ekler; üretim yalnız sahibin onayından sonra ücretli API kullanır.
- Haftada dört yayın düzeni süresizdir; `trial_days: 0`. Tercihen ilk hafta sonunda
  maliyet ve performans birlikte değerlendirilir.

## Kalıcılık ve hata davranışı

Kararlar, güncelleme konumu ve düzeltme notları herkese açık git ağacına yazılmaz.
`gd-editorial-state` adlı özel draft release'e yeni, değişmez JSON varlıkları
olarak eklenir. Ön izleme paketleri de ayrı özel draft release'lerdir. Draft'ları
**Publish release** ile herkese açmayın. Karar/ön izleme dosyaları otomatik silinmez.

Tüm editorial işlemler `otoyt-uretim` grubunda sırayla çalışır. Her iş kimliği ve
durumu doğrulanır; eski düğme ve çift tıklama yeni üretim/yayın başlatamaz. Masraflı
işlemden önce durumun kalıcı kaydı zorunludur. Yayın onayından sonra indirilen
manifest ve tüm videoların SHA-256 özeti ön izlemeyle karşılaştırılır.

Hiç başlamamış, kuyrukta iptal olmuş bir iş güvenle tekrar sıraya alınabilir.
Başlamış üretim/yayın kesilirse otomatik tekrar durur; kısmi yüklemenin ikinci
kopyası üretilmez. İş kapanabilir veya kayıtlar incelenerek devam ettirilebilir.

Mevcut yükleme yolu korunur: kanal kimliği kontrolü, altyazılar, oynatma listesi,
Shorts'un uzun video hedef kaydı ve Instagram kuyruğu. YouTube Studio'daki
**İlgili video** kilidini bu değişiklik açmaz; kanal doğrulaması yine gereklidir.

## Doğrulama

`python -m unittest discover -s tests -v`

Testler ağ, canlı Telegram mesajı, ücretli üretim veya gerçek yayın yapmaz.
Aktivasyondan sonra önce Telegram kontrol işi çalıştırılarak özel sohbet ve
webhook uygunluğu görülmeli; ilk gerçek üretim ve yayın kullanıcı düğmelerinden
ayrı ayrı onaylanmalıdır.
