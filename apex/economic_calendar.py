# -*- coding: utf-8 -*-
"""Source-attributed economic calendar for BLS, Federal Reserve FOMC and ECB.

Coverage is intentionally bounded to official sources and is not presented as
a complete global macro calendar. Date-only meetings never receive invented times.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from datetime import date, datetime, time as dtime, timezone
from typing import Any, Dict, Iterable, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - minimal Android runtimes
    ZoneInfo = None

BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
ECB_URL = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
USER_AGENT = "KALVO/7.2 (Economic Calendar; source-attributed)"
CACHE_TTL_SECONDS = 1800
MAX_ICS_BYTES = 2 * 1024 * 1024
MAX_HTML_BYTES = 3 * 1024 * 1024
_lock = threading.RLock()
_cache: Dict[str, Any] = {"at": 0.0, "events": [], "fetched": False, "errors": []}


def _unfold_ics(text: str) -> List[str]:
    """Unfold RFC5545 continuation lines."""
    lines: List[str] = []
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return (str(value or "").replace(r"\n", "\n").replace(r"\N", "\n")
            .replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\").strip())


def _parse_dt(value: str, params: str = "") -> Dict[str, Any]:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Missing DTSTART")
    tz_match = re.search(r"(?:^|;)TZID=([^;:]+)", params, flags=re.I)
    tz_name = (tz_match.group(1).strip('"') if tz_match else "UTC")
    if raw.endswith("Z"):
        parsed = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        tz_name = "UTC"
    elif re.fullmatch(r"\d{8}", raw):
        day = datetime.strptime(raw, "%Y%m%d").date()
        zone = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
        parsed = datetime.combine(day, dtime.min, tzinfo=zone)
    else:
        parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S")
        zone = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
        parsed = parsed.replace(tzinfo=zone)
    return {"timestamp": int(parsed.timestamp()), "timezone": tz_name,
            "starts_at_utc": parsed.astimezone(timezone.utc).isoformat()}


def _importance(summary: str) -> str:
    title = str(summary or "").lower()
    if any(k in title for k in ("consumer price", "producer price", "employment situation",
                                "unemployment", "job openings", "payroll", "inflation")):
        return "high"
    if any(k in title for k in ("productivity", "earnings", "employment cost", "trade")):
        return "medium"
    return "low"


def parse_ics(payload: bytes | str, *, now: Optional[int] = None) -> List[Dict[str, Any]]:
    if isinstance(payload, bytes):
        text = payload.decode("utf-8-sig", errors="replace")
    else:
        text = str(payload or "")
    if len(text.encode("utf-8", errors="ignore")) > MAX_ICS_BYTES:
        raise ValueError("BLS calendar exceeds the 2 MiB safety limit")

    current = int(now if now is not None else time.time())
    events: List[Dict[str, Any]] = []
    current_event: Dict[str, Any] | None = None
    for line in _unfold_ics(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current_event = {}
            continue
        if upper == "END:VEVENT":
            if not current_event:
                continue
            summary = _unescape(current_event.get("SUMMARY", ""))[:240]
            dtstart = current_event.get("DTSTART")
            if not summary or not dtstart:
                current_event = None
                continue
            try:
                parsed = _parse_dt(dtstart[1], dtstart[0])
            except Exception:
                current_event = None
                continue
            ts = parsed["timestamp"]
            if ts < current - 12 * 60 * 60 or ts > current + 400 * 86400:
                current_event = None
                continue
            uid = _unescape(current_event.get("UID", ""))
            link = _unescape(current_event.get("URL", "")) or BLS_ICS_URL
            ident = hashlib.sha256((uid or f"{summary}|{ts}").encode("utf-8")).hexdigest()[:20]
            events.append({
                "id": ident,
                "title": summary,
                "starts_at": ts,
                "starts_at_utc": parsed["starts_at_utc"],
                "source_timezone": parsed["timezone"],
                "source": "U.S. Bureau of Labor Statistics",
                "source_id": "bls",
                "source_url": link,
                "calendar_url": BLS_ICS_URL,
                "importance": _importance(summary),
                "status": "scheduled",
                "retrieved_at": current,
            })
            current_event = None
            continue
        if current_event is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        prop = key.split(";", 1)[0].upper()
        params = key[len(prop):]
        if prop == "DTSTART":
            current_event[prop] = (params, value.strip())
        elif prop in {"SUMMARY", "UID", "URL", "DESCRIPTION"}:
            current_event[prop] = value.strip()

    # Stable dedup and chronological ordering.
    dedup = {e["id"]: e for e in events}
    return sorted(dedup.values(), key=lambda item: (item["starts_at"], item["title"]))



class _CalendarTextParser(HTMLParser):
    """Small stdlib HTML text extractor with block boundaries preserved."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if data:
            self.parts.append(data)


def _html_lines(payload: bytes | str) -> List[str]:
    raw = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload or "")
    parser = _CalendarTextParser()
    parser.feed(raw)
    text = "".join(parser.parts).replace("\xa0", " ")
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _date_only_event(title: str, source_id: str, source: str, source_url: str,
                     starts: datetime, *, ends: datetime | None = None,
                     importance: str = "medium", now: int | None = None,
                     precision: str = "date_only") -> Dict[str, Any] | None:
    current = int(now if now is not None else time.time())
    stamp = int(starts.timestamp())
    if stamp < current - 2 * 86400 or stamp > current + 400 * 86400:
        return None
    end_stamp = int(ends.timestamp()) if ends else None
    identity = hashlib.sha256(
        f"{source_id}|{title}|{stamp}|{end_stamp or ''}".encode("utf-8")
    ).hexdigest()[:20]
    return {
        "id": identity, "title": title[:240], "starts_at": stamp,
        "ends_at": end_stamp, "source_date": starts.strftime("%Y-%m-%d"),
        "source_end_date": ends.strftime("%Y-%m-%d") if ends else None,
        "starts_at_utc": starts.astimezone(timezone.utc).isoformat(),
        "source_timezone": getattr(starts.tzinfo, "key", "UTC"),
        "time_precision": precision, "source": source, "source_id": source_id,
        "source_url": source_url, "calendar_url": source_url,
        "importance": importance, "status": "scheduled", "retrieved_at": current,
    }


def parse_fomc_html(payload: bytes | str, *, now: Optional[int] = None) -> List[Dict[str, Any]]:
    """Parse official FOMC year/month/date blocks; meeting dates have no invented time."""
    lines = _html_lines(payload)
    current_year = None
    current_month = None
    months = {name.lower(): index for index, name in enumerate((
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ), 1)}
    events = []
    zone = ZoneInfo("America/New_York") if ZoneInfo else timezone.utc
    for line in lines:
        year_match = re.search(r"\b(20\d{2})\s+FOMC Meetings\b", line, re.I)
        if year_match:
            current_year = int(year_match.group(1))
            current_month = None
            continue
        if current_year is None:
            continue
        month_text = line.strip().lower()
        if month_text in months:
            current_month = months[month_text]
            continue
        if month_text in ("apr/may", "jan/feb"):
            # Cross-month legacy entries are historical on the current official page;
            # do not guess their start month for future calendar entries.
            current_month = None
            continue
        date_match = re.fullmatch(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\*?", line.strip())
        if not date_match or current_month is None:
            continue
        first_day = int(date_match.group(1))
        last_day = int(date_match.group(2) or first_day)
        try:
            start = datetime(current_year, current_month, first_day, tzinfo=zone)
            end = datetime(current_year, current_month, last_day, tzinfo=zone)
        except ValueError:
            continue
        title = f"جلسهٔ کمیتهٔ بازار آزاد فدرال رزرو (FOMC) · {current_year}-{current_month:02d}-{first_day:02d} تا {last_day:02d}"
        event = _date_only_event(
            title, "fomc", "Federal Reserve · FOMC", FOMC_URL,
            start, ends=end, importance="high", now=now, precision="date_range",
        )
        if event:
            events.append(event)
    return sorted(events, key=lambda item: item["starts_at"])


def parse_ecb_html(payload: bytes | str, *, now: Optional[int] = None) -> List[Dict[str, Any]]:
    """Parse official ECB Governing/General Council meeting dates as date-only."""
    lines = _html_lines(payload)
    zone = ZoneInfo("Europe/Brussels") if ZoneInfo else timezone.utc
    events = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", line.strip())
        if not match:
            continue
        try:
            day, month, year = map(int, match.groups())
            starts = datetime(year, month, day, tzinfo=zone)
        except ValueError:
            continue
        title = next((x for x in lines[index + 1:index + 4] if len(x) > 12), "نشست بانک مرکزی اروپا")
        lowered = title.lower()
        important = (("monetary policy" in lowered and "non-monetary policy" not in lowered)
                    or "press conference" in lowered)
        event = _date_only_event(
            title, "ecb", "European Central Bank", ECB_URL, starts,
            importance="high" if important else "medium", now=now,
            precision="date_only",
        )
        if event:
            events.append(event)
    dedup = {row["id"]: row for row in events}
    return sorted(dedup.values(), key=lambda item: (item["starts_at"], item["title"]))


def _fetch_html(url: str, timeout: int = 9) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*",
    })
    with urllib.request.urlopen(request, timeout=max(3, min(15, int(timeout)))) as response:
        payload = response.read(MAX_HTML_BYTES + 1)
    if len(payload) > MAX_HTML_BYTES:
        raise ValueError("Calendar HTML exceeds the 3 MiB safety limit")
    return payload

def _fetch_ics(timeout: int = 9) -> bytes:
    request = urllib.request.Request(BLS_ICS_URL, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/calendar,text/plain,*/*",
    })
    with urllib.request.urlopen(request, timeout=max(3, min(15, int(timeout)))) as response:
        body = response.read(MAX_ICS_BYTES + 1)
    if len(body) > MAX_ICS_BYTES:
        raise ValueError("BLS calendar exceeds the 2 MiB safety limit")
    return body


def fetch_calendar(force_refresh: bool = False, *, now: Optional[int] = None,
                   fetcher=None) -> Dict[str, Any]:
    current = int(now if now is not None else time.time())
    mono = time.monotonic()
    with _lock:
        if not force_refresh and _cache.get("fetched") and mono - float(_cache.get("at") or 0) < CACHE_TTL_SECONDS:
            return {"events": list(_cache["events"]), "errors": list(_cache["errors"]),
                    "cached": True, "fetched_at": _cache.get("fetched_at"),
                    "coverage": "BLS releases + Federal Reserve FOMC + ECB Council schedules"}

    events: List[Dict[str, Any]] = []
    errors: List[str] = []
    if fetcher is not None:
        # Deterministic injection path used by parser/cache tests.
        try:
            events.extend(parse_ics(fetcher(), now=current))
        except Exception as exc:
            errors.append(f"BLS: {str(exc)[:140]}")
    else:
        tasks = {
            "bls": (lambda: parse_ics(_fetch_ics(), now=current)),
            "fomc": (lambda: parse_fomc_html(_fetch_html(FOMC_URL), now=current)),
            "ecb": (lambda: parse_ecb_html(_fetch_html(ECB_URL), now=current)),
        }
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="KALVO-calendar") as pool:
            futures = {pool.submit(fn): source for source, fn in tasks.items()}
            for future in as_completed(futures):
                source = futures[future]
                try:
                    events.extend(future.result())
                except Exception as exc:
                    errors.append(f"{source.upper()}: {str(exc)[:140]}")

    dedup = {event["id"]: event for event in events}
    ordered = sorted(dedup.values(), key=lambda item: (item["starts_at"], item["title"]))
    with _lock:
        previous = list(_cache.get("events") or [])
        # A complete upstream outage must not erase the last known schedule.
        # Stale rows are retained but explicitly identified as cached fallback.
        all_sources_failed = bool(errors) and not ordered
        if all_sources_failed and previous:
            fallback = [dict(row, cache_fallback=True) for row in previous]
            return {"events": fallback, "errors": errors, "cached": True,
                    "cache_fallback": True, "fetched_at": _cache.get("fetched_at"),
                    "coverage": "BLS releases + Federal Reserve FOMC + ECB Council schedules"}
        _cache.update({"at": time.monotonic(), "events": ordered, "errors": errors,
                       "fetched_at": current, "fetched": True})
    return {"events": list(ordered), "errors": list(errors), "cached": False,
            "cache_fallback": False, "fetched_at": current,
            "coverage": "BLS releases + Federal Reserve FOMC + ECB Council schedules"}


def upcoming_events(limit: int = 12, *, now: Optional[int] = None,
                    force_refresh: bool = False) -> Dict[str, Any]:
    result = fetch_calendar(force_refresh=force_refresh, now=now)
    current = int(now if now is not None else time.time())
    rows = [e for e in result["events"] if int(e.get("starts_at") or 0) >= current - 12 * 3600]
    result["events"] = rows[:max(1, min(50, int(limit)))]
    return result


def clear_cache() -> None:
    with _lock:
        _cache.update({"at": 0.0, "events": [], "fetched": False, "errors": [], "fetched_at": None})
