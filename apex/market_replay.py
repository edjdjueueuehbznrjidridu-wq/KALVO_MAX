# -*- coding: utf-8 -*-
"""Deterministic candle-by-candle replay for learning and paper review."""
from __future__ import annotations

from typing import Any, Dict, List


class CandleReplay:
    def __init__(self, candles: List[Dict[str, Any]], visible: int = 40):
        self.candles = list(candles or [])
        self.visible = max(10, int(visible))
        self.index = min(len(self.candles), self.visible)

    @property
    def finished(self) -> bool:
        return self.index >= len(self.candles)

    @property
    def progress(self) -> float:
        return self.index / len(self.candles) if self.candles else 0.0

    def current_window(self) -> List[Dict[str, Any]]:
        start = max(0, self.index - self.visible)
        return self.candles[start:self.index]

    def step(self, count: int = 1) -> List[Dict[str, Any]]:
        self.index = min(len(self.candles), self.index + max(1, int(count)))
        return self.current_window()

    def back(self, count: int = 1) -> List[Dict[str, Any]]:
        self.index = max(min(len(self.candles), self.visible), self.index - max(1, int(count)))
        return self.current_window()

    def reset(self) -> List[Dict[str, Any]]:
        self.index = min(len(self.candles), self.visible)
        return self.current_window()
