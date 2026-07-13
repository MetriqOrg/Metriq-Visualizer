from __future__ import annotations

import unittest

from PySide6.QtCore import QRectF

from metriq_visualizer_theme import DARK, LIGHT, cut_corner_path, palette_for


class ThemeTests(unittest.TestCase):
    def test_default_and_light_palettes_remain_distinct(self) -> None:
        self.assertEqual(palette_for("dark"), DARK)
        self.assertEqual(palette_for("light"), LIGHT)
        self.assertNotEqual(DARK.background, LIGHT.background)
        self.assertEqual(DARK.accent, "#06a269")

    def test_cut_corner_path_is_closed(self) -> None:
        path = cut_corner_path(QRectF(0, 0, 100, 50), 10)
        self.assertFalse(path.isEmpty())
        self.assertTrue(path.contains(QRectF(20, 10, 40, 20).center()))


if __name__ == "__main__":
    unittest.main()
