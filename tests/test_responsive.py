import unittest
from apex.responsive import profile_for_width


class ResponsiveProfileTests(unittest.TestCase):
    def test_common_android_width_matrix(self):
        cases = {
            320: ("compact", 2, 8),
            360: ("phone", 2, 12),
            393: ("phone", 2, 12),
            432: ("phone", 2, 12),
            600: ("tablet", 3, 20),
            768: ("tablet", 3, 20),
            1024: ("wide", 4, 28),
        }
        for width, expected in cases.items():
            with self.subTest(width_dp=width):
                profile = profile_for_width(width)
                self.assertEqual(
                    (profile["density"], profile["metric_columns"],
                     profile["horizontal_margin_dp"]), expected
                )

    def test_invalid_width_is_safe(self):
        self.assertEqual(profile_for_width(float("nan"))["density"], "phone")
        self.assertEqual(profile_for_width(-1)["width_dp"], 240.0)
        self.assertEqual(profile_for_width(99999)["width_dp"], 2400.0)


if __name__ == "__main__":
    unittest.main()
