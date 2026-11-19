# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from apex import commodity_market as cm
from apex import instrument_catalog as catalog
from apex import market_apis
from apex import auto_trader, spot_auto
from apex import hybrid_ai
from apex.config import load_env, get_env


class InstrumentCatalogTests(unittest.TestCase):
    def test_supported_global_domestic_and_energy_symbols(self):
        expected = {
            "XAU", "XAG", "IRG18", "IRS999", "IRS925",
            "WTI", "BRENT", "XAUF", "XAGF", "WTIF", "BRTF",
        }
        self.assertTrue(expected.issubset(set(catalog.INSTRUMENTS)))

    def test_aliases_normalize_to_canonical_symbols(self):
        pairs = {
            "GOLD": "XAU", "SILVER": "XAG", "GOLD18": "IRG18",
            "18K": "IRG18", "OIL": "WTI", "OILBRENT": "BRENT",
            "GOLDF": "XAUF", "WTIFUT": "WTIF", "BRENTFUT": "BRTF",
            "USDT/IRT": "USDTIRT", "USDT-TMN": "USDTIRT",
        }
        for source, expected in pairs.items():
            with self.subTest(source=source):
                self.assertEqual(catalog.normalize_instrument_symbol(source), expected)

    def test_spot_and_futures_are_distinct(self):
        self.assertEqual(catalog.get_instrument("XAU")["market"], "spot")
        self.assertEqual(catalog.get_instrument("XAUF")["market"], "futures")
        self.assertEqual(catalog.get_instrument("WTI")["market"], "spot")
        self.assertEqual(catalog.get_instrument("WTIF")["market"], "futures")


class CommodityPricingTests(unittest.TestCase):
    def setUp(self):
        cm.clear_cache()

    def tearDown(self):
        cm.clear_cache()

    def test_troy_ounce_conversion_for_18k_gold(self):
        expected = 3000 * 90000 / 31.1034768 * 0.75
        self.assertAlmostEqual(cm.calculate_iran_metal_toman(3000, 90000, 0.75, 0), expected, places=6)

    def test_adjustment_is_bounded_and_applied_in_basis_points(self):
        base = cm.calculate_iran_metal_toman(3000, 90000, 0.75, 0)
        adjusted = cm.calculate_iran_metal_toman(3000, 90000, 0.75, 20)
        self.assertAlmostEqual(adjusted, base * 1.002, places=6)
        self.assertAlmostEqual(cm.calculate_iran_metal_toman(3000, 90000, 0.75, 9000), base * 1.05, places=6)

    def test_invalid_formula_inputs_are_rejected(self):
        for args in ((0, 90000, .75, 0), (3000, 0, .75, 0), (3000, 90000, 1.2, 0)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    cm.calculate_iran_metal_toman(*args)

    def test_iranian_currency_normalization(self):
        self.assertEqual(cm._record_price_toman({"price": 100, "currency": "IRR"}), 10)
        self.assertEqual(cm._record_price_toman({"price": 100, "currency": "IRT"}), 100)

    def test_missing_provider_timestamp_is_not_fresh(self):
        status, stale, age = cm._status(None, 180)
        self.assertEqual(status, "unverified")
        self.assertTrue(stale)
        self.assertIsNone(age)

    def test_gold_api_parser_preserves_provider_timestamp(self):
        stamp = int(time.time())
        with patch.object(cm, "_get_json", return_value={"price": 2350.5, "updatedAt": stamp, "name": "Gold"}):
            result = cm._fetch_gold_api("XAU")
        self.assertEqual(result["price"], 2350.5)
        self.assertEqual(result["observed_at"], stamp)
        self.assertEqual(result["quote_currency"], "USD")
        self.assertEqual(result["unit"], "troy_oz")
        self.assertFalse(result["stale"])

    def test_global_metal_uses_valid_tabloo_fallback_if_gold_api_fails(self):
        record = {"symbol": "XAU_USD", "price": 3883.92, "currency": "USD",
                  "unit": "troy_ounce", "timestamp": int(time.time()), "stale": False}
        with patch.object(cm, "_fetch_gold_api", side_effect=RuntimeError("primary offline")), \
             patch.object(cm, "_tabloo_record", return_value=record):
            result = cm._fetch_global_metal("XAU")
        self.assertEqual(result["price"], 3883.92)
        self.assertIn("Tabloo", result["source"])
        self.assertFalse(result["stale"])

    def test_global_metal_fallback_rejects_wrong_units(self):
        record = {"symbol": "XAU_USD", "price": 3883.92, "currency": "USD",
                  "unit": "gram", "timestamp": int(time.time()), "stale": False}
        with patch.object(cm, "_fetch_gold_api", side_effect=RuntimeError("primary offline")), \
             patch.object(cm, "_tabloo_record", return_value=record):
            with self.assertRaisesRegex(RuntimeError, "incompatible currency/unit"):
                cm._fetch_global_metal("XAU")

    def test_global_tabloo_fallback_keeps_delayed_five_hour_quote_visible(self):
        stamp = int(time.time()) - 5 * 3600
        raw = {"price": 3883.92, "observed_at": stamp, "source": "Tabloo/TGJU global metal",
               "status": "delayed", "stale": False, "age_seconds": 5 * 3600,
               "quote_currency": "USD", "unit": "troy_oz"}
        with patch.object(cm, "_fetch_global_metal", return_value=raw):
            result = cm.get_ticker("XAU")
        self.assertEqual(result["status"], "delayed")
        self.assertFalse(result["stale"])

    def test_wallex_uses_midpoint_when_bid_and_ask_exist(self):
        payload = {"result": {"symbols": {"USDTTMN": {"stats": {
            "bidPrice": "998000", "askPrice": "1002000", "lastPrice": "1000000"
        }}}}}
        with patch.object(cm, "_get_json", return_value=payload):
            result = cm._fetch_wallex_usdt_toman()
        self.assertEqual(result["price"], 1000000)
        self.assertEqual(result["bid"], 998000)
        self.assertEqual(result["ask"], 1002000)
        self.assertEqual(result["timestamp_quality"], "polled_at")
        self.assertEqual(result["status"], "unverified")

    def test_usdt_reference_uses_iran_public_fallback_when_wallex_fails(self):
        record = {"symbol": "USDT_IRR", "price": 1000000, "currency": "IRT",
                  "timestamp": int(time.time())}
        with patch.object(cm, "_fetch_wallex_usdt_toman", side_effect=RuntimeError("offline")), \
             patch.object(cm, "_tabloo_record", return_value=record):
            result = cm._usdt_toman()
        self.assertEqual(result["price"], 1000000)
        self.assertIn("Tabloo", result["source"])
        self.assertEqual(result["status"], "delayed")

    def test_tgju_cross_ticker_keeps_usd_native_quote(self):
        raw = {"price": 1.08, "quote_currency": "USD", "source": "TGJU implied cross",
               "observed_at": int(time.time()), "status": "fresh", "stale": False,
               "unit": "USD per 1 EUR"}
        spec = catalog.get_instrument("EURUSD")
        result = cm._make_ticker("EURUSD", raw, spec)
        self.assertAlmostEqual(result["display_price"], 1.08)
        self.assertEqual(result["display_currency"], "USD")
        self.assertAlmostEqual(result["price"], 1.08)
        self.assertTrue(result["trade_demo"])

    def test_native_local_quote_survives_usdt_bridge_exception(self):
        spec = catalog.get_instrument("EURIRT")
        raw = {"price": 650000, "quote_currency": "TOMAN", "source": "TGJU",
               "observed_at": int(time.time()), "status": "fresh", "stale": False}
        with patch.object(cm, "_usdt_toman", side_effect=RuntimeError("bridge offline")):
            result = cm._make_ticker("EURIRT", raw, spec)
        self.assertEqual(result["display_price"], 650000)
        self.assertEqual(result["display_currency"], "TOMAN")
        self.assertIsNone(result["price"])
        self.assertFalse(result["paper_conversion_available"])
        self.assertFalse(result["trade_demo"])

    def test_domestic_ticker_keeps_native_price_and_usdt_ledger_price(self):
        spec = catalog.get_instrument("IRG18")
        raw = {"price": 5000000, "quote_currency": "IRT", "source": "test",
               "observed_at": int(time.time()), "status": "fresh", "stale": False}
        with patch.object(cm, "_usdt_toman", return_value={"price": 1000000, "source": "Wallex"}):
            result = cm._make_ticker("IRG18", raw, spec)
        self.assertEqual(result["display_price"], 5000000)
        self.assertEqual(result["display_currency"], "TOMAN")
        self.assertAlmostEqual(result["price"], 5.0)
        self.assertEqual(result["display_unit"], "تومان/گرم")
        self.assertTrue(result["trade_demo"])

    def test_local_quote_remains_visible_when_usdt_conversion_is_unavailable(self):
        spec = catalog.get_instrument("EURIRT")
        raw = {"price": 1100000, "quote_currency": "TOMAN", "source": "TGJU",
               "observed_at": int(time.time()), "status": "fresh", "stale": False}
        with patch.object(cm, "_usdt_toman", return_value={"price": None, "stale": True, "source": "unavailable"}):
            result = cm._make_ticker("EURIRT", raw, spec)
        self.assertEqual(result["display_price"], 1100000)
        self.assertEqual(result["display_currency"], "TOMAN")
        self.assertIsNone(result["price"])
        self.assertFalse(result["paper_conversion_available"])
        self.assertFalse(result["trade_demo"])

    def test_global_spot_ticker_is_usd_per_troy_ounce(self):
        spec = catalog.get_instrument("XAU")
        raw = {"price": 2400, "quote_currency": "USD", "source": "Gold-API",
               "observed_at": int(time.time()), "status": "fresh", "stale": False}
        result = cm._make_ticker("XAU", raw, spec)
        self.assertEqual(result["display_price"], 2400)
        self.assertEqual(result["display_currency"], "USD")
        self.assertEqual(result["display_unit"], "USD/oz t")

    def test_fred_key_is_required_and_no_fake_key_is_used(self):
        with patch.object(cm, "get_env", return_value=""):
            with patch("apex.storage.get_setting", return_value=""):
                with self.assertRaisesRegex(RuntimeError, "FRED_API_KEY"):
                    cm._fetch_fred("DCOILWTICO")

    def test_wti_spot_chart_never_substitutes_futures_candles(self):
        with patch.object(cm, "_tabloo_history", return_value=[]), \
             patch.object(cm, "_yahoo_candles") as yahoo:
            self.assertEqual(cm.fetch_candles("WTI", "1day"), [])
            self.assertEqual(cm.fetch_candles("WTI", "1hour"), [])
            yahoo.assert_not_called()


class IntegrationBoundaryTests(unittest.TestCase):
    def test_market_api_routes_reference_usdt_irt(self):
        with patch("apex.commodity_market.get_reference_ticker", return_value={"base": "USDTIRT", "price": 1000000}) as provider:
            result = market_apis.get_ticker("USDTIRT")
        self.assertEqual(result["base"], "USDTIRT")
        provider.assert_called_once_with("USDTIRT")

    def test_market_api_routes_commodity_spot(self):
        with patch("apex.commodity_market.get_ticker", return_value={"base": "XAU", "price": 2400}) as provider:
            result = market_apis.get_ticker("GOLD")
        self.assertEqual(result["base"], "XAU")
        provider.assert_called_once_with("XAU")

    def test_reference_fx_pair_is_not_added_to_portfolio_marks(self):
        with patch.object(market_apis, "get_ticker", side_effect=lambda s: {"price": 65000, "stale": False, "status": "fresh", "quote_valid": True}) as provider:
            result = market_apis.get_prices_batch(["USDTIRT", "BTC"])
        self.assertIn("BTC", result)
        self.assertNotIn("USDTIRT", result)
        self.assertEqual(provider.call_count, 1)

    def test_auto_watchlist_excludes_commodities(self):
        result = auto_trader._watch_symbols(["BTC", "XAUF", "IRG18", "WTI", "ETH"])
        self.assertEqual(result, ["BTC", "ETH"])

    def test_commodity_only_watchlist_does_not_fall_back_to_crypto(self):
        self.assertEqual(auto_trader._watch_symbols(["XAUF", "IRG18", "WTI"]), [])

    def test_auto_inspector_rejects_commodity(self):
        result = auto_trader.inspect_symbol("XAUF", now=1000)
        self.assertFalse(result["ready"])
        self.assertIn("فقط روی رمزارزها", result["reason"])

    def test_spot_auto_rejects_commodity(self):
        result = spot_auto.scan_once("IRG18", now=1000)
        self.assertEqual(result["action"], "HOLD")
        self.assertIn("فقط رمزارزها", result["reason"])

    def test_ai_context_contains_native_unit_source_and_freshness(self):
        ticker = {"base": "IRG18", "name_fa": "طلای ۱۸ عیار ایران", "category": "iran_metals",
                  "market": "spot", "price": 5.1, "paper_quote_currency": "USDT",
                  "display_price": 5100000, "display_currency": "IRT", "display_unit": "تومان/گرم",
                  "source": "Gold-API + FX", "status": "estimated", "stale": False,
                  "age_seconds": 30, "formula": "XAU * FX / 31.1034768 * 18/24",
                  "fx_source": "USD/IRT", "adjustment_bps": 20}
        context = hybrid_ai.build_context_block("IRG18", None, ticker, None, None)
        for token in ("تومان/گرم", "estimated", "31.1034768", "USD/IRT", "5100000"):
            self.assertIn(token, context)

    def test_ai_context_marks_derived_fx_and_synthetic_future(self):
        ticker = {"base": "EURUSDF", "name_fa": "قرارداد تمرینی یورو/دلار",
                  "category": "fx_cross", "market": "futures", "price": 1.08,
                  "display_price": 1.08, "display_currency": "USD",
                  "display_unit": "USD به‌ازای ۱ یورو", "source": "TGJU implied cross",
                  "status": "fresh", "stale": False, "synthetic_future": True,
                  "market_note": "شاخص آتی تمرینی؛ قرارداد واقعی نیست",
                  "conversion": "EUR/IRT ÷ USD/IRT"}
        context = hybrid_ai.build_context_block("EURUSDF", None, ticker, None, None)
        self.assertIn("نرخ بین‌بانکی یا بروکری جهانی نیست", context)
        self.assertIn("قرارداد آتی واقعی نیست", context)
        self.assertIn("EUR/IRT ÷ USD/IRT", context)

    def test_local_ai_can_answer_with_native_iranian_gold_quote(self):
        from apex import market_assistant
        ticker = {"base": "IRG18", "name_fa": "طلای ۱۸ عیار ایران",
                  "display_price": 5100000.0, "display_currency": "IRT",
                  "display_unit": "تومان/گرم", "source": "Tabloo/TGJU direct",
                  "status": "delayed", "age_seconds": 900, "pricing_method": "direct_domestic"}
        answer = market_assistant.answer_question("قیمت طلای ۱۸ عیار چنده؟", ticker=ticker)
        self.assertIn("5,100,000", answer)
        self.assertIn("تومان/گرم", answer)
        self.assertIn("با تأخیر", answer)

    def test_env_loader_reads_only_configured_values(self):
        original = {key: os.environ.get(key) for key in ("FRED_API_KEY", "IRAN_METAL_ADJUSTMENT_BPS")}
        try:
            with tempfile.TemporaryDirectory() as temp:
                env = Path(temp) / ".env"
                env.write_text("FRED_API_KEY=demo-value\nIRAN_METAL_ADJUSTMENT_BPS=25\n", encoding="utf-8")
                values = load_env(env, override=True)
                self.assertEqual(values["FRED_API_KEY"], "demo-value")
                self.assertEqual(get_env("FRED_API_KEY"), "demo-value")
                self.assertEqual(get_env("IRAN_METAL_ADJUSTMENT_BPS"), "25")
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
