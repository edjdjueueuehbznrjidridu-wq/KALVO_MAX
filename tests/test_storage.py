import os
import tempfile
import unittest
import threading

from apex import storage as db


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["APEX_DATA"] = self.tmp.name
        db.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("APEX_DATA", None)

    def test_withdrawal_cannot_make_balance_negative(self):
        with self.assertRaises(ValueError):
            db.withdraw(150)
        self.assertAlmostEqual(db.get_wallet()["balance"], 100.0)

    def test_concurrent_withdrawals_cannot_overdraw_account(self):
        outcomes = []
        barrier = threading.Barrier(2)

        def withdraw():
            barrier.wait()
            try:
                db.withdraw(70)
                outcomes.append("ok")
            except ValueError:
                outcomes.append("rejected")

        threads = [threading.Thread(target=withdraw) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertCountEqual(outcomes, ["ok", "rejected"])
        self.assertAlmostEqual(db.get_wallet()["balance"], 30.0)

    def test_zero_and_negative_deposits_rejected(self):
        for amount in (0, -5, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                db.deposit(amount)
        self.assertAlmostEqual(db.get_wallet()["balance"], 100.0)

    def test_open_position_reserves_margin_and_both_sides_close(self):
        p = db.open_position("BTC", "LONG", 100, 2, 10)
        self.assertAlmostEqual(db.get_wallet()["balance"], 89.988)
        r = db.close_position(p["id"], 110, 100)
        self.assertGreater(r["pnl"], 0)
        self.assertEqual(db.list_open_positions(), [])
        self.assertGreater(db.get_wallet()["balance"], 100)
        self.assertAlmostEqual(db.get_wallet()["balance"], 101.9748)

        short = db.open_position("ETH-USDT", "SHORT", 100, 2, 10)
        loss = db.close_position(short["id"], 110, 100)
        self.assertLess(loss["pnl"], 0)

    def test_validation_does_not_silently_clamp_leverage(self):
        with self.assertRaises(ValueError):
            db.open_position("BTC", "LONG", 100, 125, 10)
        with self.assertRaises(ValueError):
            db.open_position("BTC", "LONG", 100, 0, 10)
        with self.assertRaises(ValueError):
            db.open_position("BTC", "LONG", 100, 2, 10, sl=101)

    def test_staged_take_profit_closes_30_30_then_remainder(self):
        p = db.open_position("BTC", "LONG", 100, 2, 10,
                             tp1=102, tp2=104, tp3=106)
        self.assertEqual(db.apply_tp_sl_trail({"BTC": 103}), [f"TP1 پوزیشن #{p['id']}"])
        remaining = db.list_open_positions()[0]["qty_open"]
        self.assertAlmostEqual(remaining / p["qty"], 0.70)
        db.apply_tp_sl_trail({"BTC": 105})
        remaining = db.list_open_positions()[0]["qty_open"]
        self.assertAlmostEqual(remaining / p["qty"], 0.40)
        db.apply_tp_sl_trail({"BTC": 107})
        self.assertEqual(db.list_open_positions(), [])
        closed_qty = sum(t["qty"] for t in db.list_trades())
        self.assertAlmostEqual(closed_qty, p["qty"])

    def test_two_take_profits_split_position_in_half(self):
        position = db.open_position("BTC", "LONG", 100, 2, 10, tp1=105, tp2=110)
        db.apply_tp_sl_trail({"BTC": 106})
        current = db.list_open_positions()[0]
        self.assertAlmostEqual(current["qty_open"], position["qty"] * 0.5)
        db.apply_tp_sl_trail({"BTC": 111})
        self.assertEqual(db.list_open_positions(), [])
        self.assertEqual([t["reason"] for t in db.list_trades()], ["TP2", "TP1"])

    def test_take_profit_levels_must_be_strictly_ordered(self):
        with self.assertRaises(ValueError):
            db.open_position("BTC", "LONG", 100, 2, 10, tp1=105, tp2=105)

    def test_single_take_profit_closes_entire_position(self):
        p = db.open_position("BTC", "LONG", 100, 2, 10, tp1=101)
        db.apply_tp_sl_trail({"BTC": 102})
        self.assertEqual(db.list_open_positions(), [])
        self.assertEqual(db.list_trades()[0]["reason"], "TP1")

    def test_trailing_stop_moves_up_and_closes_once(self):
        p = db.open_position("BTC", "LONG", 100, 2, 10, trail_pct=1)
        db.apply_tp_sl_trail({"BTC": 101.5})
        current = db.list_open_positions()[0]
        self.assertTrue(current["trail_active"])
        self.assertAlmostEqual(current["qty_open"], p["qty"])
        first_stop = current["trail_stop"]
        db.apply_tp_sl_trail({"BTC": 103})
        current = db.list_open_positions()[0]
        self.assertGreater(current["trail_stop"], first_stop)
        db.apply_tp_sl_trail({"BTC": 101})
        self.assertEqual(db.list_open_positions(), [])
        self.assertEqual(db.list_trades()[0]["reason"], "TRAIL")

    def test_limit_order_waits_then_fills_once(self):
        order = db.create_order("ETH", "LONG", "LIMIT", 90, 2, 5)
        self.assertEqual(len(db.list_pending_orders()), 1)
        self.assertEqual(db.process_pending_orders({"ETH": 95}), [])
        events = db.process_pending_orders({"ETH": 89})
        self.assertEqual(len(events), 1)
        self.assertEqual(db.list_pending_orders(), [])
        pos = db.list_open_positions()[0]
        self.assertEqual(pos["source"], "pending")
        self.assertEqual(pos["entry"], 89)

    def test_equity_includes_locked_margin_and_unrealized_pnl(self):
        p = db.open_position("BTC", "LONG", 100, 2, 10)
        e = db.equity_with_marks({"BTC": 105})
        self.assertAlmostEqual(e["locked_margin"], 10)
        gross = p["qty"] * 5
        estimated_exit_fee = 105 * p["qty"] * db.FEE_RATE
        self.assertAlmostEqual(e["unrealized"], gross - estimated_exit_fee)
        self.assertAlmostEqual(e["equity"], 100 - p["entry_fee"] + gross - estimated_exit_fee)
        position = db.list_open_positions()[0]
        marked = db.position_pnl(position, 105)
        self.assertAlmostEqual(marked["pnl"], gross - estimated_exit_fee - p["entry_fee"])

    def test_partial_close_percent_is_not_silently_clamped(self):
        p = db.open_position("BTC", "LONG", 100, 2, 10)
        with self.assertRaises(ValueError):
            db.close_position(p["id"], 100, 0)
        with self.assertRaises(ValueError):
            db.close_position(p["id"], 100, 130)

    def test_pending_order_reserves_funds_from_market_orders(self):
        db.create_order("ETH", "LONG", "LIMIT", 90, 2, 80)
        with self.assertRaises(ValueError):
            db.open_position("BTC", "LONG", 100, 2, 30)
        self.assertEqual(db.list_open_positions(), [])
        self.assertEqual(len(db.list_pending_orders()), 1)

    def test_pending_order_reservation_also_protects_withdrawal(self):
        order = db.create_order("ETH", "LONG", "LIMIT", 90, 2, 80)
        with self.assertRaises(ValueError):
            db.withdraw(20)
        summary = db.equity_with_marks({})
        self.assertGreater(summary["pending_reserved"], 80)
        self.assertLess(summary["available_balance"], 20)
        self.assertTrue(db.cancel_order(order["id"]))
        db.withdraw(80)
        self.assertAlmostEqual(db.get_wallet()["balance"], 20.0)

    def test_auto_cooldown_is_symbol_specific(self):
        p = db.open_position("BTC", "LONG", 100, 1, 5, source="auto")
        self.assertTrue(db.auto_cooldown_active("BTC", 14400))
        self.assertFalse(db.auto_cooldown_active("ETH", 14400))
        db.close_position(p["id"], 101, 100)
        self.assertTrue(db.auto_cooldown_active("BTC-USDT", 14400))

    def test_stop_order_waits_then_fills_on_breakout(self):
        order = db.create_order("BTC", "LONG", "STOP", 110, 2, 2)
        self.assertEqual(db.process_pending_orders({"BTC": 105}), [])
        self.assertEqual(len(db.list_pending_orders()), 1)
        events = db.process_pending_orders({"BTC": 111})
        self.assertEqual(len(events), 1)
        self.assertEqual(db.list_open_positions()[0]["entry"], 111)
        self.assertEqual(db.list_pending_orders(), [])

    def test_stop_loss_closes_position_once(self):
        position = db.open_position("BTC", "LONG", 100, 2, 10, sl=95)
        events = db.apply_tp_sl_trail({"BTC": 94})
        self.assertEqual(len(events), 1)
        self.assertEqual(db.list_open_positions(), [])
        self.assertEqual(db.list_trades()[0]["reason"], "SL")
        self.assertEqual(db.apply_tp_sl_trail({"BTC": 93}), [])

    def test_position_manager_never_fakes_exit_fill_when_bid_ask_is_missing(self):
        position = db.open_position("BTC", "LONG", 100, 2, 10, sl=95)
        events = db.apply_tp_sl_trail({"BTC": 94, "BTC|SELL": None})
        self.assertEqual(events, [])
        self.assertEqual(len(db.list_open_positions()), 1)
        self.assertEqual(db.list_trades(), [])

    def test_position_manager_closes_at_executable_bid_not_last_trade_mark(self):
        db.open_position("BTC", "LONG", 100, 2, 10, sl=95)
        events = db.apply_tp_sl_trail({"BTC": 94, "BTC|SELL": 93.8})
        self.assertEqual(len(events), 1)
        trades = db.list_trades()
        self.assertEqual(trades[0]["reason"], "SL")
        self.assertAlmostEqual(trades[0]["exit"], 93.8)

    def test_pending_market_trigger_does_not_fill_without_executable_quote(self):
        db.create_order("BTC", "LONG", "STOP", 110, 2, 2)
        self.assertEqual(db.process_pending_orders({"BTC": 111, "BTC|BUY": None}), [])
        self.assertEqual(len(db.list_pending_orders()), 1)
        events = db.process_pending_orders({"BTC": 111, "BTC|BUY": 111.2})
        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(db.list_open_positions()[0]["entry"], 111.2)

    def test_pending_entry_rejects_wide_spread_even_with_valid_fill_prices(self):
        db.create_order("BTC", "LONG", "STOP", 110, 2, 2)
        prices = {"BTC": 111, "BTC|BUY": 111.2, "BTC|SPREAD_PCT": 2.1}
        self.assertEqual(db.process_pending_orders(prices), [])
        self.assertEqual(len(db.list_pending_orders()), 1)

    def test_cancelling_pending_order_releases_reserved_balance(self):
        order = db.create_order("BTC", "LONG", "LIMIT", 90, 2, 80)
        self.assertTrue(db.cancel_order(order["id"]))
        self.assertEqual(db.list_pending_orders(), [])
        position = db.open_position("ETH", "LONG", 100, 1, 80)
        self.assertTrue(position["id"] > 0)

    def test_short_trailing_stop_moves_down(self):
        p = db.open_position("BTC", "SHORT", 100, 2, 10, trail_pct=1)
        db.apply_tp_sl_trail({"BTC": 98.5})
        first = db.list_open_positions()[0]
        first_stop = first["trail_stop"]
        self.assertTrue(first["trail_active"])
        db.apply_tp_sl_trail({"BTC": 97})
        moved = db.list_open_positions()[0]
        self.assertLess(moved["trail_stop"], first_stop)
        db.apply_tp_sl_trail({"BTC": 98})
        self.assertEqual(db.list_open_positions(), [])
        self.assertEqual(db.list_trades()[0]["reason"], "TRAIL")


if __name__ == "__main__":
    unittest.main()
