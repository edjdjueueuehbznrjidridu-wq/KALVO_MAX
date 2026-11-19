# -*- coding: utf-8 -*-
"""Canonical instrument catalogue for KALVO's read-only market data + paper trading."""
from __future__ import annotations

from copy import deepcopy
from typing import Dict, List

INSTRUMENTS: Dict[str, dict] = {
    "XAU": {"name_fa": "طلای جهانی نقدی", "category": "metals", "market": "spot", "unit": "اونس تروا", "quote_currency": "USD", "source_kind": "gold_api", "display_unit": "USD/oz t", "trade_demo": True},
    "XAG": {"name_fa": "نقره جهانی نقدی", "category": "metals", "market": "spot", "unit": "اونس تروا", "quote_currency": "USD", "source_kind": "gold_api", "display_unit": "USD/oz t", "trade_demo": True},
    "XAUF": {"name_fa": "قرارداد آتی طلا", "category": "metals", "market": "futures", "unit": "اونس تروا", "quote_currency": "USD", "source_kind": "yahoo", "provider_symbol": "GC=F", "display_unit": "USD/oz t", "trade_demo": True},
    "XAGF": {"name_fa": "قرارداد آتی نقره", "category": "metals", "market": "futures", "unit": "اونس تروا", "quote_currency": "USD", "source_kind": "yahoo", "provider_symbol": "SI=F", "display_unit": "USD/oz t", "trade_demo": True},
    "IRG18": {"name_fa": "طلای ۱۸ عیار ایران", "category": "iran_metals", "market": "spot", "unit": "گرم", "quote_currency": "USDT", "native_currency": "IRT", "source_kind": "iran_gold", "display_unit": "تومان/گرم", "trade_demo": True},
    "IRS999": {"name_fa": "نقره ۹۹۹ ایران", "category": "iran_metals", "market": "spot", "unit": "گرم", "quote_currency": "USDT", "native_currency": "IRT", "source_kind": "iran_silver", "display_unit": "تومان/گرم", "purity": 0.999, "trade_demo": True},
    "IRS925": {"name_fa": "نقره ۹۲۵ ایران", "category": "iran_metals", "market": "spot", "unit": "گرم", "quote_currency": "USDT", "native_currency": "IRT", "source_kind": "iran_silver", "display_unit": "تومان/گرم", "purity": 0.925, "trade_demo": True},
    # Iranian open-market FX references. TGJU's source values are Rial and are
    # normalized to Toman in apex.fx_market before entering the common ticker model.
    "USDIRT": {"name_fa": "دلار آزاد ایران", "category": "fx", "market": "spot", "unit": "دلار", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "USD", "display_unit": "تومان / دلار", "trade_demo": True},
    "EURIRT": {"name_fa": "یورو آزاد ایران", "category": "fx", "market": "spot", "unit": "یورو", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "EUR", "display_unit": "تومان / یورو", "trade_demo": True},
    "GBPIRT": {"name_fa": "پوند آزاد ایران", "category": "fx", "market": "spot", "unit": "پوند", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "GBP", "display_unit": "تومان / پوند", "trade_demo": True},
    "AEDIRT": {"name_fa": "درهم آزاد ایران", "category": "fx", "market": "spot", "unit": "درهم", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "AED", "display_unit": "تومان / درهم", "trade_demo": True},
    "TRYIRT": {"name_fa": "لیر ترکیه در ایران", "category": "fx", "market": "spot", "unit": "لیر", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "TRY", "display_unit": "تومان / لیر", "trade_demo": True},
    "CADIRT": {"name_fa": "دلار کانادا در ایران", "category": "fx", "market": "spot", "unit": "دلار کانادا", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "CAD", "display_unit": "تومان / دلار کانادا", "trade_demo": True},
    "AUDIRT": {"name_fa": "دلار استرالیا در ایران", "category": "fx", "market": "spot", "unit": "دلار استرالیا", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "AUD", "display_unit": "تومان / دلار استرالیا", "trade_demo": True},
    "CNYIRT": {"name_fa": "یوان چین در ایران", "category": "fx", "market": "spot", "unit": "یوان", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "CNY", "display_unit": "تومان / یوان", "trade_demo": True},
    # Synthetic educational futures references; they use the TGJU spot index as
    # an underlying and are NOT exchange-listed futures prices.
    "USDIRTF": {"name_fa": "قرارداد تمرینی دلار/تومان", "category": "fx", "market": "futures", "unit": "دلار", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "USD", "display_unit": "تومان / دلار · مرجع نقدی", "synthetic_future": True, "trade_demo": True},
    "EURIRTF": {"name_fa": "قرارداد تمرینی یورو/تومان", "category": "fx", "market": "futures", "unit": "یورو", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "EUR", "display_unit": "تومان / یورو · مرجع نقدی", "synthetic_future": True, "trade_demo": True},
    "GBPIRTF": {"name_fa": "قرارداد تمرینی پوند/تومان", "category": "fx", "market": "futures", "unit": "پوند", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "GBP", "display_unit": "تومان / پوند · مرجع نقدی", "synthetic_future": True, "trade_demo": True},
    "AEDIRTF": {"name_fa": "قرارداد تمرینی درهم/تومان", "category": "fx", "market": "futures", "unit": "درهم", "quote_currency": "IRT", "native_currency": "TOMAN", "source_kind": "tgju_fx", "currency": "AED", "display_unit": "تومان / درهم · مرجع نقدی", "synthetic_future": True, "trade_demo": True},
    "WTI": {"name_fa": "نفت خام WTI نقدی", "category": "energy", "market": "spot", "unit": "بشکه", "quote_currency": "USD", "source_kind": "fred", "series": "DCOILWTICO", "display_unit": "USD/bbl", "trade_demo": True},
    "BRENT": {"name_fa": "نفت برنت نقدی", "category": "energy", "market": "spot", "unit": "بشکه", "quote_currency": "USD", "source_kind": "fred", "series": "DCOILBRENTEU", "display_unit": "USD/bbl", "trade_demo": True},
    "WTIF": {"name_fa": "قرارداد آتی نفت WTI", "category": "energy", "market": "futures", "unit": "بشکه", "quote_currency": "USD", "source_kind": "yahoo", "provider_symbol": "CL=F", "display_unit": "USD/bbl", "trade_demo": True},
    "BRTF": {"name_fa": "قرارداد آتی نفت برنت", "category": "energy", "market": "futures", "unit": "بشکه", "quote_currency": "USD", "source_kind": "yahoo", "provider_symbol": "BZ=F", "display_unit": "USD/bbl", "trade_demo": True},
}

# Data-driven Iranian open-market currency catalogue. Only keys documented by
# public TGJU integrations are included; uncertain CHF/JPY aliases are omitted.
FX_CURRENCY_META = {
    "USD": ("دلار آزاد ایران", "دلار آمریکا"),
    "EUR": ("یورو آزاد ایران", "یورو"),
    "GBP": ("پوند آزاد ایران", "پوند انگلیس"),
    "AED": ("درهم آزاد ایران", "درهم امارات"),
    "TRY": ("لیر ترکیه در ایران", "لیر ترکیه"),
    "CAD": ("دلار کانادا در ایران", "دلار کانادا"),
    "AUD": ("دلار استرالیا در ایران", "دلار استرالیا"),
    "CNY": ("یوان چین در ایران", "یوان چین"),
    "NZD": ("دلار نیوزیلند در ایران", "دلار نیوزیلند"),
    "SGD": ("دلار سنگاپور در ایران", "دلار سنگاپور"),
    "INR": ("روپیه هند در ایران", "روپیه هند"),
    "PKR": ("روپیه پاکستان در ایران", "روپیه پاکستان"),
    "IQD": ("دینار عراق در ایران", "دینار عراق"),
    "SYP": ("لیره سوریه در ایران", "لیره سوریه"),
    "AFN": ("افغانی در ایران", "افغانی افغانستان"),
    "DKK": ("کرون دانمارک در ایران", "کرون دانمارک"),
    "SEK": ("کرون سوئد در ایران", "کرون سوئد"),
    "NOK": ("کرون نروژ در ایران", "کرون نروژ"),
    "SAR": ("ریال عربستان در ایران", "ریال عربستان"),
    "QAR": ("ریال قطر در ایران", "ریال قطر"),
    "OMR": ("ریال عمان در ایران", "ریال عمان"),
    "KWD": ("دینار کویت در ایران", "دینار کویت"),
    "BHD": ("دینار بحرین در ایران", "دینار بحرین"),
    "MYR": ("رینگیت مالزی در ایران", "رینگیت مالزی"),
    "THB": ("بات تایلند در ایران", "بات تایلند"),
    "HKD": ("دلار هنگ‌کنگ در ایران", "دلار هنگ‌کنگ"),
    "RUB": ("روبل روسیه در ایران", "روبل روسیه"),
    "AZN": ("منات آذربایجان در ایران", "منات آذربایجان"),
    "AMD": ("درام ارمنستان در ایران", "درام ارمنستان"),
    "GEL": ("لاری گرجستان در ایران", "لاری گرجستان"),
    "KGS": ("سوم قرقیزستان در ایران", "سوم قرقیزستان"),
    "TJS": ("سامانی تاجیکستان در ایران", "سامانی تاجیکستان"),
    "TMT": ("منات ترکمنستان در ایران", "منات ترکمنستان"),
}
for _code, (_name, _unit_name) in FX_CURRENCY_META.items():
    _spot_symbol = f"{_code}IRT"
    INSTRUMENTS.setdefault(_spot_symbol, {
        "name_fa": _name, "category": "fx", "market": "spot", "unit": _unit_name,
        "quote_currency": "TOMAN", "native_currency": "TOMAN", "source_kind": "tgju_fx",
        "currency": _code, "display_unit": f"تومان / {_unit_name}", "trade_demo": True,
    })
    _future_symbol = f"{_code}IRTF"
    INSTRUMENTS.setdefault(_future_symbol, {
        "name_fa": f"قرارداد تمرینی {_unit_name}/تومان", "category": "fx", "market": "futures",
        "unit": _unit_name, "quote_currency": "TOMAN", "native_currency": "TOMAN",
        "source_kind": "tgju_fx", "currency": _code,
        "display_unit": f"تومان / {_unit_name} · مرجع نقدی",
        "synthetic_future": True, "trade_demo": True,
    })

# Implied cross rates derived from Iranian free-market Toman quotes. These are
# useful FX-style pairs (e.g. EURUSD), but are not interbank/broker spot prices.
FX_CROSS_META = {
    code: (f"نرخ تقاطعی {unit_name} به دلار", unit_name)
    for code, (_name, unit_name) in FX_CURRENCY_META.items() if code != "USD"
}
for _code, (_name, _unit_name) in FX_CROSS_META.items():
    _pair_symbol = f"{_code}USD"
    INSTRUMENTS.setdefault(_pair_symbol, {
        "name_fa": _name, "category": "fx_cross", "market": "spot",
        "unit": _unit_name, "quote_currency": "USD", "source_kind": "tgju_cross",
        "base_currency": _code, "counter_currency": "USD",
        "display_unit": f"USD به‌ازای ۱ {_unit_name}", "trade_demo": True,
        "market_note": "نرخ تقاطعی استنباطی از قیمت‌های آزاد ایران؛ نه نرخ بین‌بانکی جهانی",
    })
    _future_symbol = f"{_code}USDF"
    INSTRUMENTS.setdefault(_future_symbol, {
        "name_fa": f"قرارداد تمرینی {_unit_name}/دلار", "category": "fx_cross", "market": "futures",
        "unit": _unit_name, "quote_currency": "USD", "source_kind": "tgju_cross",
        "base_currency": _code, "counter_currency": "USD",
        "display_unit": f"USD به‌ازای ۱ {_unit_name} · مرجع نقدی",
        "synthetic_future": True, "trade_demo": True,
        "market_note": "شاخص تمرینی برگرفته از نرخ تقاطعی نقدی؛ قیمت قرارداد آتی بورسی نیست",
    })

# These are read-only reference instruments, not a base asset for a paper order.
REFERENCE_SYMBOLS = {"USDTIRT"}
FX_SPOT_SYMBOLS = {f"{code}IRT" for code in FX_CURRENCY_META}
FX_FUTURES_SYMBOLS = {f"{code}IRTF" for code in FX_CURRENCY_META}
FX_CROSS_SPOT_SYMBOLS = {f"{code}USD" for code in FX_CROSS_META}
FX_CROSS_FUTURES_SYMBOLS = {f"{code}USDF" for code in FX_CROSS_META}
ALIASES = {
    "GOLD": "XAU", "XAUUSD": "XAU", "SILVER": "XAG", "XAGUSD": "XAG",
    "GOLD18": "IRG18", "18K": "IRG18", "GOLD18IRAN": "IRG18",
    "SILVER999": "IRS999", "SILVER925": "IRS925",
    "OIL": "WTI", "CRUDE": "WTI", "OILWTI": "WTI",
    "OILBRENT": "BRENT", "GOLDF": "XAUF", "SILVERF": "XAGF",
    "WTIFUT": "WTIF", "BRENTF": "BRTF", "BRENTFUT": "BRTF",
    "USDT-IRT": "USDTIRT", "USDT-TMN": "USDTIRT", "USDTTMN": "USDTIRT",
    "USD/IRT": "USDIRT", "USD-IRT": "USDIRT", "USD-TMN": "USDIRT", "DOLLAR": "USDIRT",
    "EUR/IRT": "EURIRT", "EUR-IRT": "EURIRT", "EURO": "EURIRT", "GBP/IRT": "GBPIRT", "GBP-IRT": "GBPIRT", "POUND": "GBPIRT",
    "AED/IRT": "AEDIRT", "AED-IRT": "AEDIRT", "TRY/IRT": "TRYIRT", "TRY-IRT": "TRYIRT", "CAD/IRT": "CADIRT", "CAD-IRT": "CADIRT",
    "AUD/IRT": "AUDIRT", "AUD-IRT": "AUDIRT", "CNY/IRT": "CNYIRT", "CNY-IRT": "CNYIRT",
}
for _code in FX_CURRENCY_META:
    ALIASES[f"{_code}/IRT"] = f"{_code}IRT"
    ALIASES[f"{_code}-IRT"] = f"{_code}IRT"
    ALIASES[f"{_code}/TMN"] = f"{_code}IRT"
    ALIASES[f"{_code}-TMN"] = f"{_code}IRT"
    ALIASES[f"{_code}IRT-PERP"] = f"{_code}IRTF"
for _code in FX_CROSS_META:
    ALIASES[f"{_code}/USD"] = f"{_code}USD"
    ALIASES[f"{_code}-USD"] = f"{_code}USD"
    ALIASES[f"{_code}USD-PERP"] = f"{_code}USDF"



def normalize_instrument_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper().replace("/", "-").replace("_", "-").replace(" ", "")
    if s.endswith("-USDT"):
        s = s[:-5]
    elif s.endswith("USDT") and s not in INSTRUMENTS:
        s = s[:-4]
    return ALIASES.get(s, s)


def is_instrument(symbol: str) -> bool:
    return normalize_instrument_symbol(symbol) in INSTRUMENTS


def is_reference_symbol(symbol: str) -> bool:
    return normalize_instrument_symbol(symbol) in REFERENCE_SYMBOLS


def get_instrument(symbol: str) -> dict:
    key = normalize_instrument_symbol(symbol)
    if key not in INSTRUMENTS:
        raise KeyError(key)
    return {"symbol": key, **deepcopy(INSTRUMENTS[key])}


def list_instruments(market: str | None = None) -> List[str]:
    return [key for key, spec in INSTRUMENTS.items() if market is None or spec["market"] == market]
