"""Ağ/gerçek Telegram kullanmadan kritik davranışları doğrular."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import scraper as s


URL = "https://ilan.memurlar.net/ilan/123/test.html"
QUALIFIED = "Teknisyen: Meslek lisesi Bilişim Teknolojileri alanı mezunu olmak. KPSS P94 en az 60."


class TrackerTests(unittest.TestCase):
    def ad(self):
        return s.Ad(URL, "Örnek Üniversitesi 2 personel alacak", "test", QUALIFIED)

    def state(self):
        return {"version": 1, "ads": {s.ad_key(URL): {
            "url": URL, "title": "Örnek", "source": "test", "status": "pending"}}}

    def test_turkish_case_and_ascii(self):
        self.assertTrue(s.matches("TEKNİSYEN MESLEK LİSESİ BİLİŞİM TEKNOLOJİLERİ ALANI MEZUNU"))
        self.assertTrue(s.matches("teknisyen ortaogretim bilgisayar bolumu mezunu"))

    def test_each_negative_vetoes_mixed_ad(self):
        for word in s.NEGATIVE:
            with self.subTest(word=word):
                self.assertFalse(s.matches(QUALIFIED + " " + word))

    def test_scope_gate_prevents_electrician_and_year_false_positive(self):
        self.assertFalse(s.matches("Teknisyen: Elektrik meslek lisesi alanından mezun olmak."))
        self.assertFalse(s.matches("2011 yılında yayımlanan teknisyen ilanı"))
        self.assertTrue(s.matches("Teknisyen; nitelik kodları: 2001, 2011, 6225"))

    def test_literal_keyword_mode_can_be_selected(self):
        self.assertTrue(s.matches("Lise mezunu destek personeli", require_it_secondary=False))
        self.assertFalse(s.matches("Lise mezunu destek personeli"))

    def test_url_variants_have_same_id(self):
        self.assertEqual(s.ad_key(URL), s.ad_key(URL + "?amp=1&utm_source=x#foo"))
        self.assertEqual(s.canonical_url("https://example.gov.tr/ilan?id=2&utm_source=x#abc"),
                         "https://example.gov.tr/ilan?id=2")

    def test_list_dedupes_and_rejects_external_or_non_ad_links(self):
        markup = f'<a href="{URL}">İlan</a><a href="{URL}?amp=1">Tekrar</a>'
        markup += '<a href="/kategori/4b-ilanlari/">Kategori</a><a href="https://evil.example/ilan/2/x">Dış</a>'
        self.assertEqual(len(s.parse_listing(markup, s.MEMURLAR)), 1)

    def test_listing_structure_change_is_explicit(self):
        with self.assertRaises(s.SourceError):
            s.parse_listing("<h1>Just a moment...</h1>", s.MEMURLAR)

    def test_detail_does_not_read_sidebar_negatives(self):
        markup = f'<h1>Örnek Üniversitesi</h1><article><p>{QUALIFIED}</p></article>'
        markup += '<aside>Doktor mühendis öğretmen</aside><footer>Lisans</footer>'
        self.assertTrue(s.matches(s.parse_detail(markup, self.ad()).body))

    def test_h1_fallback_stops_before_related_ads(self):
        markup = f'<h1>Teknisyen ilanı</h1><div><p>{QUALIFIED}</p></div>'
        markup += '<h2>Kategoriler</h2><p>Doktor, lisans</p>'
        parsed = s.parse_detail(markup, self.ad())
        self.assertTrue(s.matches(parsed.title + parsed.body))
        self.assertNotIn("Doktor", parsed.body)

    def test_missing_detail_not_classified(self):
        with self.assertRaises(s.SourceError):
            s.parse_detail("<h1>Access denied</h1>", self.ad())

    def test_rss_and_atom(self):
        rss = f'<rss version="2.0"><channel><title>İlanlar</title><link>{s.MEMURLAR}</link><description>x</description><item><title>Teknisyen</title><link>{URL}</link></item></channel></rss>'
        atom = f'<feed xmlns="http://www.w3.org/2005/Atom"><title>İlan</title><entry><title>Teknisyen</title><link href="{URL}"/></entry></feed>'
        for feed in (rss, atom):
            self.assertEqual(s.parse_feed(feed.encode(), s.MEMURLAR)[0].url, URL)
        with self.assertRaises(s.SourceError):
            s.parse_feed(b"<html>blocked</html>", s.MEMURLAR)

    def test_state_atomic_roundtrip_and_corrupt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen_ads.json"
            s.save_state(path, self.state())
            self.assertEqual(s.load_state(path), self.state())
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                s.load_state(path)
            self.assertEqual(path.read_text(), "{broken")

    def test_telegram_failure_not_marked_notified(self):
        state = self.state()
        with patch.object(s, "send_telegram", return_value=False):
            result = s.process_ad(self.ad(), state, "TOKEN", "ID", require_it_secondary=True,
                                  baseline=False, dry_run=False, filter_id="x", now=1)
        self.assertFalse(result)
        self.assertEqual(state["ads"][s.ad_key(URL)]["status"], "pending")

    def test_success_and_baseline(self):
        for baseline, status in ((False, "notified"), (True, "baseline")):
            state = self.state()
            with patch.object(s, "send_telegram", return_value=True) as send:
                s.process_ad(self.ad(), state, "TOKEN", "ID", require_it_secondary=True,
                             baseline=baseline, dry_run=False, filter_id="x", now=1)
            self.assertEqual(state["ads"][s.ad_key(URL)]["status"], status)
            self.assertEqual(send.call_count, 0 if baseline else 1)

    def test_html_escape_and_message_size(self):
        ad = self.ad()
        ad.title = "Kurum <script> & 2 personel alacak"
        message = s.telegram_message(ad)
        self.assertNotIn("<script>", message)
        self.assertIn("&lt;script&gt;", message)
        self.assertLess(len(message), 4096)

    def test_post_timeout_never_leaks_bot_token(self):
        with patch.object(s.requests, "post", side_effect=requests.Timeout("botSECRET/sendMessage")):
            with self.assertLogs(s.LOG, "WARNING") as logs:
                self.assertFalse(s.send_telegram(self.ad(), "SECRET", "ID"))
        self.assertNotIn("SECRET", " ".join(logs.output))

    def test_telegram_429_bounded_retry(self):
        limited = Mock(status_code=429)
        limited.json.return_value = {"ok": False, "parameters": {"retry_after": 1}}
        success = Mock(status_code=200)
        success.json.return_value = {"ok": True}
        with patch.object(s.requests, "post", side_effect=[limited, success]) as post:
            with patch.object(s.time, "sleep"):
                self.assertTrue(s.send_telegram(self.ad(), "TOKEN", "ID"))
        self.assertEqual(post.call_count, 2)

    def test_robots_denied_prevents_fetch(self):
        web = s.Web()
        robots = Mock(status_code=200, text="User-agent: *\nDisallow: /ilan/")
        with patch.object(web, "_raw", return_value=robots) as raw:
            with self.assertRaises(s.SourceError):
                web.get(URL)
        self.assertEqual(raw.call_count, 1)
        web.session.close()

    def test_full_run_retries_failed_send_but_does_not_repeat_success(self):
        markup = f'<h1>Örnek Üniversitesi</h1><article>{QUALIFIED}</article>'
        listing = f'<a href="{URL}">Örnek Üniversitesi personel alacak</a>'
        def fetch(url):
            return Mock(text=markup if "/ilan/" in url else listing,
                        headers={"Content-Type": "text/html"}, url=url)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seen_ads.json"
            with patch.dict(s.os.environ, {"STATE_FILE": str(path),
                                          "TELEGRAM_BOT_TOKEN": "SECRET",
                                          "TELEGRAM_CHAT_ID": "ID", "RSS_URLS": ""}):
                with patch.object(s.sys, "argv", ["scraper.py"]):
                    with patch.object(s.Web, "get", side_effect=fetch):
                        with patch.object(s, "DEFAULT_RSS_URLS", ()):
                            with patch.object(s, "send_telegram", side_effect=[False, True]) as send:
                                self.assertEqual(s.main(), 1)
                                self.assertEqual(s.main(), 0)
                                self.assertEqual(s.main(), 0)
                                self.assertEqual(send.call_count, 2)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["ads"]["memurlar:123"]["status"], "notified")

    def test_blocked_primary_source_does_not_stop_healthy_rss(self):
        rss_url = 'https://example.gov.tr/feed.xml'
        ad_url = 'https://example.gov.tr/duyuru/1'
        rss = f'<rss version="2.0"><channel><title>İlan</title><link>{rss_url}</link><description>x</description><item><title>Teknisyen</title><link>{ad_url}</link></item></channel></rss>'
        detail = f'<h1>Örnek Üniversitesi</h1><article>{QUALIFIED}</article>'
        def fetch(url):
            if 'memurlar.net' in url:
                raise s.SourceError('HTTP 403')
            return Mock(text=detail, content=rss.encode(), url=url,
                        headers={'Content-Type': 'text/html'})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(s.os.environ, {'STATE_FILE': str(Path(tmp)/'state.json'),
                                          'TELEGRAM_BOT_TOKEN': 'TOKEN', 'TELEGRAM_CHAT_ID': 'ID',
                                          'RSS_URLS': rss_url}):
                with patch.object(s.sys, 'argv', ['scraper.py']):
                    with patch.object(s.Web, 'get', side_effect=fetch):
                        with patch.object(s, 'send_telegram', return_value=True) as send:
                            self.assertEqual(s.main(), 0)
                            self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
