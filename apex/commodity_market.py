# -*- coding: utf-8 -*-
"""Read-only multi-asset commodity feeds for KALVO.

This module supplies market quotations only. All orders and positions remain inside
KALVO's local paper-trading ledger; no exchange order endpoint is implemented here.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
import ssl
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import certifi
except Exception:
    certifi = None

from apex import instrument_catalog as catalog
from apex.config import get_env

TROY_OUNCE_GRAMS = 31.1034768
DEFAULT_UA = "KALVO/7.0 (read-only market data)"
TABLOO_LATEST = "https://raw.githubusercontent.com/hidevs/tabloo/main/data/latest-toman.json"
TABLOO_POPULAR = "https://raw.githubusercontent.com/hidevs/tabloo/main/data/popular.json"
WALLEX_MARKETS = "https://api.wallex.ir/v1/markets"
GOLD_API = "https://api.gold-api.com/price/"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
FRED_OBS = "https://api.stlouisfed.org/fred/series/observations"

_lock = threading.RLock()
_cache: Dict[str, tuple[float, Any]] = {}


def _positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def _finite(value: Any, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _get_json(url: str, timeout: int = 10) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA, "Accept": "application/json"})
    context = (ssl.create_default_context(cafile=certifi.where()) if certifi
               else ssl.create_default_context())
    with urllib.request.urlopen(req, timeout=max(2, min(25, int(timeout))), context=context) as response:
        raw = response.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise ValueError("پاسخ منبع بازار بیش از حد بزرگ است")
        payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, (dict, list)):
        raise ValueError("ساختار پاسخ منبع بازار نامعتبر است")
    return payload


def _cached(key: str, ttl: int, fn):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    if value is not None and value != {} and value != []:
        with _lock:
            _cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _parse_time(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000.0
            return int(number)
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return None


def _status(observed_at: Optional[int], max_age: int, provider_stale: bool = False):
    if provider_stale:
        return "stale", True, None
    now = int(time.time())
    age = max(0, now - observed_at) if observed_at else None
    if age is None:
        return "unverified", True, None
    if age <= max_age:
        return "fresh", False, age
    return "delayed", age > max_age * 6, age


def _walk_records(node: Any):
    if isinstance(node, dict):
        if "symbol" in node and ("price" in node or "last" in node):
            yield node
        for value in node.values():
            yield from _walk_records(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_records(value)


def _tabloo_snapshot() -> dict:
    def _load():
        errors = []
        for url in (TABLOO_LATEST, TABLOO_POPULAR):
            try:
                payload = _get_json(url, 12)
                if isinstance(payload, dict) and payload:
                    return payload
            except Exception as exc:
                errors.append(str(exc)[:100])
        raise RuntimeError("Tabloo unavailable: " + "; ".join(errors))
    return _cached("tabloo:latest", 600, _load)


def _tabloo_record(*symbols: str) -> Optional[dict]:
    try:
        payload = _tabloo_snapshot()
        wanted = {str(x).upper() for x in symbols if x}
        for record in _walk_records(payload):
            if str(record.get("symbol", "")).upper() in wanted:
                return dict(record)
    except Exception:
        return None
    return None


def _record_price_toman(record: dict, usd_toman: Optional[float] = None) -> Optional[float]:
    price = _positive(record.get("price"))
    if price is None:
        return None
    currency = str(record.get("currency") or "IRT").upper()
    if currency in ("IRT", "TOMAN", "TOMANS"):
        return price
    if currency in ("IRR", "RIAL", "RIALS"):
        return price / 10.0
    if currency in ("USD", "USDT") and usd_toman:
        return price * usd_toman
    return None


def _record_freshness(record: dict, max_age: int = 2 * 3600):
    stamp = _parse_time(record.get("timestamp") or record.get("timestamp_tehran"))
    stale_flag = bool(record.get("stale", False))
    return _status(stamp, max_age, stale_flag), stamp


def _fetch_gold_api(symbol: str) -> dict:
    payload = _get_json(GOLD_API + urllib.parse.quote(symbol), 9)
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ValueError("Gold API response is not an object")
    price = _positive(payload.get("price") or payload.get("value") or payload.get("last"))
    if price is None:
        raise ValueError("Gold API price missing")
    stamp = _parse_time(payload.get("updatedAt") or payload.get("updated_at") or payload.get("timestamp"))
    status, stale, age = _status(stamp, 180)
    # The provider publishes spot price per troy ounce and says prices refresh every 30 seconds.
    return {"price": price, "observed_at": stamp, "source": "Gold-API", "status": status,
            "stale": stale, "age_seconds": age, "quote_currency": "USD", "unit": "troy_oz",
            "provider_name": payload.get("name") or symbol, "timestamp_quality": "provider" if stamp else "missing"}


def _fetch_global_metal(symbol: str) -> dict:
    """Gold-API primary; a timestamped Tabloo/TGJU ounce quote is the fallback."""
    try:
        return _fetch_gold_api(symbol)
    except Exception as primary_error:
        tabloo_symbols = {"XAU": ("XAU_USD", "GOLD_OUNCE_USD"),
                          "XAG": ("SILVER", "SILVER_USD", "XAG_USD")}
        record = _tabloo_record(*tabloo_symbols.get(symbol, ()))
        if not record:
            raise RuntimeError(f"Gold-API unavailable and no Tabloo fallback for {symbol}: {str(primary_error)[:100]}")
        currency = str(record.get("currency") or "").upper()
        unit = str(record.get("unit") or "").lower()
        price = _positive(record.get("price"))
        if currency != "USD" or unit not in ("troy_ounce", "troy oz", "ounce", "oz") or price is None:
            raise RuntimeError(f"Tabloo fallback for {symbol} has incompatible currency/unit")
        # Snapshot data can be several hours behind while still provider-marked
        # valid; label it delayed and only reject it after the 12-hour stale window.
        state, stamp = _record_freshness(record, 2 * 3600)
        return {"price": price, "observed_at": stamp, "source": "Tabloo/TGJU global metal",
                "status": state[0], "stale": state[1], "age_seconds": state[2],
                "quote_currency": "USD", "unit": "troy_oz",
                "timestamp_quality": "provider", "provider_symbol": record.get("symbol")}


def _fetch_yahoo(symbol: str, interval: str = "1d", range_value: str = "5d") -> dict:
    query = urllib.parse.urlencode({"range": range_value, "interval": interval, "events": "history"})
    payload = _get_json(YAHOO_CHART + urllib.parse.quote(symbol, safe="=^.-") + "?" + query, 12)
    chart = (payload.get("chart") or {})
    if chart.get("error"):
        raise ValueError(str(chart["error"])[:160])
    rows = chart.get("result") or []
    if not rows:
        raise ValueError("Yahoo chart has no result")
    row = rows[0]
    meta = row.get("meta") or {}
    price = _positive(meta.get("regularMarketPrice"))
    timestamps = row.get("timestamp") or []
    quote = (((row.get("indicators") or {}).get("quote") or [{}])[0])
    closes = quote.get("close") or []
    if price is None:
        for candidate in reversed(closes):
            price = _positive(candidate)
            if price:
                break
    if price is None:
        raise ValueError("Yahoo chart has no valid price")
    stamp = _parse_time(meta.get("regularMarketTime"))
    if stamp is None and timestamps:
        stamp = _parse_time(timestamps[-1])
    status, stale, age = _status(stamp, 24 * 3600)
    previous = _positive(meta.get("chartPreviousClose") or meta.get("previousClose"))
    change = ((price / previous) - 1.0) * 100 if previous else None
    return {"price": price, "observed_at": stamp, "source": "Yahoo Finance chart",
            "status": "delayed" if status == "fresh" else status, "stale": stale,
            "age_seconds": age, "quote_currency": meta.get("currency") or "USD",
            "change_pct": change, "provider_symbol": symbol,
            "timestamp_quality": "provider" if stamp else "missing"}


def _fetch_wallex_usdt_toman() -> dict:
    payload = _get_json(WALLEX_MARKETS, 10)
    symbols = (((payload.get("result") or {}).get("symbols")) or {})
    market = symbols.get("USDTTMN") or symbols.get("USDTIRT")
    if not isinstance(market, dict):
        raise ValueError("Wallex USDTTMN market missing")
    stats = market.get("stats") or {}
    bid, ask = _positive(stats.get("bidPrice")), _positive(stats.get("askPrice"))
    last = _positive(stats.get("lastPrice"))
    price = ((bid + ask) / 2.0) if bid and ask else last
    if price is None:
        raise ValueError("Wallex USDT price missing")
    # Public REST ticker does not provide a market event timestamp in this payload.
    return {"price": price, "bid": bid, "ask": ask, "last": last,
            "observed_at": int(time.time()), "source": "Wallex public market",
            "status": "unverified", "stale": False, "age_seconds": 0,
            "quote_currency": "IRT", "unit": "USDT/IRT", "timestamp_quality": "polled_at"}


def _fetch_fred(series_id: str) -> dict:
    key = get_env("FRED_API_KEY")
    if not key:
        # Android builds intentionally exclude .env; allow a user-supplied key in
        # the app's private local preferences as a runtime alternative.
        try:
            from apex import storage
            key = (storage.get_setting("fred_api_key", "") or "").strip()
        except Exception:
            key = ""
    if not key:
        raise RuntimeError("FRED_API_KEY تنظیم نشده؛ آن را در .env یا تنظیمات منابع داده وارد کن")
    query = urllib.parse.urlencode({"series_id": series_id, "api_key": key,
                                    "file_type": "json", "sort_order": "desc", "limit": 1})
    payload = _get_json(FRED_OBS + "?" + query, 12)
    rows = (payload.get("observations") or [])
    row = next((r for r in rows if _positive(r.get("value")) is not None), None)
    if not row:
        raise ValueError(f"FRED has no valid observation for {series_id}")
    price = _positive(row.get("value"))
    stamp = _parse_time(str(row.get("date")) + "T00:00:00+00:00")
    status, stale, age = _status(stamp, 3 * 86400)
    return {"price": price, "observed_at": stamp, "source": f"FRED/{series_id}",
            "status": "delayed" if not stale else "stale", "stale": stale,
            "age_seconds": age, "quote_currency": "USD", "unit": "barrel",
            "change_pct": None, "timestamp_quality": "daily_close", "daily_data": True}


def _usd_toman() -> dict:
    record = _tabloo_record("USD_IRR_FREE")
    if record:
        state, stamp = _record_freshness(record, 2 * 3600)
        price = _record_price_toman(record)
        if price and not state[1]:
            return {"price": price, "source": "Tabloo/TGJU USD_IRR_FREE",
                    "status": "delayed", "stale": False, "observed_at": stamp,
                    "age_seconds": state[2], "currency": "IRT"}
    wallex = _fetch_wallex_usdt_toman()
    return {"price": wallex["price"], "source": wallex["source"] + " · USD≈USDT proxy",
            "status": wallex["status"], "stale": wallex["stale"],
            "observed_at": wallex["observed_at"], "age_seconds": wallex["age_seconds"],
            "currency": "IRT", "parity_proxy": True}


def _usdt_toman() -> dict:
    """Cached USDT/Toman bridge shared by all local-quote instruments.

    A watchlist refresh can request dozens of FX instruments at once. One short
    shared cache prevents a burst of redundant calls to Wallex/TGJU.
    """
    def _load():
        # Prefer the direct USDT/IRT market. Do not silently equate USD and USDT.
        try:
            return _fetch_wallex_usdt_toman()
        except Exception:
            record = _tabloo_record("USDT_IRR", "USDT_TMN", "USDT_IRT")
            if record:
                state, stamp = _record_freshness(record, 2 * 3600)
                price = _record_price_toman(record)
                if price and not state[1]:
                    return {"price": price, "source": "Tabloo/TGJU USDT_IRR",
                            "status": "delayed", "stale": False, "observed_at": stamp,
                            "age_seconds": state[2], "currency": "IRT"}
            # Last-resort parity proxy. The metadata identifies this approximation.
            fallback = _usd_toman()
            return {**fallback, "source": str(fallback.get("source")) + " · USD≈USDT proxy",
                    "currency": "IRT", "parity_proxy": True}
    return dict(_cached("bridge:usdt_toman", 10, _load) or {})


def _iran_metal_toman(symbol: str, spec: dict) -> dict:
    # Direct domestic feed takes precedence only when the publisher marks it non-stale.
    aliases = {
        "IRG18": ("GOLD_18K_IRR",),
        "IRS999": ("SILVER_999_IRR", "SILVER_999_TOMAN"),
        "IRS925": ("SILVER_925_IRR", "SILVER_925_TOMAN"),
    }
    record = _tabloo_record(*aliases.get(symbol, ()))
    if record:
        state, stamp = _record_freshness(record, 2 * 3600)
        price = _record_price_toman(record)
        if price and not state[1]:
            return {"price_toman": price, "source": "Tabloo/TGJU direct", "status": state[0],
                    "stale": False, "observed_at": stamp, "age_seconds": state[2],
                    "pricing_method": "direct_domestic"}

    usd_toman = _usd_toman()
    if usd_toman.get("stale") or not _positive(usd_toman.get("price")):
        raise RuntimeError("نرخ دلار/تومان معتبر برای محاسبهٔ قیمت داخلی پیدا نشد")
    if symbol == "IRG18":
        world = _fetch_global_metal("XAU")
        purity = 18.0 / 24.0
        formula = "XAU_USD_per_troy_oz * USD_TOMAN / 31.1034768 * 18/24"
    else:
        world = _fetch_global_metal("XAG")
        purity = float(spec.get("purity") or 0.999)
        formula = f"XAG_USD_per_troy_oz * USD_TOMAN / 31.1034768 * {purity}"
    if world.get("stale") or not _positive(world.get("price")):
        raise RuntimeError("قیمت جهانی فلز برای محاسبهٔ داخلی تازه و معتبر نیست")
    adjustment_bps = _finite(get_env("IRAN_METAL_ADJUSTMENT_BPS", "20"), 20.0)
    if adjustment_bps is None:
        adjustment_bps = 20.0
    price_toman = calculate_iran_metal_toman(world["price"], usd_toman["price"], purity, adjustment_bps)
    return {"price_toman": price_toman, "source": "KALVO calculated · Gold-API + USD/IRT",
            "status": "estimated", "stale": False,
            "observed_at": min(world.get("observed_at") or int(time.time()), usd_toman.get("observed_at") or int(time.time())),
            "age_seconds": max(world.get("age_seconds") or 0, usd_toman.get("age_seconds") or 0),
            "pricing_method": "calculated_estimate", "formula": formula,
            "purity": purity, "fx_source": usd_toman.get("source"),
            "fx_parity_proxy": bool(usd_toman.get("parity_proxy")),
            "fx_status": usd_toman.get("status"),
            "fx_timestamp_quality": "polled_at" if usd_toman.get("source", "").startswith("Wallex") else "provider",
            "fx_observed_at": usd_toman.get("observed_at"),
            "adjustment_bps": adjustment_bps,
            "adjustment_label": "افزایش تخمینی قابل تنظیم برای اختلاف/هزینهٔ پایهٔ بازار",
            "retail_costs_included": False}


def _make_ticker(symbol: str, raw: dict, spec: dict) -> dict:
    now = int(time.time())
    base = symbol
    # Quote `price` is in USDT per instrument unit for compatibility with the
    # existing virtual USDT ledgers. `display_price` preserves the native quote.
    native_price = _positive(raw.get("price"))
    display_price = native_price
    display_currency = raw.get("quote_currency") or spec.get("quote_currency", "USD")
    local_quote = symbol in ("IRG18", "IRS925", "IRS999") or spec.get("category") == "fx"
    paper_conversion_available = True
    if local_quote:
        # The native market quote is always kept in Toman. A separate USDT
        # reference is required before the paper ledger can trade that quote.
        display_price = native_price
        display_currency = "TOMAN"
        try:
            conversion = _usdt_toman()
        except Exception as exc:
            # Native quote remains displayable even when the USDT ledger bridge
            # is temporarily unavailable. Never fabricate a conversion.
            conversion = {"price": None, "stale": True, "source": "unavailable",
                          "error": str(exc)[:120]}
        fx = _positive(conversion.get("price")) if isinstance(conversion, dict) else None
        if not fx or conversion.get("stale"):
            price = None
            paper_conversion_available = False
        else:
            price = display_price / fx
        native_unit = spec.get("display_unit") or ("تومان/گرم" if symbol.startswith("IR") else "تومان/واحد ارز")
        raw = {**raw, "fx_source": conversion.get("source") if isinstance(conversion, dict) else None,
               "usdt_toman": fx,
               "paper_conversion": "native Toman quote / USDT-Toman reference" if fx else None}
    else:
        price = native_price
        native_unit = spec.get("display_unit") or spec.get("unit") or "unit"
    if not _positive(native_price):
        raise ValueError("قیمت ابزار نامعتبر است")
    if price is not None and not _positive(price):
        raise ValueError("قیمت معادل دفترکل نامعتبر است")
    status = str(raw.get("status") or "unverified").lower()
    stale = bool(raw.get("stale", False))
    observed_at = _parse_time(raw.get("observed_at"))
    timestamp_quality = str(raw.get("timestamp_quality") or "missing").lower()
    # Only forward a true source timestamp. Local polling time is never promoted
    # to provider time; e.g. Wallex's server response is labelled polled_at.
    provider_ts = observed_at if timestamp_quality in (
        "provider", "combined_provider", "provider_snapshot", "daily_reference", "daily_close"
    ) else None
    return {
        "base": base, "symbol": f"{base}-USDT", "price": float(price) if price is not None else None,
        "change_pct": _finite(raw.get("change_pct")), "high": _finite(raw.get("high")),
        "low": _finite(raw.get("low")), "volume": _finite(raw.get("volume")),
        "bid": _finite(raw.get("bid")), "ask": _finite(raw.get("ask")),
        "source": raw.get("source"), "ts": now, "observed_at": observed_at,
        "retrieved_at": now, "provider_ts": provider_ts,
        "source_timestamp": provider_ts, "age_seconds": raw.get("age_seconds"),
        "stale": stale, "status": status, "market": spec.get("market"),
        "category": spec.get("category"), "name_fa": spec.get("name_fa"),
        "unit": spec.get("unit"), "display_unit": native_unit,
        "quote_currency": spec.get("quote_currency", "USD"),
        "paper_quote_currency": "USDT (USD reference parity for global commodities)",
        "display_price": float(display_price), "display_currency": display_currency,
        "native_price": float(display_price), "native_currency": display_currency,
        "trade_demo": bool(spec.get("trade_demo", False)) and not stale and paper_conversion_available and price is not None,
        "paper_conversion_available": paper_conversion_available,
        "pricing_method": raw.get("pricing_method") or "provider_quote",
        "formula": raw.get("formula"), "fx_source": raw.get("fx_source"),
        "fx_parity_proxy": bool(raw.get("fx_parity_proxy", False)),
        "fx_status": raw.get("fx_status"), "fx_timestamp_quality": raw.get("fx_timestamp_quality"),
        "fx_observed_at": raw.get("fx_observed_at"),
        "usdt_toman": raw.get("usdt_toman"), "adjustment_bps": raw.get("adjustment_bps", 0),
        "adjustment_label": raw.get("adjustment_label"),
        "retail_costs_included": raw.get("retail_costs_included", True),
        "daily_data": bool(raw.get("daily_data", False)),
        "timestamp_quality": timestamp_quality,
        "provider_symbol": raw.get("provider_symbol"),
        "synthetic_future": bool(spec.get("synthetic_future") or raw.get("synthetic_future_reference")),
        "market_note": (spec.get("market_note") or "شاخص نقدی مبنا برای قرارداد تمرینی؛ قیمت قرارداد آتیِ بورسی نیست"
                        if spec.get("synthetic_future") else spec.get("market_note")),
        "paper_conversion": raw.get("paper_conversion"),
    }


def get_ticker(symbol: str) -> dict:
    key = catalog.normalize_instrument_symbol(symbol)
    spec = catalog.get_instrument(key)

    def _load():
        if spec.get("source_kind") == "tgju_fx":
            from apex import fx_market
            raw = fx_market.get_rate(spec.get("currency"))
            if spec.get("synthetic_future"):
                raw = {**raw, "source": str(raw.get("source") or "TGJU") + " · مرجع نقدی قرارداد تمرینی",
                       "synthetic_futures_reference": True}
        elif spec.get("source_kind") == "tgju_cross":
            from apex import fx_market
            raw = fx_market.get_cross_rate(spec.get("base_currency"), spec.get("counter_currency", "USD"))
            if spec.get("synthetic_future"):
                raw = {**raw, "source": str(raw.get("source") or "TGJU") + " · مرجع نقدی قرارداد تمرینی",
                       "synthetic_futures_reference": True}
        elif key in ("XAU", "XAG"):
            raw = _fetch_global_metal(key)
        elif key in ("XAUF", "XAGF", "WTIF", "BRTF"):
            raw = _fetch_yahoo(spec["provider_symbol"])
            raw["status"] = "delayed" if raw.get("status") == "fresh" else raw.get("status")
        elif key in ("IRG18", "IRS925", "IRS999"):
            raw = _iran_metal_toman(key, spec)
            raw["price"] = raw.pop("price_toman")
            raw["quote_currency"] = "IRT"
        elif key in ("WTI", "BRENT"):
            if key == "BRENT":
                record = _tabloo_record("BRENT_USD", "BRENT_CRUDE_USD", "OIL_BRENT_USD")
                if record:
                    state, stamp = _record_freshness(record, 2 * 86400)
                    price = _positive(record.get("price"))
                    if price and not state[1] and str(record.get("currency", "USD")).upper() == "USD":
                        raw = {"price": price, "source": "Tabloo/TGJU Brent reference", "observed_at": stamp,
                               "status": "delayed", "stale": False, "age_seconds": state[2],
                               "quote_currency": "USD", "daily_data": True, "timestamp_quality": "daily_reference"}
                    else:
                        raw = _fetch_fred(spec["series"])
                else:
                    raw = _fetch_fred(spec["series"])
            else:
                record = _tabloo_record("WTI_USD", "OIL_WTI_USD", "ENERGY_CRUDE_OIL")
                if record:
                    state, stamp = _record_freshness(record, 2 * 86400)
                    price = _positive(record.get("price"))
                    if price and not state[1] and str(record.get("currency", "USD")).upper() == "USD":
                        raw = {"price": price, "source": "Tabloo/TGJU WTI reference", "observed_at": stamp,
                               "status": "delayed", "stale": False, "age_seconds": state[2],
                               "quote_currency": "USD", "daily_data": True, "timestamp_quality": "daily_reference"}
                    else:
                        raw = _fetch_fred(spec["series"])
                else:
                    raw = _fetch_fred(spec["series"])
        else:
            raise ValueError("ابزار بازار پشتیبانی نمی‌شود")
        return _make_ticker(key, raw, spec)

    ttl = 12 if spec.get("category") in ("fx", "fx_cross") else (30 if key in ("XAU", "XAG", "XAUF", "XAGF", "WTIF", "BRTF") else 300)
    if key in ("WTI", "BRENT"):
        ttl = 900
    result = dict(_cached("commodity:" + key, ttl, _load) or {})
    observed = _parse_time(result.get("observed_at"))
    age = max(0, int(time.time()) - observed) if observed else None
    result["age_seconds"] = age
    # Preserve the semantic distinction: these feeds are reference/delayed, not tick feeds.
    if result.get("pricing_method") == "calculated_estimate":
        result["status"] = "estimated"
    elif result.get("daily_data") or str(result.get("source", "")).startswith("Tabloo/") or "Yahoo" in str(result.get("source", "")):
        result["status"] = "delayed"
    elif observed and age is not None and age <= 180:
        result["status"] = "fresh"
    elif observed:
        result["status"] = "delayed"
    else:
        result["status"] = "unverified"
    # A price that ages beyond its provider's acceptable window is never marked tradeable.
    if key in ("XAUF", "XAGF", "WTIF", "BRTF"):
        max_age = 7 * 86400
    elif key in ("WTI", "BRENT"):
        max_age = 3 * 86400
    elif key.startswith("IR") or spec.get("category") in ("fx", "fx_cross") or (key in ("XAU", "XAG") and str(result.get("source", "")).startswith("Tabloo/")):
        max_age = 6 * 3600
    else:
        max_age = 600
    result["stale"] = bool(result.get("stale")) or age is None or age > max_age
    result["trade_demo"] = bool(result.get("trade_demo", True)) and not result["stale"]
    return result


def get_reference_usdt_toman() -> dict:
    # Use the same explicit fallback chain as domestic-ledger conversion:
    # Wallex market -> Iranian public USDT snapshot -> labelled USD parity proxy.
    return _cached("reference:usdt_toman", 15, _usdt_toman)


def get_reference_ticker(symbol: str = "USDTIRT") -> dict:
    row = dict(get_reference_usdt_toman())
    price = _positive(row.get("price"))
    if not price:
        raise ValueError("قیمت تتر/تومان در دسترس نیست")
    now = int(time.time())
    observed_at = _parse_time(row.get("observed_at"))
    timestamp_quality = str(row.get("timestamp_quality") or "missing")
    provider_ts = observed_at if timestamp_quality in ("provider", "combined_provider", "provider_snapshot") else None
    return {"base": "USDTIRT", "symbol": "USDTIRT", "price": price,
            "display_price": price, "display_currency": "IRT", "display_unit": "تومان/تتر",
            "quote_currency": "IRT", "source": row.get("source"), "ts": now,
            "observed_at": observed_at, "retrieved_at": now, "provider_ts": provider_ts,
            "source_timestamp": provider_ts, "age_seconds": row.get("age_seconds"),
            "stale": bool(row.get("stale", False)),
            "status": row.get("status") or "unverified", "category": "fx",
            "name_fa": "قیمت تتر در بازار ایران", "market": "reference",
            "trade_demo": False, "change_pct": None, "bid": row.get("bid"),
            "ask": row.get("ask"), "timestamp_quality": timestamp_quality}


def calculate_iran_metal_toman(world_ounce_usd: float, usd_toman: float, purity: float,
                               adjustment_bps: float = 0.0) -> float:
    """Spot-equivalent domestic value per gram, before local retail charges."""
    world = _positive(world_ounce_usd)
    fx = _positive(usd_toman)
    p = _positive(purity)
    if world is None or fx is None or p is None or p > 1:
        raise ValueError("پارامترهای فرمول فلز نامعتبر است")
    adj = _finite(adjustment_bps, 0.0) or 0.0
    adj = max(-500.0, min(500.0, adj))
    return world * fx / TROY_OUNCE_GRAMS * p * (1.0 + adj / 10000.0)


def _yahoo_candles(provider_symbol: str, tf: str) -> List[dict]:
    tf_map = {"1min": ("1m", "1d"), "3min": ("5m", "5d"), "5min": ("5m", "5d"),
              "15min": ("15m", "1mo"), "30min": ("30m", "1mo"),
              "1hour": ("60m", "3mo"), "2hour": ("60m", "3mo"),
              "4hour": ("60m", "6mo"), "1day": ("1d", "5y"), "1week": ("1wk", "10y")}
    interval, range_value = tf_map.get(tf, ("1d", "1y"))
    query = urllib.parse.urlencode({"range": range_value, "interval": interval})
    payload = _get_json(YAHOO_CHART + urllib.parse.quote(provider_symbol, safe="=^.-") + "?" + query, 15)
    results = ((payload.get("chart") or {}).get("result") or [])
    if not results:
        return []
    row = results[0]
    stamps = row.get("timestamp") or []
    quote = (((row.get("indicators") or {}).get("quote") or [{}])[0])
    op, hi, lo, cl, vol = (quote.get("open") or [], quote.get("high") or [],
                          quote.get("low") or [], quote.get("close") or [], quote.get("volume") or [])
    candles = []
    for i, stamp in enumerate(stamps):
        try:
            vals = [_positive(arr[i]) if i < len(arr) else None for arr in (op, hi, lo, cl)]
            if not all(vals):
                continue
            o, h, l, c = vals
            if h < max(o, c, l) or l > min(o, c, h):
                continue
            candles.append({"time": int(stamp), "open": o, "high": h, "low": l,
                            "close": c, "volume": max(0.0, _finite(vol[i] if i < len(vol) else 0, 0.0) or 0.0)})
        except Exception:
            continue
    return candles[-500:]


def _tabloo_history(symbols: tuple[str, ...]) -> List[dict]:
    try:
        index = _cached("tabloo:history:index", 3600,
                        lambda: _get_json("https://raw.githubusercontent.com/hidevs/tabloo/main/data/history/index.json", 12))
        records = list(_walk_records(index))
        match = next((r for r in records if str(r.get("symbol", "")).upper() in {x.upper() for x in symbols}), None)
        if not match:
            return []
        url = match.get("url")
        if not url:
            return []
        history = _cached("tabloo:history:" + str(match.get("symbol")), 3600,
                          lambda: _get_json(str(url), 12))
        output = []
        for row in (history.get("data") or []):
            stamp = _parse_time(row.get("t") or row.get("timestamp"))
            o, h, l, c = (_positive(row.get(k)) for k in ("open", "high", "low", "close"))
            if stamp and all((o, h, l, c)) and h >= max(o, c, l) and l <= min(o, c, h):
                output.append({"time": stamp, "open": o, "high": h, "low": l, "close": c,
                               "volume": _finite(row.get("volume"), 0.0) or 0.0})
        return output[-500:]
    except Exception:
        return []


def fetch_candles(symbol: str, tf: str = "1hour") -> List[dict]:
    key = catalog.normalize_instrument_symbol(symbol)
    spec = catalog.get_instrument(key)
    tf_key = str(tf or "1hour").lower().strip()
    tf_alias = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
                "1h": "1hour", "2h": "2hour", "4h": "4hour", "1d": "1day", "1w": "1week"}
    tf_key = tf_alias.get(tf_key, tf_key)
    if key in ("XAU", "XAG"):
        # Yahoo's spot FX symbols are used for history; the live quote remains Gold-API spot.
        provider_symbol = "XAUUSD=X" if key == "XAU" else "XAGUSD=X"
    elif key in ("XAUF", "XAGF", "WTIF", "BRTF"):
        provider_symbol = spec["provider_symbol"]
    elif key == "IRG18":
        return _tabloo_history(("GOLD_18K_IRR",)) if tf_key == "1day" else []
    elif key == "IRS925":
        return _tabloo_history(("SILVER_925_IRR",)) if tf_key == "1day" else []
    elif key == "IRS999":
        return _tabloo_history(("SILVER_999_IRR",)) if tf_key == "1day" else []
    elif key == "WTI":
        # FRED spot series provides daily closes, not OHLC candles. Do not silently
        # substitute the CL futures contract into a chart labelled as spot WTI.
        return _tabloo_history(("WTI_USD", "OIL_WTI_USD")) if tf_key == "1day" else []
    elif key == "BRENT":
        # Only show a spot history when the provider actually supplies one.
        return _tabloo_history(("BRENT_USD", "BRENT_CRUDE_USD", "OIL_BRENT_USD"))
    else:
        return []
    try:
        return _cached(f"commodity:candles:{key}:{tf_key}", 300,
                       lambda: _yahoo_candles(provider_symbol, tf_key)) or []
    except Exception:
        return []


def get_market_overview(symbols: Optional[List[str]] = None) -> List[dict]:
    chosen = symbols or list(catalog.INSTRUMENTS)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    output = []
    with ThreadPoolExecutor(max_workers=min(6, len(chosen) or 1)) as pool:
        jobs = {pool.submit(get_ticker, symbol): symbol for symbol in chosen}
        for future in as_completed(jobs):
            try:
                output.append(future.result())
            except Exception as exc:
                output.append({"base": jobs[future], "name_fa": jobs[future], "price": None,
                               "status": "unavailable", "stale": True, "error": str(exc)[:120]})
    order = {s: i for i, s in enumerate(chosen)}
    output.sort(key=lambda x: order.get(x.get("base"), 999))
    return output
