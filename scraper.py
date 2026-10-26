#!/usr/bin/env python3
"""KPSS ilan takipçisi. Python 3.10+, tek sefer çalışır; zamanlamayı Actions yapar.

Sadece ilan başlığı + ayrıştırılmış ilan gövdesi filtrelenir. Menü, ilgili
ilanlar ve footer filtreye girmez. PDF/görsel/OCR bu sürümün kapsamında değil.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup, NavigableString
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger("kpss")
ROOT = Path(__file__).resolve().parent
MEMURLAR = "https://ilan.memurlar.net"
USER_AGENT = "KPSSTracker/1.0 (personal job notification bot)"
POSITIVE = ("teknisyen", "bilişim", "bilgisayar", "ortaöğretim", "lise", "2011")
NEGATIVE = ("mühendis", "lisans", "önlisans", "öğretmen", "doktor", "hemşire", "sağlık teknikeri")
LIST_URLS = (
    MEMURLAR + "/default.aspx",
    MEMURLAR + "/kategori/4b-ilanlari/",
    MEMURLAR + "/kategori/4b-ilanlari/2.sayfa",
    MEMURLAR + "/kategori/kpss-b-ilanlari/?Expire=false",
)
# Bağımsız beslemeler: Memurlar.net runner IP'sine kapalıysa da tarama sürer.
DEFAULT_RSS_URLS = (
    "https://devlet.tc/tr/kamu-ilanlari/rss.xml",
    "https://www.isinolsa.com/sozlesmeli-personel-alimi/feed/",
)
# Bunlar değişirse reddedilen ilanlar bir sonraki turda yeniden değerlendirilir.
PARSER_VERSION = "3"
BODY_SELECTORS = (
    '[itemprop="articleBody"]', "#ilan-content", ".ilan-content",
    ".ilan-metni", ".ilan-detay-icerik", "#haber_detay", ".haber_detay",
    ".entry-content", ".k-article__body",
    "article",
)
STOP_HEADINGS = {"kategoriler", "yeni ilanlar", "ilgili ilanlar", "anket"}


class SourceError(Exception):
    """Geçici erişim, robots veya ayrıştırma hatası; diğer kaynaklar devam eder."""


def normalize(text: str) -> str:
    text = text.replace("İ", "i").replace("ı", "i").casefold()
    text = "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def matches(text: str, require_it_secondary: bool = True) -> bool:
    """İstenen pozitif OR + negatif veto; Türkçe/ASCII yazımları eşdeğer.

    Kelime kökü eşleşmesi bilinçlidir: 'mühendisliği' de elenir. Negatifler
    TÜM ilan metninde aranır; karma ilanları kaçırma pahasına istenen katı kural.
    Ek alan/eğitim kapısı elektrik teknisyeni gibi alakasız eşleşmeleri azaltır.
    """
    text = normalize(text)
    if any(normalize(word) in text for word in NEGATIVE):
        return False
    if not any(normalize(word) in text for word in POSITIVE):
        return False
    if not require_it_secondary:
        return True
    # 2011 tarihini nitelik kodu sanma. Kod veya açık lise + bilişim mezuniyeti.
    code = re.search(r"\b(?:nitelik|kod)[^.\n]{0,80}\b2011\b", text)
    # Bilgisayar kullanma/Office/sertifika şartı bir bilişim diploması değildir.
    # Alan/bölüm/dal ifadesi doğrudan bilişim veya bilgisayara bağlanmalıdır.
    it_department = re.search(
        r"\b(?:bilisim(?: teknolojileri)?|bilgisayar(?: programciligi| teknolojileri|"
        r" teknolojisi| donanim| yazilim| ag isletmenligi)?)\s+"
        r"(?:alan\w*|bolum\w*|dal\w*)\b", text,
    )
    qualification = (
        re.search(r"\b(?:lise\w*|orta\s?ogretim)\b", text)
        and it_department
        and re.search(r"\bmezun\w*\b", text)
    )
    return bool(code or qualification)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError("Geçersiz HTTP(S) URL")
    # Sadece izleme parametreleri silinir; ilanı belirleyen query korunur.
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_")
                       and k.lower() not in {"amp", "fbclid", "gclid"}])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


def ad_key(url: str) -> str:
    parts = urlsplit(url)
    m = re.match(r"/ilan/(\d+)(?:/|$)", parts.path)
    if parts.hostname == "ilan.memurlar.net" and m:
        return "memurlar:" + m.group(1)
    return canonical_url(url)


@dataclass
class Ad:
    url: str
    title: str
    source: str
    body: str = ""
    exclusion_reason: str = ""


def deadline_is_past(value: str, now: datetime | None = None) -> bool:
    """Makine okunabilir son başvuruyu Türkiye saatine göre değerlendir."""
    try:
        deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        deadline = deadline.replace(hour=23, minute=59, second=59, microsecond=999999)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=ZoneInfo("Europe/Istanbul"))
    return deadline < (now or datetime.now(timezone.utc))


class Web:
    """GET için sınırlı retry, hız sınırı ve robots.txt denetimi."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        retry = Retry(total=1, backoff_factor=1, status_forcelist=(500, 502, 503, 504),
                      allowed_methods=frozenset({"GET"}), respect_retry_after_header=False)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.robots: dict[str, RobotFileParser | None] = {}
        self.last_request: dict[str, float] = {}

    def _raw(self, url: str) -> requests.Response:
        origin = urlunsplit((*urlsplit(url)[:2], "", "", ""))
        time.sleep(max(0.0, 1.0 - (time.monotonic() - self.last_request.get(origin, 0))))
        try:
            # Yönlendirme başka sunucuya giderse onun robots kuralını atlama.
            response = self.session.get(url, timeout=(5, 15), allow_redirects=False)
        except requests.RequestException:
            raise SourceError("Ağ hatası / timeout") from None
        finally:
            self.last_request[origin] = time.monotonic()
        return response

    def get(self, url: str, redirects: int = 0) -> requests.Response:
        url = canonical_url(url)
        origin = urlunsplit((*urlsplit(url)[:2], "", "", ""))
        if origin not in self.robots:
            # Belirsiz/engelli robots durumunda o tur kaynağı tarama.
            self.robots[origin] = None
            response = self._raw(origin + "/robots.txt")
            parser = RobotFileParser(origin + "/robots.txt")
            if response.status_code in (404, 410):
                parser.parse([])
            elif response.status_code == 200:
                if "<html" in response.text[:500].lower():
                    raise SourceError("robots.txt yerine HTML döndü")
                parser.parse(response.text.splitlines())
            else:
                raise SourceError(f"robots.txt HTTP {response.status_code}; kaynak atlandı")
            self.robots[origin] = parser
        parser = self.robots[origin]
        if parser is None or not parser.can_fetch(USER_AGENT, url):
            raise SourceError("robots.txt erişimi yasaklıyor veya doğrulanamadı")
        response = self._raw(url)
        if response.is_redirect:
            if redirects >= 4 or not response.headers.get("Location"):
                raise SourceError("Yönlendirme sınırı / eksik Location")
            return self.get(urljoin(url, response.headers["Location"]), redirects + 1)
        if response.status_code != 200:
            raise SourceError(f"HTTP {response.status_code}")
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response


def parse_listing(markup: str, base: str) -> list[Ad]:
    soup = BeautifulSoup(markup, "html.parser")
    ads: dict[str, Ad] = {}
    for link in soup.select("a[href]"):
        try:
            url = canonical_url(urljoin(base, link["href"]))
        except ValueError:
            continue
        if urlsplit(url).hostname != "ilan.memurlar.net":
            continue
        if not re.match(r"/ilan/\d+/", urlsplit(url).path):
            continue
        title = link.get_text(" ", strip=True) or link.get("title", "")
        if title:
            ads.setdefault(ad_key(url), Ad(url, title[:500], "Memurlar.net"))
    if not ads:
        raise SourceError("Liste boş: sayfa yapısı değişmiş veya erişim engellenmiş olabilir")
    return list(ads.values())


def parse_detail(markup: str, ad: Ad) -> Ad:
    soup = BeautifulSoup(markup, "html.parser")
    heading = soup.find("h1")
    if heading:
        ad.title = heading.get_text(" ", strip=True)[:500]
    # Devlet.TC'nin semantik ilan etiketleri: şirket işçisi ≠ memur / 4/B.
    # Bu kontrol, genel metindeki 'işçi' kelimesine uygulanmaz; yalnız kategoriye.
    article = soup.select_one("article[data-k-article]")
    if article:
        reasons = []
        job_header = article.select_one(".k-jobhead")
        category = article.select_one(".k-jobhead a[href*='/kamu-ilanlari/tur/']")
        if category:
            kind = urlsplit(category.get("href", "")).path.rstrip("/").rsplit("/", 1)[-1]
            if kind not in {"memur", "sozlesmeli"}:
                reasons.append("Hedef dışı ilan türü: " + category.get_text(" ", strip=True))
        expired = job_header and "sure doldu" in normalize(job_header.get_text(" ", strip=True))
        for stamp in article.select("time[datetime]"):
            label = normalize(stamp.parent.get_text(" ", strip=True))
            if ("son basvuru" in label or "basvuru suresi doldu" in label):
                expired = expired or deadline_is_past(stamp.get("datetime", ""))
        if expired:
            reasons.append("Başvuru süresi dolmuş")
        ad.exclusion_reason = "; ".join(reasons)
    for node in soup.select("script,style,nav,footer,header,aside,form,iframe"):
        node.decompose()
    body = None
    for selector in BODY_SELECTORS:
        node = soup.select_one(selector)
        if node and len(node.get_text(" ", strip=True)) >= 80:
            body = node.get_text("\n", strip=True)
            break
    if body is None and heading:
        # Semantik container yoksa H1'den ilan sonuna kadar metni topla.
        pieces = []
        for node in heading.next_elements:
            if getattr(node, "name", None) in {"h1", "h2", "h3", "h4"}:
                if normalize(node.get_text(" ", strip=True)) in STOP_HEADINGS:
                    break
            if isinstance(node, NavigableString):
                value = str(node).strip()
                if normalize(value) in STOP_HEADINGS:
                    break
                if value:
                    pieces.append(value)
        body = "\n".join(pieces)
    if not body or len(body) < 80:
        raise SourceError("İlan gövdesi okunamadı; kayıt görüldü sayılmadı")
    ad.body = body
    return ad


def parse_feed(content: bytes, base: str) -> list[Ad]:
    feed = feedparser.parse(content)  # URL açmayı feedparser değil timeout'lu Web yapar.
    if not feed.entries:
        if feed.get("version") and not feed.get("bozo"):
            return []  # Geçerli ama henüz kaydı olmayan RSS.
        raise SourceError("Geçersiz/boş RSS veya Atom yanıtı")
    ads = []
    for entry in feed.entries:
        if not entry.get("link") or not entry.get("title"):
            continue
        try:
            ads.append(Ad(canonical_url(urljoin(base, entry.link)), entry.title[:500], base))
        except ValueError:
            continue
    if not ads:
        raise SourceError("RSS girdilerinde geçerli başlık/link bulunamadı")
    return ads


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "ads": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("ads"), dict):
        raise ValueError("seen_ads.json biçimi geçersiz; otomatik sıfırlanmadı")
    for key, item in state["ads"].items():
        if not isinstance(key, str) or not isinstance(item, dict):
            raise ValueError("Geçersiz ilan kaydı; hafıza korunuyor")
        if item.get("status") not in {"pending", "rejected", "notified", "baseline"}:
            raise ValueError("Geçersiz durum; hafıza korunuyor")
        canonical_url(item["url"])
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)  # Yarım JSON yazılmasını engeller.


def institution(title: str) -> str:
    # Kesin kurum alanı yoksa başlıktan tahmin; bilgi uydurma.
    value = re.split(r"\s+\d+\s+|\s+(?:sözleşmeli|Sözleşmeli|personel|Personel)\s+", title, maxsplit=1)[0]
    return value[:250] or title[:250]


def telegram_message(ad: Ad) -> str:
    n = normalize(ad.title + " " + ad.body)
    role = "Teknisyen" if "teknisyen" in n else "Unvan için ilan metnini kontrol et"
    return (
        "🔔 <b>Yeni KPSS ilan adayı</b>\n\n"
        f"🏛 <b>Kurum (başlıktan):</b> {html.escape(institution(ad.title))}\n"
        f"💼 <b>Kadro:</b> {html.escape(role)}\n"
        f"📌 <b>Başlık:</b> {html.escape(ad.title)}\n\n"
        f'🔗 <a href="{html.escape(ad.url, quote=True)}">İlanı aç</a>\n\n'
        "Kelime filtresi eşleşti. Bölüm, KPSS yılı/puanı ve diğer şartları ilandan doğrula."
    )


def send_telegram(ad: Ad, token: str, chat_id: str) -> bool:
    # POST'a otomatik ağ retry uygulama: yanıt kaybında çift mesaj riski var.
    # Token URL'de bulunduğu için exception/response gövdesi asla loglanmaz.
    for attempt in range(2):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": telegram_message(ad),
                      "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}},
                timeout=(5, 20),
            )
            payload = response.json()
            if response.status_code == 200 and payload.get("ok") is True:
                return True
            if response.status_code == 429 and attempt == 0:
                wait = int(payload.get("parameters", {}).get("retry_after", 5))
                if 0 <= wait <= 30:
                    time.sleep(wait + 1)
                    continue
            LOG.warning("Telegram gönderilemedi (HTTP %s); sonraki tur denenecek", response.status_code)
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            LOG.warning("Telegram ağ/JSON hatası; sonraki tur denenecek")
        return False
    return False


def process_ad(ad: Ad, state: dict, token: str, chat_id: str, *,
               require_it_secondary: bool, baseline: bool, dry_run: bool,
               filter_id: str, now: float) -> bool:
    """False yalnızca gönderim başarısızsa. Başarısız gönderim pending kalır."""
    selected = not ad.exclusion_reason and matches(ad.title + "\n" + ad.body, require_it_secondary)
    if dry_run:
        LOG.info("DRY RUN eşleşme=%s %s", selected, ad.url)
        return True
    entry = state["ads"][ad_key(ad.url)]
    entry.update(checked_at=now, filter_id=filter_id)
    if not selected:
        entry["status"] = "rejected"
        entry["rejection_reason"] = ad.exclusion_reason or "Alan/eğitim veya anahtar kelime filtresi eşleşmedi"
    elif baseline:
        entry["status"] = "baseline"
    elif send_telegram(ad, token, chat_id):
        entry.update(status="notified", notified_at=datetime.now(timezone.utc).isoformat())
        LOG.info("Bildirim gönderildi: %s", ad.url)
    else:
        entry["status"] = "pending"
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Bildirim ve dosya yazımı yapma")
    parser.add_argument("--baseline", action="store_true", help="İlk kurulum: mevcut ilanları sessizce işle")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # HTTP istemcisi token içeren Telegram URL'sini kendi loguna basmasın.
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not (args.dry_run or args.baseline) and (not token or not chat_id):
        LOG.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlanmamış")
        return 2
    state_path = Path(os.getenv("STATE_FILE", str(ROOT / "seen_ads.json")))
    require_it = os.getenv("REQUIRE_IT_SECONDARY", "true").lower() != "false"
    filter_id = hashlib.sha256(json.dumps([POSITIVE, NEGATIVE, require_it, PARSER_VERSION]).encode()).hexdigest()[:16]
    try:
        state = load_state(state_path)
    except (OSError, ValueError, KeyError, TypeError):
        LOG.error("Hafıza okunamadı; bildirim gönderilmedi ve dosya sıfırlanmadı")
        return 2
    web, good_sources, failures = Web(), 0, 0
    delivery_failures, parsed_details = 0, 0
    deadline = time.monotonic() + 420
    rss_urls = [u.strip() for u in os.getenv("RSS_URLS", "").split(",") if u.strip()]
    if not rss_urls:
        rss_urls = list(DEFAULT_RSS_URLS)
    try:
        for url, kind in [(u, "html") for u in LIST_URLS] + [(u, "rss") for u in rss_urls]:
            if time.monotonic() >= deadline:
                break
            try:
                response = web.get(url)
                ads = parse_feed(response.content, response.url) if kind == "rss" else parse_listing(response.text, response.url)
                for ad in ads:
                    state["ads"].setdefault(ad_key(ad.url), {
                        "url": ad.url, "title": ad.title, "source": ad.source,
                        "status": "pending", "checked_at": 0,
                    })
                good_sources += 1
                LOG.info("Kaynak okundu: %s (%s ilan)", url, len(ads))
            except (SourceError, ValueError, TypeError, AttributeError, KeyError) as exc:
                # URL yalnızca kullanıcı tarafından tanımlanan kamu kaynağıdır.
                LOG.warning("Kaynak okunamadı (%s): %s", str(exc), url)
                failures += 1
        # Başarısız detaylar listeden düşse bile kuyrukta kalır. Adil sıra,
        # her saat aynı ilk 35 ilanın kalan ilanları aç bırakmasını engeller.
        queue = sorted(state["ads"].items(), key=lambda item: item[1].get("attempted_at", 0))
        attempted = 0
        for key, entry in queue:
            if attempted >= 35 or time.monotonic() >= deadline:
                break
            if entry["status"] in {"notified", "baseline"}:
                continue
            now = time.time()
            if (entry["status"] == "rejected" and entry.get("filter_id") == filter_id
                    and now - entry.get("checked_at", 0) < 86400):
                continue
            attempted += 1
            entry["attempted_at"] = now
            try:
                response = web.get(entry["url"])
                if "html" not in response.headers.get("Content-Type", "").lower():
                    raise SourceError("HTML olmayan detay; PDF/görsel manuel kontrol edilmeli")
                ad = parse_detail(response.text, Ad(entry["url"], entry["title"], entry["source"]))
                parsed_details += 1
                if not process_ad(ad, state, token, chat_id, require_it_secondary=require_it,
                                  baseline=args.baseline, dry_run=args.dry_run,
                                  filter_id=filter_id, now=now):
                    failures += 1
                    delivery_failures += 1
            except (SourceError, ValueError, TypeError, AttributeError, KeyError) as exc:
                LOG.warning("Detay atlandı (%s): %s", str(exc), entry["url"])
                failures += 1
            if not args.dry_run:
                save_state(state_path, state)  # Her başarılı/başarısız denemeden sonra checkpoint.
        LOG.info("Özet: başarılı kaynak=%s, detay denemesi=%s, hata=%s", good_sources, attempted, failures)
        # Tek kaynak engellendi diye çalışan bağımsız kaynakları başarısız sayma.
        # Hiçbir detay okunamıyorsa veya Telegram gönderimi hata veriyorsa kırmızı.
        unhealthy = not good_sources or delivery_failures or (attempted > 0 and parsed_details == 0)
        return 1 if unhealthy else 0
    finally:
        web.session.close()
        if not args.dry_run:
            save_state(state_path, state)


if __name__ == "__main__":
    sys.exit(main())
