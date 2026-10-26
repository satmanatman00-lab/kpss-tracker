"""Ağ/gerçek Telegram kullanmadan kritik davranışları doğrular."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
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

    def test_mixed_ad_other_roles_do_not_veto(self):
        for word in ("mühendis", "lisans", "önlisans", "öğretmen", "doktor", "hemşire", "sağlık teknikeri"):
            self.assertTrue(s.matches(QUALIFIED + " " + word))

    def test_positive_groups_both_required(self):
        self.assertFalse(s.matches("Lise mezunu destek personeli. Bilgisayar kullanabilmek."))
        self.assertFalse(s.matches("Teknisyen aranıyor."))
        for text in ("Teknisyeni bilişim", "Teknisyen bilgisayar", "Teknisyen ortaöğretim",
                     "Teknisyen lise", "Teknisyen 2011", "Teknisyen: Elektrik meslek lisesi"):
            self.assertTrue(s.matches(text), text)
        self.assertFalse(s.matches("Sağlık teknikeri lise"))

    def test_computer_literacy_without_technician_does_not_match(self):
        self.assertFalse(s.matches('Büro İşçisi. En az lise mezunu. Office ve bilgisayar kullanmak.'))

    def test_worker_category_is_rejected_even_with_it_department(self):
        markup = f'<article data-k-article><header class="k-jobhead"><a href="/tr/kamu-ilanlari/tur/isci">İşçi</a><h1>Şirket 2 personel alacak</h1></header><div class="k-article__body">{QUALIFIED}</div></article>'
        ad = s.parse_detail(markup, self.ad())
        self.assertIn('Hedef dışı ilan türü', ad.exclusion_reason)
        state = self.state()
        with patch.object(s, 'send_telegram') as send:
            s.process_ad(ad, state, 'TOKEN', 'ID', 
                         baseline=False, dry_run=False, filter_id='x', now=1)
        send.assert_not_called()
        self.assertEqual(state['ads'][s.ad_key(URL)]['status'], 'rejected')

    def test_expired_ad_is_rejected_even_when_qualification_matches(self):
        markup = f'<article data-k-article><header class="k-jobhead"><a href="/tr/kamu-ilanlari/tur/sozlesmeli">Sözleşmeli</a><span>Süre doldu</span><h1>Teknisyen alımı</h1></header><div class="k-article__body">{QUALIFIED}</div></article>'
        self.assertIn('Başvuru süresi dolmuş', s.parse_detail(markup, self.ad()).exclusion_reason)

    def test_deadline_timezone_and_date_only_end_of_day(self):
        now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
        self.assertTrue(s.deadline_is_past('2026-10-24T17:00:00+03:00', now))
        self.assertFalse(s.deadline_is_past('2026-10-26', now))
        self.assertTrue(s.deadline_is_past('2026-10-26T14:00:00', now))

    def test_url_variants_have_same_id(self):
        self.assertEqual(s.ad_key(URL), s.ad_key(URL + "?amp=1&utm_source=x#foo"))
        self.assertEqual(s.canonical_url("https://example.gov.tr/ilan?id=2&utm_source=x#abc"),
                         "https://example.gov.tr/ilan?id=2")

    def test_sbb_links_and_category_discovery(self):
        base = s.SBB_URL
        markup = '<a href="/ilanDetay.aspx?id=123">Örnek Üniversitesi 50 personel</a>'
        markup += '<a href="/ilanDetay.aspx?id=123">Tekrar</a><a href="/arsiv.aspx">Arşiv</a>'
        markup += '<a href="/?ktg=99">4/B Sözleşmeli Personel</a>'
        self.assertEqual(len(s.parse_listing(markup, base)), 1)
        self.assertEqual(s.sbb_category_links(markup, base), [base + '?ktg=99'])

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
            result = s.process_ad(self.ad(), state, "TOKEN", "ID", 
                                  baseline=False, dry_run=False, filter_id="x", now=1)
        self.assertFalse(result)
        self.assertEqual(state["ads"][s.ad_key(URL)]["status"], "pending")

    def test_success_and_baseline(self):
        for baseline, status in ((False, "notified"), (True, "baseline")):
            state = self.state()
            with patch.object(s, "send_telegram", return_value=True) as send:
                s.process_ad(self.ad(), state, "TOKEN", "ID", 
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
        listing = '<a href="https://kamuilan.sbb.gov.tr/ilanDetay.aspx?id=123">Üniversite personel alacak</a>'
        def fetch(url):
            text = markup if 'ilanDetay' in url else listing
            return Mock(text=text, content=text.encode(),
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
            self.assertEqual(saved["ads"]["https://kamuilan.sbb.gov.tr/ilanDetay.aspx?id=123"]["status"], "notified")

    def test_blocked_primary_source_does_not_stop_healthy_rss(self):
        rss_url = 'https://example.gov.tr/feed.xml'
        ad_url = 'https://example.gov.tr/duyuru/1'
        rss = f'<rss version="2.0"><channel><title>İlan</title><link>{rss_url}</link><description>x</description><item><title>Teknisyen</title><link>{ad_url}</link></item></channel></rss>'
        detail = f'<h1>Örnek Üniversitesi</h1><article>{QUALIFIED}</article>'
        def fetch(url):
            if 'sbb.gov.tr' in url:
                raise s.SourceError('HTTP 403')
            return Mock(text=detail, content=(detail.encode() if url == ad_url else rss.encode()), url=url,
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

    def pdf(self, text="Teknisyen bilgisayar lise"):
        from io import BytesIO
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 12 Tf 20 700 Td (' + text + ') Tj ET').encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
        out = BytesIO(); writer.write(out)
        return out.getvalue()

    def test_real_pdf_extraction_and_matching(self):
        text = s.extract_pdf_text(self.pdf())
        self.assertTrue(s.matches(text))
        ad = s.Ad(URL, 'Karma üniversite alımı', 'test', 'Hemşire lisans mühendis alımı')
        ad.pdf_texts.append(text)
        self.assertTrue(s.ad_matches(ad))

    def test_keywords_cannot_join_unrelated_pdf_documents(self):
        ad = self.ad(); ad.title = 'Alım'; ad.body = 'Personel'
        ad.pdf_texts = ['Teknisyen aranıyor', 'Lise mezunu büro personeli']
        self.assertFalse(s.ad_matches(ad))

    def test_scanned_corrupt_and_oversized_pdf_are_explicit(self):
        from io import BytesIO
        from pypdf import PdfWriter
        writer = PdfWriter(); writer.add_blank_page(width=600, height=800)
        out = BytesIO(); writer.write(out)
        for content in (out.getvalue(), b'not a PDF'):
            with self.assertRaises(s.SourceError): s.extract_pdf_text(content)
        with patch.object(s, 'MAX_PDF_PAGES', 0):
            with self.assertRaises(s.SourceError): s.extract_pdf_text(self.pdf())

    def test_pdf_links_relative_query_case_and_dedupe(self):
        markup = '<a href="/ek.PDF?x=1">İlan</a><a href="/ek.PDF?x=1">Tekrar</a>'
        markup += '<iframe src="/ek2.pdf"></iframe><footer><a href="/unrelated.pdf">X</a></footer>'
        self.assertEqual(s.pdf_links(markup, URL),
                         ['https://ilan.memurlar.net/ek.PDF?x=1', 'https://ilan.memurlar.net/ek2.pdf'])

    def test_pdf_only_html_is_accepted(self):
        ad = s.parse_detail('<h1>Üniversite alımı</h1><a href="/ilan.pdf">İlan metni</a>', self.ad())
        self.assertEqual(len(ad.pdf_urls), 1)

    def test_failed_pdf_stays_pending_and_next_ad_continues(self):
        ad = s.Ad(URL, 'Üniversite alımı', 'test', 'Personel alımı')
        ad.pdf_urls = ['https://example.gov.tr/ilan.pdf']
        with patch.object(s.Web, 'get', side_effect=s.SourceError('HTTP 403')):
            web = s.Web()
            s.enrich_pdfs(web, ad, s.time.monotonic() + 10)
            web.session.close()
        self.assertTrue(ad.incomplete)
        state = self.state()
        with patch.object(s, 'send_telegram') as send:
            s.process_ad(ad, state, 'TOKEN', 'ID', baseline=False, dry_run=False, filter_id='x', now=1)
        send.assert_not_called()
        self.assertEqual(state['ads'][s.ad_key(URL)]['status'], 'pending')

    def test_memurlar_feed_keeps_content_and_enclosure(self):
        rss = f'<rss version="2.0"><channel><title>İlan</title><item><title>Üniversite</title><link>{URL}</link><description><![CDATA[Hemşire lisans. Teknisyen bilgisayar lise.]]></description><enclosure url="https://example.edu.tr/ilan.pdf" type="application/pdf" length="10"/></item></channel></rss>'
        ad = s.parse_feed(rss.encode(), URL)[0]
        self.assertTrue(s.ad_matches(ad))
        self.assertEqual(ad.pdf_urls, ['https://example.edu.tr/ilan.pdf'])

    def test_memurlar_rss_never_fetches_detail_html(self):
        feed_url = 'https://example.edu.tr/feed.xml'
        rss = f'<rss version="2.0"><channel><title>İlan</title><item><title>Üniversite</title><link>{URL}</link><description>Hemşire lisans. Teknisyen bilgisayar lise.</description></item></channel></rss>'
        calls = []
        def fetch(url):
            calls.append(url)
            if url != feed_url: raise s.SourceError('HTTP 403')
            return Mock(content=rss.encode(), url=url)
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(s.os.environ, {'STATE_FILE': str(Path(tmp)/'state.json'),
                    'TELEGRAM_BOT_TOKEN': 'TOKEN', 'TELEGRAM_CHAT_ID': 'ID', 'RSS_URLS': feed_url,
                    'MEMURLAR_RSS_URLS': ''}):
                with patch.object(s.sys, 'argv', ['scraper.py']):
                    with patch.object(s.Web, 'get', side_effect=fetch):
                        with patch.object(s, 'send_telegram', return_value=True) as send:
                            self.assertEqual(s.main(), 0)
                            self.assertEqual(send.call_count, 1)
        self.assertNotIn(URL, calls)


if __name__ == "__main__":
    unittest.main()
