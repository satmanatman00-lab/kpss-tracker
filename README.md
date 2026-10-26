# KPSS ilan takipçisi

Python 3.10+; GitHub Actions her saat `:17`'de tarar. Bilgisayarın açık kalmaz.
Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Kimlik bilgileri koda yazılmaz.

## 26 Ekim 2026 mimarisi

- Negatif kelime filtresi tamamen kaldırıldı. Aynı ilan sayfasında **teknisyen/teknisyeni** ve **bilişim/bilgisayar/ortaöğretim/lise/2011** gruplarından bir kelime birlikte varsa eşleşir. Diğer kadrolardaki mühendis, lisans, hemşire vb. kelimeler ilanı elemez.
- Her PDF'nin tüm metni ayrı bir belge olarak değerlendirilir. Ayrı PDF'lerdeki birbirinden bağımsız kelimeler birleştirilmez. HTML başlığı ve ilan gövdesi aynı sayfa olarak birleşir.
- Geniş pozitif kural elektrik teknisyeni + lise ilanını da bildirir; kesin bilişim mezuniyeti veya kişisel uygunluk garantisi vermez. Telegram mesajı bunu **ilan adayı** diye belirtir.
- Menü/yan sütun/footer ayıklanır. Daha önce eklenen Devlet.TC kaynak türü ve geçmiş son başvuru kontrolleri korunur. Bunlar negatif meslek kelimesi filtresi değildir.

## Kaynak sırası ve doğrulanmış sınırlar

1. `https://kamuilan.sbb.gov.tr/` ilk denenir. Sayfanın gerçek 4/B sekme bağlantısı varsa izlenir; JavaScript filtresiyse ana listedeki detaylar değerlendirilir. Kategori numarası uydurulmaz. **26 Ekim kontrolünde bu ortamdan SBB HTTP 403 verdi; canlı HTML ayrıştırması doğrulanamadı.** Kod bağlantı yapısı değişirse açık uyarı verir. SBB erişimi garanti değildir.
2. `MEMURLAR_RSS_URLS`: virgülle ayrılmış Memurlar.net RSS adresleri. **Çalışan resmî Memurlar.net beslemesi doğrulanamadığı için varsayılan adres yoktur.** Bu değişken boşsa kaynak uyarıyla devre dışıdır. Memurlar.net detay HTML'sine, eski kuyruktaki URL'lere de, istek yapılmaz. RSS açıklama/content ve PDF ekleri kullanılır; kısa içerik eşleşmiyorsa kesin ret yerine beklemeye alınır.
3. Mevcut bağımsız yedekler: `https://devlet.tc/tr/kamu-ilanlari/rss.xml` ve `https://www.isinolsa.com/sozlesmeli-personel-alimi/feed/`. Bunlar resmî kurum sitesi değildir. `RSS_URLS` repository variable tanımlanırsa bu yedek listeyi değiştirir. Tüm RSS indirmelerini zaman aşımı olan HTTP istemcisi yapar; `feedparser` yalnız XML'yi ayrıştırır.

HTTP istemcisi tarayıcı biçimli User-Agent, Accept ve Accept-Language gönderir.
Bunlar 403/Cloudflare çözme garantisi değildir. Proxy/CAPTCHA aşma yoktur.
Robots kuralları ve saniyede en fazla bir istek/host sınırı korunur.

## PDF okuma

- Sayfadaki `.pdf` bağlantıları (büyük/küçük uzantı ve query dahil), iframe/embed/object ve RSS PDF enclosure'ları bulunur.
- Dosyalar `requests` ile sınırlı stream olarak indirilir; `BytesIO` ve `pypdf` ile bellekte okunur, diske yazılmaz.
- Dosya başına en fazla 12 MiB, 80 sayfa; ilan başına 5 PDF. Sınır aşılması/bozuk/şifreli dosya/timeout diğer ilanları durdurmaz.
- **OCR yoktur:** taranmış görsel PDF'nin metni çıkarılamaz. Okunamayan ek, pozitif eşleşme de yoksa ilanı `pending` bırakır; sonraki tur tekrar denenir. Okunabilen HTML/PDF'de pozitif eşleşme varsa diğer ekte hata olsa bile bildirilir.
- Yalnız PDF uzantısı veya PDF enclosure ile belirtilmiş ekler keşfedilir; JavaScript ile üretilen indirmeler ve uzantısız dosya indirme sayfaları kapsam dışı olabilir. Doğrudan PDF ilan URL'si Content-Type veya PDF imzasından tanınır.
- ÖSYM tercih kılavuzlarını ayrıca keşfeden bir modül yoktur.

## Kurulum / kullanım

1. Telegram'da resmî `@BotFather` hesabına `/newbot` yaz. Oluşturduğun botu açıp **Başlat**'a bas ve mesaj at.
2. Sohbet ID'ni Telegram Bot API `getUpdates` yanıtındaki `message.chat.id` alanından bul. Tokenı herkese açık URL/mesaj/koda yazma.
3. Dosyaları GitHub reposuna koy. Repo **Settings → Secrets and variables → Actions** bölümüne `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID` secrets ekle.
4. Workflow `contents: write` ister. Repo ayarlarının ve varsa branch korumasının botun `seen_ads.json` dosyasını commit/push etmesine izin vermesi gerekir.
5. Gerekirse **Variables** bölümüne `RSS_URLS` / `MEMURLAR_RSS_URLS` ekle; içine yalnız kamuya açık, doğrulanmış besleme adresleri koy.
6. **Actions → KPSS ilan takipçisi → Run workflow**. İsteğe bağlı Telegram test kutusu tek bağlantı mesajı gönderir. Normal cron bunu göndermez.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scraper.py --dry-run
```

`--dry-run` bildirim göndermez ve hafızayı yazmaz. `--baseline` mevcut eşleşmeleri sessizce kaydeder; her tur 35 ilan sınırı nedeniyle kuyruk bitene kadar kullanılmalıdır. Gerçek çalışmada iki Telegram ortam değişkeni gerekir. `STATE_FILE` hafıza dosyasının yolunu değiştirebilir.

## Kalıcılık ve hata davranışı

- Mevcut `seen_ads.json` sıfırlanmaz. URL/ilan ID üzerinden tekilleştirilir. Gönderilmiş kayıtlar tekrar gönderilmez. Farklı sitelerdeki aynı ilan, ayrı URL olduğu için tekrar gelebilir.
- Yeni filtre sürümü eski reddedilmiş kayıtları tekrar değerlendirir. Sonrasında reddedilenler 24 saatte bir kontrol edilir. Her tur en fazla 35 detay ve yaklaşık 7 dakika tarama bütçesi vardır; PDF/ağ işlemleri bütçeyi bir miktar aşabilir. Kuyruk adil sırayla ilerler.
- Telegram `ok: true` dönerse `notified`; gönderim hatasında `pending`. Her denemeden sonra JSON atomik kaydedilir. Bozuk JSON sıfırlanmaz; tur durur.
- Workflow kısmi hatada da JSON'u commit/push eder; eşzamanlı çalışmalar aynı dosyada yarışmaz. Push rebase ile denenir; force-push yapılmaz.
- En az bir kaynak ve denenmiş ilanlardan en az biri okunursa kısmi kaynak hataları uyarı sayılır. Hiç kaynak/detay okunamaması veya Telegram hatası işi başarısız yapar. PDF hataları ayrıca uyarı verir; yeşil iş bütün kaynakların/eklerin okunduğu anlamına gelmez.
- Mesaj gönderimi ile Git commit ortak işlem değildir: mesajdan sonra runner/push çökerse nadir tekrar olabilir. Tam exactly-once garantisi yoktur.
- Public hafızada kamu ilanı URL/başlık/durum/zamanları ve yeniden denemek için RSS metni/PDF bağlantıları tutulur. Bot tokenı veya sohbet ID'si tutulmaz.

## Ücret ve zamanlama

Standart Ubuntu runner kullanan public repolar için ücretsiz Actions kapsamı hedeflenir. Private repo veya ücretli runner kota/ücret doğurabilir. Cron saatliktir; anlık bildirim değildir, GitHub kuyruğunda gecikebilir. Türkiye saati her saat `:17`. Zamanlanmış işler uzun süre hareketsiz public repolarda kapanabilir; Actions ekranını ara sıra kontrol et.

## Doğrulama

31 otomatik test: karma ilanlarda negatif veto olmaması, iki pozitif grup, Türkçe normalizasyon, SBB bağlantıları, RSS/Atom ve Memurlar HTML'sine hiç gitmeme, gerçek metin PDF'si, görsel/bozuk PDF, ek hatasında pending, URL tekilleştirme, hafıza, Telegram hataları ve gönderim sonrası tekrar etmeme. Testler ağ/gerçek Telegram kullanmaz. SBB testleri sentetik HTML kullanır; canlı erişim doğrulaması değildir.
