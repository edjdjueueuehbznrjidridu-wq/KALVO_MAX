# -*- coding: utf-8 -*-
"""KALVO 7.0 continuous position supervision for the local paper ledger.

It marks only validated quotes, processes pending/TP/SL/trailing rules, checks
strategy-thesis invalidation on closed multi-timeframe candles, records every
action, and never sends an exchange order.
"""
from __future__ import annotations

import json
import time
import threading
from typing import Any, Dict, List

from . import analysis_engine as analysis
from . import market_data_hub as data_hub
from . import storage
from .execution_simulator import market_fill

AUDIT_KEY = "kalvo_position_manager_events_v1"
MAX_AUDIT = 250
THESIS_TIMEFRAMES = ("15min", "1hour", "4hour")
THESIS_INVALIDATION_SCORE = 85
_MANAGER_LOCK = threading.Lock()


def _audit(event: Dict[str, Any]) -> None:
    try:
        rows = json.loads(storage.get_setting(AUDIT_KEY, "[]") or "[]")
        if not isinstance(rows, list):
            rows = []
        rows.append({**event, "ts": int(event.get("ts") or time.time())})
        storage.set_setting(AUDIT_KEY, json.dumps(rows[-MAX_AUDIT:], ensure_ascii=False))
    except Exception:
        # Diagnostics must never interrupt position protection.
        return


def recent_events(limit: int = 30) -> List[Dict[str, Any]]:
    try:
        rows = json.loads(storage.get_setting(AUDIT_KEY, "[]") or "[]")
        return rows[-max(1, min(200, int(limit))):] if isinstance(rows, list) else []
    except Exception:
        return []


def _thesis_break(position: Dict[str, Any], now: float) -> Dict[str, Any]:
    symbol = str(position.get("symbol") or "")
    bundle = data_hub.get_market_bundle(symbol, THESIS_TIMEFRAMES, now=now)
    if not bundle.get("trade_ready"):
        return {"confirmed": False, "reason": "دادهٔ معتبر برای تأیید شکست استراتژی کافی نیست",
                "bundle": bundle}
    frames = bundle.get("timeframes") or {}
    side = str(position.get("side") or "").upper()
    opposite = "SHORT_BIAS" if side == "LONG" else "LONG_BIAS"
    scores = []
    for timeframe in THESIS_TIMEFRAMES:
        frame = frames.get(timeframe) or {}
        if not frame.get("ready") or frame.get("signal") != opposite:
            return {"confirmed": False,
                    "reason": f"شکست هم‌زمان استراتژی در {timeframe} تأیید نشد",
                    "bundle": bundle}
        score = int(frame.get("confluence_score") or 0)
        if score < THESIS_INVALIDATION_SCORE:
            return {"confirmed": False,
                    "reason": f"قدرت سیگنال مخالف در {timeframe} کافی نیست",
                    "bundle": bundle}
        scores.append(score)
    return {"confirmed": True, "reason": "سه تایم‌فریم بسته‌شده با امتیاز قوی، خلاف فرضیهٔ ورود هستند",
            "score": min(scores), "price": bundle.get("price"), "bundle": bundle,
            "frames": frames}


def _thesis_review_due(position: Dict[str, Any], now: float, cooldown_seconds: int = 60) -> bool:
    """Throttle the candle-heavy strategy review independently per position."""
    pid = int(position.get("id") or 0)
    key = f"kalvo_thesis_review_at_{pid}"
    try:
        last = float(storage.get_setting(key, "0") or 0)
    except (TypeError, ValueError):
        last = 0.0
    if now - last < max(15, int(cooldown_seconds)):
        return False
    storage.set_setting(key, str(int(now)))
    return True


def _ai_confirm_thesis_exit(position: Dict[str, Any], thesis: Dict[str, Any],
                            now: float) -> Dict[str, Any]:
    """Require a structured cloud-AI confirmation before thesis-based paper exit.

    Mechanical TP/SL/trailing protection is deliberately independent of this call.
    A per-position cooldown prevents repeated cloud calls from the fast supervisor.
    """
    pid = int(position.get("id") or 0)
    cooldown_key = f"kalvo_ai_exit_review_at_{pid}"
    try:
        last_review = float(storage.get_setting(cooldown_key, "0") or 0)
    except (TypeError, ValueError):
        last_review = 0.0
    if now - last_review < 300:
        return {"approved": False, "status": "cooldown",
                "reason": "بازبینی AI برای این موقعیت در وقفهٔ پنج‌دقیقه‌ای است."}

    # Persist the cooldown before network I/O to avoid duplicate requests.
    storage.set_setting(cooldown_key, str(int(now)))
    try:
        from . import coarena_ai, hybrid_ai
        from .config import get_env
        api_key = (storage.get_setting("coarena_api_key", "") or
                   get_env("COARENA_API_KEY") or "").strip()
        if not api_key:
            api_key = coarena_ai.resolve_key("")
        if not api_key or len(api_key) < 8:
            return {"approved": False, "status": "unavailable",
                    "reason": "کلید یا سرویس AI برای تأیید خروج در دسترس نیست."}

        bundle = thesis.get("bundle") or {}
        quote = dict(bundle.get("quote") or {})
        quote["data_quality"] = {
            "trade_ready": bool(bundle.get("trade_ready")),
            "provider_consistent": bool(bundle.get("provider_consistent")),
            "quality_score": bundle.get("quality_score"),
            "problems": bundle.get("problems") or [],
        }
        quote["news_context"] = None
        frames = thesis.get("frames") or {}
        context = hybrid_ai.build_context_block(
            symbol=str(position.get("symbol") or ""),
            analysis=frames.get("1hour") or {}, ticker=quote,
            positions=None, wallet=None, share_account=False,
        )
        prompt = (
            "یک موقعیت کاغذی باز، طبق قواعد محلی در سه تایم‌فریم بسته‌شده نشانهٔ نقض فرضیه دارد. "
            "فقط تصمیم بگیر آیا خروج مبتنی بر نقض فرضیه را تأیید می‌کنی یا خیر. "
            "حد ضرر/حد سود مکانیکی مستقل هستند و موضوع این درخواست نیستند. "
            "اگر داده ناقص، متناقض، کهنه یا ناکافی است، hold بده. دستور صرافی صادر نکن. "
            "فقط JSON با کلیدهای decision (close|hold), confidence (0..100), reason (حداکثر 160 نویسه) بده.\n"
            f"موقعیت: {position.get('symbol')} {position.get('side')}؛ شناسه: {pid}؛ "
            f"قدرت شواهد مخالف: {thesis.get('score')} از 100؛\nزمینه: {context}"
        )
        raw = coarena_ai.chat(
            api_key=api_key, model=storage.get_setting("coarena_model", "") or coarena_ai.DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "Return valid JSON only. You are reviewing a paper position. Never issue an order."},
                {"role": "user", "content": prompt},
            ], temperature=0.1, max_tokens=350, retries=1, timeout=25,
        )
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("پاسخ JSON معتبر نیست")
        answer = json.loads(raw[start:end + 1])
        confidence = float(answer.get("confidence", 0))
        decision = str(answer.get("decision") or "").strip().lower()
        if decision not in ("close", "hold") or not 0 <= confidence <= 100:
            raise ValueError("ساختار تصمیم خروج معتبر نیست")
        approved = decision == "close" and confidence >= 80
        return {"approved": approved, "status": "confirmed" if approved else "hold",
                "confidence": confidence, "reason": str(answer.get("reason") or "بدون توضیح")[:160]}
    except Exception as exc:
        return {"approved": False, "status": "unavailable",
                "reason": f"تأیید AI دریافت نشد: {str(exc)[:120]}"}


def _manage_once_impl(extra_symbols: List[str] | None = None, now: float | None = None) -> Dict[str, Any]:
    current = float(now if now is not None else time.time())
    positions = storage.list_open_positions()
    pending = storage.list_pending_orders()
    alerts = storage.alerts_open(50)
    symbols = list(dict.fromkeys(
        [str(p.get("symbol")) for p in positions if p.get("symbol")]
        + [str(o.get("symbol")) for o in pending if o.get("symbol")]
        + [str(a.get("symbol")) for a in alerts if a.get("symbol")]
        + [str(s) for s in (extra_symbols or []) if s]
    ))[:40]
    quotes = data_hub.get_quotes_batch(symbols, now=current) if symbols else {}
    prices: Dict[str, float] = {}
    errors: List[str] = []
    for symbol, quote in quotes.items():
        if quote.get("quote_valid") and quote.get("status") == "fresh" and quote.get("price"):
            base = str(quote.get("base") or quote.get("canonical_symbol") or symbol).upper()
            mark = float(quote["price"])
            prices[base] = mark
            prices[f"{base}-USDT"] = mark
            try:
                slip = float(storage.get_setting("paper_slippage_bps", "2.0") or 2.0)
                # Supervisor may close an existing position through a wider book
                # than the entry gate permits. New pending entries remain capped
                # separately in storage at 2% spread.
                buy_fill = market_fill(quote, "BUY", slippage_bps=slip,
                                       require_fresh=True, max_spread_pct=5.0)
                sell_fill = market_fill(quote, "SELL", slippage_bps=slip,
                                        require_fresh=True, max_spread_pct=5.0)
                prices[f"{base}|SPREAD_PCT"] = float(buy_fill["spread_pct"])
                prices[f"{base}-USDT|SPREAD_PCT"] = float(buy_fill["spread_pct"])
                prices[f"{base}|BUY"] = float(buy_fill["fill_price"])
                prices[f"{base}-USDT|BUY"] = float(buy_fill["fill_price"])
                prices[f"{base}|SELL"] = float(sell_fill["fill_price"])
                prices[f"{base}-USDT|SELL"] = float(sell_fill["fill_price"])
            except Exception:
                # Explicit None sentinels prevent a last-price-only fake fill.
                prices[f"{base}|BUY"] = None
                prices[f"{base}-USDT|BUY"] = None
                prices[f"{base}|SELL"] = None
                prices[f"{base}-USDT|SELL"] = None
        else:
            errors.append(f"{symbol}: {quote.get('display_status_fa') or quote.get('error') or 'قیمت نامعتبر'}")

    pending_events: List[str] = []
    risk_events: List[str] = []
    alert_events: List[str] = []
    if prices:
        pending_events = storage.process_pending_orders(prices)
        risk_events = storage.apply_tp_sl_trail(prices)
        alert_events = storage.process_alerts(prices)
        smart_rows = storage.smart_alerts_open(50)
        smart_symbols = list(dict.fromkeys(a.get("symbol") for a in smart_rows if a.get("symbol")))
        if smart_symbols:
            smart_quotes = data_hub.get_quotes_batch(smart_symbols, now=current)
            smart_events = storage.process_smart_alerts(smart_quotes)
            alert_events.extend(smart_events)

    supervisor_events: List[Dict[str, Any]] = []
    manager_enabled = storage.get_setting("ai_position_manager_enabled", "1") == "1"
    # Refresh the list after TP/SL processing; closed positions are no longer supervised.
    for position in storage.list_open_positions():
        symbol = str(position.get("symbol") or "")
        quote = quotes.get(symbol) or quotes.get(str(position.get("symbol", "")).replace("-USDT", ""))
        if not quote or not quote.get("quote_valid") or quote.get("status") != "fresh":
            continue
        try:
            mark = float(quote["price"])
            pnl = storage.position_pnl(position, mark)
        except Exception:
            continue
        event = {"position_id": int(position.get("id") or 0), "symbol": symbol,
                 "side": position.get("side"), "mark": mark,
                 "unrealized_pnl": pnl.get("pnl"), "status": "MONITORED",
                 "reason": "قیمت معتبر دریافت شد؛ حد سود، حد ضرر و وضعیت موقعیت بررسی شد."}
        is_auto = str(position.get("source") or "").lower() == "auto"
        if manager_enabled and is_auto and _thesis_review_due(position, current):
            try:
                thesis = _thesis_break(position, current)
                if thesis.get("confirmed"):
                    ai_exit = _ai_confirm_thesis_exit(position, thesis, current)
                    if ai_exit.get("approved"):
                        close_quote = (thesis.get("bundle") or {}).get("quote") or quote
                        close_action = "SELL" if str(position.get("side")).upper() == "LONG" else "BUY"
                        close_fill = market_fill(
                            close_quote, close_action,
                            slippage_bps=float(storage.get_setting("paper_slippage_bps", "2.0") or 2.0),
                            require_fresh=True, max_spread_pct=5.0,
                        )
                        result = storage.close_position(
                            int(position["id"]), float(close_fill["fill_price"]), 100.0,
                            reason="AI_THESIS_INVALIDATION",
                        )
                        event.update({"status": "AI_CLOSED_PAPER", "reason": thesis["reason"],
                                      "close": result, "thesis_score": thesis.get("score"),
                                      "ai_exit": ai_exit})
                        try:
                            storage.notification_add(
                                "position_management", "مدیر هوشمند KALVO · خروج موقعیت",
                                f"{symbol} · #{position['id']} · AI نقض فرضیهٔ استراتژی را تأیید کرد؛ خروج شبیه‌سازی‌شده ثبت شد.",
                            )
                        except Exception:
                            pass
                        supervisor_events.append(event)
                        _audit(event)
                        continue
                    event.update({"status": "THESIS_REVIEW_PENDING",
                                  "reason": ai_exit.get("reason") or "تأیید AI برای خروج دریافت نشد؛ موقعیت باز می‌ماند.",
                                  "thesis_score": thesis.get("score"), "ai_exit": ai_exit})
                    if ai_exit.get("status") != "cooldown":
                        _audit(event)
                        try:
                            storage.notification_add(
                                "position_management", "KALVO · بازبینی AI موقعیت",
                                f"{symbol} · #{position['id']} · خروج مبتنی بر فرضیه تأیید نشد؛ موقعیت باز ماند. "
                                f"{str(ai_exit.get('reason') or '')[:120]}",
                            )
                        except Exception:
                            pass
            except Exception as exc:
                event["reason"] = f"ارزیابی فرضیه ناموفق بود: {str(exc)[:110]}"
        supervisor_events.append(event)

    # Persist concise lifecycle events in the inbox. Do not emit repetitive
    # notifications for every healthy HOLD scan.
    for label, rows in (("pending", pending_events), ("risk", risk_events), ("alerts", alert_events)):
        for message in rows:
            try:
                storage.notification_add(
                    "position_manager", "KALVO Position Manager",
                    f"{label}: {str(message)[:180]}",
                )
            except Exception:
                pass
            _audit({"status": label.upper(), "reason": str(message)[:180]})

    return {
        "prices": prices, "quotes": quotes, "pending_events": pending_events,
        "risk_events": risk_events, "alert_events": alert_events,
        "supervisor_events": supervisor_events, "errors": errors,
        "checked_positions": len(positions), "managed_positions": len(supervisor_events),
        "generated_at": int(current), "mode": "paper_only",
    }


def manage_once(extra_symbols: List[str] | None = None, now: float | None = None) -> Dict[str, Any]:
    """Run at most one manager cycle; concurrent callers receive a busy result."""
    if not _MANAGER_LOCK.acquire(blocking=False):
        return {"busy": True, "prices": {}, "pending_events": [], "risk_events": [],
                "alert_events": [], "supervisor_events": [], "errors": [],
                "generated_at": int(now if now is not None else time.time()),
                "mode": "paper_only"}
    try:
        return _manage_once_impl(extra_symbols=extra_symbols, now=now)
    finally:
        _MANAGER_LOCK.release()
