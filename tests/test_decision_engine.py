import unittest
from apex.decision_engine import evaluate_entry


class DecisionEngineTests(unittest.TestCase):
    def candidate(self):
        frames = {
            tf: {"ready": True, "signal": "LONG_BIAS", "confluence_score": 82,
                 "volatility_ratio": 1.1, "rsi14": 58}
            for tf in ("15min", "1hour", "4hour")
        }
        return {
            "consensus": {"direction": "LONG", "aligned": True},
            "data_quality": {"trade_ready": True, "provider_consistent": True,
                             "quality_score": 98, "problems": []},
            "timeframes": frames,
            "ticker": {"spread_pct": 0.03},
        }

    def test_clean_candidate_passes_only_as_paper_decision(self):
        result = evaluate_entry(self.candidate(), minimum_score=70)
        self.assertTrue(result["allow"])
        self.assertEqual(result["decision"], "APPROVE_PAPER")
        self.assertEqual(result["execution_scope"], "paper_only")

    def test_missing_provider_quality_is_rejected(self):
        candidate = self.candidate()
        candidate["data_quality"]["provider_consistent"] = False
        result = evaluate_entry(candidate)
        self.assertFalse(result["allow"])
        self.assertIn("REJECT", result["decision"])

    def test_ai_direction_flip_must_match_local_timeframes(self):
        candidate = self.candidate()
        candidate["consensus"]["direction"] = "SHORT"
        result = evaluate_entry(candidate)
        self.assertFalse(result["allow"])
        self.assertTrue(any("هم‌خوان" in x for x in result["reasons"]))

    def test_high_spread_is_rejected(self):
        candidate = self.candidate()
        candidate["ticker"]["spread_pct"] = 0.8
        self.assertFalse(evaluate_entry(candidate)["allow"])

    def test_unknown_spread_is_not_eligible_for_automatic_entry(self):
        candidate = self.candidate()
        candidate["ticker"].pop("spread_pct")
        result = evaluate_entry(candidate)
        self.assertFalse(result["allow"])
        self.assertTrue(any("اسپرد قابل محاسبه نیست" in item for item in result["reasons"]))

    def test_news_veto_is_respected(self):
        result = evaluate_entry(self.candidate(), news_gate={"enabled": True, "allow": False,
                                                              "reason": "high event risk"})
        self.assertFalse(result["allow"])
