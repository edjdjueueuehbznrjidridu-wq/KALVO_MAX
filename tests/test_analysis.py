import unittest
from apex import analysis_engine as analysis
from apex.analysis_engine import analyze_candles, auto_entry_signal, completed_candles


class AnalysisTests(unittest.TestCase):
    def test_short_series_is_not_presented_as_valid_signal(self):
        candles = [{"open": 100, "high": 102, "low": 99, "close": 101, "volume": 1} for _ in range(20)]
        result = analyze_candles(candles)
        self.assertFalse(result["ready"])
        self.assertIsNone(auto_entry_signal(result))

    def test_indicators_are_finite_for_valid_series(self):
        candles = []
        price = 100.0
        for i in range(100):
            old = price
            price *= 1.002
            candles.append({"time": i, "open": old, "high": price * 1.002,
                            "low": old * 0.998, "close": price, "volume": 10 + i})
        result = analyze_candles(candles)
        self.assertTrue(result["ready"])
        self.assertIsNotNone(result["rsi14"])
        self.assertIsNotNone(result["ema50"])
        self.assertIn(result["signal"], ("LONG_BIAS", "SHORT_BIAS", "LONG_WATCH", "SHORT_WATCH", "NEUTRAL"))

    def test_incomplete_current_candle_is_excluded(self):
        candles = [
            {"time": 1000, "open": 10, "high": 11, "low": 9, "close": 10.5},
            {"time": 1900, "open": 10.5, "high": 12, "low": 10, "close": 11.5},
        ]
        completed = completed_candles(candles, "15min", now=2000)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["time"], 1000)

    def test_invalid_ohlc_rows_are_discarded(self):
        candles = [{"open": 100, "high": 99, "low": 101, "close": 100, "volume": 1} for _ in range(45)]
        self.assertFalse(analyze_candles(candles)["ready"])

    def test_extended_indicators_and_confluence_score_are_present(self):
        candles = []
        price = 100.0
        for i in range(260):
            old = price
            price *= 1.001 if i % 9 else 0.999
            candles.append({"time": i, "open": old, "high": max(old, price) * 1.002,
                            "low": min(old, price) * 0.998, "close": price,
                            "volume": 100 + (i % 17) * 2})
        result = analyze_candles(candles)
        self.assertTrue(result["ready"])
        for key in ("ema200", "adx14", "bb_upper", "bb_lower", "volume_ratio", "macd_hist"):
            self.assertIsNotNone(result[key])
        self.assertGreaterEqual(result["confluence_score"], 0)
        self.assertLessEqual(result["confluence_score"], 100)

    def test_multi_timeframe_consensus_never_counts_missing_frames_as_neutral(self):
        from apex.analysis_engine import combine_timeframes
        one = {"ready": True, "signal": "LONG_BIAS", "directional_score": 80}
        out = combine_timeframes({"15min": one, "1hour": one})
        self.assertFalse(out["ready"])

    def test_multi_timeframe_consensus_requires_all_three_aligned(self):
        from apex.analysis_engine import combine_timeframes
        long = {"ready": True, "signal": "LONG_BIAS", "directional_score": 80}
        short = {"ready": True, "signal": "SHORT_BIAS", "directional_score": -80}
        aligned = combine_timeframes({"15min": long, "1hour": long, "4hour": long})
        mixed = combine_timeframes({"15min": long, "1hour": long, "4hour": short})
        self.assertTrue(aligned["aligned"])
        self.assertFalse(mixed["aligned"])


    def test_market_regime_and_atr_scenario_ranges_are_added(self):
        import math
        rows = []
        price = 100.0
        for i in range(260):
            op = price
            price *= 1.001 + 0.0005 * math.sin(i / 4)
            rows.append({"open": op, "high": max(op, price) * 1.003,
                         "low": min(op, price) * 0.997, "close": price,
                         "volume": 1000 + (i % 17) * 10, "time": i * 3600})
        result = analysis.analyze_candles(rows)
        self.assertTrue(result["ready"])
        self.assertIn("market_regime", result)
        self.assertIn("volatility_state", result)
        self.assertGreater(result["scenario_up_1atr"], result["price"])
        self.assertLess(result["scenario_down_1atr"], result["price"])

if __name__ == "__main__":
    unittest.main()
