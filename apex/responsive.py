# -*- coding: utf-8 -*-
"""Responsive layout profiles expressed in density-independent pixels.

This module is intentionally UI-toolkit independent so device-width breakpoints
can be regression-tested on desktop CI before a physical Android visual pass.
"""
from __future__ import annotations

import math
from typing import Dict


def profile_for_width(width_dp: float) -> Dict[str, object]:
    try:
        width = float(width_dp)
    except (TypeError, ValueError, OverflowError):
        width = 360.0
    if not math.isfinite(width):
        width = 360.0
    width = max(240.0, min(2400.0, width))

    if width < 360:
        density = "compact"
        columns = 2
        nav_font = 8.5
        horizontal_margin = 8
        button_height = 46
    elif width < 600:
        density = "phone"
        columns = 2
        nav_font = 9.5
        horizontal_margin = 12
        button_height = 50
    elif width < 840:
        density = "tablet"
        columns = 3
        nav_font = 10.5
        horizontal_margin = 20
        button_height = 52
    else:
        density = "wide"
        columns = 4
        nav_font = 11.0
        horizontal_margin = 28
        button_height = 54

    return {
        "width_dp": round(width, 1),
        "density": density,
        "compact": density == "compact",
        "metric_columns": columns,
        "shortcut_columns": min(6, columns + 1),
        "nav_font_dp": nav_font,
        "horizontal_margin_dp": horizontal_margin,
        "button_height_dp": button_height,
    }
