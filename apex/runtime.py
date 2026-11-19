# -*- coding: utf-8 -*-
"""Bounded background task runner and crash diagnostics for KALVO UI."""
from __future__ import annotations

import concurrent.futures
import logging
import logging.handlers
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

_lock = threading.RLock()
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="KALVO-IO")
_TASK_SLOTS = threading.BoundedSemaphore(18)  # 6 running + at most 12 queued
_logger: Optional[logging.Logger] = None


def _get_logger() -> logging.Logger:
    global _logger
    with _lock:
        if _logger is not None:
            return _logger
        logger = logging.getLogger("kalvo.runtime")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            try:
                root = Path(os.environ.get("KALVO_LOG_DIR") or Path.home() / ".kalvo" / "logs")
                root.mkdir(parents=True, exist_ok=True)
                handler = logging.handlers.RotatingFileHandler(
                    root / "runtime.log", maxBytes=1_000_000, backupCount=3,
                    encoding="utf-8",
                )
                handler.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s %(threadName)s %(message)s"
                ))
                logger.addHandler(handler)
            except Exception:
                logger.addHandler(logging.NullHandler())
        _logger = logger
        return logger


def log_exception(context: str, exc: BaseException) -> None:
    try:
        _get_logger().error("%s: %s\n%s", context, exc, "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ))
    except Exception:
        pass


def submit(target: Callable[..., Any], *args: Any, context: str = "background task",
           **kwargs: Any) -> concurrent.futures.Future:
    """Submit work to a bounded executor; retain exceptions in logs and Future."""
    if not _TASK_SLOTS.acquire(blocking=False):
        raise RuntimeError("KALVO پس‌زمینه مشغول است؛ درخواست تکراری در صف قرار نگرفت")
    try:
        future = _executor.submit(target, *args, **kwargs)
    except Exception:
        _TASK_SLOTS.release()
        raise

    def _done(completed: concurrent.futures.Future) -> None:
        try:
            error = completed.exception()
            if error is not None:
                log_exception(context, error)
        except concurrent.futures.CancelledError:
            return
        except Exception as exc:
            log_exception("background callback", exc)
        finally:
            _TASK_SLOTS.release()

    future.add_done_callback(_done)
    return future


def install_exception_hooks() -> None:
    """Log uncaught UI/main/thread exceptions without silently swallowing them."""
    logger = _get_logger()
    previous_sys = sys.excepthook

    def _sys_hook(exc_type, exc_value, exc_tb):
        try:
            logger.error("Uncaught main exception", exc_info=(exc_type, exc_value, exc_tb))
        finally:
            previous_sys(exc_type, exc_value, exc_tb)

    sys.excepthook = _sys_hook

    if hasattr(threading, "excepthook"):
        previous_thread = threading.excepthook

        def _thread_hook(args):
            try:
                logger.error("Uncaught worker exception", exc_info=(
                    args.exc_type, args.exc_value, args.exc_traceback
                ))
            finally:
                previous_thread(args)

        threading.excepthook = _thread_hook


def shutdown(wait: bool = False) -> None:
    try:
        _executor.shutdown(wait=wait, cancel_futures=not wait)
    except TypeError:
        _executor.shutdown(wait=wait)
