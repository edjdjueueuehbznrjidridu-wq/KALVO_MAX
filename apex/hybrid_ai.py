# -*- coding: utf-8 -*-
"""KALVO Hybrid Intelligence: local coach + optional Coarena cloud models."""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from apex import market_assistant as local
from apex import coarena_ai as cloud


SYSTEM_FA = (
    "تو تحلیل‌گر و دستیار بازار چنددارایی برای آزمایشگاه آموزشی KALVO هستی؛ "
    "با رمزارزها، طلا، نقره، طلای ۱۸ عیار ایران، نفت خام و قراردادهای آتی کار می‌کنی. "
    "پاسخ‌ها فارسی، دقیق، شفاف و بدون وعدهٔ سود یا پیش‌بینی قطعی باشند. "
    "واحد، ارز مظنه، نوع بازار (نقدی/آتی)، منبع، زمان و وضعیت تازگی داده را دقیق رعایت کن. "
    "هرگز قیمت هر گرم طلای ایران را با قیمت هر اونس جهانی مقایسهٔ مستقیم نکن؛ "
    "فرمول تبدیل، عیار، نرخ ارز و هر ضریب تعدیل را توضیح بده. "
    "قیمت‌های روزانه، تأخیری، برآوردی و زمان‌تأییدنشده را لحظه‌ای معرفی نکن. "
    "قیمت نقدی را با قرارداد آتی یکی ندان و اگر دادهٔ کافی نیست صریح بگو. "
    "از دادهٔ تکنیکال و وضعیت حساب دمو که به تو داده می‌شود استفاده کن. "
    "قیمت، حمایت، مقاومت، روند یا رویداد را از خودت نساز؛ اگر در دادهٔ ورودی نیست، آن را تأییدنشده اعلام کن. "
    "اگر timestamp، bid/ask، کندل بسته‌شده یا منبع معتبر وجود ندارد، وضعیت را DATA-INSUFFICIENT اعلام کن و سیگنال قطعی نساز. "
    "تحلیل را با تفکیک تایم‌فریم، رژیم بازار، شواهد موافق و مخالف، سناریوی صعودی و نزولی، شرط ابطال، ریسک رویداد و محدودیت داده ارائه کن. "
    "در پایان یکی از وضعیت‌های آموزشی LONG-BIAS، SHORT-BIAS یا NO-TRADE را فقط با ذکر شواهد و عدم‌قطعیت بیاور؛ این وضعیت سفارش یا توصیهٔ مالی واقعی نیست. "
    "برای پرسش دربارهٔ استراتژی، خط روند یا نمودار، ابتدا داده و بازهٔ زمانی موجود را مشخص کن و سپس قواعد را توضیح بده؛ سطحی را که از ورودی قابل محاسبه نیست حدس نزن. "
    "حافظهٔ معاملات بسته‌شده فقط آمار توصیفی است، نه احتمال موفقیت آینده و نه مجوز افزایش ریسک. "
    "هرگز ادعا نکن سفارش واقعی به صرافی می‌فرستی؛ همهٔ معاملات KALVO مجازی/آموزشی‌اند."
)


_ANALYSIS_KEYS = (
    "signal", "trend", "market_regime", "confluence_score", "directional_score",
    "rsi14", "macd_hist", "adx14", "atr14", "atr_pct", "volatility_state",
    "ema20", "ema50", "ema200", "bb_width_pct", "volume_ratio", "momentum_5_pct",
    "support_50", "resistance_50", "warning",
)


def local_answer(question: str, ctx: Optional[dict] = None) -> str:
    ctx = ctx or {}
    analysis = ctx.get("analysis")
    try:
        return local.answer_question(
            question, analysis=analysis, ticker=ctx.get("ticker"),
            positions=ctx.get("positions"), price_map=ctx.get("price_map"),
            weekly_report=ctx.get("weekly_report"), auto_report=ctx.get("auto_report"),
            learning_profile=ctx.get("learning_profile"),
        )
    except TypeError:
        return local.answer_question(question)



def cloud_available(api_key: str = "") -> bool:
    try:
        key = cloud.resolve_key(api_key)
    except Exception:
        key = api_key or ""
    return bool(key and len(key.strip()) >= 8)


def _json_safe(value: Any, depth: int = 0) -> Any:
    """Normalize prompt context so NaN/Inf or unusual objects cannot break AI calls."""
    if depth > 10:
        return "[depth-limit]"
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v, depth + 1) for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v, depth + 1) for v in list(value)[:100]]
    return str(value)[:300]


def _compact_json(value: Any, limit: int) -> str:
    try:
        return json.dumps(_json_safe(value), ensure_ascii=False, allow_nan=False)[:limit]
    except Exception:
        return "{}"


def build_context_block(
    symbol: str,
    analysis: Optional[dict],
    ticker: Optional[dict],
    positions: Optional[list],
    wallet: Optional[dict],
    learning_summary: Optional[str] = None,
    share_account: bool = False,
) -> str:
    """Build the cloud prompt context.

    Privacy: by default NO per-position or balance details leave the device; only
    market data and aggregate counts are sent. Set share_account=True (setting
    ``ai_share_account=1``) to include positions and equity.
    """
    parts: List[str] = [f"نماد: {symbol}"]
    if ticker:
        parts.append(
            "اطلاعات ساخت‌یافتهٔ مظنه (واحدها را دقیق حفظ کن): "
            f"نام: {ticker.get('name_fa') or ticker.get('base')}; "
            f"نماد: {ticker.get('base')}; دسته: {ticker.get('category')}; "
            f"بازار: {ticker.get('market')}; "
            f"قیمت دفترکل دمو: {ticker.get('price')} {ticker.get('paper_quote_currency') or 'USDT'}؛ "
            f"قیمت بومی: {ticker.get('display_price', ticker.get('native_price'))} "
            f"{ticker.get('display_currency') or ticker.get('native_currency') or ticker.get('quote_currency')} "
            f"به‌ازای {ticker.get('display_unit') or ticker.get('unit')}; "
            f"منبع: {ticker.get('source')}; وضعیت: {ticker.get('status')}; "
            f"کهنه: {ticker.get('stale')}; سن داده به ثانیه: {ticker.get('age_seconds')}; "
            f"زمان دریافت محلی: {ticker.get('retrieved_at') or ticker.get('observed_at')}; "
            f"زمان ارائه‌دهنده: {ticker.get('provider_ts')}; "
            f"کیفیت timestamp: {ticker.get('timestamp_quality')}; "
            f"تغییر: {ticker.get('change_pct')}; "
            f"روش قیمت‌گذاری: {ticker.get('pricing_method')}; "
            f"نرخ تبدیل/واحد مشتق: {ticker.get('paper_conversion') or ticker.get('conversion')}"
        )
        if ticker.get("data_quality"):
            parts.append("کیفیت دادهٔ چندبازه‌ای: " + _compact_json(ticker.get("data_quality"), 900))
        if ticker.get("decision_context"):
            parts.append("زمینهٔ کامل تصمیم خودکار (فقط داده و محدودیت‌ها؛ نه دستور اجرا): " +
                         _compact_json(ticker.get("decision_context"), 2400))
        if ticker.get("news_context"):
            parts.append("زمینهٔ خبرِ منبع‌دار؛ صرفاً برای ارزیابی ریسک و نه پیش‌بینی جهت: " +
                         _compact_json(ticker.get("news_context"), 1800))
        if ticker.get("market_note"):
            parts.append("محدودیت/ماهیت این ابزار: " + str(ticker.get("market_note")))
        if ticker.get("synthetic_future"):
            parts.append("این نماد قرارداد آتی واقعی نیست؛ فقط شاخص تمرینی متصل به قیمت نقدی زیرین است.")
        if ticker.get("category") == "fx_cross":
            parts.append("نرخ FX این جفت از تقسیم دو نرخ آزاد محلی برحسب تومان ساخته شده و نرخ بین‌بانکی یا بروکری جهانی نیست.")
        if ticker.get("formula"):
            parts.append(
                "روش محاسبهٔ قیمت داخلی: " + str(ticker.get("formula")) +
                f"؛ منبع ارز: {ticker.get('fx_source')}; "
                f"وضعیت نرخ ارز: {ticker.get('fx_status')}; کیفیت زمان FX: {ticker.get('fx_timestamp_quality')}; "
                f"تعدیل پایه (bps): {ticker.get('adjustment_bps')}; "
                f"قیمت‌گذاری برآوردی: {ticker.get('pricing_method')}; "
                f"هزینهٔ اجرت/مالیات/فروشگاه لحاظ شده: {ticker.get('retail_costs_included')}"
            )
        if ticker.get("daily_data"):
            parts.append("این مظنه دادهٔ روزانه/با تأخیر است؛ آن را تیک‌به‌تیک یا لحظه‌ای توصیف نکن.")
        if ticker.get("timestamp_quality") == "polled_at":
            parts.append("منبع زمان رویداد بازار نداده؛ زمان ثبت‌شده فقط زمان دریافت از API است.")
    if analysis:
        parts.append("تحلیل تکنیکال (خلاصه):")
        for k in _ANALYSIS_KEYS:
            if analysis.get(k) is not None:
                parts.append(f"  - {k}: {analysis[k]}")
        # dump compact remaining keys
        extra = {k: v for k, v in analysis.items() if k not in set(_ANALYSIS_KEYS) | {"candles"}
                 and not isinstance(v, (list, dict))}
        if extra:
            parts.append("  - سایر: " + json.dumps(extra, ensure_ascii=False)[:500])
    if share_account:
        if positions:
            parts.append(f"پوزیشن‌های باز دمو ({len(positions)}):")
            for p in positions[:12]:
                parts.append(
                    f"  - {p.get('symbol')} {p.get('side')} qty={p.get('qty')} "
                    f"entry={p.get('entry')} lev={p.get('leverage')} "
                    f"tp={p.get('tp')} sl={p.get('sl')} uPnL={p.get('upnl')}"
                )
        if wallet:
            parts.append(
                f"اکوئیتی دمو: {wallet.get('equity')} | آزاد: {wallet.get('free')} | "
                f"قفل: {wallet.get('locked')}"
            )
    elif positions:
        parts.append(f"تعداد پوزیشن‌های باز دمو: {len(positions)} (جزئیات حساب ارسال نمی‌شود)")
    if learning_summary:
        parts.append("یادگیری از تاریخچه: " + learning_summary[:400])
    return "\n".join(parts)


def cloud_analyze(
    api_key: str,
    model: str,
    symbol: str,
    user_prompt: str,
    context_block: str,
    retries: int = 3,
) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_FA},
        {
            "role": "user",
            "content": (
                f"بافت بازار و حساب:\n{context_block}\n\n"
                f"درخواست کاربر دربارهٔ {symbol}:\n{user_prompt}\n\n"
                "گزارش را با این ترتیب و تیترهای روشن ارائه کن: "
                "۱) کیفیت و تازگی داده و هر محدودیت منبع؛ "
                "۲) رژیم بازار و ساختار روند در تایم‌فریم‌های موجود؛ "
                "۳) شواهد صعودی و نزولی، جدا از تفسیر؛ "
                "۴) حمایت/مقاومت فقط اگر در ورودی محاسبه شده‌اند؛ "
                "۵) سناریوی صعود، سناریوی نزول و شرط ابطال هرکدام؛ "
                "۶) رویدادهای خبری/اقتصادی مرتبط و عدم‌قطعیت آن‌ها؛ "
                "۷) وضعیت نهایی LONG-BIAS / SHORT-BIAS / NO-TRADE / DATA-INSUFFICIENT؛ "
                "۸) طرح فرضی مدیریت موقعیت دفترکل دمو و ریسک‌های باقی‌مانده. "
                "هرگز دادهٔ گمشده را حدس نزن، از یک تیتر خبر جهت قیمت استنتاج نکن، و سفارش واقعی یا توصیهٔ قطعی صادر نکن."
            ),
        },
    ]
    last_err = None
    for attempt in range(max(1, retries)):
        try:
            return cloud.chat(
                api_key=api_key,
                messages=messages,
                model=model or cloud.DEFAULT_MODEL,
                temperature=0.35,
                max_tokens=4096,
                reasoning_effort="high",
                retries=2,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise RuntimeError(f"Coarena پس از {retries} تلاش ناموفق: {last_err}")


def hybrid_reply(
    mode: str,
    question: str,
    api_key: str,
    model: str,
    ctx: dict,
) -> Dict[str, Any]:
    """mode: local | cloud | both"""
    local_txt = local_answer(question, ctx)
    if mode == "local" or not cloud_available(api_key):
        return {"mode": "local", "text": local_txt, "cloud": None}
    try:
        cloud_txt = cloud_analyze(
            api_key=api_key,
            model=model or cloud.DEFAULT_MODEL,
            symbol=str(ctx.get("symbol") or "BTC"),
            user_prompt=question,
            context_block=ctx.get("context_block") or "",
            retries=3,
        )
        if mode == "cloud":
            return {"mode": "cloud", "text": cloud_txt, "cloud": cloud_txt, "local": local_txt}
        merged = (
            "—— هوش ابری KALVO ——\n"
            + cloud_txt
            + "\n\n—— پایگاه دانش محلی ——\n"
            + local_txt
        )
        return {"mode": "both", "text": merged, "cloud": cloud_txt, "local": local_txt}
    except Exception as exc:  # noqa: BLE001
        return {
            "mode": "local_fallback",
            "text": f"[ابر در دسترس نبود: {exc}]\n\n" + local_txt,
            "cloud": None,
            "local": local_txt,
        }


GATE_SYSTEM = (
    "You are a risk reviewer for a PAPER-trading simulator. You receive one candidate "
    "trade produced by rule-based analysis. You may only approve it, reduce its size, "
    "or veto it. You can never increase size. Reply with ONE JSON object only: "
    '{"verdict":"approve|reduce|veto","confidence":0-100,"reason":"<=140 chars"}'
)


AUTOPILOT_SYSTEM = (
    "You are the primary AI decision reviewer for a PAPER-trading simulator only; never "
    "claim or request real exchange execution. A candidate is only a hypothesis, not an "
    "instruction. Inspect source/timestamp quality, closed-candle multi-timeframe evidence, "
    "spread, volatility, regime, exposure and the supplied source-aware news risk context. "
    "Never invent missing market evidence, never treat a headline as directional proof, and "
    "never increase risk. If any key evidence is stale, unverified, conflicting, incomplete, "
    "or uncertain, veto. Approve only when the full context supports the proposed setup; "
    "reduce only when the setup remains valid but risk is elevated; veto otherwise. A side "
    "change is allowed only as a proposal and must still pass the independent local gate. "
    "size_mult must be between 0.25 and 1.0. Reply with ONE JSON object only: "
    '{"verdict":"approve|reduce|veto","side":"LONG|SHORT",'
    '"size_mult":0.25-1.0,"confidence":0-100,"reason":"<=140 chars"}'
)


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Extract the first complete JSON object without greedy-regex over-capture."""
    source = str(text or "")
    start = source.find("{")
    if start < 0:
        raise ValueError("JSON object not found")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(source[start:index + 1])
                if not isinstance(value, dict):
                    raise ValueError("JSON response must be an object")
                return value
    raise ValueError("Incomplete JSON object")


def parse_gate_reply(text: str) -> Dict[str, Any]:
    """Parse an AI gate reply strictly; malformed/range-invalid output fails closed."""
    try:
        data = _extract_json_object(text)
        verdict = str(data.get("verdict", "")).strip().lower()
        if verdict not in ("approve", "reduce", "veto"):
            raise ValueError("bad verdict")

        confidence_raw = data.get("confidence")
        if isinstance(confidence_raw, bool):
            raise ValueError("boolean confidence is invalid")
        confidence = float(confidence_raw)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 100.0:
            raise ValueError("confidence is outside 0..100")

        size_raw = data.get("size_mult", 1.0)
        if isinstance(size_raw, bool):
            raise ValueError("boolean size multiplier is invalid")
        size = float(size_raw)
        if not math.isfinite(size) or not 0.25 <= size <= 1.0:
            raise ValueError("size multiplier is outside 0.25..1.0")

        side = str(data.get("side", "")).strip().upper()
        if side and side not in ("LONG", "SHORT"):
            raise ValueError("invalid side")
        reason = str(data.get("reason", "")).strip()[:200]
        return {"ok": True, "verdict": verdict, "confidence": confidence,
                "side": side, "size_mult": size, "reason": reason,
                "side_present": "side" in data,
                "size_mult_present": "size_mult" in data}
    except Exception:
        return {"ok": False, "verdict": "unavailable", "confidence": 0,
                "side": "", "size_mult": 0.0,
                "reason": "پاسخ مدل ناقص، نامعتبر یا خارج از محدوده بود"}


def cloud_gate(api_key: str, model: str, context_block: str, symbol: str,
               side: str, score: int, regime: str, autopilot: bool = False) -> Dict[str, Any]:
    """Ask the cloud model to approve/reduce/veto ONE candidate. Never raises."""
    prompt = (
        f"Candidate: {symbol} {side}, rule-based confluence score {score}/100, "
        f"market regime {regime}.\nMarket context:\n{context_block}\n"
        "Return the JSON verdict now."
    )
    try:
        text = cloud.chat(
            api_key=api_key,
            messages=[{"role": "system", "content": AUTOPILOT_SYSTEM if autopilot else GATE_SYSTEM},
                      {"role": "user", "content": prompt}],
            model=model or cloud.DEFAULT_MODEL,
            temperature=0.1, max_tokens=300, retries=2, timeout=30,
        )
        return parse_gate_reply(text)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "verdict": "unavailable", "confidence": 0,
                "reason": f"ابر در دسترس نبود: {str(exc)[:80]}"}
