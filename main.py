# -*- coding: utf-8 -*-
"""KALVO 7.2.3 — Android boot diagnostics and paper-trading platform."""
from __future__ import annotations
import os
import math
import json
import threading
import time
import sys
import traceback
import faulthandler
from datetime import datetime


_BOOT_PHASE = "python-entry"
_BOOT_LOG_PATH = None
_BOOT_LOG_DIR = None
_BOOT_FAULT_HANDLE = None


def _boot_log_dirs():
    """Return writable, app-private diagnostic folders in priority order."""
    candidates = []
    if _BOOT_LOG_DIR:
        candidates.append(_BOOT_LOG_DIR)
    for base in (
        os.environ.get("ANDROID_PRIVATE"),
        os.environ.get("HOME"),
        os.path.expanduser("~"),
        os.getcwd(),
    ):
        if base:
            candidates.append(os.path.join(base, ".kalvo", "logs"))
    # Preserve order while avoiding duplicate writes.
    result = []
    for path in candidates:
        if path not in result:
            result.append(path)
    return result


def _ensure_faulthandler() -> None:
    """Enable supported fatal-signal tracebacks as early as an app-private path allows."""
    global _BOOT_FAULT_HANDLE
    if _BOOT_FAULT_HANDLE is not None:
        return
    for log_dir in _boot_log_dirs():
        try:
            os.makedirs(log_dir, exist_ok=True)
            fatal_path = os.path.join(log_dir, "startup-fatal.log")
            handle = open(fatal_path, "a", encoding="utf-8", buffering=1)
            try:
                faulthandler.enable(file=handle, all_threads=True)
            except Exception:
                handle.close()
                continue
            _BOOT_FAULT_HANDLE = handle
            return
        except Exception:
            continue


def _set_boot_log_dir(path: str) -> None:
    """Prefer Kivy's confirmed private app-data directory for subsequent breadcrumbs."""
    global _BOOT_LOG_DIR
    try:
        _BOOT_LOG_DIR = os.path.abspath(path)
        os.makedirs(_BOOT_LOG_DIR, exist_ok=True)
        _ensure_faulthandler()
        _boot_trace("private app diagnostic directory configured")
    except Exception as exc:
        _boot_trace(f"diagnostic directory setup unavailable: {type(exc).__name__}")


def _boot_trace(message: str) -> None:
    """Write startup breadcrumbs to a private file and Android process log."""
    global _BOOT_LOG_PATH
    try:
        _ensure_faulthandler()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} | pid={os.getpid()} | {_BOOT_PHASE} | {message}\n"
        for log_dir in _boot_log_dirs():
            try:
                os.makedirs(log_dir, exist_ok=True)
                path = os.path.join(log_dir, "startup-trace.log")
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
                _BOOT_LOG_PATH = path
                break
            except Exception:
                continue
        # stdout is captured by Android's process log on python-for-android builds.
        try:
            print("[KALVO-BOOT] " + line.rstrip(), flush=True)
        except Exception:
            pass
    except Exception:
        pass


def _early_crash_hook(exc_type, exc_value, exc_tb):
    """Persist uncaught Python startup failures before the Kivy App exists."""
    try:
        details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _boot_trace(f"UNCAUGHT {exc_type.__name__}: {exc_value}")
        for log_dir in _boot_log_dirs():
            try:
                os.makedirs(log_dir, exist_ok=True)
                crash_path = os.path.join(log_dir, "startup-crash.log")
                with open(crash_path, "a", encoding="utf-8") as fh:
                    fh.write(f"\n\n===== UNCAUGHT STARTUP EXCEPTION | phase={_BOOT_PHASE} =====\n")
                    fh.write(details)
                    fh.flush()
                break
            except Exception:
                continue
    except Exception:
        pass
    try:
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    except Exception:
        pass


_boot_trace("main.py entered")
sys.excepthook = _early_crash_hook
if hasattr(threading, "excepthook"):
    def _early_thread_crash_hook(args):
        _early_crash_hook(args.exc_type, args.exc_value, args.exc_traceback)
    threading.excepthook = _early_thread_crash_hook

_BOOT_PHASE = "import-kivy"
_boot_trace("begin Kivy imports")

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle, Rectangle, Line
from kivy.metrics import dp
from kivy.animation import Animation
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button as _KivyButton
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label as _KivyLabel
from kivy.uix.screenmanager import Screen, ScreenManager, FadeTransition
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner as _KivySpinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.uix.popup import Popup

_BOOT_PHASE = "import-apex-market"
_boot_trace("Kivy imports completed; importing market API modules")
from apex import market_apis as mapi
from apex import instrument_catalog as instruments
from apex.config import load_env, get_env

_BOOT_PHASE = "load-env"
_boot_trace("market API modules imported; loading environment")
# Load developer/local configuration before any provider or AI screen reads it.
load_env()

_BOOT_PHASE = "import-apex-core"
_boot_trace("environment loader completed; importing engine modules")
from apex import analysis_engine as analysis
from apex import market_assistant as assistant
from apex import learning as learning_engine
from apex import auto_trader as paper_auto
from apex import storage as db
from apex import strategy_lab, market_replay, portfolio_analytics

_BOOT_PHASE = "import-apex-ui-services"
_boot_trace("core engine imports completed; importing UI and service modules")
from apex import market_data_hub, position_manager, news_intelligence, runtime, ui_theme
from apex import economic_calendar, learning_memory, ui_feedback, background_monitor, responsive
from apex import platform_notifications, typography

_BOOT_PHASE = "module-imports-complete"
_boot_trace("all top-level imports completed")


def _run_background(target, *args, **kwargs):
    """Run I/O through the bounded executor without leaking queue errors into Kivy callbacks."""
    try:
        return runtime.submit(target, *args, context=getattr(target, "__name__", "UI worker"), **kwargs)
    except Exception as exc:
        runtime.log_exception("background task could not be queued", exc)
        try:
            app = App.get_running_app()
            status = getattr(app, "runtime_status_callback", None)
            if callable(status):
                Clock.schedule_once(lambda *_a, msg=str(exc)[:100]: status(msg), 0)
        except Exception:
            pass
        return None

# Premium terminal design system: midnight navy, electric blue and jade.
BG = ui_theme.BG
CARD = ui_theme.CARD
LINE = ui_theme.LINE
TEXT = ui_theme.TEXT
MUTED = ui_theme.MUTED
GREEN = ui_theme.GREEN
RED = ui_theme.RED
GOLD = ui_theme.ACCENT
DARK_BTN = ui_theme.DARK_BTN
BLUE_GLOW = ui_theme.ACCENT_SURFACE

try:
    Window.clearcolor = BG
except Exception:
    pass
try:
    Window.softinput_mode = "below_target"
except Exception:
    pass

UI_FONT_SCALE = 1.0
UI_COMPACT = False
_BOOT_PHASE = "register-font"
_boot_trace("register system font")
UI_FONT_NAME = typography.register_system_font()
UI_SCREEN_PROFILE = responsive.profile_for_width(393)
_BOOT_PHASE = "module-ready"
_boot_trace("main module definitions ready")


def _shape_widget_text(text):
    return typography.shape_rtl_text(text)


class Label(_KivyLabel):
    """Label with optional Persian shaping and a system-font fallback."""
    def __init__(self, **kwargs):
        self._kalvo_shaping = False
        kwargs.setdefault("font_name", UI_FONT_NAME)
        super().__init__(**kwargs)

    def on_text(self, instance, value):
        if getattr(self, "_kalvo_shaping", False):
            return
        shaped = _shape_widget_text(value)
        if shaped != value:
            self._kalvo_shaping = True
            try:
                self.text = shaped
            finally:
                self._kalvo_shaping = False


class Button(_KivyButton):
    """Button with optional Persian shaping and system-font fallback."""
    def __init__(self, **kwargs):
        self._kalvo_shaping = False
        kwargs.setdefault("font_name", UI_FONT_NAME)
        super().__init__(**kwargs)

    def on_text(self, instance, value):
        if getattr(self, "_kalvo_shaping", False):
            return
        shaped = _shape_widget_text(value)
        if shaped != value:
            self._kalvo_shaping = True
            try:
                self.text = shaped
            finally:
                self._kalvo_shaping = False


class Spinner(_KivySpinner):
    """Spinner text uses the same locale/font path as the other controls."""
    def __init__(self, **kwargs):
        self._kalvo_shaping = False
        kwargs.setdefault("font_name", UI_FONT_NAME)
        super().__init__(**kwargs)

    def on_text(self, instance, value):
        if getattr(self, "_kalvo_shaping", False):
            return
        shaped = _shape_widget_text(value)
        if shaped != value:
            self._kalvo_shaping = True
            try:
                self.text = shaped
            finally:
                self._kalvo_shaping = False


COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "LINK", "AVAX", "DOT"]


def money(x) -> str:
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return "—"


def pxfmt(x) -> str:
    try:
        v = float(x)
        if v >= 1000:
            return f"{v:,.2f}"
        if v >= 1:
            return f"{v:,.4f}"
        return f"{v:.8g}"
    except Exception:
        return "—"


def ticker_display(ticker: dict) -> str:
    """Native market value for the UI; simulator ledger values remain USDT-based."""
    try:
        value = float(ticker.get("display_price", ticker.get("price")))
        currency = str(ticker.get("display_currency") or ticker.get("quote_currency") or "USDT")
        unit = str(ticker.get("display_unit") or "")
        if currency in ("IRT", "TOMAN"):
            return f"{value:,.0f} {unit or 'تومان'}"
        if currency in ("USD", "USDT"):
            return f"${value:,.4f} · {unit}" if value < 1000 else f"${value:,.2f} · {unit}"
        return f"{value:,.4f} {currency} · {unit}"
    except Exception:
        return "—"


def canonical_market_symbol(symbol: str) -> str:
    candidate = instruments.normalize_instrument_symbol(symbol)
    if candidate in instruments.INSTRUMENTS or candidate in instruments.REFERENCE_SYMBOLS:
        return candidate
    return mapi.normalize_symbol(symbol)[0]


def _apply_font_scale(widget, scale: float):
    """Apply one stable font multiplier without compounding on repeated changes."""
    try:
        if hasattr(widget, "font_size"):
            base = getattr(widget, "_kalvo_base_font_size", None)
            if base is None:
                base = float(widget.font_size)
                setattr(widget, "_kalvo_base_font_size", base)
            widget.font_size = max(dp(8), min(dp(30), float(base) * float(scale)))
        for child in getattr(widget, "children", ()):
            _apply_font_scale(child, scale)
    except Exception:
        return


class RoundButton(Button):
    """Premium rounded touch target with a clean pressed state."""
    def __init__(self, bg=DARK_BTN, fg=TEXT, **kwargs):
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_down", "")
        kwargs.setdefault("background_color", bg)
        kwargs.setdefault("color", fg)
        kwargs.setdefault("bold", True)
        kwargs.setdefault("font_size", dp(13))
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(46 if UI_COMPACT else 50))
        kwargs.setdefault("padding", [dp(13), dp(10)])
        super().__init__(**kwargs)
        self._kalvo_base_font_size = float(self.font_size)
        with self.canvas.before:
            self._shape_color = Color(*self.background_color)
            self._shape = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(13)])
        self.bind(pos=self._sync_shape, size=self._sync_shape,
                  background_color=self._sync_shape_color)
        self._feedback_anim = None
        self.bind(on_press=self._darken, on_release=self._restore)

    def _sync_shape(self, *_):
        self._shape.pos, self._shape.size = self.pos, self.size

    def _sync_shape_color(self, *_):
        self._shape_color.rgba = self.background_color

    def _darken(self, *_):
        self._pressed_color = tuple(self.background_color)
        self.background_color = tuple(max(0.0, c * 0.76) if i < 3 else c
                                       for i, c in enumerate(self._pressed_color))
        if ui_feedback.animations_enabled():
            try:
                if self._feedback_anim:
                    self._feedback_anim.cancel(self)
                self._feedback_anim = Animation(opacity=0.84, duration=0.055, transition="out_quad")
                self._feedback_anim.start(self)
            except Exception:
                pass

    def _restore(self, *_):
        if getattr(self, "_pressed_color", None):
            self.background_color = self._pressed_color
        if ui_feedback.animations_enabled():
            try:
                Animation(opacity=1.0, duration=0.11, transition="out_quad").start(self)
            except Exception:
                pass
        ui_feedback.play("tap")


class H(Label):
    def __init__(self, text="", size=16, color=TEXT, **kwargs):
        super().__init__(
            text=text, color=color, bold=True, font_size=dp(size),
            size_hint_y=None, height=dp(size + 14), halign="right", **kwargs,
        )
        self._kalvo_base_font_size = float(self.font_size)
        self.bind(size=lambda *_: setattr(self, "text_size", self.size))


class M(Label):
    def __init__(self, text="", **kwargs):
        super().__init__(
            text=text, color=MUTED, font_size=dp(12),
            size_hint_y=None, height=dp(22), **kwargs,
        )
        self._kalvo_base_font_size = float(self.font_size)


class MetricCard(BoxLayout):
    """Raised metric card with a sky-blue edge and a hairline border."""
    def __init__(self, title, value="—", accent=GOLD, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("padding", [dp(14), dp(10)])
        kwargs.setdefault("spacing", dp(5))
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(86))
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*CARD)
            self._bg = RoundedRectangle(radius=[dp(17)])
            Color(*LINE)
            self._border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(17)), width=1.0)
            Color(*GOLD)
            self._stripe = RoundedRectangle(pos=(self.x, self.y + dp(10)), size=(dp(3), max(dp(12), self.height-dp(20))), radius=[dp(2)])
        self.bind(pos=self._sync_canvas, size=self._sync_canvas)
        self.caption = Label(text=title, color=MUTED, font_size=dp(12),
                             size_hint_y=None, height=dp(22), halign="right", valign="middle")
        self.value_label = Label(text=value, color=accent, bold=True,
                                 font_size=dp(19), halign="right", valign="middle")
        self.caption.bind(size=lambda *_: setattr(self.caption, "text_size", self.caption.size))
        self.value_label.bind(size=lambda *_: setattr(self.value_label, "text_size", self.value_label.size))
        self.add_widget(self.caption)
        self.add_widget(self.value_label)

    def _sync_canvas(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(17))
        self._stripe.pos = (self.x, self.y + dp(10))
        self._stripe.size = (dp(3), max(dp(12), self.height-dp(20)))

    def set_value(self, value, color=None):
        self.value_label.text = str(value)
        if color is not None:
            self.value_label.color = color


class AppHeader(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None,
                         height=dp(88), padding=[dp(18), dp(12)], spacing=dp(12), **kwargs)
        with self.canvas.before:
            Color(*CARD)
            self._bg = RoundedRectangle(radius=[dp(19)])
            Color(*LINE)
            self._border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(19)), width=1.0)
            Color(*GOLD)
            self._accent = RoundedRectangle(pos=(self.x, self.y + dp(12)), size=(dp(3), self.height-dp(24)), radius=[dp(2)])
        self.bind(pos=self._sync_canvas, size=self._sync_canvas)
        brand = BoxLayout(orientation="vertical", spacing=0)
        self.brand = Label(text="KALVO", color=GOLD, bold=True, font_size=dp(25),
                           size_hint_y=None, height=dp(29), halign="left")
        self.brand.bind(size=lambda *_: setattr(self.brand, "text_size", self.brand.size))
        brand.add_widget(self.brand)
        brand.add_widget(Label(text="SEE CLEARLY  ·  THINK DEEPLY  ·  STAY AWARE", color=MUTED,
                               font_size=dp(8), halign="left"))
        self.add_widget(brand)
        self.add_widget(Widget())
        info = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(166), spacing=0)
        self.symbol = Label(text="BTC · بازار انتخابی", color=TEXT, bold=True, font_size=dp(12),
                            size_hint_y=None, height=dp(23), halign="right", shorten=True, shorten_from="left")
        self.symbol.bind(size=lambda *_: setattr(self.symbol, "text_size", self.symbol.size))
        self.price = Label(text="قیمت —", color=GOLD, font_size=dp(11),
                           size_hint_y=None, height=dp(22), halign="right", shorten=True, shorten_from="left")
        self.price.bind(size=lambda *_: setattr(self.price, "text_size", self.price.size))
        self.status = Label(text="PAPER MODE", color=GREEN, font_size=dp(9), halign="right")
        self.status.bind(size=lambda *_: setattr(self.status, "text_size", self.status.size))
        info.add_widget(self.symbol)
        info.add_widget(self.price)
        info.add_widget(self.status)
        self.add_widget(info)

    def _sync_canvas(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(19))
        self._accent.pos = (self.x, self.y + dp(12))
        self._accent.size = (dp(3), self.height-dp(24))

    def update_market(self, symbol, ticker=None, price=None, fresh=False):
        """Show native quote and feed quality; never label every market as USDT."""
        if isinstance(ticker, dict):
            name = ticker.get("name_fa") or symbol
            self.symbol.text = f"{symbol} · {name}"
            value = ticker_display(ticker) if ticker.get("display_price", ticker.get("price")) else "قیمت ناموجود"
            self.price.text = value
            states = {"fresh": "دادهٔ تازه", "delayed": "دادهٔ تأخیری",
                      "estimated": "برآورد محاسباتی", "stale": "دادهٔ کهنه",
                      "unverified": "زمان منبع تأییدنشده", "unavailable": "قیمت ناموجود"}
            state = states.get(ticker.get("status"), "وضعیت نامشخص")
            self.status.text = f"حساب شبیه‌سازی · {state}"
            status = ticker.get("status")
            self.status.color = GREEN if status == "fresh" else GOLD if status in ("delayed", "estimated", "unverified") else MUTED
            return
        self.symbol.text = f"{symbol} · بازار انتخابی"
        self.price.text = f"قیمت {pxfmt(price)}" if price else "قیمت تازه —"
        self.status.text = "حساب شبیه‌سازی · دادهٔ تازه" if fresh and price else "حساب شبیه‌سازی · قیمت ناموجود"
        self.status.color = GREEN if fresh and price else MUTED


class Field(TextInput):
    def __init__(self, **kwargs):
        kwargs.setdefault("multiline", False)
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(50))
        kwargs.setdefault("padding", [dp(14), dp(14), dp(14), dp(14)])
        kwargs.setdefault("foreground_color", TEXT)
        kwargs.setdefault("cursor_color", GOLD)
        kwargs.setdefault("font_name", UI_FONT_NAME)
        super().__init__(
            background_normal="", background_active="",
            background_color=(0.045, 0.070, 0.100, 1),
            **kwargs,
        )


class CandleChart(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.candles = []
        self.bind(pos=self.redraw, size=self.redraw)

    def set_candles(self, candles):
        self.candles = candles or []
        self.redraw()

    def redraw(self, *a):
        self.canvas.clear()
        with self.canvas:
            Color(*CARD)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
        if not self.candles or self.width < 20:
            return
        data = self.candles[-60:]
        mn = min(c["low"] for c in data)
        mx = max(c["high"] for c in data)
        if mx <= mn:
            mx = mn + 1
        n = len(data)
        w = self.width / n
        bw = max(w * 0.55, 1.5)
        for i, c in enumerate(data):
            x = self.x + i * w + (w - bw) / 2
            def Y(v):
                return self.y + 6 + (v - mn) / (mx - mn) * (self.height - 12)
            up = c["close"] >= c["open"]
            with self.canvas:
                Color(*(GREEN if up else RED))
                Line(points=[x + bw / 2, Y(c["low"]), x + bw / 2, Y(c["high"])], width=1.2)
                y0, y1 = sorted([Y(c["open"]), Y(c["close"])])
                RoundedRectangle(pos=(x, y0), size=(bw, max(y1 - y0, 1.5)), radius=[1])


class TradeShareCard(BoxLayout):
    """Standalone, brand-styled card that can be exported as a PNG."""
    def __init__(self, position, mark_price=None, pnl=None, fresh=False, **kwargs):
        super().__init__(orientation="vertical", padding=dp(22), spacing=dp(12),
                         size_hint=(None, None), size=(min(dp(390), Window.width * 0.84), min(dp(520), Window.height * 0.72)), **kwargs)
        with self.canvas.before:
            Color(0.008, 0.014, 0.030, 1)
            self._bg = RoundedRectangle(radius=[dp(24)])
            Color(*GOLD)
            self._stripe = RoundedRectangle(pos=(self.x, self.y), size=(dp(5), self.height), radius=[dp(3)])
            Color(*LINE)
            self._border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(24)), width=1.2)
        self.bind(pos=self._sync_canvas, size=self._sync_canvas)

        def label(text, size=14, color=TEXT, bold=False, height=dp(28), halign="right"):
            w = Label(text=text, color=color, font_size=dp(size), bold=bold,
                      size_hint_y=None, height=height, halign=halign, valign="middle")
            w.bind(size=lambda *_: setattr(w, "text_size", w.size))
            return w

        side = str(position.get("side") or "LONG").upper()
        side_color = GREEN if side == "LONG" else RED
        self.add_widget(label("KALVO  /  MARKET INTELLIGENCE", 21, GOLD, True, dp(34), "left"))
        self.add_widget(label("PAPER POSITION  ·  گزارش معاملهٔ مجازی", 10, MUTED, False, dp(24), "left"))
        self.add_widget(Widget(size_hint_y=None, height=dp(8)))
        self.add_widget(label(f"{position.get('symbol','—')}   ·   {side}   ·   {float(position.get('leverage') or 1):g}×",
                              17, side_color, True, dp(42), "left"))
        pnl_value = float((pnl or {}).get("pnl") or 0.0) if fresh else None
        pnl_text = f"{pnl_value:+,.2f} USDT" if pnl_value is not None else "قیمت تازه در دسترس نیست"
        pnl_color = GREEN if pnl_value is not None and pnl_value >= 0 else RED if pnl_value is not None else MUTED
        self.add_widget(label(pnl_text, 29, pnl_color, True, dp(58), "left"))
        roe_text = f"بازده روی مارجین: {float(pnl.get('roe') or 0):+.2f}%" if fresh and pnl else "بازده در انتظار قیمت تازه"
        self.add_widget(label(roe_text, 13, MUTED, False, dp(28), "left"))
        self.add_widget(Widget(size_hint_y=None, height=dp(4)))
        entry = float(position.get("entry") or 0)
        qty = float(position.get("qty_open") or 0)
        margin = float(position.get("margin_open") or position.get("margin") or 0)
        values = (
            ("قیمت ورود", pxfmt(entry)),
            ("قیمت نشان‌گذاری", pxfmt(mark_price) if fresh else "—"),
            ("حجم باقی‌مانده", f"{qty:.7g}"),
            ("مارجین مجازی", f"{margin:,.2f} USDT"),
            ("حد سود", pxfmt(position.get("tp1")) if position.get("tp1") else "—"),
            ("حد ضرر", pxfmt(position.get("sl")) if position.get("sl") else "—"),
        )
        grid = GridLayout(cols=2, spacing=dp(7), size_hint_y=None, height=dp(132))
        for title, value in values:
            cell = BoxLayout(orientation="vertical", padding=[dp(9), dp(6)], spacing=dp(2))
            with cell.canvas.before:
                Color(*CARD)
                rect = RoundedRectangle(radius=[dp(10)])
            cell.bind(pos=lambda w, *_: setattr(rect, "pos", w.pos),
                      size=lambda w, *_: setattr(rect, "size", w.size))
            cell.add_widget(label(title, 10, MUTED, False, dp(21)))
            cell.add_widget(label(value, 13, TEXT, True, dp(25)))
            grid.add_widget(cell)
        self.add_widget(grid)
        self.add_widget(Widget())
        self.add_widget(label(datetime.now().strftime("%Y-%m-%d  %H:%M"), 11, MUTED, False, dp(24), "left"))
        self.add_widget(label("VIRTUAL FUNDS  ·  NOT A LIVE ORDER", 10, GOLD, True, dp(28), "left"))

    def _sync_canvas(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(24))
        self._stripe.pos = self.pos
        self._stripe.size = (dp(5), self.height)


class PositionCard(BoxLayout):
    """Expanded open-position card inspired by the user's exchange screenshot."""
    def __init__(self, owner, position, mark_price=None, fresh=False, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=dp(324),
                         padding=[dp(15), dp(13)], spacing=dp(7), **kwargs)
        self.owner = owner
        self.position = position
        self.mark_price = mark_price
        self.fresh = fresh
        with self.canvas.before:
            Color(*CARD)
            self._bg = RoundedRectangle(radius=[dp(19)])
            Color(*LINE)
            self._border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(19)), width=1.0)
            Color(*GOLD)
            self._stripe = RoundedRectangle(pos=(self.x, self.y + dp(14)), size=(dp(3), self.height-dp(28)), radius=[dp(2)])
        self.bind(pos=self._sync_canvas, size=self._sync_canvas)

        side = str(position.get("side") or "LONG").upper()
        side_color = GREEN if side == "LONG" else RED
        head = BoxLayout(size_hint_y=None, height=dp(33), spacing=dp(7))
        title = Label(text=position.get("symbol", "—"), color=TEXT, bold=True,
                      font_size=dp(17), halign="right", valign="middle")
        title.bind(size=lambda *_: setattr(title, "text_size", title.size))
        chip = Label(text="لانگ" if side == "LONG" else "شورت", color=side_color,
                     bold=True, font_size=dp(11), size_hint_x=None, width=dp(56))
        lev = Label(text=f"{float(position.get('leverage') or 1):g}×", color=GOLD,
                    bold=True, font_size=dp(12), size_hint_x=None, width=dp(55))
        origin = str(position.get("source") or "manual").lower()
        note = str(position.get("note") or "")
        if "decision_owner=AI" in note:
            origin_text = "✦ AI"
        elif origin == "auto":
            origin_text = "AUTO"
        else:
            origin_text = "MANUAL"
        origin_color = GOLD if origin in ("auto", "ai", "autopilot") else MUTED
        origin_lbl = Label(text=origin_text, color=origin_color, bold=True, font_size=dp(9),
                           size_hint_x=None, width=dp(62))
        head.add_widget(title); head.add_widget(origin_lbl); head.add_widget(chip); head.add_widget(lev)
        self.add_widget(head)

        if fresh and mark_price:
            metrics = db.position_pnl(position, mark_price)
            pnl_text = f"{metrics['pnl']:+,.4f} USDT"
            pnl_color = GREEN if metrics["pnl"] >= 0 else RED
            roi_text = f"ROE {metrics['roe']:+.2f}%"
        else:
            metrics = None
            pnl_text = "قیمت تازه در دسترس نیست"
            pnl_color = MUTED
            roi_text = "محاسبهٔ PnL متوقف تا دریافت قیمت تازه"
        pnl_row = BoxLayout(size_hint_y=None, height=dp(37), spacing=dp(8))
        pnl_lbl = Label(text=pnl_text, color=pnl_color, bold=True, font_size=dp(20), halign="left")
        pnl_lbl.bind(size=lambda *_: setattr(pnl_lbl, "text_size", pnl_lbl.size))
        roi_lbl = Label(text=roi_text, color=pnl_color if metrics else MUTED, font_size=dp(11), halign="right")
        roi_lbl.bind(size=lambda *_: setattr(roi_lbl, "text_size", roi_lbl.size))
        pnl_row.add_widget(pnl_lbl); pnl_row.add_widget(roi_lbl)
        self.add_widget(pnl_row)

        values = [
            ("قیمت ورود", pxfmt(position.get("entry"))),
            ("قیمت بازار", pxfmt(mark_price) if fresh else "—"),
            ("حجم", f"{float(position.get('qty_open') or 0):.7g}"),
            ("مارجین", f"{float(position.get('margin_open') or position.get('margin') or 0):,.2f}"),
            ("حد سود", pxfmt(position.get("tp1")) if position.get("tp1") else "—"),
            ("حد ضرر", pxfmt(position.get("sl")) if position.get("sl") else "—"),
        ]
        grid = GridLayout(cols=3, spacing=dp(6), size_hint_y=None, height=dp(76))
        for title_text, value_text in values:
            cell = BoxLayout(orientation="vertical", padding=[dp(5), dp(3)], spacing=dp(1))
            label = Label(text=title_text, color=MUTED, font_size=dp(9),
                          size_hint_y=None, height=dp(20), halign="right")
            label.bind(size=lambda *_args, w=label: setattr(w, "text_size", w.size))
            val = Label(text=value_text, color=TEXT, font_size=dp(11), bold=True, halign="right")
            val.bind(size=lambda *_args, w=val: setattr(w, "text_size", w.size))
            cell.add_widget(label); cell.add_widget(val); grid.add_widget(cell)
        self.add_widget(grid)

        pid, sym = position["id"], position["symbol"]
        actions_top = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(5))
        b25 = RoundButton(text="۲۵٪", bg=DARK_BTN, font_size=dp(10), height=dp(40))
        b50 = RoundButton(text="۵۰٪", bg=DARK_BTN, font_size=dp(10), height=dp(40))
        add_margin = RoundButton(text="افزایش مارجین", bg=BLUE_GLOW, font_size=dp(10), height=dp(40))
        edit_levels = RoundButton(text="تنظیم TP/SL", bg=DARK_BTN, fg=GOLD, font_size=dp(10), height=dp(40))
        b25.bind(on_release=lambda *_: owner.close_pos(pid, sym, 25))
        b50.bind(on_release=lambda *_: owner.close_pos(pid, sym, 50))
        add_margin.bind(on_release=lambda *_: owner.add_margin_dialog(position))
        edit_levels.bind(on_release=lambda *_: owner.edit_levels_dialog(position))
        for button in (b25, b50, add_margin, edit_levels):
            actions_top.add_widget(button)
        actions_bottom = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(6))
        b100 = RoundButton(text="بستن کامل پوزیشن", bg=RED, fg=TEXT, font_size=dp(11), height=dp(40))
        share = RoundButton(text="ساخت کارت تصویری", bg=BLUE_GLOW, fg=TEXT, font_size=dp(11), height=dp(40))
        b100.bind(on_release=lambda *_: owner.close_pos(pid, sym, 100))
        share.bind(on_release=lambda *_: owner.show_position_card(position, mark_price, metrics, fresh))
        actions_bottom.add_widget(b100); actions_bottom.add_widget(share)
        self.add_widget(actions_top)
        self.add_widget(actions_bottom)

    def _sync_canvas(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(19))
        self._stripe.pos = (self.x, self.y + dp(14))
        self._stripe.size = (dp(3), self.height-dp(28))


class TradeScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._auto = False
        self._auto_busy = False
        self._tick_busy = False
        self._auto_event = None
        self._smart_alert_last = 0.0

        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)], spacing=dp(8), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))
        content.add_widget(H("مرکز معاملات", 23, GOLD))
        content.add_widget(M("اسپات و قراردادهای مشتقه · مدیریت سفارش، موقعیت و ریسک"))

        top = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(120), spacing=dp(4), padding=dp(10))
        with top.canvas.before:
            Color(*CARD)
            self._top_bg = RoundedRectangle(radius=[dp(12)])
        top.bind(pos=lambda *_: setattr(self._top_bg, "pos", top.pos), size=lambda *_: setattr(self._top_bg, "size", top.size))
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.sym = Field(text=db.get_setting("default_symbol", "BTC"), hint_text="نماد: BTC · XAUF · WTIF · USDIRTF · EURIRTF")
        self.sym.size_hint_x = 0.45
        go = RoundButton(text="قیمت تازه", bg=GOLD, fg=(0, 0, 0, 1), size_hint_x=0.25)
        go.bind(on_release=lambda *_: self.fetch_price())
        self.order_type = Spinner(text="بازار", values=("بازار", "حد", "حد ضرر"), size_hint_x=0.3)
        self.order_type.background_color = DARK_BTN
        row.add_widget(self.sym)
        row.add_widget(go)
        row.add_widget(self.order_type)
        self.price_lbl = H("—", 26, GOLD)
        self.meta = M("منبع — · تغییر ۲۴س —")
        top.add_widget(row)
        top.add_widget(self.price_lbl)
        top.add_widget(self.meta)
        content.add_widget(top)
        content.add_widget(M("فلزات، انرژی و ارزهای خارجی · قراردادهای تمرینی ارزی بر پایهٔ شاخص نقدی TGJU هستند."))
        self.asset_picker = Spinner(
            text="انتخاب بازار مشتقه",
            values=tuple(["XAUF", "XAGF", "WTIF", "BRTF"] +
                         sorted(instruments.FX_FUTURES_SYMBOLS) +
                         sorted(instruments.FX_CROSS_FUTURES_SYMBOLS)),
            size_hint_y=None, height=dp(42),
        )
        self.asset_picker.background_color = DARK_BTN
        self.asset_picker.bind(text=lambda _sp, value: setattr(self.sym, "text", value)
                               if value in instruments.INSTRUMENTS else None)
        content.add_widget(self.asset_picker)

        side_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.btn_long = RoundButton(text="خرید / لانگ", bg=GREEN, fg=(0, 0, 0, 1))
        self.btn_short = RoundButton(text="فروش / شورت", bg=DARK_BTN, fg=TEXT)
        self.side = "LONG"
        self.btn_long.bind(on_release=lambda *_: self.set_side("LONG"))
        self.btn_short.bind(on_release=lambda *_: self.set_side("SHORT"))
        side_row.add_widget(self.btn_long)
        side_row.add_widget(self.btn_short)
        content.add_widget(side_row)

        grid = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(96))
        self.entry = Field(hint_text="قیمت ورود / فعال‌سازی", input_filter="float")
        self.lev = Field(text=db.get_setting("default_leverage", "2"), hint_text="اهرم (حداکثر ۵)", input_filter="float")
        self.margin = Field(text=db.get_setting("default_margin", "10"), hint_text="مارجین مجازی USDT", input_filter="float")
        self.trail = Field(text="0", hint_text="تریل: فاصله و فعال‌سازی %", input_filter="float")
        for w in (self.entry, self.lev, self.margin, self.trail):
            grid.add_widget(w)
        content.add_widget(grid)

        risk_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(7))
        self.risk_pct = Field(text=db.get_setting("default_risk_pct", "1"), hint_text="ریسک حساب %", input_filter="float", size_hint_x=0.34)
        risk_btn = RoundButton(text="محاسبه حجم با حد ضرر", bg=DARK_BTN, fg=GOLD)
        risk_btn.bind(on_release=lambda *_: self.calculate_risk())
        watch_btn = RoundButton(text="+ واچ‌لیست", bg=DARK_BTN, fg=TEXT, size_hint_x=0.28)
        watch_btn.bind(on_release=lambda *_: self.add_watch())
        risk_row.add_widget(self.risk_pct)
        risk_row.add_widget(risk_btn)
        risk_row.add_widget(watch_btn)
        content.add_widget(risk_row)

        grid2 = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(96))
        self.tp1 = Field(hint_text="حد سود TP1", input_filter="float")
        self.tp2 = Field(hint_text="حد سود TP2", input_filter="float")
        self.tp3 = Field(hint_text="حد سود TP3", input_filter="float")
        self.sl = Field(hint_text="حد ضرر SL", input_filter="float")
        for w in (self.tp1, self.tp2, self.tp3, self.sl):
            grid2.add_widget(w)
        content.add_widget(grid2)

        open_btn = RoundButton(text="ثبت سفارش", bg=GREEN, fg=(0.02, 0.08, 0.06, 1), size_hint_y=None, height=dp(52))
        open_btn.bind(on_release=lambda *_: self.open_pos())
        content.add_widget(open_btn)

        auto_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.auto_btn = RoundButton(text="فعال‌سازی مدیریت هوشمند", bg=GOLD, fg=(0.08, 0.07, 0.04, 1))
        self.auto_stop = RoundButton(text="توقف", bg=RED, fg=(1, 1, 1, 1), size_hint_x=0.35)
        self.auto_btn.bind(on_release=lambda *_: self.start_auto())
        self.auto_stop.bind(on_release=lambda *_: self.stop_auto())
        auto_row.add_widget(self.auto_btn)
        auto_row.add_widget(self.auto_stop)
        content.add_widget(auto_row)

        self.status = M("آماده · قیمت‌ها فقط هنگام دریافت موفق به‌روزرسانی می‌شوند")
        self.status.size_hint_y = None
        self.status.height = dp(34)
        content.add_widget(self.status)

        content.add_widget(H("سفارش‌های در انتظار", 14))
        self.order_box = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.order_box.bind(minimum_height=self.order_box.setter("height"))
        content.add_widget(self.order_box)
        content.add_widget(H("پوزیشن‌های باز", 14))
        self.pos_box = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        self.pos_box.bind(minimum_height=self.pos_box.setter("height"))
        content.add_widget(self.pos_box)

        scroll.add_widget(content)
        self.add_widget(scroll)
        interval = max(5, min(60, int(db.get_setting("refresh_interval", "8"))))
        self._refresh_event = Clock.schedule_interval(lambda dt: self.tick(), interval)
        Clock.schedule_once(lambda dt: self.tick(), 0.5)

    def set_side(self, s):
        self.side = s
        if s == "LONG":
            self.btn_long.background_color = GREEN
            self.btn_long.color = (0, 0, 0, 1)
            self.btn_short.background_color = DARK_BTN
            self.btn_short.color = TEXT
        else:
            self.btn_short.background_color = RED
            self.btn_short.color = (1, 1, 1, 1)
            self.btn_long.background_color = DARK_BTN
            self.btn_long.color = TEXT

    def fetch_price(self):
        sym = self.sym.text.strip() or "BTC"
        self.status.text = "دریافت قیمت تازه از منابع عمومی…"
        def work():
            try:
                base = canonical_market_symbol(sym)
                if instruments.is_reference_symbol(base):
                    raise ValueError("USDT/IRT فقط نرخ مرجع تبدیل است و قابل معامله نیست")
                if instruments.is_instrument(base) and instruments.get_instrument(base).get("market") != "futures":
                    raise ValueError("برای صفحهٔ فیوچرز از نماد آتی استفاده کن؛ نمونه: XAUF یا WTIF")
                ticker = mapi.get_ticker(base)
                px = ticker.get("price")
                if not px or ticker.get("stale") or not ticker.get("trade_demo", True):
                    raise ValueError(ticker.get("error") or "قیمت معتبر برای شبیه‌سازی در دسترس نیست")
                def ui(*_):
                    app = App.get_running_app()
                    base = ticker.get("base") or canonical_market_symbol(sym)
                    app.set_symbol(base)
                    app.price_cache[base] = float(px)
                    app.price_cache[f"{base}-USDT"] = float(px)
                    app.price_cache_ts[base] = float(ticker.get("ts") or time.time())
                    app.current_ticker = ticker
                    self.price_lbl.text = ticker_display(ticker)
                    ch = ticker.get("change_pct")
                    chs = f"{ch:+.2f}%" if ch is not None else "—"
                    status_fa = {"fresh": "تازه", "delayed": "با تأخیر", "estimated": "محاسباتی", "unverified": "زمان تأییدنشده"}.get(ticker.get("status"), "نامشخص")
                    ledger_px = pxfmt(px)
                    self.meta.text = f"{status_fa} · معادل دفترکل: {ledger_px} USDT · منبع {ticker.get('source') or '—'} · تغییر {chs}"
                    self.meta.color = GREEN if ticker.get("status") == "fresh" else GOLD if ticker.get("status") in ("delayed", "estimated") else MUTED
                    app.current_symbol = base
                    app.current_price = float(px)
                    app.current_price_ts = time.time()
                    app.refresh_header()
                    if self.order_type.text == "بازار":
                        self.entry.text = ""
                    self.status.text = "قیمت بازار دریافت شد؛ منبع و تأخیر آن در بالا مشخص شده است"
                    self.refresh_positions()
                Clock.schedule_once(ui)
            except Exception as exc:
                message = str(exc)[:110]
                def failed(*_):
                    self.status.text = f"دریافت ناموفق: {message}"
                    self.meta.text = "قیمت تازه در دسترس نیست؛ مقدار قبلی را تازه فرض نکن"
                    self.meta.color = MUTED
                Clock.schedule_once(failed)
        _run_background(work)

    def open_pos(self):
        try:
            sym = canonical_market_symbol(self.sym.text.strip() or "BTC")
            if instruments.is_reference_symbol(sym):
                raise ValueError("USDT/IRT نرخ مرجع تبدیل است و قابل معامله نیست")
            if instruments.is_instrument(sym) and instruments.get_instrument(sym).get("market") != "futures":
                raise ValueError("این نماد نقدی است؛ برای فلزات و نفت در فیوچرز از XAUF / XAGF / WTIF / BRTF استفاده کن")
            order_mode = {"بازار": "MARKET", "حد": "LIMIT", "حد ضرر": "STOP"}.get(self.order_type.text, "MARKET")
            app = App.get_running_app()
            margin = float(self.margin.text or 0)
            leverage = float(self.lev.text or 0)
            tp1 = float(self.tp1.text) if self.tp1.text else None
            tp2 = float(self.tp2.text) if self.tp2.text else None
            tp3 = float(self.tp3.text) if self.tp3.text else None
            sl = float(self.sl.text) if self.sl.text else None
            trail = float(self.trail.text or 0)

            def _submit(trigger_price):
                result = db.create_order(sym, self.side, order_mode, trigger_price, leverage, margin,
                                         tp1=tp1, tp2=tp2, tp3=tp3, sl=sl,
                                         trail_pct=trail, note="ثبت از ترمینال")
                if result.get("status") == "pending":
                    self.status.text = f"سفارش {result['order_type']} #{result['id']} ثبت شد؛ منتظر رسیدن قیمت"
                else:
                    self.status.text = f"موقعیت #{result['id']} باز شد · حجم {result['qty']:.6g}"
                self.refresh_positions()
                self.refresh_orders()
                app.refresh_wallet()
                try:
                    from apex import bus
                    bus.publish("positions_changed", source="open_pos")
                    bus.publish("wallet_changed", source="open_pos")
                except Exception:
                    pass

            if order_mode == "MARKET":
                base = mapi.normalize_symbol(sym)[0]
                self.status.text = "دریافت مظنهٔ تازه و محاسبهٔ قیمت اجرای شبیه‌سازی…"
                def work():
                    try:
                        from apex.execution_simulator import market_fill
                        ticker = mapi.get_ticker(sym)
                        action = "BUY" if self.side == "LONG" else "SELL"
                        slip = float(db.get_setting("paper_slippage_bps", "2.0") or 2.0)
                        execution = market_fill(ticker, action, slippage_bps=slip, require_fresh=True)
                        px = float(execution["fill_price"])
                        def ui(*_):
                            app.price_cache[base] = float(ticker.get("price") or px)
                            app.price_cache[f"{base}-USDT"] = float(ticker.get("price") or px)
                            app.price_cache_ts[base] = time.time()
                            app.current_symbol = base
                            app.current_price = float(ticker.get("price") or px)
                            app.current_price_ts = time.time()
                            app.current_ticker = ticker
                            self.price_lbl.text = ticker_display(ticker)
                            app.refresh_header()
                            try:
                                _submit(px)
                                self.status.text += (f" · مبنای اجرا: {action} · اسپرد {execution['spread_pct']:.4f}% "
                                                     f"· لغزش {execution['slippage_bps']:.1f} bps · فقط دمو")
                            except Exception as exc:
                                self.status.text = f"خطا: {str(exc)[:140]}"
                        Clock.schedule_once(ui)
                    except Exception as exc:
                        msg = str(exc)[:140]
                        Clock.schedule_once(lambda *_a, message=msg: setattr(
                            self.status, "text", f"باز شدن ناموفق: {message}"
                        ))
                _run_background(work)
                return
            trigger = float(self.entry.text or 0)
            if trigger <= 0:
                self.status.text = "برای سفارش حد یا توقف، قیمت فعال‌سازی را وارد کن"
                return
            _submit(trigger)
        except Exception as exc:
            self.status.text = f"خطا: {str(exc)[:140]}"

    def calculate_risk(self):
        try:
            entry = float(self.entry.text or 0)
            sl = float(self.sl.text or 0)
            leverage = float(self.lev.text or 0)
            risk_pct = float(self.risk_pct.text or 0)
            if entry <= 0 or sl <= 0:
                raise ValueError("قیمت ورود و حد ضرر را وارد کن")
            if self.side == "LONG" and sl >= entry:
                raise ValueError("برای لانگ، حد ضرر باید پایین‌تر از ورود باشد")
            if self.side == "SHORT" and sl <= entry:
                raise ValueError("برای شورت، حد ضرر باید بالاتر از ورود باشد")
            result = db.risk_size(float(db.get_wallet().get("balance") or 0),
                                  risk_pct, entry, sl, leverage)
            self.margin.text = f"{result['margin']:.4f}"
            self.status.text = (f"بودجه ریسک با احتساب بافر کارمزد ≈ ${result['risk_budget']:.2f} · "
                                f"مارجین پیشنهادی ${result['margin']:.2f} · "
                                f"حجم {result['quantity']:.6g}")
        except Exception as exc:
            self.status.text = f"محاسبه ریسک انجام نشد: {str(exc)[:120]}"

    def add_watch(self):
        try:
            symbol = canonical_market_symbol(self.sym.text or "BTC")
            db.watchlist_add(symbol)
            self.status.text = f"{symbol} به واچ‌لیست شخصی اضافه شد؛ در رادار بازار تازه‌سازی کن"
        except Exception as exc:
            self.status.text = str(exc)[:120]

    def refresh_positions(self):
        self.pos_box.clear_widgets()
        app = App.get_running_app()
        positions = db.list_open_positions()
        if not positions:
            self.pos_box.add_widget(M("هنوز پوزیشن بازی وجود ندارد؛ معاملات این بخش کاملاً مجازی‌اند"))
            return
        for position in positions:
            base = position["symbol"].replace("-USDT", "")
            px = app.price_cache.get(base)
            stamp = app.price_cache_ts.get(base, 0)
            fresh = bool(px and time.time() - stamp <= 30)
            self.pos_box.add_widget(PositionCard(self, position, px, fresh))

    def show_position_card(self, position, mark_price=None, metrics=None, fresh=False):
        """Preview and export a polished, clearly labelled paper-trade image."""
        try:
            card = TradeShareCard(position, mark_price, metrics, fresh)
            wrapper = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
            wrapper.add_widget(card)
            actions = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
            save = RoundButton(text="ذخیره تصویر PNG", bg=GOLD, fg=(0, 0, 0, 1))
            close = RoundButton(text="بستن", bg=DARK_BTN)
            actions.add_widget(save); actions.add_widget(close)
            wrapper.add_widget(actions)
            popup = Popup(title="گزارش معامله", content=wrapper,
                          size_hint=(0.96, 0.94), auto_dismiss=True,
                          background="", background_color=BG)
            def save_image(*_):
                try:
                    directory = os.path.join(App.get_running_app().user_data_dir, "position_cards")
                    os.makedirs(directory, exist_ok=True)
                    filename = f"KALVO_{position.get('symbol','POSITION').replace('/', '-')}_{position.get('id','NA')}_{int(time.time())}.png"
                    path = os.path.join(directory, filename)
                    def export_later(_dt):
                        try:
                            card.export_to_png(path, scale=2)
                            self.status.text = f"کارت معامله ذخیره شد: {path}"
                            save.text = "تصویر ذخیره شد ✓"
                            save.disabled = True
                        except Exception as exc:
                            self.status.text = f"ذخیرهٔ تصویر ناموفق: {str(exc)[:100]}"
                    Clock.schedule_once(export_later, 0.35)
                except Exception as exc:
                    self.status.text = f"خطا در ذخیرهٔ کارت: {str(exc)[:100]}"
            save.bind(on_release=save_image)
            close.bind(on_release=lambda *_: popup.dismiss())
            popup.open()
        except Exception as exc:
            self.status.text = f"ساخت کارت معامله ناموفق بود: {str(exc)[:100]}"

    def add_margin_dialog(self, position):
        amount = Field(text="5", input_filter="float", hint_text="مقدار USDT از اعتبار آزاد")
        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        body.add_widget(M(f"افزایش مارجین پوزیشن #{position['id']} · {position['symbol']}"))
        body.add_widget(amount)
        actions = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        popup = Popup(title="افزایش مارجین مجازی", content=body, size_hint=(0.9, 0.36),
                      background="", background_color=BG)
        confirm = RoundButton(text="تأیید افزایش", bg=GOLD, fg=(0,0,0,1))
        cancel = RoundButton(text="انصراف", bg=DARK_BTN)
        def apply(*_):
            try:
                result = db.add_position_margin(position['id'], float(amount.text or 0))
                self.status.text = f"مارجین پوزیشن افزایش یافت؛ مارجین جدید {result['margin_open']:.2f} USDT"
                popup.dismiss()
                self.refresh_positions()
                App.get_running_app().refresh_wallet()
            except Exception as exc:
                self.status.text = f"افزایش مارجین ناموفق: {str(exc)[:120]}"
        confirm.bind(on_release=apply); cancel.bind(on_release=lambda *_: popup.dismiss())
        actions.add_widget(confirm); actions.add_widget(cancel); body.add_widget(actions)
        popup.open()

    def edit_levels_dialog(self, position):
        body = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        body.add_widget(M(f"تنظیم سطوح برای {position['symbol']} · {position['side']}"))
        grid = GridLayout(cols=2, spacing=dp(7), size_hint_y=None, height=dp(104))
        fields = {}
        for key, title in (("tp1","TP1"),("tp2","TP2"),("tp3","TP3"),("sl","حد ضرر")):
            old_value = position.get(key)
            field = Field(text=str(old_value) if old_value is not None else "",
                          hint_text=f"{title} · خالی = حذف", input_filter="float")
            fields[key] = field
            grid.add_widget(field)
        body.add_widget(grid)
        trail = Field(text=str(position.get('trail_pct') or 0), hint_text="فاصلهٔ تریل %", input_filter="float")
        body.add_widget(trail)
        actions = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        popup = Popup(title="حد سود و حد ضرر موقعیت", content=body, size_hint=(0.94, 0.60),
                      background="", background_color=BG)
        confirm = RoundButton(text="ذخیرهٔ تغییرات", bg=GOLD, fg=(0,0,0,1))
        cancel = RoundButton(text="انصراف", bg=DARK_BTN)
        def apply(*_):
            try:
                db.update_position_risk_levels(position['id'],
                    tp1=fields['tp1'].text or None, tp2=fields['tp2'].text or None,
                    tp3=fields['tp3'].text or None, sl=fields['sl'].text or None,
                    trail_pct=trail.text or 0)
                self.status.text = f"سطوح ریسک پوزیشن #{position['id']} ذخیره شد"
                popup.dismiss()
                self.refresh_positions()
            except Exception as exc:
                self.status.text = f"سطوح نامعتبر یا ذخیره ناموفق: {str(exc)[:120]}"
        confirm.bind(on_release=apply); cancel.bind(on_release=lambda *_: popup.dismiss())
        actions.add_widget(confirm); actions.add_widget(cancel); body.add_widget(actions)
        popup.open()

    def refresh_orders(self):
        self.order_box.clear_widgets()
        for order in db.list_pending_orders():
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
            row.add_widget(Label(
                text=f"#{order['id']} {order['symbol']} {order['side']} {order['order_type']} @ {pxfmt(order['trigger_price'])}",
                color=TEXT, font_size=dp(11)))
            cancel = RoundButton(text="لغو", bg=RED, fg=(1, 1, 1, 1), size_hint_x=0.22)
            cancel.bind(on_release=lambda btn, oid=order["id"]: self.cancel_pending(oid))
            row.add_widget(cancel)
            self.order_box.add_widget(row)
        if not db.list_pending_orders():
            self.order_box.add_widget(M("سفارش در انتظاری وجود ندارد"))

    def cancel_pending(self, order_id):
        if db.cancel_order(order_id):
            self.status.text = f"سفارش #{order_id} لغو شد"
        self.refresh_orders()

    def close_pos(self, pid, sym, pct):
        self.status.text = "دریافت مظنهٔ دوطرفه برای بستن شبیه‌سازی‌شده…"
        def work():
            try:
                from apex.execution_simulator import market_fill
                position = next((p for p in db.list_open_positions() if int(p.get("id") or 0) == int(pid)), None)
                if not position:
                    raise ValueError("پوزیشن باز پیدا نشد")
                action = "SELL" if position.get("side") == "LONG" else "BUY"
                ticker = mapi.get_ticker(sym)
                slip = float(db.get_setting("paper_slippage_bps", "2.0") or 2.0)
                execution = market_fill(ticker, action, slippage_bps=slip,
                                         require_fresh=True, max_spread_pct=5.0)
                result = db.close_position(pid, float(execution["fill_price"]), pct, "MANUAL")
                Clock.schedule_once(lambda *_: self._closed(result))
            except Exception as exc:
                message = str(exc)[:140]
                Clock.schedule_once(lambda *_a, msg=message: setattr(self.status, "text", f"خطا: {msg}"))
        _run_background(work)

    def _closed(self, result):
        self.status.text = f"بسته شد · سود/زیان خالص {money(result['pnl'])} · کارمزد {money(result['fee'])}"
        try:
            db.notification_add("position_closed", "پوزیشن بسته شد",
                                f"سود/زیان خالص {money(result['pnl'])} USDT · کارمزد {money(result['fee'])} USDT")
        except Exception:
            pass
        self.refresh_positions()
        App.get_running_app().refresh_wallet()
        try:
            from apex import learning_memory as _lm
            _lm.refresh_if_new_trades()
        except Exception:
            pass
        try:
            from apex import bus
            bus.publish("positions_changed", source="close")
            bus.publish("wallet_changed", source="close")
            bus.publish("trade_closed", result=result)
        except Exception:
            pass

    def tick(self):
        if self._tick_busy:
            return
        self._tick_busy = True
        def work():
            try:
                symbols = [self.sym.text.strip() or "BTC"]
                symbols += [p["symbol"] for p in db.list_open_positions()]
                symbols += [o["symbol"] for o in db.list_pending_orders()]
                symbols = list(dict.fromkeys(symbols))
                alert_symbols = [a["symbol"] for a in db.alerts_open(30)]
                symbols = list(dict.fromkeys(symbols + alert_symbols))[:40]
                quotes = market_data_hub.get_quotes_batch(symbols, now=time.time())
                prices = {}
                for symbol, quote in quotes.items():
                    if quote.get("quote_valid") and quote.get("status") == "fresh":
                        base = str(quote.get("base") or quote.get("canonical_symbol") or symbol).upper()
                        prices[base] = float(quote["price"])
                        prices[f"{base}-USDT"] = float(quote["price"])
                pending_events = []
                risk_events = []
                alert_events = []
                # Position and alert lifecycle is supervised app-wide by KALVOApp,
                # not by whichever screen happens to be visible.
                if prices:
                    now = time.time()
                    def ui(*_):
                        app = App.get_running_app()
                        for key, value in prices.items():
                            if key.endswith("-USDT") or key.endswith("USDT"):
                                continue
                            app.price_cache[key] = value
                            app.price_cache[f"{key}-USDT"] = value
                            app.price_cache_ts[key] = now
                        try:
                            current_base = canonical_market_symbol(self.sym.text.strip() or "BTC")
                        except ValueError:
                            current_base = ""
                        current_px = prices.get(current_base) if current_base else None
                        if current_px:
                            self.price_lbl.text = pxfmt(current_px)
                            self.meta.text = "منابع عمومی بازار · قیمت در این نوبت تازه‌سازی شد"
                            self.meta.color = MUTED
                            app.current_symbol = current_base
                            app.current_price = float(current_px)
                            app.current_price_ts = now
                            app.refresh_header()
                        else:
                            self.meta.text = "قیمت تازهٔ این نماد دریافت نشد؛ مقدار قبلی ممکن است کهنه باشد"
                            self.meta.color = MUTED
                        if pending_events or risk_events or alert_events:
                            self.status.text = " · ".join(pending_events + risk_events + alert_events)
                        elif not current_px:
                            self.status.text = "قیمت تازه برای نماد انتخابی دریافت نشد؛ سفارش بازار غیرفعال می‌ماند"
                        self.refresh_positions()
                        self.refresh_orders()
                        app.refresh_wallet()
                        try:
                            from apex import bus
                            bus.publish("prices", prices=dict(prices), ts=now)
                            bus.publish("positions_changed", source="tick")
                        except Exception:
                            pass
                    Clock.schedule_once(ui)
                else:
                    def no_fresh_prices(*_):
                        self.meta.text = "قیمت تازه دریافت نشد؛ مقدار قبلی ممکن است کهنه باشد"
                        self.meta.color = MUTED
                        self.status.text = "هیچ قیمت تازه‌ای از منابع عمومی دریافت نشد"
                    Clock.schedule_once(no_fresh_prices)
            except Exception as exc:
                message = str(exc)[:80]
                def tick_failed(*_):
                    self.status.text = f"به‌روزرسانی بازار ناموفق: {message}"
                    self.meta.text = "قیمت تازه در دسترس نیست؛ مقدار قبلی را تازه فرض نکن"
                    self.meta.color = MUTED
                Clock.schedule_once(tick_failed)
            finally:
                self._tick_busy = False
        _run_background(work)

    def start_auto(self):
        if self._auto:
            self.status.text = "Auto-Trade دمو از قبل فعال است"
            return
        self._auto = True
        self.status.text = "اسکن خودکار دمو آغاز شد؛ برای توقف از دکمه توقف استفاده کن"
        Clock.schedule_once(lambda *_: self._auto_cycle(), 0.1)

    def _auto_cycle(self):
        if not self._auto or self._auto_busy:
            return
        self._auto_busy = True

        def work():
            try:
                outcome = paper_auto.scan_once()
                opened = outcome.get("opened") or []
                skipped = outcome.get("skipped") or []
                audit = paper_auto.format_scan_report(outcome)
                # Persist every completed scan so the local coach can explain
                # why a symbol was accepted or rejected in a later chat turn.
                db.add_chat("analysis", audit, "KALVO Auto Audit")
                for item in opened:
                    db.notification_add(
                        "auto_trade", "KALVO AI · پوزیشن جدید",
                        f"#{item['id']} · {item['symbol']} · {item['side']} · امتیاز ارزیابی {item['score']}",
                    )
                if opened:
                    detail = "، ".join(f"{x['symbol']} {x['side']} (امتیاز {x['score']})" for x in opened)
                    message = f"اسکن چندبازه‌ای دمو: {detail}"
                else:
                    message = outcome.get("message") or "ورودی دمو پیدا نشد"
                if skipped:
                    top_reasons = "؛ ".join(f"{sym}: {reason}" for sym, reason in skipped[:2])
                    message += f" · {len(skipped)} رد شد · {top_reasons}"
            except Exception as exc:
                message = f"اسکن دمو ناموفق: {str(exc)[:100]}"
            finally:
                self._auto_busy = False
                def ui(*_):
                    self.status.text = message
                    self.refresh_positions()
                    self.refresh_orders()
                    App.get_running_app().refresh_wallet()
                    App.get_running_app().refresh_header()
                    try:
                        from apex import learning_memory as _lm
                        _lm.refresh_if_new_trades()
                    except Exception:
                        pass
                    try:
                        from apex import bus
                        bus.publish("auto_scan_done", message=message)
                        bus.publish("positions_changed", source="auto")
                    except Exception:
                        pass
                    if self._auto:
                        interval = paper_auto.get_auto_config().get("scan_interval", 120)
                        self._auto_event = Clock.schedule_once(lambda dt: self._auto_cycle(), interval)
                Clock.schedule_once(ui)

        _run_background(work)

    def stop_auto(self):
        self._auto = False
        if self._auto_event is not None:
            self._auto_event.cancel()
            self._auto_event = None
        self.status.text = "Auto-Trade دمو متوقف شد؛ پوزیشن‌های باز همچنان طبق TP/SL مدیریت می‌شوند"


class ChartScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        root = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(8))
        root.add_widget(H("چارت و بازپخش بازار", 22, GOLD))
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.sym = Field(text="BTC")
        self.tf = Spinner(text=db.get_setting("default_timeframe", "1hour"), values=("5min", "15min", "30min", "1hour", "4hour", "1day", "1week"))
        btn = RoundButton(text="بارگذاری", bg=GOLD, fg=(0, 0, 0, 1), size_hint_x=0.35)
        btn.bind(on_release=lambda *_: self.load())
        row.add_widget(self.sym)
        row.add_widget(self.tf)
        row.add_widget(btn)
        self.chart = CandleChart()
        self.info = M("داده از منابع عمومی · بازپخش فقط روی کندل‌های بسته‌شده")
        replay_row = GridLayout(cols=4, size_hint_y=None, height=dp(50), spacing=dp(6))
        self.replay_reset_btn = RoundButton(text="شروع", bg=BLUE_GLOW, font_size=dp(11))
        self.replay_back_btn = RoundButton(text="قبلی", bg=DARK_BTN, font_size=dp(11))
        self.replay_next_btn = RoundButton(text="گام بعد", bg=GREEN, fg=(0,0,0,1), font_size=dp(11))
        self.replay_end_btn = RoundButton(text="پایان", bg=DARK_BTN, font_size=dp(11))
        self.replay_reset_btn.bind(on_release=lambda *_: self.reset_replay())
        self.replay_back_btn.bind(on_release=lambda *_: self.step_replay(-1))
        self.replay_next_btn.bind(on_release=lambda *_: self.step_replay(1))
        self.replay_end_btn.bind(on_release=lambda *_: self.step_replay(999999))
        for widget in (self.replay_reset_btn, self.replay_back_btn, self.replay_next_btn, self.replay_end_btn):
            replay_row.add_widget(widget)
        self.replay_session = None
        root.add_widget(row)
        root.add_widget(replay_row)
        root.add_widget(self.chart)
        root.add_widget(self.info)
        self.add_widget(root)

    def reset_replay(self):
        if not self.replay_session:
            self.info.text = "ابتدا کندل‌ها را بارگذاری کن"
            return
        self.replay_session.reset()
        self._render_replay()

    def step_replay(self, direction):
        if not self.replay_session:
            self.info.text = "ابتدا کندل‌ها را بارگذاری کن"
            return
        if direction > 0:
            self.replay_session.step(direction if direction < 1000 else 1000000)
        else:
            self.replay_session.back(abs(direction))
        self._render_replay()

    def _render_replay(self):
        if not self.replay_session:
            return
        window = self.replay_session.current_window()
        self.chart.set_candles(window)
        self.info.text = (f"بازپخش آموزشی · {self.replay_session.index}/{len(self.replay_session.candles)} کندل · "
                          f"پیشرفت {self.replay_session.progress * 100:.1f}% · بدون سفارش واقعی")

    def load(self):
        sym = self.sym.text.strip() or "BTC"
        tf = self.tf.text
        try:
            App.get_running_app().set_symbol(sym)
            sym = App.get_running_app().current_symbol
        except ValueError as exc:
            self.info.text = str(exc)
            return
        self.info.text = "بارگذاری کندل…"
        def work():
            try:
                c = mapi.fetch_candles(sym, tf)
                completed = analysis.completed_candles(c, tf)
                replay = market_replay.CandleReplay(completed, visible=50)
                Clock.schedule_once(lambda *_: self._set_replay(replay, sym, tf))
            except Exception as e:
                message = str(e)[:120]
                Clock.schedule_once(lambda *_: setattr(self.info, "text", f"خطا: {message}"))
        _run_background(work)

    def _set_replay(self, replay, symbol, timeframe):
        self.replay_session = replay
        self._render_replay()
        self.info.text += f" · {symbol} · {timeframe}"


class MarketScreen(Screen):
    """Market overview with a compact watchlist and optional derivatives snapshot."""
    CRYPTO_WATCHLIST = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX", "TON"]
    COMMODITY_WATCHLIST = ["XAU", "XAG", "XAUF", "XAGF", "IRG18", "IRS999", "IRS925", "WTI", "BRENT", "WTIF", "BRTF"]
    FX_WATCHLIST = (
        sorted(instruments.FX_SPOT_SYMBOLS) + sorted(instruments.FX_FUTURES_SYMBOLS) +
        sorted(instruments.FX_CROSS_SPOT_SYMBOLS) + sorted(instruments.FX_CROSS_FUTURES_SYMBOLS)
    )
    WATCHLIST = CRYPTO_WATCHLIST + COMMODITY_WATCHLIST + FX_WATCHLIST + ["USDTIRT"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._busy = False
        root = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(7))
        root.add_widget(H("کشف و پایش بازار", 23, GOLD))
        root.add_widget(M("یک جست‌وجو برای رمزارز، فلزات، انرژی و ارزهای جهانی و ایران"))

        search_row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(7))
        self.search_field = Field(hint_text="جست‌وجوی نماد یا نام دارایی؛ مثل SO، XRP، دلار، EUR")
        self.search_button = RoundButton(text="جست‌وجو", bg=GOLD, fg=(0.08, 0.07, 0.04, 1), size_hint_x=0.27, font_size=dp(11))
        self.search_button.bind(on_release=lambda *_: self._run_symbol_search(self.search_field.text))
        self.search_field.bind(text=self._search_text_changed)
        search_row.add_widget(self.search_field); search_row.add_widget(self.search_button)
        root.add_widget(search_row)
        self.search_status = M("جست‌وجوی زنده در رمزارزها، فلزات، انرژی و نرخ ارز ایران · نمونه: SO → SOL")
        self.search_status.size_hint_y = None; self.search_status.height = dp(28)
        root.add_widget(self.search_status)
        self.suggestion_scroll = ScrollView(do_scroll_x=False, size_hint_y=None, height=0, opacity=0)
        self.suggestions = GridLayout(cols=1, spacing=dp(4), size_hint_y=None, height=0)
        self.suggestion_scroll.add_widget(self.suggestions)
        root.add_widget(self.suggestion_scroll)
        self._search_event = None
        self._search_generation = 0

        category_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(7))
        self.market_filter = Spinner(text="همه بازارها",
                                     values=("همه بازارها", "رمزارزها", "فلزات جهانی", "فلزات ایران", "انرژی", "فارکس و ارزهای آزاد"),
                                     size_hint_x=0.50)
        self.market_filter.background_color = DARK_BTN
        self.market_filter.bind(text=lambda *_: self.refresh_watchlist())
        category_row.add_widget(self.market_filter)
        category_row.add_widget(M("فیلتر دستهٔ بازار"))
        root.add_widget(category_row)

        bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(7))
        self.coin = Spinner(text=db.get_setting("default_symbol", "BTC"), values=tuple(self.WATCHLIST), size_hint_x=0.34)
        self.coin.background_color = DARK_BTN
        self.refresh_btn = RoundButton(text="به‌روزرسانی", bg=GOLD, fg=(0, 0, 0, 1))
        self.refresh_btn.bind(on_release=lambda *_: self.refresh_watchlist())
        self.detail_btn = RoundButton(text="جزئیات", bg=DARK_BTN)
        self.detail_btn.bind(on_release=lambda *_: self.load_details())
        bar.add_widget(self.coin)
        bar.add_widget(self.refresh_btn)
        bar.add_widget(self.detail_btn)
        root.add_widget(bar)
        root.add_widget(M("قیمت بومی، معادل دفترکل، تغییرات و کیفیت داده در یک قالب هماهنگ نمایش داده می‌شوند."))

        self.status = M("برای دریافت نمای کلی، به‌روزرسانی را بزن")
        root.add_widget(self.status)
        cards = GridLayout(cols=2, size_hint_y=None, height=dp(156), spacing=dp(7))
        self.card_price = MetricCard("قیمت فعلی", "—", GOLD)
        self.card_change = MetricCard("تغییر ۲۴ ساعته", "—", GREEN)
        self.card_spread = MetricCard("اسپرد", "—", TEXT)
        self.card_funding = MetricCard("Funding", "—", TEXT)
        for card in (self.card_price, self.card_change, self.card_spread, self.card_funding):
            cards.add_widget(card)
        root.add_widget(cards)
        self.detail = TextInput(
            text="جزئیات بازار انتخاب‌شده اینجا نمایش داده می‌شود.",
            readonly=True, multiline=True, size_hint_y=None, height=dp(142),
            background_normal="", background_active="", background_color=CARD,
            foreground_color=TEXT, cursor_color=GOLD, padding=dp(9), font_size=dp(12),
        )
        root.add_widget(self.detail)
        self.chart_shortcut = RoundButton(
            text="بازکردن نمودار حرفه‌ای و بازپخش کندل", bg=BLUE_GLOW,
            size_hint_y=None, height=dp(50), font_size=dp(13),
        )
        self.chart_shortcut.bind(on_release=lambda *_: App.get_running_app().navigate_to("chart"))
        root.add_widget(self.chart_shortcut)
        root.add_widget(H("فهرست پیگیری", 14))
        self.rows = GridLayout(cols=1, spacing=dp(4), size_hint_y=None)
        self.rows.bind(minimum_height=self.rows.setter("height"))
        sc = ScrollView(do_scroll_x=False)
        sc.add_widget(self.rows)
        root.add_widget(sc)
        self.add_widget(root)

    def _search_text_changed(self, _field, value):
        query = (value or "").strip()
        if self._search_event is not None:
            self._search_event.cancel()
            self._search_event = None
        if not query:
            self.suggestions.clear_widgets()
            self.suggestions.height = 0
            self.suggestion_scroll.height = 0; self.suggestion_scroll.opacity = 0
            self.search_status.text = "نماد را تایپ کن؛ پیشنهادها با قیمت و وضعیت منبع ظاهر می‌شوند."
            return
        self.search_status.text = "در حال آماده‌سازی پیشنهادها…"
        self._search_event = Clock.schedule_once(lambda *_: self._run_symbol_search(query), 0.45)

    def _run_symbol_search(self, query):
        query = (query or "").strip()
        if not query:
            return
        self._search_generation += 1
        generation = self._search_generation
        self.search_status.text = f"جست‌وجوی «{query}» در فهرست ابزارها و بازارهای متصل…"
        self.search_button.disabled = True
        def work():
            try:
                result = mapi.search_symbol_suggestions(query, limit=7)
            except Exception as exc:
                result = {"query": query, "items": [], "found": False, "message": str(exc)[:120]}
            Clock.schedule_once(lambda *_: self._apply_symbol_search(query, generation, result))
        _run_background(work)

    def _apply_symbol_search(self, query, generation, result):
        self.search_button.disabled = False
        if generation != self._search_generation or query != (self.search_field.text or "").strip():
            return
        self.suggestions.clear_widgets()
        items = result.get("items") or []
        if not items:
            self.suggestions.height = 0
            self.suggestion_scroll.height = 0; self.suggestion_scroll.opacity = 0
            self.search_status.text = f"نماد «{query}» در منابع متصل شناسایی نشد؛ قیمت ساختگی نمایش داده نمی‌شود."
            return
        self.suggestions.height = dp(49 * len(items))
        self.suggestion_scroll.height = dp(150); self.suggestion_scroll.opacity = 1
        self.search_status.text = f"{len(items)} پیشنهاد · نمادهای شناسایی‌شده با قیمت یا وضعیت منبع"
        for item in items:
            symbol = item.get("symbol") or "—"
            name = item.get("name_fa") or symbol
            ticker = item.get("ticker") or {}
            if item.get("available") and ticker:
                price_text = ticker_display(ticker)
                status_text = {"fresh": "تازه", "delayed": "تأخیری", "estimated": "برآوردی",
                               "stale": "کهنه", "unverified": "تأییدنشده"}.get(item.get("status"), "قیمت موجود")
                if item.get("market") == "reference" or not item.get("paper_tradable") and item.get("category") == "fx":
                    status_text = "نرخ مرجع · غیرقابل معامله"
                elif ticker.get("synthetic_future"):
                    status_text = "شاخص آتی تمرینی · " + status_text
                elif item.get("category") == "fx_cross":
                    status_text = "نرخ تقاطعی · " + status_text
                value_color = GREEN if item.get("status") == "fresh" and item.get("paper_tradable") else GOLD if item.get("status") in ("delayed", "estimated") else MUTED
            else:
                price_text = "قیمت فعلاً ناموجود"
                status_text = "نماد در فهرست هست؛ مظنه دریافت نشد"
                value_color = MUTED
            row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6), padding=[dp(7), dp(2)])
            with row.canvas.before:
                Color(*CARD)
                bg = RoundedRectangle(pos=row.pos, size=row.size, radius=[dp(11)])
            row.bind(pos=lambda w, *_a, r=bg: setattr(r, "pos", w.pos),
                     size=lambda w, *_a, r=bg: setattr(r, "size", w.size))
            name_lbl = Label(text=f"{symbol}  ·  {name}", color=TEXT, bold=True, font_size=dp(10),
                             halign="right", shorten=True, shorten_from="left", size_hint_x=0.36)
            name_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            price_lbl = Label(text=f"{price_text}  ·  {status_text}", color=value_color, font_size=dp(9),
                              halign="right", shorten=True, shorten_from="left", size_hint_x=0.46)
            price_lbl.bind(size=lambda w, *_: setattr(w, "text_size", w.size))
            choose = RoundButton(text="انتخاب", bg=BLUE_GLOW, fg=GOLD, size_hint_x=0.18,
                                 height=dp(39), font_size=dp(9))
            choose.bind(on_release=lambda _btn, sym=symbol: self._select_search_result(sym))
            row.add_widget(name_lbl); row.add_widget(price_lbl); row.add_widget(choose)
            self.suggestions.add_widget(row)

    def _select_search_result(self, symbol):
        values = list(self.coin.values or ())
        if symbol not in values:
            self.coin.values = tuple(values + [symbol])
        self.coin.text = symbol
        self.search_field.text = symbol
        if self._search_event is not None:
            self._search_event.cancel(); self._search_event = None
        self.suggestions.clear_widgets(); self.suggestions.height = 0
        self.suggestion_scroll.height = 0; self.suggestion_scroll.opacity = 0
        self.search_status.text = f"نماد {symbol} انتخاب شد"
        self.select_coin(symbol)

    def on_enter(self, *a):
        if not self.rows.children and not self._busy:
            self.refresh_watchlist()

    def refresh_watchlist(self):
        if self._busy:
            return
        self._busy = True
        self.refresh_btn.disabled = True
        self.status.text = "دریافت مظنه‌ها و وضعیت منابع بازار…"
        personal = []
        try:
            personal = [row["symbol"].replace("-USDT", "") for row in db.watchlist_list()]
        except Exception:
            personal = []
        filter_name = self.market_filter.text if hasattr(self, "market_filter") else "همه بازارها"
        if filter_name == "رمزارزها":
            base_symbols = list(self.CRYPTO_WATCHLIST)
        elif filter_name == "فلزات جهانی":
            base_symbols = ["XAU", "XAG", "XAUF", "XAGF"]
        elif filter_name == "فلزات ایران":
            base_symbols = ["IRG18", "IRS999", "IRS925"]
        elif filter_name == "انرژی":
            base_symbols = ["WTI", "BRENT", "WTIF", "BRTF"]
        elif filter_name == "فارکس و ارزهای آزاد":
            base_symbols = list(self.FX_WATCHLIST)
        else:
            base_symbols = list(self.WATCHLIST)
        symbols = list(dict.fromkeys(base_symbols + (personal if filter_name == "همه بازارها" else [])))[:180]
        self.coin.values = tuple(symbols)

        def work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            found = []
            try:
                with ThreadPoolExecutor(max_workers=min(6, len(symbols) or 1)) as pool:
                    jobs = {pool.submit(mapi.get_ticker, coin): coin for coin in symbols}
                    for future in as_completed(jobs):
                        coin = jobs[future]
                        try:
                            item = future.result()
                            if item and (item.get("display_price") or item.get("price")) and not item.get("error"):
                                found.append((coin, item))
                            else:
                                found.append((coin, {"base": coin, "name_fa": instruments.INSTRUMENTS.get(coin, {}).get("name_fa", coin),
                                                     "price": None, "status": "unavailable", "stale": True,
                                                     "error": (item or {}).get("error") or "قیمت معتبر از منبع دریافت نشد"}))
                        except Exception as exc:
                            found.append((coin, {"base": coin, "name_fa": instruments.INSTRUMENTS.get(coin, {}).get("name_fa", coin),
                                                 "price": None, "status": "unavailable", "stale": True,
                                                 "error": str(exc)[:100]}))
                found.sort(key=lambda x: x[0])
                Clock.schedule_once(lambda *_: self._watchlist_done(found))
            except Exception as exc:
                message = str(exc)[:120]
                Clock.schedule_once(lambda *_: self._watchlist_error(message))

        _run_background(work)

    def _watchlist_done(self, found):
        self._busy = False
        self.refresh_btn.disabled = False
        self.rows.clear_widgets()
        app = App.get_running_app()
        now = time.time()
        unavailable_count = 0
        for coin, item in found:
            has_price = bool(item.get("display_price") or item.get("price"))
            if has_price:
                ledger_price = float(item["price"]) if item.get("price") else None
                ts = float(item.get("ts") or now)
                if not item.get("stale") and ledger_price and item.get("paper_conversion_available", True):
                    app.price_cache[coin] = ledger_price
                    app.price_cache[f"{coin}-USDT"] = ledger_price
                    app.price_cache_ts[coin] = ts
            else:
                unavailable_count += 1
            change = item.get("change_pct")
            change_text = f"{float(change):+.2f}%" if change is not None else "—"
            color = GREEN if change is not None and float(change) >= 0 else RED if change is not None else MUTED

            row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(5), padding=[dp(8), dp(3)])
            with row.canvas.before:
                Color(*CARD)
                row_bg = RoundedRectangle(pos=row.pos, size=row.size, radius=[dp(12)])
                Color(*LINE)
                row_border = Line(rounded_rectangle=(row.x, row.y, row.width, row.height, dp(12)), width=0.65)
            def _sync_market_row(w, *_args, bg=row_bg, border=row_border):
                bg.pos, bg.size = w.pos, w.size
                border.rounded_rectangle = (w.x, w.y, w.width, w.height, dp(12))
            row.bind(pos=_sync_market_row, size=_sync_market_row)
            label = item.get("name_fa") or coin
            row.add_widget(Label(text=f"{coin} · {label}", color=TEXT, size_hint_x=0.30, font_size=dp(9), bold=True))
            row.add_widget(Label(text=ticker_display(item) if has_price else "قیمت ناموجود", color=TEXT if has_price else MUTED, size_hint_x=0.34, font_size=dp(9)))
            status_label = {"fresh": "تازه", "delayed": "تأخیری", "estimated": "برآوردی",
                            "stale": "کهنه", "unverified": "نامطمئن", "unavailable": "ناموجود"}.get(item.get("status"), "—")
            if item.get("synthetic_future"):
                status_label = "آتی تمرینی"
            elif item.get("category") == "fx_cross":
                status_label = "تقاطعی"
            if has_price and item.get("paper_conversion_available") is False:
                status_label = "فقط نمایش"
            row.add_widget(Label(text=status_label, color=GREEN if item.get("status") == "fresh" else GOLD if item.get("status") in ("delayed", "estimated", "unverified") else MUTED,
                                 size_hint_x=0.14, font_size=dp(9)))
            row.add_widget(Label(text=change_text, color=color, size_hint_x=0.12, font_size=dp(10)))
            pick = RoundButton(text="جزئیات", bg=DARK_BTN, size_hint_x=0.16, font_size=dp(9))
            pick.bind(on_release=lambda _btn, c=coin: self.select_coin(c))
            row.add_widget(pick)
            self.rows.add_widget(row)
        if not found:
            self.rows.add_widget(M("هیچ قیمت تازه‌ای دریافت نشد؛ اتصال یا محدودیت منابع را بررسی کن."))
            self.status.text = "دادهٔ تازه در دسترس نیست"
        else:
            current = next((item for coin, item in found if coin == app.current_symbol), None)
            if current and not current.get("stale"):
                app.current_ticker = current
                if current.get("price"):
                    app.current_price = float(current["price"])
                    app.current_price_ts = float(current.get("ts") or now)
                else:
                    app.current_price = None
                    app.current_price_ts = 0.0
                app.refresh_header()
            self.status.text = (f"{len(found)} نماد · {unavailable_count} مورد ناموجود · "
                                 f"زمان بررسی {datetime.now().strftime('%H:%M:%S')}")

    def _watchlist_error(self, message):
        self._busy = False
        self.refresh_btn.disabled = False
        self.status.text = f"بارگذاری ناموفق: {message[:100]}"

    def select_coin(self, coin):
        self.coin.text = coin
        if not instruments.is_reference_symbol(coin):
            App.get_running_app().set_symbol(coin)
        self.load_details()

    def load_details(self):
        if self._busy:
            self.status.text = "درخواست قبلی هنوز در حال انجام است"
            return
        symbol = self.coin.text or "BTC"
        try:
            if not instruments.is_reference_symbol(symbol):
                App.get_running_app().set_symbol(symbol)
        except ValueError as exc:
            self.status.text = str(exc)
            return
        self._busy = True
        self.detail_btn.disabled = True
        self.status.text = f"دریافت جزئیات {symbol} از منابع عمومی…"

        def work():
            try:
                snap = mapi.rich_snapshot(symbol)
                Clock.schedule_once(lambda *_: self._details_done(symbol, snap))
            except Exception as exc:
                message = str(exc)[:120]
                Clock.schedule_once(lambda *_: self._details_error(message))

        _run_background(work)

    def _details_done(self, symbol, snap):
        self._busy = False
        self.detail_btn.disabled = False
        price = pxfmt(snap.get("price"))
        change = snap.get("change_pct")
        funding = snap.get("funding")
        oi = snap.get("open_interest")
        spread = snap.get("spread_pct")
        bid, ask = snap.get("bid"), snap.get("ask")
        if snap.get("category") in ("metals", "iran_metals", "energy", "fx"):
            status_fa = {"fresh": "تازه", "delayed": "با تأخیر", "estimated": "برآورد محاسباتی", "stale": "کهنه", "unverified": "زمان تأییدنشده"}.get(snap.get("status"), "نامشخص")
            lines = [
                f"{snap.get('name_fa') or symbol} · نماد {symbol}",
                f"قیمت نمایشی: {ticker_display(snap)}",
                (f"معادل دفترکل مجازی: {pxfmt(snap.get('price'))} USDT به‌ازای هر {snap.get('unit') or 'واحد'}"
                 if snap.get("price") else "معادل دفترکل مجازی: تا دریافت نرخ تازهٔ تتر/تومان قابل محاسبه نیست"),
                f"نوع بازار: {'فیوچرز' if snap.get('market') == 'futures' else 'اسپات'} · وضعیت داده: {status_fa}",
                f"منبع: {snap.get('source') or 'ناموجود'} · سن داده: {snap.get('age_seconds')} ثانیه" if snap.get("age_seconds") is not None else f"منبع: {snap.get('source') or 'ناموجود'} · زمان مشاهده نامشخص",
                f"روش قیمت‌گذاری: {snap.get('pricing_method')}",
            ]
            if snap.get("formula"):
                lines += [f"فرمول: {snap['formula']}",
                          f"منبع نرخ ارز: {snap.get('fx_source') or 'نامشخص'}",
                          f"تعدیل پایه: {float(snap.get('adjustment_bps') or 0):.1f} bps؛ این یک فرض قابل تنظیم KALVO است، نه کارمزد رسمی.",
                          "قیمت محاسباتی، ارزش فلز خام است و اجرت، مالیات، حباب بازار و هزینهٔ فروشگاهی را شامل نمی‌شود."]
                if snap.get("fx_parity_proxy"):
                    lines.append("نرخ تبدیل دلار از تتر به‌عنوان proxy برابری تقریبی USD≈USDT گرفته شده؛ اختلاف احتمالی این دو در برآورد اثر دارد.")
            if snap.get("daily_data"):
                lines.append("این ابزار دادهٔ نقدی روزانه دارد؛ قیمت لحظه‌ای تیک‌به‌تیک نیست.")
            lines.append("ارزش جهانی با USD نمایش داده می‌شود و معادل آن در دفترکل مجازی با فرض برابری تقریبی USD/USDT ثبت می‌شود.")
        else:
            lines = [
                f"{symbol} / USDT · قیمت {price} · منبع {snap.get('source') or 'ناموجود'}",
                f"تغییر ۲۴ ساعته: {float(change):+.2f}%" if change is not None else "تغییر ۲۴ ساعته: ناموجود",
                f"بیشترین / کمترین: {pxfmt(snap.get('high'))} / {pxfmt(snap.get('low'))}",
                f"حجم برحسب USDT: {money(snap.get('volume'))}",
                f"بهترین خرید / فروش: {pxfmt(bid)} / {pxfmt(ask)}",
                f"اسپرد دفتر سفارش: {float(spread):.4f}%" if spread is not None else "اسپرد دفتر سفارش: ناموجود",
                f"عدم‌تعادل عمق سفارش: {float(snap.get('depth_imbalance_pct')):+.2f}%" if snap.get("depth_imbalance_pct") is not None else "عدم‌تعادل عمق سفارش: ناموجود",
                f"فاندینگ قرارداد دائمی: {float(funding) * 100:.5f}%" if funding is not None else "فاندینگ: ناموجود",
                f"مقدار Open Interest دریافتی: {money(oi)}" if oi is not None else "Open Interest: ناموجود",
                f"منبع دفتر سفارش: {snap.get('orderbook_source') or 'ناموجود'}",
                "این داده‌ها ممکن است ناقص یا با تأخیر باشند؛ شاخص مشتقه برای همهٔ دارایی‌ها در دسترس نیست.",
            ]
        self.detail.text = "\n".join(lines)
        # Keep the shared header and wallet mark cache aligned with the selected market.
        if snap.get("price") and snap.get("category") != "fx":
            app = App.get_running_app()
            stamp = float(snap.get("ts") or time.time())
            app.current_symbol = symbol
            app.current_price = float(snap["price"])
            app.current_price_ts = stamp
            app.current_ticker = snap
            app.price_cache[symbol] = float(snap["price"])
            app.price_cache_ts[symbol] = stamp
            app.refresh_header()
        self.card_price.set_value(ticker_display(snap), GOLD)
        self.card_change.set_value(f"{float(change):+.2f}%" if change is not None else "—",
                                   GREEN if change is not None and float(change) >= 0 else RED if change is not None else MUTED)
        self.card_spread.set_value(f"{float(spread):.4f}%" if spread is not None else "—")
        self.card_funding.set_value(f"{float(funding) * 100:.5f}%" if funding is not None else "—")
        self.status.text = "جزئیات به‌روزرسانی شد؛ مقادیر ناموجود به‌عنوان صفر فرض نشده‌اند"

    def _details_error(self, message):
        self._busy = False
        self.detail_btn.disabled = False
        self.status.text = f"دریافت جزئیات ناموفق: {message[:100]}"


class SpotScreen(Screen):
    """Manual spot paper trading with a separate virtual spot wallet."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._busy = False
        self.price = None
        self.price_ts = 0.0
        self.symbol_base = "BTC"
        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(orientation="vertical", padding=[dp(14), dp(12)],
                            spacing=dp(9), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))
        content.add_widget(H("بازار اسپات", 23, GOLD))
        content.add_widget(M("بازار نقدی چنددارایی · قیمت بومی و ارزش معادل دفترکل"))

        balance_card = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(122),
                                 padding=dp(16), spacing=dp(7))
        with balance_card.canvas.before:
            Color(*CARD); balance_card._bg = RoundedRectangle(radius=[dp(18)])
            Color(*GOLD); balance_card._edge = RoundedRectangle(pos=(0,0), size=(dp(4),dp(70)), radius=[dp(2)])
        balance_card.bind(pos=lambda w,*_: (setattr(w._bg,'pos',w.pos), setattr(w._edge,'pos',(w.x,w.y+dp(16)))),
                          size=lambda w,*_: (setattr(w._bg,'size',w.size), setattr(w._edge,'size',(dp(4),w.height-dp(32)))))
        self.total = H("ارزش دارایی‌های قیمت‌گذاری‌شده: —", 20, GOLD)
        self.usdt = M("USDT آزاد: —")
        self.missing = M("دارایی‌های بدون قیمت تازه: —")
        balance_card.add_widget(self.total); balance_card.add_widget(self.usdt); balance_card.add_widget(self.missing)
        content.add_widget(balance_card)

        market = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(9))
        self.sym = Field(text="BTC", hint_text="BTC · XAU · IRG18 · WTI · USDIRT · EURIRT")
        self.price_lbl = H("قیمت تازه: —", 17, TEXT)
        fetch = RoundButton(text="دریافت قیمت", bg=GOLD, fg=(0,0,0,1), size_hint_x=0.40)
        fetch.bind(on_release=lambda *_: self.fetch_price())
        market.add_widget(self.sym); market.add_widget(fetch)
        content.add_widget(market)
        content.add_widget(M("رمزارزها · طلا و نقره · نفت · دلار، یورو و ارزهای منتخب بازار ایران"))
        self.asset_picker = Spinner(
            text="انتخاب دارایی",
            values=tuple(["XAU", "XAG", "IRG18", "IRS999", "IRS925", "WTI", "BRENT"] +
                         sorted(instruments.FX_SPOT_SYMBOLS) +
                         sorted(instruments.FX_CROSS_SPOT_SYMBOLS)),
            size_hint_y=None, height=dp(42),
        )
        self.asset_picker.background_color = DARK_BTN
        self.asset_picker.bind(text=lambda _sp, value: setattr(self.sym, "text", value)
                               if value in instruments.INSTRUMENTS else None)
        content.add_widget(self.asset_picker)
        content.add_widget(self.price_lbl)

        order = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(9))
        self.amount = Field(text="50", input_filter="float", hint_text="خرید: مبلغ USDT / فروش: مقدار دارایی")
        self.side = Spinner(text="خرید", values=("خرید", "فروش"), size_hint_x=0.30)
        self.side.background_color = DARK_BTN
        order.add_widget(self.amount); order.add_widget(self.side)
        content.add_widget(order)
        execute = RoundButton(text="ثبت سفارش اسپات", bg=GREEN, fg=(0.02, 0.08, 0.06, 1), size_hint_y=None, height=dp(58))
        execute.bind(on_release=lambda *_: self.execute_order())
        content.add_widget(execute)
        self.status = M("قیمت بازار باید تازه باشد؛ بدون قیمت معتبر سفارش ثبت نمی‌شود")
        content.add_widget(self.status)

        content.add_widget(H("مدیریت کیف پول اسپات", 16, GOLD))
        funds = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(9))
        self.fund_amount = Field(text="100", input_filter="float", hint_text="مبلغ USDT")
        deposit = RoundButton(text="افزایش اعتبار", bg=BLUE_GLOW, size_hint_x=0.50)
        withdraw = RoundButton(text="کاهش موجودی", bg=DARK_BTN, size_hint_x=0.38)
        deposit.bind(on_release=lambda *_: self.change_funds(True))
        withdraw.bind(on_release=lambda *_: self.change_funds(False))
        funds.add_widget(self.fund_amount); funds.add_widget(deposit); funds.add_widget(withdraw)
        content.add_widget(funds)
        content.add_widget(M("شارژ و کاهش موجودی فقط دفترکل آزمایشی را تغییر می‌دهد و به بانک یا بلاک‌چین وصل نیست."))

        self.long_term = RoundButton(text="تحلیل بلندمدت ۴ساعته / روزانه", bg=DARK_BTN, fg=GOLD,
                                     size_hint_y=None, height=dp(48))
        self.long_term.bind(on_release=lambda *_: self.analyze_long_term())
        content.add_widget(self.long_term)
        self.long_note = M("این تحلیل فقط گزارش می‌دهد و معاملهٔ خودکار ثبت نمی‌کند.")
        content.add_widget(self.long_note)
        self._spot_auto_enabled = False
        self._spot_auto_busy = False
        self._spot_auto_event = None
        self.auto_btn = RoundButton(text="فعال‌سازی مدیریت خودکار اسپات", bg=BLUE_GLOW,
                                    size_hint_y=None, height=dp(50))
        self.auto_btn.bind(on_release=lambda *_: self.toggle_spot_auto())
        content.add_widget(self.auto_btn)
        content.add_widget(M("اختیاری و با فعال‌سازی دستی: فقط کیف پول مجازی اسپات؛ سقف تخصیص هر خرید ۱۰٪ موجودی USDT، سقف سفارش ۱۵۰ USDT و فاصلهٔ حداقل ۴ ساعت. هیچ سفارش واقعی ارسال نمی‌شود."))
        content.add_widget(H("دارایی‌های کیف پول", 16))
        self.assets = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.assets.bind(minimum_height=self.assets.setter("height"))
        content.add_widget(self.assets)
        content.add_widget(H("تاریخچهٔ سفارش‌های اسپات", 16))
        self.history = GridLayout(cols=1, spacing=dp(4), size_hint_y=None)
        self.history.bind(minimum_height=self.history.setter("height"))
        content.add_widget(self.history)
        scroll.add_widget(content)
        self.add_widget(scroll)
        interval = max(5, min(60, int(db.get_setting("refresh_interval", "8"))))
        self._refresh_event = Clock.schedule_interval(self._periodic_prices, interval)

    def on_enter(self, *args):
        self.refresh()
        if not self.price or time.time() - self.price_ts > 20:
            self.fetch_price()

    def fetch_price(self):
        if self._busy:
            return
        self._busy = True
        symbol = self.sym.text.strip() or "BTC"
        self.status.text = "در حال دریافت قیمت عمومی تازه…"
        def worker():
            try:
                canonical = canonical_market_symbol(symbol)
                if instruments.is_reference_symbol(canonical):
                    raise ValueError("USDT/IRT فقط نرخ مرجع تبدیل است و قابل معامله نیست")
                if instruments.is_instrument(canonical) and instruments.get_instrument(canonical).get("market") != "spot":
                    raise ValueError("این نماد فیوچرز است؛ برای اسپات از XAU / XAG / IRG18 / WTI / BRENT استفاده کن")
                ticker = mapi.get_ticker(canonical)
                px = float(ticker.get("price") or 0)
                if px <= 0 or ticker.get("error") or ticker.get("stale") or not ticker.get("trade_demo", True):
                    raise ValueError(ticker.get("error") or "قیمت معتبر برای شبیه‌سازی موجود نیست")
                base = ticker.get("base") or canonical
                ts = float(ticker.get("ts") or time.time())
                def done(*_):
                    self.symbol_base = base
                    self.sym.text = base
                    self.price, self.price_ts = px, ts
                    app = App.get_running_app()
                    app.price_cache[base] = px
                    app.price_cache[f"{base}-USDT"] = px
                    app.price_cache_ts[base] = ts
                    app.set_symbol(base)
                    app.current_price = float(px)
                    app.current_price_ts = ts
                    app.current_ticker = ticker
                    self.price_lbl.text = f"{ticker_display(ticker)} · معادل {pxfmt(px)} USDT/{ticker.get('unit') or 'واحد'}"
                    status_fa = {"fresh": "تازه", "delayed": "با تأخیر", "estimated": "برآوردی", "unverified": "زمان تأییدنشده"}.get(ticker.get("status"), "نامشخص")
                    self.status.text = f"منبع: {ticker.get('source') or '—'} · {status_fa} · فقط شبیه‌سازی"
                    self._busy = False
                    self.refresh()
                Clock.schedule_once(done)
            except Exception as exc:
                message = str(exc)[:120]
                def failed(*_):
                    self.status.text = f"قیمت تازه دریافت نشد: {message}"
                    self.price_lbl.text = "قیمت تازه: در دسترس نیست"
                    self._busy = False
                Clock.schedule_once(failed)
        _run_background(worker)

    def execute_order(self):
        try:
            current_symbol = canonical_market_symbol(self.sym.text.strip() or "BTC")
            if instruments.is_reference_symbol(current_symbol):
                raise ValueError("USDT/IRT نرخ مرجع تبدیل است و قابل معامله نیست")
            if instruments.is_instrument(current_symbol) and instruments.get_instrument(current_symbol).get("market") != "spot":
                raise ValueError("این ابزار در دستهٔ فیوچرز است و در اسپات دمو قابل انتخاب نیست")
            if not self.price or time.time() - self.price_ts > 20:
                self.status.text = "قیمت از مهلت تازگی گذشته؛ ابتدا دوباره قیمت بگیر"
                self.fetch_price()
                return
            amount = float(self.amount.text or 0)
            side = "BUY" if self.side.text == "خرید" else "SELL"
            app = App.get_running_app()
            ticker = dict(getattr(app, "current_ticker", {}) or {})
            action = "BUY" if side == "BUY" else "SELL"
            from apex.execution_simulator import market_fill
            slip = float(db.get_setting("paper_slippage_bps", "2.0") or 2.0)
            execution = market_fill(ticker, action, slippage_bps=slip, require_fresh=True)
            result = db.spot_market_order(self.symbol_base, side, amount,
                                          float(execution["fill_price"]),
                                          note=(f"KALVO 7.2 paper · {action} · spread={execution['spread_pct']:.5f}% "
                                                f"· slip={execution['slippage_bps']:.2f}bps"))
            self.status.text = (f"سفارش مجازی #{result['id']} ثبت شد · {result['quantity']:.7g} {self.symbol_base} · "
                                f"کارمزد {result['fee']:.4f} USDT · قیمت اجرای محاسباتی")
            self.refresh()
            app.refresh_wallet()
        except Exception as exc:
            self.status.text = f"سفارش انجام نشد: {str(exc)[:130]}"

    def change_funds(self, deposit):
        try:
            amount = float(self.fund_amount.text or 0)
            db.spot_deposit(amount) if deposit else db.spot_withdraw(amount)
            self.status.text = "کیف پول اسپات به‌روزرسانی شد"
            self.refresh()
        except Exception as exc:
            self.status.text = f"عملیات کیف پول انجام نشد: {str(exc)[:120]}"

    def refresh(self):
        app = App.get_running_app()
        marks = {k: v for k, v in getattr(app, "price_cache", {}).items()
                 if not k.endswith("-USDT") and getattr(app, "price_cache_ts", {}).get(k, 0) >= time.time()-30}
        snapshot = db.spot_summary(marks)
        self.total.text = f"ارزش قیمت‌گذاری‌شده: ${money(snapshot['total_known_value'])}"
        usdt = next((r for r in snapshot['balances'] if r['asset']=='USDT'), None)
        self.usdt.text = f"USDT آزاد: {money(usdt['free'] if usdt else 0)}"
        self.missing.text = ("قیمت تازه نداریم: " + "، ".join(snapshot['missing_prices'])) if snapshot['missing_prices'] else "همهٔ دارایی‌ها قیمت تازه دارند"
        self.assets.clear_widgets()
        for row in snapshot['balances']:
            value = money(row['value']) if row['value'] is not None else "منتظر قیمت تازه"
            card = BoxLayout(size_hint_y=None, height=dp(50), padding=dp(8), spacing=dp(8))
            with card.canvas.before:
                Color(*CARD); rect = RoundedRectangle(radius=[dp(12)])
            card.bind(pos=lambda w,*_,r=rect: setattr(r,'pos',w.pos), size=lambda w,*_,r=rect: setattr(r,'size',w.size))
            card.add_widget(Label(text=row['asset'], color=TEXT, bold=True, font_size=dp(13), size_hint_x=.20))
            card.add_widget(Label(text=f"{float(row['free']):.8g}", color=TEXT, font_size=dp(12)))
            card.add_widget(Label(text=f"${value}" if value != "منتظر قیمت تازه" else value,
                                  color=GOLD if row['value'] is not None else MUTED, font_size=dp(11)))
            self.assets.add_widget(card)
        self.history.clear_widgets()
        trades = db.spot_trades(20)
        if not trades:
            self.history.add_widget(M("سفارشی ثبت نشده است"))
        for trade in trades:
            col = GREEN if trade['side']=='BUY' else RED
            self.history.add_widget(Label(
                text=f"{trade['symbol']} · {'خرید' if trade['side']=='BUY' else 'فروش'} · {trade['quantity']:.7g} @ {pxfmt(trade['price'])} · کارمزد {trade['fee']:.4f}",
                color=col, size_hint_y=None, height=dp(30), font_size=dp(11)))

    def _periodic_prices(self, _dt):
        if not self.manager or self.manager.current != "spot" or self._busy:
            return
        def worker():
            try:
                balances = db.get_spot_balances()
                symbols = [row["asset"] for row in balances if row["asset"] != "USDT"]
                symbols.append(self.sym.text.strip() or "BTC")
                prices = mapi.get_prices_batch(list(dict.fromkeys(symbols)))
                now = time.time()
                base_prices = {key: value for key, value in prices.items()
                               if "-" not in key and not key.endswith("USDT")}
                def done(*_):
                    app = App.get_running_app()
                    for base, value in base_prices.items():
                        app.price_cache[base] = value
                        app.price_cache[f"{base}-USDT"] = value
                        app.price_cache_ts[base] = now
                    try:
                        current = canonical_market_symbol(self.sym.text.strip() or "BTC")
                    except ValueError:
                        current = ""
                    if current in base_prices:
                        self.price = base_prices[current]
                        self.price_ts = now
                        self.price_lbl.text = f"{current}/USDT  ·  {pxfmt(self.price)}  ·  به‌روزرسانی شد"
                    self.refresh()
                Clock.schedule_once(done)
            except Exception:
                return
        _run_background(worker)

    def toggle_spot_auto(self):
        current = canonical_market_symbol(self.sym.text.strip() or "BTC")
        if instruments.is_instrument(current) or instruments.is_reference_symbol(current):
            self.status.text = "معامله‌گر خودکار فقط برای واچ‌لیست رمزارزها فعال است؛ فلزات و انرژی دستی و دمو هستند."
            return
        self._spot_auto_enabled = not self._spot_auto_enabled
        if self._spot_auto_enabled:
            self.auto_btn.text = "توقف مدیریت خودکار اسپات"
            self.auto_btn.background_color = RED
            self.status.text = "مدیریت خودکار اسپات فعال شد؛ اولین بررسی آغاز می‌شود"
            Clock.schedule_once(lambda *_: self._spot_auto_cycle(), 0.2)
        else:
            if self._spot_auto_event is not None:
                self._spot_auto_event.cancel()
                self._spot_auto_event = None
            self.auto_btn.text = "فعال‌سازی مدیریت خودکار اسپات"
            self.auto_btn.background_color = BLUE_GLOW
            self.status.text = "مدیریت خودکار اسپات متوقف شد"

    def _spot_auto_cycle(self):
        if not self._spot_auto_enabled or self._spot_auto_busy:
            return
        self._spot_auto_busy = True
        symbol = self.sym.text.strip() or "BTC"
        def worker():
            try:
                from apex import spot_auto
                result = spot_auto.scan_once(symbol)
                action = result.get("action", "HOLD")
                trade = result.get("trade") or {}
                if action in ("BUY", "SELL"):
                    msg = (f"دستیار اسپات دمو: {'خرید' if action == 'BUY' else 'فروش'} مجازی {symbol} · "
                           f"مقدار {float(trade.get('quantity') or 0):.7g} · {result.get('reason','')}")
                else:
                    msg = f"دستیار بلندمدت: عدم معامله · {result.get('reason','') }"
            except Exception as exc:
                msg = f"اسکن دستیار بلندمدت ناموفق بود: {str(exc)[:100]}"
            def done(*_):
                self._spot_auto_busy = False
                self.status.text = msg
                self.refresh()
                if self._spot_auto_enabled:
                    self._spot_auto_event = Clock.schedule_once(lambda _dt: self._spot_auto_cycle(), 900)
            Clock.schedule_once(done)
        _run_background(worker)

    def analyze_long_term(self):
        symbol = canonical_market_symbol(self.sym.text.strip() or "BTC")
        if instruments.is_instrument(symbol):
            self.long_note.text = "گزارش تاریخچهٔ اختصاصی این ابزار هنوز به‌طور کامل اعتبارسنجی نشده است؛ برای مشاهدهٔ قیمت و دادهٔ منبع از رادار بازار استفاده کن."
            return
        self.long_note.text = "در حال دریافت کندل‌های بسته‌شدهٔ ۴ساعته و روزانه…"
        def worker():
            try:
                reports = []
                for tf in ("4hour", "1day"):
                    candles = mapi.fetch_candles(symbol, tf)
                    completed = analysis.completed_candles(candles, tf)
                    result = analysis.analyze_candles(completed)
                    if result.get("ready"):
                        reports.append(f"{tf}: {result.get('signal')} · امتیاز {result.get('confluence_score')} · رژیم {result.get('market_regime') or 'نامشخص'}")
                    else:
                        reports.append(f"{tf}: دادهٔ کافی نیست")
                message = "تحلیل بلندمدت: " + " | ".join(reports) + " · صرفاً مطالعه، بدون سفارش خودکار"
                Clock.schedule_once(lambda *_: setattr(self.long_note, "text", message))
            except Exception as exc:
                msg = str(exc)[:110]
                Clock.schedule_once(lambda *_: setattr(self.long_note, "text", f"تحلیل در دسترس نیست: {msg}"))
        _run_background(worker)


class WalletScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        scroll = ScrollView(do_scroll_x=False)
        root = BoxLayout(orientation="vertical", padding=[dp(14), dp(12)], spacing=dp(9), size_hint_y=None)
        root.bind(minimum_height=root.setter("height"))
        root.add_widget(H("پرتفوی مجازی KALVO", 23, GOLD))
        root.add_widget(M("دفترکل آزمایشی · موجودی، مارجین، سود باز و تاریخچهٔ معاملات"))
        hero = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(116), padding=dp(15), spacing=dp(4))
        with hero.canvas.before:
            Color(*CARD); hero._bg = RoundedRectangle(radius=[dp(18)])
            Color(*GOLD); hero._stripe = RoundedRectangle(radius=[dp(2)])
        hero.bind(pos=lambda w,*_: (setattr(w._bg,'pos',w.pos), setattr(w._stripe,'pos',(w.x,w.y+dp(12)))),
                  size=lambda w,*_: (setattr(w._bg,'size',w.size), setattr(w._stripe,'size',(dp(4),w.height-dp(24)))))
        self.bal = H("$100.00", 27, GOLD)
        self.eq = M("اکوئیتی —")
        self.available = M("موجودی قابل استفاده —")
        hero.add_widget(self.bal); hero.add_widget(self.eq); hero.add_widget(self.available)
        root.add_widget(hero)
        pnlrow = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(92))
        self.upnl = MetricCard("سود و زیان باز", "—", GREEN)
        self.rpnl = MetricCard("سود و زیان بسته / کارمزد", "—", TEXT)
        pnlrow.add_widget(self.upnl); pnlrow.add_widget(self.rpnl)
        root.add_widget(pnlrow)
        self.risk_cockpit = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(104),
                                      padding=[dp(12), dp(9)], spacing=dp(3))
        with self.risk_cockpit.canvas.before:
            Color(*CARD)
            self.risk_cockpit._bg = RoundedRectangle(radius=[dp(16)])
            Color(*LINE)
            self.risk_cockpit._border = Line(rounded_rectangle=(0,0,0,0,dp(16)), width=1)
            Color(*GOLD)
            self.risk_cockpit._stripe = RoundedRectangle(radius=[dp(2)])
        self.risk_cockpit.bind(pos=lambda w,*_: (setattr(w._bg,"pos",w.pos),
            setattr(w._border,"rounded_rectangle",(w.x,w.y,w.width,w.height,dp(16))),
            setattr(w._stripe,"pos",(w.x,w.y+dp(12))),setattr(w._stripe,"size",(dp(3),w.height-dp(24)))),
            size=lambda w,*_: (setattr(w._bg,"size",w.size),
            setattr(w._border,"rounded_rectangle",(w.x,w.y,w.width,w.height,dp(16))),
            setattr(w._stripe,"size",(dp(3),w.height-dp(24)))))
        self.risk_title = H("داشبورد ریسک", 14, GOLD)
        self.risk_text = M("در انتظار محاسبهٔ داده‌های ژورنال…")
        self.risk_text.height = dp(62)
        self.risk_cockpit.add_widget(self.risk_title)
        self.risk_cockpit.add_widget(self.risk_text)
        root.add_widget(self.risk_cockpit)
        root.add_widget(H("مدیریت موجودی مجازی", 16, GOLD))
        row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        self.amt = Field(text="100", input_filter="float", hint_text="مبلغ USDT")
        bd = RoundButton(text="افزایش موجودی", bg=GREEN, fg=(0, 0, 0, 1))
        bw = RoundButton(text="کاهش اعتبار", bg=RED, fg=TEXT)
        bd.bind(on_release=lambda *_: self.cash(True)); bw.bind(on_release=lambda *_: self.cash(False))
        row.add_widget(self.amt); row.add_widget(bd); row.add_widget(bw)
        root.add_widget(row)
        quick = GridLayout(cols=4, spacing=dp(6), size_hint_y=None, height=dp(42))
        for amount in (100, 500, 1000, 5000):
            button = RoundButton(text=f"+{amount}", bg=DARK_BTN, fg=GOLD, font_size=dp(12), height=dp(40))
            button.bind(on_release=lambda _b, a=amount: setattr(self.amt, "text", str(a)))
            quick.add_widget(button)
        root.add_widget(quick)
        root.add_widget(M("افزایش موجودی فقط اعتبار مجازی حساب دمو را تغییر می‌دهد؛ واریز یا برداشت واقعی انجام نمی‌شود."))
        root.add_widget(H("گزارش عملکرد معاملات بسته‌شده", 16, GOLD))
        report = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        self.period = Spinner(text="یک ماه", values=("یک روز", "سه روز", "یک هفته", "یک ماه", "یک سال", "همه"))
        self.period.background_color = DARK_BTN
        export = RoundButton(text="دانلود CSV", bg=BLUE_GLOW)
        export.bind(on_release=lambda *_: self.export())
        report.add_widget(self.period); report.add_widget(export)
        root.add_widget(report)
        self.msg = M("")
        root.add_widget(self.msg)
        root.add_widget(H("تاریخچهٔ معاملات", 16))
        self.box = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.box.bind(minimum_height=self.box.setter("height"))
        root.add_widget(self.box)
        scroll.add_widget(root)
        self.add_widget(scroll)

    def on_enter(self, *a):
        self.refresh()

    def refresh(self):
        app = App.get_running_app()
        now = time.time()
        fresh_prices = {k: v for k, v in app.price_cache.items()
                        if not k.endswith("-USDT") and app.price_cache_ts.get(k, 0) >= now - 30}
        e = db.equity_with_marks(fresh_prices)
        self.bal.text = f"${money(e['balance'])}"
        missing_note = f" · قیمت تازه برای {e['missing_prices']} پوزیشن نداریم" if e["missing_prices"] else ""
        self.eq.text = f"اکوئیتی ${money(e['equity'])} · مارجین قفل‌شده ${money(e['locked_margin'])}{missing_note}"
        self.available.text = (f"قابل استفاده ${money(e['available_balance'])} · رزرو سفارش‌ها ${money(e['pending_reserved'])}")
        self.upnl.value_label.text = f"${money(e['unrealized'])}"
        self.upnl.value_label.color = GREEN if e['unrealized'] >= 0 else RED
        self.rpnl.value_label.text = f"${money(e['realized_pnl'])} · fee ${money(e['fees_paid'])}"
        try:
            risk = portfolio_analytics.build_risk_report(
                db.report_trades("1y"), db.list_open_positions(), fresh_prices,
                initial_equity=float(db.get_wallet().get("total_deposited") or 100.0))
            pf = "—" if risk["profit_factor"] is None else f"{risk['profit_factor']:.2f}"
            self.risk_text.text = (f"نرخ مثبت {risk['win_rate_pct']:.1f}% · PF {pf} · افت بیشینه {risk['max_drawdown']:.2f} USDT\n"
                                   f"PnL امروز {risk['today_realized']:+.2f} · مواجهه {risk['exposure']:.2f} · "
                                   f"پوزیشن باز {risk['open_positions']} · زنجیره زیان {risk['loss_streak']}")
        except Exception as exc:
            self.risk_text.text = f"داشبورد ریسک: {str(exc)[:100]}"
        self.box.clear_widgets()
        for trade in db.list_trades(80):
            col = GREEN if (trade.get("pnl") or 0) >= 0 else RED
            row = BoxLayout(size_hint_y=None, height=dp(38), padding=[dp(7), dp(3)], spacing=dp(7))
            with row.canvas.before:
                Color(*CARD); rect = RoundedRectangle(radius=[dp(10)])
            row.bind(pos=lambda w,*_,r=rect: setattr(r,'pos',w.pos), size=lambda w,*_,r=rect: setattr(r,'size',w.size))
            row.add_widget(Label(text=f"{trade.get('symbol')} · {trade.get('side')}", color=TEXT, font_size=dp(11)))
            row.add_widget(Label(text=f"{money(trade.get('pnl'))} USDT", color=col, bold=True, font_size=dp(11)))
            row.add_widget(Label(text=str(trade.get('reason') or '—'), color=MUTED, font_size=dp(10), size_hint_x=.65))
            self.box.add_widget(row)

    def cash(self, dep):
        try:
            amount = float(self.amt.text or 0)
            db.deposit(amount, "افزایش اعتبار") if dep else db.withdraw(amount, "برداشت از موجودی دمو")
            self.refresh()
            self.msg.text = "دفترکل دمو به‌روزرسانی شد"
        except Exception as exc:
            self.msg.text = str(exc)[:140]

    def export(self):
        try:
            mapping = {"یک روز":"1d", "سه روز":"3d", "یک هفته":"1w", "یک ماه":"1m", "یک سال":"1y", "همه":"all"}
            period = mapping.get(self.period.text, "1m")
            app = App.get_running_app()
            csv_data = db.export_period_csv(period)
            stamp = int(time.time())
            directory = os.path.join(app.user_data_dir, "reports")
            os.makedirs(directory, exist_ok=True)
            csv_path = os.path.join(directory, f"KALVO_trades_{period}_{stamp}.csv")
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write(csv_data)
            now = time.time()
            fresh_prices = {k: v for k, v in app.price_cache.items()
                            if not k.endswith("-USDT") and app.price_cache_ts.get(k, 0) >= now - 30}
            account_report = {
                "app": "KALVO", "report_type": "virtual_account_snapshot",
                "generated_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                "trade_period": period,
                "futures_wallet": db.get_wallet(),
                "futures_equity": db.equity_with_marks(fresh_prices),
                "open_futures_positions": db.list_open_positions(),
                "pending_futures_orders": db.list_pending_orders(),
                "spot_wallet": db.spot_summary(fresh_prices),
                "closed_trades_in_period": db.report_trades(period),
                "price_freshness": "Only cached prices younger than 30 seconds are used; missing marks remain unpriced.",
                "mode": "VIRTUAL_PAPER_TRADING_ONLY",
            }
            json_path = os.path.join(directory, f"KALVO_account_{period}_{stamp}.json")
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(account_report, handle, ensure_ascii=False, indent=2, default=str)
            count = len(account_report["closed_trades_in_period"])
            self.msg.text = f"دو گزارش ساخته شد · CSV معاملات: {count} ردیف · JSON کامل حساب\n{csv_path}\n{json_path}"
        except Exception as exc:
            self.msg.text = f"ساخت گزارش ناموفق: {str(exc)[:120]}"


class AIScreen(Screen):
    """Local multi-timeframe analyst and Persian market-learning workspace."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                            spacing=dp(8), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))
        content.add_widget(H("مرکز هوشمندی KALVO", 22, GOLD))
        content.add_widget(M("تحلیل چندبازه‌ای · رمزارزها، فلزات گران‌بها و انرژی · دادهٔ قابل ردیابی"))

        status_card = BoxLayout(orientation="vertical", size_hint_y=None,
                                height=dp(70), padding=dp(10), spacing=dp(3))
        with status_card.canvas.before:
            Color(*CARD)
            status_card._bg = RoundedRectangle(radius=[dp(12)])
        status_card.bind(pos=lambda *_: setattr(status_card._bg, "pos", status_card.pos),
                         size=lambda *_: setattr(status_card._bg, "size", status_card.size))
        status_card.add_widget(H("KALVO INTELLIGENCE  ·  LOCAL + CLOUD", 14, GREEN))
        status_card.add_widget(M("پایگاه دانش محلی + هوش ابری Coarena (اختیاری با کلید)"))
        content.add_widget(status_card)

        key_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        _saved_key = db.get_setting("coarena_api_key", "") or get_env("COARENA_API_KEY")
        self.api_key = Field(text=_saved_key, hint_text="کلید Coarena")
        self.api_key.password = True
        save_key = RoundButton(text="ذخیره کلید", bg=DARK_BTN, fg=TEXT, size_hint_x=0.35)
        def _save_key(*_):
            db.set_setting("coarena_api_key", (self.api_key.text or "").strip())
            self.status.text = "کلید ذخیره شد"
        save_key.bind(on_release=_save_key)
        key_row.add_widget(self.api_key)
        key_row.add_widget(save_key)
        content.add_widget(key_row)

        data_key_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.fred_key = Field(text=db.get_setting("fred_api_key", "") or get_env("FRED_API_KEY"),
                              hint_text="FRED_API_KEY · برای نفت نقدی روزانه")
        self.fred_key.password = True
        save_fred = RoundButton(text="ذخیره کلید نفت", bg=DARK_BTN, fg=GOLD, size_hint_x=0.36)
        def _save_fred(*_):
            db.set_setting("fred_api_key", (self.fred_key.text or "").strip())
            self.status.text = "کلید FRED در تنظیمات محلی ذخیره شد؛ برای استفاده از .env مقدار آن اولویت دارد"
        save_fred.bind(on_release=_save_fred)
        data_key_row.add_widget(self.fred_key)
        data_key_row.add_widget(save_fred)
        content.add_widget(data_key_row)
        content.add_widget(M("TGJU ارزهای ایران را به ریال برمی‌گرداند و KALVO با تقسیم بر ۱۰ به تومان تبدیل می‌کند. Gold-API و Wallex عمومی‌اند؛ FRED برای برخی داده‌های روزانهٔ نفت به کلید نیاز دارد."))

        mode_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.ai_mode = Spinner(
            text=db.get_setting("ai_mode", "both"),
            values=("local", "cloud", "both"),
            size_hint_x=0.40,
        )
        self.ai_mode.background_color = DARK_BTN
        mode_lbl = M("حالت AI")
        mode_lbl.size_hint_x = 0.25
        mode_row.add_widget(mode_lbl)
        mode_row.add_widget(self.ai_mode)
        content.add_widget(mode_row)

        content.add_widget(H("انتخاب مدل (لمس کن)", 14, GOLD))
        from apex.defaults import AI_MODELS, DEFAULT_AI_MODEL
        default_model = db.get_setting("coarena_model", DEFAULT_AI_MODEL) or DEFAULT_AI_MODEL
        self.model_sp = Spinner(
            text=default_model,
            values=tuple(m[0] for m in AI_MODELS),
            size_hint_y=None,
            height=dp(0),
            opacity=0,
        )
        # visible model chips
        self._model_btns = {}
        model_grid = GridLayout(cols=2, size_hint_y=None, height=dp(420), spacing=dp(6))
        def _pick_model(mid, btn):
            def _cb(*_):
                self.model_sp.text = mid
                db.set_setting("coarena_model", mid)
                for k, b in self._model_btns.items():
                    if k == mid:
                        b.background_color = GOLD
                        b.color = (0, 0, 0, 1)
                    else:
                        b.background_color = DARK_BTN
                        b.color = TEXT
                self.status.text = f"مدل فعال: {mid}"
            return _cb
        for mid, label in AI_MODELS:
            b = RoundButton(text=label, bg=GOLD if mid == default_model else DARK_BTN,
                            fg=(0, 0, 0, 1) if mid == default_model else TEXT, height=dp(44))
            b.bind(on_release=_pick_model(mid, b))
            self._model_btns[mid] = b
            model_grid.add_widget(b)
        content.add_widget(model_grid)
        content.add_widget(self.model_sp)

        def _refresh_models(*_):
            key = (self.api_key.text or db.get_setting("coarena_api_key", "")).strip()
            if len(key) < 8:
                self.status.text = "ابتدا کلید Coarena را ذخیره کن"
                return
            self.status.text = "در حال دریافت فهرست مدل‌ها…"
            def _work():
                try:
                    from apex import coarena_ai as _c
                    ids = [m.get("id") for m in _c.list_models(key) if m.get("id")]
                    def _apply(*_a):
                        known = set(self._model_btns)
                        added = 0
                        for mid in ids:
                            if mid in known:
                                continue
                            b = RoundButton(text=mid, bg=DARK_BTN, fg=TEXT, height=dp(44))
                            b.bind(on_release=_pick_model(mid, b))
                            self._model_btns[mid] = b
                            model_grid.add_widget(b)
                            added += 1
                        self.status.text = f"فهرست مدل‌ها به‌روز شد ({len(ids)} مدل، {added} جدید)"
                    Clock.schedule_once(_apply)
                except Exception as exc:
                    message = str(exc)[:120]
                    Clock.schedule_once(lambda *_a, msg=message: setattr(
                        self.status, "text", f"دریافت مدل‌ها ناموفق: {msg}"
                    ))
            _run_background(_work)
        refresh_models = RoundButton(text="به‌روزرسانی فهرست مدل‌ها از Coarena", bg=CARD, fg=GOLD,
                                     size_hint_y=None, height=dp(42))
        refresh_models.bind(on_release=_refresh_models)
        content.add_widget(refresh_models)

        gate_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        gate_lbl = M("دروازهٔ AI در Auto-Trade")
        gate_lbl.size_hint_x = 0.45
        self.ai_gate = Spinner(text=db.get_setting("ai_gate", "autopilot"),
                               values=("off", "advisory", "veto", "autopilot"), size_hint_x=0.55)
        self.ai_gate.background_color = DARK_BTN
        self.ai_gate.bind(text=lambda _i, v: db.set_setting("ai_gate", v))
        gate_row.add_widget(gate_lbl)
        gate_row.add_widget(self.ai_gate)
        content.add_widget(gate_row)
        content.add_widget(M("autopilot=تصمیم‌گیری AI در حساب تمرینی؛ پاسخ نامعتبر، اطمینان کمتر از ۷۰٪ یا دادهٔ بی‌کیفیت یعنی عدم ورود. سقف ریسک و تأیید مستقل محلی همیشه برقرار است. advisory فقط نظر ثبت می‌کند؛ veto حق رد/کاهش دارد؛ off هوش ابری را کنار می‌گذارد."))

        # Source-aware news risk filter. Default remains OFF; it never emits direction.
        news_row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(9))
        news_title = BoxLayout(orientation="vertical", spacing=dp(1))
        news_title.add_widget(H("معامله بر مبنای خبر", 14, TEXT))
        news_title.add_widget(M("پیش‌فرض خاموش · RSS منبع‌دار، تازگی خبر و فیلتر رویداد پرریسک"))
        self.news_trade_enabled = db.get_setting("news_trade_enabled", "0") == "1"
        self.news_toggle = RoundButton(
            text="● روشن" if self.news_trade_enabled else "○ خاموش",
            bg=GREEN if self.news_trade_enabled else DARK_BTN,
            fg=(0.02, 0.08, 0.06, 1) if self.news_trade_enabled else MUTED,
            size_hint_x=0.34, height=dp(46), font_size=dp(11))
        def _toggle_news(*_):
            self.news_trade_enabled = not self.news_trade_enabled
            db.set_setting("news_trade_enabled", "1" if self.news_trade_enabled else "0")
            self.news_toggle.text = "● روشن" if self.news_trade_enabled else "○ خاموش"
            self.news_toggle.background_color = GREEN if self.news_trade_enabled else DARK_BTN
            self.news_toggle.color = (0.02, 0.08, 0.06, 1) if self.news_trade_enabled else MUTED
            self.status.text = ("فیلتر ریسک خبر فعال شد؛ نبود خبر تازه/معتبر، ورود خودکار را متوقف می‌کند"
                                if self.news_trade_enabled else "فیلتر ریسک خبر خاموش شد")
        self.news_toggle.bind(on_release=_toggle_news)
        news_row.add_widget(news_title); news_row.add_widget(self.news_toggle)
        content.add_widget(news_row)
        content.add_widget(M("منابع پیکربندی‌شده: CoinDesk، Federal Reserve، BLS، ECB و SEC. پوشش کامل همهٔ بازارها نیست. خبر فقط زمینهٔ ریسک است، نه سیگنال خرید/فروش؛ فیلتر خبر پیش‌فرض خاموش است."))
        self.news_status = M("وضعیت خبرخوان هنوز بررسی نشده است")
        self.news_status.height = dp(48)
        content.add_widget(self.news_status)
        news_refresh = RoundButton(text="دریافت و بررسی خبرهای تازه", bg=CARD, fg=GOLD,
                                   size_hint_y=None, height=dp(42))
        def _refresh_news(*_):
            self.news_status.text = "در حال دریافت فیدهای خبری…"
            def _load_news():
                try:
                    result = news_intelligence.fetch_news(force_refresh=True)
                    count = len(result.get("items") or [])
                    errors = result.get("errors") or []
                    label = f"{count} خبر دریافت شد · {len(errors)} خطای منبع"
                    if errors:
                        label += "\n" + " · ".join(errors[:2])
                    Clock.schedule_once(lambda *_: setattr(self.news_status, "text", label))
                except Exception as exc:
                    message = f"خبرخوان ناموفق: {str(exc)[:120]}"
                    Clock.schedule_once(lambda *_a, msg=message: setattr(self.news_status, "text", msg))
            _run_background(_load_news)
        news_refresh.bind(on_release=_refresh_news)
        content.add_widget(news_refresh)

        news_ai = RoundButton(text="تحلیل زمینه‌ای خبر با هوش مصنوعی", bg=BLUE_GLOW, fg=TEXT,
                              size_hint_y=None, height=dp(44))
        def _analyze_news(*_):
            symbol = str(getattr(App.get_running_app(), "current_symbol", "BTC") or "BTC").upper()
            self.news_status.text = f"در حال تحلیل خبرهای مرتبط با {symbol}…"
            def _work_news_ai():
                try:
                    context = news_intelligence.get_news_context(symbol, force_refresh=False)
                    items = context.get("items") or []
                    if not items:
                        raise ValueError("خبر تازه و مرتبط از منبع تأییدشده پیدا نشد")
                    api_key = (db.get_setting("coarena_api_key", "") or get_env("COARENA_API_KEY") or "").strip()
                    model = db.get_setting("coarena_model", "") or ""
                    summaries = []
                    for article in items[:2]:
                        result = news_intelligence.summarize_news_with_ai(
                            symbol, article, api_key=api_key, model=model
                        )
                        data = result.get("analysis") or {}
                        summaries.append(
                            f"{article.get('source')}: {data.get('summary_fa') or article.get('title')}\n"
                            f"ریسک: {data.get('risk_level', 'نامشخص')} · اطمینان: {data.get('confidence', 0)}٪ · "
                            f"عدم‌قطعیت: {str(data.get('uncertainty') or 'ثبت نشده')[:120]}"
                        )
                    message = "\n\n".join(summaries) + "\n\nاین تحلیل خبر، دستور خرید یا فروش نیست."
                except Exception as exc:
                    message = f"تحلیل خبر انجام نشد: {str(exc)[:150]}"
                Clock.schedule_once(lambda *_a, msg=message: setattr(self.news_status, "text", msg))
            _run_background(_work_news_ai)
        news_ai.bind(on_release=_analyze_news)
        content.add_widget(news_ai)

        # Economic release calendar: official BLS ICS coverage, source and timezone explicit.
        content.add_widget(H("تقویم اقتصادی رسمی", 15, GOLD))
        self.calendar_status = M("منابع رسمی: BLS، فدرال رزرو و ECB · بعضی رویدادها فقط تاریخ دارند")
        self.calendar_status.height = dp(42)
        content.add_widget(self.calendar_status)
        calendar_actions = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(7))
        self.calendar_refresh_btn = RoundButton(text="به‌روزرسانی تقویم", bg=CARD, fg=GOLD)
        self.calendar_refresh_btn.bind(on_release=lambda *_: self.refresh_economic_calendar())
        calendar_actions.add_widget(self.calendar_refresh_btn)
        content.add_widget(calendar_actions)
        self.calendar_box = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.calendar_box.bind(minimum_height=self.calendar_box.setter("height"))
        content.add_widget(self.calendar_box)

        manager_row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        manager_title = BoxLayout(orientation="vertical", spacing=dp(1))
        manager_title.add_widget(H("مدیر هوشمند موقعیت", 14, TEXT))
        manager_title.add_widget(M("پایش معامله، TP/SL و اعتبار فرضیه؛ فقط دفترکل شبیه‌سازی"))
        manager_enabled = db.get_setting("ai_position_manager_enabled", "1") == "1"
        self.position_manager_toggle = RoundButton(
            text="● روشن" if manager_enabled else "○ خاموش",
            bg=GREEN if manager_enabled else DARK_BTN,
            fg=(0.02, 0.08, 0.06, 1) if manager_enabled else MUTED,
            size_hint_x=0.34, height=dp(46), font_size=dp(11))
        def _toggle_position_manager(*_):
            enabled = db.get_setting("ai_position_manager_enabled", "1") != "1"
            db.set_setting("ai_position_manager_enabled", "1" if enabled else "0")
            self.position_manager_toggle.text = "● روشن" if enabled else "○ خاموش"
            self.position_manager_toggle.background_color = GREEN if enabled else DARK_BTN
            self.position_manager_toggle.color = (0.02, 0.08, 0.06, 1) if enabled else MUTED
        self.position_manager_toggle.bind(on_release=_toggle_position_manager)
        manager_row.add_widget(manager_title)
        manager_row.add_widget(self.position_manager_toggle)
        content.add_widget(manager_row)

        share_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        share_lbl = M("ارسال جزئیات حساب به ابر")
        share_lbl.size_hint_x = 0.45
        self.ai_share = Spinner(text="روشن" if db.get_setting("ai_share_account", "0") == "1" else "خاموش",
                                values=("خاموش", "روشن"), size_hint_x=0.55)
        self.ai_share.background_color = DARK_BTN
        self.ai_share.bind(text=lambda _i, v: db.set_setting("ai_share_account", "1" if v == "روشن" else "0"))
        share_row.add_widget(share_lbl)
        share_row.add_widget(self.ai_share)
        content.add_widget(share_row)

        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.sym = Field(text=db.get_setting("default_symbol", "BTC"), hint_text="BTC / XAU / IRG18 / WTI / XAUF / WTIF")
        self.tf = Spinner(text=db.get_setting("default_timeframe", "1hour"), values=("15min", "1hour", "4hour", "1day"), size_hint_x=0.42)
        self.tf.background_color = DARK_BTN
        row.add_widget(self.sym)
        row.add_widget(self.tf)
        content.add_widget(row)

        actions = GridLayout(cols=2, size_hint_y=None, height=dp(188), spacing=dp(8))
        self.analysis_btn = RoundButton(text="تحلیل تایم‌فریم انتخابی", bg=GOLD, fg=(0, 0, 0, 1))
        self.analysis_btn.bind(on_release=lambda *_: self.analyze())
        self.multi_btn = RoundButton(text="تحلیل چندبازه‌ای ۱۵د / ۱س / ۴س", bg=GREEN, fg=(0, 0, 0, 1))
        self.multi_btn.bind(on_release=lambda *_: self.analyze_multi())
        self.chat_btn = RoundButton(text="پاسخ ترکیبی AI", bg=DARK_BTN, fg=TEXT)
        self.chat_btn.bind(on_release=lambda *_: self.chat())
        self.cloud_btn = RoundButton(text="تحلیل ابری Coarena", bg=GOLD, fg=(0, 0, 0, 1))
        self.cloud_btn.bind(on_release=lambda *_: self.cloud_deep())
        self.clear_btn = RoundButton(text="پاک‌کردن پرسش", bg=DARK_BTN, fg=TEXT)
        self.clear_btn.bind(on_release=lambda *_: setattr(self.prompt, "text", ""))
        self.mem_btn = RoundButton(text="حافظهٔ یادگیری", bg=DARK_BTN, fg=TEXT)
        self.mem_btn.bind(on_release=lambda *_: self.show_memory())
        for widget in (self.analysis_btn, self.multi_btn, self.chat_btn, self.cloud_btn, self.mem_btn, self.clear_btn):
            actions.add_widget(widget)
        content.add_widget(actions)
        self.history_btn = RoundButton(text="گزارش یادگیری از ژورنال دمو", bg=CARD, fg=GOLD,
                                        size_hint_y=None, height=dp(42))
        self.history_btn.bind(on_release=lambda *_: self.show_trade_learning())
        content.add_widget(self.history_btn)

        lab_row = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(7))
        self.strategy_select = Spinner(text="تقاطع EMA", values=("تقاطع EMA", "بازگشت RSI", "شکست کانال"), size_hint_x=0.38)
        self.strategy_select.background_color = DARK_BTN
        self.backtest_btn = RoundButton(text="اجرای بک‌تست", bg=BLUE_GLOW, font_size=dp(11))
        self.validation_btn = RoundButton(text="آزمون ۷۰/۳۰", bg=DARK_BTN, fg=GOLD, font_size=dp(11))
        self.backtest_btn.bind(on_release=lambda *_: self.run_backtest(False))
        self.validation_btn.bind(on_release=lambda *_: self.run_backtest(True))
        lab_row.add_widget(self.strategy_select)
        lab_row.add_widget(self.backtest_btn)
        lab_row.add_widget(self.validation_btn)
        content.add_widget(lab_row)
        self.weekly_btn = RoundButton(text="ساخت گزارش هفتگی مربی هوشمند", bg=CARD, fg=GREEN,
                                      size_hint_y=None, height=dp(48))
        self.weekly_btn.bind(on_release=lambda *_: self.show_weekly_review())
        content.add_widget(self.weekly_btn)

        self.prompt = Field(hint_text="مثلاً ADX و RSI چه فرقی دارند؟", multiline=True,
                            height=dp(70), size_hint_y=None)
        content.add_widget(self.prompt)
        quick = GridLayout(cols=3, size_hint_y=None, height=dp(84), spacing=dp(5))
        for label, question in (("RSI / ADX", "RSI و ADX چه تفاوتی دارند؟"),
                                ("Funding / OI", "فاندینگ و اوپن اینترست چه می‌گویند؟"),
                                ("مدیریت ریسک", "مدیریت ریسک در دفترکل شبیه‌سازی چیست؟"),
                                ("استراتژی من", "استراتژی انتخاب‌شده چگونه کار می‌کند و چه زمانی نامعتبر می‌شود؟"),
                                ("حمایت / مقاومت", "با توجه به کندل‌های بسته‌شده، نواحی حمایت و مقاومت را چگونه ارزیابی می‌کنی؟"),
                                ("چرا رد شد؟", "چرا آخرین سناریوی خودکار رد شد؟ همهٔ شروط و داده‌های ناقص را توضیح بده.")):
            btn = RoundButton(text=label, bg=CARD, fg=MUTED, font_size=dp(10))
            btn.bind(on_release=lambda _btn, q=question: self.set_question(q))
            quick.add_widget(btn)
        content.add_widget(quick)
        coach_quick = GridLayout(cols=3, size_hint_y=None, height=dp(42), spacing=dp(5))
        for label, question in (("آخرین اسکن", "آخرین اسکن معامله‌گر تمرینی چه نتیجه‌ای داشت و چرا نمادها رد شدند؟"),
                                ("یادگیری", "حافظهٔ آماری معاملات بسته‌شده برای این نماد چه چیزی نشان می‌دهد؟"),
                                ("هفتگی", "گزارش هفتگی عملکرد دفترکل شبیه‌سازی را توضیح بده")):
            btn = RoundButton(text=label, bg=BLUE_GLOW, fg=TEXT, font_size=dp(10))
            btn.bind(on_release=lambda _btn, q=question: self.set_question(q))
            coach_quick.add_widget(btn)
        content.add_widget(coach_quick)

        self.status = M("برای شروع، نماد را انتخاب و تحلیل را اجرا کن.")
        self.status.height = dp(28)
        content.add_widget(self.status)
        self.out = TextInput(text="خروجی تحلیل و پاسخ آموزشی اینجا نمایش داده می‌شود.",
                             multiline=True, readonly=True, size_hint_y=None, height=dp(330),
                             background_normal="", background_active="", background_color=CARD,
                             foreground_color=TEXT, cursor_color=GOLD, padding=dp(11), font_size=dp(12))
        content.add_widget(self.out)
        content.add_widget(M("امتیاز هم‌گرایی، احتمال موفقیت نیست. خروجی برای آموزش و معاملهٔ مجازی است."))
        scroll.add_widget(content)
        self.add_widget(scroll)
        self.latest_analysis = None
        self.latest_analysis_symbol = None
        self.latest_ticker = None
        self.latest_multi = None
        history = db.list_chat(2)
        if history:
            self.out.text = "\n\n".join(f"{x['role']}: {x['content']}" for x in history[-2:])

    def refresh_economic_calendar(self, force=True):
        if getattr(self, "_calendar_loading", False):
            return
        self._calendar_loading = True
        self.calendar_refresh_btn.disabled = True
        self.calendar_status.text = "در حال دریافت تقویم رسمی BLS، فدرال رزرو و ECB…"

        def worker():
            try:
                result = economic_calendar.upcoming_events(10, force_refresh=force)
                rows = result.get("events") or []
                errors = result.get("errors") or []
                lines = []
                for event in rows[:8]:
                    precision = event.get("time_precision", "exact")
                    if precision == "date_range":
                        date_label = f"{event.get('source_date')} تا {event.get('source_end_date')} · تمام‌روز"
                    elif precision == "date_only":
                        date_label = f"{event.get('source_date')} · ساعت قطعی اعلام نشده"
                    else:
                        local_dt = datetime.fromtimestamp(int(event["starts_at"]))
                        date_label = local_dt.strftime('%Y-%m-%d  %H:%M')
                    importance = {"high": "مهم", "medium": "متوسط", "low": "عادی"}.get(
                        event.get("importance"), "نامشخص")
                    lines.append(
                        f"{date_label} · {importance}\n"
                        f"{event.get('title')}\n"
                        f"منبع: {event.get('source')} · منطقهٔ زمانی منبع: {event.get('source_timezone')}"
                    )
                if not lines:
                    lines.append("رویداد آینده‌ای از منابع تقویم پیکربندی‌شده دریافت نشد.")
                note = "پوشش فعلی: انتشارهای BLS، جلسات FOMC و نشست‌های شورای حکام ECB؛ تقویم کامل جهانی نیست."
                if result.get("cache_fallback"):
                    note += "\nهشدار: منابع این بار پاسخ ندادند؛ رویدادها از آخرین کش شناخته‌شده‌اند و تازه‌بودنشان تأیید نشده است."
                if errors:
                    note += "\nخطا: " + " · ".join(errors[:1])
                text = note + "\n\n" + "\n\n".join(lines)
                status = (
                    f"{len(rows)} رویداد در کش · BLS / Fed / ECB · به‌روزرسانی "
                    f"{datetime.fromtimestamp(result.get('fetched_at') or time.time()).strftime('%H:%M')}"
                )
            except Exception as exc:
                text = "دریافت تقویم ناموفق بود؛ رویداد ساختگی نمایش داده نمی‌شود."
                status = f"خطای تقویم: {str(exc)[:120]}"

            def apply(*_):
                self._calendar_loading = False
                self.calendar_refresh_btn.disabled = False
                self.calendar_status.text = status
                self.calendar_box.clear_widgets()
                label = M(text)
                label.height = dp(max(64, min(220, 18 * (text.count("\n") + 2))))
                self.calendar_box.add_widget(label)
            Clock.schedule_once(apply)
        _run_background(worker)

    def on_enter(self, *args):
        # Generate and save one local coaching review automatically on the first
        # AI-screen visit of each ISO week; it never sends data to an external AI.
        week_key = datetime.now().strftime("%G-W%V")
        if db.get_setting("weekly_review_generated", "") == week_key:
            return
        self.status.text = "در حال ساخت مرور هفتگی محلی از ژورنال دمو…"
        def worker():
            try:
                result = learning_engine.generate_weekly_review()
                report = result["report"]
                db.add_chat("analysis", report, "KALVO Weekly Coach")
                db.set_setting("weekly_review_generated", week_key)
                message = "گزارش هفتگی تازه ساخته و در تاریخچه ذخیره شد"
            except Exception as exc:
                message = f"ساخت خودکار گزارش هفتگی ناموفق بود: {str(exc)[:100]}"
            Clock.schedule_once(lambda *_: setattr(self.status, "text", message))
        _run_background(worker)

    def set_question(self, question):
        self.prompt.text = question
        self.chat()

    def _safe_symbol(self):
        symbol = self.sym.text.strip() or "BTC"
        try:
            App.get_running_app().set_symbol(symbol)
            return App.get_running_app().current_symbol
        except ValueError as exc:
            self.status.text = str(exc)
            return None

    def analyze(self):
        symbol = self._safe_symbol()
        if not symbol:
            return
        timeframe = self.tf.text
        self.status.text = f"دریافت دادهٔ بسته‌شدهٔ {timeframe} و محاسبه شاخص‌ها…"
        self.analysis_btn.disabled = True

        def work():
            try:
                from apex import analysis_engine as eng
                ticker = mapi.get_ticker(symbol)
                candles = eng.completed_candles(mapi.fetch_candles(symbol, timeframe), timeframe)
                result = eng.analyze_candles(candles)
                output = eng.format_analysis(symbol, result)
                if ticker.get("price"):
                    age = max(0, int(time.time() - float(ticker.get("ts") or time.time())))
                    output += f"\n\nقیمت بازار: {pxfmt(ticker['price'])} · منبع {ticker.get('source') or '—'} · سن داده {age} ثانیه"
                    output += f"\nوضعیت منبع: {ticker.get('status') or 'نامشخص'} · زمان ارائه‌دهنده: {ticker.get('provider_ts') or 'ثبت نشده'}"
                else:
                    output += "\n\nقیمت بازار تازه دریافت نشد؛ از قیمت قبلی استفاده نشده است."
                memory = learning_memory.context_for_symbol(symbol)
                overall = memory.get("overall") or {}
                output += (f"\n\nحافظهٔ آماری دمو برای {symbol}: نمونه {overall.get('n', 0)} · "
                           f"نرخ مشاهده‌شده {overall.get('observed_win_rate') if overall.get('observed_win_rate') is not None else 'ناموجود'}٪ · "
                           f"بازهٔ ۹۵٪ {overall.get('interval_95')} · قابلیت اتکا {overall.get('reliability')}.")
                output += "\nاین آمار شرح تاریخچه است، نه احتمال برد آینده یا توصیهٔ معامله."
                self.latest_analysis, self.latest_ticker = result, ticker
                self.latest_analysis_symbol = symbol
                db.add_chat("analysis", output, "KALVO Local")
                Clock.schedule_once(lambda *_: self._analysis_done(output, True))
            except Exception as exc:
                message = str(exc)[:150]
                Clock.schedule_once(lambda *_: self._analysis_done(f"تحلیل انجام نشد: {message}", False))

        _run_background(work)

    def analyze_multi(self):
        symbol = self._safe_symbol()
        if not symbol:
            return
        self.multi_btn.disabled = True
        self.status.text = "تحلیل ۱۵ دقیقه، ۱ ساعت و ۴ ساعت…"

        def work():
            try:
                from apex import analysis_engine as eng
                frames = ("15min", "1hour", "4hour")
                results, candles_count = {}, {}
                ticker = mapi.get_ticker(symbol)
                for timeframe in frames:
                    candles = eng.completed_candles(mapi.fetch_candles(symbol, timeframe), timeframe)
                    results[timeframe] = eng.analyze_candles(candles)
                    candles_count[timeframe] = len(candles)
                consensus = eng.combine_timeframes(results)
                parts = [f"گزارش چندبازه‌ای KALVO · {symbol}"]
                for timeframe in frames:
                    parts.append(f"\n--- {timeframe} · {candles_count[timeframe]} کندل بسته‌شده ---\n" +
                                 eng.format_analysis(symbol, results[timeframe]))
                parts.append("\n--- جمع‌بندی تایم‌فریم‌ها ---")
                if consensus.get("ready"):
                    parts.append(f"هم‌جهتی: {'تأیید' if consensus.get('aligned') else 'تأیید نشد'} · "
                                 f"جهت: {consensus.get('direction')} · امتیاز ترکیبی: {consensus.get('score')}/100")
                    parts.append(consensus.get("note", ""))
                else:
                    parts.append(consensus.get("reason", "دادهٔ چندبازه‌ای کامل نیست"))
                if ticker.get("price"):
                    parts.append(f"قیمت بازار {pxfmt(ticker['price'])} · منبع {ticker.get('source') or '—'}")
                memory = learning_memory.context_for_symbol(symbol)
                overall = memory.get("overall") or {}
                parts.append(f"حافظهٔ آماری دفترکل دمو: {overall.get('n', 0)} نمونه · نرخ مشاهده‌شده {overall.get('observed_win_rate')}٪ · بازهٔ ۹۵٪ {overall.get('interval_95')}.")
                parts.append("این نتیجه احتمال برد یا توصیهٔ معامله نیست؛ فقط مقایسهٔ قواعد تکنیکال در سه بازه است. حافظه، نتیجهٔ گذشته را توصیف می‌کند و آینده را پیش‌بینی نمی‌کند.")
                output = "\n".join(parts)
                self.latest_analysis = results.get("1hour")
                self.latest_analysis_symbol = symbol
                self.latest_ticker = ticker
                self.latest_multi = {"frames": results, "consensus": consensus}
                db.add_chat("analysis", output, "KALVO Multi-Timeframe")
                Clock.schedule_once(lambda *_: self._multi_done(output, True))
            except Exception as exc:
                message = str(exc)[:150]
                Clock.schedule_once(lambda *_: self._multi_done(f"تحلیل چندبازه‌ای انجام نشد: {message}", False))

        _run_background(work)

    def _analysis_done(self, output, ok):
        self.analysis_btn.disabled = False
        self.out.text = output
        self.status.text = "تحلیل تکمیل شد" if ok else "تحلیل ناموفق بود"
        ui_feedback.play("success" if ok else "alert")

    def _multi_done(self, output, ok):
        self.multi_btn.disabled = False
        self.out.text = output
        self.status.text = "تحلیل چندبازه‌ای تکمیل شد" if ok else "تحلیل چندبازه‌ای ناموفق بود"
        ui_feedback.play("success" if ok else "alert")

    def chat(self):
        question = self.prompt.text.strip()
        if not question:
            self.status.text = "ابتدا پرسش آموزشی را بنویس"
            return
        mode = (self.ai_mode.text if hasattr(self, "ai_mode") else "local") or "local"
        db.set_setting("ai_mode", mode)
        if hasattr(self, "model_sp"):
            db.set_setting("coarena_model", self.model_sp.text)
        if hasattr(self, "api_key"):
            db.set_setting("coarena_api_key", (self.api_key.text or "").strip())
        self.chat_btn.disabled = True
        self.status.text = "در حال تولید پاسخ ترکیبی…"

        def work():
            try:
                if any(term in question.lower() for term in ("ژورنال", "عملکرد من", "تاریخچه معاملات", "معاملات من", "وین ریت")):
                    answer = learning_engine.format_history_fa(learning_engine.analyze_history())
                    tag = "KALVO Local Journal"
                else:
                    app = App.get_running_app()
                    live_prices = {k: v for k, v in getattr(app, "price_cache", {}).items()
                                   if not k.endswith("-USDT") and getattr(app, "price_cache_ts", {}).get(k, 0) >= time.time() - 30}
                    weekly = learning_engine.generate_weekly_review()
                    chat_history = db.list_chat(200)
                    auto_audit = next((item.get("content", "") for item in reversed(chat_history)
                                       if item.get("model") == "KALVO Auto Audit"), None)
                    selected_symbol = canonical_market_symbol(self.sym.text.strip() or db.get_setting("default_symbol", "BTC"))
                    try:
                        active_ticker = mapi.get_ticker(selected_symbol)
                    except Exception:
                        active_ticker = self.latest_ticker if (self.latest_ticker or {}).get("base") == selected_symbol else None
                    analysis_context = self.latest_analysis if self.latest_analysis_symbol == selected_symbol else None
                    learning_profile = learning_engine.adaptive_profile()
                    symbol_memory = learning_memory.context_for_symbol(selected_symbol)
                    try:
                        calendar_context = economic_calendar.upcoming_events(5).get("events", [])
                    except Exception:
                        calendar_context = []
                    local_answer = assistant.answer_question(
                        question, analysis_context, active_ticker,
                        positions=db.list_open_positions(), price_map=live_prices,
                        weekly_report=weekly, auto_report=auto_audit,
                        learning_profile={**(learning_profile or {}), "symbol_memory": symbol_memory},
                    )
                    api_key = db.get_setting("coarena_api_key", "") or get_env("COARENA_API_KEY")
                    try:
                        from apex import coarena_ai as _c
                        api_key = _c.resolve_key(api_key)
                    except Exception:
                        pass
                    if mode in ("cloud", "both") and api_key and len(api_key) >= 8:
                        from apex import hybrid_ai
                        symbol = selected_symbol
                        ctx_block = hybrid_ai.build_context_block(
                            symbol=symbol,
                            analysis=analysis_context,
                            ticker=active_ticker,
                            positions=db.list_open_positions(),
                            wallet=db.wallet_snapshot() if hasattr(db, "wallet_snapshot") else None,
                            learning_summary=(str(learning_profile)[:250] + " | " + str(symbol_memory)[:900]),
                            share_account=db.get_setting("ai_share_account", "0") == "1",
                        )
                        try:
                            news_context = news_intelligence.get_news_context(symbol)
                        except Exception:
                            news_context = {"items": [], "errors": ["news context unavailable"]}
                        ctx_block += "\nتقویم اقتصادی رسمی BLS / FOMC / ECB (صرفاً زمان‌بندی، نه پیش‌بینی): " + json.dumps(calendar_context[:5], ensure_ascii=False, default=str)[:1400]
                        ctx_block += "\nزمینهٔ اخبار منبع‌دار (جهت خرید/فروش محسوب نشود): " + json.dumps(news_context, ensure_ascii=False, default=str)[:1500]
                        ctx_block += "\nاین محصول فقط دفترکل و معاملات شبیه‌سازی‌شده دارد؛ هیچ سفارش زنده‌ای صادر نکن. اعداد عملکرد گذشته، احتمال موفقیت آینده نیستند."
                        result = hybrid_ai.hybrid_reply(
                            mode=mode,
                            question=question,
                            api_key=api_key,
                            model=db.get_setting("coarena_model", "claude-sonnet-5-5"),
                            ctx={"symbol": symbol, "analysis": analysis_context, "ticker": active_ticker,
                                 "context_block": ctx_block},
                        )
                        answer = result.get("text") or local_answer
                        tag = "KALVO Hybrid AI"
                    else:
                        answer = local_answer
                        tag = "KALVO Local"
                db.add_chat("user", question, tag)
                db.add_chat("assistant", answer, tag)
                Clock.schedule_once(lambda *_: self._chat_done(answer, True))
            except Exception as exc:
                message = str(exc)[:180]
                Clock.schedule_once(lambda *_a, msg=message: self._chat_done(f"خطا: {msg}", False))

        _run_background(work)

    def _chat_done(self, text, ok):
        self.chat_btn.disabled = False
        if hasattr(self, "cloud_btn"):
            self.cloud_btn.disabled = False
        self.out.text = text
        self.status.text = "پاسخ آماده شد" if ok else "پاسخ ناموفق"
        ui_feedback.play("success" if ok else "alert")

    def cloud_deep(self):
        """Deep Coarena analysis using latest multi-timeframe context."""
        api_key = ((self.api_key.text if hasattr(self, "api_key") else "")
                   or db.get_setting("coarena_api_key", "") or get_env("COARENA_API_KEY"))
        try:
            from apex import coarena_ai as _c
            api_key = _c.resolve_key(api_key)
        except Exception:
            pass
        if not api_key or len(api_key.strip()) < 8:
            self.status.text = "ابتدا کلید Coarena را ذخیره کن"
            return
        symbol = self._safe_symbol()
        if not symbol:
            return
        db.set_setting("coarena_api_key", api_key.strip())
        if hasattr(self, "model_sp"):
            db.set_setting("coarena_model", self.model_sp.text)
        self.cloud_btn.disabled = True
        self.status.text = "تحلیل ابری عمیق (تا ۳ تلاش)…"

        def work():
            try:
                from apex import hybrid_ai, analysis_engine as eng
                frames = {}
                for tf in ("15min", "1hour", "4hour"):
                    candles = eng.completed_candles(mapi.fetch_candles(symbol, tf), tf)
                    frames[tf] = eng.analyze_candles(candles)
                consensus = eng.combine_timeframes(frames)
                ticker = mapi.get_ticker(symbol)
                analysis = frames.get("1hour") or {}
                analysis = dict(analysis)
                analysis["mtf_consensus"] = consensus
                symbol_memory = learning_memory.context_for_symbol(symbol)
                try:
                    news_context = news_intelligence.get_news_context(symbol)
                except Exception as news_exc:
                    news_context = {"items": [], "errors": [str(news_exc)[:100]],
                                    "coverage_note": "News context unavailable; do not infer headlines."}
                try:
                    calendar_context = economic_calendar.upcoming_events(8).get("events", [])
                except Exception as calendar_exc:
                    calendar_context = [{"status": "unavailable", "reason": str(calendar_exc)[:100]}]
                ctx_block = hybrid_ai.build_context_block(
                    symbol=symbol,
                    analysis=analysis,
                    ticker=ticker,
                    positions=db.list_open_positions(),
                    wallet=db.wallet_snapshot() if hasattr(db, "wallet_snapshot") else None,
                    learning_summary=(str(learning_engine.adaptive_profile())[:500] +
                                      " | symbol_memory=" + json.dumps(symbol_memory, ensure_ascii=False, default=str)[:1000]),
                    share_account=db.get_setting("ai_share_account", "0") == "1",
                )
                ctx_block += "\nزمینهٔ اخبار معتبر و مرتبط (جهت بازار را از تیتر نتیجه نگیر): " + json.dumps(news_context, ensure_ascii=False, default=str)[:1700]
                ctx_block += "\nتقویم BLS/FOMC/ECB (تاریخ‌محور؛ بعضی رویدادها ساعت دقیق ندارند): " + json.dumps(calendar_context[:8], ensure_ascii=False, default=str)[:1700]
                prompt = (self.prompt.text.strip() or
                          "تحلیل چندبازه‌ای و قابل ممیزی بده: وضعیت داده، رژیم بازار، ساختار روند، شواهد موافق و مخالف، سناریوی صعود و نزول، شرط ابطال، نواحی فرضی فقط اگر از داده پشتیبانی می‌شوند، و برنامهٔ مدیریت یک موقعیت کاملاً شبیه‌سازی‌شده. اگر ستاپ معتبر نیست صریحاً NO-TRADE بنویس.")
                text = hybrid_ai.cloud_analyze(
                    api_key=api_key.strip(),
                    model=db.get_setting("coarena_model", "claude-sonnet-5-5"),
                    symbol=symbol,
                    user_prompt=prompt,
                    context_block=ctx_block,
                    retries=3,
                )
                self.latest_analysis = analysis
                self.latest_ticker = ticker
                db.add_chat("analysis", text, "KALVO Coarena")
                Clock.schedule_once(lambda *_: self._chat_done(text, True))
            except Exception as exc:
                message = str(exc)[:180]
                Clock.schedule_once(lambda *_a, msg=message: self._chat_done(f"ابر ناموفق: {msg}", False))

        _run_background(work)

    def show_memory(self):
        try:
            from apex import learning_memory as lm
            from apex import ai_core
            text = lm.format_memory_fa() + "\n\n" + ai_core.format_core_status_fa()
        except Exception as exc:
            text = f"حافظه در دسترس نیست: {exc}"
        self.out.text = text
        self.status.text = "حافظهٔ تطبیقی"

    def run_backtest(self, validation=False):

        symbol = self._safe_symbol()
        if not symbol:
            return
        strategy_map = {"تقاطع EMA": "ema_cross", "بازگشت RSI": "rsi_rebound", "شکست کانال": "donchian_breakout"}
        strategy = strategy_map.get(self.strategy_select.text, "ema_cross")
        timeframe = self.tf.text
        self.backtest_btn.disabled = True
        self.validation_btn.disabled = True
        self.status.text = "دریافت تاریخچه و اجرای آزمون با کارمزد و لغزش…"
        def work():
            try:
                raw = mapi.fetch_candles(symbol, timeframe)
                candles = analysis.completed_candles(raw, timeframe)
                result = strategy_lab.walk_forward(candles, strategy) if validation else strategy_lab.backtest(candles, strategy)
                if validation:
                    a, b = result["train"], result["validation"]
                    output = (f"آزمون زمانی ۷۰/۳۰ · {symbol} · {strategy}\n"
                              f"آموزش: {a['trades']} معامله · بازده {a['return_pct']:+.2f}% · افت {a['max_drawdown']:.2f}\n"
                              f"اعتبارسنجی: {b['trades']} معامله · بازده {b['return_pct']:+.2f}% · افت {b['max_drawdown']:.2f}\n"
                              f"{result['note']}\nاین آزمون ساده‌شده است و تضمین عملکرد آینده نیست.")
                else:
                    st = result["stats"]
                    pf = "—" if st["profit_factor"] is None else ("∞" if math.isinf(st["profit_factor"]) else f"{st['profit_factor']:.2f}")
                    output = (f"آزمایش استراتژی KALVO · {symbol} · {strategy}\n"
                              f"کندل: {result['candles']} · معاملات: {st['trades']} · برد: {st['win_rate_pct']:.1f}%\n"
                              f"سود/زیان خالص: {st['net_pnl']:+.2f} USDT مجازی · بازده: {st['return_pct']:+.2f}%\n"
                              f"Profit Factor: {pf} · افت بیشینه: {st['max_drawdown']:.2f} · کارمزد: {st['fees_paid']:.2f}\n"
                              f"{result['note']}\nاین نتیجهٔ تاریخی است، نه توصیه یا پیش‌بینی.")
                db.add_chat("analysis", output, "KALVO Strategy Lab")
            except Exception as exc:
                output = f"بک‌تست انجام نشد: {str(exc)[:160]}"
            Clock.schedule_once(lambda *_: self._lab_done(output))
        _run_background(work)

    def _lab_done(self, output):
        self.backtest_btn.disabled = False
        self.validation_btn.disabled = False
        self.out.text = output
        self.status.text = "آزمایش استراتژی تکمیل شد" if "انجام نشد" not in output else "آزمایش ناموفق بود"

    def show_weekly_review(self):
        self.weekly_btn.disabled = True
        self.status.text = "تحلیل ژورنال هفت روز گذشته…"
        def work():
            try:
                result = learning_engine.generate_weekly_review()
                report = result["report"]
                db.add_chat("analysis", report, "KALVO Weekly Coach")
            except Exception as exc:
                report = f"گزارش هفتگی ساخته نشد: {str(exc)[:120]}"
            Clock.schedule_once(lambda *_: self._weekly_done(report))
        _run_background(work)

    def _weekly_done(self, report):
        self.weekly_btn.disabled = False
        self.out.text = report
        self.status.text = "گزارش هفتگی محلی ساخته شد"

    def show_trade_learning(self):
        self.history_btn.disabled = True
        self.status.text = "محاسبهٔ آمار معاملات بسته‌شده…"
        def work():
            try:
                report = learning_engine.format_history_fa(learning_engine.analyze_history())
                db.add_chat("analysis", report, "KALVO Trade Journal")
            except Exception as exc:
                report = f"گزارش ژورنال ناموفق بود: {str(exc)[:120]}"
            Clock.schedule_once(lambda *_: self._history_done(report))
        _run_background(work)

    def _history_done(self, report):
        self.history_btn.disabled = False
        self.out.text = report
        self.status.text = "گزارش ژورنال تکمیل شد"


class MoreScreen(Screen):
    """Personal workspace for notes, watchlist, alerts and provider health."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        scroll = ScrollView(do_scroll_x=False)
        root = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(8), size_hint_y=None)
        root.bind(minimum_height=root.setter("height"))
        root.add_widget(H("مرکز ابزار و شخصی‌سازی", 22, GOLD))
        root.add_widget(H("شخصی‌سازی معاملات", 14))
        prefs = GridLayout(cols=3, size_hint_y=None, height=dp(44), spacing=dp(6))
        self.pref_lev = Field(text="2", hint_text="اهرم پیش‌فرض", input_filter="float")
        self.pref_margin = Field(text="10", hint_text="مارجین پیش‌فرض", input_filter="float")
        self.pref_risk = Field(text="1", hint_text="ریسک پیش‌فرض %", input_filter="float")
        for field in (self.pref_lev, self.pref_margin, self.pref_risk):
            prefs.add_widget(field)
        root.add_widget(prefs)
        profile = GridLayout(cols=2, size_hint_y=None, height=dp(96), spacing=dp(7))
        self.pref_symbol = Field(text="BTC", hint_text="نماد پیش‌فرض")
        self.pref_refresh = Field(text="8", hint_text="تازه‌سازی ثانیه (۵ تا ۶۰)", input_filter="int")
        self.pref_tf = Spinner(text="1hour", values=("15min", "1hour", "4hour", "1day"))
        self.pref_tf.background_color = DARK_BTN
        self.pref_accent = Spinner(text="آبی آسمانی", values=("آبی آسمانی", "فیروزه‌ای", "بنفش"))
        self.pref_accent.background_color = DARK_BTN
        for field in (self.pref_symbol, self.pref_refresh, self.pref_tf, self.pref_accent):
            profile.add_widget(field)
        root.add_widget(profile)
        save_prefs = RoundButton(text="ذخیره تنظیمات پیش‌فرض", bg=DARK_BTN, fg=GOLD,
                                 size_hint_y=None, height=dp(40))
        save_prefs.bind(on_release=lambda *_: self.save_preferences())
        root.add_widget(save_prefs)
        self.pref_msg = M("اهرم ۱ تا ۵ · ریسک هر موقعیت ۰٫۱ تا ۳ درصد")
        root.add_widget(self.pref_msg)

        root.add_widget(H("تنظیمات مدیریت خودکار", 15, GOLD))
        auto_prefs = GridLayout(cols=2, size_hint_y=None, height=dp(96), spacing=dp(7))
        self.auto_interval = Field(text="120", hint_text="فاصلهٔ اسکن (۶۰ تا ۳۰۰ ثانیه)", input_filter="int")
        self.auto_risk = Field(text="0.30", hint_text="بودجهٔ ریسک هر ورودی (%)", input_filter="float")
        self.auto_slots = Field(text="2", hint_text="حداکثر پوزیشن خودکار (۱ تا ۴)", input_filter="int")
        self.auto_score = Field(text="70", hint_text="حداقل هم‌گرایی (۶۰ تا ۹۰)", input_filter="int")
        for field in (self.auto_interval, self.auto_risk, self.auto_slots, self.auto_score):
            auto_prefs.add_widget(field)
        root.add_widget(auto_prefs)
        save_auto = RoundButton(text="ذخیره تنظیمات Auto-Trade", bg=BLUE_GLOW,
                                size_hint_y=None, height=dp(48))
        save_auto.bind(on_release=lambda *_: self.save_auto_preferences())
        root.add_widget(save_auto)
        self.auto_pref_msg = M("فقط دفترکل دمو · ریسک تطبیقی فقط می‌تواند ثابت بماند یا کاهش یابد")
        root.add_widget(self.auto_pref_msg)

        root.add_widget(H("هشدار قیمت", 14))
        alert_row = GridLayout(cols=3, size_hint_y=None, height=dp(42), spacing=dp(6))
        self.alert_sym = Field(text="BTC", hint_text="نماد")
        self.alert_cond = Spinner(text="بالاتر از", values=("بالاتر از", "پایین‌تر از"))
        self.alert_cond.background_color = DARK_BTN
        self.alert_price = Field(hint_text="قیمت هدف", input_filter="float")
        alert_row.add_widget(self.alert_sym)
        alert_row.add_widget(self.alert_cond)
        alert_row.add_widget(self.alert_price)
        root.add_widget(alert_row)
        alert_add = RoundButton(text="ثبت هشدار یک‌باره", bg=GOLD, fg=(0, 0, 0, 1),
                                size_hint_y=None, height=dp(40))
        alert_add.bind(on_release=lambda *_: self.add_alert())
        root.add_widget(alert_add)
        self.alert_msg = M("هشدار فقط با قیمت تازه بررسی می‌شود و پس از فعال‌شدن تکرار نمی‌شود")
        root.add_widget(self.alert_msg)
        self.alert_box = GridLayout(cols=1, spacing=dp(4), size_hint_y=None)
        self.alert_box.bind(minimum_height=self.alert_box.setter("height"))
        root.add_widget(self.alert_box)

        root.add_widget(H("هشدار هوشمند تغییر ۲۴ساعته", 15, GOLD))
        smart_row = GridLayout(cols=3, size_hint_y=None, height=dp(48), spacing=dp(6))
        self.smart_sym = Field(text="BTC", hint_text="نماد")
        self.smart_cond = Spinner(text="بالاتر از", values=("بالاتر از", "پایین‌تر از"))
        self.smart_cond.background_color = DARK_BTN
        self.smart_pct = Field(text="5", hint_text="درصد تغییر", input_filter="float")
        for widget in (self.smart_sym, self.smart_cond, self.smart_pct):
            smart_row.add_widget(widget)
        root.add_widget(smart_row)
        smart_add = RoundButton(text="ثبت هشدار درصد تغییر", bg=BLUE_GLOW, size_hint_y=None, height=dp(48))
        smart_add.bind(on_release=lambda *_: self.add_smart_alert())
        root.add_widget(smart_add)
        self.smart_alert_msg = M("فقط با دادهٔ تازهٔ تغییر ۲۴ساعته ارزیابی می‌شود")
        root.add_widget(self.smart_alert_msg)
        self.smart_alert_box = GridLayout(cols=1, spacing=dp(4), size_hint_y=None)
        self.smart_alert_box.bind(minimum_height=self.smart_alert_box.setter("height"))
        root.add_widget(self.smart_alert_box)

        root.add_widget(H("سناریوی تغییر قیمت پوزیشن‌ها", 15, GOLD))
        scenario_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(7))
        self.scenario_pct = Field(text="5", hint_text="تغییر فرضی قیمت %", input_filter="float")
        scenario_btn = RoundButton(text="محاسبه سناریو", bg=DARK_BTN, fg=GOLD)
        scenario_btn.bind(on_release=lambda *_: self.run_scenario())
        scenario_row.add_widget(self.scenario_pct)
        scenario_row.add_widget(scenario_btn)
        root.add_widget(scenario_row)
        self.scenario_result = M("اثر فرضی بر پوزیشن‌های باز؛ پیش‌بینی قیمت نیست")
        self.scenario_result.height = dp(70)
        root.add_widget(self.scenario_result)

        root.add_widget(H("صندوق اعلان‌ها", 15, GOLD))
        notify_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(7))
        refresh_notifications = RoundButton(text="به‌روزرسانی", bg=DARK_BTN)
        mark_read = RoundButton(text="خواندن همه", bg=BLUE_GLOW)
        refresh_notifications.bind(on_release=lambda *_: self.reload_notifications())
        mark_read.bind(on_release=lambda *_: self.mark_all_notifications_read())
        notify_row.add_widget(refresh_notifications)
        notify_row.add_widget(mark_read)
        root.add_widget(notify_row)
        self.notifications_box = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.notifications_box.bind(minimum_height=self.notifications_box.setter("height"))
        root.add_widget(self.notifications_box)

        root.add_widget(H("یادداشت شخصی", 14))
        self.title_in = Field(hint_text="عنوان")
        self.body_in = Field(hint_text="متن یادداشت", multiline=True, height=dp(90))
        save = RoundButton(text="ذخیره یادداشت", bg=GREEN, fg=(0, 0, 0, 1),
                           size_hint_y=None, height=dp(42))
        save.bind(on_release=lambda *_: self.save())
        root.add_widget(self.title_in)
        root.add_widget(self.body_in)
        root.add_widget(save)
        self.box = GridLayout(cols=1, spacing=dp(4), size_hint_y=None)
        self.box.bind(minimum_height=self.box.setter("height"))
        root.add_widget(self.box)

        root.add_widget(H("تجربهٔ کاربری و نظارت پس‌زمینه", 15, GOLD))
        root.add_widget(M("شعار KALVO: بازار را دقیق‌تر ببین؛ تصمیم را آگاهانه‌تر بررسی کن."))
        self._ui_setting_controls = {}

        def add_ui_toggle(title, key, default, description):
            row = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(8))
            texts = BoxLayout(orientation="vertical", spacing=dp(1))
            texts.add_widget(H(title, 13, TEXT))
            texts.add_widget(M(description))
            enabled = db.get_setting(key, default) == "1"
            button = RoundButton(text="● روشن" if enabled else "○ خاموش",
                                 bg=GREEN if enabled else DARK_BTN,
                                 fg=(0.02, 0.08, 0.06, 1) if enabled else MUTED,
                                 size_hint_x=0.32, height=dp(46), font_size=dp(10))
            def toggle(*_):
                new_value = db.get_setting(key, default) != "1"
                db.set_setting(key, "1" if new_value else "0")
                button.text = "● روشن" if new_value else "○ خاموش"
                button.background_color = GREEN if new_value else DARK_BTN
                button.color = (0.02, 0.08, 0.06, 1) if new_value else MUTED
                if key == "ui_animations_enabled":
                    ui_feedback.configure(animations=new_value)
                elif key == "ui_sounds_enabled":
                    ui_feedback.configure(sounds=new_value)
                    if new_value:
                        App.get_running_app().schedule_optional_sounds()
                elif key == "system_notifications_enabled" and new_value:
                    platform_notifications.request_permission()
                elif key == "background_monitor_enabled":
                    app = App.get_running_app()
                    ok, message = app.start_background_monitor() if new_value else app.stop_background_monitor()
                    self.background_monitor_status.text = message
                    if new_value and not ok:
                        self.background_monitor_status.color = RED
                button.text = "● روشن" if db.get_setting(key, default) == "1" else "○ خاموش"
            button.bind(on_release=toggle)
            row.add_widget(texts)
            row.add_widget(button)
            root.add_widget(row)
            self._ui_setting_controls[key] = button

        add_ui_toggle("انیمیشن‌های رابط", "ui_animations_enabled", "1",
                      "حرکت‌های کوتاه و انتقال نرم بین صفحات")
        add_ui_toggle("افکت صوتی ملایم", "ui_sounds_enabled", "0",
                      "پیش‌فرض خاموش · صدای کلیک و اعلان کوتاه")
        add_ui_toggle("اعلان‌های سیستمی اندروید", "system_notifications_enabled", "1",
                      "اعلان‌های بیرون از برنامه؛ به مجوز اندروید نیاز دارد")
        add_ui_toggle("نظارت پس‌زمینهٔ موقعیت‌های تمرینی", "background_monitor_enabled", "0",
                      "اختیاری، با اعلان دائمی؛ سیستم‌عامل می‌تواند سرویس را متوقف کند")
        self.background_monitor_status = M("نظارت پس‌زمینه خاموش است؛ هیچ سفارش واقعی ارسال نمی‌شود.")
        self.background_monitor_status.height = dp(44)
        root.add_widget(self.background_monitor_status)

        ui_profile = GridLayout(cols=2, size_hint_y=None, height=dp(48), spacing=dp(7))
        self.ui_font_scale = Spinner(text="استاندارد", values=("استاندارد", "درشت", "خیلی درشت"))
        self.ui_font_scale.background_color = DARK_BTN
        self.ui_density = Spinner(text="خودکار", values=("خودکار", "راحت", "فشرده"))
        self.ui_density.background_color = DARK_BTN
        self.ui_font_scale.bind(text=lambda _i, value: self.set_ui_font_scale(value))
        self.ui_density.bind(text=lambda _i, value: self.set_ui_density(value))
        ui_profile.add_widget(self.ui_font_scale)
        ui_profile.add_widget(self.ui_density)
        root.add_widget(ui_profile)
        self.ui_profile_status = M("اندازهٔ متن فوراً اعمال می‌شود؛ تراکم دکمه‌ها پس از راه‌اندازی دوباره اعمال خواهد شد.")
        self.ui_profile_status.height = dp(38)
        root.add_widget(self.ui_profile_status)

        hb = RoundButton(text="سلامت منابع داده بازار", bg=DARK_BTN,
                         size_hint_y=None, height=dp(42))
        hb.bind(on_release=lambda *_: self.health())
        self.health_lbl = M("وضعیت منابع را هنگام نیاز بررسی کن")
        self.health_lbl.height = dp(80)
        root.add_widget(hb)
        root.add_widget(self.health_lbl)
        scroll.add_widget(root)
        self.add_widget(scroll)

    def on_enter(self, *a):
        self.pref_lev.text = db.get_setting("default_leverage", "2")
        self.pref_margin.text = db.get_setting("default_margin", "10")
        self.pref_risk.text = db.get_setting("default_risk_pct", "1")
        self.pref_symbol.text = db.get_setting("default_symbol", "BTC")
        self.pref_refresh.text = db.get_setting("refresh_interval", "8")
        self.pref_tf.text = db.get_setting("default_timeframe", "1hour")
        accent_map = {"sky":"آبی آسمانی", "teal":"فیروزه‌ای", "violet":"بنفش"}
        self.pref_accent.text = accent_map.get(db.get_setting("accent_theme", "sky"), "آبی آسمانی")
        font_map = {"1.0": "استاندارد", "1.08": "درشت", "1.16": "خیلی درشت"}
        self.ui_font_scale.text = font_map.get(db.get_setting("ui_font_scale", "1.0"), "استاندارد")
        self.ui_density.text = {"auto": "خودکار", "compact": "فشرده", "comfortable": "راحت"}.get(db.get_setting("ui_density", "comfortable"), "راحت")
        for key, button in self._ui_setting_controls.items():
            enabled = db.get_setting(key, "1" if key in ("ui_animations_enabled", "system_notifications_enabled") else "0") == "1"
            button.text = "● روشن" if enabled else "○ خاموش"
            button.background_color = GREEN if enabled else DARK_BTN
            button.color = (0.02, 0.08, 0.06, 1) if enabled else MUTED
        bg_on = db.get_setting("background_monitor_enabled", "0") == "1"
        last_cycle = db.get_setting("background_monitor_last_cycle", "")
        last_status = db.get_setting("background_monitor_last_status", "unknown")
        if bg_on:
            if last_cycle.isdigit():
                last_label = datetime.fromtimestamp(int(last_cycle)).strftime("%Y-%m-%d %H:%M")
            else:
                last_label = "هنوز چرخه‌ای ثبت نشده"
            status_label = "سالم" if last_status == "ok" else "نیازمند بررسی"
            self.background_monitor_status.text = (
                f"سرویس درخواست شده · وضعیت آخر: {status_label} · آخرین چرخه: {last_label}. "
                "Android ممکن است سرویس را به‌خاطر باتری، توقف اجباری یا محدودیت سیستم متوقف کند؛ ۲۴/۷ تضمین نمی‌شود."
            )
        else:
            self.background_monitor_status.text = "نظارت پس‌زمینه خاموش است؛ هیچ سفارش واقعی ارسال نمی‌شود."
        self.auto_interval.text = db.get_setting("auto_scan_interval", "120")
        self.auto_risk.text = db.get_setting("auto_risk_pct", "0.30")
        self.auto_slots.text = db.get_setting("auto_max_positions", "2")
        self.auto_score.text = db.get_setting("auto_min_confluence", "70")
        self.reload()

    def set_ui_font_scale(self, value):
        scales = {"استاندارد": ("1.0", 1.0), "درشت": ("1.08", 1.08), "خیلی درشت": ("1.16", 1.16)}
        key, scale = scales.get(value, scales["استاندارد"])
        db.set_setting("ui_font_scale", key)
        global UI_FONT_SCALE
        UI_FONT_SCALE = scale
        app = App.get_running_app()
        if getattr(app, "root", None) is not None:
            _apply_font_scale(app.root, scale)
        self.ui_profile_status.text = f"اندازهٔ متن روی {value} تنظیم شد."

    def set_ui_density(self, value):
        global UI_COMPACT, UI_SCREEN_PROFILE
        setting = {"خودکار": "auto", "فشرده": "compact", "راحت": "comfortable"}.get(value, "auto")
        db.set_setting("ui_density", setting)
        automatic_compact = bool(UI_SCREEN_PROFILE.get("compact"))
        UI_COMPACT = setting == "compact" or (setting == "auto" and automatic_compact)
        self.ui_profile_status.text = (
            "تراکم بر اساس عرض نمایشگر تنظیم می‌شود؛ برای اعمال کامل، برنامه را دوباره باز کن."
            if setting == "auto" else "تراکم جدید بعد از بستن و بازکردن برنامه اعمال می‌شود."
        )

    def save_auto_preferences(self):
        try:
            interval = int(self.auto_interval.text or 120)
            risk_pct = float(self.auto_risk.text or 0.30)
            slots = int(self.auto_slots.text or 2)
            score = int(self.auto_score.text or 70)
            if not 60 <= interval <= 300:
                raise ValueError("فاصلهٔ اسکن باید بین ۶۰ تا ۳۰۰ ثانیه باشد")
            if not math.isfinite(risk_pct) or not 0.10 <= risk_pct <= 0.50:
                raise ValueError("بودجهٔ ریسک خودکار باید بین ۰٫۱۰ تا ۰٫۵۰ درصد باشد")
            if not 1 <= slots <= 4:
                raise ValueError("حداکثر پوزیشن خودکار باید بین ۱ تا ۴ باشد")
            if not 60 <= score <= 90:
                raise ValueError("حداقل هم‌گرایی باید بین ۶۰ تا ۹۰ باشد")
            db.set_setting("auto_scan_interval", str(interval))
            db.set_setting("auto_risk_pct", f"{risk_pct:.2f}")
            db.set_setting("auto_max_positions", str(slots))
            db.set_setting("auto_min_confluence", str(score))
            self.auto_pref_msg.text = (
                f"ذخیره شد · اسکن هر {interval} ثانیه · ریسک {risk_pct:.2f}% · "
                f"حداکثر {slots} پوزیشن · حداقل هم‌گرایی {score}"
            )
            self.auto_pref_msg.color = GREEN
        except Exception as exc:
            self.auto_pref_msg.text = str(exc)[:140]
            self.auto_pref_msg.color = RED

    def save_preferences(self):
        try:
            lev = float(self.pref_lev.text or 0)
            margin = float(self.pref_margin.text or 0)
            risk_pct = float(self.pref_risk.text or 0)
            if not all(math.isfinite(v) for v in (lev, margin, risk_pct)):
                raise ValueError("مقادیر باید عدد معتبر باشند")
            if not 1 <= lev <= db.MAX_LEVERAGE:
                raise ValueError(f"اهرم باید بین ۱ تا {db.MAX_LEVERAGE:g} باشد")
            if margin <= 0:
                raise ValueError("مارجین پیش‌فرض باید بزرگ‌تر از صفر باشد")
            if not 0.1 <= risk_pct <= 3:
                raise ValueError("ریسک پیش‌فرض باید بین ۰٫۱ تا ۳ درصد باشد")
            refresh_seconds = int(self.pref_refresh.text or 8)
            if not 5 <= refresh_seconds <= 60:
                raise ValueError("فاصلهٔ تازه‌سازی باید بین ۵ تا ۶۰ ثانیه باشد")
            symbol = mapi.normalize_symbol(self.pref_symbol.text.strip() or "BTC")[0]
            timeframe = self.pref_tf.text if self.pref_tf.text in ("15min", "1hour", "4hour", "1day") else "1hour"
            accent_key = {"آبی آسمانی":"sky", "فیروزه‌ای":"teal", "بنفش":"violet"}.get(self.pref_accent.text, "sky")
            db.set_setting("default_leverage", str(lev))
            db.set_setting("default_margin", str(margin))
            db.set_setting("default_risk_pct", str(risk_pct))
            db.set_setting("default_symbol", symbol)
            db.set_setting("refresh_interval", str(refresh_seconds))
            db.set_setting("default_timeframe", timeframe)
            db.set_setting("accent_theme", accent_key)
            app = App.get_running_app()
            app.apply_preferences(symbol=symbol, timeframe=timeframe, refresh_seconds=refresh_seconds)
            self.pref_msg.text = "تنظیمات ذخیره شد؛ نماد، تایم‌فریم و فاصلهٔ تازه‌سازی همین حالا اعمال شدند. رنگ‌بندی پس از راه‌اندازی دوباره اعمال می‌شود."
            self.pref_msg.color = GREEN
        except Exception as exc:
            self.pref_msg.text = str(exc)[:120]
            self.pref_msg.color = RED

    def add_alert(self):
        try:
            symbol = self.alert_sym.text.strip() or "BTC"
            condition = "above" if self.alert_cond.text == "بالاتر از" else "below"
            alert_id = db.alert_add(symbol, condition, float(self.alert_price.text or 0))
            self.alert_price.text = ""
            self.alert_msg.text = f"هشدار #{alert_id} ثبت شد"
            self.reload_alerts()
        except Exception as exc:
            self.alert_msg.text = str(exc)[:120]

    def reload_alerts(self):
        self.alert_box.clear_widgets()
        alerts = db.alerts_open(12)
        for alert in alerts:
            label = "بالاتر از" if alert["condition"] == "above" else "پایین‌تر از"
            row = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(5))
            row.add_widget(M(f"#{alert['id']} · {alert['symbol']} {label} {pxfmt(alert['price'])}"))
            cancel = RoundButton(text="لغو", bg=RED, fg=(1, 1, 1, 1),
                                 size_hint_x=None, width=dp(52), font_size=dp(10))
            cancel.bind(on_release=lambda _btn, aid=alert["id"]: self.cancel_alert(aid))
            row.add_widget(cancel)
            self.alert_box.add_widget(row)
        if not alerts:
            self.alert_box.add_widget(M("هشدار بازی وجود ندارد"))

    def cancel_alert(self, alert_id):
        db.alert_cancel(alert_id)
        self.reload_alerts()

    def add_smart_alert(self):
        try:
            symbol = self.smart_sym.text.strip() or "BTC"
            condition = "above" if self.smart_cond.text == "بالاتر از" else "below"
            threshold = float(self.smart_pct.text or 0)
            alert_id = db.smart_alert_add(symbol, condition, threshold)
            self.smart_alert_msg.text = f"هشدار درصدی #{alert_id} ثبت شد"
            self.smart_pct.text = ""
            self.reload_smart_alerts()
        except Exception as exc:
            self.smart_alert_msg.text = str(exc)[:120]

    def reload_smart_alerts(self):
        self.smart_alert_box.clear_widgets()
        rows = db.smart_alerts_open(20)
        for alert in rows:
            direction = "بالاتر از" if alert["condition"] == "above" else "پایین‌تر از"
            row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(5))
            row.add_widget(M(f"#{alert['id']} · {alert['symbol']} تغییر {direction} {alert['threshold']:+.2f}%"))
            cancel = RoundButton(text="لغو", bg=RED, fg=TEXT, size_hint_x=None, width=dp(60), font_size=dp(10))
            cancel.bind(on_release=lambda _b, aid=alert["id"]: self.cancel_smart_alert(aid))
            row.add_widget(cancel)
            self.smart_alert_box.add_widget(row)
        if not rows:
            self.smart_alert_box.add_widget(M("هشدار درصدی بازی وجود ندارد"))

    def cancel_smart_alert(self, alert_id):
        db.smart_alert_cancel(alert_id)
        self.reload_smart_alerts()

    def run_scenario(self):
        try:
            shock = float(self.scenario_pct.text or 0)
            if not -50 <= shock <= 50:
                raise ValueError("تغییر فرضی باید بین منفی ۵۰ تا مثبت ۵۰ درصد باشد")
            app = App.get_running_app()
            now = time.time()
            marks = {k: v for k, v in app.price_cache.items()
                     if not k.endswith("-USDT") and app.price_cache_ts.get(k, 0) >= now - 30}
            positions = db.list_open_positions()
            if not positions:
                self.scenario_result.text = "پوزیشن باز دمو وجود ندارد."
                return
            rows = portfolio_analytics.scenario_table(positions, marks, (shock,))
            if not rows:
                self.scenario_result.text = "قیمت تازه برای پوزیشن‌ها موجود نیست؛ سناریو محاسبه نشد."
                return
            value = rows[0]["unrealized_pnl"]
            self.scenario_result.text = (f"اگر قیمت همهٔ دارایی‌های قیمت‌گذاری‌شده {shock:+.2f}% تغییر کند، "
                                         f"سود/زیان باز تقریبی {value:+.2f} USDT دمو می‌شود. "
                                         f"این سناریوی فرضی است و قیمت آینده را پیش‌بینی نمی‌کند.")
        except Exception as exc:
            self.scenario_result.text = str(exc)[:120]

    def reload_notifications(self):
        self.notifications_box.clear_widgets()
        rows = db.notifications_list(18)
        if not rows:
            self.notifications_box.add_widget(M("اعلان جدیدی ثبت نشده است"))
            return
        for item in rows:
            unread = item.get("read_ts") is None
            row = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(58), padding=[dp(9), dp(6)], spacing=dp(2))
            with row.canvas.before:
                Color(*(BLUE_GLOW if unread else CARD))
                rect = RoundedRectangle(radius=[dp(11)])
            row.bind(pos=lambda w,*_,r=rect: setattr(r, "pos", w.pos),
                     size=lambda w,*_,r=rect: setattr(r, "size", w.size))
            row.add_widget(Label(text=("● " if unread else "○ ") + item["title"], color=TEXT, bold=True,
                                 size_hint_y=None, height=dp(22), halign="right"))
            row.add_widget(Label(text=item["body"], color=MUTED, font_size=dp(10), halign="right"))
            if unread:
                row.bind(on_touch_down=lambda w, touch, nid=item["id"]: (db.notifications_mark_read(nid), self.reload_notifications()) if w.collide_point(*touch.pos) else None)
            self.notifications_box.add_widget(row)

    def mark_all_notifications_read(self):
        db.notifications_mark_read()
        self.reload_notifications()

    def save(self):
        if not (self.title_in.text.strip() or self.body_in.text.strip()):
            self.health_lbl.text = "عنوان یا متن یادداشت را وارد کن"
            return
        db.add_note(self.title_in.text or "یادداشت", self.body_in.text or "",
                    App.get_running_app().current_symbol or "")
        self.title_in.text = ""
        self.body_in.text = ""
        self.reload_notes()

    def reload_notes(self):
        self.box.clear_widgets()
        for note in db.list_notes(30):
            self.box.add_widget(Label(
                text=f"{note.get('title')}: {(note.get('body') or '')[:70]}",
                color=TEXT, size_hint_y=None, height=dp(32), font_size=dp(12),
            ))

    def reload(self):
        self.reload_notes()
        self.reload_alerts()
        self.reload_smart_alerts()
        self.reload_notifications()

    def health(self):
        self.health_lbl.text = "در حال بررسی منابع عمومی…"
        def work():
            try:
                rows = mapi.health_check()
                txt = "\n".join(f"{'✓' if r['ok'] else '✗'} {r['name']} {r['ms']}ms" for r in rows)
                Clock.schedule_once(lambda *_: setattr(self.health_lbl, "text", txt))
            except Exception as exc:
                message = str(exc)[:140]
                Clock.schedule_once(lambda *_: setattr(self.health_lbl, "text", message))
        _run_background(work)


class DashboardScreen(Screen):
    """Institutional-style home terminal with source-aware quotes and AI supervision."""
    WATCH = ("BTC", "ETH", "SOL", "XRP")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._refreshing = False
        self._last_refresh = 0.0
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3))
        content = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                            spacing=dp(10), size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        title_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        title_box = BoxLayout(orientation="vertical", spacing=dp(1))
        title_box.add_widget(H("مرکز هوشمندی KALVO", 21, TEXT))
        title_box.add_widget(M("بازار را دقیق‌تر ببین؛ تصمیم را آگاهانه‌تر بررسی کن."))
        refresh_btn = RoundButton(text="↻", bg=BLUE_GLOW, fg=GOLD,
                                  size_hint_x=None, width=dp(48), font_size=dp(20))
        refresh_btn.bind(on_release=lambda *_: self.refresh(force=True))
        title_row.add_widget(title_box)
        title_row.add_widget(refresh_btn)
        content.add_widget(title_row)

        status = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)],
                           spacing=dp(4), size_hint_y=None, height=dp(76))
        with status.canvas.before:
            Color(*CARD)
            status._bg = RoundedRectangle(radius=[dp(15)])
            Color(*LINE)
            status._line = Line(rounded_rectangle=(status.x, status.y, status.width, status.height, dp(15)), width=0.9)
        status.bind(pos=lambda *_: (setattr(status._bg, "pos", status.pos),
                                    setattr(status._line, "rounded_rectangle", (status.x, status.y, status.width, status.height, dp(15)))),
                    size=lambda *_: (setattr(status._bg, "size", status.size),
                                     setattr(status._line, "rounded_rectangle", (status.x, status.y, status.width, status.height, dp(15)))))
        status.add_widget(H("KALVO INTELLIGENCE  /  ACTIVE SUPERVISION", 13, GREEN))
        self.ai_status = M("مدیر موقعیت در زمان اجرای برنامه فعال است · دفترکل شبیه‌سازی")
        status.add_widget(self.ai_status)
        content.add_widget(status)

        content.add_widget(H("نمای کلی حساب", 15, GOLD))
        metrics = GridLayout(cols=int(UI_SCREEN_PROFILE.get("metric_columns", 2)), spacing=dp(8), size_hint_y=None, height=dp(188))
        self.metrics_grid = metrics
        self.metric_equity = MetricCard("ارزش حساب شبیه‌سازی", "—", accent=TEXT, height=dp(88))
        self.metric_cash = MetricCard("موجودی آزاد", "—", accent=GREEN, height=dp(88))
        self.metric_positions = MetricCard("موقعیت‌های باز", "—", accent=GOLD, height=dp(88))
        self.metric_ai = MetricCard("وضعیت مدیر هوشمند", "فعال", accent=GREEN, height=dp(88))
        for card in (self.metric_equity, self.metric_cash, self.metric_positions, self.metric_ai):
            metrics.add_widget(card)
        content.add_widget(metrics)

        content.add_widget(H("دسترسی سریع", 15, GOLD))
        shortcuts = GridLayout(cols=int(UI_SCREEN_PROFILE.get("shortcut_columns", 3)), spacing=dp(7), size_hint_y=None, height=dp(104))
        self.shortcuts_grid = shortcuts
        shortcut_specs = (
            ("بازارها", "market", "◈"), ("معاملات", "trade", "⇄"), ("اسپات", "spot", "◇"),
            ("هوش مصنوعی", "ai", "✦"), ("دارایی‌ها", "wallet", "▣"), ("بیشتر", "more", "•••"),
        )
        for label, screen, glyph in shortcut_specs:
            button = RoundButton(text=f"{glyph}\n{label}", bg=CARD, fg=TEXT,
                                 font_size=dp(11), height=dp(48))
            button.bind(on_release=lambda _b, target=screen: App.get_running_app().navigate_to(target))
            shortcuts.add_widget(button)
        content.add_widget(shortcuts)

        content.add_widget(H("بازارهای منتخب", 15, GOLD))
        self.market_box = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.market_box.bind(minimum_height=self.market_box.setter("height"))
        self.market_box.height = dp(150)
        content.add_widget(self.market_box)

        content.add_widget(H("موقعیت‌های فعال", 15, GOLD))
        self.positions_box = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.positions_box.bind(minimum_height=self.positions_box.setter("height"))
        content.add_widget(self.positions_box)

        content.add_widget(H("آخرین خبرهای دریافت‌شده", 15, GOLD))
        self.news_box = GridLayout(cols=1, spacing=dp(5), size_hint_y=None)
        self.news_box.bind(minimum_height=self.news_box.setter("height"))
        content.add_widget(self.news_box)
        self.refresh_status = M("در انتظار دریافت دادهٔ تازه…")
        content.add_widget(self.refresh_status)
        scroll.add_widget(content)
        self.add_widget(scroll)

    def on_enter(self, *args):
        # Let Android/Kivy render the first real frame before launching network workers.
        try:
            Clock.schedule_once(lambda *_: self.refresh(force=False), 2.5)
        except Exception as exc:
            runtime.log_exception("schedule initial dashboard refresh", exc)

    def refresh(self, force=False):
        if self._refreshing:
            return
        if not force and time.time() - self._last_refresh < 12:
            return
        self._refreshing = True
        self.refresh_status.text = "در حال دریافت مظنه‌ها و وضعیت حساب…"

        def _load():
            try:
                quotes = market_data_hub.get_quotes_batch(self.WATCH, max_workers=4)
                positions = db.list_open_positions()
                wallet = db.get_wallet()
                marks = market_data_hub.prices_for_ledger(quotes)
                try:
                    equity = db.equity_with_marks(marks)
                except Exception:
                    equity = float(wallet.get("balance") or 0)
                try:
                    news = news_intelligence.fetch_news(force_refresh=False)
                except Exception as exc:
                    news = {"items": [], "errors": [str(exc)[:100]]}
                data = {"quotes": quotes, "positions": positions, "wallet": wallet,
                        "marks": marks, "equity": equity, "news": news,
                        "generated_at": time.time()}
                Clock.schedule_once(lambda *_: self._apply(data))
            except Exception as exc:
                message = str(exc)[:140]
                Clock.schedule_once(lambda *_: self._failed(message))
        try:
            _run_background(_load)
        except Exception as exc:
            self._failed(str(exc)[:140])

    def _failed(self, message):
        self._refreshing = False
        self.refresh_status.text = f"به‌روزرسانی انجام نشد: {message}"

    def _apply(self, data):
        self._refreshing = False
        self._last_refresh = float(data.get("generated_at") or time.time())
        wallet = data.get("wallet") or {}
        positions = data.get("positions") or []
        manager_on = db.get_setting("ai_position_manager_enabled", "1") == "1"
        news_on = db.get_setting("news_trade_enabled", "0") == "1"
        self.metric_equity.set_value(f"{money(data.get('equity'))} USDT")
        self.metric_cash.set_value(f"{money(wallet.get('balance'))} USDT")
        self.metric_positions.set_value(str(len(positions)))
        self.metric_ai.set_value("فعال" if manager_on else "خاموش", GREEN if manager_on else MUTED)
        self.ai_status.text = ("مدیر موقعیت روشن است" if manager_on else "مدیر موقعیت خاموش است") + \
            (" · فیلتر خبر روشن" if news_on else " · فیلتر خبر خاموش") + " · فقط شبیه‌سازی"

        self.market_box.clear_widgets()
        quotes = data.get("quotes") or {}
        for symbol in self.WATCH:
            quote = quotes.get(symbol) or {}
            row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(7), padding=[dp(8), dp(3)])
            with row.canvas.before:
                Color(*CARD)
                row._bg = RoundedRectangle(radius=[dp(9)])
            row.bind(pos=lambda w, *_: setattr(w._bg, "pos", w.pos),
                     size=lambda w, *_: setattr(w._bg, "size", w.size))
            name = Label(text=symbol, color=TEXT, bold=True, font_size=dp(12), size_hint_x=0.20)
            price = quote.get("price")
            if price and quote.get("quote_valid"):
                price_text = f"{pxfmt(price)} USDT"
            elif price and quote.get("retrieval_valid"):
                price_text = f"~{pxfmt(price)} · زمان منبع نامشخص"
            else:
                price_text = "قیمت در دسترس نیست"
            price_lbl = Label(text=price_text, color=GREEN if quote.get("quote_valid") else MUTED,
                              font_size=dp(11), halign="right")
            source = Label(text=str(quote.get("source") or "—"), color=MUTED,
                           font_size=dp(9), size_hint_x=0.18)
            row.add_widget(name); row.add_widget(price_lbl); row.add_widget(source)
            self.market_box.add_widget(row)
        if not quotes:
            self.market_box.add_widget(M("دادهٔ بازار در دسترس نیست"))

        self.positions_box.clear_widgets()
        for position in positions[:6]:
            note = str(position.get("note") or "")
            origin = str(position.get("source") or "").lower()
            source = "AI" if "decision_owner=AI" in note else "AUTO" if origin == "auto" else "MANUAL"
            label = (f"{position.get('symbol')} · {position.get('side')} · "
                     f"{source} · ورود {pxfmt(position.get('entry'))}")
            self.positions_box.add_widget(M(label))
        if not positions:
            self.positions_box.add_widget(M("موقعیت بازی وجود ندارد"))

        self.news_box.clear_widgets()
        news_items = (data.get("news") or {}).get("items") or []
        for item in news_items[:4]:
            label = f"{item.get('source')} · {item.get('title')}"
            self.news_box.add_widget(M(label[:170]))
        if not news_items:
            self.news_box.add_widget(M("خبر تازه‌ای از فیدهای انتخاب‌شده دریافت نشد؛ این به معنی نبود رویداد بازار نیست."))
        self.refresh_status.text = (
            f"آخرین همگام‌سازی {datetime.fromtimestamp(self._last_refresh).strftime('%H:%M:%S')} · "
            f"{len(data.get('marks') or {})} مظنهٔ تأییدشده · دفترکل شبیه‌سازی"
        )


class Nav(BoxLayout):
    """Premium persistent bottom navigation with a clear active destination."""
    def __init__(self, sm, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(76),
                         padding=[dp(8), dp(7)], spacing=dp(5), **kwargs)
        with self.canvas.before:
            Color(*CARD)
            self._r = RoundedRectangle(radius=[dp(18)])
            Color(*LINE)
            self._border = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(18)), width=0.85)
        self.bind(pos=self._sync_nav, size=self._sync_nav)
        self.sm = sm
        self.btns = {}
        items = (("home", "⌂\nخانه"), ("market", "◈\nبازار"), ("trade", "⇄\nفیوچرز"),
                 ("spot", "◇\nاسپات"), ("wallet", "▣\nدارایی"), ("ai", "✦\nهوش"))
        for name, title in items:
            button = RoundButton(text=title, bg=CARD, fg=MUTED, font_size=dp(float(UI_SCREEN_PROFILE.get("nav_font_dp", 9.5))), height=dp(60), padding=[dp(2), dp(4)])
            button.bind(on_release=lambda _btn, key=name: self.go(key))
            self.btns[name] = button
            self.add_widget(button)
        self.go("home")

    def _sync_nav(self, *_):
        self._r.pos, self._r.size = self.pos, self.size
        self._border.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(18))

    def go(self, name):
        app = App.get_running_app()
        if app is not None:
            app.navigate_to(name)
        else:
            self.sm.current = name
        for key, button in self.btns.items():
            active = key == name
            button.background_color = BLUE_GLOW if active else CARD
            button.color = GOLD if active else MUTED


class KALVOApp(App):
    current_symbol = "BTC"
    current_price = None
    price_cache = {}

    def set_symbol(self, symbol):
        """Keep selected market synchronized across radar, chart, analysis and trading."""
        base = canonical_market_symbol(symbol)
        if base in instruments.REFERENCE_SYMBOLS:
            raise ValueError("نماد مرجع تبدیل ارز برای معامله انتخاب نمی‌شود")
        if base != getattr(self, "current_symbol", None):
            # Do not carry the previous instrument's quote into the new symbol header.
            self.current_price = None
            self.current_price_ts = 0.0
            self.current_ticker = None
        self.current_symbol = base
        if getattr(self, "sm", None) is not None:
            for screen_name, field_name in (("trade", "sym"), ("market", "coin"),
                                            ("chart", "sym"), ("ai", "sym"), ("spot", "sym")):
                try:
                    field = getattr(self.sm.get_screen(screen_name), field_name)
                    field.text = base
                except Exception:
                    continue
        self.refresh_header()
        return base

    def start_background_monitor(self):
        """Start an optional Android foreground service for paper-only monitoring."""
        try:
            from kivy.utils import platform
            if platform != "android":
                return False, "سرویس پس‌زمینه فقط در APK اندروید قابل اجراست."
            from jnius import autoclass
            ServiceClass = autoclass("org.kalvo.lab.kalvomax.ServiceKalvomonitor")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            platform_notifications.request_permission()
            argument = json.dumps({
                "data_dir": self.user_data_dir,
                "interval_seconds": 60,
                "mode": "paper_only",
            }, ensure_ascii=False)
            ServiceClass.start(
                activity, "ic_launcher", "KALVO · نظارت تمرینی",
                "پایش موقعیت‌های شبیه‌سازی‌شده؛ بدون ارسال سفارش زنده", argument,
            )
            return True, "درخواست شروع سرویس ارسال شد؛ وضعیت آن به محدودیت‌های Android و باتری وابسته است."
        except Exception as exc:
            runtime.log_exception("start KALVO background monitor", exc)
            return False, f"شروع سرویس ممکن نشد: {str(exc)[:100]}"

    def stop_background_monitor(self):
        """Request service stop; its persisted off-switch is the reliable fallback."""
        try:
            db.set_setting("background_monitor_enabled", "0")
            from kivy.utils import platform
            if platform == "android":
                from jnius import autoclass
                ServiceClass = autoclass("org.kalvo.lab.kalvomax.ServiceKalvomonitor")
                PythonActivity = autoclass("org.kivy.android.PythonActivity")
                Intent = autoclass("android.content.Intent")
                activity = PythonActivity.mActivity
                intent = Intent(activity, ServiceClass)
                activity.stopService(intent)
            return True, "درخواست توقف ثبت شد؛ در صورت فعال‌بودن سرویس، حداکثر پس از چرخهٔ بررسی بعدی خارج می‌شود."
        except Exception as exc:
            runtime.log_exception("stop KALVO background monitor", exc)
            return True, "کلید توقف ذخیره شد؛ سرویس در بررسی بعدی باید متوقف شود."

    def schedule_optional_sounds(self):
        """Load optional audio only after the first UI frame and only when enabled."""
        if getattr(self, "_sound_load_scheduled", False):
            return
        self._sound_load_scheduled = True
        Clock.schedule_once(self._load_optional_sounds, 1.25)

    def _load_optional_sounds(self, *_args):
        self._sound_load_scheduled = False
        try:
            if db.get_setting("ui_sounds_enabled", "0") != "1":
                return
            ui_feedback.load_sounds(os.path.join(os.path.dirname(__file__), "assets"))
        except Exception as exc:
            runtime.log_exception("deferred UI sound initialization", exc)

    def navigate_to(self, name):
        """Create a screen on demand so startup does not build every page at once."""
        if not getattr(self, "sm", None):
            raise RuntimeError("صفحه‌های KALVO هنوز آماده نیستند")
        name = str(name)
        if name not in self.sm.screen_names:
            factory = getattr(self, "_screen_factories", {}).get(name)
            if factory is None:
                raise KeyError(f"Unknown KALVO screen: {name}")
            try:
                screen = factory(name=name)
            except Exception as exc:
                runtime.log_exception(f"construct KALVO screen: {name}", exc)
                screen = Screen(name=name)
                message = f"بارگذاری این بخش ناموفق بود: {name}\n{type(exc).__name__}: {exc}"
                screen.add_widget(_KivyLabel(text=message, color=TEXT, halign="center", valign="middle"))
            self.sm.add_widget(screen)
        self.sm.current = name
        return self.sm.get_screen(name)

    def _startup_failure_view(self, exc):
        """Keep the process alive and expose Python build exceptions to the user."""
        try:
            os.makedirs(os.path.join(self.user_data_dir, "logs"), exist_ok=True)
            report = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            with open(os.path.join(self.user_data_dir, "logs", "startup-crash.log"), "a", encoding="utf-8") as fh:
                fh.write("\n\n===== KALVO BUILD FAILURE =====\n")
                fh.write(report)
        except Exception:
            report = f"{type(exc).__name__}: {exc}"
        root = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        root.add_widget(_KivyLabel(text="راه‌اندازی KALVO کامل نشد", color=TEXT,
                                   bold=True, size_hint_y=None, height=dp(48)))
        details = TextInput(text=report, readonly=True, multiline=True,
                            size_hint_y=1, foreground_color=TEXT,
                            background_color=(0.035, 0.045, 0.060, 1))
        root.add_widget(details)
        root.add_widget(_KivyLabel(
            text="گزارش خطا در پوشهٔ خصوصی برنامه، logs/startup-crash.log ذخیره شد.",
            color=MUTED, size_hint_y=None, height=dp(48)))
        return root

    def on_start(self):
        global _BOOT_PHASE
        _BOOT_PHASE = "app-on-start"
        _boot_trace("Kivy App on_start entered")
        runtime.install_exception_hooks()
        self._manager_busy = False
        interval = max(10, min(30, int(getattr(self, "refresh_interval", 8) or 8) * 2))
        self._manager_event = Clock.schedule_interval(self._supervise_positions, interval)
        Clock.schedule_once(lambda *_: self._supervise_positions(), 8.0)
        _BOOT_PHASE = "app-on-start-defaults"
        _boot_trace("initialize non-secret AI defaults")
        try:
            from apex.defaults import DEFAULT_AI_MODEL, AI_GATE_DEFAULT
            if not (db.get_setting("coarena_model", "") or "").strip():
                db.set_setting("coarena_model", DEFAULT_AI_MODEL)
            if not (db.get_setting("auto_ai_gate", "") or "").strip():
                db.set_setting("auto_ai_gate", AI_GATE_DEFAULT)
        except Exception as exc:
            runtime.log_exception("non-secret AI defaults initialization", exc)

        # Initialize non-secret defaults only. Provider credentials must be supplied
        # explicitly by the user and are never seeded from source-code constants.
        _BOOT_PHASE = "app-on-start-migration"
        _boot_trace("run settings migrations")
        try:
            from apex.app_migrations import migrate_v7_ai_gate
            migrate_v7_ai_gate(db.get_setting, db.set_setting)
        except Exception as exc:
            runtime.log_exception("KALVO settings migration", exc)

        try:
            from apex.defaults import (DEFAULT_AI_MODEL, DEFAULT_AI_MODE,
                                       AI_GATE_DEFAULT, AI_SHARE_ACCOUNT_DEFAULT)
            defaults_to_apply = {
                "coarena_model": DEFAULT_AI_MODEL,
                "ai_mode": DEFAULT_AI_MODE,
                "ai_gate": AI_GATE_DEFAULT,
                "auto_ai_gate": AI_GATE_DEFAULT,
                "ai_share_account": AI_SHARE_ACCOUNT_DEFAULT,
                "ui_animations_enabled": "1",
                "ui_sounds_enabled": "0",
                "system_notifications_enabled": "1",
                "background_monitor_enabled": "0",
                "ui_font_scale": "1.0",
                "ui_density": "auto",
            }
            for key, value in defaults_to_apply.items():
                if db.get_setting(key, "") == "":
                    db.set_setting(key, value)
        except Exception:
            # A settings initialization issue must not prevent the UI from opening.
            pass

        try:
            ui_feedback.configure(
                sounds=db.get_setting("ui_sounds_enabled", "0") == "1",
                animations=db.get_setting("ui_animations_enabled", "1") == "1",
            )
            if db.get_setting("ui_sounds_enabled", "0") == "1":
                self.schedule_optional_sounds()
        except Exception as exc:
            runtime.log_exception("UI feedback initialization", exc)

        _BOOT_PHASE = "app-on-start-background-monitor"
        _boot_trace("check optional background monitor")
        if db.get_setting("background_monitor_enabled", "0") == "1":
            ok, message = self.start_background_monitor()
            if not ok:
                runtime.log_exception("persistent paper monitor not started", RuntimeError(message))
        _BOOT_PHASE = "app-started"
        _boot_trace("Kivy App on_start completed")

    def _supervise_positions(self, *_):
        if getattr(self, "_manager_busy", False):
            return
        self._manager_busy = True
        selected = getattr(self, "current_symbol", "BTC")
        def _work():
            try:
                result = background_monitor.run_cycle(extra_symbols=[selected])
                if result.get("skipped"):
                    return
                if result.get("pending_events") or result.get("risk_events") or result.get("alert_events"):
                    Clock.schedule_once(lambda *_: self._refresh_managed_views())
            except Exception as exc:
                runtime.log_exception("position supervisor", exc)
            finally:
                self._manager_busy = False
        future = _run_background(_work)
        if future is None:
            self._manager_busy = False

    def _refresh_managed_views(self):
        try:
            if self.sm.current == "trade":
                screen = self.sm.get_screen("trade")
                screen.refresh_positions()
                screen.refresh_orders()
            if self.sm.current == "home":
                self.sm.get_screen("home").refresh(force=True)
            self.refresh_wallet()
        except Exception:
            pass

    def on_stop(self):
        event = getattr(self, "_manager_event", None)
        if event is not None:
            event.cancel()
        runtime.shutdown(wait=False)

    def build(self):
        """Guard the Python UI bootstrap and turn Python build failures into a readable screen."""
        global _BOOT_PHASE
        try:
            _BOOT_PHASE = "app-build-enter"
            _boot_trace("KALVOApp.build entered")
            log_dir = os.path.join(self.user_data_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            _set_boot_log_dir(log_dir)
            os.environ["KALVO_LOG_DIR"] = log_dir
            runtime.install_exception_hooks()
            self._boot_phase = "build-ui"
            _BOOT_PHASE = "build-ui"
            _boot_trace("enter _build_ui")
            result = self._build_ui()
            _BOOT_PHASE = "build-ui-complete"
            _boot_trace("_build_ui completed; root widget returned")
            return result
        except BaseException as exc:
            _BOOT_PHASE = f"build-failed:{type(exc).__name__}"
            _boot_trace(f"build failed: {type(exc).__name__}: {exc}")
            try:
                runtime.log_exception(f"fatal KALVO startup phase={getattr(self, '_boot_phase', 'build-entry')}", exc)
            except Exception:
                pass
            try:
                return self._startup_failure_view(exc)
            except BaseException:
                _early_crash_hook(type(exc), exc, exc.__traceback__)
                raise

    def _build_ui(self):
        global _BOOT_PHASE
        _BOOT_PHASE = "build-ui-database"
        _boot_trace("configure private database directory")
        self.title = "KALVO"
        # Initialize/migrate the private database before reading personalized settings.
        # Keep the previous internal storage location to preserve existing demo history.
        db.configure_data_dir(self.user_data_dir)
        _boot_trace("database directory configured")
        db.init_db()
        _BOOT_PHASE = "build-ui-database-ready"
        _boot_trace("database initialized")
        try:
            sounds_enabled = db.get_setting("ui_sounds_enabled", "0") == "1"
            ui_feedback.configure(
                sounds=sounds_enabled,
                animations=db.get_setting("ui_animations_enabled", "1") == "1",
            )
            if sounds_enabled:
                self.schedule_optional_sounds()
        except Exception as exc:
            runtime.log_exception("early UI preference initialization", exc)
        global UI_FONT_SCALE, UI_COMPACT
        try:
            UI_FONT_SCALE = float(db.get_setting("ui_font_scale", "1.0"))
            if UI_FONT_SCALE not in (1.0, 1.08, 1.16):
                UI_FONT_SCALE = 1.0
        except Exception:
            UI_FONT_SCALE = 1.0
        try:
            from kivy.metrics import Metrics
            logical_width = float(Window.width) / max(0.75, float(Metrics.density or 1.0))
        except Exception:
            logical_width = 393.0
        global UI_SCREEN_PROFILE
        UI_SCREEN_PROFILE = responsive.profile_for_width(logical_width)
        density_setting = db.get_setting("ui_density", "auto")
        UI_COMPACT = density_setting == "compact" or (
            density_setting == "auto" and bool(UI_SCREEN_PROFILE.get("compact"))
        )
        # Apply the user's saved sky-blue / teal / violet accent before widgets are built.
        global GOLD, BLUE_GLOW
        accent = db.get_setting("accent_theme", "sky")
        accent_map = {
            "sky": ((0.30, 0.69, 0.98, 1), (0.085, 0.205, 0.345, 1)),
            "teal": ((0.16, 0.82, 0.72, 1), (0.045, 0.30, 0.29, 1)),
            "violet": ((0.64, 0.55, 0.98, 1), (0.26, 0.20, 0.48, 1)),
        }
        GOLD, BLUE_GLOW = accent_map.get(accent, accent_map["sky"])
        self.current_symbol = db.get_setting("default_symbol", "BTC")
        self.price_cache = {}
        self.price_cache_ts = {}
        self.current_price_ts = 0.0
        self.current_ticker = None
        self.refresh_interval = max(5, min(60, int(db.get_setting("refresh_interval", "8"))))
        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=[dp(float(UI_SCREEN_PROFILE.get("horizontal_margin_dp", 12))), dp(8)])
        self._root_container = root
        self.header = AppHeader()
        root.add_widget(self.header)
        sm = ScreenManager(transition=FadeTransition(duration=0.18 if ui_feedback.animations_enabled() else 0))
        self.sm = sm
        self._screen_factories = {
            "home": DashboardScreen,
            "trade": TradeScreen,
            "market": MarketScreen,
            "chart": ChartScreen,
            "spot": SpotScreen,
            "wallet": WalletScreen,
            "ai": AIScreen,
            "more": MoreScreen,
        }
        # The dashboard is the only eagerly-created screen. All other pages are
        # constructed on first navigation to reduce Android startup memory/latency.
        _BOOT_PHASE = "build-dashboard"
        _boot_trace("construct DashboardScreen")
        sm.add_widget(DashboardScreen(name="home"))
        _boot_trace("DashboardScreen constructed")
        root.add_widget(sm)
        _BOOT_PHASE = "build-navigation"
        _boot_trace("construct navigation")
        self.nav = Nav(sm)
        _boot_trace("navigation constructed")
        root.add_widget(self.nav)
        Window.bind(size=self._on_window_resize)
        if UI_FONT_SCALE != 1.0:
            _apply_font_scale(root, UI_FONT_SCALE)
        self.refresh_header()
        _BOOT_PHASE = "build-root-ready"
        _boot_trace("root UI ready")
        return root

    def _on_window_resize(self, _window, size):
        """Reflow the dashboard/navigation for phones, foldables and tablets."""
        try:
            from kivy.metrics import Metrics
            width_dp = float(size[0]) / max(0.75, float(Metrics.density or 1.0))
            profile = responsive.profile_for_width(width_dp)
            global UI_SCREEN_PROFILE, UI_COMPACT
            UI_SCREEN_PROFILE = profile
            density = db.get_setting("ui_density", "auto")
            UI_COMPACT = density == "compact" or (density == "auto" and bool(profile.get("compact")))
            root = getattr(self, "_root_container", None)
            if root is not None:
                root.padding = [dp(float(profile.get("horizontal_margin_dp", 12))), dp(8)]
            screen_manager = getattr(self, "sm", None)
            if screen_manager is not None:
                home = screen_manager.get_screen("home")
                if hasattr(home, "metrics_grid"):
                    home.metrics_grid.cols = int(profile.get("metric_columns", 2))
                if hasattr(home, "shortcuts_grid"):
                    home.shortcuts_grid.cols = int(profile.get("shortcut_columns", 3))
            nav = getattr(self, "nav", None)
            if nav is not None:
                for button in nav.btns.values():
                    button.font_size = dp(float(profile.get("nav_font_dp", 9.5)))
        except Exception as exc:
            runtime.log_exception("responsive layout update", exc)

    def apply_preferences(self, symbol=None, timeframe=None, refresh_seconds=None):
        if symbol:
            self.set_symbol(symbol)
        if timeframe:
            for name in ("chart", "ai"):
                try:
                    getattr(self.sm.get_screen(name), "tf").text = timeframe
                except Exception:
                    pass
        if refresh_seconds is not None:
            self.refresh_interval = max(5, min(60, int(refresh_seconds)))
            for screen_name, callback in (("trade", lambda screen: lambda _dt: screen.tick()),
                                          ("spot", lambda screen: screen._periodic_prices)):
                try:
                    screen = self.sm.get_screen(screen_name)
                    old_event = getattr(screen, "_refresh_event", None)
                    if old_event is not None:
                        old_event.cancel()
                    screen._refresh_event = Clock.schedule_interval(callback(screen), self.refresh_interval)
                except Exception:
                    continue

    def refresh_header(self):
        header = getattr(self, "header", None)
        if header is None:
            return
        ticker = getattr(self, "current_ticker", None)
        if isinstance(ticker, dict) and ticker.get("base") == self.current_symbol:
            header.update_market(self.current_symbol, ticker=ticker)
        else:
            fresh = time.time() - float(getattr(self, "current_price_ts", 0) or 0) <= 30
            header.update_market(self.current_symbol, price=self.current_price, fresh=fresh)

    def refresh_wallet(self):
        try:
            self.sm.get_screen("wallet").refresh()
        except Exception:
            pass


if __name__ == "__main__":
    _BOOT_PHASE = "create-app-and-run"
    _boot_trace("construct KALVOApp and enter run loop")
    try:
        KALVOApp().run()
    except BaseException as exc:
        _BOOT_PHASE = f"run-failed:{type(exc).__name__}"
        _boot_trace(f"App.run raised {type(exc).__name__}: {exc}")
        _early_crash_hook(type(exc), exc, exc.__traceback__)
        raise
