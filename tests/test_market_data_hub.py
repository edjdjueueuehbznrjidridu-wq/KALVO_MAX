import unittest
from unittest.mock import patch

from apex import market_data_hub as hub


class MarketDataHubTests(unittest.TestCase):
    def test_provider_timestamp_is_required_for_trading_eligibility(self):
        now = 1_800_000_000
        verified = hub.normalize_quote("BTC", {
            "price": 100, "source": "binance", "ts": now,
            "provider_ts": now - 3, "base": "BTC",
        }, now=now)
        self.assertEqual(verified["status"], "fresh")
        self.assertTrue(verified["quote_valid"])

        unverified = hub.normalize_quote("BTC", {
            "price": 100, "source": "bybit", "ts": now, "base": "BTC",
        }, now=now)
        self.assertEqual(unverified["status"], "unverified")
        self.assertTrue(unverified["retrieval_valid"])
        self.assertFalse(unverified["quote_valid"])
        self.assertTrue(unverified["display_only"])

    def test_old_retrieval_is_stale_even_with_positive_price(self):
        now = 1_800_000_000
        quote = hub.normalize_quote("ETH", {
            "price": 2000, "source": "binance", "ts": now - 120,
            "provider_ts": now - 2,
        }, now=now, max_age=45)
        self.assertEqual(quote["status"], "stale")
        self.assertFalse(quote["quote_valid"])

    def test_fresh_last_price_without_valid_bid_ask_is_not_execution_ready(self):
        now = 1_800_000_000
        quote = hub.normalize_quote("BTC", {
            "price": 100, "source": "binance", "ts": now,
            "provider_ts": now - 1, "bid": 99.9,
        }, now=now)
        self.assertEqual(quote["status"], "fresh")
        self.assertTrue(quote["quote_valid"])
        self.assertFalse(quote["execution_quote_valid"])

    def test_wide_spread_is_not_execution_ready(self):
        now = 1_800_000_000
        quote = hub.normalize_quote("BTC", {
            "price": 100, "source": "binance", "ts": now,
            "provider_ts": now - 1, "bid": 90, "ask": 110,
        }, now=now)
        self.assertFalse(quote["execution_quote_valid"])

    def test_ohlcv_validation_rejects_impossible_candles_and_deduplicates(self):
        rows = [
            {"time": 20, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 3, "source": "binance"},
            {"time": 10, "open": 10, "high": 9, "low": 8, "close": 11, "volume": 2},
            {"time": 20, "open": 10, "high": 13, "low": 9, "close": 12, "volume": 4, "source": "binance"},
        ]
        clean = hub.validate_ohlcv(rows)
        self.assertEqual(len(clean), 1)
        self.assertEqual(clean[0]["time"], 20)
        self.assertEqual(clean[0]["close"], 12)


    def test_unverified_quote_short_circuits_candle_requests(self):
        now = 1_800_000_000
        quote = hub.normalize_quote("BTC", {
            "price": 100, "source": "binance", "ts": now, "base": "BTC",
        }, now=now)
        with patch.object(hub, "get_quote", return_value=quote), \
             patch.object(hub, "_fetch_candles") as candles:
            bundle = hub.get_market_bundle("BTC", ("15min", "1hour"), now=now)
        self.assertFalse(bundle["trade_ready"])
        self.assertFalse(bundle["ready"])
        self.assertEqual(bundle["timeframes"], {})
        candles.assert_not_called()

    def test_bundle_rejects_mixed_quote_and_candle_providers(self):
        now = 1_800_000_000
        quote = hub.normalize_quote("BTC", {
            "price": 100, "source": "binance", "ts": now,
            "provider_ts": now - 1, "base": "BTC",
        }, now=now)
        candle_rows = [{"time": now - 3600, "open": 99, "high": 102,
                        "low": 98, "close": 100, "volume": 4,
                        "source": "kucoin"}]
        with patch.object(hub, "get_quote", return_value=quote), \
             patch.object(hub, "_fetch_candles", return_value=(candle_rows, "kucoin")), \
             patch("apex.analysis_engine.completed_candles", return_value=candle_rows), \
             patch("apex.analysis_engine.analyze_candles", return_value={
                 "ready": True, "signal": "LONG_BIAS", "confluence_score": 80
             }):
            bundle = hub.get_market_bundle("BTC", ("1hour",), now=now)
        self.assertFalse(bundle["provider_consistent"])
        self.assertFalse(bundle["trade_ready"])
        self.assertTrue(any("یکسان نیست" in p for p in bundle["problems"]))
