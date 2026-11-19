# -*- coding: utf-8 -*-
"""Public market-data adapters for KALVO.

Read-only endpoints only. Failed requests are never presented as fresh data;
failed/empty values are not cached, so the next refresh can recover promptly.
"""
from __future__ import annotations

import json
import math
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request

try:
    import certifi
except Exception:
    certifi = None
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

UA = "KALVO/7.0 (Android; read-only market data)"
_lock = threading.RLock()
_cache: Dict[str, Tuple[float, Any]] = {}
TTL_PX = 2.0
TTL_SLOW = 15.0
MAX_CANDLES = 500
MAX_TICKER_AGE_SECONDS = 45.0


def _get(url: str, timeout: int = 8) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=max(2, min(30, int(timeout))), context=context) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("پاسخ سرویس بازار بیش از حد بزرگ است")
        return raw


def _json(url: str, timeout: int = 8) -> Any:
    data = json.loads(_get(url, timeout).decode("utf-8"))
    if not isinstance(data, (dict, list)):
        raise ValueError("ساختار پاسخ سرویس بازار نامعتبر است")
    return data


def _cached(key: str, ttl: float, fn):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    # Do not cache failed, empty, or structurally unusable market responses.
    cacheable = value is not None and value != {} and value != []
    if isinstance(value, dict):
        if value.get("error"):
            cacheable = False
        if "price" in value and _positive(value.get("price")) is None:
            cacheable = False
        if "bids" in value or "asks" in value:
            cacheable = cacheable and bool(value.get("bids") or value.get("asks"))
    if cacheable:
        with _lock:
            _cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def normalize_symbol(symbol: str) -> Tuple[str, str, str]:
    """Return base, KuCoin spot pair, and Binance spot pair."""
    s = (symbol or "BTC").upper().strip().replace("_", "-").replace("/", "-")
    s = re.sub(r"\s+", "", s)
    if s.endswith("-USDT"):
        base = s[:-5]
    elif s.endswith("USDT"):
        base = s[:-4]
    elif "-" in s:
        raise ValueError("در حال حاضر فقط جفت‌های USDT پشتیبانی می‌شوند")
    else:
        base = s
    if not re.fullmatch(r"[A-Z0-9]{2,20}", base):
        raise ValueError("نماد بازار نامعتبر است")
    return base, f"{base}-USDT", f"{base}USDT"


def _finite_number(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        n = float(value)
        return n if math.isfinite(n) else default
    except (TypeError, ValueError):
        return default


def _positive(value: Any) -> Optional[float]:
    n = _finite_number(value)
    return n if n is not None and n > 0 else None


def _binance_price(base: str) -> Optional[float]:
    try:
        j = _json(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT", 6)
        return _positive(j.get("price"))
    except Exception:
        return None


def _binance_24h(base: str) -> Optional[Dict[str, float]]:
    try:
        j = _json(f"https://api.binance.com/api/v3/ticker/24hr?symbol={base}USDT", 8)
        price = _positive(j.get("lastPrice"))
        if price is None:
            return None
        close_time = _finite_number(j.get("closeTime"))
        return {
            "price": price,
            "change_pct": _finite_number(j.get("priceChangePercent"), 0.0),
            "high": _positive(j.get("highPrice")),
            "low": _positive(j.get("lowPrice")),
            "volume": _positive(j.get("quoteVolume")) or 0.0,
            "bid": _positive(j.get("bidPrice")),
            "ask": _positive(j.get("askPrice")),
            # Provider snapshot boundary, not a promise of last-trade precision.
            "provider_ts": (close_time / 1000.0 if close_time and close_time > 10_000_000_000
                            else close_time),
            "timestamp_quality": "provider_snapshot" if close_time else "retrieval_only",
        }
    except Exception:
        return None


def _kucoin_price(base: str) -> Optional[float]:
    try:
        j = _json(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={base}-USDT", 6)
        if j.get("code") not in (None, "200000"):
            return None
        return _positive((j.get("data") or {}).get("price"))
    except Exception:
        return None


def _kucoin_stats(base: str) -> Optional[Dict[str, Any]]:
    try:
        j = _json(f"https://api.kucoin.com/api/v1/market/stats?symbol={base}-USDT", 8)
        if j.get("code") not in (None, "200000"):
            return None
        d = j.get("data") or {}
        last = _positive(d.get("last"))
        if last is None:
            return None
        provider_time = _finite_number(d.get("time") or d.get("timePoint") or d.get("timestamp"))
        if provider_time and provider_time > 10_000_000_000:
            provider_time /= 1000.0
        return {"price": last, "change_pct": _finite_number(d.get("changeRate"), 0.0) * 100,
                "high": _positive(d.get("high")), "low": _positive(d.get("low")),
                "volume": _positive(d.get("volValue")) or 0.0,
                "bid": _positive(d.get("buy")), "ask": _positive(d.get("sell")),
                "provider_ts": provider_time,
                "timestamp_quality": "provider_snapshot" if provider_time else "retrieval_only"}
    except Exception:
        return None


def _provider_epoch_seconds(value: Any) -> Optional[float]:
    """Normalize provider millisecond/second timestamps without inventing time."""
    stamp = _finite_number(value, None)
    if stamp is None or stamp <= 0:
        return None
    if stamp > 10_000_000_000:
        stamp /= 1000.0
    return stamp


def _bybit_snapshot(base: str) -> Optional[Dict[str, Any]]:
    """Return Bybit spot ticker plus server snapshot time and best bid/ask."""
    try:
        j = _json(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={base}USDT", 6)
        if int(j.get("retCode", -1)) != 0:
            return None
        rows = (j.get("result") or {}).get("list") or []
        if not rows:
            return None
        row = rows[0]
        price = _positive(row.get("lastPrice"))
        provider_ts = _provider_epoch_seconds(j.get("time"))
        if price is None or provider_ts is None:
            return None
        return {
            "price": price,
            "bid": _positive(row.get("bid1Price")),
            "ask": _positive(row.get("ask1Price")),
            "high": _positive(row.get("highPrice24h")),
            "low": _positive(row.get("lowPrice24h")),
            "volume": _positive(row.get("turnover24h")) or 0.0,
            "change_pct": (_finite_number(row.get("price24hPcnt"), 0.0) or 0.0) * 100.0,
            "provider_ts": provider_ts,
            "timestamp_quality": "provider_snapshot",
        }
    except Exception:
        return None


def _okx_snapshot(base: str) -> Optional[Dict[str, Any]]:
    """Return OKX spot ticker with its documented server timestamp."""
    try:
        j = _json(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT", 6)
        if str(j.get("code", "0")) != "0":
            return None
        rows = j.get("data") or []
        if not rows:
            return None
        row = rows[0]
        price = _positive(row.get("last"))
        provider_ts = _provider_epoch_seconds(row.get("ts"))
        if price is None or provider_ts is None:
            return None
        return {
            "price": price,
            "bid": _positive(row.get("bidPx")),
            "ask": _positive(row.get("askPx")),
            "high": _positive(row.get("high24h")),
            "low": _positive(row.get("low24h")),
            "volume": _positive(row.get("volCcy24h")) or 0.0,
            "provider_ts": provider_ts,
            "timestamp_quality": "provider_snapshot",
        }
    except Exception:
        return None


def _kucoin_level1_snapshot(base: str) -> Optional[Dict[str, Any]]:
    """Return KuCoin level-1 quote with provider timestamp and BBO."""
    try:
        j = _json(
            f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={base}-USDT", 6
        )
        if j.get("code") not in (None, "200000"):
            return None
        row = j.get("data") or {}
        price = _positive(row.get("price"))
        provider_ts = _provider_epoch_seconds(row.get("time"))
        if price is None or provider_ts is None:
            return None
        return {
            "price": price,
            "bid": _positive(row.get("bestBid")),
            "ask": _positive(row.get("bestAsk")),
            "provider_ts": provider_ts,
            "timestamp_quality": "provider_snapshot",
        }
    except Exception:
        return None


def _bybit_price(base: str) -> Optional[float]:
    snapshot = _bybit_snapshot(base)
    return _positive(snapshot.get("price")) if snapshot else None


def _okx_price(base: str) -> Optional[float]:
    snapshot = _okx_snapshot(base)
    return _positive(snapshot.get("price")) if snapshot else None


def _gate_price(base: str) -> Optional[float]:
    try:
        pair = urllib.parse.quote(f"{base}_USDT", safe="")
        j = _json(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={pair}", 6)
        if isinstance(j, list) and j:
            return _positive(j[0].get("last"))
    except Exception:
        return None
    return None


def get_ticker(symbol: str) -> Dict[str, Any]:
    # Commodity/metals/oil adapters are routed before crypto pair normalization.
    try:
        from apex import instrument_catalog as catalog
        from apex import commodity_market as commodities
        canonical = catalog.normalize_instrument_symbol(symbol)
        if catalog.is_reference_symbol(canonical):
            return commodities.get_reference_ticker(canonical)
        if catalog.is_instrument(canonical):
            return commodities.get_ticker(canonical)
    except Exception as exc:
        canonical = (symbol or "").strip().upper().replace("-", "")
        try:
            known_instrument = catalog.is_instrument(canonical)
            known_reference = catalog.is_reference_symbol(canonical)
        except Exception:
            known_instrument = canonical in {"XAU", "XAG", "IRG18", "IRS999", "IRS925", "WTI", "BRENT", "XAUF", "XAGF", "WTIF", "BRTF"}
            known_reference = canonical == "USDTIRT"
        if known_reference or known_instrument:
            return {"base": canonical, "symbol": f"{canonical}-USDT", "price": None,
                    "source": None, "ts": int(time.time()), "stale": True,
                    "status": "unavailable", "trade_demo": False,
                    "error": f"دادهٔ بازار در دسترس نیست: {str(exc)[:120]}"}
    base, kucoin_pair, binance_pair = normalize_symbol(symbol)

    def _do():
        now = int(time.time())
        out: Dict[str, Any] = {"base": base, "symbol": kucoin_pair, "binance": binance_pair,
                               "price": None, "change_pct": None, "high": None, "low": None,
                               "volume": None, "bid": None, "ask": None, "source": None,
                               "ts": now, "stale": False}
        providers = (("binance", _binance_24h), ("kucoin", _kucoin_stats))
        for source, fn in providers:
            data = fn(base)
            if data and _positive(data.get("price")):
                out.update(data)
                out["source"] = source
                out["ts"] = int(time.time())
                return out
        fallbacks = (
            ("bybit", _bybit_snapshot), ("okx", _okx_snapshot),
            ("kucoin", _kucoin_level1_snapshot),
            ("gate", _gate_price), ("binance", _binance_price),
            ("kucoin", _kucoin_price),
        )
        for source, fn in fallbacks:
            snapshot = fn(base)
            if isinstance(snapshot, dict) and _positive(snapshot.get("price")):
                out.update(snapshot)
                out["source"] = source
                out["ts"] = int(time.time())
                return out
            price = _positive(snapshot)
            if price:
                out.update({"price": price, "source": source, "ts": int(time.time())})
                return out
        out["error"] = "هیچ منبعی قیمت تازه و معتبر برنگرداند"
        return out

    raw = _cached(f"ticker:{base}", TTL_PX, _do) or {}
    # Recompute freshness on every read, including cache hits.
    result = dict(raw)
    stamp = _finite_number(result.get("ts"), 0.0) or 0.0
    age = time.time() - stamp if stamp > 0 else None
    valid_price = _positive(result.get("price")) is not None
    valid_source = bool(result.get("source"))
    retrieval_fresh = (valid_price and valid_source and age is not None
                       and -10.0 <= age <= MAX_TICKER_AGE_SECONDS and not result.get("error"))
    provider_ts = _finite_number(result.get("provider_ts"), None)
    provider_age = time.time() - provider_ts if provider_ts else None
    provider_fresh = (provider_age is not None
                      and -10.0 <= provider_age <= MAX_TICKER_AGE_SECONDS * 2.0)
    if not valid_price or not valid_source:
        status = "unavailable"
    elif not retrieval_fresh:
        status = "stale"
    elif provider_ts is None:
        status = "unverified"
    elif not provider_fresh:
        status = "stale"
    else:
        status = "fresh"
    result.update({
        # ts/observed_at are local retrieval timestamps. They are not claimed to
        # be the provider's market timestamp; both clocks are kept separately.
        "ts": int(stamp) if stamp > 0 else None,
        "retrieved_at": int(stamp) if stamp > 0 else None,
        "observed_at": int(stamp) if stamp > 0 else None,
        "age_seconds": round(max(0.0, age), 2) if age is not None else None,
        "provider_ts": provider_ts,
        "provider_age_seconds": round(max(0.0, provider_age), 2) if provider_age is not None else None,
        "timestamp_quality": result.get("timestamp_quality") or ("provider_snapshot" if provider_ts else "retrieval_only"),
        "freshness_basis": "provider_snapshot" if status == "fresh" else "retrieval_only_or_unverified",
        "retrieval_fresh": bool(retrieval_fresh),
        "quote_valid": bool(status == "fresh"),
        "stale": status in ("stale", "unavailable"),
        "status": status,
    })
    return result



POPULAR_CRYPTO_ORDER = ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "TON", "TRX",
                        "LINK", "AVAX", "DOT", "LTC", "BCH", "UNI", "ATOM", "NEAR", "SUI"]
POPULAR_CRYPTO_RANK = {symbol: idx for idx, symbol in enumerate(POPULAR_CRYPTO_ORDER)}

CRYPTO_NAME_FA = {
    "BTC": "بیت‌کوین", "ETH": "اتریوم", "SOL": "سولانا", "XRP": "ریپل",
    "BNB": "بایننس کوین", "DOGE": "دوج‌کوین", "ADA": "کاردانو", "TON": "تون",
    "TRX": "ترون", "LINK": "چین‌لینک", "AVAX": "آوالانچ", "DOT": "پولکادات",
    "LTC": "لایت‌کوین", "BCH": "بیت‌کوین کش", "UNI": "یونی‌سواپ", "ATOM": "کازماس",
    "ETC": "اتریوم کلاسیک", "NEAR": "نیر", "APT": "اپتوس", "ARB": "آربیتروم",
    "OP": "آپتیمیسم", "SUI": "سوئی", "FIL": "فایل‌کوین", "AAVE": "آوه",
    "PEPE": "پپه", "SHIB": "شیبا", "ICP": "اینترنت کامپیوتر", "XLM": "استلار",
    "INJ": "اینجکتیو", "MATIC": "پالیگان", "POL": "پالیگان", "FTM": "فانتوم",
}

def _binance_spot_search_universe() -> list[dict]:
    """Cached exchange metadata used only to validate searchable USDT spot symbols."""
    payload = _cached("binance:exchange_info:spot", 3600,
                      lambda: _json("https://api.binance.com/api/v3/exchangeInfo?permissions=SPOT", 12))
    rows = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Binance exchangeInfo ساختار قابل‌قبولی ندارد")
    output = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("status") != "TRADING" or row.get("quoteAsset") != "USDT":
            continue
        if row.get("isSpotTradingAllowed") is False:
            continue
        base = str(row.get("baseAsset") or "").upper()
        symbol = str(row.get("symbol") or "").upper()
        if base and symbol == base + "USDT":
            output.append({"symbol": base, "provider_symbol": symbol,
                           "name_fa": CRYPTO_NAME_FA.get(base, base),
                           "category": "crypto", "market": "spot"})
    return output

def rank_search_candidates(query: str, rows: list[dict], limit: int = 8) -> list[dict]:
    """Pure deterministic ranking: exact, prefix, then contains; no fake matches."""
    q = str(query or "").strip().upper().replace("/", "").replace("-", "").replace(" ", "")
    if not q:
        return []
    def score(row):
        symbol = str(row.get("symbol") or "").upper()
        name = str(row.get("name_fa") or "").upper()
        if symbol == q:
            return (0, len(symbol), symbol)
        if symbol.startswith(q):
            return (1, POPULAR_CRYPTO_RANK.get(symbol, 999), len(symbol), symbol)
        if q in symbol or q in name:
            return (2, POPULAR_CRYPTO_RANK.get(symbol, 999), len(symbol), symbol)
        return (9, len(symbol), symbol)
    matches = [r for r in rows if score(r)[0] < 9]
    return sorted(matches, key=score)[:max(1, min(20, int(limit)))]

def search_symbol_suggestions(query: str, limit: int = 8) -> dict:
    """Return validated local instruments and real Binance USDT spot listings.

    Each candidate is accompanied by a quote when the corresponding provider can
    currently deliver one. A listed-but-unavailable asset stays distinguishable
    from a symbol that the source does not recognize.
    """
    q = str(query or "").strip()
    if len(q) < 1:
        return {"query": q, "items": [], "found": False, "message": "حداقل یک نویسه وارد کن"}
    candidates: list[dict] = []
    try:
        from apex import instrument_catalog as catalog
        for symbol, spec in catalog.INSTRUMENTS.items():
            candidates.append({"symbol": symbol, "name_fa": spec.get("name_fa") or symbol,
                                "category": spec.get("category"), "market": spec.get("market")})
        for symbol in catalog.REFERENCE_SYMBOLS:
            candidates.append({"symbol": symbol, "name_fa": "نرخ تتر به تومان", 
                                "category": "fx", "market": "reference"})
    except Exception:
        pass
    try:
        candidates.extend(_binance_spot_search_universe())
    except Exception:
        # Offline fallback is a discovery hint only; each result is still validated
        # against live market adapters below before it is shown as available.
        candidates.extend({"symbol": sym, "name_fa": name, "category": "crypto", "market": "spot"}
                          for sym, name in CRYPTO_NAME_FA.items())
    ranked = rank_search_candidates(q, candidates, limit=max(8, limit * 2))
    unique = []
    seen = set()
    for row in ranked:
        symbol = row["symbol"]
        if symbol not in seen:
            unique.append(row); seen.add(symbol)
    unique = unique[:max(1, min(12, limit))]
    if not unique:
        return {"query": q, "items": [], "found": False,
                "message": f"نماد «{q}» در فهرست ابزارهای پشتیبانی‌شده پیدا نشد"}

    from concurrent.futures import ThreadPoolExecutor, as_completed
    found = []
    with ThreadPoolExecutor(max_workers=min(6, len(unique))) as pool:
        jobs = {pool.submit(get_ticker, row["symbol"]): row for row in unique}
        for future in as_completed(jobs):
            row = jobs[future]
            try:
                ticker = future.result()
                native_quote = _positive(ticker.get("display_price")) if isinstance(ticker, dict) else None
                ledger_quote = _positive(ticker.get("price")) if isinstance(ticker, dict) else None
                valid = bool(ticker and (native_quote or ledger_quote) and not ticker.get("error"))
                found.append({**row, "ticker": ticker if valid else None,
                              "available": valid,
                              "status": (ticker or {}).get("status") if isinstance(ticker, dict) else "unavailable",
                              "price": (ticker or {}).get("price") if valid else None,
                              "paper_tradable": bool((ticker or {}).get("trade_demo")) if isinstance(ticker, dict) else False,
                              "display_price": (ticker or {}).get("display_price") if valid else None,
                              "display_currency": (ticker or {}).get("display_currency") if valid else None,
                              "display_unit": (ticker or {}).get("display_unit") if valid else None,
                              "source": (ticker or {}).get("source") if isinstance(ticker, dict) else None})
            except Exception as exc:
                found.append({**row, "ticker": None, "available": False,
                              "status": "unavailable", "price": None, "error": str(exc)[:120]})
    def _result_rank(row):
        symbol = str(row.get("symbol") or "").upper()
        if symbol == q.upper():
            prefix_rank = 0
        elif symbol.startswith(q.upper()):
            prefix_rank = 1
        elif q.upper() in symbol or q.upper() in str(row.get("name_fa") or "").upper():
            prefix_rank = 2
        else:
            prefix_rank = 9
        ticker = row.get("ticker") or {}
        volume = _finite_number(ticker.get("volume"), 0.0) or 0.0
        pop = POPULAR_CRYPTO_RANK.get(symbol, 999)
        return (prefix_rank, pop, -volume, symbol)
    found.sort(key=_result_rank)
    any_available = any(row["available"] for row in found)
    return {"query": q, "items": found, "found": True,
            "has_live_quotes": any_available,
            "message": "پیشنهادهای نماد" if any_available else "نماد شناسایی شد، اما قیمت معتبر فعلاً از منابع دریافت نشد"}

def get_live_price(symbol: str) -> Optional[float]:
    """Fresh crypto price for automatic paper scanners; commodities are manual-only.

    Delayed daily/indicative commodity quotes must never enter the auto-scanner.
    """
    try:
        from apex import instrument_catalog as catalog
        canonical = catalog.normalize_instrument_symbol(symbol)
        if catalog.is_instrument(canonical) or catalog.is_reference_symbol(canonical):
            return None
    except Exception:
        pass
    result = get_ticker(symbol)
    if not result or result.get("stale") or result.get("status") != "fresh":
        return None
    return _positive(result.get("price"))


def get_prices_batch(symbols: List[str]) -> Dict[str, float]:
    """Build local portfolio marks. Commodity quotes may be delayed, but are tagged upstream.

    Automatic scanners use get_live_price(), which intentionally rejects all commodities.
    """
    normalized = []
    try:
        from apex import instrument_catalog as catalog
    except Exception:
        catalog = None
    for symbol in symbols:
        try:
            if catalog is not None:
                canonical = catalog.normalize_instrument_symbol(symbol)
                # FX/reference marks (for example USDT/IRT) are never base assets
                # in either paper ledger and must not be routed into crypto APIs.
                if catalog.is_reference_symbol(canonical):
                    continue
                if catalog.is_instrument(canonical):
                    base = canonical
                else:
                    base, _, _ = normalize_symbol(symbol)
            else:
                base, _, _ = normalize_symbol(symbol)
        except ValueError:
            continue
        if base not in normalized:
            normalized.append(base)
    result: Dict[str, float] = {}
    if not normalized:
        return result
    def _mark(base):
        ticker = get_ticker(base)
        price = _positive(ticker.get("price"))
        if not price or ticker.get("status") != "fresh" or not ticker.get("quote_valid"):
            return None
        return price
    with ThreadPoolExecutor(max_workers=min(6, len(normalized))) as pool:
        jobs = {pool.submit(_mark, base): base for base in normalized[:30]}
        for future in as_completed(jobs):
            base = jobs[future]
            try:
                price = future.result()
                if price:
                    result[base] = price
                    result[f"{base}-USDT"] = price
                    result[f"{base}USDT"] = price
            except Exception:
                continue
    return result


KC_TF = {"1min": "1min", "3min": "3min", "5min": "5min", "15min": "15min",
         "30min": "30min", "1hour": "1hour", "2hour": "2hour", "4hour": "4hour",
         "1day": "1day", "1week": "1week", "1m": "1min", "3m": "3min",
         "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1hour",
         "2h": "2hour", "4h": "4hour", "1d": "1day", "1w": "1week"}


def fetch_candles(symbol: str, tf: str = "1hour", start: int = 0, end: int = 0,
                  provider: Optional[str] = None) -> List[Dict[str, float]]:
    """Read validated OHLCV, preferring the ticker's provider when requested.

    Every row carries ``source`` metadata so the market-data hub can reject
    mixed-provider decisions while legacy analysis callers can ignore it.
    """
    try:
        from apex import instrument_catalog as catalog
        if catalog.is_instrument(symbol):
            from apex import commodity_market as commodities
            rows = commodities.fetch_candles(symbol, tf)
            return [dict(row, source=row.get("source") or "commodity_adapter") for row in rows or []]
    except Exception:
        try:
            if catalog.is_instrument(symbol):
                return []
        except Exception:
            pass
    base, pair, binance_pair = normalize_symbol(symbol)
    requested_tf = str(tf or "").lower().strip()
    if requested_tf not in KC_TF:
        supported = ", ".join(sorted(KC_TF))
        raise ValueError(f"تایم‌فریم پشتیبانی نمی‌شود؛ مقادیر مجاز: {supported}")
    interval = KC_TF[requested_tf]
    end = int(end or time.time())
    seconds = {"1min": 60, "3min": 180, "5min": 300, "15min": 900, "30min": 1800,
               "1hour": 3600, "2hour": 7200, "4hour": 14400, "1day": 86400,
               "1week": 604800}[interval]
    start = int(start or (end - MAX_CANDLES * seconds))
    if start >= end:
        raise ValueError("بازه زمانی کندل نامعتبر است")
    preferred = str(provider or "").lower().strip()
    if preferred not in ("binance", "kucoin", "bybit", "okx"):
        preferred = ""
    key = f"candles:{base}:{interval}:{start}:{end // max(60, seconds)}:{preferred or 'auto'}"

    def _valid(rows, source):
        output = []
        for row in rows:
            try:
                t = int(row["time"])
                opn, close = float(row["open"]), float(row["close"])
                high, low = float(row["high"]), float(row["low"])
                volume = float(row.get("volume") or 0)
                nums = (opn, close, high, low, volume)
                if not all(math.isfinite(x) for x in nums) or min(opn, close, high, low) <= 0 or volume < 0:
                    continue
                if high < max(opn, close, low) or low > min(opn, close, high):
                    continue
                output.append({"time": t, "open": opn, "close": close, "high": high,
                               "low": low, "volume": max(0.0, volume), "source": source})
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                continue
        dedup = {row["time"]: row for row in sorted(output, key=lambda x: x["time"])}
        return list(dedup.values())[-MAX_CANDLES:]

    def _kucoin():
        query = urllib.parse.urlencode({"type": interval, "symbol": pair,
                                        "startAt": start, "endAt": end})
        j = _json(f"https://api.kucoin.com/api/v1/market/candles?{query}", 15)
        if j.get("code") not in (None, "200000"):
            return []
        rows = []
        for r in j.get("data") or []:
            if len(r) >= 6:
                rows.append({"time": int(r[0]), "open": r[1], "close": r[2],
                             "high": r[3], "low": r[4], "volume": r[5]})
        return _valid(rows, "kucoin")

    def _binance():
        bn_tf = {"1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m",
                 "30min": "30m", "1hour": "1h", "2hour": "2h", "4hour": "4h",
                 "1day": "1d", "1week": "1w"}[interval]
        query = urllib.parse.urlencode({"symbol": binance_pair, "interval": bn_tf,
                                        "startTime": start * 1000, "endTime": end * 1000,
                                        "limit": MAX_CANDLES})
        payload = _json(f"https://api.binance.com/api/v3/klines?{query}", 15)
        if not isinstance(payload, list):
            return []
        rows = []
        for row in payload:
            if len(row) >= 6:
                rows.append({"time": int(row[0]) // 1000, "open": row[1],
                             "high": row[2], "low": row[3], "close": row[4],
                             "volume": row[5]})
        return _valid(rows, "binance")

    def _bybit():
        interval_map = {"1min": "1", "3min": "3", "5min": "5", "15min": "15",
                        "30min": "30", "1hour": "60", "2hour": "120",
                        "4hour": "240", "1day": "D", "1week": "W"}
        query = urllib.parse.urlencode({
            "category": "spot", "symbol": binance_pair,
            "interval": interval_map[interval], "start": start * 1000,
            "end": end * 1000, "limit": MAX_CANDLES,
        })
        payload = _json(f"https://api.bybit.com/v5/market/kline?{query}", 15)
        if not isinstance(payload, dict) or int(payload.get("retCode", -1)) != 0:
            return []
        values = ((payload.get("result") or {}).get("list") or [])
        rows = []
        for row in values:
            if len(row) >= 6:
                rows.append({"time": int(row[0]) // 1000, "open": row[1],
                             "high": row[2], "low": row[3], "close": row[4],
                             "volume": row[5]})
        return _valid(rows, "bybit")

    def _okx():
        bar_map = {"1min": "1m", "3min": "3m", "5min": "5m", "15min": "15m",
                   "30min": "30m", "1hour": "1H", "2hour": "2H",
                   "4hour": "4H", "1day": "1D", "1week": "1W"}
        # OKX public candle endpoint returns the newest candles. Filter them to
        # the requested UTC window locally; the response is capped at 300 rows.
        query = urllib.parse.urlencode({"instId": pair, "bar": bar_map[interval], "limit": 300})
        payload = _json(f"https://www.okx.com/api/v5/market/candles?{query}", 15)
        if not isinstance(payload, dict) or str(payload.get("code", "-1")) != "0":
            return []
        rows = []
        for row in (payload.get("data") or []):
            if len(row) >= 6:
                rows.append({"time": int(row[0]) // 1000, "open": row[1],
                             "high": row[2], "low": row[3], "close": row[4],
                             "volume": row[5]})
        return _valid([r for r in rows if start <= int(r["time"]) <= end], "okx")

    fetchers = {"binance": _binance, "kucoin": _kucoin,
                "bybit": _bybit, "okx": _okx}

    def _do():
        if preferred:
            order = [preferred] + [name for name in ("binance", "kucoin", "bybit", "okx")
                                   if name != preferred]
        else:
            order = ["kucoin", "binance", "bybit", "okx"]
        for name in order:
            try:
                rows = fetchers[name]()
                if rows:
                    return rows
            except Exception:
                continue
        return []

    return _cached(key, TTL_SLOW, _do) or []


def fetch_orderbook(symbol: str, depth: int = 20) -> Dict[str, Any]:
    """Best-effort public order book with a second public data source."""
    base, pair, binance_pair = normalize_symbol(symbol)
    depth = 100 if depth == 100 else 20

    def _clean(bids, asks, source):
        clean_bids, clean_asks = [], []
        for dest, rows in ((clean_bids, bids or []), (clean_asks, asks or [])):
            for row in rows[:depth]:
                try:
                    px, qty = float(row[0]), float(row[1])
                    if px > 0 and qty > 0 and math.isfinite(px * qty):
                        dest.append([px, qty])
                except Exception:
                    continue
        clean_bids.sort(key=lambda x: x[0], reverse=True)
        clean_asks.sort(key=lambda x: x[0])
        if not clean_bids or not clean_asks:
            return None
        return {"bids": clean_bids, "asks": clean_asks,
                "ts": int(time.time()), "source": source}

    def _do():
        try:
            j = _json(f"https://api.kucoin.com/api/v1/market/orderbook/level2_20?symbol={pair}", 8)
            if j.get("code") in (None, "200000"):
                d = j.get("data") or {}
                result = _clean(d.get("bids"), d.get("asks"), "kucoin")
                if result:
                    return result
        except Exception:
            pass
        try:
            j = _json(f"https://api.binance.com/api/v3/depth?symbol={binance_pair}&limit={depth}", 8)
            return _clean(j.get("bids"), j.get("asks"), "binance")
        except Exception:
            return None

    return _cached(f"orderbook:{base}:{depth}", TTL_PX, _do) or {
        "bids": [], "asks": [], "ts": None, "source": None}

def funding_rate(base: str) -> Optional[float]:
    base, _, _ = normalize_symbol(base)
    contract = "XBTUSDTM" if base == "BTC" else f"{base}USDTM"

    def _do():
        try:
            j = _json(f"https://api-futures.kucoin.com/api/v1/funding-rate/{contract}/current", 8)
            if j.get("code") not in (None, "200000"):
                return None
            d = j.get("data") or {}
            value = d.get("value")
            if value is None:
                value = d.get("fundingRate")
            return float(value) if value is not None and math.isfinite(float(value)) else None
        except Exception:
            return None

    return _cached(f"funding:{base}", TTL_SLOW, _do)


def open_interest(base: str) -> Optional[float]:
    base, _, _ = normalize_symbol(base)
    contract = "XBTUSDTM" if base == "BTC" else f"{base}USDTM"

    def _do():
        try:
            j = _json(f"https://api-futures.kucoin.com/api/v1/contracts/{contract}", 8)
            if j.get("code") not in (None, "200000"):
                return None
            d = j.get("data") or {}
            value = d.get("openInterest") or d.get("openInterestQty")
            n = float(value) if value is not None else 0.0
            return n if n > 0 and math.isfinite(n) else None
        except Exception:
            return None

    return _cached(f"open_interest:{base}", TTL_SLOW, _do)


def health_check() -> List[Dict[str, Any]]:
    tests = [
        ("Binance", "https://api.binance.com/api/v3/ping"),
        ("KuCoin", "https://api.kucoin.com/api/v1/status"),
        ("Bybit", "https://api.bybit.com/v5/market/time"),
        ("OKX", "https://www.okx.com/api/v5/public/time"),
        ("Gate", "https://api.gateio.ws/api/v4/spot/currency_pairs?limit=1"),
        ("Gold-API", "https://api.gold-api.com/symbols"),
        ("Wallex public", "https://api.wallex.ir/v1/markets"),
    ]
    out = []
    for name, url in tests:
        start = time.monotonic()
        ok, err = False, ""
        try:
            payload = _json(url, 6)
            if isinstance(payload, dict):
                if "retCode" in payload and int(payload.get("retCode", -1)) != 0:
                    raise ValueError(str(payload.get("retMsg") or "خطای API"))
                if "code" in payload and str(payload.get("code")) not in ("0", "200000"):
                    raise ValueError(str(payload.get("msg") or "خطای API"))
            ok = True
        except Exception as exc:
            err = str(exc)[:100]
        out.append({"name": name, "ok": ok, "ms": int((time.monotonic() - start) * 1000), "error": err})
    return out


def rich_snapshot(symbol: str) -> Dict[str, Any]:
    try:
        from apex import instrument_catalog as catalog
        canonical = catalog.normalize_instrument_symbol(symbol)
        if catalog.is_instrument(canonical) or catalog.is_reference_symbol(canonical):
            ticker = get_ticker(canonical)
            return {**ticker, "funding": None, "open_interest": None,
                    "spread_pct": None, "depth_imbalance_pct": None,
                    "bid_depth_usdt": None, "ask_depth_usdt": None,
                    "orderbook_ts": None, "orderbook_source": None,
                    "snapshot_kind": "commodity_reference"}
    except Exception:
        pass
    base, pair, binance_pair = normalize_symbol(symbol)
    ticker = get_ticker(base)
    # These independent read-only requests can run in parallel to avoid blocking the AI screen.
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_book = pool.submit(fetch_orderbook, base)
        f_funding = pool.submit(funding_rate, base)
        f_oi = pool.submit(open_interest, base)
        book = f_book.result()
        funding = f_funding.result()
        oi = f_oi.result()
    bids, asks = book.get("bids") or [], book.get("asks") or []
    spread = depth_imbalance = None
    bid_depth = ask_depth = None
    try:
        if bids and asks and float(bids[0][0]) > 0:
            candidate = (float(asks[0][0]) - float(bids[0][0])) / float(bids[0][0]) * 100.0
            spread = candidate if math.isfinite(candidate) and candidate >= 0 else None
        bid_depth = sum(float(px) * float(qty) for px, qty in bids[:20])
        ask_depth = sum(float(px) * float(qty) for px, qty in asks[:20])
        total_depth = bid_depth + ask_depth
        if total_depth > 0 and math.isfinite(total_depth):
            depth_imbalance = (bid_depth - ask_depth) / total_depth * 100.0
    except (TypeError, ValueError, OverflowError):
        pass
    return {**ticker, "symbol": pair, "binance": binance_pair,
            "funding": funding, "open_interest": oi, "spread_pct": spread,
            "depth_imbalance_pct": depth_imbalance,
            "bid_depth_usdt": bid_depth if bids and asks else None,
            "ask_depth_usdt": ask_depth if bids and asks else None,
            "bid": float(bids[0][0]) if bids else ticker.get("bid"),
            "ask": float(asks[0][0]) if asks else ticker.get("ask"),
            "orderbook_ts": book.get("ts"), "orderbook_source": book.get("source")}


POPULAR = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "DOT", "MATIC",
           "LTC", "BCH", "ATOM", "NEAR", "APT", "SUI", "ARB", "OP", "INJ", "TIA",
           "PEPE", "WIF", "BNB", "TON", "TRX", "SHIB", "FIL", "UNI", "AAVE", "FET"]
