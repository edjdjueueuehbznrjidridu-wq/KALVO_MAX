# -*- coding: utf-8 -*-
"""python-for-android service entry point for KALVO paper-only monitoring."""
from __future__ import annotations

import json
import os
import signal
import threading
import time


def _load_args():
    try:
        return json.loads(os.environ.get("PYTHON_SERVICE_ARGUMENT", "{}") or "{}")
    except Exception:
        return {}


def main():
    # Ask python-for-android to restart this service after a process exit where
    # the OS permits it. This remains best-effort, not a 24/7 guarantee.
    try:
        from jnius import autoclass
        PythonService = autoclass("org.kivy.android.PythonService")
        PythonService.mService.setAutoRestartService(True)
    except Exception:
        pass
    args = _load_args()
    data_dir = str(args.get("data_dir") or "").strip()
    if data_dir:
        os.environ["APEX_DATA"] = data_dir
        os.environ["KALVO_LOG_DIR"] = os.path.join(data_dir, "logs")

    from apex import storage, runtime, background_monitor

    stop_event = threading.Event()
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    except Exception:
        pass

    try:
        storage.init_db()
    except Exception as exc:
        runtime.log_exception("background monitor database init", exc)
        return

    def is_enabled():
        return storage.get_setting("background_monitor_enabled", "0") == "1"

    def on_cycle(result):
        try:
            storage.set_setting("background_monitor_last_cycle", str(int(time.time())))
            storage.set_setting("background_monitor_last_status", "ok")
            counts = result or {}
            actionable = any(counts.get(key) for key in ("pending_events", "risk_events", "alert_events"))
            try:
                last_notice = float(storage.get_setting("background_monitor_last_notice", "0") or 0)
            except (TypeError, ValueError):
                last_notice = 0.0
            # Collapse recurring manager events into one digest notification per 10 minutes.
            if actionable and background_monitor.notification_due(last_notice, time.time(), 600):
                storage.notification_add(
                    "background", "KALVO · پایش موقعیت",
                    "رویداد جدیدی در دفترکل شبیه‌سازی ثبت شد؛ برای جزئیات برنامه را باز کن.",
                )
                storage.set_setting("background_monitor_last_notice", str(int(time.time())))
        except Exception as exc:
            runtime.log_exception("background monitor cycle status", exc)

    def on_error(exc):
        runtime.log_exception("background monitor cycle", exc)
        try:
            storage.set_setting("background_monitor_last_status", "error")
        except Exception:
            pass

    background_monitor.monitor_loop(
        stop_event=stop_event,
        interval=int(args.get("interval_seconds") or 60),
        enabled=is_enabled,
        on_cycle=on_cycle,
        on_error=on_error,
    )


if __name__ == "__main__":
    main()
