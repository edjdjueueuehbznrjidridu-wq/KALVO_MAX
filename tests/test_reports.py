import os
import tempfile
import unittest
import time
from apex import storage as db

class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); os.environ['APEX_DATA']=self.tmp.name; db.init_db()
    def tearDown(self):
        self.tmp.cleanup(); os.environ.pop('APEX_DATA',None)
    def test_period_map_is_exact(self):
        self.assertEqual(db.report_period_seconds('1d'),86400)
        self.assertEqual(db.report_period_seconds('3d'),259200)
        self.assertEqual(db.report_period_seconds('1w'),604800)
        self.assertEqual(db.report_period_seconds('1m'),2592000)
        self.assertEqual(db.report_period_seconds('1y'),31536000)
        with self.assertRaises(ValueError): db.report_period_seconds('7y')
    def test_period_report_includes_recent_closures(self):
        p=db.open_position('BTC','LONG',100,1,5)
        db.close_position(p['id'],101,100)
        rows=db.report_trades('1d')
        self.assertEqual(len(rows),1)
        self.assertIn('symbol',rows[0])
        csv_data=db.export_period_csv('1d')
        self.assertIn('BTC',csv_data)
