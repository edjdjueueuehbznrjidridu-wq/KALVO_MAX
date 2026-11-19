import unittest
from apex.strategy_lab import backtest, walk_forward


def candles(count=240):
    rows = []
    price = 100.0
    for i in range(count):
        # Deterministic cycles to produce crossovers and reversals.
        price += (1.0 if (i // 20) % 2 == 0 else -0.8) + ((i % 5) - 2) * 0.03
        rows.append({"time": i * 3600, "open": price - .2, "high": price + .8,
                     "low": price - .8, "close": price, "volume": 100 + i})
    return rows


class StrategyLabTests(unittest.TestCase):
    def test_backtest_returns_costs_and_metrics(self):
        result = backtest(candles(), "ema_cross")
        self.assertEqual(result["candles"], 240)
        self.assertGreater(result["stats"]["fees_paid"], 0)
        self.assertGreaterEqual(result["stats"]["max_drawdown"], 0)
        self.assertTrue(result["equity_curve"])

    def test_supported_strategies(self):
        for strategy in ("ema_cross", "rsi_rebound", "donchian_breakout"):
            result = backtest(candles(), strategy)
            self.assertIn("stats", result)

    def test_rejects_short_series(self):
        with self.assertRaises(ValueError):
            backtest(candles(30))

    def test_walk_forward_uses_chronological_holdout(self):
        result = walk_forward(candles(240))
        self.assertEqual(result["split_index"], 168)
        self.assertIn("validation", result)

    def test_invalid_capital_and_strategy_rejected(self):
        with self.assertRaises(ValueError):
            backtest(candles(), initial_equity=0)
        with self.assertRaises(ValueError):
            backtest(candles(), strategy="unknown")


if __name__ == "__main__":
    unittest.main()
