import unittest
from apex.portfolio_analytics import build_risk_report, scenario_table

class PortfolioAnalyticsTests(unittest.TestCase):
    def test_risk_report_metrics(self):
        trades = [
            {"pnl": -2, "ts": 100000, "symbol": "BTC"},
            {"pnl": 5, "ts": 100100, "symbol": "ETH"},
            {"pnl": -1, "ts": 100200, "symbol": "BTC"},
        ]
        positions = [{"symbol": "BTC", "side": "LONG", "entry": 100, "qty_open": 2, "margin_open": 50}]
        result = build_risk_report(trades, positions, {"BTC": 110}, initial_equity=100)
        self.assertEqual(result["trade_count"], 3)
        self.assertEqual(result["loss_streak"], 1)
        self.assertEqual(result["exposure"], 220)
        self.assertEqual(result["locked_margin"], 50)
        self.assertGreaterEqual(result["max_drawdown"], 0)

    def test_scenario_table_directionality(self):
        positions = [{"symbol": "BTC", "side": "LONG", "entry": 100, "qty_open": 1}]
        rows = scenario_table(positions, {"BTC": 100}, (-10, 10))
        self.assertEqual(rows[0]["unrealized_pnl"], -10)
        self.assertAlmostEqual(rows[1]["unrealized_pnl"], 10)

if __name__ == "__main__":
    unittest.main()
