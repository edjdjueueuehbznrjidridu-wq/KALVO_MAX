# KALVO UI Design System — 7.2.0

## Brand direction

**MIDNIGHT FINANCIAL TERMINAL** — حرفه‌ای، کم‌نویز، خوانا و مبتنی بر وضعیت‌های قابل‌فهم. شعارها:

- `SEE CLEARLY · THINK DEEPLY · STAY AWARE`
- `بازار را دقیق‌تر ببین؛ تصمیم را آگاهانه‌تر بررسی کن.`
- `Every Decision. Accounted For.`

## Color tokens

| Token | Color | Meaning |
|---|---|---|
| BG | `#07090C` / theme token | Canvas |
| CARD | charcoal/navy | Surface |
| TEXT | soft platinum | Main text |
| MUTED | slate | Secondary text |
| ACCENT | user-selectable sky/teal/violet | Selection and primary actions |
| GREEN | jade | Positive/healthy |
| RED | muted coral | Negative/error/risk |

State color is always paired with text. Avoid using color alone to convey risk, freshness or order status.

## Responsive profiles

`apex/responsive.py` exposes density-independent width profiles:

| Logical width | Profile | Metrics columns | Margins |
|---:|---|---:|---:|
| 320 dp | compact | 2 | 8 dp |
| 360–599 dp | phone | 2 | 12 dp |
| 600–839 dp | tablet | 3 | 20 dp |
| 840+ dp | wide | 4 | 28 dp |

Dashboard metric/shortcut grids and bottom-nav type scale adapt to the profile. The profile matrix is regression-tested in CI; it is not equivalent to real-device visual testing.

## Typography and Persian shaping

- Prefer the installed Android Noto Naskh/Noto Sans Arabic system font when present.
- Fall back to Kivy Roboto if the device lacks the candidate font.
- `arabic-reshaper` and `python-bidi` shape text in supported labels/buttons.
- Text input caret and mixed-direction editing still require physical Android testing; never rewrite editable input contents during typing merely to force visual shaping.
- No external font binaries are bundled in the project.

## Motion and sound

- Screen transition: short fade, disabled when the user disables animations.
- Pressed-state feedback: short opacity animation.
- SFX: `tap.wav`, `success.wav`, `alert.wav`; opt-in and default OFF.
- Keep sounds brief and low-volume. No continuous background audio.

## Personalization

- Accent palette: sky, teal, violet.
- Font scale: 1.00, 1.08, 1.16.
- Density: auto, comfortable, compact.
- Motion: on/off.
- UI sounds: on/off.
- System notifications: on/off and Android runtime permission.
- Background monitor: opt-in foreground service; explicitly describe battery/OS limitations.

## Core screen hierarchy

1. Home: account/paper-ledger snapshot, data health, AI status, positions and news.
2. Markets: symbol discovery, source-aware quotes and freshness.
3. Futures: paper-only derivative simulation.
4. Spot: paper-only balances and order ledger.
5. Wallet: simulated balance and history.
6. AI Center: single-symbol, multi-timeframe, cloud analysis, chat, memory and calendar/news context.
7. More: customization, alerts, notification inbox, preferences and monitoring.

## Accessibility and stability rules

- 44–50 dp touch targets wherever practical.
- Never hide quote freshness, price unit, simulated-account state or AI limitations.
- Do not rely on red/green alone.
- Respect OS font scale and device width.
- Keep animated effects optional and short.
- Never let animation or sound block background data workers.
- Validate all major screens at 320, 360, 393, 432, 600, 768 and 1024 logical dp plus physical devices before claiming UI release readiness.
