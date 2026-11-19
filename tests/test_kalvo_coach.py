import ast
import pathlib
import unittest
from unittest.mock import patch

from apex import learning
from apex.market_assistant import answer_question


class KalvoCoachTests(unittest.TestCase):
    def test_weekly_review_filters_to_last_seven_days(self):
        now = 1_800_000_000
        rows = [
            {"id": 2, "ts": now - 3600, "symbol": "BTC-USDT", "pnl": 4.0, "reason": "TP"},
            {"id": 1, "ts": now - 8 * 86400, "symbol": "ETH-USDT", "pnl": -9.0, "reason": "SL"},
        ]
        with patch.object(learning.storage, "list_trades", return_value=rows):
            result = learning.generate_weekly_review(now=now)
        self.assertEqual(result["stats"]["sample"], 1)
        self.assertEqual(result["stats"]["net_pnl"], 4.0)
        self.assertIn("فقط ژورنال دمو", result["report"])
        self.assertIn("تضمین سود", result["report"])

    def test_weekly_review_does_not_overclaim_small_sample(self):
        with patch.object(learning.storage, "list_trades", return_value=[]):
            result = learning.generate_weekly_review(now=1_800_000_000)
        self.assertIn("نمونه کمتر از ۵ خروج", result["report"])
        self.assertEqual(result["stats"]["sample"], 0)

    def test_contextual_assistant_answers_about_open_demo_positions(self):
        position = {"id": 1, "symbol": "BTC-USDT", "side": "LONG", "entry": 100,
                    "qty_open": 1, "leverage": 2, "margin_open": 50}
        answer = answer_question("وضعیت پوزیشن های من چیست؟", positions=[position],
                                 price_map={"BTC-USDT": 110})
        self.assertIn("BTC-USDT", answer)
        self.assertIn("PnL تخمینی", answer)
        self.assertIn("مجازی", answer)

    def test_contextual_assistant_explains_signal_only_when_analysis_exists(self):
        answer = answer_question("چرا سیگنال خنثی است؟", analysis={"ready": False})
        self.assertIn("ابتدا تحلیل", answer)

    def test_contextual_assistant_explains_last_auto_scan(self):
        audit = "گزارش اسکن · BTC رد شد: قیمت تازه نیست"
        answer = answer_question("چرا اتو ترید معامله باز نکرد؟", auto_report=audit)
        self.assertIn("قیمت تازه نیست", answer)
        self.assertIn("گزارش ممیزی", answer)

    def test_contextual_assistant_explains_adaptive_learning_without_overclaim(self):
        answer = answer_question("سیستم از معاملات من چی یاد گرفته؟", learning_profile={
            "sample": 23, "risk_scale": 0.5, "min_confluence": 85,
            "state": "reduced", "loss_streak": 2,
            "note": "ریسک کاهش یافته است.", "profit_factor_recent": 0.7,
        })
        self.assertIn("نمونهٔ معاملات بسته‌شده: 23", answer)
        self.assertIn("ریسک را خودکار افزایش نمی‌دهد", answer)


class KalvoUiContractTests(unittest.TestCase):
    def test_header_calls_existing_update_market_method(self):
        source = pathlib.Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertTrue(any(call.func.attr == "update_market" for call in calls))
        self.assertFalse(any(call.func.attr == "update" and isinstance(call.func.value, ast.Name)
                             and call.func.value.id == "header" for call in calls))

    def test_app_initializes_migrated_database_before_loading_preferences(self):
        source = pathlib.Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        app = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "KALVOApp")
        build = next(node for node in app.body if isinstance(node, ast.FunctionDef) and node.name == "_build_ui")
        calls = [node for node in ast.walk(build) if isinstance(node, ast.Call)]
        wrapper = next(node for node in app.body if isinstance(node, ast.FunctionDef) and node.name == "build")
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "_build_ui" for node in ast.walk(wrapper)))
        configure = next(i for i, n in enumerate(calls) if isinstance(n.func, ast.Attribute) and n.func.attr == "configure_data_dir")
        init = next(i for i, n in enumerate(calls) if isinstance(n.func, ast.Attribute) and n.func.attr == "init_db")
        settings = next(i for i, n in enumerate(calls) if isinstance(n.func, ast.Attribute) and n.func.attr == "get_setting")
        self.assertLess(configure, settings)
        self.assertLess(init, settings)


if __name__ == "__main__":
    unittest.main()
