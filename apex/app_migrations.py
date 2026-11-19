# -*- coding: utf-8 -*-
"""Small, additive application-setting migrations for KALVO."""
from __future__ import annotations
from typing import Callable, Dict


def migrate_v7_ai_gate(get_setting: Callable[[str, str], str],
                       set_setting: Callable[[str, str], object]) -> Dict[str, str]:
    """Move the legacy default 'advisory' to AI-first autopilot exactly once.

    Explicit user choices off/veto/autopilot are preserved. The previous
    release seeded advisory as its default, so advisory is treated as the
    legacy default only during this one-time version migration. After the
    migration, future user selections are never rewritten.
    """
    marker = "kalvo_v7_ai_gate_migration_done"
    if str(get_setting(marker, "0") or "0") == "1":
        return {"status": "already_migrated"}

    changed: Dict[str, str] = {}
    for key in ("ai_gate", "auto_ai_gate"):
        current = str(get_setting(key, "") or "").strip().lower()
        if current in ("", "advisory"):
            set_setting(key, "autopilot")
            changed[key] = "autopilot"

    set_setting(marker, "1")
    return {"status": "migrated", **changed}
