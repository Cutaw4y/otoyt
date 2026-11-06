# Gölgedefter — Telegram'dan konu seç, ÜRET ile planlı yayına al

Gölgedefter'in hedefi haftada dört uzun video: **Salı, Perşembe, Cumartesi ve Pazar**. Beyond Qaf'ın otomasyonu değişmez.

## Kullanıcının yaptığı iki işlem

1. Yayın gününden önce bot dört farklı konu önerir. Üzerinde **1, 2, 3 veya 4** yazan Telegram cevap klavyesi (ya da aynı rakamı normal mesaj olarak yazmak) yalnızca konuyu seçer. Seçilmediyse video üretilmez.
2. Bot seçilen konuyu tekrar gösterir. Kullanıcı **ÜRET** gönderirse **aynı onay hem üretimi hem YouTube'da planlı yayını kapsar**. Ek bir **Yayınla** sorusu çıkmaz. Uzun video + Shorts mevcut yayın takvimine göre yüklenir/planlanır; mevcut Instagram Reels yolu çalışır.

**ÜRET demeden üretim, yükleme veya harcama başlamaz.** Kanaldaki hedef gün ve saatler geçerli kalır: ÜRET, o anda YouTube'da herkese açık olma sözü değildir; planlı yükleme/yayın iznidir.

Üretim sırasında tutarsızlık, model kotası veya upload hatası olursa otomasyon **ikinci kez kör yükleme yapmaz**. İş güvenle durur ve hata kontrol edilir. Daha önce başlayan eski bir özel ön izleme görevi varsa yalnız onun mevcut eski onay biçimi korunabilir; yeni işler ayrı yayın onayı istemez.

## Yeni konu çizgisi

Konu seçiminde öncelik: **somut araştırma izi + akıl almaz ihtimal**. Mu Kıtası efsanesi, yıldız kapısı anlatıları, Hayu Marca kaya yapısı, Antikythera mekanizması, Atlantis, Derinkuyu, Yonaguni ve Piri Reis haritasına yüklenen olağanüstü iddialar gibi konuların farklı yönleri araştırılır.

Dört önerinin en az üçü kayıp medeniyetler, efsaneler veya antik teknoloji/astronot iddiaları kategorisinden seçilir. Kaf Dağı gibi yalnız fantastik coğrafyayı anlatan konular önerilmez. Daha önce üretilmiş Deccal/Alkarısı videoları tekrarlanmaz. Yeni konular rakip kanalların metinlerinden değil **özgün senaryo ve kaynak incelemesinden** doğar.

Mu Kıtası'nın tarihsel varlığı kanıtlanmış değildir. Yıldız kapısı bulunduğu iddiası, bir kaya yapısının varlığıyla kanıtlanmış sayılmaz. Belgeselde efsane, metin, fiziksel bulgu ve bilimsel/tarihî açıklama açıkça ayrılır.

İlham veren YouTube formatları ve kaynak değerlendirmesi: [Gölgedefter içerik araştırması](golgedefter_topic_research.md).

## Güvenlik ve çalışma sınırları

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` ile yalnızca belirtilen özel sohbet sahibi onay verebilir. Başka kullanıcı/sohbet, eski yanlış düğme ve ikinci tıklama yeni üretim başlatamaz.
- Telegram kararları, iş durumu ve notlar herkese açık git ağacına yazılmaz; GitHub'ın özel draft release kayıtlarında saklanır.
- Bot mesajları GitHub Actions zamanlayıcısı tarafından 24 saat yaklaşık her 5 dakikada bir kontrol edilir; bu gerçek zamanlı webhook değildir, GitHub gecikmeleri olabilir.
- ÜRET onayı yeni durum kaydında `publish_authorized=true` ile kalıcı tutulur. Eskiden verilmiş ve **bu açık izni taşımayan** yalnızca üretim onayı yeni otomatik yayına dönüştürülemez.
- Üretim başladıktan sonra yayın tamamlanmadan zaman aşımı/hata yaşanırsa yeniden yükleme için otomatik tekrar yoktur.
- Mevcut kanal-kimlik doğrulaması, altyazı, yayın saatleri, Shorts hedef kayıtları ve Instagram sırası korunur.
- Shorts'taki gerçek **İlgili video** ayarı bu otomasyonla YouTube Studio'ya yazılamaz. Hazırlanan Studio eşleştirme listesi ayrıdır.
- Google Drive kurtarma PR #5 **ertelenmiştir**. Yeni ücretli API, Drive bağlantısı veya ek kullanıcı hesabı istenmez.

## Testler

GitHub Actions `Kod kontrolü` iki kanalın import/derleme testlerini, tüm ağsız regresyon testlerini ve kayıt denetimini çalıştırır. Test başarıları gerçek üretim/yayını kanıtlamaz; ilk gerçek onaylı üretimden sonra YouTube ve Telegram durumu ayrıca doğrulanmalıdır.
