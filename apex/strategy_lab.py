# -*- coding: utf-8 -*-
"""Educational, long-only / short-capable candle backtester for paper research.

Signals are computed on completed candles and filled at the next candle open.
This is a simplified simulator, not an exchange execution model or advice.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List


def _num(value: Any, default=None):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1.0 - alpha) * out[-1])
    return out


def _rsi(values: List[float], period: int = 14) -> List[float]:
    out = [50.0] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [], []
    for a, b in zip(values[:-1], values[1:]):
        gains.append(max(0.0, b - a))
        losses.append(max(0.0, a - b))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    out[period] = 100.0 if avg_l == 0 and avg_g > 0 else (50.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l))
    for i in range(period + 1, len(values)):
        avg_g = (avg_g * (period - 1) + gains[i - 1]) / period
        avg_l = (avg_l * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if avg_l == 0 and avg_g > 0 else (50.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l))
    return out


def _signals(candles: List[Dict[str, Any]], strategy: str) -> List[int]:
    closes = [float(c["close"]) for c in candles]
    signals = [0] * len(candles)  # +1 long, -1 short, 0 flat/hold
    if strategy == "ema_cross":
        fast, slow = _ema(closes, 12), _ema(closes, 26)
        for i in range(1, len(candles)):
            if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
                signals[i] = 1
            elif fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
                signals[i] = -1
    elif strategy == "rsi_rebound":
        r = _rsi(closes)
        for i in range(1, len(candles)):
            if r[i - 1] < 30 <= r[i]:
                signals[i] = 1
            elif r[i - 1] > 70 >= r[i]:
                signals[i] = -1
    elif strategy == "donchian_breakout":
        lookback = 20
        for i in range(lookback, len(candles)):
            previous = candles[i - lookback:i]
            hi = max(float(x["high"]) for x in previous)
            lo = min(float(x["low"]) for x in previous)
            if closes[i] > hi:
                signals[i] = 1
            elif closes[i] < lo:
                signals[i] = -1
    else:
        raise ValueError("استراتژی ناشناخته است")
    return signals


def _metrics(trades: List[Dict[str, Any]], equity_curve: List[Dict[str, float]], initial: float) -> Dict[str, Any]:
    pnls = [float(t["pnl"]) for t in trades]
    wins = [x for x in pnls if x > 1e-9]
    losses = [x for x in pnls if x < -1e-9]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    peak = initial
    max_dd = 0.0
    for point in equity_curve:
        equity = point["equity"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    final = equity_curve[-1]["equity"] if equity_curve else initial
    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": 100.0 * len(wins) / len(pnls) if pnls else 0.0,
        "net_pnl": final - initial, "return_pct": (final / initial - 1) * 100 if initial else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else (None if not gross_win else float("inf")),
        "expectancy": sum(pnls) / len(pnls) if pnls else 0.0,
        "max_drawdown": max_dd, "final_equity": final,
        "fees_paid": sum(float(t["fees"]) for t in trades),
    }


def backtest(candles: List[Dict[str, Any]], strategy: str = "ema_cross",
             initial_equity: float = 10000.0, position_fraction: float = 0.25,
             fee_rate: float = 0.0006, slippage_bps: float = 2.0) -> Dict[str, Any]:
    """Run a simple paper backtest with next-open fills and two-sided costs."""
    initial = _num(initial_equity)
    fraction = _num(position_fraction)
    fee = _num(fee_rate)
    slip = _num(slippage_bps)
    if initial is None or initial <= 0:
        raise ValueError("سرمایهٔ آزمایشی باید مثبت باشد")
    if fraction is None or not 0.01 <= fraction <= 1.0:
        raise ValueError("سهم سرمایه باید بین ۱ تا ۱۰۰ درصد باشد")
    if fee is None or not 0 <= fee <= 0.02 or slip is None or not 0 <= slip <= 200:
        raise ValueError("هزینه یا لغزش نامعتبر است")
    valid = []
    for row in candles or []:
        try:
            c = {k: float(row[k]) for k in ("open", "high", "low", "close")}
            c["time"] = float(row.get("time", len(valid)))
            if min(c["open"], c["high"], c["low"], c["close"]) <= 0:
                continue
            if c["high"] < max(c["open"], c["close"], c["low"]) or c["low"] > min(c["open"], c["close"], c["high"]):
                continue
            valid.append(c)
        except (KeyError, TypeError, ValueError):
            continue
    if len(valid) < 60:
        raise ValueError("برای بک‌تست حداقل ۶۰ کندل معتبر لازم است")
    signals = _signals(valid, strategy)
    cash = initial
    pos = None
    trades: List[Dict[str, Any]] = []
    curve: List[Dict[str, float]] = []
    slip_rate = slip / 10000.0

    def close_position(index: int, raw_price: float, reason: str):
        nonlocal cash, pos
        if pos is None:
            return
        side = pos["side"]
        fill = raw_price * (1 - slip_rate if side == "LONG" else 1 + slip_rate)
        gross = (fill - pos["entry"]) * pos["qty"] * (1 if side == "LONG" else -1)
        exit_fee = abs(fill * pos["qty"]) * fee
        pnl = gross - pos["entry_fee"] - exit_fee
        cash += gross - exit_fee
        trades.append({"side": side, "entry_index": pos["entry_index"], "exit_index": index,
                       "entry": pos["entry"], "exit": fill, "qty": pos["qty"],
                       "pnl": pnl, "fees": pos["entry_fee"] + exit_fee, "reason": reason})
        pos = None

    for i in range(1, len(valid)):
        signal = signals[i - 1]  # signal known at prior completed close; execute at this open
        raw_open = valid[i]["open"]
        if pos and signal and signal != (1 if pos["side"] == "LONG" else -1):
            close_position(i, raw_open, "signal_reversal")
        if pos is None and signal:
            side = "LONG" if signal > 0 else "SHORT"
            fill = raw_open * (1 + slip_rate if side == "LONG" else 1 - slip_rate)
            notional = max(0.0, cash) * fraction
            qty = notional / fill if fill > 0 else 0.0
            entry_fee = notional * fee
            if qty > 0 and cash > entry_fee:
                cash -= entry_fee
                pos = {"side": side, "entry": fill, "qty": qty,
                       "entry_fee": entry_fee, "entry_index": i}
        mark = valid[i]["close"]
        equity = cash
        if pos:
            direction = 1 if pos["side"] == "LONG" else -1
            equity += (mark - pos["entry"]) * pos["qty"] * direction
        curve.append({"time": valid[i]["time"], "equity": float(equity)})
    if pos:
        close_position(len(valid) - 1, valid[-1]["close"], "end_of_data")
        if curve:
            curve[-1]["equity"] = cash
    stats = _metrics(trades, curve, initial)
    return {"strategy": strategy, "candles": len(valid), "stats": stats,
            "trades": trades, "equity_curve": curve,
            "note": "سیگنال از کندل بسته‌شده و اجرا در بازشدن کندل بعدی؛ هزینه و لغزش ساده‌سازی شده‌اند."}


def walk_forward(candles: List[Dict[str, Any]], strategy: str = "ema_cross", split: float = 0.7) -> Dict[str, Any]:
    """Chronological holdout check; strategy parameters are not tuned on the test slice."""
    split = _num(split)
    if split is None or not 0.5 <= split <= 0.85:
        raise ValueError("نسبت تقسیم باید بین ۵۰ تا ۸۵ درصد باشد")
    n = len(candles or [])
    cut = int(n * split)
    if cut < 60 or n - cut < 60:
        raise ValueError("برای آزمون جداگانه حداقل ۱۲۰ کندل لازم است")
    train = backtest(candles[:cut], strategy)
    test = backtest(candles[cut:], strategy)
    return {"split_index": cut, "train": train["stats"], "validation": test["stats"],
            "note": "بخش اعتبارسنجی پس از بخش آموزش قرار دارد؛ این آزمون از بیش‌برازش جلوگیری کامل نمی‌کند."}
