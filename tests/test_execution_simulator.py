import unittest

from apex.execution_simulator import market_fill


class PaperExecutionSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.quote = {
            "price": 100.0,
            "bid": 99.9,
            "ask": 100.1,
            "source": "test-provider",
            "provider_ts": 1_800_000_000,
            "status": "fresh",
            "quote_valid": True,
        }

    def test_buy_uses_ask_plus_slippage(self):
        result = market_fill(self.quote, "BUY", slippage_bps=2.0)
        self.assertAlmostEqual(result["reference_price"], 100.1)
        self.assertAlmostEqual(result["fill_price"], 100.1 * 1.0002)

    def test_sell_uses_bid_minus_slippage(self):
        result = market_fill(self.quote, "SELL", slippage_bps=2.0)
        self.assertAlmostEqual(result["reference_price"], 99.9)
        self.assertAlmostEqual(result["fill_price"], 99.9 * 0.9998)

    def test_rejects_unverified_or_stale_quote(self):
        for changes in (
            {"status": "stale"},
            {"status": "unverified"},
            {"quote_valid": False},
            {"quote_valid": None},
        ):
            quote = dict(self.quote, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                market_fill(quote, "BUY")

    def test_rejects_quote_without_provider_timestamp_or_source(self):
        for changes in ({"provider_ts": None}, {"source": ""}):
            quote = dict(self.quote, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                market_fill(quote, "BUY")

    def test_rejects_missing_or_crossed_book(self):
        for changes in ({"bid": None}, {"ask": None}, {"bid": 101.0, "ask": 100.0}):
            quote = dict(self.quote, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                market_fill(quote, "BUY")

    def test_rejects_invalid_slippage_and_unknown_action(self):
        with self.assertRaises(ValueError):
            market_fill(self.quote, "BUY", slippage_bps=51)
        with self.assertRaises(ValueError):
            market_fill(self.quote, "HOLD")

    def test_rejects_unacceptably_wide_spread(self):
        quote = dict(self.quote, bid=90.0, ask=110.0)
        with self.assertRaisesRegex(ValueError, "اسپرد"):
            market_fill(quote, "BUY")


if __name__ == "__main__":
    unittest.main()
