import os
import sqlite3
import tempfile
import unittest
import hashlib
from unittest.mock import patch

from apex import storage as db


LEGACY_SCHEMA = """
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE wallet(id INTEGER PRIMARY KEY CHECK(id=1), balance REAL, equity REAL,
    realized_pnl REAL, total_deposited REAL, peak_equity REAL, fees_paid REAL);
CREATE TABLE positions(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, side TEXT,
    entry REAL, qty REAL, qty_open REAL, leverage REAL, margin REAL, margin_open REAL,
    tp1 REAL, tp2 REAL, tp3 REAL, sl REAL, trail_pct REAL DEFAULT 0,
    trail_active INTEGER DEFAULT 0, status TEXT, opened_ts INTEGER,
    note TEXT DEFAULT '', source TEXT DEFAULT 'manual');
CREATE TABLE trades(id INTEGER PRIMARY KEY AUTOINCREMENT, pos_id INTEGER, symbol TEXT,
    side TEXT, entry REAL, exit REAL, qty REAL, leverage REAL, pnl REAL,
    pnl_pct REAL, fee REAL, reason TEXT, ts INTEGER);
CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, body TEXT,
    symbol TEXT, ts INTEGER);
CREATE TABLE ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, amount REAL,
    balance_after REAL, note TEXT, ts INTEGER);
CREATE TABLE chat_log(id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT,
    model TEXT, ts INTEGER);
"""


class MigrationTests(unittest.TestCase):
    def test_invalid_legacy_demo_balance_is_repaired_without_crash(self):
        with tempfile.TemporaryDirectory() as data_dir:
            os.environ["APEX_DATA"] = data_dir
            c = sqlite3.connect(os.path.join(data_dir, "apex.db"))
            c.executescript(LEGACY_SCHEMA)
            c.execute("INSERT INTO wallet VALUES(1,-50,-50,0,100,100,0)")
            c.commit()
            c.close()
            db.init_db()
            self.assertEqual(db.get_wallet()["balance"], 0)
            repair = db.list_ledger(1)[0]
            self.assertEqual(repair["type"], "migration_repair")
            self.assertEqual(repair["amount"], 50)

    def test_targeted_legacy_credential_scrub_preserves_a_different_user_key(self):
        with tempfile.TemporaryDirectory() as data_dir:
            previous = os.environ.get("APEX_DATA")
            os.environ["APEX_DATA"] = data_dir
            try:
                c = sqlite3.connect(os.path.join(data_dir, "apex.db"))
                c.executescript(LEGACY_SCHEMA)
                c.execute("INSERT INTO settings(key,value) VALUES('legacy_key_scrubbed','1')")
                c.execute("INSERT INTO settings(key,value) VALUES('coarena_api_key','known-legacy-value')")
                c.execute("INSERT INTO settings(key,value) VALUES('coarena_key','user-owned-value')")
                c.commit()
                c.close()
                fingerprint = hashlib.sha256(b"known-legacy-value").hexdigest()
                with patch.object(db, "_LEGACY_BUNDLED_KEY_SHA256", fingerprint):
                    db.init_db()
                self.assertEqual(db.get_setting("coarena_api_key", ""), "")
                self.assertEqual(db.get_setting("coarena_key", ""), "user-owned-value")
            finally:
                if previous is None:
                    os.environ.pop("APEX_DATA", None)
                else:
                    os.environ["APEX_DATA"] = previous

    def test_old_database_is_copied_and_additive_migrations_preserve_data(self):
        with tempfile.TemporaryDirectory() as old_dir, tempfile.TemporaryDirectory() as new_dir:
            old_path = os.path.join(old_dir, "apex.db")
            c = sqlite3.connect(old_path)
            c.executescript(LEGACY_SCHEMA)
            c.execute("INSERT INTO wallet VALUES(1, 42, 42, 3, 120, 150, 0.5)")
            c.execute("INSERT INTO notes(title,body,symbol,ts) VALUES('یادداشت','متن','BTC',1)")
            c.execute("INSERT INTO settings(key,value) VALUES('coarena_api_key','legacy-secret')")
            c.commit()
            c.close()

            previous = os.environ.get("APEX_DATA")
            os.environ["APEX_DATA"] = old_dir
            try:
                db.configure_data_dir(new_dir)
                db.init_db()
                wallet = db.get_wallet()
                self.assertEqual(wallet["balance"], 42)
                self.assertEqual(wallet["realized_pnl"], 3)
                self.assertEqual(len(db.list_notes()), 1)
                self.assertEqual(db.get_setting("coarena_api_key", ""), "")
                old_conn = sqlite3.connect(old_path)
                try:
                    self.assertIsNone(old_conn.execute(
                        "SELECT 1 FROM settings WHERE key='coarena_api_key'"
                    ).fetchone())
                finally:
                    old_conn.close()
                self.assertEqual(db.create_order("BTC", "LONG", "LIMIT", 40, 1, 1)["status"], "pending")
                new_conn = sqlite3.connect(os.path.join(new_dir, "apex.db"))
                try:
                    pos_cols = {r[1] for r in new_conn.execute("PRAGMA table_info(positions)")}
                    trade_cols = {r[1] for r in new_conn.execute("PRAGMA table_info(trades)")}
                    self.assertIn("trail_stop", pos_cols)
                    self.assertIn("entry_fee", trade_cols)
                    self.assertTrue(new_conn.execute("SELECT 1 FROM orders LIMIT 1").fetchone())
                finally:
                    new_conn.close()
            finally:
                if previous is None:
                    os.environ.pop("APEX_DATA", None)
                else:
                    os.environ["APEX_DATA"] = previous


if __name__ == "__main__":
    unittest.main()
