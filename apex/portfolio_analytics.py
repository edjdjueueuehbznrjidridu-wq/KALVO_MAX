# -*- coding: utf-8 -*-
"""Descriptive risk cockpit for virtual portfolio and closed paper trades."""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any, Dict, List


def build_risk_report(trades: List[Dict[str, Any]], positions: List[Dict[str, Any]],
                      price_map: Dict[str, float], initial_equity: float = 0.0) -> Dict[str, Any]:
    rows = list(reversed(trades or []))
    pnls = []
    by_day = defaultdict(float)
    for t in rows:
        try:
            pnl = float(t.get("pnl") or 0)
            if not math.isfinite(pnl):
                continue
        except (TypeError, ValueError):
            continue
        pnls.append(pnl)
        try:
            day = int(t.get("ts") or 0) // 86400
        except (TypeError, ValueError):
            day = 0
        by_day[day] += pnl
    wins = sum(x > 0 for x in pnls)
    losses = sum(x < 0 for x in pnls)
    gross_profit = sum(x for x in pnls if x > 0)
    gross_loss = abs(sum(x for x in pnls if x < 0))
    balance = float(initial_equity or 0.0)
    peak = balance
    max_dd = 0.0
    curve = []
    for t, pnl in zip(rows, pnls):
        balance += pnl
        peak = max(peak, balance)
        dd = max(0.0, peak - balance)
        max_dd = max(max_dd, dd)
        curve.append({"ts": float(t.get("ts") or 0), "equity": balance, "drawdown": dd})
    loss_streak = 0
    for pnl in reversed(pnls):
        if pnl < 0:
            loss_streak += 1
        else:
            break
    exposure = 0.0
    margin = 0.0
    unpriced = []
    for p in positions or []:
        symbol = str(p.get("symbol") or "")
        price = price_map.get(symbol) or price_map.get(symbol.replace("-USDT", ""))
        qty = float(p.get("qty_open") or 0)
        exposure += qty * float(price or 0)
        margin += float(p.get("margin_open") or 0)
        if not price:
            unpriced.append(symbol)
    today_key = int(time.time()) // 86400
    return {
        "trade_count": len(pnls), "win_rate_pct": wins / len(pnls) * 100 if pnls else 0.0,
        "net_pnl": sum(pnls), "gross_profit": gross_profit, "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "max_drawdown": max_dd, "loss_streak": loss_streak,
        "today_realized": by_day.get(today_key, 0.0) if today_key is not None else 0.0,
        "exposure": exposure, "locked_margin": margin,
        "open_positions": len(positions or []), "unpriced_positions": unpriced,
        "equity_curve": curve[-120:],
    }


def scenario_table(positions: List[Dict[str, Any]], price_map: Dict[str, float],
                   changes_pct=(-10, -5, -2, 2, 5, 10)) -> List[Dict[str, float]]:
    """What-if mark-to-market PnL under hypothetical parallel price shocks."""
    usable = []
    for p in positions or []:
        sym = str(p.get("symbol") or "")
        px = price_map.get(sym) or price_map.get(sym.replace("-USDT", ""))
        try:
            px = float(px)
            if not math.isfinite(px) or px <= 0:
                continue
            usable.append((p, px))
        except (TypeError, ValueError):
            continue
    output = []
    for change in changes_pct:
        shock = float(change) / 100.0
        total = 0.0
        for p, px in usable:
            hypothetical = px * (1.0 + shock)
            direction = 1.0 if p.get("side") == "LONG" else -1.0
            total += (hypothetical - float(p.get("entry") or 0)) * float(p.get("qty_open") or 0) * direction
        output.append({"change_pct": float(change), "unrealized_pnl": total})
    return output
