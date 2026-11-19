# -*- coding: utf-8 -*-
"""Lightweight in-process event bus so screens share live market state."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, DefaultDict, Dict, List

_subs: DefaultDict[str, List[Callable[..., Any]]] = defaultdict(list)


def subscribe(topic: str, callback: Callable[..., Any]) -> None:
    if callback not in _subs[topic]:
        _subs[topic].append(callback)


def unsubscribe(topic: str, callback: Callable[..., Any]) -> None:
    try:
        _subs[topic].remove(callback)
    except ValueError:
        pass


def publish(topic: str, **payload: Any) -> None:
    for cb in list(_subs.get(topic, ())):
        try:
            cb(**payload)
        except Exception:
            continue


def clear() -> None:
    _subs.clear()
