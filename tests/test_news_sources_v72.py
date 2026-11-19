import unittest
from apex import news_intelligence


class NewsSources72Tests(unittest.TestCase):
    def test_sec_press_feed_is_configured_as_official_regulatory_source(self):
        sec = next(item for item in news_intelligence.FEEDS if item["id"] == "sec")
        self.assertEqual(sec["scope"], "regulatory")
        self.assertEqual(sec["domains"], ("sec.gov",))
        self.assertIn("sec.gov", sec["url"])
        self.assertGreaterEqual(sec["trust"], 0.95)

    def test_sec_feed_items_are_attributed_from_allowed_domain(self):
        import time
        import xml.etree.ElementTree as ET
        feed = next(item for item in news_intelligence.FEEDS if item["id"] == "sec")
        now = int(time.time())
        payload = f'''<?xml version="1.0"?><rss><channel><item>
        <title>SEC announces enforcement action</title>
        <link>https://www.sec.gov/newsroom/press-releases/example</link>
        <description>Official regulatory update.</description>
        <pubDate>Sun, 15 Nov 2026 10:00:00 GMT</pubDate>
        </item></channel></rss>'''.encode()
        rows = news_intelligence._parse_feed(feed, payload, now=max(now, 1794746400))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_id"], "sec")
        self.assertEqual(rows[0]["scope"], "regulatory")


if __name__ == "__main__":
    unittest.main()
