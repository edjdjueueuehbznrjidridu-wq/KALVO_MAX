# -*- coding: utf-8 -*-
"""Small, dependency-free environment configuration loader for KALVO.

Secrets are read from the process environment first, then from a local .env file.
The .env file is intentionally excluded from Git and Android packages.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

_loaded = False


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        values[key] = value
    return values


def load_env(path: str | os.PathLike | None = None, override: bool = False) -> Dict[str, str]:
    """Load a local .env once. Existing process variables win unless override=True."""
    global _loaded
    target = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    values = _parse_env_file(target)
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    _loaded = True
    return values


def get_env(name: str, default: str = "") -> str:
    if not _loaded:
        load_env()
    return os.environ.get(name, default).strip()
