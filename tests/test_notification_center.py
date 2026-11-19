import os
import tempfile
import time
import unittest
from unittest.mock import patch
from apex import storage

class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("APEX_DATA")
        os.environ["APEX_DATA"] = self.tmp.name
        storage.init_db()

    def tearDown(self):
        if self.old is None:
            os.environ.pop("APEX_DATA", None)
        else:
            os.environ["APEX_DATA"] = self.old
        self.tmp.cleanup()

    def test_notification_add_list_mark_read(self):
        nid = storage.notification_add("test", "عنوان", "متن")
        self.assertEqual(len(storage.notifications_list(unread_only=True)), 1)
        self.assertEqual(storage.notifications_mark_read(nid), 1)
        self.assertEqual(storage.notifications_list(unread_only=True), [])

    def test_smart_alert_requires_fresh_snapshot_and_triggers_once(self):
        aid = storage.smart_alert_add("BTC", "above", 5.0)
        stale = {"BTC": {"change_pct": 8.0, "ts": time.time() - 600}}
        self.assertEqual(storage.process_smart_alerts(stale), [])
        fresh = {"BTC": {"change_pct": 8.0, "ts": time.time()}}
        events = storage.process_smart_alerts(fresh)
        self.assertEqual(len(events), 1)
        self.assertEqual(storage.process_smart_alerts(fresh), [])
        self.assertEqual(len(storage.notifications_list(unread_only=True)), 1)

    def test_invalid_smart_alert_rejected(self):
        with self.assertRaises(ValueError):
            storage.smart_alert_add("BTC", "sideways", 5)

if __name__ == "__main__":
    unittest.main()
