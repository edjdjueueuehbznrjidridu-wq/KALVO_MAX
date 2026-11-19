import unittest
from apex.auto_trader import _plan


class AutoTraderTests(unittest.TestCase):
    def test_plan_is_demo_small_and_risk_bounded(self):
        plan = _plan("BTC", "LONG", 100.0, {"atr14": 1.0}, 100.0, free_cash=100.0)
        self.assertIsNotNone(plan)
        self.assertLessEqual(plan["margin"], 4.0)
        self.assertLessEqual(plan["leverage"], 2.0)
        self.assertLessEqual(plan["actual_risk"], plan["risk_budget"] * 1.05)
        self.assertLess(plan["sl"], 100.0)
        self.assertGreater(plan["tp1"], 100.0)

    def test_short_plan_has_correct_directional_levels(self):
        plan = _plan("ETH", "SHORT", 200.0, {"atr14": 2.0}, 100.0, free_cash=100.0)
        self.assertIsNotNone(plan)
        self.assertGreater(plan["sl"], 200.0)
        self.assertLess(plan["tp1"], 200.0)
        self.assertLess(plan["tp2"], plan["tp1"])

    def test_no_plan_when_atr_missing(self):
        self.assertIsNone(_plan("BTC", "LONG", 100.0, {}, 100.0))


if __name__ == "__main__":
    unittest.main()

class AutoTraderConfigurationTests(unittest.TestCase):
    def test_auto_config_clamps_unsafe_or_invalid_saved_values(self):
        from unittest.mock import patch
        from apex.auto_trader import get_auto_config
        values = {
            "auto_max_positions": "99", "auto_risk_pct": "9",
            "auto_min_confluence": "10", "auto_max_leverage": "8",
            "auto_cooldown_hours": "0", "auto_scan_interval": "2",
        }
        with patch("apex.auto_trader.store.get_setting", side_effect=lambda key, default="": values.get(key, default)):
            config = get_auto_config()
        self.assertEqual(config["max_positions"], 4)
        self.assertEqual(config["risk_pct"], 0.50)
        self.assertEqual(config["min_confluence"], 60)
        self.assertEqual(config["max_leverage"], 2.0)
        self.assertEqual(config["cooldown_hours"], 1)
        self.assertEqual(config["scan_interval"], 60)

    def test_plan_rejects_non_finite_inputs(self):
        from apex.auto_trader import _plan
        for price, atr, equity in ((float("nan"), 1.0, 1000.0),
                                   (100.0, float("inf"), 1000.0),
                                   (100.0, 1.0, float("nan"))):
            with self.subTest(price=price, atr=atr, equity=equity):
                self.assertIsNone(_plan("BTC", "LONG", price,
                                        {"atr14": atr}, equity, free_cash=1000.0))

    def test_plan_respects_custom_demo_risk_budget_and_leverage(self):
        from apex.auto_trader import _plan
        plan = _plan("BTC", "LONG", 100.0, {"atr14": 1.0}, 1000.0,
                     free_cash=1000.0, risk_pct=0.20, max_leverage=1.0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["leverage"], 1.0)
        self.assertLessEqual(plan["actual_risk"], plan["risk_budget"] + 1e-9)
        self.assertLessEqual(plan["margin"], 20.0)

    def test_scan_uses_saved_watchlist_and_records_clear_reasons(self):
        from unittest.mock import patch
        from apex import auto_trader
        tf_result = {tf: {"ready": True, "signal": "LONG_BIAS", "confluence_score": 78,
                          "atr14": 1.0, "volatility_ratio": 1.0}
                     for tf in auto_trader.TIMEFRAMES}
        candidate = {
            "symbol": "ETH", "ready": True, "price": 100.0, "ticker_ts": 1_800_000_000,
            "ticker_source": "test", "ticker": {"source": "test", "spread_pct": 0.02,
                                           "price": 100.0, "bid": 99.99, "ask": 100.01,
                                           "status": "fresh", "quote_valid": True, "provider_ts": 1_800_000_000},
            "timeframes": tf_result,
            "consensus": {"ready": True, "aligned": True, "direction": "LONG"},
            "data_quality": {"trade_ready": True, "provider_consistent": True,
                             "quality_score": 98, "problems": []},
        }
        settings = {"auto_max_positions": "2", "auto_risk_pct": "0.35",
                    "auto_min_confluence": "70", "auto_max_leverage": "2",
                    "auto_cooldown_hours": "4", "auto_scan_interval": "120",
                    # This test isolates the local scanner path; AI autopilot is
                    # covered by the dedicated gate tests and is default for new installs.
                    "ai_gate": "off"}
        with patch.object(auto_trader.store, "watchlist_list", return_value=[{"symbol": "ETH-USDT"}]), \
             patch.object(auto_trader.store, "get_setting", side_effect=lambda k, d="": settings.get(k, d)), \
             patch.object(auto_trader.store, "list_open_positions", return_value=[]), \
             patch.object(auto_trader.store, "get_wallet", return_value={"balance": 1000.0}), \
             patch.object(auto_trader.store, "auto_cooldown_active", return_value=False), \
             patch.object(auto_trader.learning_engine, "adaptive_profile", return_value={
                 "risk_scale": 1.0, "min_confluence": 70, "auto_paused": False, "note": "base"
             }), \
             patch.object(auto_trader, "inspect_symbol", return_value=candidate) as inspect, \
             patch.object(auto_trader.store, "open_position", return_value={"id": 9, "entry_fee": 0.0}):
            result = auto_trader.scan_once(now=1_800_000_000)
        inspect.assert_called_once_with("ETH", 1_800_000_000.0)
        self.assertEqual(result["watchlist"], ["ETH"])
        self.assertEqual(len(result["opened"]), 1)
        self.assertEqual(result["opened"][0]["id"], 9)

    def test_scan_report_shows_skipped_reasons(self):
        from apex.auto_trader import format_scan_report
        report = format_scan_report({
            "generated_ts": 1_800_000_000, "watchlist": ["BTC"], "checked": 1,
            "opened": [], "skipped": [("BTC", "قیمت تازه نیست")],
            "evaluated": [], "message": "معامله‌ای باز نشد", "config": {
                "risk_pct": 0.35, "min_confluence": 70, "max_positions": 2,
                "max_leverage": 2.0,
            },
        })
        self.assertIn("دلایل رد", report)
        self.assertIn("قیمت تازه نیست", report)


class AutoTraderAIDefaultTests(unittest.TestCase):
    def test_new_install_uses_ai_autopilot_by_default(self):
        from apex.defaults import AI_GATE_DEFAULT
        self.assertEqual(AI_GATE_DEFAULT, "autopilot")


class AutoTraderAutopilotGateTests(unittest.TestCase):
    def test_ai_unavailable_fails_closed(self):
        from apex.auto_trader import _autopilot_block_reason
        self.assertIsNotNone(_autopilot_block_reason({"ok": False}))

    def test_low_confidence_fails_closed(self):
        from apex.auto_trader import _autopilot_block_reason
        self.assertIsNotNone(_autopilot_block_reason({
            "ok": True, "verdict": "approve", "confidence": 69
        }))

    def test_malformed_confidence_fails_closed(self):
        from apex.auto_trader import _autopilot_block_reason
        self.assertIsNotNone(_autopilot_block_reason({
            "ok": True, "verdict": "approve", "confidence": "unknown"
        }))

    def test_valid_ai_approval_is_accepted_by_gate(self):
        from apex.auto_trader import _autopilot_block_reason
        self.assertIsNone(_autopilot_block_reason({
            "ok": True, "verdict": "approve", "confidence": 82,
            "side": "LONG", "side_present": True,
            "size_mult": 0.75, "size_mult_present": True,
        }))

    def test_ai_veto_is_preserved(self):
        from apex.auto_trader import _autopilot_block_reason
        reason = _autopilot_block_reason({
            "ok": True, "verdict": "veto", "confidence": 94, "reason": "ریسک بالا"
        })
        self.assertIn("ریسک بالا", reason)
