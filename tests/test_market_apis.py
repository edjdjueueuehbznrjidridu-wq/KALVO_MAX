import unittest
from unittest.mock import patch

from apex import market_apis as mapi


class MarketApiTests(unittest.TestCase):
    def setUp(self):
        mapi.clear_cache()

    def tearDown(self):
        mapi.clear_cache()

    def test_failed_ticker_payload_is_not_cached(self):
        calls = []

        def request():
            calls.append(1)
            if len(calls) == 1:
                return {"price": None, "error": "temporary outage"}
            return {"price": 123.45, "source": "mock"}

        with patch.object(mapi.time, "monotonic", return_value=100.0):
            first = mapi._cached("ticker:test", 30, request)
            second = mapi._cached("ticker:test", 30, request)

        self.assertIsNone(first["price"])
        self.assertEqual(second["price"], 123.45)
        self.assertEqual(len(calls), 2)

    def test_valid_ticker_payload_is_cached(self):
        calls = []

        def request():
            calls.append(1)
            return {"price": 12.5, "source": "mock"}

        with patch.object(mapi.time, "monotonic", return_value=100.0):
            first = mapi._cached("ticker:good", 30, request)
            second = mapi._cached("ticker:good", 30, request)

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_ticker_freshness_is_recomputed_on_each_read(self):
        raw = {"price": 100.0, "source": "mock", "ts": 1_000,
               "provider_ts": 1_000, "stale": False}
        with patch.object(mapi, "_cached", return_value=raw), \
             patch.object(mapi.time, "time", return_value=1_010):
            fresh = mapi.get_ticker("BTC")
        self.assertEqual(fresh["status"], "fresh")
        self.assertFalse(fresh["stale"])
        with patch.object(mapi, "_cached", return_value=raw), \
             patch.object(mapi.time, "time", return_value=1_100):
            stale = mapi.get_ticker("BTC")
        self.assertEqual(stale["status"], "stale")
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["age_seconds"], 100.0)

    def test_bybit_fallback_preserves_provider_timestamp_and_bid_ask(self):
        payload = {
            "retCode": 0,
            "time": 1_800_000_000_000,
            "result": {"list": [{
                "lastPrice": "100", "bid1Price": "99.9", "ask1Price": "100.1",
                "highPrice24h": "105", "lowPrice24h": "95",
                "turnover24h": "12345", "price24hPcnt": "0.02",
            }]},
        }
        with patch.object(mapi, "_json", return_value=payload):
            snapshot = mapi._bybit_snapshot("BTC")
        self.assertEqual(snapshot["price"], 100.0)
        self.assertEqual(snapshot["provider_ts"], 1_800_000_000.0)
        self.assertEqual(snapshot["bid"], 99.9)
        self.assertEqual(snapshot["ask"], 100.1)

    def test_okx_fallback_preserves_provider_timestamp(self):
        payload = {"code": "0", "data": [{
            "last": "2500", "bidPx": "2499", "askPx": "2501",
            "ts": "1800000000000", "high24h": "2550", "low24h": "2450",
            "volCcy24h": "9876",
        }]}
        with patch.object(mapi, "_json", return_value=payload):
            snapshot = mapi._okx_snapshot("ETH")
        self.assertEqual(snapshot["provider_ts"], 1_800_000_000.0)
        self.assertEqual(snapshot["price"], 2500.0)

    def test_kucoin_level1_fallback_preserves_provider_timestamp(self):
        payload = {"code": "200000", "data": {
            "time": 1_800_000_000_000, "price": "100",
            "bestBid": "99.8", "bestAsk": "100.2",
        }}
        with patch.object(mapi, "_json", return_value=payload):
            snapshot = mapi._kucoin_level1_snapshot("BTC")
        self.assertEqual(snapshot["provider_ts"], 1_800_000_000.0)
        self.assertEqual(snapshot["bid"], 99.8)
        self.assertEqual(snapshot["ask"], 100.2)

    def test_retrieval_timestamp_without_provider_timestamp_is_unverified(self):
        raw = {"price": 100.0, "source": "mock", "ts": 1_000, "stale": False}
        with patch.object(mapi, "_cached", return_value=raw), \
             patch.object(mapi.time, "time", return_value=1_010):
            quote = mapi.get_ticker("BTC")
        self.assertEqual(quote["status"], "unverified")
        self.assertTrue(quote["retrieval_fresh"])
        self.assertFalse(quote["quote_valid"])
        self.assertFalse(quote["stale"])

    def test_live_price_rejects_unverified_quote(self):
        with patch.object(mapi, "get_ticker", return_value={
            "price": 100.0, "status": "unverified", "stale": False,
            "quote_valid": False,
        }):
            self.assertIsNone(mapi.get_live_price("BTC"))

    def test_live_price_rejects_explicitly_stale_ticker(self):
        with patch.object(mapi, "get_ticker", return_value={
            "price": 100.0, "status": "stale", "stale": True,
        }):
            self.assertIsNone(mapi.get_live_price("BTC"))

    def test_zero_funding_value_remains_cacheable(self):
        calls = []

        def request():
            calls.append(1)
            return 0.0

        with patch.object(mapi.time, "monotonic", return_value=100.0):
            mapi._cached("funding:zero", 30, request)
            mapi._cached("funding:zero", 30, request)

        self.assertEqual(len(calls), 1)

    def test_rich_snapshot_calculates_depth_imbalance(self):
        book = {
            "bids": [[100.0, 2.0], [99.0, 1.0]],
            "asks": [[101.0, 1.0], [102.0, 1.0]],
            "ts": 123, "source": "test",
        }
        ticker = {"base": "BTC", "price": 100.5, "bid": 100.0, "ask": 101.0,
                  "source": "test", "ts": 123}
        with patch.object(mapi, "get_ticker", return_value=ticker), \
             patch.object(mapi, "fetch_orderbook", return_value=book), \
             patch.object(mapi, "funding_rate", return_value=None), \
             patch.object(mapi, "open_interest", return_value=None):
            snapshot = mapi.rich_snapshot("BTC")
        self.assertGreater(snapshot["depth_imbalance_pct"], 0)
        self.assertAlmostEqual(snapshot["depth_imbalance_pct"],
                               (299.0 - 203.0) / (299.0 + 203.0) * 100.0)

    def test_symbol_normalization_is_explicit_about_quote(self):
        self.assertEqual(mapi.normalize_symbol("btc")[0], "BTC")
        with self.assertRaises(ValueError):
            mapi.normalize_symbol("BTC-EUR")

    def test_unsupported_timeframe_is_rejected_instead_of_silently_using_one_hour(self):
        with self.assertRaisesRegex(ValueError, "تایم‌فریم پشتیبانی نمی‌شود"):
            mapi.fetch_candles("BTC", "10min")

    def test_bybit_candles_are_normalized_sorted_and_source_tagged(self):
        payload = {"retCode": 0, "result": {"list": [
            ["1800003600000", "101", "103", "100", "102", "12"],
            ["1800000000000", "99", "102", "98", "101", "10"],
        ]}}
        with patch.object(mapi, "_cached", side_effect=lambda key, ttl, fn: fn()), \
             patch.object(mapi, "_json", return_value=payload) as request:
            rows = mapi.fetch_candles("BTC", "1hour", start=1_799_999_000,
                                      end=1_800_010_000, provider="bybit")
        self.assertEqual([r["time"] for r in rows], [1_800_000_000, 1_800_003_600])
        self.assertTrue(all(r["source"] == "bybit" for r in rows))
        self.assertIn("category=spot", request.call_args.args[0])
        self.assertIn("interval=60", request.call_args.args[0])

    def test_okx_candles_are_window_filtered_and_source_tagged(self):
        payload = {"code": "0", "data": [
            ["1800007200000", "103", "105", "102", "104", "13"],
            ["1799990000000", "98", "100", "97", "99", "8"],
            ["1800003600000", "101", "104", "100", "103", "11"],
        ]}
        with patch.object(mapi, "_cached", side_effect=lambda key, ttl, fn: fn()), \
             patch.object(mapi, "_json", return_value=payload) as request:
            rows = mapi.fetch_candles("BTC", "1hour", start=1_800_000_000,
                                      end=1_800_500_000, provider="okx")
        self.assertEqual([r["time"] for r in rows], [1_800_003_600, 1_800_007_200])
        self.assertTrue(all(r["source"] == "okx" for r in rows))
        self.assertIn("instId=BTC-USDT", request.call_args.args[0])
        self.assertIn("bar=1H", request.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
