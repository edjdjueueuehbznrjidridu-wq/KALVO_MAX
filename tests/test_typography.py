import sys
import types
import unittest
from unittest.mock import patch
from apex import typography


class TypographyTests(unittest.TestCase):
    def test_non_arabic_text_is_unchanged(self):
        self.assertEqual(typography.shape_rtl_text("KALVO BTC 123"), "KALVO BTC 123")

    def test_missing_optional_shaper_falls_back_safely(self):
        with patch.dict(sys.modules, {"arabic_reshaper": None, "bidi": None, "bidi.algorithm": None}):
            self.assertEqual(typography.shape_rtl_text("\u0645\u062a\u0646 "), "\u0645\u062a\u0646 ")

    def test_shaping_is_applied_per_line_when_dependencies_exist(self):
        reshaper = types.SimpleNamespace(reshape=lambda text: "RESHAPED:" + text)
        algorithm = types.ModuleType("bidi.algorithm")
        algorithm.get_display = lambda text, base_dir=None: "DISPLAY:" + text
        bidi = types.ModuleType("bidi")
        bidi.algorithm = algorithm
        with patch.dict(sys.modules, {"arabic_reshaper": reshaper, "bidi": bidi, "bidi.algorithm": algorithm}):
            result = typography.shape_rtl_text("\u0645\u062a\u0646\nBTC")
        self.assertEqual(result, "DISPLAY:RESHAPED:\u0645\u062a\u0646\nBTC")

    def test_non_android_font_selection_uses_safe_fallback(self):
        self.assertEqual(typography.register_system_font(platform="linux"), "Roboto")


if __name__ == "__main__":
    unittest.main()
