# -*- coding: utf-8 -*-
"""Resilient, paper-only background position supervision primitives."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import runtime

DEFAULT_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 30
MAX_INTERVAL_SECONDS = 300
_LOCAL_CYCLE_LOCK = threading.Lock()


def _try_process_lock():
    """Acquire a process-wide lock shared by the app UI and Android service.

    The two Kivy-for-Android processes have different Python locks. A small
    advisory file lock prevents them from managing the same paper positions
    at the same time. On platforms without fcntl, a process-local lock remains
    as a conservative fallback.
    """
    try:
        from . import storage
        db_path = Path(storage._db_path())
        db_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(str(db_path.parent / "position-manager.lock"), "a+b")
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return ("file", handle)
        except BlockingIOError:
            handle.close()
            return None
        except ImportError:
            handle.close()
    except Exception:
        pass
    if not _LOCAL_CYCLE_LOCK.acquire(blocking=False):
        return None
    return ("thread", None)


def _release_process_lock(lock):
    if not lock:
        return
    kind, handle = lock
    if kind == "file" and handle is not None:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass
    elif kind == "thread":
        try:
            _LOCAL_CYCLE_LOCK.release()
        except RuntimeError:
            pass


def run_cycle(position_manager_module=None, *, extra_symbols=()) -> Dict[str, Any]:
    """Run one cross-process-serialized paper-ledger supervision cycle."""
    lock = _try_process_lock()
    if lock is None:
        return {"skipped": True, "reason": "position_manager_cycle_already_running"}
    try:
        if position_manager_module is None:
            from . import position_manager as position_manager_module
        result = position_manager_module.manage_once(extra_symbols=list(extra_symbols or []))
        if not isinstance(result, dict):
            raise TypeError("Position manager returned an invalid result")
        return result
    finally:
        _release_process_lock(lock)



def notification_due(last_sent: float, now: float, cooldown_seconds: int = 600) -> bool:
    """Return whether a manager digest notification is outside its cooldown."""
    try:
        last = float(last_sent)
        current = float(now)
        cooldown = max(60, min(86400, int(cooldown_seconds)))
        return current >= last and current - last >= cooldown
    except (TypeError, ValueError, OverflowError):
        return False

def monitor_loop(*, stop_event: threading.Event, interval: int = DEFAULT_INTERVAL_SECONDS,
                 cycle: Optional[Callable[[], Dict[str, Any]]] = None,
                 enabled: Optional[Callable[[], bool]] = None,
                 on_cycle: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_error: Optional[Callable[[BaseException], None]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
    """Run bounded supervision cycles until stopped or preference disabled.

    Android can terminate any process. This loop is not a guarantee of 24/7
    availability; it is designed to restart safely and never persist duplicate
    orders on its own.
    """
    wait_seconds = max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, int(interval)))
    next_run = clock()
    execute = cycle or run_cycle
    while not stop_event.is_set():
        if enabled is not None:
            try:
                if not enabled():
                    break
            except Exception as exc:
                if on_error:
                    on_error(exc)
                else:
                    runtime.log_exception("background monitor enabled-check", exc)
                stop_event.wait(wait_seconds)
                continue
        remaining = next_run - clock()
        if remaining > 0 and stop_event.wait(remaining):
            break
        started = clock()
        try:
            result = execute()
            if on_cycle:
                on_cycle(result)
        except Exception as exc:
            if on_error:
                try:
                    on_error(exc)
                except Exception as callback_exc:
                    runtime.log_exception("background monitor error callback", callback_exc)
            else:
                runtime.log_exception("background monitor cycle", exc)
        next_run = max(started + wait_seconds, clock())
