# -*- coding: utf-8 -*-
"""KALVO 7.0 explainable entry gate.

This layer does not forecast certainty. It rejects low-quality/misaligned inputs,
combines independent risk constraints, and returns a human-auditable decision.
All downstream automated actions stay inside KALVO's simulated ledger.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate_entry(candidate: Dict[str, Any], minimum_score: int = 70,
                    news_gate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    reasons = []
    warnings = []
    side = str((candidate.get("consensus") or {}).get("direction") or "").upper()
    if side not in ("LONG", "SHORT"):
        reasons.append("جهت چندبازه‌ای مشخص نیست")

    quality = candidate.get("data_quality") or {}
    if not quality.get("trade_ready"):
        reasons.extend(quality.get("problems") or ["کیفیت یا سازگاری داده برای تصمیم خودکار کافی نیست"])
    if not quality.get("provider_consistent"):
        reasons.append("منبع قیمت و کندل‌ها یکسان نیست")

    frames = candidate.get("timeframes") or {}
    if not frames:
        reasons.append("دادهٔ چندبازه‌ای موجود نیست")

    scores = []
    volatility_values = []
    for timeframe, frame in frames.items():
        if not frame.get("ready"):
            reasons.append(f"تحلیل {timeframe} آماده نیست")
            continue
        scores.append(int(_number(frame.get("confluence_score")) or 0))
        vol = _number(frame.get("volatility_ratio"))
        if vol is not None:
            volatility_values.append(vol)
        signal = str(frame.get("signal") or "").upper()
        if side == "LONG" and signal not in ("LONG_BIAS", "BUY", "LONG"):
            reasons.append(f"جهت تحلیل {timeframe} با LONG هم‌خوان نیست")
        if side == "SHORT" and signal not in ("SHORT_BIAS", "SELL", "SHORT"):
            reasons.append(f"جهت تحلیل {timeframe} با SHORT هم‌خوان نیست")

    score = min(scores) if scores else 0
    if not scores or score < int(minimum_score):
        reasons.append(f"کمترین امتیاز هم‌گرایی {score} از حداقل {minimum_score} کمتر است")

    if volatility_values and max(volatility_values) > 3.0:
        reasons.append("نوسان غیرعادی است؛ ورود خودکار متوقف شد")

    ticker = candidate.get("ticker") or {}
    spread = _number(ticker.get("spread_pct"))
    if spread is None:
        # An unknown spread is not a safe execution condition. Display may continue,
        # but automated paper entry must have a measurable transaction-cost guard.
        reasons.append("اسپرد قابل محاسبه نیست؛ ورود خودکار تا تأیید هزینهٔ معامله متوقف شد")
    elif spread > 0.25:
        reasons.append(f"اسپرد {spread:.3f}% از سقف محافظه‌کارانه 0.25% بیشتر است")

    if side == "LONG":
        for timeframe, frame in frames.items():
            rsi = _number(frame.get("rsi14"))
            if rsi is not None and rsi >= 82:
                reasons.append(f"RSI در {timeframe} بیش‌خرید افراطی را نشان می‌دهد")
    elif side == "SHORT":
        for timeframe, frame in frames.items():
            rsi = _number(frame.get("rsi14"))
            if rsi is not None and rsi <= 18:
                reasons.append(f"RSI در {timeframe} بیش‌فروش افراطی را نشان می‌دهد")

    if news_gate is not None and not news_gate.get("allow", False):
        reasons.append(str(news_gate.get("reason") or "دروازهٔ خبر ورود را تأیید نکرد"))

    allow = not reasons
    return {
        "allow": allow,
        "side": side,
        "score": score,
        "quality_score": int(quality.get("quality_score") or 0),
        "reasons": list(dict.fromkeys(reasons)),
        "warnings": warnings,
        "news_gate": news_gate,
        "decision": "APPROVE_PAPER" if allow else "REJECT",
        "model": "KALVO_LOCAL_RISK_GATE_V1",
        "execution_scope": "paper_only",
    }
