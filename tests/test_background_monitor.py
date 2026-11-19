import threading
import unittest
from unittest.mock import Mock, patch
from apex import background_monitor


class BackgroundMonitorTests(unittest.TestCase):
    def test_run_cycle_requires_mapping(self):
        fake = Mock(manage_once=Mock(return_value={'pending_events': []}))
        self.assertEqual(background_monitor.run_cycle(fake), {'pending_events': []})
        fake.manage_once.assert_called_once()

    def test_run_cycle_rejects_invalid_result(self):
        fake = Mock(manage_once=Mock(return_value=None))
        with self.assertRaises(TypeError):
            background_monitor.run_cycle(fake)

    def test_run_cycle_skips_when_another_process_holds_the_lock(self):
        fake = Mock(manage_once=Mock(return_value={"ok": True}))
        with patch("apex.background_monitor._try_process_lock", return_value=None):
            result = background_monitor.run_cycle(fake)
        self.assertTrue(result["skipped"])
        fake.manage_once.assert_not_called()

    def test_process_lock_is_released_after_manager_error(self):
        failing = Mock(manage_once=Mock(side_effect=RuntimeError("failure")))
        with self.assertRaises(RuntimeError):
            background_monitor.run_cycle(failing)
        good = Mock(manage_once=Mock(return_value={"ok": True}))
        result = background_monitor.run_cycle(good)
        self.assertEqual(result, {"ok": True})

    def test_notification_cooldown(self):
        self.assertTrue(background_monitor.notification_due(0, 1000, 600))
        self.assertFalse(background_monitor.notification_due(500, 700, 600))
        self.assertFalse(background_monitor.notification_due(1000, 900, 600))

    def test_disabled_monitor_exits_without_cycle(self):
        stop = threading.Event()
        cycle = Mock(return_value={})
        background_monitor.monitor_loop(
            stop_event=stop, interval=30, cycle=cycle,
            enabled=lambda: False,
        )
        cycle.assert_not_called()

    def test_monitor_runs_cycle_and_stops_after_callback(self):
        stop = threading.Event()
        calls = []
        def cycle():
            calls.append(1)
            stop.set()
            return {'ok': True}
        background_monitor.monitor_loop(
            stop_event=stop, interval=30, cycle=cycle,
            enabled=lambda: True,
        )
        self.assertEqual(len(calls), 1)

    def test_monitor_handles_cycle_error(self):
        stop = threading.Event()
        errors = []
        def cycle():
            raise RuntimeError('expected')
        def on_error(exc):
            errors.append(str(exc))
            stop.set()
        background_monitor.monitor_loop(
            stop_event=stop, interval=30, cycle=cycle,
            enabled=lambda: True, on_error=on_error,
        )
        self.assertEqual(errors, ['expected'])
