# -*- coding: utf-8 -*-
"""Small Kivy-only diagnostic app for isolating Android runtime startup failures."""
from __future__ import annotations

import os
import sys
import time
import traceback


def _write_probe_log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} | {message}\n"
    print("[KALVO-BOOT-PROBE] " + line.rstrip(), flush=True)
    candidates = [os.environ.get("ANDROID_PRIVATE"), os.environ.get("HOME"), os.path.expanduser("~")]
    for base in candidates:
        if not base:
            continue
        try:
            folder = os.path.join(base, "kalvo_boot_probe")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "boot-probe.log"), "a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
            break
        except Exception:
            continue


_write_probe_log("Python entry reached")

try:
    import kivy
    from kivy.app import App
    from kivy.clock import Clock
    from kivy.core.window import Window
    from kivy.graphics import Color, Rectangle
    from kivy.metrics import dp
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.label import Label
except BaseException as exc:
    _write_probe_log("Kivy import failed: " + type(exc).__name__ + ": " + str(exc))
    try:
        with open(os.path.join(os.path.expanduser("~"), "kalvo-boot-probe-import-error.log"), "a", encoding="utf-8") as stream:
            stream.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass
    raise


class BootProbeApp(App):
    def build(self):
        _write_probe_log("App.build entered")
        self.title = "KALVO Boot Probe"
        try:
            Window.clearcolor = (0.035, 0.055, 0.085, 1)
        except Exception:
            pass

        root = BoxLayout(
            orientation="vertical",
            padding=dp(22),
            spacing=dp(14),
        )
        with root.canvas.before:
            Color(0.035, 0.055, 0.085, 1)
            self._background = Rectangle(pos=root.pos, size=root.size)
        root.bind(
            pos=lambda widget, value: setattr(self._background, "pos", value),
            size=lambda widget, value: setattr(self._background, "size", value),
        )

        title = Label(
            text="KALVO · آزمایش راه‌اندازی اندروید",
            font_size=dp(22),
            bold=True,
            color=(0.88, 0.94, 1, 1),
            size_hint_y=None,
            height=dp(62),
        )
        root.add_widget(title)

        details = (
            "اگر این صفحه را می‌بینی، Kivy روی این گوشی اجرا شده است.\n\n"
            f"Python: {sys.version.split()[0]}\n"
            f"Kivy: {kivy.__version__}\n"
            f"Platform: {sys.platform}\n\n"
            "این برنامه مستقل است و موتورهای اصلی KALVO، دیتابیس، شبکه و API را بارگذاری نمی‌کند."
        )
        self.status = Label(
            text=details,
            color=(0.78, 0.86, 0.96, 1),
            font_size=dp(15),
            halign="center",
            valign="middle",
        )
        self.status.bind(size=lambda widget, value: setattr(widget, "text_size", value))
        root.add_widget(self.status)

        button = Button(
            text="اجرای آزمایش گرافیک",
            size_hint_y=None,
            height=dp(56),
            font_size=dp(16),
            background_normal="",
            background_color=(0.12, 0.42, 0.68, 1),
            color=(1, 1, 1, 1),
        )
        button.bind(on_release=self.run_graphics_test)
        root.add_widget(button)

        self.result = Label(
            text="وضعیت: در انتظار آزمایش",
            size_hint_y=None,
            height=dp(42),
            color=(0.45, 0.9, 0.72, 1),
            font_size=dp(14),
        )
        root.add_widget(self.result)
        Clock.schedule_once(self._first_frame, 1.0)
        _write_probe_log("App.build completed; root widget returned")
        return root

    def _first_frame(self, _dt):
        _write_probe_log("First scheduled frame reached")
        self.result.text = "وضعیت: فریم اول با موفقیت اجرا شد"

    def run_graphics_test(self, *_args):
        _write_probe_log("Graphics test button pressed")
        try:
            # Exercise a few Kivy canvas operations without loading app-specific code.
            with self.root.canvas.after:
                Color(0.18, 0.72, 0.58, 0.8)
                Rectangle(pos=(dp(18), dp(18)), size=(dp(16), dp(16)))
            self.result.text = "وضعیت: آزمایش گرافیک موفق بود"
            _write_probe_log("Graphics test completed")
        except Exception as exc:
            self.result.text = "وضعیت: خطای گرافیک — " + type(exc).__name__
            _write_probe_log("Graphics test failed: " + type(exc).__name__ + ": " + str(exc))

    def on_start(self):
        _write_probe_log("App.on_start completed; Kivy event loop is active")

    def on_stop(self):
        _write_probe_log("App.on_stop called")


if __name__ == "__main__":
    _write_probe_log("Constructing BootProbeApp and entering run loop")
    BootProbeApp().run()
