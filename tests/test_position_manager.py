import unittest
from unittest.mock import patch

from apex import position_manager as manager


class PositionManagerTests(unittest.TestCase):
    def test_unverified_quote_never_marks_or_processes_paper_positions(self):
        quote = {"price": 100.0, "status": "unverified", "quote_valid": False,
                 "retrieval_valid": True, "source": "bybit", "base": "BTC"}
        with patch.object(manager.storage, "list_open_positions", return_value=[
                 {"id": 1, "symbol": "BTC", "side": "LONG", "source": "auto"}
             ]), patch.object(manager.storage, "list_pending_orders", return_value=[]), \
             patch.object(manager.storage, "alerts_open", return_value=[]), \
             patch.object(manager.data_hub, "get_quotes_batch", return_value={"BTC": quote}), \
             patch.object(manager.storage, "process_pending_orders") as pending, \
             patch.object(manager.storage, "apply_tp_sl_trail") as trailing, \
             patch.object(manager.storage, "process_alerts") as alerts, \
             patch.object(manager.storage, "get_setting", return_value="1"):
            result = manager.manage_once(now=1_800_000_000)
        self.assertEqual(result["prices"], {})
        pending.assert_not_called()
        trailing.assert_not_called()
        alerts.assert_not_called()

    def test_manager_is_explicitly_paper_only(self):
        with patch.object(manager.storage, "list_open_positions", return_value=[]), \
             patch.object(manager.storage, "list_pending_orders", return_value=[]), \
             patch.object(manager.storage, "alerts_open", return_value=[]), \
             patch.object(manager.data_hub, "get_quotes_batch", return_value={}), \
             patch.object(manager.storage, "get_setting", return_value="1"):
            result = manager.manage_once(now=1_800_000_000)
        self.assertEqual(result["mode"], "paper_only")


class PositionManagerAIExitTests(unittest.TestCase):
    def test_thesis_exit_requires_ai_close_and_confidence_at_least_80(self):
        position = {"id": 41, "symbol": "BTC", "side": "LONG", "source": "auto"}
        thesis = {
            "score": 92,
            "bundle": {"quote": {"price": 100, "source": "binance",
                                  "provider_ts": 1_800_000_000, "status": "fresh"},
                       "trade_ready": True, "provider_consistent": True,
                       "quality_score": 98, "problems": []},
            "frames": {"1hour": {"ready": True, "signal": "SHORT_BIAS"}},
        }
        from apex import coarena_ai
        with patch.object(manager.storage, "get_setting", side_effect=lambda k, d="": d), \
             patch.object(manager.storage, "set_setting"), \
             patch.object(coarena_ai, "chat", return_value='{"decision":"close","confidence":79,"reason":"تأیید ضعیف"}'):
            result = manager._ai_confirm_thesis_exit(position, thesis, 1_800_000_000)
        self.assertFalse(result["approved"])

    def test_thesis_exit_accepts_explicit_high_confidence_ai_confirmation(self):
        position = {"id": 42, "symbol": "ETH", "side": "SHORT", "source": "auto"}
        thesis = {
            "score": 91,
            "bundle": {"quote": {"price": 100, "source": "binance",
                                  "provider_ts": 1_800_000_000, "status": "fresh"},
                       "trade_ready": True, "provider_consistent": True,
                       "quality_score": 98, "problems": []},
            "frames": {"1hour": {"ready": True, "signal": "LONG_BIAS"}},
        }
        from apex import coarena_ai
        with patch.object(manager.storage, "get_setting", side_effect=lambda k, d="": d), \
             patch.object(manager.storage, "set_setting"), \
             patch.object(coarena_ai, "resolve_key", return_value="unit-test-provider-key"), \
             patch.object(coarena_ai, "chat", return_value='{"decision":"close","confidence":88,"reason":"فرضیه نقض شده"}'):
            result = manager._ai_confirm_thesis_exit(position, thesis, 1_800_000_000)
        self.assertTrue(result["approved"])
        self.assertEqual(result["status"], "confirmed")
