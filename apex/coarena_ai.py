# -*- coding: utf-8 -*-
"""Coarena Model API client — OpenAI-compatible, pure Python, full catalog."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from apex.defaults import (
        COARENA_BASE,
        DEFAULT_AI_MODEL,
        AI_MODEL_IDS,
    )
except Exception:  # pragma: no cover
    COARENA_BASE = "https://coarena.ai/api/v1"
    DEFAULT_AI_MODEL = "claude-opus-5-5"
    AI_MODEL_IDS = [DEFAULT_AI_MODEL]

BASE_URL = COARENA_BASE
DEFAULT_MODEL = DEFAULT_AI_MODEL
UA = "KALVO/7.0 (Coarena; Hybrid)"


class CoarenaError(Exception):
    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


def resolve_key(api_key: Optional[str] = None) -> str:
    """Prefer an explicit user key, then environment; never use a bundled secret."""
    key = (api_key or "").strip()
    if key and len(key) >= 8:
        return key
    try:
        import os
        env = (os.environ.get("COARENA_API_KEY") or os.environ.get("COARENA_KEY") or "").strip()
        if env and len(env) >= 8:
            return env
    except Exception:
        pass
    return key


def _request(method: str, path: str, api_key: str, body: Optional[dict] = None, timeout: int = 90) -> dict:
    key = resolve_key(api_key)
    if (not key or len(key) < 8) and path != "/models":
        raise CoarenaError("کلید Coarena تنظیم نشده", "no_key", 401)
    url = BASE_URL.rstrip("/") + path
    data = None
    headers = {
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Accept": "application/json",
    }
    if key:
        headers["Authorization"] = "Bearer " + key
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        code = "http"
        low = err_body.lower()
        if e.code == 401:
            code = "invalid_api_key"
        elif e.code == 429:
            code = "429"
        elif "model_off" in low:
            code = "model_off"
        elif "model_not_found" in low:
            code = "model_not_found"
        raise CoarenaError(f"HTTP {e.code}: {err_body or e.reason}", code, e.code)
    except urllib.error.URLError as e:
        raise CoarenaError(f"شبکه: {e.reason}", "network", 0)


def list_models(api_key: str = "") -> List[dict]:
    j = _request("GET", "/models", api_key, timeout=30)
    data = j.get("data") or []
    return data if isinstance(data, list) else []


def chat(
    api_key: str = "",
    messages: Optional[List[Dict[str, str]]] = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    temperature: float = 0.55,
    system: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    retries: int = 3,
    timeout: int = 120,
) -> str:
    """Chat completion; returns assistant text. Retries on 429/network/model_off."""
    msgs = list(messages or [])
    if system:
        msgs = [{"role": "system", "content": system}] + msgs
    use_model = model or DEFAULT_MODEL
    body: Dict[str, Any] = {
        "model": use_model,
        "messages": msgs,
        "max_tokens": min(int(max_tokens or 4096), 16384),
        "temperature": float(temperature),
        "stream": False,
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort

    last_err: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        try:
            j = _request("POST", "/chat/completions", api_key, body, timeout=timeout)
            choices = j.get("choices") or []
            if not choices:
                raise CoarenaError("پاسخ خالی از مدل", "empty")
            msg = choices[0].get("message") or {}
            text = (msg.get("content") or "").strip()
            if not text:
                raise CoarenaError("متن پاسخ خالی است", "empty")
            return text
        except CoarenaError as e:
            last_err = e
            if e.code in ("429", "network") and attempt < retries - 1:
                time.sleep(1.2 * (attempt + 1))
                continue
            if e.code in ("model_off", "model_not_found") and use_model != DEFAULT_MODEL:
                use_model = DEFAULT_MODEL
                body["model"] = use_model
                continue
            if e.code in ("model_off", "model_not_found") and use_model == DEFAULT_MODEL:
                # last resort sonnet
                use_model = "claude-sonnet-5-5"
                body["model"] = use_model
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise CoarenaError(str(e)[:160], "unknown")
    raise CoarenaError(str(last_err or "خطای ناشناخته"), "failed")


def kalvo_analyze(
    api_key: str = "",
    user_prompt: str = "",
    system_prompt: str = "",
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = "high",
) -> str:
    return chat(
        api_key=api_key,
        messages=[{"role": "user", "content": user_prompt}],
        model=model,
        max_tokens=8192,
        temperature=0.4,
        system=system_prompt,
        reasoning_effort=reasoning_effort,
        retries=3,
    )


# backward alias
apex_analyze = kalvo_analyze
