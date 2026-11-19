# -*- coding: utf-8 -*-
"""Recency-aware descriptive memory over closed paper trades (local, transparent).

Stores lightweight weights in settings JSON so the assistant and auto-trader
gradually bias risk and symbol preferences from the user's own paper results.
This is not a trained or self-modifying neural network — statistics and weights are inspectable.
"""
from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Dict

from . import storage
from . import learning as base


def _load() -> Dict[str, Any]:
    raw = storage.get_setting("learning_memory_v1", "")
    if not raw:
        return {"updated": 0, "symbol_bias": {}, "side_bias": {}, "sessions": 0, "notes": []}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"updated": 0, "symbol_bias": {}, "side_bias": {}, "sessions": 0, "notes": []}
        data.setdefault("symbol_bias", {})
        data.setdefault("side_bias", {})
        data.setdefault("sessions", 0)
        data.setdefault("notes", [])
        return data
    except Exception:
        return {"updated": 0, "symbol_bias": {}, "side_bias": {}, "sessions": 0, "notes": []}


def _save(data: Dict[str, Any]) -> None:
    data["updated"] = time.time()
    storage.set_setting("learning_memory_v1", json.dumps(data, ensure_ascii=False))



def _wilson_interval(wins: float, total: float, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 100.0)
    p = max(0.0, min(1.0, wins / total))
    den = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / den
    radius = z * math.sqrt((p * (1.0 - p) / total) + z * z / (4.0 * total * total)) / den
    return (max(0.0, (center - radius) * 100.0), min(100.0, (center + radius) * 100.0))


def _posterior_summary(trades: list[dict], *, half_life: float = 60.0) -> Dict[str, Any]:
    """Recency-weighted Beta(5,5) descriptive statistics, never a predictor."""
    valid = []
    for row in trades:
        try:
            pnl = float(row.get("pnl") or 0.0)
            if not math.isfinite(pnl):
                continue
            valid.append((pnl, int(row.get("ts") or 0)))
        except (TypeError, ValueError, OverflowError):
            continue
    n = len(valid)
    if not n:
        return {"n": 0, "posterior_win_rate": 50.0, "observed_win_rate": None,
                "mean_pnl": None, "interval_95": [0.0, 100.0], "reliability": "insufficient"}
    weighted_wins = 0.0
    total_weight = 0.0
    pnl_weighted = 0.0
    plain_wins = 0
    for index, (pnl, _ts) in enumerate(valid):
        weight = 0.5 ** (index / max(1.0, half_life))
        total_weight += weight
        pnl_weighted += pnl * weight
        if pnl > 1e-9:
            weighted_wins += weight
            plain_wins += 1
    # A symmetric prior avoids overreacting to a tiny personal sample.
    alpha = 5.0 + weighted_wins
    beta = 5.0 + max(0.0, total_weight - weighted_wins)
    posterior = alpha / (alpha + beta) * 100.0
    low, high = _wilson_interval(float(plain_wins), float(n))
    reliability = "low" if n < 8 else "moderate" if n < 25 else "sampled"
    return {
        "n": n,
        "posterior_win_rate": round(posterior, 2),
        "observed_win_rate": round(plain_wins / n * 100.0, 2),
        "mean_pnl": round(pnl_weighted / max(total_weight, 1e-9), 6),
        "interval_95": [round(low, 1), round(high, 1)],
        "reliability": reliability,
        "prior": "Beta(5,5)",
        "weighting": "recent trades receive higher descriptive weight",
    }


def context_for_symbol(symbol: str, limit: int = 300) -> Dict[str, Any]:
    """Compact inspectable memory context for the AI about closed paper trades."""
    target = str(symbol or "").upper().strip()
    try:
        rows = storage.list_trades(max(1, min(500, int(limit))))
    except Exception:
        rows = []
    same = [row for row in rows if str(row.get("symbol") or "").upper() == target]
    sides = {}
    for side in ("LONG", "SHORT"):
        sides[side] = _posterior_summary([r for r in same if str(r.get("side") or "").upper() == side])
    regimes = {}
    for row in same:
        match = re.search(r"regime=([a-z_]+)", str(row.get("position_note") or ""), flags=re.I)
        if match:
            regimes.setdefault(match.group(1).lower(), []).append(row)
    return {
        "symbol": target,
        "scope": "closed local paper trades only",
        "overall": _posterior_summary(same),
        "by_side": sides,
        "by_regime": {key: _posterior_summary(value) for key, value in regimes.items()},
        "sample_warning": "Small samples are noisy; these are historical summaries, not future win probabilities.",
        "risk_policy": "memory never raises the configured risk budget",
    }

def refresh_from_history() -> Dict[str, Any]:
    """Recompute soft biases from closed paper trades."""
    hist = base.analyze_history(400)
    mem = _load()
    symbol_bias = dict(mem.get("symbol_bias") or {})
    side_bias = dict(mem.get("side_bias") or {})

    by_symbol = hist.get("by_symbol") or {}
    for sym, stats in by_symbol.items():
        try:
            n = int(stats.get("trades") or 0)
            wr = float(stats.get("win_rate") or 0)
            avg = float(stats.get("avg_pnl") or 0)
        except Exception:
            continue
        if n < 3:
            continue
        # map to [-1, 1]
        score = max(-1.0, min(1.0, (wr - 50.0) / 50.0 * 0.6 + (1 if avg > 0 else -1) * 0.2))
        prev = float(symbol_bias.get(sym, 0) or 0)
        symbol_bias[sym] = round(prev * 0.7 + score * 0.3, 4)

    by_side = hist.get("by_side") or {}
    for side, stats in by_side.items():
        try:
            n = int(stats.get("trades") or 0)
            wr = float(stats.get("win_rate") or 0)
        except Exception:
            continue
        if n < 5:
            continue
        score = max(-1.0, min(1.0, (wr - 50.0) / 50.0))
        prev = float(side_bias.get(side, 0) or 0)
        side_bias[str(side).lower()] = round(prev * 0.7 + score * 0.3, 4)

    mem["symbol_bias"] = symbol_bias
    mem["side_bias"] = side_bias
    mem["regime_bias"] = _regime_stats()
    try:
        newest = storage.list_trades(1)
        mem["last_trade_id"] = int(newest[0]["id"]) if newest else 0
    except Exception:
        pass
    mem["sessions"] = int(mem.get("sessions") or 0) + 1
    mem["last_hist"] = {
        "trades": hist.get("sample") or 0,
        "win_rate": hist.get("win_rate"),
        "profit_factor": hist.get("profit_factor"),
    }
    _save(mem)
    return mem


def _regime_stats() -> Dict[str, Any]:
    """Per-market-regime learning from closed *auto* trades (regime stored in the note)."""
    import re
    groups: Dict[str, list] = {}
    for t in storage.list_trades(500):
        if t.get("trade_source") != "auto":
            continue
        m = re.search(r"regime=([a-z]+)", t.get("position_note") or "")
        if not m:
            continue
        try:
            groups.setdefault(m.group(1), []).append(float(t.get("pnl") or 0))
        except (TypeError, ValueError):
            continue
    out: Dict[str, Any] = {}
    for regime, vals in groups.items():
        n = len(vals)
        if n < 6:
            continue
        wr = sum(1 for v in vals if v > 1e-9) / n * 100.0
        avg = sum(vals) / n
        bias = max(-1.0, min(1.0, (wr - 50.0) / 50.0 * 0.6 + (1 if avg > 0 else -1) * 0.4))
        out[regime] = {"n": n, "win_rate": round(wr, 1), "avg_pnl": round(avg, 4),
                       "bias": round(bias, 3)}
    return out


def regime_bias(regime: str) -> Dict[str, Any]:
    """Learned bias for one market regime: {'n': samples, 'bias': -1..1}."""
    return dict((_load().get("regime_bias") or {}).get(regime) or {"n": 0, "bias": 0.0})


def refresh_if_new_trades() -> Dict[str, Any]:
    """Refresh descriptive memory only when a new closed paper trade exists."""
    mem = _load()
    try:
        newest = storage.list_trades(1)
        latest = int(newest[0]["id"]) if newest else 0
    except Exception:
        return mem
    if latest != int(mem.get("last_trade_id") or 0):
        return refresh_from_history()
    return mem


def risk_scale() -> float:
    """Return a 0.7..1.0 risk multiplier. Reduce-only: memory never enlarges risk."""
    mem = _load()
    hist = mem.get("last_hist") or {}
    try:
        if int(hist.get("trades") or 0) < 20:
            return 1.0  # insufficient sample -> base policy
        wr = float(hist.get("win_rate") if hist.get("win_rate") is not None else 50)
    except Exception:
        return 1.0
    if wr >= 52:
        return 1.0
    if wr >= 45:
        return 0.85
    return 0.7


def symbol_priority_boost(symbol: str) -> float:
    mem = _load()
    return float((mem.get("symbol_bias") or {}).get(symbol, 0) or 0)


def format_memory_fa() -> str:
    mem = refresh_if_new_trades()
    lines = ["حافظهٔ تطبیقی KALVO (فقط معاملات دموی خودت):"]
    lines.append(f"تعداد به‌روزرسانی: {mem.get('sessions', 0)}")
    sb = mem.get("symbol_bias") or {}
    if sb:
        top = sorted(sb.items(), key=lambda x: x[1], reverse=True)[:8]
        lines.append("تمایل نمادها (مثبت=نتیجهٔ بهتر در تاریخچه):")
        for s, v in top:
            lines.append(f"  · {s}: {v:+.2f}")
    sides = mem.get("side_bias") or {}
    if sides:
        lines.append("تمایل جهت: " + ", ".join(f"{k}={v:+.2f}" for k, v in sides.items()))
    rb = mem.get("regime_bias") or {}
    if rb:
        lines.append("یادگیری به تفکیک رژیم بازار: " + ", ".join(
            f"{k}: n={v['n']} bias={v['bias']:+.2f}" for k, v in rb.items()))
    lines.append(f"ضریب ریسک (فقط کاهشی): {risk_scale():.2f}")
    lines.append("یادگیری آماری: prior Beta(5,5)، وزن‌دهی بیشتر به معاملات جدیدتر و بازهٔ اطمینان؛ نمونهٔ کم قابل اتکا نیست.")
    lines.append("این آمار تضمین سود، احتمال برد آینده یا مدل پیش‌بینی قیمت نیست؛ فقط خلاصهٔ نتایج دفترکل مجازی است.")
    return "\n".join(lines)


def evaluate_setup(features: dict) -> dict:
    """Soft score for a candidate using symbol bias and risk_scale (no DB required)."""
    try:
        sym = str((features or {}).get("symbol") or "")
        boost = symbol_priority_boost(sym) if sym else 0.0
        scale = risk_scale()
    except Exception:
        boost, scale = 0.0, 1.0
    score = max(0.0, min(1.0, 0.5 + boost * 0.25 + (scale - 1.0) * 0.2))
    return {"score": score, "boost": boost, "risk_scale": scale, "accept": score >= 0.45}
