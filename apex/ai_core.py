# -*- coding: utf-8 -*-
"""KALVO AI core (paper trading only).

One coherent layer that sits between the market scanner and the virtual ledger:

* regime detection        -> classify the market state of every candidate
* experience memory       -> every decision is logged with its context and later
                             linked to the closed trade (long-term memory)
* risk governor           -> equity-drawdown limits that reduce or pause the scanner
* explainability          -> each decision carries human-readable reasons
* model scorecard         -> measures how the optional cloud model's verdicts
                             actually performed (independent self-evaluation)

Hard rule: this layer (and any cloud model behind it) can only *veto or reduce*
risk. It can never enlarge a position or loosen a local safety filter.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from . import storage
from . import defaults

LOG_KEY = "ai_decision_log_v1"
LOG_MAX = 300
REGIME_FA = {
    "trend": "رونددار", "range": "دامنه‌ای", "mixed": "گذار",
    "volatile": "پرنوسان", "unknown": "نامشخص",
}


def _f(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        x = float(value)
        return x if x == x and abs(x) != float("inf") else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- regime
def classify_regime(hourly: Dict[str, Any]) -> Dict[str, Any]:
    """Classify market state from the 1h analysis (ADX + volatility ratio)."""
    adx = _f((hourly or {}).get("adx14"), None)
    vol_ratio = _f((hourly or {}).get("volatility_ratio"), 1.0)
    if vol_ratio is not None and vol_ratio >= 1.6:
        regime = "volatile"
    elif adx is None:
        regime = "unknown"
    elif adx >= 25:
        regime = "trend"
    elif adx <= 18:
        regime = "range"
    else:
        regime = "mixed"
    return {"regime": regime, "label": REGIME_FA[regime], "adx": adx,
            "volatility_ratio": vol_ratio}


# ---------------------------------------------------------------- risk governor
def drawdown_state() -> Dict[str, Any]:
    """Realized-equity drawdown from the journal (independent of locked margin)."""
    try:
        trades = list(reversed(storage.list_trades(1000)))
        wallet = storage.get_wallet()
    except Exception:
        return {"state": "ok", "risk_mult": 1.0, "drawdown_pct": 0.0, "paused": False}
    base = _f(wallet.get("total_deposited"), 0.0) or 100.0
    cum = peak = 0.0
    for t in trades:
        cum += _f(t.get("pnl"), 0.0)
        peak = max(peak, cum)
    dd_pct = max(0.0, (peak - cum) / max(base + peak, 1e-9) * 100.0)
    if dd_pct >= defaults.DRAWDOWN_PAUSE_PCT:
        state, mult = "paused", 0.0
    elif dd_pct >= defaults.DRAWDOWN_REDUCE_PCT:
        state, mult = "reduced", 0.5
    else:
        state, mult = "ok", 1.0
    return {"state": state, "risk_mult": mult, "drawdown_pct": round(dd_pct, 2),
            "paused": state == "paused"}


# ---------------------------------------------------------------- assessment
def assess_candidate(symbol: str, side: str, score: int,
                     hourly: Dict[str, Any], memory=None) -> Dict[str, Any]:
    """Explainable, reduce-only assessment of one scanner candidate."""
    reg = classify_regime(hourly)
    explain: List[str] = [
        f"رژیم بازار: {reg['label']}"
        + (f" (ADX={reg['adx']:.1f})" if reg["adx"] is not None else "")
    ]
    risk_mult, extra_score, allow = 1.0, 0, True
    if reg["regime"] == "volatile":
        risk_mult *= 0.75
        explain.append("نوسان بالاتر از معمول؛ اندازهٔ موقعیت ۲۵٪ کمتر شد.")
    if memory is not None:
        try:
            info = memory.regime_bias(reg["regime"])
        except Exception:
            info = None
        if info and info.get("n", 0) >= 8:
            bias = float(info.get("bias", 0.0))
            if bias <= -0.6:
                allow = False
                explain.append(f"در رژیم «{reg['label']}» نتایج گذشتهٔ خودت ضعیف بوده "
                               f"(n={info['n']}، bias={bias:+.2f}) → ورود رد شد.")
            elif bias <= -0.3:
                extra_score = 5
                explain.append(f"در رژیم «{reg['label']}» نتایج گذشته ضعیف است؛ "
                               "حداقل امتیاز ۵ واحد سخت‌تر شد.")
            else:
                explain.append(f"حافظه برای این رژیم مخالفتی ندارد (n={info['n']}، bias={bias:+.2f}).")
        else:
            explain.append("برای این رژیم هنوز نمونهٔ کافی در حافظه نیست؛ سیاست پایه حفظ شد.")
    return {"regime": reg["regime"], "regime_label": reg["label"], "allow": allow,
            "risk_mult": risk_mult, "extra_score": extra_score, "explain": explain}


# ---------------------------------------------------------------- experience log
def _load_log() -> List[Dict[str, Any]]:
    raw = storage.get_setting(LOG_KEY, "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def log_decision(entry: Dict[str, Any]) -> None:
    """Append one decision (opened / vetoed / skipped-by-ai) to the long-term log."""
    try:  # logging must never break a scan
        log = _load_log()
        row = dict(entry)
        row.setdefault("ts", int(time.time()))
        log.append(row)
        storage.set_setting(LOG_KEY, json.dumps(log[-LOG_MAX:], ensure_ascii=False))
    except Exception:
        pass


def recent_decisions(limit: int = 20) -> List[Dict[str, Any]]:
    return _load_log()[-max(1, limit):]


def regime_from_note(note: str) -> Optional[str]:
    m = re.search(r"regime=([a-z]+)", note or "")
    return m.group(1) if m else None


def gate_from_note(note: str) -> Optional[str]:
    m = re.search(r"gate=([a-z_]+)", note or "")
    return m.group(1) if m else None


def model_scorecard() -> Dict[str, Dict[str, Any]]:
    """How did entries perform per cloud-gate verdict? (self-evaluation of the model)."""
    out: Dict[str, List[float]] = {}
    for t in storage.list_trades(500):
        if t.get("trade_source") != "auto":
            continue
        verdict = gate_from_note(t.get("position_note") or "") or "none"
        out.setdefault(verdict, []).append(_f(t.get("pnl"), 0.0))
    card = {}
    for verdict, vals in out.items():
        wins = [v for v in vals if v > 1e-9]
        card[verdict] = {"n": len(vals), "win_rate": len(wins) / len(vals) * 100.0,
                         "net_pnl": sum(vals)}
    return card


def format_core_status_fa() -> str:
    dd = drawdown_state()
    lines = ["هستهٔ هوش KALVO (فقط شبیه‌سازی)",
             f"محافظ سرمایه: {dd['state']} · افت از قله: {dd['drawdown_pct']:.2f}%"]
    card = model_scorecard()
    if card:
        lines.append("عملکرد ورودها به تفکیک نظر مدل ابری:")
        for verdict, s in card.items():
            lines.append(f"  · {verdict}: n={s['n']} · نرخ مثبت {s['win_rate']:.0f}% · خالص {s['net_pnl']:+.2f}")
    recent = recent_decisions(5)
    if recent:
        lines.append("آخرین تصمیم‌ها:")
        for r in recent:
            lines.append(f"  · {r.get('symbol')} {r.get('side')} → {r.get('action')} "
                         f"({r.get('regime', '—')}) {r.get('why', '')}"[:140])
    lines.append("این لایه فقط می‌تواند رد یا کم‌کردن ریسک کند؛ هرگز ریسک را افزایش نمی‌دهد.")
    return "\n".join(lines)
