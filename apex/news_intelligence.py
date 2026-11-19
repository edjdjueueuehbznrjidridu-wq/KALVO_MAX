# -*- coding: utf-8 -*-
"""KALVO 7.2 News Intelligence: source-aware RSS ingestion and fail-closed gate.

Headlines are not trade signals. This module deduplicates news, checks source
allow-lists and publication times, tags risk/event types, and only supplies a
risk gate. A headline can block an automated paper entry; it cannot force one.
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from . import storage

USER_AGENT = "KALVO/7.2 (News Intelligence; source-attributed RSS)"
CACHE_TTL_SECONDS = 300
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ITEMS_PER_FEED = 35
MAX_ARTICLE_AGE_SECONDS = 3 * 60 * 60
_lock = threading.RLock()
_fetch_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "items": [], "errors": [], "fetched": False}

# URLs are public RSS endpoints; providers are named for attribution and are not
# treated as proof that every headline is correct.
FEEDS = (
    {"id": "coindesk", "name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
     "trust": 0.82, "scope": "crypto", "domains": ("coindesk.com",)},
    {"id": "federal_reserve", "name": "Federal Reserve", "url": "https://www.federalreserve.gov/feeds/press_all.xml",
     "trust": 0.98, "scope": "macro", "domains": ("federalreserve.gov",)},
    {"id": "bls", "name": "U.S. Bureau of Labor Statistics", "url": "https://www.bls.gov/feed/bls_latest.rss",
     "trust": 0.99, "scope": "macro", "domains": ("bls.gov",)},
    {"id": "ecb", "name": "European Central Bank", "url": "https://www.ecb.europa.eu/rss/press.html",
     "trust": 0.98, "scope": "macro", "domains": ("ecb.europa.eu",)},
    {"id": "sec", "name": "U.S. Securities and Exchange Commission", "url": "https://www.sec.gov/rss/news/press.xml",
     "trust": 0.99, "scope": "regulatory", "domains": ("sec.gov",)},
)

ASSET_TERMS = {
    "BTC": ("bitcoin", "btc"), "ETH": ("ethereum", "ether", "eth"),
    "SOL": ("solana", "sol"), "XRP": ("xrp", "ripple"),
    "BNB": ("bnb", "binance coin"), "DOGE": ("dogecoin", "doge"),
    "ADA": ("cardano", "ada"), "LINK": ("chainlink", "link"),
    "AVAX": ("avalanche", "avax"), "DOT": ("polkadot", "dot"),
    "XAU": ("gold", "xau"), "XAG": ("silver", "xag"),
    "WTI": ("wti", "crude oil", "oil prices", "oil market"),
    "BRENT": ("brent", "crude oil", "oil prices", "oil market"),
    "USD": ("us dollar", "dollar index", "federal reserve", "fed policy", "interest rates", "inflation", "cpi"),
    "EUR": ("euro", "ecb", "european central bank", "eurozone"),
}
MACRO_TERMS = ("federal reserve", "fed", "interest rate", "rate cut", "rate hike",
               "inflation", "consumer prices", "cpi", "central bank", "monetary policy",
               "dollar", "treasury yield")
EVENT_PATTERNS = (
    ("security", 95, ("hack", "hacked", "exploit", "breach", "stolen", "security incident", "vulnerability")),
    ("solvency", 95, ("bankruptcy", "insolvency", "withdrawal freeze", "withdrawals halted", "liquidation")),
    ("outage", 85, ("outage", "trading halted", "suspended trading", "platform down", "network outage")),
    ("regulatory", 75, ("sec charges", "securities and exchange commission", "lawsuit", "ban", "regulatory action", "enforcement action", "sanctions", "trading suspension")),
    ("macro", 65, ("rate decision", "rate cut", "rate hike", "inflation data", "fomc", "policy statement",
                    "consumer price index", "producer price index", "employment situation", "nonfarm payroll",
                    "job openings", "ecb monetary policy", "deposit facility rate")),
    ("market_structure", 50, ("etf approval", "etf rejection", "listing", "delisting", "token unlock")),
)


def _parse_date(value: str) -> Optional[int]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except Exception:
        return None


def _text(parent: ET.Element, names: Iterable[str]) -> str:
    for name in names:
        node = parent.find(name)
        if node is not None and (node.text or "").strip():
            return re.sub(r"\s+", " ", node.text).strip()
    return ""


def _fetch_xml(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=max(3, min(20, int(timeout)))) as response:
        payload = response.read(MAX_FEED_BYTES + 1)
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError("RSS feed exceeds 2 MiB safety limit")
    return payload


def _source_for_url(url: str) -> Optional[dict]:
    host = (urlparse(url).hostname or "").lower()
    for feed in FEEDS:
        if any(host == domain or host.endswith("." + domain) for domain in feed["domains"]):
            return feed
    return None


def _parse_feed(feed: dict, payload: bytes, now: int) -> List[dict]:
    root = ET.fromstring(payload)
    nodes = list(root.findall(".//item")) or list(root.findall(".//{*}entry"))
    items = []
    for node in nodes[:MAX_ITEMS_PER_FEED]:
        title = _text(node, ("title", "{*}title"))
        link = _text(node, ("link", "{*}link"))
        if not link:
            link_node = node.find("{*}link")
            if link_node is not None:
                link = link_node.attrib.get("href", "")
        summary = _text(node, ("description", "summary", "content", "{*}summary", "{*}content"))
        published = _text(node, ("pubDate", "published", "updated", "{*}published", "{*}updated", "{*}date"))
        timestamp = _parse_date(published)
        if not title or not link or not timestamp:
            continue
        if timestamp > now + 300:
            continue
        host = (urlparse(link).hostname or urlparse(feed["url"]).hostname or "").lower()
        source = _source_for_url("https://" + host)
        if source is None:
            continue
        uid = hashlib.sha256((link or title).encode("utf-8", "ignore")).hexdigest()[:20]
        items.append({
            "id": uid, "title": title[:300], "summary": summary[:1200],
            "url": link[:700], "published_at": timestamp,
            "age_seconds": max(0, now - timestamp),
            "source": source["name"], "source_id": source["id"],
            "source_domain": host, "source_trust": float(source["trust"]),
            "scope": source["scope"],
        })
    return items


def classify_article(article: Dict[str, Any]) -> Dict[str, Any]:
    text = (str(article.get("title") or "") + " " + str(article.get("summary") or "")).lower()
    event, severity = "general", 20
    for label, weight, terms in EVENT_PATTERNS:
        if any(term in text for term in terms):
            event, severity = label, weight
            break
    # This is an event-risk classifier, not a directional price prediction.
    return {**article, "event_type": event, "severity": severity,
            "sentiment": "unknown", "directional_signal": None,
            "classification_method": "transparent_keyword_risk_tags"}


def fetch_news(force_refresh: bool = False, now: Optional[float] = None) -> Dict[str, Any]:
    wall_now = int(now if now is not None else time.time())
    mono_now = time.monotonic()
    with _lock:
        if (not force_refresh and _cache.get("fetched")
                and mono_now - float(_cache.get("at") or 0) < CACHE_TTL_SECONDS):
            return {"items": list(_cache["items"]), "errors": list(_cache["errors"]),
                    "cached": True, "fetched_at": _cache.get("fetched_at")}
    with _fetch_lock:
        with _lock:
            if (not force_refresh and _cache.get("fetched")
                    and time.monotonic() - float(_cache.get("at") or 0) < CACHE_TTL_SECONDS):
                return {"items": list(_cache["items"]), "errors": list(_cache["errors"]),
                        "cached": True, "fetched_at": _cache.get("fetched_at")}
        all_items: List[dict] = []
        errors: List[str] = []
        # Pull feeds concurrently so a slow publisher cannot serialize all sources.
        # Every source still has an individual timeout and strict hostname allow-list.
        with ThreadPoolExecutor(max_workers=min(4, len(FEEDS)), thread_name_prefix="KALVO-news") as pool:
            futures = {pool.submit(_fetch_xml, feed["url"], 8): feed for feed in FEEDS}
            for future in as_completed(futures):
                feed = futures[future]
                try:
                    payload = future.result()
                    all_items.extend(_parse_feed(feed, payload, wall_now))
                except Exception as exc:
                    errors.append(f"{feed['name']}: {str(exc)[:120]}")
        # Stable dedup by URL/article ID; most recently published instance wins.
        dedup = {item["id"]: classify_article(item) for item in all_items}
        items = sorted(dedup.values(), key=lambda item: item["published_at"], reverse=True)
        with _lock:
            _cache.update({"at": time.monotonic(), "items": items, "errors": errors,
                           "fetched_at": wall_now, "fetched": True})
        return {"items": list(items), "errors": list(errors), "cached": False,
                "fetched_at": wall_now}


def _relevance(article: Dict[str, Any], symbol: str) -> float:
    text = (str(article.get("title") or "") + " " + str(article.get("summary") or "")).lower()
    code = str(symbol or "").upper().replace("-USDT", "").replace("USDT", "")
    terms = list(ASSET_TERMS.get(code, ()))
    if not terms:
        terms = (code.lower(),)
    def _hit(term: str) -> bool:
        candidate = str(term).lower().strip()
        if not candidate:
            return False
        if re.fullmatch(r"[a-z0-9]{1,4}", candidate):
            return re.search(r"(?<![a-z0-9])" + re.escape(candidate) + r"(?![a-z0-9])", text) is not None
        return candidate in text
    direct_hits = sum(1 for term in terms if _hit(term))
    if direct_hits:
        return min(1.0, 0.65 + 0.15 * direct_hits)
    if article.get("scope") == "macro" and any(term in text for term in MACRO_TERMS):
        return 0.55
    return 0.0


def get_news_context(symbol: str, force_refresh: bool = False,
                     now: Optional[int] = None) -> Dict[str, Any]:
    current = int(now if now is not None else time.time())
    feed_result = fetch_news(force_refresh=force_refresh, now=current)
    relevant = []
    for article in feed_result["items"]:
        item = dict(article)
        item["relevance"] = _relevance(item, symbol)
        if item["relevance"] > 0:
            item["age_seconds"] = max(0, current - int(item["published_at"]))
            relevant.append(item)
    fresh = [x for x in relevant if x["age_seconds"] <= MAX_ARTICLE_AGE_SECONDS
             and x["source_trust"] >= 0.80]
    high_risk = [x for x in fresh if int(x.get("severity") or 0) >= 80]
    return {
        "symbol": str(symbol).upper(),
        "items": fresh[:12],
        "relevant_count": len(relevant),
        "fresh_verified_count": len(fresh),
        "high_risk_count": len(high_risk),
        "risk_level": "high" if high_risk else "moderate" if fresh else "unknown",
        "source_errors": feed_result["errors"],
        "fetched_at": feed_result.get("fetched_at"),
        "cached": feed_result.get("cached", False),
        "method": "parallel RSS + source allow-list + publication-time + relevance + event-risk tags",
        "directional_prediction": False,
        "coverage_note": "Coverage is limited to configured feeds; this is not a complete global news terminal.",
    }


def evaluate_entry_gate(symbol: str, side: str, enabled: Optional[bool] = None,
                        now: Optional[int] = None) -> Dict[str, Any]:
    active = (storage.get_setting("news_trade_enabled", "0") == "1") if enabled is None else bool(enabled)
    if not active:
        return {"enabled": False, "allow": True, "status": "disabled",
                "reason": "فیلتر خبری خاموش است؛ خبر در تصمیم ورود دخالت داده نمی‌شود."}
    context = get_news_context(symbol, now=now)
    if context["fresh_verified_count"] == 0:
        return {"enabled": True, "allow": False, "status": "no_verified_context",
                "reason": "خبر تازه و مرتبط از منبع تأییدشده در دسترس نیست؛ ورود خودکار متوقف شد.",
                "context": context}
    if context["high_risk_count"] > 0:
        top = next((row for row in context["items"] if int(row.get("severity") or 0) >= 80), context["items"][0])
        return {"enabled": True, "allow": False, "status": "high_event_risk",
                "reason": f"رویداد پرریسک ({top.get('event_type')}) از {top.get('source')}؛ ورود متوقف شد.",
                "context": context}
    return {"enabled": True, "allow": True, "status": "verified_context_no_block",
            "reason": "منابع خبری بررسی شدند؛ خبر به‌تنهایی جهت خرید یا فروش تولید نمی‌کند.",
            "context": context}


def summarize_news_with_ai(symbol: str, article: Dict[str, Any], api_key: str = "",
                           model: str = "") -> Dict[str, Any]:
    """Optional structured AI summary. The model cannot emit an entry/exit command."""
    from . import coarena_ai, hybrid_ai
    prompt = (
        "این خبر را برای مرکز ریسک بازار خلاصه کن. هیچ دستور خرید/فروش صادر نکن. "
        "فقط JSON با کلیدهای summary_fa, affected_assets, event_type, risk_level, "
        "confidence, uncertainty بده. confidence عدد 0 تا 100 است. اگر داده کافی نیست، "
        "uncertainty را توضیح بده.\n"
        f"نماد مورد بررسی: {symbol}\n"
        f"منبع: {article.get('source')}\nعنوان: {article.get('title')}\n"
        f"خلاصهٔ خبر: {article.get('summary')}"
    )
    raw = coarena_ai.chat(
        api_key=api_key, model=model or coarena_ai.DEFAULT_MODEL,
        messages=[{"role": "system", "content": "Return valid JSON only. Never provide a trade instruction."},
                  {"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=700, retries=2, timeout=45,
    )
    parsed = hybrid_ai._extract_json_object(raw)
    forbidden = {"trade", "order", "signal", "side", "entry", "exit", "buy", "sell"}
    if forbidden.intersection(str(k).lower() for k in parsed):
        raise ValueError("خروجی AI شامل فیلد معاملاتی غیرمجاز است")
    summary = str(parsed.get("summary_fa") or "").strip()
    uncertainty = str(parsed.get("uncertainty") or "").strip()
    assets = parsed.get("affected_assets")
    event_type = str(parsed.get("event_type") or "").strip().lower()
    risk_level = str(parsed.get("risk_level") or "").strip().lower()
    confidence_raw = parsed.get("confidence")
    if isinstance(confidence_raw, bool):
        raise ValueError("confidence خبر نامعتبر است")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("confidence خبر نامعتبر است")
    if not math.isfinite(confidence) or not 0 <= confidence <= 100:
        raise ValueError("confidence خبر خارج از محدوده است")
    if not summary or not uncertainty or not isinstance(assets, list):
        raise ValueError("فیلدهای ضروری خلاصهٔ خبر ناقص هستند")
    if risk_level not in ("low", "moderate", "high", "unknown"):
        raise ValueError("سطح ریسک خبر نامعتبر است")
    if event_type not in {"general", "security", "solvency", "outage", "regulatory",
                          "macro", "market_structure", "other"}:
        raise ValueError("نوع رویداد خبر نامعتبر است")
    clean_assets = [str(x).upper().strip()[:24] for x in assets[:20] if str(x).strip()]
    return {"symbol": symbol, "article_id": article.get("id"),
            "source": article.get("source"), "source_domain": article.get("source_domain"),
            "published_at": article.get("published_at"),
            "analysis": {"summary_fa": summary[:1200], "affected_assets": clean_assets,
                         "event_type": event_type, "risk_level": risk_level,
                         "confidence": confidence, "uncertainty": uncertainty[:500]},
            "model": model or coarena_ai.DEFAULT_MODEL,
            "execution_allowed": False}


def clear_cache() -> None:
    with _lock:
        _cache.update({"at": 0.0, "items": [], "errors": [], "fetched_at": None, "fetched": False})
