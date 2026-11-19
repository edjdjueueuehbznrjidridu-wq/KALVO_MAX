import unittest
from unittest import mock
from datetime import datetime, timezone

from apex.fx_market import parse_tgju_payload, parse_timestamp, get_cross_rate
from apex.market_apis import rank_search_candidates
from apex import instrument_catalog
from apex import market_apis


class TGJUFxParsingTests(unittest.TestCase):
    def test_tgju_rial_price_is_divided_by_ten(self):
        now = int(datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc).timestamp())
        payload = {"current": {"price_dollar_rl": {
            "p": "1,234,560", "h": "1,240,000", "l": "1,220,000",
            "d": "-5,000", "dp": "-0.40", "ts": now,
        }}}
        row = parse_tgju_payload(payload, "USD", now=now)
        self.assertEqual(row["raw_price_irr"], 1234560)
        self.assertEqual(row["price"], 123456)
        self.assertEqual(row["high"], 124000)
        self.assertEqual(row["low"], 122000)
        self.assertEqual(row["change"], -500)
        self.assertEqual(row["change_pct"], -0.4)
        self.assertFalse(row["stale"])
        self.assertEqual(row["quote_currency"], "TOMAN")

    def test_persian_digits_are_supported(self):
        payload = {"current": {"price_eur": {"p": "۸۷۶۵۴۳۲", "ts": 1794657600}}}
        row = parse_tgju_payload(payload, "EUR", now=1794657600)
        self.assertEqual(row["price"], 876543.2)

    def test_missing_timestamp_is_not_marked_fresh(self):
        payload = {"current": {"price_gbp": {"p": "800000"}}}
        row = parse_tgju_payload(payload, "GBP", now=1794657600)
        self.assertTrue(row["stale"])
        self.assertEqual(row["status"], "unverified")

    def test_missing_rate_is_not_fabricated(self):
        with self.assertRaises(LookupError):
            parse_tgju_payload({"current": {}}, "AED", now=1794657600)

    def test_extended_tgju_currency_catalogue_converts_rial(self):
        payload = {"current": {"price_nzd": {"p": "250000", "ts": 1794657600}}}
        row = parse_tgju_payload(payload, "NZD", now=1794657600)
        self.assertEqual(row["price"], 25000)
        self.assertEqual(row["source_key"], "price_nzd")

    def test_unverified_currency_identifier_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_tgju_payload({"current": {"price_chf": {"p": "100000"}}}, "CHF", now=1794657600)

    def test_cross_rate_is_derived_from_two_toman_quotes(self):
        base = {"currency": "EUR", "price": 1_080_000, "stale": False,
                "status": "fresh", "age_seconds": 30, "observed_at": 1794657600,
                "source_key": "price_eur", "source": "TGJU"}
        quote = {"currency": "USD", "price": 1_000_000, "stale": False,
                 "status": "fresh", "age_seconds": 25, "observed_at": 1794657605,
                 "source_key": "price_dollar_rl", "source": "TGJU"}
        with mock.patch("apex.fx_market.get_rate", side_effect=[base, quote]):
            row = get_cross_rate("EUR", "USD")
        self.assertAlmostEqual(row["price"], 1.08)
        self.assertEqual(row["quote_currency"], "USD")
        self.assertEqual(row["status"], "fresh")
        self.assertEqual(row["conversion"], "EUR/IRT ÷ USD/IRT")

    def test_cross_rate_rejects_stale_input(self):
        base = {"currency": "EUR", "price": 1_080_000, "stale": True,
                "status": "stale", "age_seconds": 999999}
        quote = {"currency": "USD", "price": 1_000_000, "stale": False,
                 "status": "fresh", "age_seconds": 20}
        with mock.patch("apex.fx_market.get_rate", side_effect=[base, quote]):
            with self.assertRaises(ValueError):
                get_cross_rate("EUR", "USD")

    def test_tgju_tehran_timestamp_parsing(self):
        stamp = parse_timestamp("2026-11-14 15:30:00")
        self.assertIsNotNone(stamp)
        # Tehran is UTC+03:30.
        self.assertEqual(stamp, int(datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc).timestamp()))


class SymbolDiscoveryTests(unittest.TestCase):
    def test_prefix_ranking_puts_sol_first_for_so(self):
        rows = [
            {"symbol": "SOG", "name_fa": "SOG"},
            {"symbol": "SOL", "name_fa": "سولانا"},
            {"symbol": "XSO", "name_fa": "XSO"},
        ]
        found = rank_search_candidates("SO", rows, limit=5)
        self.assertEqual([x["symbol"] for x in found], ["SOL", "SOG", "XSO"])

    def test_fx_spot_and_futures_catalogue_entries_exist(self):
        self.assertEqual(instrument_catalog.get_instrument("EURIRT")["market"], "spot")
        self.assertEqual(instrument_catalog.get_instrument("EURIRTF")["market"], "futures")
        self.assertTrue(instrument_catalog.get_instrument("EURIRTF")["synthetic_future"])
        self.assertIn("NZDIRT", instrument_catalog.INSTRUMENTS)
        self.assertIn("NZDIRTF", instrument_catalog.INSTRUMENTS)
        self.assertEqual(instrument_catalog.get_instrument("NZDIRT")["currency"], "NZD")

    def test_implied_major_fx_pairs_and_synthetic_futures_exist(self):
        self.assertEqual(instrument_catalog.get_instrument("EURUSD")["market"], "spot")
        self.assertEqual(instrument_catalog.get_instrument("EURUSDF")["market"], "futures")
        self.assertTrue(instrument_catalog.get_instrument("EURUSDF")["synthetic_future"])
        self.assertEqual(instrument_catalog.normalize_instrument_symbol("EUR/USD"), "EURUSD")

    def test_search_returns_ranked_symbol_with_native_quote_and_status(self):
        tick = {"base": "SOL", "display_price": 185.25, "display_currency": "USDT",
                "display_unit": "USDT", "price": 185.25, "status": "fresh",
                "stale": False, "trade_demo": True, "source": "Binance public"}
        with mock.patch.object(market_apis, "_binance_spot_search_universe", return_value=[
            {"symbol": "SOL", "name_fa": "سولانا", "category": "crypto", "market": "spot"},
            {"symbol": "SOG", "name_fa": "SOG", "category": "crypto", "market": "spot"},
        ]), mock.patch.object(market_apis, "get_ticker", return_value=tick):
            result = market_apis.search_symbol_suggestions("SO", limit=5)
        self.assertTrue(result["found"])
        self.assertEqual(result["items"][0]["symbol"], "SOL")
        self.assertTrue(result["items"][0]["available"])
        self.assertEqual(result["items"][0]["display_price"], 185.25)
        self.assertEqual(result["items"][0]["status"], "fresh")

    def test_search_can_find_usdt_reference_instrument(self):
        tick = {"base": "USDTIRT", "display_price": 1_000_000,
                "display_currency": "IRT", "display_unit": "تومان/تتر",
                "price": 1_000_000, "status": "unverified", "stale": False,
                "trade_demo": False, "source": "Wallex public"}
        with mock.patch.object(market_apis, "_binance_spot_search_universe", return_value=[]), \
             mock.patch.object(market_apis, "get_ticker", return_value=tick):
            result = market_apis.search_symbol_suggestions("USDT", limit=5)
        self.assertTrue(result["found"])
        self.assertTrue(any(row["symbol"] == "USDTIRT" for row in result["items"]))

    def test_alias_normalization(self):
        self.assertEqual(instrument_catalog.normalize_instrument_symbol("EUR-IRT"), "EURIRT")
        self.assertEqual(instrument_catalog.normalize_instrument_symbol("USD-IRT"), "USDIRT")


if __name__ == "__main__":
    unittest.main()
