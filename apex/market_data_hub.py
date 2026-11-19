# -*- coding: utf-8 -*-
"""KALVO 7.0 unified market-data quality and snapshot layer.

The hub does not invent timestamps or prices. It distinguishes a quote's local
retrieval time from a provider timestamp, validates OHLCV, tracks source
consistency, and marks snapshots unsuitable for automated paper decisions when
critical provenance is missing.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional

from . import market_apis as market

DEFAULT_QUOTE_MAX_AGE = 45.0
DEFAULT_CANDLE_MAX_AGE_MULTIPLIER = 2.2


def _positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timeframe_seconds(timeframe: str) -> int:
    from .analysis_engine import TIMEFRAME_SECONDS
    try:
        return int(TIMEFRAME_SECONDS[str(timeframe).lower()])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"تایم‌فریم نامعتبر است: {timeframe}")


def normalize_quote(symbol: str, raw: Dict[str, Any], now: Optional[float] = None,
                    max_age: float = DEFAULT_QUOTE_MAX_AGE) -> Dict[str, Any]:
    """Normalize and validate one quote without conflating retrieval/provider time."""
    current = float(now if now is not None else time.time())
    quote = dict(raw or {})
    price = _positive(quote.get("price"))
    source = str(quote.get("source") or "").strip().lower() or None
    retrieved_at = quote.get("retrieved_at") or quote.get("ts")
    try:
        retrieved_at = float(retrieved_at) if retrieved_at is not None else None
    except (TypeError, ValueError):
        retrieved_at = None
    retrieval_age = current - retrieved_at if retrieved_at else None
    provider_timestamp = quote.get("provider_ts") or quote.get("source_timestamp")
    try:
        provider_timestamp = float(provider_timestamp) if provider_timestamp is not None else None
    except (TypeError, ValueError):
        provider_timestamp = None
    provider_age = current - provider_timestamp if provider_timestamp else None

    retrieval_ok = (
        price is not None and bool(source) and retrieval_age is not None
        and -5.0 <= retrieval_age <= max_age and not quote.get("error")
        and not quote.get("stale")
    )
    provider_time_ok = (
        provider_age is not None and -5.0 <= provider_age <= max_age * 2.0
    )
    # Preserve a provider's semantic quality class. A daily/delayed/estimated
    # commodity quote must not become "fresh" merely because it was polled now.
    source_status = str(quote.get("status") or "").strip().lower()
    restricted_statuses = {"stale", "unavailable", "unverified", "delayed", "estimated"}
    if not retrieval_ok:
        status = "unavailable" if price is None or not source else "stale"
    elif source_status in restricted_statuses:
        status = source_status
    elif provider_timestamp is None:
        status = "unverified"
    elif not provider_time_ok:
        status = "stale"
    else:
        status = "fresh"

    bid = _positive(quote.get("bid"))
    ask = _positive(quote.get("ask"))
    spread_pct = None
    if bid and ask and ask >= bid:
        mid = (bid + ask) / 2.0
        spread_pct = ((ask - bid) / mid * 100.0) if mid > 0 else None

    executable_quote_valid = bool(
        bid is not None and ask is not None and ask >= bid
        and spread_pct is not None and spread_pct <= 2.0
    )
    quote.update({
        "canonical_symbol": str(quote.get("base") or symbol).upper(),
        "price": price,
        "source": source,
        "retrieved_at": retrieved_at,
        "retrieval_age_seconds": round(max(0.0, retrieval_age), 2) if retrieval_age is not None else None,
        "provider_ts": provider_timestamp,
        "provider_age_seconds": round(max(0.0, provider_age), 2) if provider_age is not None else None,
        "timestamp_quality": "provider" if provider_timestamp is not None else "retrieval_only",
        "spread_pct": round(spread_pct, 6) if spread_pct is not None else None,
        "status": status,
        "stale": status in ("stale", "unavailable"),
        "display_status_fa": {
            "fresh": "قیمت تازه با زمان منبع",
            "unverified": "دریافت تازه؛ زمان منبع تأییدنشده",
            "delayed": "قیمت با تأخیر منبع",
            "estimated": "قیمت برآوردی؛ صرفاً برای نمایش و تحلیل محدود",
            "stale": "قیمت کهنه یا خارج از مهلت",
            "unavailable": "قیمت ناموجود",
        }.get(status, "وضعیت قیمت نامشخص"),
        # A quote can be displayed after a fresh poll, but only a provider-time-
        # verified quote may mark the paper ledger or authorize automatic decisions.
        "retrieval_valid": bool(retrieval_ok),
        "display_only": not (retrieval_ok and status == "fresh"),
        "quote_valid": bool(retrieval_ok and status == "fresh"),
        "execution_quote_valid": bool(
            retrieval_ok and status == "fresh" and executable_quote_valid
        ),
    })
    return quote


def get_quote(symbol: str, now: Optional[float] = None,
              max_age: float = DEFAULT_QUOTE_MAX_AGE,
              force_refresh: bool = False) -> Dict[str, Any]:
    """Read one quote and attach explicit quality/provenance metadata."""
    raw = market.get_ticker(symbol)
    return normalize_quote(symbol, raw, now=now, max_age=max_age)


def _fetch_candles(symbol: str, timeframe: str, provider: Optional[str] = None) -> tuple[list[dict], Optional[str]]:
    try:
        rows = market.fetch_candles(symbol, timeframe, provider=provider)
    except TypeError:
        # Compatibility with test doubles / downstream adapters using the old signature.
        rows = market.fetch_candles(symbol, timeframe)
    rows = list(rows or [])
    source = None
    if rows:
        sources = {str(row.get("source") or "").strip().lower() for row in rows if row.get("source")}
        if len(sources) == 1:
            source = next(iter(sources))
        elif len(sources) > 1:
            source = "mixed"
    return rows, source


def validate_ohlcv(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strictly validate, deduplicate and sort OHLCV rows."""
    clean: Dict[int, Dict[str, Any]] = {}
    for row in rows or []:
        try:
            stamp = int(float(row.get("time")))
            values = {k: float(row[k]) for k in ("open", "high", "low", "close")}
            volume = float(row.get("volume") or 0.0)
            nums = list(values.values()) + [volume]
            if stamp <= 0 or not all(math.isfinite(v) for v in nums):
                continue
            if min(values.values()) <= 0 or volume < 0:
                continue
            if values["high"] < max(values["open"], values["close"], values["low"]):
                continue
            if values["low"] > min(values["open"], values["close"], values["high"]):
                continue
            clean[stamp] = {
                "time": stamp, **values, "volume": volume,
                **({"source": row.get("source")} if row.get("source") else {}),
            }
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
    return [clean[stamp] for stamp in sorted(clean)]


def get_market_bundle(symbol: str, timeframes: Iterable[str], now: Optional[float] = None,
                      quote_max_age: float = DEFAULT_QUOTE_MAX_AGE) -> Dict[str, Any]:
    """Build a consistent quote + completed-candle bundle for decision systems."""
    from . import analysis_engine as analysis

    current = float(now if now is not None else time.time())
    quote = get_quote(symbol, now=current, max_age=quote_max_age)
    price = _positive(quote.get("price"))
    provider = quote.get("source")
    frames: Dict[str, Dict[str, Any]] = {}
    problems: List[str] = []
    provider_consistent = bool(provider)

    if not quote.get("quote_valid"):
        problems.append(quote.get("display_status_fa") or "مظنه قابل استفاده نیست")
        # This bundle is a decision/execution boundary, not a historical chart
        # endpoint. Without a verified live quote there is no reason to issue
        # additional candle requests: doing so wastes provider capacity and can
        # leave background workers blocked after the quote is already unusable.
        return {
            "symbol": str(symbol).upper(),
            "quote": quote,
            "price": price,
            "provider": provider,
            "timeframes": {},
            "provider_consistent": False,
            "quality_score": 0,
            "trade_ready": False,
            "ready": False,
            "problems": list(dict.fromkeys(problems)),
            "generated_at": int(current),
            "mode": "paper_only",
        }

    for timeframe in timeframes:
        seconds = _timeframe_seconds(timeframe)
        try:
            raw, candle_provider = _fetch_candles(symbol, timeframe, provider=provider)
            clean = validate_ohlcv(raw)
            closed = analysis.completed_candles(clean, timeframe, current)
            if not closed:
                frames[timeframe] = {"ready": False, "reason": f"کندل بسته‌شدهٔ {timeframe} وجود ندارد",
                                     "source": candle_provider, "count": 0}
                problems.append(f"کندل بسته‌شدهٔ {timeframe} موجود نیست")
                provider_consistent = False
                continue
            last_ts = int(closed[-1]["time"])
            candle_age = current - (last_ts + seconds)
            fresh = -5.0 <= candle_age <= seconds * DEFAULT_CANDLE_MAX_AGE_MULTIPLIER
            same_provider = bool(candle_provider and provider and candle_provider == provider)
            if not same_provider:
                provider_consistent = False
                problems.append(f"منبع کندل {timeframe} با منبع قیمت یکسان نیست")
            if not fresh:
                problems.append(f"کندل {timeframe} کهنه یا زمان آن نامعتبر است")
            result = analysis.analyze_candles(closed)
            result.update({
                "source": candle_provider,
                "last_candle_ts": last_ts,
                "candle_age_seconds": round(max(0.0, candle_age), 2),
                "fresh": fresh,
                "provider_match": same_provider,
                "raw_count": len(clean),
                "closed_count": len(closed),
            })
            if not fresh:
                result["ready"] = False
                result["reason"] = f"کندل بسته‌شدهٔ {timeframe} تازه نیست"
            frames[timeframe] = result
        except Exception as exc:
            frames[timeframe] = {"ready": False, "reason": str(exc)[:140], "source": None}
            provider_consistent = False
            problems.append(f"خطا در دریافت {timeframe}: {str(exc)[:90]}")

    all_ready = bool(frames) and all(bool(row.get("ready")) for row in frames.values())
    quality_score = 100
    if not quote.get("quote_valid"):
        quality_score -= 45
    if quote.get("timestamp_quality") != "provider":
        quality_score -= 20
    if not provider_consistent:
        quality_score -= 25
    if any(not row.get("ready") for row in frames.values()):
        quality_score -= 20
    quality_score = max(0, min(100, quality_score))
    if not quote.get("execution_quote_valid"):
        problems.append("مظنهٔ دوطرفهٔ قابل‌اجرا (Bid/Ask) موجود نیست یا اسپرد از حد مجاز بیشتر است")
    trade_ready = bool(
        quote.get("quote_valid") and quote.get("execution_quote_valid")
        and quote.get("status") == "fresh" and provider_consistent
        and all_ready and price is not None and quality_score >= 80
    )
    return {
        "symbol": str(symbol).upper(),
        "quote": quote,
        "price": price,
        "provider": provider,
        "timeframes": frames,
        "provider_consistent": provider_consistent,
        "quality_score": quality_score,
        "trade_ready": trade_ready,
        "ready": all_ready,
        "problems": list(dict.fromkeys(problems)),
        "generated_at": int(current),
        "mode": "paper_only",
    }


def get_quotes_batch(symbols: Iterable[str], max_workers: int = 6,
                     now: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """Bounded concurrent quote refresh with per-symbol error isolation."""
    unique = list(dict.fromkeys(str(s).strip() for s in symbols if str(s).strip()))[:40]
    if not unique:
        return {}
    output: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), len(unique)))) as pool:
        jobs = {pool.submit(get_quote, symbol, now): symbol for symbol in unique}
        for future in as_completed(jobs):
            symbol = jobs[future]
            try:
                output[symbol] = future.result()
            except Exception as exc:
                output[symbol] = {
                    "canonical_symbol": symbol.upper(), "price": None, "source": None,
                    "status": "unavailable", "stale": True, "quote_valid": False,
                    "error": str(exc)[:140], "timestamp_quality": "missing",
                }
    return output


def prices_for_ledger(quotes: Dict[str, Dict[str, Any]]) -> Dict[str, float]:
    """Return only valid, fresh retrieval marks for the paper ledger."""
    prices: Dict[str, float] = {}
    for symbol, quote in (quotes or {}).items():
        value = _positive(quote.get("price"))
        if value is None or not quote.get("quote_valid"):
            continue
        base = str(quote.get("base") or symbol).upper()
        prices[base] = value
        if quote.get("quote_currency") == "USDT" or quote.get("symbol", "").endswith("-USDT"):
            prices[f"{base}-USDT"] = value
    return prices
