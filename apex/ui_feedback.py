# -*- coding: utf-8 -*-
"""Optional, low-volume UI feedback. All effects are opt-in / preference-driven."""
from __future__ import annotations

import os
from typing import Dict, Optional

_sounds: Dict[str, object] = {}
_sound_enabled = False
_animations_enabled = True
_loaded = False


def configure(*, sounds: Optional[bool] = None, animations: Optional[bool] = None) -> None:
    global _sound_enabled, _animations_enabled
    if sounds is not None:
        _sound_enabled = bool(sounds)
    if animations is not None:
        _animations_enabled = bool(animations)


def animations_enabled() -> bool:
    return _animations_enabled


def sounds_enabled() -> bool:
    return _sound_enabled


def load_sounds(asset_dir: str) -> bool:
    global _loaded
    if _loaded:
        return bool(_sounds)
    _loaded = True
    try:
        from kivy.core.audio import SoundLoader
    except Exception:
        return False
    for name in ("tap", "success", "alert"):
        path = os.path.join(asset_dir, "sfx", name + ".wav")
        try:
            sound = SoundLoader.load(path)
            if sound is not None:
                sound.volume = 0.16 if name == "tap" else 0.22
                _sounds[name] = sound
        except Exception:
            continue
    return bool(_sounds)


def play(name: str) -> bool:
    if not _sound_enabled:
        return False
    sound = _sounds.get(str(name))
    if sound is None:
        return False
    try:
        sound.stop()
        sound.play()
        return True
    except Exception:
        return False
