import os
import tempfile
import unittest
from apex import storage as db


class PositionControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['APEX_DATA'] = self.tmp.name
        db.init_db()

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop('APEX_DATA', None)

    def test_add_margin_moves_only_free_demo_cash_into_open_margin(self):
        pos = db.open_position('BTC', 'LONG', 100, 2, 10, sl=95, tp1=110)
        before = db.get_wallet()['balance']
        result = db.add_position_margin(pos['id'], 5)
        current = db.list_open_positions()[0]
        self.assertAlmostEqual(result['margin_open'], 15)
        self.assertAlmostEqual(current['margin_open'], 15)
        self.assertAlmostEqual(current['qty_open'], pos['qty'])
        self.assertAlmostEqual(db.get_wallet()['balance'], before - 5)

    def test_add_margin_cannot_use_reserved_or_unavailable_cash(self):
        pos = db.open_position('BTC', 'LONG', 100, 2, 10)
        with self.assertRaises(ValueError):
            db.add_position_margin(pos['id'], 10_000)

    def test_position_risk_levels_are_checked_against_direction_and_order(self):
        pos = db.open_position('BTC', 'LONG', 100, 2, 10)
        result = db.update_position_risk_levels(pos['id'], tp1=110, tp2=120, sl=95, trail_pct=1.0)
        self.assertEqual((result['tp1'], result['tp2'], result['sl']), (110, 120, 95))
        with self.assertRaises(ValueError):
            db.update_position_risk_levels(pos['id'], tp1=90, sl=105)
        with self.assertRaises(ValueError):
            db.update_position_risk_levels(pos['id'], tp1=120, tp2=110)

    def test_closed_position_cannot_be_modified(self):
        pos = db.open_position('BTC', 'LONG', 100, 2, 10)
        db.close_position(pos['id'], 101)
        with self.assertRaises(ValueError):
            db.add_position_margin(pos['id'], 1)
        with self.assertRaises(ValueError):
            db.update_position_risk_levels(pos['id'], sl=95)
