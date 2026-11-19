# -*- coding: utf-8 -*-
"""Read-only Iranian FX reference rates from TGJU's public AJAX snapshot.

TGJU publishes Iranian domestic exchange rates in IRR. KALVO converts those
values to Toman (IRT / 10) at the adapter boundary so every downstream module
receives a single, explicit native unit.
"""
from __future__ import annotations

import json
import math
import os
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    import certifi
except Exception:
    certifi = None

from apex.config import get_env

TGJU_AJAX_DEFAULT = "https://call2.tgju.org/ajax.json"
USER_AGENT = "KALVO/7.0 (read-only FX market data)"
CACHE_SECONDS = 12
FRESH_SECONDS = 15 * 60
STALE_SECONDS = 6 * 60 * 60
_lock = threading.RLock()
_fetch_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "payload": None}

# These aliases target TGJU's stable current-board slugs. Values are Rial.
CURRENCY_KEYS: Dict[str, tuple[str, ...]] = {
    "USD": ("price_dollar_rl",),
    "EUR": ("price_eur",),
    "GBP": ("price_gbp",),
    "AED": ("price_aed",),
    "TRY": ("price_try",),
    "CAD": ("price_cad",),
    "AUD": ("price_aud",),
    "CNY": ("price_cny",),
    "NZD": ("price_nzd",),
    "SGD": ("price_sgd",),
    "INR": ("price_inr",),
    "PKR": ("price_pkr",),
    "IQD": ("price_iqd",),
    "SYP": ("price_syp",),
    "AFN": ("price_afn",),
    "DKK": ("price_dkk",),
    "SEK": ("price_sek",),
    "NOK": ("price_nok",),
    "SAR": ("price_sar",),
    "QAR": ("price_qar",),
    "OMR": ("price_omr",),
    "KWD": ("price_kwd",),
    "BHD": ("price_bhd",),
    "MYR": ("price_myr",),
    "THB": ("price_thb",),
    "HKD": ("price_hkd",),
    "RUB": ("price_rub",),
    "AZN": ("price_azn",),
    "AMD": ("price_amd",),
    "GEL": ("price_gel",),
    "KGS": ("price_kgs",),
    "TJS": ("price_tjs",),
    "TMT": ("price_tmt",),
}



def _digits(value: Any) -> str:
    text = str(value or "").strip()
    table = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return text.translate(table).replace("٬", "").replace(",", "").replace(" ", "")


def _number(value: Any) -> Optional[float]:
    try:
        n = float(_digits(value))
        return n if math.isfinite(n) and n > 0 else None
    except (TypeError, ValueError):
        return None

def _finite_number(value: Any) -> Optional[float]:
    try:
        n = float(_digits(value))
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: Any) -> Optional[int]:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            n = float(value)
            return int(n / 1000 if n > 10_000_000_000 else n)
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        if parsed.tzinfo is None:
            # TGJU's timestamp is Tehran local time when no offset is provided.
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=3, minutes=30)))
        return int(parsed.timestamp())
    except Exception:
        return None


def _payload_current(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("پاسخ TGJU باید JSON object باشد")
    current = payload.get("current")
    if not isinstance(current, dict):
        # Defensive compatibility for wrappers that put current under data.
        data = payload.get("data")
        current = data.get("current") if isinstance(data, dict) else None
    if not isinstance(current, dict):
        raise ValueError("ساختار TGJU فاقد بخش current است")
    return current


def parse_tgju_payload(payload: Any, currency: str, now: Optional[int] = None) -> dict:
    """Parse one currency; always convert source Rial to Toman by dividing by 10."""
    code = str(currency or "").upper().strip()
    candidates = CURRENCY_KEYS.get(code)
    if not candidates:
        raise ValueError(f"ارز {code or 'نامشخص'} در نگاشت TGJU تعریف نشده است")
    current = _payload_current(payload)
    record = next((current.get(key) for key in candidates if isinstance(current.get(key), dict)), None)
    if not record:
        raise LookupError(f"نرخ {code} در snapshot فعلی TGJU پیدا نشد")
    raw_irr = _number(record.get("p"))
    if raw_irr is None:
        raise ValueError(f"قیمت {code} در TGJU معتبر نیست")
    price_toman = raw_irr / 10.0
    if price_toman <= 0 or not math.isfinite(price_toman):
        raise ValueError(f"قیمت تبدیل‌شدهٔ {code} معتبر نیست")

    stamp = parse_timestamp(record.get("ts"))
    current_time = int(now if now is not None else time.time())
    age = max(0, current_time - stamp) if stamp else None
    if age is None:
        status, stale = "unverified", True
    elif age <= FRESH_SECONDS:
        status, stale = "fresh", False
    elif age <= STALE_SECONDS:
        status, stale = "delayed", False
    else:
        status, stale = "stale", True

    def rial_to_toman(field: str) -> Optional[float]:
        n = _number(record.get(field))
        return n / 10.0 if n is not None else None

    return {
        "currency": code,
        "price": price_toman,
        "raw_price_irr": raw_irr,
        "high": rial_to_toman("h"),
        "low": rial_to_toman("l"),
        "change": (_finite_number(record.get("d")) or 0.0) / 10.0,
        "change_pct": _finite_number(record.get("dp")) if record.get("dp") not in (None, "") else None,
        "observed_at": stamp,
        "age_seconds": age,
        "status": status,
        "stale": stale,
        "source": "TGJU call2.ajax.json",
        "source_key": next((key for key in candidates if isinstance(current.get(key), dict)), None),
        "quote_currency": "TOMAN",
        "unit": f"تومان به‌ازای ۱ {code}",
        "raw_unit": "IRR per 1 currency unit",
        "conversion": "TGJU Rial price / 10 = Toman price",
        "timestamp_quality": "provider" if stamp else "missing",
        "market": "Iran open-market FX reference",
    }


def get_cross_rate(base_currency: str, quote_currency: str = "USD") -> dict:
    """Derive a local implied FX cross from two TGJU Toman rates.

    This is an Iranian free-market implied cross, not an interbank/broker FX feed.
    Both inputs must be timestamped and trade-eligible before the result is marked
    fresh; the older input controls the combined age/status.
    """
    base_code = str(base_currency or "").upper().strip()
    quote_code = str(quote_currency or "USD").upper().strip()
    if base_code == quote_code:
        raise ValueError("ارز پایه و مظنه نمی‌توانند یکسان باشند")
    base = get_rate(base_code)
    quote = get_rate(quote_code)
    if base.get("stale") or quote.get("stale"):
        raise ValueError("نرخ یکی از ارزهای پایه/مظنه تازه و قابل اتکا نیست")
    base_toman = _number(base.get("price"))
    quote_toman = _number(quote.get("price"))
    if not base_toman or not quote_toman:
        raise ValueError("نرخ تقاطعی از دادهٔ معتبر قابل محاسبه نیست")
    price = base_toman / quote_toman
    if not math.isfinite(price) or price <= 0:
        raise ValueError("نرخ تقاطعی نامعتبر است")
    ages = [x for x in (base.get("age_seconds"), quote.get("age_seconds")) if x is not None]
    age = max(ages) if ages else None
    observed = min(x for x in (base.get("observed_at"), quote.get("observed_at")) if x) if base.get("observed_at") and quote.get("observed_at") else None
    statuses = {base.get("status"), quote.get("status")}
    status = "delayed" if "delayed" in statuses else ("fresh" if statuses == {"fresh"} else "unverified")
    return {
        "currency": f"{base_code}{quote_code}", "base_currency": base_code,
        "quote_currency": quote_code, "price": price,
        "observed_at": observed, "age_seconds": age, "status": status,
        "stale": False, "source": "TGJU implied cross · Toman/Toman",
        "source_inputs": [base.get("source_key"), quote.get("source_key")],
        "quote_unit": f"{quote_code} per 1 {base_code}",
        "unit": f"{quote_code} per 1 {base_code}",
        "conversion": f"{base_code}/IRT ÷ {quote_code}/IRT",
        "timestamp_quality": "combined_provider", "market": "Iran free-market implied FX cross",
    }


def _fetch_payload() -> dict:
    url = get_env("TGJU_AJAX_URL", TGJU_AJAX_DEFAULT) or TGJU_AJAX_DEFAULT
    rev = get_env("TGJU_REV", "")
    if rev:
        separator = "&" if "?" in url else "?"
        url = url + separator + urllib.parse.urlencode({"rev": rev})
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.7",
        "Referer": "https://www.tgju.org/",
        "Origin": "https://www.tgju.org",
    })
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=12, context=context) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("پاسخ TGJU بیش از حد بزرگ است")
    payload = json.loads(raw.decode("utf-8-sig"))
    _payload_current(payload)
    return payload


def get_snapshot(force: bool = False) -> dict:
    now = time.monotonic()
    with _lock:
        if not force and _cache.get("payload") is not None and now - float(_cache.get("at") or 0) <= CACHE_SECONDS:
            return _cache["payload"]
    # Single-flight fetch: parallel search/watchlist requests share one snapshot.
    with _fetch_lock:
        now = time.monotonic()
        with _lock:
            if not force and _cache.get("payload") is not None and now - float(_cache.get("at") or 0) <= CACHE_SECONDS:
                return _cache["payload"]
        payload = _fetch_payload()
        with _lock:
            _cache.update({"at": time.monotonic(), "payload": payload})
        return payload


def get_rate(currency: str, force: bool = False) -> dict:
    return parse_tgju_payload(get_snapshot(force=force), currency)


def get_rates(currencies: Optional[list[str]] = None, force: bool = False) -> dict:
    payload = get_snapshot(force=force)
    selected = currencies or list(CURRENCY_KEYS)
    result = {}
    for currency in selected:
        try:
            result[currency] = parse_tgju_payload(payload, currency)
        except (LookupError, ValueError):
            continue
    return result


def clear_cache() -> None:
    with _lock:
        _cache.update({"at": 0.0, "payload": None})
