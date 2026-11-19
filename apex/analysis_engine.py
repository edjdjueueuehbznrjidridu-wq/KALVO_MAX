# -*- coding: utf-8 -*-
"""KALVO local market analysis engine.

Pure-Python, explainable indicators. The score measures rule confluence only;
it is not a probability, price target, or financial recommendation.
"""
from __future__ import annotations

import math
import statistics
import time
from typing import Any, Dict, Iterable, List, Optional


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _ema(values: List[float], period: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2.0 / (period + 1.0)
    previous = seed
    for index in range(period, len(values)):
        previous = values[index] * alpha + previous * (1.0 - alpha)
        out[index] = previous
    return out


def _rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) <= period:
        return None
    gains, losses = [], []
    for old, new in zip(values[:period], values[1:period + 1]):
        delta = new - old
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for old, new in zip(values[period:], values[period + 1:]):
        delta = new - old
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
    if avg_loss <= 1e-15:
        return 100.0 if avg_gain > 1e-15 else 50.0
    ratio = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + ratio))


def _true_ranges(rows: List[Dict[str, float]]) -> List[float]:
    values = []
    for index, row in enumerate(rows):
        if index == 0:
            values.append(row["high"] - row["low"])
        else:
            previous = rows[index - 1]["close"]
            values.append(max(row["high"] - row["low"],
                              abs(row["high"] - previous),
                              abs(row["low"] - previous)))
    return values


def _adx(rows: List[Dict[str, float]], period: int = 14):
    """Wilder-style ADX and directional indicators; returns last values."""
    if len(rows) < period * 2 + 1:
        return None, None, None
    tr, plus_dm, minus_dm = [], [], []
    for i in range(1, len(rows)):
        current, previous = rows[i], rows[i - 1]
        up = current["high"] - previous["high"]
        down = previous["low"] - current["low"]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(current["high"] - current["low"],
                      abs(current["high"] - previous["close"]),
                      abs(current["low"] - previous["close"])))
    smooth_tr = sum(tr[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])
    dx_values = []
    for i in range(period, len(tr)):
        if i > period:
            smooth_tr = smooth_tr - smooth_tr / period + tr[i]
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm[i]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm[i]
        if smooth_tr <= 1e-15:
            dx_values.append(0.0)
            continue
        pdi = 100.0 * smooth_plus / smooth_tr
        mdi = 100.0 * smooth_minus / smooth_tr
        denom = pdi + mdi
        dx_values.append(100.0 * abs(pdi - mdi) / denom if denom > 1e-15 else 0.0)
    if len(dx_values) < period:
        return None, None, None
    adx_value = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx_value = ((adx_value * (period - 1)) + dx) / period
    # Return the current directional indicators from the smoothed values.
    pdi = 100.0 * smooth_plus / smooth_tr if smooth_tr > 1e-15 else 0.0
    mdi = 100.0 * smooth_minus / smooth_tr if smooth_tr > 1e-15 else 0.0
    return adx_value, pdi, mdi


TIMEFRAME_SECONDS = {
    "1min": 60, "3min": 180, "5min": 300, "15min": 900,
    "30min": 1800, "1hour": 3600, "2hour": 7200, "4hour": 14400,
    "1day": 86400, "1week": 604800,
}


def completed_candles(candles: List[Dict[str, Any]], timeframe: str,
                      now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Exclude the candle that is still being formed."""
    seconds = TIMEFRAME_SECONDS.get(str(timeframe).lower())
    if seconds is None:
        raise ValueError("بازهٔ زمانی برای تشخیص کندل بسته‌شده معتبر نیست")
    current = float(now if now is not None else time.time())
    complete = []
    for candle in candles or []:
        stamp = _finite(candle.get("time"))
        if stamp is not None and stamp > 0 and stamp + seconds <= current:
            complete.append(candle)
    return complete


def analyze_candles(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate a multi-factor technical snapshot from validated OHLCV rows."""
    rows: List[Dict[str, float]] = []
    for candle in candles or []:
        opn = _finite(candle.get("open"))
        high = _finite(candle.get("high"))
        low = _finite(candle.get("low"))
        close = _finite(candle.get("close"))
        volume = _finite(candle.get("volume", 0))
        if None in (opn, high, low, close) or min(opn, high, low, close) <= 0:
            continue
        if high < max(opn, close, low) or low > min(opn, close, high):
            continue
        rows.append({"open": opn, "high": high, "low": low,
                     "close": close, "volume": max(0.0, volume or 0.0)})
    if len(rows) < 50:
        return {"ready": False, "count": len(rows),
                "reason": "برای تحلیل چندشاخصی حداقل ۵۰ کندل معتبر لازم است."}

    close = [r["close"] for r in rows]
    high = [r["high"] for r in rows]
    low = [r["low"] for r in rows]
    volumes = [r["volume"] for r in rows]
    last = close[-1]
    ema20 = _ema(close, 20)[-1]
    ema50 = _ema(close, 50)[-1]
    ema200 = _ema(close, 200)[-1] if len(close) >= 200 else None
    rsi = _rsi(close, 14)

    ema12, ema26 = _ema(close, 12), _ema(close, 26)
    macd_line = [None if a is None or b is None else a - b
                 for a, b in zip(ema12, ema26)]
    macd_compact = [x for x in macd_line if x is not None]
    macd_signal_series = _ema(macd_compact, 9) if len(macd_compact) >= 9 else []
    macd = macd_compact[-1] if macd_compact else None
    macd_signal = macd_signal_series[-1] if macd_signal_series else None
    macd_hist = macd - macd_signal if macd is not None and macd_signal is not None else None

    tr_values = _true_ranges(rows)
    atr = sum(tr_values[-14:]) / 14.0
    atr_pct = (atr / last * 100.0) if last > 0 else None
    # Current ATR against the prior 50 rolling ATR values is a descriptive
    # market-regime feature, not a prediction or a trading recommendation.
    atr_series = [sum(tr_values[idx - 13:idx + 1]) / 14.0
                  for idx in range(13, len(tr_values))]
    atr_baseline_values = atr_series[-51:-1] if len(atr_series) > 1 else []
    atr_baseline = (sum(atr_baseline_values) / len(atr_baseline_values)
                    if atr_baseline_values else None)
    volatility_ratio = atr / atr_baseline if atr_baseline and atr_baseline > 0 else None
    if volatility_ratio is None:
        volatility_state = "نامشخص"
    elif volatility_ratio >= 1.5:
        volatility_state = "نوسان بالاتر از معمول"
    elif volatility_ratio <= 0.7:
        volatility_state = "نوسان پایین‌تر از معمول"
    else:
        volatility_state = "نوسان عادی"
    adx, plus_di, minus_di = _adx(rows, 14)
    if adx is None:
        regime = "دادهٔ روند ناکافی"
    elif adx >= 25:
        regime = "رونددار"
    elif adx <= 18:
        regime = "دامنه‌ای / کم‌روند"
    else:
        regime = "گذار بین روند و دامنه"

    band_window = close[-20:]
    band_mid = sum(band_window) / len(band_window)
    band_std = statistics.pstdev(band_window)
    bb_upper = band_mid + 2.0 * band_std
    bb_lower = band_mid - 2.0 * band_std
    bb_width_pct = (bb_upper - bb_lower) / band_mid * 100 if band_mid else None

    base_vol = sum(volumes[-21:-1]) / 20.0
    volume_ratio = volumes[-1] / base_vol if base_vol > 0 else None
    momentum_5 = (last / close[-6] - 1.0) * 100.0 if len(close) >= 6 and close[-6] else None
    support = min(low[-50:])
    resistance = max(high[-50:])
    recent_high = max(high[-20:])
    recent_low = min(low[-20:])

    bull = bear = 0
    checks: Dict[str, str] = {}
    # Trend stack, with a longer-term filter when enough history exists.
    if last > ema20 > ema50:
        bull += 25
        checks["ema_structure"] = "bullish"
    elif last < ema20 < ema50:
        bear += 25
        checks["ema_structure"] = "bearish"
    else:
        checks["ema_structure"] = "mixed"
    if ema200 is not None:
        if last > ema200:
            bull += 10
            checks["ema200"] = "above"
        elif last < ema200:
            bear += 10
            checks["ema200"] = "below"
        else:
            checks["ema200"] = "at"
    else:
        checks["ema200"] = "insufficient_history"

    if macd_hist is not None and macd_hist > 0:
        bull += 15
        checks["macd"] = "bullish"
    elif macd_hist is not None and macd_hist < 0:
        bear += 15
        checks["macd"] = "bearish"
    else:
        checks["macd"] = "mixed"

    if rsi is not None:
        if 52.0 <= rsi <= 68.0:
            bull += 10
            checks["rsi"] = "bullish"
        elif 32.0 <= rsi <= 48.0:
            bear += 10
            checks["rsi"] = "bearish"
        elif rsi > 75.0:
            checks["rsi"] = "overbought"
        elif rsi < 25.0:
            checks["rsi"] = "oversold"
        else:
            checks["rsi"] = "neutral"
    else:
        checks["rsi"] = "unavailable"

    if adx is not None and adx >= 18.0:
        if plus_di > minus_di:
            bull += 15
            checks["adx"] = "trend_up"
        elif minus_di > plus_di:
            bear += 15
            checks["adx"] = "trend_down"
        else:
            checks["adx"] = "weak_direction"
    else:
        checks["adx"] = "weak_trend" if adx is not None else "unavailable"

    last_up = rows[-1]["close"] > rows[-1]["open"]
    if volume_ratio is not None and volume_ratio >= 1.15:
        if last_up:
            bull += 10
            checks["volume"] = "above_average_up"
        elif rows[-1]["close"] < rows[-1]["open"]:
            bear += 10
            checks["volume"] = "above_average_down"
        else:
            checks["volume"] = "above_average_flat"
    else:
        checks["volume"] = "normal_or_unavailable"

    if momentum_5 is not None:
        if momentum_5 > 0.35:
            bull += 10
            checks["momentum"] = "positive"
        elif momentum_5 < -0.35:
            bear += 10
            checks["momentum"] = "negative"
        else:
            checks["momentum"] = "flat"

    score = bull - bear
    if score >= 70:
        signal = "LONG_BIAS"
    elif score >= 45:
        signal = "LONG_WATCH"
    elif score <= -70:
        signal = "SHORT_BIAS"
    elif score <= -45:
        signal = "SHORT_WATCH"
    else:
        signal = "NEUTRAL"

    if ema200 is None:
        warning = "داده برای فیلتر EMA200 کافی نیست؛ این فیلتر در امتیاز لحاظ نشده است."
    else:
        warning = ""
    if rsi is not None and rsi >= 75:
        warning = (warning + " RSI در ناحیهٔ داغ است؛ تعقیب قیمت ریسک بیشتری دارد.").strip()
    elif rsi is not None and rsi <= 25:
        warning = (warning + " RSI در ناحیهٔ ضعیف است؛ احتمال نوسان و برگشت کوتاه‌مدت را در نظر بگیر.").strip()

    trend = "صعودی" if bull > bear + 15 else "نزولی" if bear > bull + 15 else "ترکیبی/خنثی"
    return {
        "ready": True, "count": len(rows), "price": last,
        "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "rsi14": rsi, "macd": macd, "macd_signal": macd_signal,
        "macd_hist": macd_hist, "atr14": atr, "atr_pct": atr_pct,
        "atr_baseline": atr_baseline, "volatility_ratio": volatility_ratio,
        "volatility_state": volatility_state, "market_regime": regime,
        "scenario_up_1atr": last + atr, "scenario_down_1atr": max(0.0, last - atr),
        "scenario_up_2atr": last + 2 * atr, "scenario_down_2atr": max(0.0, last - 2 * atr),
        "adx14": adx, "plus_di": plus_di, "minus_di": minus_di,
        "bb_mid": band_mid, "bb_upper": bb_upper, "bb_lower": bb_lower,
        "bb_width_pct": bb_width_pct, "volume_ratio": volume_ratio,
        "momentum_5_pct": momentum_5,
        "support_50": support, "resistance_50": resistance,
        "recent_high_20": recent_high, "recent_low_20": recent_low,
        "trend": trend, "signal": signal,
        "confluence_score": abs(score), "confidence": abs(score),
        "directional_score": score, "bull_score": bull, "bear_score": bear,
        "checks": checks, "warning": warning,
        "explanation": _explain(signal, rsi, adx, volume_ratio, atr_pct, score),
    }


def _explain(signal: str, rsi: Optional[float], adx: Optional[float],
             volume_ratio: Optional[float], atr_pct: Optional[float], score: int) -> str:
    if signal == "LONG_BIAS":
        lead = "چند شرط تکنیکال هم‌جهت مثبت شده‌اند؛ این سوگیری صعودی است، نه پیش‌بینی قطعی."
    elif signal == "SHORT_BIAS":
        lead = "چند شرط تکنیکال هم‌جهت منفی شده‌اند؛ این سوگیری نزولی است، نه پیش‌بینی قطعی."
    elif signal.endswith("WATCH"):
        lead = "هم‌گرایی ناقص است؛ وضعیت فقط برای زیرنظرگرفتن مناسب است و تأیید کامل ندارد."
    else:
        lead = "شرط‌های صعودی و نزولی هم‌جهتی کافی ندارند؛ خروجی خنثی است."
    details = [lead, f"امتیاز هم‌گرایی قواعد: {abs(score)}/100 (احتمال موفقیت نیست)."]
    if rsi is not None:
        details.append(f"RSI(14): {rsi:.1f}")
    if adx is not None:
        details.append(f"ADX(14): {adx:.1f}")
    if volume_ratio is not None:
        details.append(f"حجم آخرین کندل نسبت به میانگین ۲۰ کندل قبلی: {volume_ratio:.2f} برابر")
    if atr_pct is not None:
        details.append(f"ATR به قیمت: {atr_pct:.2f}%")
    details.append("حمایت و مقاومت، محدوده‌های گذشته‌اند و تضمین برگشت یا شکست نیستند.")
    return " ".join(details)


def format_analysis(symbol: str, result: Dict[str, Any]) -> str:
    if not result.get("ready"):
        return f"{symbol}: {result.get('reason', 'دادهٔ کافی نیست')}"

    def fmt(value: Any, digits: int = 4) -> str:
        return f"{float(value):,.{digits}f}" if value is not None else "—"

    score = int(result.get("directional_score") or 0)
    direction = "مثبت" if score > 0 else "منفی" if score < 0 else "خنثی"
    checks = result.get("checks") or {}
    rows = [
        f"گزارش تحلیل KALVO · {symbol}",
        f"قیمت آخرین کندل بسته‌شده: {fmt(result.get('price'), 8)}",
        f"سوگیری: {result.get('signal')} · امتیاز جهت‌دار: {score:+d}/100 · جهت غالب: {direction}",
        f"EMA20 / EMA50 / EMA200: {fmt(result.get('ema20'), 8)} / {fmt(result.get('ema50'), 8)} / {fmt(result.get('ema200'), 8)}",
        f"RSI14: {fmt(result.get('rsi14'), 2)} · MACD hist: {fmt(result.get('macd_hist'), 8)}",
        f"ADX14: {fmt(result.get('adx14'), 2)} · +DI / -DI: {fmt(result.get('plus_di'), 2)} / {fmt(result.get('minus_di'), 2)}",
        f"ATR14: {fmt(result.get('atr14'), 8)} ({fmt(result.get('atr_pct'), 2)}%)",
        f"رژیم بازار: {result.get('market_regime') or 'نامشخص'} · وضعیت نوسان: {result.get('volatility_state') or 'نامشخص'}",
        f"نسبت ATR به مبنای اخیر: {fmt(result.get('volatility_ratio'), 2)}",
        f"دامنهٔ مرجع 1 ATR: {fmt(result.get('scenario_down_1atr'), 8)} تا {fmt(result.get('scenario_up_1atr'), 8)}",
        f"دامنهٔ مرجع 2 ATR: {fmt(result.get('scenario_down_2atr'), 8)} تا {fmt(result.get('scenario_up_2atr'), 8)}",
        f"Bollinger upper / mid / lower: {fmt(result.get('bb_upper'), 8)} / {fmt(result.get('bb_mid'), 8)} / {fmt(result.get('bb_lower'), 8)}",
        f"حجم: {fmt(result.get('volume_ratio'), 2)} برابر میانگین ۲۰ کندل قبل",
        f"حمایت / مقاومت ۵۰ کندل: {fmt(result.get('support_50'), 8)} / {fmt(result.get('resistance_50'), 8)}",
        result.get("explanation", ""),
    ]
    if result.get("warning"):
        rows.append("هشدار داده: " + result["warning"])
    rows.append("فقط تحلیل آموزشی و شبیه‌سازی؛ این خروجی توصیهٔ مالی یا تضمین نتیجه نیست.")
    return "\n".join(rows)


def combine_timeframes(results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Conservative 15m/1h/4h agreement; unavailable frames never count as neutral."""
    required = ("15min", "1hour", "4hour")
    missing = [tf for tf in required if not (results.get(tf) or {}).get("ready")]
    if missing:
        return {"ready": False, "reason": "دادهٔ کافی برای همهٔ بازه‌ها موجود نیست: " + ", ".join(missing)}
    directions = []
    for tf in required:
        signal = results[tf].get("signal")
        directions.append("LONG" if signal == "LONG_BIAS" else "SHORT" if signal == "SHORT_BIAS" else "NEUTRAL")
    aligned = directions[0] == directions[1] == directions[2] and directions[0] in ("LONG", "SHORT")
    weighted = (abs(float(results["15min"].get("directional_score") or 0)) * 0.20 +
                abs(float(results["1hour"].get("directional_score") or 0)) * 0.35 +
                abs(float(results["4hour"].get("directional_score") or 0)) * 0.45)
    signed_values = [float(results[tf].get("directional_score") or 0) for tf in required]
    same_sign = all(v > 0 for v in signed_values) or all(v < 0 for v in signed_values)
    return {"ready": True, "aligned": aligned and same_sign,
            "direction": directions[0] if aligned else "NEUTRAL",
            "score": round(weighted, 1), "frames": dict(zip(required, directions)),
            "note": "هم‌جهتی سه بازه برقرار است." if aligned else "سه بازه هم‌جهت نیستند؛ ورود خودکار مجاز نیست."}


def auto_entry_signal(result: Dict[str, Any], min_confidence: int = 75) -> Optional[str]:
    """Single-frame bias only; the automated demo strategy uses all frames."""
    if not result.get("ready") or int(result.get("confluence_score") or 0) < int(min_confidence):
        return None
    if result.get("signal") == "LONG_BIAS":
        return "LONG"
    if result.get("signal") == "SHORT_BIAS":
        return "SHORT"
    return None
