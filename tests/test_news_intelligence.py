import unittest
from unittest.mock import patch

from apex import news_intelligence as news


class NewsIntelligenceTests(unittest.TestCase):
    def test_untrusted_article_domain_is_dropped(self):
        feed = {"id": "coindesk", "name": "CoinDesk", "url": news.FEEDS[0]["url"],
                "trust": 0.82, "scope": "crypto", "domains": ("coindesk.com",)}
        payload = b'''<rss><channel><item><title>Bitcoin market update</title>
        <link>https://not-coindesk.example/redirect</link>
        <description>BTC market</description><pubDate>Sun, 15 Nov 2026 10:00:00 GMT</pubDate>
        </item></channel></rss>'''
        parsed = news._parse_feed(feed, payload, now=1_800_000_000)
        self.assertEqual(parsed, [])

    def test_short_symbol_matching_uses_word_boundaries(self):
        article = {"title": "SOL market update", "summary": "A new market move",
                   "scope": "crypto"}
        false_hit = {"title": "Consolidation in the market", "summary": "No coin mentioned",
                     "scope": "crypto"}
        self.assertGreater(news._relevance(article, "SOL"), 0)
        self.assertEqual(news._relevance(false_hit, "SOL"), 0)

    def test_disabled_news_filter_is_non_blocking_by_default(self):
        with patch.object(news.storage, "get_setting", return_value="0"):
            result = news.evaluate_entry_gate("BTC", "LONG", now=1_800_000_000)
        self.assertFalse(result["enabled"])
        self.assertTrue(result["allow"])

    def test_enabled_news_filter_fails_closed_without_verified_context(self):
        with patch.object(news, "get_news_context", return_value={
            "fresh_verified_count": 0, "high_risk_count": 0, "items": [],
        }):
            result = news.evaluate_entry_gate("BTC", "LONG", enabled=True, now=1_800_000_000)
        self.assertFalse(result["allow"])
        self.assertEqual(result["status"], "no_verified_context")

    def test_high_risk_news_blocks_entry(self):
        context = {
            "fresh_verified_count": 2, "high_risk_count": 1,
            "items": [
                {"source": "CoinDesk", "severity": 20, "event_type": "general"},
                {"source": "CoinDesk", "severity": 95, "event_type": "security"},
            ],
        }
        with patch.object(news, "get_news_context", return_value=context):
            result = news.evaluate_entry_gate("BTC", "LONG", enabled=True, now=1_800_000_000)
        self.assertFalse(result["allow"])
        self.assertEqual(result["status"], "high_event_risk")
        self.assertIn("security", result["reason"])

    def test_news_summary_cannot_return_trade_command(self):
        raw = '{"summary_fa":"خبر","affected_assets":["BTC"],"event_type":"macro","risk_level":"low","confidence":70,"uncertainty":"—","signal":"BUY"}'
        with patch("apex.coarena_ai.chat", return_value=raw):
            with self.assertRaises(ValueError):
                news.summarize_news_with_ai("BTC", {"title": "x"}, api_key="abcdefgh")

    def test_news_summary_validates_schema_and_never_authorizes_execution(self):
        raw = ('{"summary_fa":"خبر درباره سیاست پولی","affected_assets":["BTC","USD"],'
               '"event_type":"macro","risk_level":"moderate","confidence":82,'
               '"uncertainty":"اثر نهایی به واکنش بازار بستگی دارد"}')
        article = {"id": "a1", "source": "Federal Reserve", "source_domain": "federalreserve.gov",
                   "published_at": 1_800_000_000, "title": "Policy update"}
        with patch("apex.coarena_ai.chat", return_value=raw):
            result = news.summarize_news_with_ai("BTC", article, api_key="abcdefgh")
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["analysis"]["risk_level"], "moderate")
        self.assertEqual(result["analysis"]["affected_assets"], ["BTC", "USD"])
        self.assertEqual(result["source_domain"], "federalreserve.gov")

    def test_news_summary_rejects_non_finite_confidence(self):
        raw = ('{"summary_fa":"خبر","affected_assets":["BTC"],"event_type":"macro",'
               '"risk_level":"low","confidence":NaN,"uncertainty":"نامشخص"}')
        with patch("apex.coarena_ai.chat", return_value=raw):
            with self.assertRaises(ValueError):
                news.summarize_news_with_ai("BTC", {"title": "x"}, api_key="abcdefgh")
