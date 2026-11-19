# -*- coding: utf-8 -*-
"""System-font selection and optional Persian/Arabic text shaping.

No font files are bundled. On Android, KALVO prefers an installed Noto Arabic
system font and falls back to Kivy's Roboto. Text shaping libraries are pure
Python dependencies included in the Android build; desktop tests gracefully
fall back when they are not installed.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any

FONT_ALIAS = "KALVOSystem"
_ARABIC_RE = re.compile(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]")
_font_registered = False
_font_name = "Roboto"


def register_system_font(label_base: Any = None, *, platform: str | None = None) -> str:
    """Register a font already installed on Android; never copy/share font files."""
    global _font_registered, _font_name
    if _font_registered:
        return _font_name
    if platform is None:
        try:
            from kivy.utils import platform as kivy_platform
            platform = kivy_platform
        except Exception:
            platform = "unknown"
    candidates = []
    if platform == "android":
        candidates = [
            "/system/fonts/NotoNaskhArabicUI-Regular.ttf",
            "/system/fonts/NotoNaskhArabic-Regular.ttf",
            "/system/fonts/NotoSansArabicUI-Regular.ttf",
            "/system/fonts/NotoSansArabic-Regular.ttf",
        ]
    selected = next((path for path in candidates if os.path.isfile(path)), None)
    if selected and label_base is None:
        try:
            from kivy.core.text import LabelBase as label_base
        except Exception:
            label_base = None
    if selected and label_base is not None:
        try:
            label_base.register(FONT_ALIAS, fn_regular=selected)
            _font_name = FONT_ALIAS
        except Exception:
            _font_name = "Roboto"
    else:
        _font_name = "Roboto"
    _font_registered = True
    return _font_name


@lru_cache(maxsize=2048)
def shape_rtl_text(value: str) -> str:
    """Shape Arabic-script paragraphs while preserving line breaks.

    If the optional shaping dependencies are unavailable, return the original
    text instead of breaking screen construction.
    """
    text = str(value or "")
    if not _ARABIC_RE.search(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except Exception:
        return text
    try:
        paragraphs = text.split("\n")
        output = []
        for paragraph in paragraphs:
            if _ARABIC_RE.search(paragraph):
                output.append(get_display(arabic_reshaper.reshape(paragraph), base_dir="R"))
            else:
                output.append(paragraph)
        return "\n".join(output)
    except Exception:
        return text


def shaping_available() -> bool:
    try:
        import arabic_reshaper  # noqa: F401
        from bidi.algorithm import get_display  # noqa: F401
        return True
    except Exception:
        return False
