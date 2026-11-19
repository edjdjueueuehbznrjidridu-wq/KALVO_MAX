# -*- coding: utf-8 -*-
"""Transparent, local statistics over closed KALVO paper trades.

This module does not train a predictive model and never changes trade signals.
It summarizes only the user's own simulated closed-trade history.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from . import storage


def _safe_float(value: Any) -> float:
    try:
        result = float(value or 0.0)
        return result if result == result and abs(result) != float("inf") else 0.0
    except (TypeError, ValueError):
        return 0.0


def analyze_history(limit: int = 300) -> Dict[str, Any]:
    """Return cautious descriptive stats; groups need enough samples."""
    trades = storage.list_trades(max(1, min(1000, int(limit))))
    # list_trades is newest-first. Reverse to calculate a chronological equity curve.
    chronological = list(reversed(trades))
    pnls = [_safe_float(t.get("pnl")) for t in chronological]
    wins = [p for p in pnls if p > 1e-9]
    losses = [p for p in pnls if p < -1e-9]
    flats = len(pnls) - len(wins) - len(losses)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 1e-12 else None
    win_rate = (len(wins) / len(pnls) * 100.0) if pnls else 0.0

    running = peak = max_drawdown = 0.0
    for pnl in pnls:
        running += pnl
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)

    by_symbol: Dict[str, List[float]] = defaultdict(list)
    by_side: Dict[str, List[float]] = defaultdict(list)
    by_reason: Dict[str, List[float]] = defaultdict(list)
    by_source: Dict[str, List[float]] = defaultdict(list)
    for trade, pnl in zip(chronological, pnls):
        by_symbol[str(trade.get("symbol") or "?").upper()].append(pnl)
        by_side[str(trade.get("side") or "?").upper()].append(pnl)
        by_reason[str(trade.get("reason") or "نامشخص").upper()].append(pnl)
        by_source[str(trade.get("trade_source") or "manual").lower()].append(pnl)

    def summarize(groups: Dict[str, List[float]], minimum: int) -> Dict[str, Dict[str, Any]]:
        out = {}
        for key, values in groups.items():
            if len(values) < minimum:
                continue
            positive = [v for v in values if v > 1e-9]
            out[key] = {
                "trades": len(values),
                "win_rate": len(positive) / len(values) * 100.0,
                "net_pnl": sum(values),
                "avg_pnl": sum(values) / len(values),
            }
        return out

    sample = len(pnls)
    confidence_note = (
        "نمونهٔ بسیار کم؛ فقط گزارش توصیفی، بدون نتیجه‌گیری دربارهٔ برتری روش"
        if sample < 10 else
        "نمونهٔ محدود؛ مقایسه‌ها هنوز ناپایدارند"
        if sample < 30 else
        "نمونهٔ قابل بررسی؛ همچنان وابسته به شرایط بازار و روش اجرا"
    )
    tips = []
    if sample < 10:
        tips.append("برای ارزیابی عملکرد، ابتدا نمونهٔ بیشتری از معاملات دمو ثبت کن.")
    else:
        avg_win = gross_profit / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0
        if win_rate < 40:
            tips.append("نرخ معاملات مثبت پایین است؛ شرایط ورود و کیفیت سیگنال‌ها را بازبینی کن.")
        if avg_loss > 0 and avg_win > 0 and avg_loss > avg_win * 1.5:
            tips.append("میانگین زیان از میانگین سود بزرگ‌تر است؛ نسبت ریسک به بازده و خروج‌ها را بررسی کن.")
        if max_drawdown > 0:
            tips.append("افت از قلهٔ منحنی سود ثبت شده؛ اندازهٔ موقعیت و توالی زیان‌ها را مرور کن.")
        if not tips:
            tips.append("الگوی هشداردهندهٔ واضحی از این نمونه دیده نشد؛ این به معنی تضمین عملکرد آینده نیست.")

    return {
        "ok": bool(pnls), "sample": sample, "wins": len(wins), "losses": len(losses),
        "flats": flats, "win_rate": win_rate, "net_pnl": sum(pnls),
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_win": gross_profit / len(wins) if wins else 0.0,
        "avg_loss": gross_loss / len(losses) if losses else 0.0,
        "max_drawdown": max_drawdown,
        "by_symbol": summarize(by_symbol, 5),
        "by_side": summarize(by_side, 10),
        "by_reason": summarize(by_reason, 3),
        "by_source": summarize(by_source, 10),
        "best_symbols": ([k for k, v in sorted(summarize(by_symbol, 5).items(),
                         key=lambda item: (item[1]["net_pnl"], item[1]["win_rate"]), reverse=True)[:5]]
                         if sample >= 30 else []),
        "worst_symbols": ([k for k, v in sorted(summarize(by_symbol, 5).items(),
                          key=lambda item: (item[1]["net_pnl"], item[1]["win_rate"]))[:5]]
                          if sample >= 30 else []),
        "confidence_note": confidence_note, "tips": tips,
    }


def format_history_fa(result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return "هنوز معاملهٔ بسته‌شده‌ای در ژورنال دمو وجود ندارد. پس از ثبت چند معامله، گزارش آماری در دسترس خواهد بود."
    pf = result.get("profit_factor")
    pf_text = f"{pf:.2f}" if pf is not None else ("بدون زیان ثبت‌شده" if result["gross_profit"] > 0 else "—")
    lines = [
        "گزارش آماری ژورنال معاملات دمو",
        f"تعداد نمونه: {result['sample']} · مثبت: {result['wins']} · منفی: {result['losses']} · سربه‌سر: {result['flats']}",
        f"نرخ معاملات مثبت: {result['win_rate']:.1f}% · سود/زیان خالص: {result['net_pnl']:+.2f} USDT",
        f"میانگین سود: {result['avg_win']:.2f} · میانگین زیان: {result['avg_loss']:.2f} USDT",
        f"Profit Factor: {pf_text} · بیشترین افت از قله: {result['max_drawdown']:.2f} USDT",
        f"اعتبار نمونه: {result['confidence_note']}",
    ]
    if result["by_symbol"]:
        lines.append("\nعملکرد نمادها (حداقل ۵ معامله برای هر نماد):")
        for symbol, stats in sorted(result["by_symbol"].items(), key=lambda x: x[1]["net_pnl"], reverse=True):
            lines.append(f"{symbol}: {stats['trades']} معامله · نرخ مثبت {stats['win_rate']:.1f}% · خالص {stats['net_pnl']:+.2f}")
    if result["by_side"]:
        lines.append("\nعملکرد جهت‌ها (حداقل ۱۰ معامله برای هر جهت):")
        for side, stats in sorted(result["by_side"].items()):
            lines.append(f"{side}: {stats['trades']} معامله · نرخ مثبت {stats['win_rate']:.1f}% · خالص {stats['net_pnl']:+.2f}")
    if result.get("by_reason"):
        lines.append("\nنتیجه بر اساس علت خروج:")
        for reason, stats in sorted(result["by_reason"].items(), key=lambda item: item[1]["trades"], reverse=True):
            lines.append(f"{reason}: {stats['trades']} خروج · خالص {stats['net_pnl']:+.2f} USDT")
    if result.get("by_source"):
        lines.append("\nمقایسهٔ روش ورود (حداقل ۱۰ خروج برای هر گروه):")
        for source, stats in sorted(result["by_source"].items()):
            lines.append(f"{source}: {stats['trades']} معامله · نرخ مثبت {stats['win_rate']:.1f}% · خالص {stats['net_pnl']:+.2f}")
    if result.get("best_symbols") or result.get("worst_symbols"):
        lines.append("\nرتبه‌بندی نماد فقط با حداقل ۳۰ معاملهٔ کل و ۵ نمونه برای هر نماد فعال می‌شود:")
        if result.get("best_symbols"):
            lines.append("نمادهای بهتر در نمونهٔ گذشته: " + ", ".join(result["best_symbols"]))
        if result.get("worst_symbols"):
            lines.append("نمادهای نیازمند بازبینی: " + ", ".join(result["worst_symbols"]))
    lines.append("\nنکات بازبینی:")
    lines.extend("• " + tip for tip in result["tips"])
    lines.append("\nاین گزارش توصیفی از معاملات مجازی گذشته است؛ مدل پیش‌بینی نیست و سیگنال یا نتیجهٔ آینده را تضمین نمی‌کند.")
    return "\n".join(lines)



def adaptive_profile() -> Dict[str, Any]:
    """Conservative demo-only feedback loop based on the user's own closed trades.

    The profile never changes market direction or claims to predict prices.
    It can only retain or reduce the demo auto-scanner's risk and tighten its
    entry filter. Fewer than 20 observations are treated as an insufficient
    sample, so the default policy remains unchanged.
    """
    trades = storage.list_trades(100)
    pnls = [_safe_float(t.get("pnl")) for t in trades[:30]]
    sample = len(pnls)
    profile = {
        "sample": sample, "risk_scale": 1.0, "min_confluence": 70,
        "auto_paused": False, "state": "warmup",
        "note": "نمونهٔ کافی برای تغییر سیاست دمو وجود ندارد؛ تنظیم پایه حفظ شده است.",
    }
    if sample < 20:
        return profile
    recent = pnls[:20]
    wins = [x for x in recent if x > 1e-9]
    losses = [x for x in recent if x < -1e-9]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 1e-12 else (99.0 if gross_profit > 0 else 0.0)
    streak = 0
    for value in recent:
        if value < -1e-9:
            streak += 1
        else:
            break
    # Recent rows are newest-first. A loss streak is therefore counted from
    # the newest result backwards, not from the oldest end of the sample.
    if streak >= 5:
        profile.update({"risk_scale": 0.0, "min_confluence": 90,
                        "auto_paused": True, "state": "cooldown",
                        "note": "پنج نتیجهٔ منفی متوالی ثبت شده؛ اسکنر خودکار دمو تا بازبینی ژورنال متوقف می‌ماند."})
    elif pf < 0.75:
        profile.update({"risk_scale": 0.50, "min_confluence": 85,
                        "state": "reduced", "note": "عملکرد نمونهٔ اخیر ضعیف است؛ اندازهٔ موقعیت دمو نصف و فیلتر سخت‌گیرتر شده است."})
    elif pf < 1.0:
        profile.update({"risk_scale": 0.75, "min_confluence": 80,
                        "state": "cautious", "note": "نتایج اخیر نامطمئن‌اند؛ ریسک دمو کاهش یافته و فیلتر ورود کمی سخت‌تر شده است."})
    else:
        profile.update({"risk_scale": 1.0, "min_confluence": 75,
                        "state": "base", "note": "نتایج اخیر از محدودسازی بیشتر پشتیبانی نمی‌کنند؛ ریسک پایه حفظ شده و افزایش خودکار انجام نمی‌شود."})
    profile["profit_factor_recent"] = round(pf, 3)
    profile["win_rate_recent"] = round(len(wins) / len(recent) * 100.0, 1)
    profile["loss_streak"] = streak
    return profile


def generate_weekly_review(now=None) -> Dict[str, Any]:
    """Create an evidence-backed local weekly coach report from the demo journal.

    This is deterministic statistical text, not a hosted LLM or a price forecast.
    """
    import time
    from collections import defaultdict
    stamp = int(now if now is not None else time.time())
    cutoff = stamp - 7 * 86400
    rows = [t for t in storage.list_trades(1000) if int(t.get("ts") or 0) >= cutoff]
    values = [_safe_float(t.get("pnl")) for t in rows]
    wins = [x for x in values if x > 1e-9]
    losses = [x for x in values if x < -1e-9]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 1e-12 else None
    by_symbol = defaultdict(float)
    by_reason = defaultdict(float)
    for row, pnl in zip(rows, values):
        by_symbol[str(row.get("symbol") or "—")] += pnl
        by_reason[str(row.get("reason") or "نامشخص")] += pnl
    stats = {
        "period_days": 7, "sample": len(values), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": len(wins) / len(values) * 100 if values else 0.0,
        "net_pnl": sum(values), "avg_pnl": sum(values) / len(values) if values else 0.0,
        "profit_factor": pf, "best_trade": max(values) if values else 0.0,
        "worst_trade": min(values) if values else 0.0,
        "best_symbol": max(by_symbol, key=by_symbol.get) if by_symbol else None,
        "worst_symbol": min(by_symbol, key=by_symbol.get) if by_symbol else None,
        "by_reason": dict(by_reason),
    }
    lines = ["گزارش هفتگی مربی هوشمند KALVO · فقط ژورنال دمو",
             f"بازه: {time.strftime('%Y-%m-%d', time.localtime(cutoff))} تا {time.strftime('%Y-%m-%d', time.localtime(stamp))}",
             f"تعداد خروج‌های ثبت‌شده: {stats['sample']} · مثبت: {stats['wins']} · منفی: {stats['losses']}",
             f"سود/زیان خالص: {stats['net_pnl']:+.2f} USDT مجازی · نرخ مثبت: {stats['win_rate_pct']:.1f}%"]
    lines.append(f"میانگین هر خروج: {stats['avg_pnl']:+.2f} USDT · بهترین: {stats['best_trade']:+.2f} · ضعیف‌ترین: {stats['worst_trade']:+.2f}")
    lines.append(f"Profit Factor: {pf:.2f}" if pf is not None else "Profit Factor: برای محاسبه نمونهٔ زیان کافی نیست")
    if len(values) < 5:
        lines.append("ارزیابی: نمونه کمتر از ۵ خروج است؛ این هفته فقط گزارش توصیفی می‌دهیم و هیچ روش برتر یا ضعیف‌تری اعلام نمی‌کنیم.")
        lines.append("پیشنهاد تمرینی: قبل از تغییر قواعد، ژورنال را با دلیل ورود، حد ضرر و علت خروج کامل کن.")
    else:
        if stats["net_pnl"] < 0:
            lines.append("بازبینی: خالص هفته منفی است؛ ابتدا اندازهٔ موقعیت، هزینه‌ها و پایبندی به حد ضرر را مرور کن.")
        if stats["win_rate_pct"] < 40:
            lines.append("بازبینی: نرخ خروج‌های مثبت پایین بوده؛ کیفیت ورود و شرایط بازار را کنار نتیجهٔ هر معامله مقایسه کن.")
        if stats["best_symbol"]:
            lines.append(f"نماد با خالص بهتر در همین نمونه: {stats['best_symbol']}؛ این رتبه‌بندی پیش‌بینی آینده نیست.")
        if not any(x in " ".join(lines) for x in ("خالص هفته منفی", "نرخ خروج‌های مثبت پایین")):
            lines.append("بازبینی: هشدار آماری غالبی در این نمونه دیده نشد؛ هزینه‌ها و افت سرمایه را همچنان کنترل کن.")
    lines.append("KALVO از این داده‌ها برای گزارش و یادگیری توصیفی استفاده می‌کند؛ عملکرد گذشته، سیگنال قطعی یا تضمین سود نمی‌سازد.")
    return {"stats": stats, "report": "\n".join(lines), "generated_ts": stamp}
