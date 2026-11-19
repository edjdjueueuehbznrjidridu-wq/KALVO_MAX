import os
import tempfile
import unittest

from apex import storage as db


class StorageExtrasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["APEX_DATA"] = self.tmp.name
        db.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("APEX_DATA", None)

    def test_watchlist_normalizes_and_upserts_symbol(self):
        db.watchlist_add("btc")
        db.watchlist_add("BTC-USDT", "main")
        rows = db.watchlist_list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "BTC-USDT")
        self.assertEqual(rows[0]["note"], "main")
        db.watchlist_remove("BTC")
        self.assertEqual(db.watchlist_list(), [])

    def test_price_alert_triggers_once_only_when_threshold_crossed(self):
        alert_id = db.alert_add("ETH", "above", 2000)
        self.assertEqual(db.process_alerts({"ETH": 1999}), [])
        events = db.process_alerts({"ETH-USDT": 2001})
        self.assertEqual(len(events), 1)
        self.assertIn(f"#{alert_id}", events[0])
        self.assertEqual(db.process_alerts({"ETH": 2100}), [])
        self.assertEqual(db.alerts_open(), [])

    def test_below_alert_and_cancel(self):
        db.alert_add("SOL", "below", 100)
        self.assertEqual(db.process_alerts({}), [])
        events = db.process_alerts({"SOL": 99})
        self.assertEqual(len(events), 1)
        alert_id = db.alert_add("BTC", "above", 50000)
        self.assertTrue(db.alert_cancel(alert_id))
        self.assertFalse(db.alert_cancel(alert_id))

    def test_risk_sizing_includes_fee_buffer_and_respects_bounds(self):
        r = db.risk_size(1000, 1, 100, 95, 2)
        self.assertAlmostEqual(r["risk_budget"], 10)
        self.assertAlmostEqual(r["risk_including_fee_buffer"], 10)
        self.assertGreater(r["margin"], 0)
        self.assertLess(r["margin"], r["notional"])
        for values in ((100, 0, 100, 95, 2), (100, 4, 100, 95, 2),
                       (100, 1, 100, 100, 2), (100, 1, 100, 95, 0)):
            with self.assertRaises(ValueError):
                db.risk_size(*values)


if __name__ == "__main__":
    unittest.main()

class SpotWalletTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["APEX_DATA"] = self.tmp.name
        db.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("APEX_DATA", None)

    def test_spot_buy_sell_applies_fee_and_tracks_realized_pnl(self):
        buy = db.spot_market_order("ETH", "BUY", 200, 100)
        self.assertAlmostEqual(buy["quantity"], 1.998)
        self.assertAlmostEqual(buy["usdt_balance"], 800)
        sell = db.spot_market_order("ETH", "SELL", 1, 120)
        self.assertAlmostEqual(sell["realized_pnl"], 19.7799, places=3)
        balances = {x["asset"]: x for x in db.get_spot_balances()}
        self.assertAlmostEqual(balances["ETH"]["free"], 0.998)
        self.assertGreater(balances["USDT"]["free"], 900)

    def test_spot_sell_cannot_create_short_inventory(self):
        with self.assertRaises(ValueError):
            db.spot_market_order("BTC", "SELL", 0.01, 50000)

    def test_spot_wallet_cannot_be_overdrawn(self):
        with self.assertRaises(ValueError):
            db.spot_market_order("BTC", "BUY", 1200, 50000)
        with self.assertRaises(ValueError):
            db.spot_withdraw(1200)

    def test_spot_deposit_withdraw_changes_virtual_usdt_balance(self):
        db.spot_deposit(100)
        db.spot_withdraw(25)
        balances = {x["asset"]: x for x in db.get_spot_balances()}
        self.assertAlmostEqual(balances["USDT"]["free"], 1075)

    def test_spot_summary_uses_only_provided_fresh_marks(self):
        db.spot_market_order("BTC", "BUY", 100, 50000)
        snap = db.spot_summary({"BTC": 55000})
        self.assertAlmostEqual(snap["total_known_value"], 1000.0 - 100 + (100 * 0.999 / 50000) * 55000)
        missing = db.spot_summary({})
        self.assertIn("BTC", missing["missing_prices"])

