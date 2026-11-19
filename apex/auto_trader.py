# -*- coding: utf-8 -*-
"""KALVO paper-only multi-timeframe scanner.

The module is intentionally limited to the local virtual ledger. It does not
contain exchange order endpoints, account credentials, or live execution code.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from . import analysis_engine as analysis
from . import market_apis as market
from . import market_data_hub as data_hub
from . import decision_engine
from . import news_intelligence
from . import storage as store
from . import learning as learning_engine
try:
    from . import learning_memory as mem
except Exception:
    mem = None
from . import ai_core
from . import instrument_catalog as instruments
from .config import get_env

WATCHLIST = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "LINK", "AVAX", "ADA", "DOT")
TIMEFRAMES = ("15min", "1hour", "4hour")
MAX_AUTO_POSITIONS = 2
MAX_LEVERAGE = 2.0
COOLDOWN_SECONDS = 4 * 60 * 60


def _stored_number(key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(store.get_setting(key, str(default)))
        if not math.isfinite(value):
            return default
        return max(low, min(high, value))
    except (TypeError, ValueError):
        return default


def get_auto_config() -> Dict[str, Any]:
    """Read and clamp the user's demo-only auto-scan preferences."""
    max_positions = int(_stored_number("auto_max_positions", 3, 1, 4))
    risk_pct = _stored_number("auto_risk_pct", 0.30, 0.10, 0.50)
    min_confluence = int(_stored_number("auto_min_confluence", 68, 60, 90))
    max_leverage = _stored_number("auto_max_leverage", 2.0, 1.0, 2.0)
    cooldown_hours = _stored_number("auto_cooldown_hours", 4, 1, 24)
    scan_interval = int(_stored_number("auto_scan_interval", 90, 60, 300))
    return {
        "max_positions": max_positions,
        "risk_pct": risk_pct,
        "min_confluence": min_confluence,
        "max_leverage": max_leverage,
        "cooldown_hours": cooldown_hours,
        "cooldown_seconds": int(cooldown_hours * 3600),
        "scan_interval": scan_interval,
        "max_margin_pct_per_position": 2.0,
        "max_free_cash_pct_per_position": 4.0,
        "paper_slippage_bps": _stored_number("paper_slippage_bps", 2.0, 0.0, 50.0),
    }


def _ai_gate_review(item: Dict[str, Any], assessment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Optional cloud second opinion. Returns None when the gate is off/unconfigured.

    The cloud model may only approve, reduce or veto; failure never blocks or
    enlarges a trade (the local rules stand).
    """
    from .defaults import AI_GATE_DEFAULT
    mode = (store.get_setting("ai_gate", AI_GATE_DEFAULT) or AI_GATE_DEFAULT).lower()
    if mode not in ("advisory", "veto", "autopilot"):
        return None
    key = (store.get_setting("coarena_api_key", "") or get_env("COARENA_API_KEY") or "").strip()
    if len(key) < 8:
        if mode == "autopilot":  # fail-closed: no key -> no AI decision -> no entry
            return {"ok": False, "verdict": "unavailable", "confidence": 0,
                    "reason": "کلید Coarena تنظیم نشده", "mode": mode}
        return None
    try:
        from . import hybrid_ai
        hourly = item["timeframes"]["1hour"]
        ticker_context = dict(item.get("ticker") or {})
        ticker_context.update({"price": item.get("price"), "source": item.get("ticker_source")})
        ticker_context["data_quality"] = item.get("data_quality")
        ticker_context["news_context"] = (item.get("news_gate") or {}).get("context")
        ticker_context["decision_context"] = {
            "consensus": item.get("consensus"),
            "closed_timeframes": {
                tf: {k: frame.get(k) for k in (
                    "signal", "confluence_score", "rsi14", "adx14", "atr14",
                    "volatility_ratio", "market_regime", "last_candle_ts", "fresh", "source"
                ) if frame.get(k) is not None}
                for tf, frame in (item.get("timeframes") or {}).items()
            },
            "local_assessment": assessment,
            "news_gate_status": (item.get("news_gate") or {}).get("status"),
            "risk_limits": {"max_leverage": MAX_LEVERAGE,
                            "max_auto_positions": MAX_AUTO_POSITIONS,
                            "execution_scope": "paper_only"},
        }
        ctx = hybrid_ai.build_context_block(
            symbol=item["symbol"], analysis=hourly,
            ticker=ticker_context,
            positions=None, wallet=None, share_account=False)
        out = hybrid_ai.cloud_gate(
            key, store.get_setting("coarena_model", ""), ctx, item["symbol"],
            item["consensus"]["direction"], int(item.get("score") or 0),
            assessment.get("regime_label", ""), autopilot=(mode == "autopilot"))
    except Exception as exc:  # noqa: BLE001
        out = {"ok": False, "verdict": "unavailable", "confidence": 0,
               "reason": str(exc)[:80]}
    out["mode"] = mode
    return out


def _autopilot_block_reason(gate: Dict[str, Any]) -> Optional[str]:
    """Fail-closed validation for the AI-primary paper-entry decision."""
    if not isinstance(gate, dict) or not gate.get("ok"):
        return "اتوپایلوت AI: پاسخ مدل در دسترس نبود؛ بدون تصمیم AI ورودی انجام نمی‌شود"
    try:
        confidence = float(gate.get("confidence", 0))
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    if isinstance(gate.get("confidence"), bool) or not math.isfinite(confidence) or confidence < 70 or confidence > 100:
        return "اتوپایلوت AI: اطمینان معتبر حداقل ۷۰٪ دریافت نشد؛ ورود متوقف شد"
    verdict = str(gate.get("verdict") or "").strip().lower()
    if verdict not in ("approve", "reduce", "veto"):
        return "اتوپایلوت AI: پاسخ تصمیم معتبر نیست؛ ورود متوقف شد"
    if verdict == "veto":
        return "مدل هوش مصنوعی ورود را وتو کرد: " + str(gate.get("reason") or "ریسک نامشخص")[:140]
    if not gate.get("side_present") or str(gate.get("side") or "").upper() not in ("LONG", "SHORT"):
        return "اتوپایلوت AI: جهت صریح LONG/SHORT در پاسخ مدل وجود ندارد؛ ورود متوقف شد"
    if not gate.get("size_mult_present"):
        return "اتوپایلوت AI: ضریب اندازهٔ صریح در پاسخ مدل وجود ندارد؛ ورود متوقف شد"
    try:
        if isinstance(gate.get("size_mult"), bool):
            raise ValueError("boolean size multiplier")
        size_mult = float(gate.get("size_mult"))
    except (TypeError, ValueError, OverflowError):
        size_mult = float("nan")
    if not math.isfinite(size_mult) or not 0.25 <= size_mult <= 1.0:
        return "اتوپایلوت AI: ضریب اندازه خارج از محدودهٔ مجاز است؛ ورود متوقف شد"
    return None


def _watch_symbols(symbols: Optional[List[str]]) -> List[str]:
    if symbols is not None:
        raw = symbols
        source = "explicit"
    else:
        saved = store.watchlist_list(20)
        raw = [row.get("symbol") for row in saved if row.get("symbol")] or list(WATCHLIST)
        source = "watchlist" if saved else "default"
    normalized: List[str] = []
    for item in raw:
        try:
            canonical = instruments.normalize_instrument_symbol(str(item))
            if instruments.is_instrument(canonical) or instruments.is_reference_symbol(canonical):
                continue
            base = market.normalize_symbol(str(item))[0]
        except (TypeError, ValueError):
            continue
        if base not in normalized:
            normalized.append(base)
        if len(normalized) >= 10:
            break
    if normalized:
        return normalized
    # An explicit or saved commodity-only watchlist must not silently fall back
    # to a different set of crypto symbols.
    return [] if source in ("explicit", "watchlist") else list(WATCHLIST)


def inspect_symbol(symbol: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Build a provider-consistent, completed-candle snapshot; never opens a position."""
    current = float(now if now is not None else time.time())
    canonical = instruments.normalize_instrument_symbol(symbol)
    if instruments.is_instrument(canonical) or instruments.is_reference_symbol(canonical):
        return {"symbol": canonical, "ready": False,
                "reason": "اسکن خودکار فقط روی رمزارزهای دارای دادهٔ قابل‌اعتبارسنجی اجرا می‌شود؛ سایر بازارها فعلاً فقط برای مشاهده و تمرین هستند."}

    bundle = data_hub.get_market_bundle(symbol, TIMEFRAMES, now=current)
    quote = bundle.get("quote") or {}
    if not bundle.get("trade_ready"):
        return {"symbol": symbol, "ready": False,
                "reason": "؛ ".join(bundle.get("problems") or ["کیفیت داده برای تصمیم خودکار کافی نیست"]),
                "data_quality": bundle}

    results = bundle.get("timeframes") or {}
    consensus = analysis.combine_timeframes(results)
    if not consensus.get("ready"):
        return {"symbol": symbol, "ready": False,
                "reason": consensus.get("reason") or "اجماع چندبازه‌ای آماده نیست",
                "data_quality": bundle}

    latest = {tf: float((results.get(tf) or {}).get("last_candle_ts") or 0)
              for tf in TIMEFRAMES}
    return {
        "symbol": str(symbol).upper(), "ready": True,
        "price": float(bundle["price"]),
        "ticker_source": quote.get("source"),
        "ticker_ts": float(quote.get("retrieved_at") or 0),
        "ticker": quote,
        "timeframes": results,
        "consensus": consensus,
        "last_candle_ts": latest,
        "data_quality": {k: bundle.get(k) for k in (
            "provider", "provider_consistent", "quality_score", "trade_ready", "problems"
        )},
    }


def _plan(symbol: str, side: str, price: float,
          hourly: Dict[str, Any], equity: float,
          free_cash: Optional[float] = None,
          risk_scale: float = 1.0,
          risk_pct: float = 0.30,
          max_leverage: float = MAX_LEVERAGE,
          max_margin_pct: float = 2.0,
          max_free_cash_pct: float = 4.0,
          spread_pct: float = 0.0,
          slippage_bps: float = 2.0) -> Optional[Dict[str, float]]:
    """Size a paper position including ATR risk, fees, spread and slippage."""
    try:
        atr = float(hourly.get("atr14") or 0)
        price = float(price)
        equity = float(equity)
        risk_scale = float(risk_scale)
        risk_pct = float(risk_pct)
        max_leverage = float(max_leverage)
    except (TypeError, ValueError, OverflowError):
        return None
    core_values = (atr, price, equity, risk_scale, risk_pct, max_leverage)
    if (not all(math.isfinite(value) for value in core_values)
            or atr <= 0 or price <= 0 or equity <= 0
            or side not in ("LONG", "SHORT")):
        return None
    stop_distance = max(atr * 1.7, price * 0.003)
    risk_scale = max(0.0, min(1.0, risk_scale))
    risk_pct = max(0.10, min(0.50, risk_pct))
    max_leverage = max(1.0, min(MAX_LEVERAGE, max_leverage))
    if risk_scale <= 0:
        return None
    try:
        spread_pct = float(spread_pct)
        slippage_bps = float(slippage_bps)
    except (TypeError, ValueError, OverflowError):
        return None
    if (not math.isfinite(spread_pct) or not 0.0 <= spread_pct <= 5.0
            or not math.isfinite(slippage_bps) or not 0.0 <= slippage_bps <= 50.0):
        return None
    risk_budget = equity * (risk_pct / 100.0) * risk_scale
    # Conservative round-trip reserve: one spread, two slippage legs, and fees.
    cost_per_unit = price * (spread_pct / 100.0 + 2.0 * slippage_bps / 10000.0 + 2.0 * store.FEE_RATE)
    risk_per_unit = stop_distance + cost_per_unit
    notional_from_risk = risk_budget * price / risk_per_unit
    notional_cap = equity * 0.12 * risk_scale
    notional = min(notional_from_risk, notional_cap)
    leverage = max_leverage
    if free_cash is None:
        free_cash = float(store.get_wallet().get("balance") or 0)
    try:
        free_cash = float(free_cash)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(free_cash):
        return None
    free_cash = max(0.0, free_cash)
    margin = min(notional / leverage,
                 free_cash * max(0.5, min(10.0, max_free_cash_pct)) / 100.0,
                 equity * max(0.5, min(5.0, max_margin_pct)) / 100.0)
    if margin < 0.25:
        return None
    notional = margin * leverage
    quantity = notional / price
    actual_risk = quantity * risk_per_unit
    if actual_risk > risk_budget * 1.001:
        return None
    if side == "LONG":
        sl = price - stop_distance
        tp1 = price + stop_distance
        tp2 = price + stop_distance * 1.8
    else:
        sl = price + stop_distance
        tp1 = price - stop_distance
        tp2 = price - stop_distance * 1.8
    if min(sl, tp1, tp2) <= 0:
        return None
    trail_pct = max(0.25, min(3.0, stop_distance / price * 100.0 * 0.8))
    return {"leverage": leverage, "margin": margin, "sl": sl,
            "tp1": tp1, "tp2": tp2, "trail_pct": trail_pct,
            "risk_budget": risk_budget, "actual_risk": actual_risk,
            "stop_distance": stop_distance, "cost_per_unit": cost_per_unit,
            "spread_pct": spread_pct, "slippage_bps": slippage_bps,
            "quantity": quantity, "notional": notional}


def _scan_once_impl(symbols: Optional[List[str]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Run one watchlist scan and open only validated local demo positions."""
    stamp = float(now if now is not None else time.time())
    watch = _watch_symbols(symbols)
    config = get_auto_config()
    existing = store.list_open_positions()
    auto_open = [p for p in existing if p.get("source") == "auto"]
    profile = learning_engine.adaptive_profile()
    effective_min_score = max(config["min_confluence"], int(profile.get("min_confluence") or 70))
    result: Dict[str, Any] = {
        "checked": 0, "opened": [], "skipped": [], "evaluated": [],
        "adaptive_profile": profile, "config": config, "watchlist": watch,
        "message": "اسکن انجام نشد", "generated_ts": int(stamp),
    }
    if not watch:
        result["message"] = "در واچ‌لیست نماد واجد شرایطی برای اسکن خودکار وجود ندارد؛ فلزات و نفت فقط دستی و دمو هستند."
        return result
    if mem is not None:
        try:
            mem.refresh_if_new_trades()  # automatic retraining when a trade closed
        except Exception:
            pass
    dd = ai_core.drawdown_state()
    result["drawdown"] = dd
    if profile.get("auto_paused"):
        result["message"] = profile.get("note") or "اسکن خودکار دمو موقتاً متوقف است"
        result["paused"] = True
        return result
    if dd.get("paused"):
        result["message"] = (f"محافظ سرمایه: افت {dd['drawdown_pct']:.1f}% از قله؛ "
                             "اسکنر خودکار دمو تا بازبینی ژورنال متوقف است")
        result["paused"] = True
        return result
    if len(auto_open) >= config["max_positions"]:
        result["message"] = f"سقف {config['max_positions']} پوزیشن خودکار دمو از قبل پر است"
        result["capacity_full"] = True
        return result
    equity = float(store.get_wallet().get("balance") or 0)
    if equity <= 0:
        result["message"] = "موجودی آزاد دمو کافی نیست"
        return result

    occupied = set()
    for position in existing:
        try:
            occupied.add(market.normalize_symbol(position.get("symbol", ""))[1])
        except ValueError:
            continue

    candidates = []
    with ThreadPoolExecutor(max_workers=min(5, len(watch) or 1)) as pool:
        jobs = {pool.submit(inspect_symbol, symbol, stamp): symbol for symbol in watch}
        for future in as_completed(jobs):
            symbol = jobs[future]
            result["checked"] += 1
            try:
                item = future.result()
            except Exception as exc:
                reason = str(exc)[:100]
                result["skipped"].append((symbol, reason))
                result["evaluated"].append({"symbol": symbol, "status": "skip", "reason": reason})
                continue
            if not item.get("ready"):
                reason = item.get("reason", "داده کافی نیست")
                result["skipped"].append((symbol, reason))
                result["evaluated"].append({"symbol": symbol, "status": "skip", "reason": reason})
                continue
            consensus = item.get("consensus") or {}
            if not consensus.get("aligned"):
                reason = "سه بازهٔ ۱۵ دقیقه، ۱ ساعت و ۴ ساعت هم‌جهت نیستند"
                result["skipped"].append((symbol, reason))
                result["evaluated"].append({"symbol": symbol, "status": "skip", "reason": reason})
                continue
            news_gate = news_intelligence.evaluate_entry_gate(
                item["symbol"], consensus.get("direction"), now=int(stamp)
            )
            decision = decision_engine.evaluate_entry(
                item, minimum_score=effective_min_score, news_gate=news_gate
            )
            item["news_gate"] = news_gate
            item["decision"] = decision
            if not decision.get("allow"):
                reason = "؛ ".join(decision.get("reasons") or ["دروازهٔ تصمیم ورود را رد کرد"])
                result["skipped"].append((symbol, reason))
                result["evaluated"].append({
                    "symbol": symbol, "status": "rejected_by_decision_engine",
                    "reason": reason, "score": decision.get("score"),
                    "warnings": decision.get("warnings", []),
                })
                ai_core.log_decision({
                    "symbol": item["symbol"], "side": consensus.get("direction"),
                    "action": "rejected_by_decision_engine", "score": decision.get("score"),
                    "quality": decision.get("quality_score"), "why": reason[:180],
                })
                continue

            pair = market.normalize_symbol(item["symbol"])[1]
            if pair in occupied:
                reason = "برای این نماد پوزیشن باز وجود دارد"
                result["skipped"].append((symbol, reason))
                result["evaluated"].append({"symbol": symbol, "status": "skip", "reason": reason})
                continue
            if store.auto_cooldown_active(pair, config["cooldown_seconds"]):
                reason = f"وقفهٔ ایمنی {config['cooldown_hours']:g} ساعته هنوز تمام نشده"
                result["skipped"].append((symbol, reason))
                result["evaluated"].append({"symbol": symbol, "status": "skip", "reason": reason})
                continue
            score = min(int(item["timeframes"][tf].get("confluence_score") or 0) for tf in TIMEFRAMES)
            if score < effective_min_score:
                reason = f"امتیاز هم‌گرایی {score} از حداقل لازم {effective_min_score} کمتر است"
                result["skipped"].append((symbol, reason))
                result["evaluated"].append({"symbol": symbol, "status": "skip", "reason": reason,
                                            "score": score})
                continue
            assessment = ai_core.assess_candidate(
                symbol, consensus.get("direction"), score,
                item["timeframes"]["1hour"], mem)
            item["assessment"] = assessment
            if not assessment["allow"] or score < effective_min_score + assessment["extra_score"]:
                why = " ".join(assessment["explain"][-1:]) or "رد شده توسط هستهٔ هوش"
                result["skipped"].append((symbol, why))
                result["evaluated"].append({"symbol": symbol, "status": "skip", "reason": why,
                                            "score": score, "explain": assessment["explain"]})
                ai_core.log_decision({"symbol": symbol, "side": consensus.get("direction"),
                                      "action": "rejected_by_core", "regime": assessment["regime"],
                                      "score": score, "why": why})
                continue
            boost = 0.0
            if mem is not None:
                try:
                    boost = max(0.0, float(mem.symbol_priority_boost(symbol))) * 8.0  # rank only
                except Exception:
                    boost = 0.0
            adj = int(max(0, min(100, score + boost)))
            item["score"] = adj
            item["memory_boost"] = boost
            candidates.append((adj, item))
            result["evaluated"].append({"symbol": symbol, "status": "candidate", "score": score,
                                        "direction": consensus.get("direction"),
                                        "source": item.get("ticker_source")})

    candidates.sort(key=lambda pair: (pair[0], pair[1].get("ticker_ts", 0)), reverse=True)
    open_capacity = max(0, config["max_positions"] - len(auto_open))
    for score, item in candidates[:open_capacity]:
        side = item["consensus"]["direction"]
        quote = dict(item.get("ticker") or {})
        slippage_bps = float(config.get("paper_slippage_bps", 2.0))
        free_cash = float(store.get_wallet().get("balance") or 0)
        assessment = item.get("assessment") or {"explain": [], "risk_mult": 1.0, "regime": "unknown"}
        item["score"] = score
        gate = _ai_gate_review(item, assessment)
        gate_tag = "none"
        gate_mult = 1.0
        if gate is not None:
            gate_tag = gate["verdict"] if gate.get("ok") else "unavailable"
            assessment["explain"].append(
                f"نظر مدل ابری ({gate['mode']}): {gate['verdict']} · اطمینان {gate['confidence']} · {gate['reason']}")
            skip_reason = None
            if gate["mode"] == "autopilot":
                skip_reason = _autopilot_block_reason(gate)
            elif gate.get("ok") and gate["mode"] == "veto" and gate.get("verdict") == "veto":
                skip_reason = "مدل ابری ورود را وتو کرد: " + str(gate.get("reason") or "ریسک نامشخص")[:140]
            if skip_reason:
                result["skipped"].append((item["symbol"], skip_reason))
                result["evaluated"].append({"symbol": item["symbol"], "status": "vetoed",
                                            "reason": skip_reason, "score": score,
                                            "explain": assessment["explain"]})
                ai_core.log_decision({"symbol": item["symbol"], "side": side, "action": "vetoed",
                                      "regime": assessment["regime"], "score": score,
                                      "gate": gate_tag, "why": gate["reason"]})
                continue
            if gate.get("ok") and gate["mode"] in ("veto", "autopilot"):
                if gate["verdict"] == "reduce":
                    gate_mult = 0.5
                if gate["mode"] == "autopilot":
                    try:
                        requested_mult = float(gate.get("size_mult", 1.0))
                    except (TypeError, ValueError):
                        requested_mult = 1.0
                    gate_mult = min(gate_mult, max(0.0, min(1.0, requested_mult)))
                    proposed_side = str(gate.get("side") or side).upper()
                    if proposed_side not in ("LONG", "SHORT"):
                        proposed_side = side
                    if proposed_side != side:
                        # Cloud AI cannot reverse the position against independent
                        # local closed-candle evidence. Re-run the local risk gate.
                        proposed_candidate = dict(item)
                        proposed_candidate["consensus"] = dict(item.get("consensus") or {})
                        proposed_candidate["consensus"]["direction"] = proposed_side
                        confirmation = decision_engine.evaluate_entry(
                            proposed_candidate, minimum_score=effective_min_score,
                            news_gate=item.get("news_gate"),
                        )
                        if not confirmation.get("allow"):
                            skip_reason = (
                                "جهت پیشنهادی AI با شواهد چندبازه‌ای/ریسک محلی تأیید نشد: "
                                + "؛ ".join(confirmation.get("reasons") or [])[:180]
                            )
                            result["skipped"].append((item["symbol"], skip_reason))
                            result["evaluated"].append({
                                "symbol": item["symbol"], "status": "vetoed",
                                "reason": skip_reason, "score": score,
                                "explain": assessment["explain"],
                            })
                            ai_core.log_decision({
                                "symbol": item["symbol"], "side": proposed_side,
                                "action": "ai_direction_not_confirmed", "score": score,
                                "why": skip_reason[:180],
                            })
                            continue
                        assessment["explain"].append(
                            f"جهت پیشنهادی AI ({proposed_side}) توسط دادهٔ محلی مستقل تأیید شد."
                        )
                        side = proposed_side
        # Execute the paper fill only after the final AI/local-approved side is known.
        try:
            from .execution_simulator import market_fill
            execution = market_fill(quote, "BUY" if side == "LONG" else "SELL",
                                    slippage_bps=slippage_bps, require_fresh=True)
            px = float(execution["fill_price"])
        except Exception as exc:
            reason = f"مظنهٔ قابل‌اجرا برای شبیه‌سازی موجود نیست: {str(exc)[:100]}"
            result["skipped"].append((item["symbol"], reason))
            result["evaluated"].append({"symbol": item["symbol"], "status": "skip_no_executable_quote",
                                        "reason": reason, "score": score})
            continue
        # Reduce-only composition: every factor is <= 1.0, so nothing can enlarge risk.
        scale = 1.0
        try:
            scale = float(profile.get("risk_scale", 1.0))
        except (TypeError, ValueError):
            scale = 1.0
        if mem is not None:
            try:
                scale = min(scale, float(mem.risk_scale()))
            except Exception:
                pass
        scale = max(0.0, min(1.0, scale * dd.get("risk_mult", 1.0)
                             * float(assessment.get("risk_mult", 1.0)) * gate_mult))
        plan = _plan(
            item["symbol"], side, px, item["timeframes"]["1hour"], equity,
            free_cash=free_cash, risk_scale=scale,
            risk_pct=config["risk_pct"], max_leverage=config["max_leverage"],
            max_margin_pct=config["max_margin_pct_per_position"],
            max_free_cash_pct=config["max_free_cash_pct_per_position"],
            spread_pct=float(execution.get("spread_pct") or 0.0),
            slippage_bps=float(execution.get("slippage_bps") or slippage_bps),
        )
        if not plan:
            reason = "اندازهٔ موقعیت با بودجهٔ ریسک، مارجین آزاد و هزینه‌ها سازگار نشد"
            result["skipped"].append((item["symbol"], reason))
            continue
        try:
            opened = store.open_position(
                item["symbol"], side, px, plan["leverage"], plan["margin"],
                tp1=plan["tp1"], tp2=plan["tp2"], sl=plan["sl"],
                trail_pct=plan["trail_pct"], source="auto",
                note=(f"paper MTF score={score}; risk={config['risk_pct']:.2f}%; "
                      f"stop_atr={plan['stop_distance']:.8g}; regime={assessment.get('regime', 'unknown')}; "
                      f"decision_owner={'AI' if gate and gate.get('mode') == 'autopilot' and gate.get('verdict') in ('approve', 'reduce') else 'LOCAL'}; "
                      f"gate={gate_tag}; confidence={int((gate or {}).get('confidence') or 0)}; "
                      f"scale={scale:.2f}; bid={execution['bid']:.8g}; ask={execution['ask']:.8g}; "
                      f"spread={execution['spread_pct']:.5f}%; slip={execution['slippage_bps']:.2f}bps; paper only"),
            )
            opened_row = {
                "symbol": item["symbol"], "side": side, "id": opened["id"],
                "score": score, "risk_budget": plan["risk_budget"],
                "actual_risk": plan["actual_risk"], "margin": plan["margin"],
                "leverage": plan["leverage"], "entry": px, "sl": plan["sl"],
                "tp1": plan["tp1"], "tp2": plan["tp2"],
                "execution": execution,
            }
            result["opened"].append(opened_row)
            result["evaluated"].append({"symbol": item["symbol"], "status": "opened",
                                        "score": score, "direction": side, "position_id": opened["id"],
                                        "explain": assessment["explain"], "risk_scale": scale})
            ai_core.log_decision({"symbol": item["symbol"], "side": side, "action": "opened",
                                  "regime": assessment["regime"], "score": score, "gate": gate_tag,
                                  "pos_id": opened["id"], "risk_scale": round(scale, 3),
                                  "why": "; ".join(assessment["explain"])[:160]})
            occupied.add(market.normalize_symbol(item["symbol"])[1])
            equity = max(0.0, equity - plan["margin"])
        except Exception as exc:
            reason = str(exc)[:100]
            result["skipped"].append((item["symbol"], reason))
            result["evaluated"].append({"symbol": item["symbol"], "status": "error", "reason": reason})

    result["message"] = (f"{len(result['opened'])} پوزیشن دمو باز شد" if result["opened"]
                         else "شرط معتبر و هم‌جهت برای ورود دمو پیدا نشد")
    result["adaptive_note"] = profile.get("note")
    result["effective_min_confluence"] = effective_min_score
    result["remaining_auto_capacity"] = max(0, config["max_positions"] - len(auto_open) - len(result["opened"]))
    return result



_SCAN_LOCK = __import__("threading").Lock()


def scan_once(symbols: Optional[List[str]] = None, now: Optional[float] = None) -> Dict[str, Any]:
    """Prevent overlapping scans from creating duplicate paper entries."""
    if not _SCAN_LOCK.acquire(blocking=False):
        return {"checked": 0, "opened": [], "skipped": [], "evaluated": [],
                "watchlist": [], "message": "اسکن قبلی هنوز در حال اجراست؛ اجرای هم‌زمان رد شد.",
                "busy": True, "generated_ts": int(now if now is not None else time.time())}
    try:
        return _scan_once_impl(symbols=symbols, now=now)
    finally:
        _SCAN_LOCK.release()


def format_scan_report(result: Dict[str, Any]) -> str:
    """Human-readable audit note consumed by the UI and local coach."""
    config = result.get("config") or get_auto_config()
    opened = result.get("opened") or []
    skipped = result.get("skipped") or []
    evaluated = result.get("evaluated") or []
    lines = [
        "گزارش ممیزی Auto-Trade دمو KALVO",
        time.strftime("زمان: %Y-%m-%d %H:%M:%S", time.localtime(result.get("generated_ts") or time.time())),
        f"نمادهای بررسی‌شده: {', '.join(result.get('watchlist') or []) or '—'}",
        f"اسکن: {result.get('checked', 0)} · کاندیدا/ارزیابی: {len(evaluated)} · بازشده: {len(opened)} · ردشده: {len(skipped)}",
        f"تنظیمات: ریسک {config.get('risk_pct', 0.30):.2f}% · حداقل امتیاز {result.get('effective_min_confluence', config.get('min_confluence'))} · سقف پوزیشن {config.get('max_positions')} · اهرم مجاز شبیه‌سازی {config.get('max_leverage')}×",
        str(result.get("message") or "—"),
    ]
    for row in opened:
        lines.append(
            f"ورود دمو #{row['id']}: {row['symbol']} {row['side']} · قیمت {row['entry']:.8g} · "
            f"مارجین {row['margin']:.2f} · ریسک محاسباتی {row['actual_risk']:.3f} USDT · "
            f"SL {row['sl']:.8g} · TP1 {row['tp1']:.8g}"
        )
    if (result.get("drawdown") or {}).get("state") not in (None, "ok"):
        d = result["drawdown"]
        lines.append(f"محافظ سرمایه: {d['state']} · افت {d['drawdown_pct']:.1f}% از قله")
    for row in evaluated:
        if row.get("status") in ("opened", "vetoed") and row.get("explain"):
            lines.append(f"چرا {row['symbol']}؟")
            lines.extend("  - " + x for x in row["explain"])
    if skipped:
        lines.append("دلایل رد:")
        lines.extend(f"• {symbol}: {reason}" for symbol, reason in skipped[:12])
    if result.get("adaptive_note"):
        lines.append("بازخورد ژورنال: " + str(result["adaptive_note"]))
    lines.append("این اسکن فقط سفارش مجازی در دفترکل محلی می‌سازد و به صرافی سفارش نمی‌فرستد.")
    return "\n".join(lines)
