from __future__ import annotations

import unittest

from metriq_visualizer_performance import (
    DEFAULT_PERFORMANCE_PROFILE,
    PERFORMANCE_PROFILES,
    AdaptivePreviewController,
    apply_live_limits,
    normalize_profile_name,
    profile_for,
)
from metriq_visualizer_render import ExportOptions


class PerformanceProfileTests(unittest.TestCase):
    def test_profiles_cover_fast_preview_through_full_live(self) -> None:
        self.assertEqual(DEFAULT_PERFORMANCE_PROFILE, "Balanced")
        self.assertEqual(
            list(PERFORMANCE_PROFILES),
            ["Fast preview", "Balanced", "High quality", "Full live simulation"],
        )
        budgets = [profile.point_budget for profile in PERFORMANCE_PROFILES.values()]
        self.assertEqual(budgets, sorted(budgets))
        self.assertEqual(profile_for("render").name, "Full live simulation")
        self.assertEqual(profile_for("Fast preview").pixel_ratio_cap, 1.0)
        self.assertIsNone(profile_for("Full live simulation").pixel_ratio_cap)
        self.assertEqual(normalize_profile_name("draft"), "Fast preview")

    def test_fast_preview_is_live_only_and_simplifies_expensive_artists(self) -> None:
        options = ExportOptions(
            render_mode="Tube",
            path_curve_mode="Smooth spline",
            curve_detail=16,
            tube_sides=20,
            point_label_mode="All visible",
            show_colorbar=True,
            ghost_path=True,
            comet_duration=0.8,
            flash_duration=0.2,
        )
        live, points, fps = apply_live_limits(options, profile_for("Fast preview"))

        self.assertIsNot(live, options)
        self.assertEqual(live.render_mode, "Points + line")
        self.assertEqual(live.path_curve_mode, "Smooth spline")
        self.assertEqual(live.curve_detail, 4)
        self.assertEqual(live.point_label_mode, "Off")
        self.assertFalse(live.show_colorbar)
        self.assertFalse(live.ghost_path)
        self.assertEqual(live.comet_duration, 0.8)
        self.assertEqual(live.flash_duration, 0.2)
        self.assertEqual((points, fps), (650, 15))

        # Creator/export settings are not rewritten by a live profile.
        self.assertEqual(options.render_mode, "Tube")
        self.assertEqual(options.path_curve_mode, "Smooth spline")
        self.assertEqual(options.curve_detail, 16)
        self.assertTrue(options.show_colorbar)

    def test_adaptive_controller_reduces_and_recovers_live_density(self) -> None:
        controller = AdaptivePreviewController()
        self.assertEqual(controller.reset(1200, 12), 1200)

        reduced = 1200
        for _ in range(5):
            reduced = controller.observe(180.0, enabled=True)
        self.assertLess(reduced, 1200)
        self.assertGreaterEqual(reduced, 240)

        recovered = reduced
        for _ in range(48):
            recovered = controller.observe(20.0, enabled=True)
        self.assertGreater(recovered, reduced)
        self.assertLessEqual(recovered, 1200)
        self.assertEqual(controller.observe(20.0, enabled=False), 1200)

    def test_full_live_profile_retains_scene_features(self) -> None:
        options = ExportOptions(
            render_mode="Tube",
            path_curve_mode="Smooth spline",
            curve_detail=20,
            tube_sides=18,
            point_label_mode="Current point",
            show_colorbar=True,
            ghost_path=True,
            comet_duration=0.5,
        )
        live, points, fps = apply_live_limits(options, profile_for("Full live simulation"))
        self.assertEqual(live.render_mode, "Tube")
        self.assertEqual(live.path_curve_mode, "Smooth spline")
        self.assertEqual(live.curve_detail, 20)
        self.assertEqual(live.tube_sides, 18)
        self.assertEqual(live.point_label_mode, "Current point")
        self.assertTrue(live.show_colorbar)
        self.assertTrue(live.ghost_path)
        self.assertEqual(live.comet_duration, 0.5)
        self.assertEqual((points, fps), (6_000, 5))


if __name__ == "__main__":
    unittest.main()
