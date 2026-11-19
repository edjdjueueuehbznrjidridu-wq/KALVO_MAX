# -*- coding: utf-8 -*-
"""Quote-aware paper execution helpers. No exchange order API exists here."""
from __future__ import annotations
import math
from typing import Any, Dict


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def market_fill(quote: Dict[str, Any], action: str, slippage_bps: float = 2.0,
                require_fresh: bool = True, max_spread_pct: float = 2.0) -> Dict[str, float | str]:
    """Estimate conservative local paper fill from a fresh bid/ask quote."""
    quote = dict(quote or {})
    action = str(action or "").strip().upper()
    if action not in ("BUY", "SELL"):
        raise ValueError("جهت اجرای شبیه‌سازی باید BUY یا SELL باشد")
    if require_fresh:
        if quote.get("status") != "fresh" or quote.get("quote_valid") is not True:
            raise ValueError("مظنهٔ منبع‌تأییدشده و تازه برای شبیه‌سازی اجرا وجود ندارد")
        if not str(quote.get("source") or "").strip():
            raise ValueError("منبع مظنه مشخص نیست؛ اجرای شبیه‌سازی متوقف شد")
        if _positive(quote.get("provider_ts")) is None:
            raise ValueError("زمان ثبت‌شده توسط ارائه‌دهنده موجود نیست؛ اجرای شبیه‌سازی متوقف شد")
    bid, ask, last = _positive(quote.get("bid")), _positive(quote.get("ask")), _positive(quote.get("price"))
    if bid is None or ask is None:
        raise ValueError("قیمت خرید/فروش (Bid/Ask) موجود نیست؛ اجرای شبیه‌سازی متوقف شد")
    if ask < bid:
        raise ValueError("دفتر مظنه نامعتبر است؛ Ask از Bid کمتر است")
    mid = (bid + ask) / 2.0
    if last is not None and abs(last - mid) / mid > 0.15:
        raise ValueError("آخرین قیمت با Bid/Ask اختلاف غیرعادی دارد")
    try:
        bps = float(slippage_bps)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("فرض لغزش قیمت نامعتبر است")
    if not math.isfinite(bps) or not 0.0 <= bps <= 50.0:
        raise ValueError("فرض لغزش قیمت باید بین صفر تا ۵۰ واحد پایه باشد")
    reference = ask if action == "BUY" else bid
    factor = 1.0 + bps / 10000.0 if action == "BUY" else 1.0 - bps / 10000.0
    fill = reference * factor
    if not math.isfinite(fill) or fill <= 0:
        raise ValueError("قیمت اجرای شبیه‌سازی نامعتبر شد")
    try:
        spread_limit = float(max_spread_pct)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("حداکثر اسپرد نامعتبر است")
    spread_pct = ((ask - bid) / mid * 100.0) if mid else 0.0
    if not math.isfinite(spread_limit) or not 0.0 < spread_limit <= 10.0:
        raise ValueError("حداکثر اسپرد باید بین صفر تا ۱۰ درصد باشد")
    if spread_pct > spread_limit:
        raise ValueError(f"اسپرد {spread_pct:.3f}% از سقف {spread_limit:.3f}% بیشتر است")
    return {"action": action, "reference_price": reference, "fill_price": fill,
            "bid": bid, "ask": ask, "mid": mid, "spread_pct": spread_pct,
            "slippage_bps": bps, "provider_ts": _positive(quote.get("provider_ts")) or 0.0}
