# -*- coding: utf-8 -*-
"""KALVO central defaults — market endpoints + full Coarena model catalog."""
from __future__ import annotations

APP_NAME = "KALVO"
APP_VERSION = "7.2.3"

# No provider credential is shipped in source code or APK.
DEFAULT_COARENA_API_KEY = ""
COARENA_BASE = "https://coarena.ai/api/v1"
COARENA_ANTHROPIC_BASE = "https://coarena.ai/api"

# Best overall default
DEFAULT_AI_MODEL = "claude-opus-5-5"
DEFAULT_AI_MODE = "both"  # local | cloud | both
DEFAULT_REASONING = "high"

# Full catalog from Coarena docs (id, Persian label, tier)
AI_MODELS = [
    # Tier S
    ("claude-opus-5-5", "Opus 5.5 · بالاترین", "S"),
    ("gemini-4-carbon", "Gemini 4 Carbon · بالا", "S"),
    ("qwen3-8-2-4t-a95b", "Qwen3 Max · بالا", "S"),
    ("gpt-6-1-sol", "GPT-6.1 Sol", "S"),
    ("gpt-6-1-terra", "GPT-6.1 Terra", "A"),
    # Tier A
    ("claude-sonnet-5-5", "Sonnet 5.5 · متعادل", "A"),
    ("gemini-4-argon", "Gemini 4 Argon", "A"),
    ("kimi-k3-1-pro", "Kimi K3.1 Pro", "A"),
    ("grok-4-7", "Grok 4.7", "A"),
    ("gpt-6-luna", "GPT-6 Luna", "A"),
    # Tier B
    ("claude-fable-5-2", "Claude Fable 5.2", "B"),
    ("claude-fable-5-1", "Claude Fable 5.1", "B"),
    ("claude-fable-5", "Claude Fable 5", "B"),
    ("mimo-v2-6-pro", "MiMo V2.6 Pro", "B"),
    ("hy4", "Tencent Hy4", "B"),
    ("claude-haiku-5-5", "Haiku 5.5 · سبک", "B"),
    ("kimi-k3", "Kimi K3", "B"),
    # Currently switched off by the provider (model_off) -> auto-fallback in coarena_ai.chat
    ("gpt-6-astra", "GPT-6 Astra · فعلاً خاموش", "B"),
    # Tier Fast
    ("gemini-3-8-flash", "Gemini 3.8 Flash · سریع", "F"),
    ("glm-5-3-flash", "GLM 5.3 Flash · سریع", "F"),
    ("minimax-m3-1-flash", "MiniMax M3.1 Flash", "F"),
    ("deepseek-v4-1-flash-1017", "DeepSeek V4.1 Flash", "F"),
]

AI_MODEL_IDS = [m[0] for m in AI_MODELS]

MARKET_ENDPOINTS = {
    "binance": "https://api.binance.com",
    "kucoin": "https://api.kucoin.com",
    "bybit": "https://api.bybit.com",
    "okx": "https://www.okx.com",
    "gate": "https://api.gateio.ws",
}

DEFAULT_SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX", "DOT",
    "MATIC", "ATOM", "LTC", "NEAR", "APT",
]

AUTO_MAX_POSITIONS = 3
AUTO_RISK_PCT = 0.30
AUTO_MIN_CONFLUENCE = 68
AUTO_MAX_LEVERAGE = 2.0
AUTO_SCAN_INTERVAL = 90
AUTO_COOLDOWN_HOURS = 3

# --- AI core safety limits (paper trading only) ---
AI_GATE_DEFAULT = "autopilot"       # off | advisory | veto | autopilot; AI-first for new installs
AI_SHARE_ACCOUNT_DEFAULT = "0"  # 0 = send only market data + aggregate stats to the cloud
DRAWDOWN_REDUCE_PCT = 8.0       # equity drawdown from peak: halve risk
DRAWDOWN_PAUSE_PCT = 15.0       # equity drawdown from peak: pause auto-scanner

# autopilot: the cloud model makes the final entry decision. Hard caps stay in code.
AUTOPILOT_MIN_SIZE_MULT = 0.25
AUTOPILOT_MAX_SIZE_MULT = 1.0   # never above the code-enforced risk budget
