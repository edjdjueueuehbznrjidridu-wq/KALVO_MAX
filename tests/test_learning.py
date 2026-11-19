import unittest
import time
from unittest.mock import patch

from apex import learning


class LearningTests(unittest.TestCase):
    def test_empty_journal_is_not_overclaimed(self):
        with patch.object(learning.storage, "list_trades", return_value=[]):
            result = learning.analyze_history()
        self.assertFalse(result["ok"])
        self.assertEqual(result["sample"], 0)

    def test_history_statistics_are_descriptive_and_sample_gated(self):
        trades = [
            {"id": 3, "symbol": "BTC-USDT", "side": "LONG", "pnl": 4},
            {"id": 2, "symbol": "BTC-USDT", "side": "LONG", "pnl": -2},
            {"id": 1, "symbol": "ETH-USDT", "side": "SHORT", "pnl": 0},
        ]
        with patch.object(learning.storage, "list_trades", return_value=trades):
            result = learning.analyze_history()
        self.assertEqual(result["sample"], 3)
        self.assertAlmostEqual(result["win_rate"], 100 / 3)
        self.assertEqual(result["net_pnl"], 2)
        self.assertEqual(result["max_drawdown"], 2)
        self.assertFalse(result["by_symbol"])  # groups require at least 3, ETH/BTC each below threshold
        self.assertIn("نمونهٔ بسیار کم", result["confidence_note"])

    def test_report_does_not_call_the_stats_a_prediction(self):
        result = {"ok": True, "sample": 12, "wins": 7, "losses": 4, "flats": 1,
                  "win_rate": 58.3, "net_pnl": 8.2, "avg_win": 2.0, "avg_loss": 1.5,
                  "profit_factor": 2.3, "gross_profit": 14, "max_drawdown": 3.1,
                  "confidence_note": "نمونه محدود", "by_symbol": {}, "by_side": {},
                  "tips": ["روش را بازبینی کن"]}
        report = learning.format_history_fa(result)
        self.assertIn("مدل پیش‌بینی نیست", report)


    def test_adaptive_profile_only_reduces_risk_after_large_sample(self):
        trades = [{"pnl": -1.0, "symbol": "BTC-USDT", "side": "LONG"} for _ in range(20)]
        with patch.object(learning.storage, "list_trades", return_value=trades):
            profile = learning.adaptive_profile()
        self.assertTrue(profile["auto_paused"])
        self.assertEqual(profile["risk_scale"], 0.0)
        self.assertEqual(profile["loss_streak"], 20)
        # A mixed record sample must never raise risk above its base value.
        mixed = [{"pnl": 2.0}, {"pnl": -1.0}] * 10
        with patch.object(learning.storage, "list_trades", return_value=mixed):
            profile = learning.adaptive_profile()
        self.assertLessEqual(profile["risk_scale"], 1.0)

    def test_journal_groups_exit_reason_and_strategy_source_with_sample_gates(self):
        trades = []
        for i in range(15):
            trades.append({"id": i, "symbol": "BTC-USDT", "side": "LONG", "pnl": 2.0,
                           "reason": "TP1", "trade_source": "manual"})
        for i in range(15, 30):
            trades.append({"id": i, "symbol": "ETH-USDT", "side": "SHORT", "pnl": -1.0,
                           "reason": "SL", "trade_source": "auto"})
        with patch.object(learning.storage, "list_trades", return_value=trades):
            result = learning.analyze_history()
        self.assertIn("TP1", result["by_reason"])
        self.assertIn("SL", result["by_reason"])
        self.assertIn("manual", result["by_source"])
        self.assertEqual(result["best_symbols"][0], "BTC-USDT")
        self.assertIn("ETH-USDT", result["worst_symbols"])
        formatted = learning.format_history_fa(result)
        self.assertIn("علت خروج", formatted)
        self.assertIn("روش ورود", formatted)

if __name__ == "__main__":
    unittest.main()
