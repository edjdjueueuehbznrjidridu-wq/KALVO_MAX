# -*- coding: utf-8 -*-
"""KALVO 7.0 opt-in spot paper manager.

This assistant only writes to the local virtual spot ledger. It requires a
provider-consistent quote/candle bundle, uses a multi-timeframe decision gate,
and treats verified news only as a risk filter—not as a directional signal.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict

from . import analysis_engine as analysis
from . import decision_engine
from . import market_data_hub as data_hub
from . import news_intelligence
from . import storage as store
from . import instrument_catalog as instruments
from . import market_apis as market
from .execution_simulator import market_fill

COOLDOWN_SECONDS = 4 * 60 * 60
MAX_ALLOC_PCT = 8.0
MAX_ORDER_USDT = 100.0
MIN_CONFLUENCE = 78
MAX_VOLATILITY_RATIO = 2.5
_SCAN_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _symbol_lock(symbol: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _SCAN_LOCKS.setdefault(symbol, threading.Lock())



def _cloud_spot_gate(symbol: str, side: str, bundle: Dict[str, Any],
                     news_gate: Dict[str, Any]) -> Dict[str, Any]:
    """AI-primary gate for spot paper entries/exits; fail closed on uncertainty."""
    from .defaults import AI_GATE_DEFAULT
    mode = str(store.get_setting("ai_gate", AI_GATE_DEFAULT) or AI_GATE_DEFAULT).lower()
    if mode != "autopilot":
        return {"ok": False, "reason": "مدیریت خودکار اسپات نیازمند حالت AI Autopilot است."}
    try:
        from . import coarena_ai, hybrid_ai
        from .config import get_env
        key = (store.get_setting("coarena_api_key", "") or get_env("COARENA_API_KEY") or "").strip()
        if not key:
            key = coarena_ai.resolve_key("")
        if not key or len(key) < 8:
            return {"ok": False, "reason": "کلید AI در دسترس نیست؛ اقدام خودکار متوقف شد."}
        frames = bundle.get("timeframes") or {}
        quote = dict(bundle.get("quote") or {})
        quote["data_quality"] = {
            "trade_ready": bool(bundle.get("trade_ready")),
            "provider_consistent": bool(bundle.get("provider_consistent")),
            "quality_score": bundle.get("quality_score"),
            "problems": bundle.get("problems") or [],
        }
        quote["news_context"] = (news_gate or {}).get("context")
        context = hybrid_ai.build_context_block(
            symbol=symbol, analysis=frames.get("4hour") or {}, ticker=quote,
            positions=None, wallet=None, share_account=False,
        )
        score = min(int((frames.get(tf) or {}).get("confluence_score") or 0)
                    for tf in ("4hour", "1day"))
        result = hybrid_ai.cloud_gate(
            key, store.get_setting("coarena_model", ""), context,
            symbol, side, score, str((frames.get("4hour") or {}).get("regime") or "unknown"),
            autopilot=True,
        )
        if not result.get("ok"):
            return {"ok": False, "reason": result.get("reason") or "پاسخ معتبر AI دریافت نشد",
                    "raw": result}
        try:
            if isinstance(result.get("confidence"), bool) or isinstance(result.get("size_mult"), bool):
                raise ValueError("boolean values are not valid AI numeric fields")
            confidence = float(result.get("confidence", 0))
            size_mult = float(result.get("size_mult", 1.0))
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "reason": "confidence یا size_mult نامعتبر است", "raw": result}
        proposed_side = str(result.get("side") or "").upper()
        verdict = str(result.get("verdict") or "").lower()
        import math
        if not math.isfinite(confidence) or not 70 <= confidence <= 100:
            return {"ok": False, "reason": "اطمینان AI کمتر از ۷۰٪ یا خارج از محدوده است", "raw": result}
        if not result.get("side_present") or not result.get("size_mult_present"):
            return {"ok": False, "reason": "جهت و ضریب اندازهٔ صریح در پاسخ AI موجود نیست", "raw": result}
        if not math.isfinite(size_mult) or not 0.25 <= size_mult <= 1.0:
            return {"ok": False, "reason": "ضریب اندازهٔ AI خارج از محدودهٔ مجاز است", "raw": result}
        if proposed_side != side:
            return {"ok": False, "reason": "جهت پیشنهادی AI با جهت محلی اسپات یکسان نیست", "raw": result}
        if verdict not in ("approve", "reduce"):
            return {"ok": False, "reason": "AI ورود/خروج اسپات را تأیید نکرد", "raw": result}
        size_mult = max(0.25, min(1.0, size_mult))
        if verdict == "reduce":
            size_mult = min(size_mult, 0.5)
        return {"ok": True, "verdict": verdict, "side": proposed_side,
                "confidence": confidence, "size_mult": size_mult,
                "reason": str(result.get("reason") or "تأیید AI")[:180]}
    except Exception as exc:
        return {"ok": False, "reason": f"خطای AI: {str(exc)[:110]}"}


def _scan_once_impl(symbol: str, now: float | None = None) -> Dict[str, Any]:
    stamp = float(now if now is not None else time.time())
    canonical = instruments.normalize_instrument_symbol(symbol)
    if instruments.is_instrument(canonical) or instruments.is_reference_symbol(canonical):
        return {"action": "HOLD", "reason": "مدیریت خودکار اسپات فقط رمزارزهای واجد شرایط را در دفترکل شبیه‌سازی‌شده پایش می‌کند."}

    bundle = data_hub.get_market_bundle(symbol, ("4hour", "1day"), now=stamp)
    if not bundle.get("trade_ready"):
        return {"action": "HOLD", "reason": "دادهٔ قیمت/کندل معتبر و هم‌منبع نیست؛ هیچ سفارش مجازی ثبت نشد.",
                "data_quality": bundle}

    frames = bundle.get("timeframes") or {}
    base = market.normalize_symbol(symbol)[0]
    quote = bundle.get("quote") or {}
    price = float(bundle.get("price") or 0)
    if price <= 0:
        return {"action": "HOLD", "reason": "قیمت معتبر نیست؛ هیچ سفارشی ثبت نشد."}

    strong = all(int(frames[tf].get("confluence_score") or 0) >= MIN_CONFLUENCE
                 for tf in ("4hour", "1day"))
    volatility_ok = all(float(frames[tf].get("volatility_ratio") or 99.0) <= MAX_VOLATILITY_RATIO
                         for tf in ("4hour", "1day"))
    long_ok = strong and volatility_ok and all(
        frames[tf].get("signal") == "LONG_BIAS" for tf in frames)
    short_ok = strong and volatility_ok and all(
        frames[tf].get("signal") == "SHORT_BIAS" for tf in frames)

    consensus = {"direction": "LONG" if long_ok else "SHORT" if short_ok else None,
                 "aligned": bool(long_ok or short_ok)}
    news_gate = news_intelligence.evaluate_entry_gate(base, consensus.get("direction") or "LONG", now=int(stamp))
    candidate = {"consensus": consensus, "timeframes": frames,
                 "data_quality": bundle, "ticker": quote}
    gate = decision_engine.evaluate_entry(candidate, minimum_score=MIN_CONFLUENCE,
                                           news_gate=news_gate if news_gate.get("enabled") else None)

    balances = {r["asset"]: r for r in store.get_spot_balances()}
    holding = float((balances.get(base) or {}).get("free") or 0)
    usdt = float((balances.get("USDT") or {}).get("free") or 0)
    key = f"spot_auto_last_{base}"
    try:
        last = float(store.get_setting(key, "0") or 0)
    except (TypeError, ValueError):
        last = 0.0
    if stamp - last < COOLDOWN_SECONDS:
        return {"action": "HOLD", "reason": "وقفهٔ ایمنی چهار ساعته هنوز تمام نشده.",
                "price": price, "holding": holding, "usdt": usdt, "data_quality": bundle}

    if long_ok and holding <= 1e-12:
        if not gate.get("allow"):
            return {"action": "HOLD", "reason": "؛ ".join(gate.get("reasons") or ["دروازهٔ تصمیم ورود تأیید نکرد"]),
                    "price": price, "holding": holding, "usdt": usdt, "decision": gate}
        ai_gate = _cloud_spot_gate(base, "LONG", bundle, news_gate)
        if not ai_gate.get("ok"):
            return {"action": "HOLD", "reason": ai_gate.get("reason") or "AI ورود اسپات را تأیید نکرد",
                    "price": price, "holding": holding, "usdt": usdt,
                    "decision": gate, "ai_decision": ai_gate}
        try:
            slip = float(store.get_setting("paper_slippage_bps", "2.0") or 2.0)
            execution = market_fill(quote, "BUY", slippage_bps=slip, require_fresh=True)
            execution_price = float(execution["fill_price"])
        except Exception as exc:
            return {"action": "HOLD", "reason": f"قیمت قابل‌اجرای خرید موجود نیست: {str(exc)[:100]}",
                    "price": price, "data_quality": bundle}
        amount = min(usdt * MAX_ALLOC_PCT / 100.0, MAX_ORDER_USDT) * ai_gate["size_mult"]
        if amount < 10:
            return {"action": "HOLD", "reason": "موجودی مجازی یا اندازهٔ پس از کاهش ریسک AI برای سفارش کافی نیست.",
                    "price": price, "holding": holding, "usdt": usdt, "ai_decision": ai_gate}
        trade = store.spot_market_order(base, "BUY", amount, execution_price,
                                        note=(f"KALVO 7.1 paper · AI {ai_gate['verdict']} "
                                              f"confidence={ai_gate['confidence']:.0f}; ask={execution['ask']:.8g}; "
                                              f"spread={execution['spread_pct']:.5f}%; slippage={execution['slippage_bps']:.2f}bps"))
        store.set_setting(key, str(int(stamp)))
        return {"action": "BUY", "reason": "داده هم‌منبع و بسته بود؛ فیلتر محلی و AI Autopilot ورود مجازی را تأیید کردند.",
                "price": price, "trade": trade, "frames": frames, "decision": gate,
                "ai_decision": ai_gate, "data_quality": bundle}

    if short_ok and holding > 1e-12:
        if not gate.get("allow"):
            return {"action": "HOLD", "reason": "خروج خودکار طبق فیلتر خبری/کیفیت متوقف شد؛ موقعیت برای بررسی علامت‌گذاری شد.",
                    "price": price, "holding": holding, "decision": gate}
        ai_gate = _cloud_spot_gate(base, "SHORT", bundle, news_gate)
        if not ai_gate.get("ok"):
            return {"action": "HOLD", "reason": ai_gate.get("reason") or "AI خروج اسپات را تأیید نکرد؛ دارایی مجازی حفظ شد.",
                    "price": price, "holding": holding, "decision": gate, "ai_decision": ai_gate}
        try:
            slip = float(store.get_setting("paper_slippage_bps", "2.0") or 2.0)
            execution = market_fill(quote, "SELL", slippage_bps=slip, require_fresh=True)
            execution_price = float(execution["fill_price"])
        except Exception as exc:
            return {"action": "HOLD", "reason": f"قیمت قابل‌اجرای فروش موجود نیست: {str(exc)[:100]}",
                    "price": price, "data_quality": bundle}
        quantity = holding * ai_gate["size_mult"]
        trade = store.spot_market_order(base, "SELL", quantity, execution_price,
                                        note=(f"KALVO 7.1 paper · AI {ai_gate['verdict']} "
                                              f"confidence={ai_gate['confidence']:.0f}; bid={execution['bid']:.8g}; "
                                              f"spread={execution['spread_pct']:.5f}%; slippage={execution['slippage_bps']:.2f}bps; thesis invalidation"))
        store.set_setting(key, str(int(stamp)))
        return {"action": "SELL", "reason": "فرضیهٔ صعودی در هر دو بازه نقض و اقدام فروش مجازی توسط AI تأیید شد.",
                "price": price, "trade": trade, "frames": frames, "decision": gate,
                "ai_decision": ai_gate, "data_quality": bundle}

    return {"action": "HOLD", "reason": "هم‌جهتی و قدرت سیگنال کافی نیست یا نوسان از سقف مجاز بالاتر است.",
            "price": price, "holding": holding, "usdt": usdt, "frames": frames,
            "data_quality": bundle}


def scan_once(symbol: str, now: float | None = None) -> Dict[str, Any]:
    """Per-symbol lock prevents overlapping cycles and duplicate paper orders."""
    canonical = str(symbol or "").strip().upper()
    lock = _symbol_lock(canonical)
    if not lock.acquire(blocking=False):
        return {"action": "HOLD", "reason": "بررسی قبلی این نماد هنوز در حال اجراست؛ چرخهٔ تکراری رد شد.",
                "busy": True}
    try:
        return _scan_once_impl(canonical, now=now)
    finally:
        lock.release()
