# KPSS Ortaöğretim Bilişim İlan Takipçisi

Saatte bir ilanları kontrol eder, eşleşen yeni ilanı Telegram'a gönderir ve
hafızayı GitHub reposuna commit/push ile kaydeder. Bilgisayarının açık kalması
gerekmez. Token ve chat ID kaynak koduna yazılmaz.

## Hızlı kurulum

1. Telegram'da doğrulanmış [@BotFather](https://t.me/BotFather) hesabını aç.
   `/newbot` yaz; botun adını ve `bot` ile biten kullanıcı adını seç. Verilen
   tokenı sakla. Kendi botunu açıp **Başlat / Start** düğmesine bas ve bir mesaj gönder.
2. Chat ID'ni bul. Bilgisayarda aşağıdaki komutu çalıştır; token gizli girişle
   sorulur, terminal geçmişine veya kaynak dosyasına yazılmaz. Botuna gönderdiğin
   mesajdaki `message.chat.id` değerini kullan. Kod yalnızca sohbet ID'lerini basar.

   ```bash
   python - <<'PY'
   import getpass, json, urllib.request
   token = getpass.getpass('Bot token: ').strip()
   try:
       with urllib.request.urlopen(
           'https://api.telegram.org/bot' + token + '/getUpdates', timeout=20
       ) as response:
           data = json.load(response)
       if not data.get('ok'):
           print('Telegram isteği başarısız. Tokenı kontrol et.')
       for update in data.get('result', []):
           message = update.get('message', {})
           chat = message.get('chat', {})
           if 'id' in chat:
               print('CHAT_ID:', chat['id'], 'Tür:', chat.get('type'))
       if not data.get('result'):
           print('Botuna şimdi mesaj gönder, sonra bu komutu tekrar çalıştır.')
   except Exception:
       print('İstek başarısız. Tokenı, interneti ve varsa webhook ayarını kontrol et.')
   PY
   ```

   Yeni bot için webhook kurma. Mevcut botta webhook varsa `getUpdates` çalışmaz;
   bu proje için ayrı bir bot açmak kolaydır. Telegram gelen güncellemeleri en
   fazla 24 saat tutar; ID bulunamazsa botuna yeni mesaj at.
3. GitHub'da yeni bir **Public** repo aç: örneğin `kpss-tracker`. Standart
   `ubuntu-latest` runner kullanan public repolarda Actions ücretsizdir. Private
   repo kullanımının aylık ücretsiz kotası vardır; kotayı aşmak ücret doğurabilir.
4. ZIP'i aç. İçindeki `kpss-tracker` klasörünün **içeriğini** repo köküne koy:

   ```text
   scraper.py
   requirements.txt
   seen_ads.json
   README.md
   .gitignore
   .github/workflows/tracker.yml
   tests/test_scraper.py
   ```

   `.github` klasörü gizli görünebilir. GitHub'da gerekirse **Add file → Create
   new file** üzerinden `.github/workflows/tracker.yml` yoluyla oluştur.
   `scraper.py` bir üst klasörde kalmamalı. Dosyaları varsayılan dala, genellikle
   `main`, commit et. ZIP dosyasını olduğu gibi repoya yüklemek kurulumu yapmaz.
5. Repoda **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN`: BotFather'ın verdiği token.
   - `TELEGRAM_CHAT_ID`: az önce bulduğun ID; varsa baştaki eksi işaretini koru.
6. **Settings → Actions → General → Workflow permissions** bölümünde
   **Read and write permissions** seçeneğine izin ver. Workflow ayrıca
   `contents: write` ister. Varsayılan dalın ruleset/branch protection kuralı botun
   doğrudan push yapmasını engelliyorsa bu repo için uygun push izni ver; kod
   bu korumayı zorla aşmaz. PAT gerekmiyor, yerleşik `GITHUB_TOKEN` kullanılır.
7. **Actions → KPSS ilan takipçisi → Run workflow** ile ilk çalışmayı başlat.
   Logdaki kaynak/detay sayısına ve `seen_ads.json` commit'ine bak. Telegram'a
   yalnızca eşleşme varsa mesaj gelir; hiç mesaj gelmemesi tek başına hata değildir.
   Bundan sonra cron her saatin **17. dakikasında** tetiklenir.

## Filtre tam olarak ne yapıyor?

- Pozitif OR: `teknisyen`, `bilişim`, `bilgisayar`, `ortaöğretim`, `lise`, `2011`.
- Negatif veto: `mühendis`, `lisans`, `önlisans`, `öğretmen`, `doktor`, `hemşire`,
  `sağlık teknikeri`. Bunlardan biri başlık veya ilan gövdesindeyse ilan elenir.
- Türkçe büyük/küçük harf ve ASCII yazımları normalize edilir. Kök/alt dize
  eşleşmesi kullanılır; örneğin `mühendisliği` de negatif sayılır.
- Sadece lise bilişim hedefini daraltmak için ek kapı varsayılan olarak açıktır:
  aynı cümlede lise/ortaöğretim, bilişim/bilgisayar ve mezun/bölüm/alan ifadesi
  veya nitelik/kod bağlamında `2011` aranır. `2011 yılında` tek başına yeterli olmaz.
- Yalnızca senin yazdığın ham kelime filtresini istiyorsan workflow'daki
  `REQUIRE_IT_SECONDARY: 'true'` değerini `'false'` yap. O mod, elektrik teknisyeni
  veya lise destek personeli gibi alan dışı ilanları da bildirebilir.

**Katı negatif filtre karma ilanları kaçırır.** Örneğin aynı ilanda lise bilişim
teknisyeni ve lisans hemşire kadrosu varsa tüm ilan elenir. `lisans mezunu
olmamak` ifadesi de veto edilir. Bu, talep edilen global eleme kuralının bilinçli
sonucudur. Satır bazında kadro değerlendirmesi bu sürümde yok.

Bildirimler kesin başvuru uygunluğu değil **ilan adayı** gösterir. Meslek alanı,
KPSS yılı/puanı, yaş, belge, son başvuru tarihi ve benzeri şartları bağlantıdan
kontrol et. Kurum başlıktan çıkarılır; açık unvan bulunamazsa uydurulmaz.

## Kaynak ve kapsam

Varsayılan kaynaklar Memurlar.net ana ilan sayfası, 4/B kategorisinin ilk iki
sayfası ve yayındaki KPSS-B ilanlarıdır. İlan başlığı filtreye uymasa bile detay sayfası okunur; genel başlıklı
üniversite ilanları böyle yakalanabilir. Menü/footer ve yan sütun metinleri
ayıklanır. Semantik gövde bulunamazsa H1 ile ilan sonundaki başlıklar arası okunur.

RSS/Atom desteği de var. GitHub'da **Settings → Secrets and variables → Actions →
Variables** bölümüne `RSS_URLS` adlı bir **repository variable** ekleyip, kendi
doğruladığın erişilebilir kamu ilanı RSS adreslerini virgülle ayırarak yazabilirsin.
İçine token veya kişisel oturum bağlantısı koyma. Besleme, ilan keşfinde kullanılır;
özetin eksik olması riskine karşı HTML detay sayfası ayrıca okunur. Yeni kaynağın
HTML düzeni farklıysa `BODY_SELECTORS` veya `parse_detail` uyarlaması gerekebilir.
Varsayılan bağımsız kaynaklar Devlet.TC kamu ilanları RSS
(`https://devlet.tc/tr/kamu-ilanlari/rss.xml`) ve İşin Olsa RSS
(`https://www.isinolsa.com/sozlesmeli-personel-alimi/feed/`) beslemeleridir. Bunlar bağımsız haber/ilan
rehberleridir, resmî kamu kurumu değildir. RSS_URLS boşsa bu iki kaynak kullanılır;
doluysa yazdığın liste kullanılır.

403, CAPTCHA veya robots yasağında kaynak atlanır; engel aşma/proxy kullanımı yok.
Bir kaynağın bugün açık olması GitHub runner IP'sinden sürekli açık kalacağı
anlamına gelmez. PDF veya görsel içindeki kadro tabloları bu sürümde OCR ile
okunmaz. ÖSYM merkezi tercih kılavuzlarını/PDF tablolarını ayrıca izleyen bir
modül yoktur; sistem web ilan metninde geçen 2011 kodlarını arar. Tüm 2011
kadrolarını eksiksiz bulma garantisi yoktur.

İlk tur geçmişte yayımlanmış ama listede bulunan ilanları da değerlendirebilir;
son başvuru tarihi filtresi uygulanmaz. Her tur en fazla 35 detay işlenir;
kalanlar kuyrukta saklanır ve sonraki turlarda devam edilir.

## Hafıza ve hata davranışı

- `seen_ads.json` repo içinde izlenir; `.gitignore` içine alınmamalı.
- Memurlar.net ilan ID'siyle tekilleştirme yapılır; `?amp=1` gibi varyantlar
  ayrı bildirim oluşturmaz. Başka kaynaklarda URL bazında tekilleştirilir;
  aynı ilanın farklı sitelerdeki kopyaları ayrı bildirim olabilir.
- Telegram `ok: true` döndürdükten sonra `notified` yazılır. Gönderim hataları
  `pending` kalır, sonraki turda yeniden denenir. Liste dışına çıkan bekleyen
  detaylar da kuyruktan kaybolmaz.
- Negatif filtrelenen ilanlar `rejected` olur ve 24 saat sonra yeniden okunur.
  Filtre/parser sürümü değişirse hemen yeniden değerlendirilir.
- Gönderilmiş ilanlar tekrar gönderilmez; aynı ID'deki sonradan yapılan
  düzeltmeler için ayrıca bildirim yoktur.
- Her işlenen detaydan sonra JSON atomik yazılır. Bozuk JSON otomatik olarak
  sıfırlanmaz; yanlışlıkla tüm ilanları tekrar göndermemek için tur durdurulur.
- Workflow, scraper hata kodu döndürse de değişen JSON'u commit/push eder.
  Aynı anda iki workflow JSON'a yazmaz. Push'ta normal kod/README değişiklikleri
  için rebase denenir; JSON üzerinde gerçek çakışma varsa force-push yapılmaz.
- Kaynak hatası diğer kaynakları durdurmaz. En az bir kaynak ve bekleyen
  detaylardan en az biri okunabiliyorsa kısmi kaynak hataları uyarı olarak loglanır.
  Hiç kaynak okunamazsa, denenen detayların hiçbiri okunamazsa veya Telegram
  gönderimi başarısızsa Actions kırmızı görünür.
- Token, chat ID ve hata yanıtı loglanmaz. Public repodaki JSON sadece kamu ilanı
  URL/ID, başlık, durum ve zaman damgalarını içerir.

**Tam bir exactly-once garantisi yok.** Telegram mesajı alıp yanıtı kaybolursa
veya mesajdan sonra runner kapanır/push başarısız olursa nadiren tekrar mesaj
gelebilir. Telegram gönderimiyle Git commit'i ortak bir atomik işlem yapamaz.

## Zamanlama ve ücretsiz çalışma

- Saatlik cron gerçek zamanlı bildirim değildir. GitHub kuyruğu gecikebilir ve
  yoğunlukta tetikleme atlanabilir. Dakika `17`, saat başı yoğunluğundan kaçınır.
- Cron varsayılan dalda çalışır. Elle farklı bir daldan çalıştırsan bile bu
  workflow kodu ve hafızayı varsayılan daldan alır.
- Sadece hafta içi Türkiye saati 08:17–17:17 çalışması için cron'u
  `'17 5-14 * * 1-5'` yapabilirsin; cron UTC kullanır.
- Public repoda 60 gün etkinlik olmazsa GitHub zamanlanmış workflow'u kapatabilir.
  Durum commit'leri oluşsa da çalışmayı ara sıra Actions ekranından kontrol et;
  kapatılmışsa yeniden etkinleştir.
- Standart Ubuntu runner kullanılır; ücretli büyük runner, harici sunucu,
  ücretli API veya Actions artifact depolaması gerekmez.

## Yerelde deneme

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scraper.py --dry-run
```

`--dry-run` Telegram'a göndermez ve JSON'u değiştirmez. Gerçek çalışmada iki
Telegram ortam değişkenini tanımlayıp `python scraper.py` çalıştır. Başlangıçta
mevcut eşleşmeleri sessizce kaydetmek istersen `--baseline` var; her tur 35 detay
sınırı geçerli olduğu için kuyruk tamamlanana kadar bu modda çalıştırmalısın.
Normal moda geçildikten sonra bekleyen ilanlar bildirim adayıdır.

## Doğrulama ve bilinen sınır

20 otomatik test; Türkçe normalizasyonu, negatif veto, alan/eğitim kapısı,
RSS/Atom, ID tekilleştirme, gövde ayrıştırma, atomik kayıt, bozuk hafıza, Telegram
429/timeout, tokenın loglanmaması ve ardışık çalışmalarda yeniden deneme/tekrar
göndermeme davranışlarını doğrular. Memurlar.net engelliyken sağlıklı RSS üzerinden
bildirimin devam ettiği de test edilir. Testler ağ ve gerçek Telegram hesabı kullanmaz.

Canlı `--dry-run` denemesinde üç liste kaynağı okundu (sırasıyla 35, 25 ve 25
bağlantı; kaynaklar arasında tekrarlar var), ilk 35 ilan detayı ayrıştırıldı ve
çalışma hatasız tamamlandı. Sonradan eklenen KPSS-B kaynağı da canlı olarak
okundu. Bu örneklemde filtreye uyan ilan çıkmadı. Deneme
Telegram mesajı göndermedi ve boş başlangıç hafızasını değiştirmedi. Workflow
YAML/Bash sözdizimi ve gerçek bir geçici Git reposunda JSON commit/push ile
kalıcılığı da kontrol edildi. GitHub üzerinde ilk gerçek çalışmada Telegram test mesajı başarıyla
teslim edildi ve JSON commit/push adımı başarılı oldu. Memurlar.net GitHub runner
IP'sine HTTP 403 döndürdüğü için bağımsız RSS kaynakları eklendi. Memurlar.net
taraması loglarda uyarıyla devam eder; erişim açılırsa yeniden çalışır.

Elle çalıştırırken **Telegram bağlantısını test et** seçeneği işaretlenirse
ilandan bağımsız tek bir bağlantı test mesajı gönderilir. Saatlik cron çalışmaları
bu test mesajını göndermez.

## Resmî teknik kaynaklar

- [GitHub Actions ücretlendirme](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Schedule davranışı ve gecikmeler](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Workflow etkinleştirme/devre dışı bırakma](https://docs.github.com/actions/managing-workflow-runs/disabling-and-enabling-a-workflow)
- [Telegram BotFather başlangıcı](https://core.telegram.org/bots/tutorial#obtain-your-bot-token)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Memurlar.net 4/B ilanları](https://ilan.memurlar.net/kategori/4b-ilanlari/)
