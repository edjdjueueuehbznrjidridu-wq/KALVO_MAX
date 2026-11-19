import os
import tempfile
import unittest
from unittest.mock import patch

from apex import ai_core, coarena_ai, defaults, hybrid_ai
from apex import auto_trader, learning_memory as lm


class SecurityTests(unittest.TestCase):
    def test_provider_key_is_never_bundled_and_user_key_is_resolved(self):
        self.assertEqual(getattr(defaults, "DEFAULT_COARENA_API_KEY", ""), "")
        with patch.dict(os.environ, {"COARENA_API_KEY": "", "COARENA_KEY": ""}):
            self.assertEqual(coarena_ai.resolve_key(""), "")
            self.assertEqual(coarena_ai.resolve_key(None), "")
        self.assertEqual(coarena_ai.resolve_key("  user-supplied-key  "), "user-supplied-key")

    def test_provider_key_can_come_from_environment(self):
        with patch.dict(os.environ, {"COARENA_API_KEY": "env-test-key-123"}):
            self.assertEqual(coarena_ai.resolve_key(""), "env-test-key-123")

    def test_context_hides_account_by_default(self):
        pos = [{"symbol": "BTC", "side": "LONG", "qty": 1, "entry": 123456.789,
                "leverage": 2, "tp": 1, "sl": 1, "upnl": 5}]
        wallet = {"equity": 987.654, "free": 1, "locked": 2}
        hidden = hybrid_ai.build_context_block("BTC", None, None, pos, wallet)
        self.assertNotIn("123456.789", hidden)
        self.assertNotIn("987.654", hidden)
        shown = hybrid_ai.build_context_block("BTC", None, None, pos, wallet, share_account=True)
        self.assertIn("987.654", shown)

    def test_context_uses_real_indicator_keys(self):
        text = hybrid_ai.build_context_block("BTC", {"rsi14": 61.5, "adx14": 27.1, "atr14": 3.2}, None, None, None)
        self.assertIn("rsi14: 61.5", text)
        self.assertIn("adx14: 27.1", text)


class CatalogTests(unittest.TestCase):
    def test_full_22_model_catalog(self):
        ids = defaults.AI_MODEL_IDS
        self.assertEqual(len(ids), 22)
        self.assertEqual(len(set(ids)), 22)
        for mid in ("gpt-6-1-sol", "gpt-6-1-terra", "gpt-6-astra", "gpt-6-luna",
                    "claude-opus-5-5", "qwen3-8-2-4t-a95b"):
            self.assertIn(mid, ids)

    def test_out_of_range_size_mult_fails_closed_instead_of_clamping(self):
        r = hybrid_ai.parse_gate_reply('{"verdict":"approve","side":"long","size_mult":9,"confidence":60}')
        self.assertFalse(r["ok"])
        self.assertEqual(r["size_mult"], 0.0)
        self.assertEqual(r["side"], "")
        r = hybrid_ai.parse_gate_reply('{"verdict":"approve","size_mult":0.01,"confidence":80}')
        self.assertFalse(r["ok"])

    def test_autopilot_reply_requires_explicit_side_and_size(self):
        r = hybrid_ai.parse_gate_reply('{"verdict":"approve","confidence":80}')
        self.assertTrue(r["ok"])
        self.assertFalse(r["side_present"])
        self.assertFalse(r["size_mult_present"])


class GateParsingTests(unittest.TestCase):
    def test_valid_and_malformed_replies(self):
        ok = hybrid_ai.parse_gate_reply('x {"verdict":"VETO","confidence":80,"reason":"r"} y')
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["verdict"], "veto")
        self.assertFalse(hybrid_ai.parse_gate_reply("نه")["ok"])
        self.assertFalse(hybrid_ai.parse_gate_reply('{"verdict":"increase"}')["ok"])


class RegimeAndRiskTests(unittest.TestCase):
    def test_regime_classification(self):
        self.assertEqual(ai_core.classify_regime({"adx14": 30, "volatility_ratio": 1.0})["regime"], "trend")
        self.assertEqual(ai_core.classify_regime({"adx14": 12, "volatility_ratio": 1.0})["regime"], "range")
        self.assertEqual(ai_core.classify_regime({"adx14": 22, "volatility_ratio": 1.0})["regime"], "mixed")
        self.assertEqual(ai_core.classify_regime({"adx14": 30, "volatility_ratio": 2.0})["regime"], "volatile")
        self.assertEqual(ai_core.classify_regime({})["regime"], "unknown")

    def test_assessment_is_reduce_only_and_explains(self):
        class Mem:
            @staticmethod
            def regime_bias(_r):
                return {"n": 12, "bias": -0.7}
        bad = ai_core.assess_candidate("BTC", "LONG", 80, {"adx14": 30}, Mem)
        self.assertFalse(bad["allow"])
        self.assertTrue(bad["explain"])
        base = ai_core.assess_candidate("BTC", "LONG", 80, {"adx14": 30}, None)
        self.assertTrue(base["allow"])
        self.assertLessEqual(base["risk_mult"], 1.0)

    def test_drawdown_governor(self):
        trades = [{"pnl": -9.0}] + [{"pnl": 5.0}]  # newest-first: +5 then -9 -> dd 9 on base 100 + peak 5
        with patch("apex.ai_core.storage.list_trades", return_value=trades), \
             patch("apex.ai_core.storage.get_wallet", return_value={"total_deposited": 100}):
            state = ai_core.drawdown_state()
        self.assertEqual(state["state"], "reduced")
        self.assertEqual(state["risk_mult"], 0.5)
        big = [{"pnl": -20.0}]
        with patch("apex.ai_core.storage.list_trades", return_value=big), \
             patch("apex.ai_core.storage.get_wallet", return_value={"total_deposited": 100}):
            self.assertTrue(ai_core.drawdown_state()["paused"])

    def test_memory_risk_scale_never_exceeds_one(self):
        for wr in (30, 50, 55, 80):
            with patch.object(lm, "_load", return_value={"last_hist": {"trades": 40, "win_rate": wr}}):
                self.assertLessEqual(lm.risk_scale(), 1.0)
        with patch.object(lm, "_load", return_value={"last_hist": {"trades": 5, "win_rate": 10}}):
            self.assertEqual(lm.risk_scale(), 1.0)  # too little data -> base policy

    def test_memory_learns_from_real_stat_keys(self):
        fake_hist = {"sample": 40, "win_rate": 60.0, "profit_factor": 1.5,
                     "by_symbol": {"BTC": {"trades": 10, "win_rate": 80.0, "net_pnl": 5, "avg_pnl": 0.5}},
                     "by_side": {"LONG": {"trades": 12, "win_rate": 70.0, "net_pnl": 3, "avg_pnl": 0.2}}}
        saved = {}
        with patch.object(lm.base, "analyze_history", return_value=fake_hist), \
             patch.object(lm, "_load", return_value={"symbol_bias": {}, "side_bias": {}, "sessions": 0}), \
             patch.object(lm, "_save", side_effect=lambda d: saved.update(d)), \
             patch.object(lm, "_regime_stats", return_value={}), \
             patch.object(lm.storage, "list_trades", return_value=[{"id": 9}]):
            lm.refresh_from_history()
        self.assertGreater(saved["symbol_bias"]["BTC"], 0)
        self.assertIn("long", saved["side_bias"])
        self.assertEqual(saved["last_hist"]["trades"], 40)


class ConfigCapsTests(unittest.TestCase):
    def test_plan_never_exceeds_leverage_cap(self):
        plan = auto_trader._plan("BTC", "LONG", 100.0, {"atr14": 1.0}, 1000.0,
                                 free_cash=1000.0, max_leverage=9.0, risk_scale=5.0)
        self.assertLessEqual(plan["leverage"], 2.0)
        self.assertLessEqual(plan["actual_risk"], plan["risk_budget"] * 1.001)

    def test_zero_risk_scale_blocks_entry(self):
        self.assertIsNone(auto_trader._plan("BTC", "LONG", 100.0, {"atr14": 1.0}, 1000.0,
                                            free_cash=1000.0, risk_scale=0.0))


class ScanIntegrationTests(unittest.TestCase):
    def _run_scan(self, gate_result, dd=None, profile_scale=1.0):
        tf = {k: {"ready": True, "signal": "LONG_BIAS", "confluence_score": 80,
                  "atr14": 1.0, "adx14": 30, "volatility_ratio": 1.0}
              for k in auto_trader.TIMEFRAMES}
        cand = {"symbol": "ETH", "ready": True, "price": 100.0, "ticker_ts": 1_800_000_000,
                "ticker_source": "t", "ticker": {"source": "t", "price": 100.0, "bid": 99.99, "ask": 100.01, "status": "fresh", "quote_valid": True, "provider_ts": 1_800_000_000, "spread_pct": 0.02},
                "timeframes": tf,
                "data_quality": {"trade_ready": True, "provider_consistent": True,
                                 "quality_score": 98, "problems": []},
                "consensus": {"ready": True, "aligned": True, "direction": "LONG"}}
        settings = {"auto_max_positions": "2", "ai_gate": "veto", "coarena_api_key": "k" * 12}
        dd = dd or {"state": "ok", "risk_mult": 1.0, "drawdown_pct": 0.0, "paused": False}
        opened = {}
        def fake_open(*a, **k):
            opened.update(k)
            return {"id": 5, "entry_fee": 0.0}
        S = auto_trader.store
        with patch.object(S, "watchlist_list", return_value=[{"symbol": "ETH"}]), \
             patch.object(S, "get_setting", side_effect=lambda k, d="": settings.get(k, d)), \
             patch.object(S, "list_open_positions", return_value=[]), \
             patch.object(S, "get_wallet", return_value={"balance": 1000.0}), \
             patch.object(S, "auto_cooldown_active", return_value=False), \
             patch.object(auto_trader.learning_engine, "adaptive_profile", return_value={
                 "risk_scale": profile_scale, "min_confluence": 70, "auto_paused": False}), \
             patch.object(auto_trader, "inspect_symbol", return_value=cand), \
             patch.object(auto_trader, "_ai_gate_review", return_value=gate_result), \
             patch.object(auto_trader.ai_core, "drawdown_state", return_value=dd), \
             patch.object(auto_trader.ai_core, "log_decision"), \
             patch.object(auto_trader.mem, "refresh_if_new_trades"), \
             patch.object(auto_trader.mem, "risk_scale", return_value=1.0), \
             patch.object(auto_trader.mem, "symbol_priority_boost", return_value=0.0), \
             patch.object(auto_trader.mem, "regime_bias", return_value={"n": 0, "bias": 0.0}), \
             patch.object(S, "open_position", side_effect=fake_open):
            return auto_trader.scan_once(now=1_800_000_000), opened

    def test_cloud_veto_blocks_entry(self):
        gate = {"ok": True, "verdict": "veto", "confidence": 90, "reason": "ریسک خبری", "mode": "veto"}
        result, opened = self._run_scan(gate)
        self.assertEqual(len(result["opened"]), 0)
        self.assertEqual(result["evaluated"][-1]["status"], "vetoed")
        self.assertFalse(opened)

    def test_cloud_failure_never_blocks_or_enlarges(self):
        gate = {"ok": False, "verdict": "unavailable", "confidence": 0, "reason": "x", "mode": "veto"}
        result, opened = self._run_scan(gate)
        self.assertEqual(len(result["opened"]), 1)
        self.assertIn("gate=unavailable", opened["note"])
        self.assertIn("regime=trend", opened["note"])

    def test_cloud_reduce_halves_risk(self):
        full, _ = self._run_scan({"ok": True, "verdict": "approve", "confidence": 80, "reason": "", "mode": "veto"})
        half, _ = self._run_scan({"ok": True, "verdict": "reduce", "confidence": 80, "reason": "", "mode": "veto"})
        self.assertAlmostEqual(half["opened"][0]["risk_budget"], full["opened"][0]["risk_budget"] / 2, places=6)

    def test_autopilot_fails_closed_when_ai_unavailable(self):
        gate = {"ok": False, "verdict": "unavailable", "confidence": 0, "reason": "x", "mode": "autopilot"}
        result, opened = self._run_scan(gate)
        self.assertEqual(len(result["opened"]), 0)
        self.assertFalse(opened)

    def test_autopilot_cannot_flip_against_timeframe_evidence(self):
        gate = {"ok": True, "verdict": "approve", "confidence": 70, "reason": "r",
                "side": "SHORT", "size_mult": 0.5, "mode": "autopilot"}
        result, opened = self._run_scan(gate)
        self.assertEqual(len(result["opened"]), 0)
        self.assertFalse(opened)
        self.assertIn("vetoed", [row.get("status") for row in result["evaluated"]])

    def test_drawdown_pause_stops_scanner(self):
        dd = {"state": "paused", "risk_mult": 0.0, "drawdown_pct": 17.0, "paused": True}
        result, opened = self._run_scan(None, dd=dd)
        self.assertTrue(result.get("paused"))
        self.assertFalse(opened)

    def test_profile_zero_scale_is_respected_not_bypassed(self):
        result, opened = self._run_scan(None, profile_scale=0.0)
        self.assertEqual(len(result["opened"]), 0)
        self.assertFalse(opened)


if __name__ == "__main__":
    unittest.main()


class V7SettingsMigrationTests(unittest.TestCase):
    def test_legacy_advisory_default_migrates_to_autopilot_once(self):
        from apex.app_migrations import migrate_v7_ai_gate
        state = {"ai_gate": "advisory", "auto_ai_gate": "advisory"}
        get = lambda key, default="": state.get(key, default)
        set_value = lambda key, value: state.__setitem__(key, value)
        result = migrate_v7_ai_gate(get, set_value)
        self.assertEqual(result["status"], "migrated")
        self.assertEqual(state["ai_gate"], "autopilot")
        self.assertEqual(state["auto_ai_gate"], "autopilot")
        state["ai_gate"] = "off"
        again = migrate_v7_ai_gate(get, set_value)
        self.assertEqual(again["status"], "already_migrated")
        self.assertEqual(state["ai_gate"], "off")

    def test_explicit_off_choice_is_preserved(self):
        from apex.app_migrations import migrate_v7_ai_gate
        state = {"ai_gate": "off", "auto_ai_gate": "veto"}
        get = lambda key, default="": state.get(key, default)
        set_value = lambda key, value: state.__setitem__(key, value)
        migrate_v7_ai_gate(get, set_value)
        self.assertEqual(state["ai_gate"], "off")
        self.assertEqual(state["auto_ai_gate"], "veto")


class AIContextSerializationTests(unittest.TestCase):
    def test_non_finite_context_values_are_safely_normalized(self):
        context = hybrid_ai.build_context_block(
            symbol="BTC", analysis={"score": float("nan")},
            ticker={"price": 100, "data_quality": {"quality": float("inf")},
                    "decision_context": {"risk": float("nan"), "nested": [float("-inf")]},
                    "news_context": {"confidence": float("nan")}},
            positions=None, wallet=None, share_account=False,
        )
        self.assertIn('"quality": null', context)
        self.assertIn('"risk": null', context)
        self.assertIn('"confidence": null', context)
