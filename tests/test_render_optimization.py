from __future__ import annotations

import ast
import os
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from metriq_visualizer_3d import (  # noqa: E402
    Interactive3DViewport,
    _catmull_rom,
    _finite_points,
    _visible_indices,
    compute_trail_state,
)
from metriq_visualizer_core import GeometryResult  # noqa: E402
from metriq_visualizer_realtime import Realtime3DCanvas, media_path_segments  # noqa: E402
from metriq_visualizer_render import ExportOptions  # noqa: E402


def geometry_fixture(count: int = 320) -> GeometryResult:
    t = np.linspace(0.0, 8.0, count)
    rgba = np.column_stack((t / 8, 1 - t / 8, np.full(count, 0.5), np.ones(count)))
    data = dict(x=np.sin(t), y=np.cos(t), z=t, color=t, size=np.ones(count), times=t, rgba=rgba,
                source_indices=np.arange(count))
    return GeometryResult(**{f"{key}_{suffix}": value for suffix in ("full", "plot") for key, value in data.items()},
                          formulas={}, normalize_mode="None", colormap="viridis")


def scalar_spline_reference(points, colors, widths, detail):
    """Independent pre-optimization formula; retain it as a numeric oracle."""
    detail = max(1, int(detail))
    if len(points) < 4 or detail <= 1:
        return points, colors, widths
    detail = max(1, min(detail, 6000 // (len(points) - 1)))
    if detail <= 1:
        return points, colors, widths
    padded = np.vstack((points[0], points, points[-1]))
    xyz, rgba, scale = [], [], []
    for i in range(len(points) - 1):
        p0, p1, p2, p3 = padded[i:i + 4]
        for step in range(detail):
            t = step / detail
            xyz.append(0.5 * (2*p1 + (-p0+p2)*t + (2*p0-5*p1+4*p2-p3)*(t*t)
                              + (-p0+3*p1-3*p2+p3)*(t*t*t)))
            rgba.append((1-t)*colors[i] + t*colors[i+1])
            scale.append((1-t)*widths[i] + t*widths[i+1])
    return np.vstack((xyz, points[-1])), np.vstack((rgba, colors[-1])), np.r_[scale, widths[-1]]


class RenderMathOptimizationTests(unittest.TestCase):
    def test_vectorized_spline_matches_original_formula_and_endpoints(self):
        rng = np.random.default_rng(71)
        for count, detail in ((0, 4), (1, 4), (3, 8), (4, 1), (4, 6), (37, 8), (1200, 24), (6100, 24)):
            with self.subTest(count=count, detail=detail):
                points, colors, widths = rng.normal(size=(count, 3)), rng.random((count, 4)), rng.random(count)
                actual = _catmull_rom(points, colors, widths, detail)
                expected = scalar_spline_reference(points, colors, widths, detail)
                for new, old in zip(actual, expected, strict=True):
                    np.testing.assert_allclose(new, old, rtol=1e-13, atol=1e-13)
                    if count:
                        np.testing.assert_array_equal(new[-1], old[-1])

    def test_visible_sampling_matches_original_without_history_sized_allocation(self):
        times = np.linspace(0, 100, 100_000)
        for history, now, maximum in (("Full static", 42.2, 123), ("Cumulative reveal", 82.1, 1200),
                                     ("Trail fade", 90.0, 100), ("Full static", -1, 100),
                                     ("Full static", 42.2, 1), ("Cumulative reveal", 0.0, 100)):
            stop = int(np.searchsorted(times, now + 1e-9, side="right"))
            head = max(0, min(times.size - 1, stop - 1))
            if stop <= 0:
                expected = np.array([head])
            else:
                start = 0
                if "Full" in history:
                    stop = times.size
                elif "Trail" in history:
                    start = int(np.searchsorted(times, now - 3.0, side="left"))
                expected = np.arange(start, stop)
                if expected.size > maximum:
                    expected = expected[np.unique(np.linspace(0, len(expected) - 1, maximum, dtype=np.int64))]
                if head not in expected:
                    expected[np.argmin(np.abs(expected - head))] = head
                    expected = np.unique(expected)
            original_arange = np.arange

            def bounded_arange(start, stop, *, limit=maximum, original=original_arange, **kwargs):
                self.assertLessEqual(stop - start, limit)
                return original(start, stop, **kwargs)

            with patch("metriq_visualizer_3d.np.arange", side_effect=bounded_arange):
                actual = _visible_indices(times, now, history, 3.0, maximum)
            np.testing.assert_array_equal(actual, expected)

    def test_bounded_paths_remain_connected_and_preserve_endpoints(self):
        t = np.linspace(0, 20, 1200)
        points = np.column_stack((t, np.sin(t), np.cos(t)))
        rgba = np.ones((len(t), 4))
        for smooth in (False, True):
            for budget in (1, 100, 2400):
                with self.subTest(smooth=smooth, budget=budget):
                    segments, colors, widths = media_path_segments(points, rgba, line_width=2, smooth=smooth,
                                                                  detail=8, maximum_segments=budget)
                    self.assertLessEqual(len(segments), budget)
                    self.assertEqual(len(segments), len(colors))
                    self.assertEqual(len(segments), len(widths))
                    np.testing.assert_array_equal(segments[:-1, 1], segments[1:, 0])
                    np.testing.assert_array_equal(segments[0, 0], points[0])
                    np.testing.assert_array_equal(segments[-1, 1], points[-1])

    def test_bounded_paths_preserve_filtered_temporal_gaps(self):
        source = np.r_[0:80, 150:230, 400:450]
        points = np.column_stack((source, np.sin(source), np.cos(source))).astype(float)
        for smooth in (False, True):
            segments, _, _ = media_path_segments(points, np.ones((len(source), 4)), line_width=2,
                                                smooth=smooth, detail=8, source_indices=source,
                                                maximum_segments=25)
            self.assertLessEqual(len(segments), 25)
            for start, end in ((0, 79), (150, 229), (400, 449)):
                within = (segments[:, 0, 0] >= start) & (segments[:, 0, 0] <= end)
                self.assertTrue(within.any())
                self.assertTrue(np.all((segments[within, 1, 0] >= start) & (segments[within, 1, 0] <= end)))
                np.testing.assert_allclose(segments[within][0, 0, 0], start)
                np.testing.assert_allclose(segments[within][-1, 1, 0], end)

    def test_realtime_state_omits_discarded_mesh_but_preserves_temporal_state(self):
        geometry = geometry_fixture(40)
        options = ExportOptions(history_mode="Full static", path_curve_mode="Smooth spline", render_mode="Tube")
        exact = compute_trail_state(geometry, 4.0, options)
        self.assertGreater(exact.tube_faces.size, 0)
        with patch("metriq_visualizer_3d._finite_points", side_effect=AssertionError("duplicate points")), \
             patch("metriq_visualizer_3d._catmull_rom", side_effect=AssertionError("discarded spline")), \
             patch("metriq_visualizer_3d._tube_faces", side_effect=AssertionError("discarded mesh")), \
             patch("metriq_visualizer_3d.np.ptp", side_effect=AssertionError("full-array bounds scan")):
            realtime = compute_trail_state(geometry, 4.0, options, prepared_points=_finite_points(geometry),
                                           include_path_geometry=False)
        omitted = {"segments", "segment_rgba", "segment_widths", "tube_faces", "tube_rgba"}
        for field in fields(exact):
            if field.name not in omitted:
                np.testing.assert_array_equal(getattr(exact, field.name), getattr(realtime, field.name))
        self.assertEqual(realtime.tube_faces.size, 0)
        self.assertEqual(realtime.segments.size, 0)

    def test_wheel_module_list_includes_every_runtime_module(self):
        # Python 3.10 compatibility: no dependency on tomllib in this test.
        root = Path(__file__).resolve().parents[1]
        text = (root / "pyproject.toml").read_text()
        listed = ast.literal_eval(text.split("py-modules =", 1)[1].split("[tool.ruff]", 1)[0].strip())
        actual = {p.stem for p in root.glob("metriq_visualizer_*.py")}
        self.assertTrue(actual.issubset(set(listed)), actual - set(listed))


class RenderWidgetOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_live_buffers_align_after_filtering_and_own_their_data(self):
        canvas = Realtime3DCanvas()
        points = np.arange(360, dtype=float).reshape(-1, 3)
        colors = np.tile([0.2, 0.3, 0.4, 1.0], (120, 1))
        sizes = np.arange(120, dtype=float)
        points[30] = np.nan
        canvas.maximum_points = 100
        canvas.set_live_trajectory(points, colors, sizes)
        self.assertEqual(canvas._live_points.shape, (100, 3))
        expected = np.flatnonzero(np.isfinite(points).all(axis=1))[-100:]
        np.testing.assert_array_equal(canvas._live_sizes, sizes[expected])
        saved = canvas._live_points.copy()
        points[:] = 0
        colors[:] = 0
        sizes[:] = 0
        np.testing.assert_array_equal(canvas._live_points, saved)
        self.assertTrue(np.all(canvas._live_rgba[:, 3] == 1))
        self.assertGreater(canvas._live_sizes[-1], 0)

    def test_live_uniform_attributes_nonfinite_values_and_transactional_failure(self):
        canvas = Realtime3DCanvas()
        points = np.arange(30, dtype=float).reshape(-1, 3)
        canvas.set_live_trajectory(points, np.array([np.nan, 2, -1, 1]), np.array([np.inf]))
        np.testing.assert_array_equal(canvas._live_rgba, np.tile([0, 1, 0, 1], (10, 1)))
        np.testing.assert_array_equal(canvas._live_sizes, np.zeros(10))
        saved = canvas._live_points.copy()
        with self.assertRaises(ValueError):
            canvas.set_live_trajectory(points + 1, np.ones((2, 4)))
        np.testing.assert_array_equal(canvas._live_points, saved)
        with self.assertRaises(ValueError):
            canvas.set_live_trajectory(points + 1, sizes=np.ones(2))
        np.testing.assert_array_equal(canvas._live_points, saved)
        canvas.set_live_trajectory(np.full((10, 3), np.nan), np.ones((10, 4)), np.ones(10))
        self.assertFalse(canvas.live_active)
        self.assertEqual(canvas._live_rgba.shape, (0, 4))
        self.assertEqual(canvas._live_sizes.shape, (0,))

    def test_media_preview_uses_cached_geometry_and_honors_points_only_and_zero_accents(self):
        canvas = Realtime3DCanvas()
        options = ExportOptions(history_mode="Full static", render_mode="Points only", autorotate=False,
                                head_size_scale=0, halo_size_scale=0, flash_size_scale=0)
        canvas.set_scene(None, geometry_fixture(), options)
        canvas.set_time(4.0)
        painter = MagicMock()
        with patch.object(canvas, "_draw_segments") as lines, patch.object(canvas, "_draw_points") as points, \
             patch("metriq_visualizer_3d._finite_points", side_effect=AssertionError("uncached geometry")):
            canvas._draw_media(painter)
        lines.assert_not_called()
        points.assert_called_once()
        painter.drawEllipse.assert_not_called()
        canvas.clear_scene()
        self.assertEqual(canvas._prepared_points.size, 0)
        self.assertEqual(canvas.current_time, 0)

    def test_pause_refinement_uses_latest_playback_clock(self):
        # Exercise the actual handoff method without requiring an audio device.
        geometry = geometry_fixture()
        viewport = Interactive3DViewport()
        self.addCleanup(viewport.close)
        viewport.options = ExportOptions(autorotate=False)
        viewport.geometry = geometry
        viewport.scene = MagicMock()
        viewport.scene.current_time = 0.0
        viewport.set_motion_mode(True)
        viewport.update_time(5.25)
        viewport.scene.update_time.assert_not_called()
        viewport.set_motion_mode(False)
        viewport.scene.update_time.assert_called_once_with(5.25, draw=True)

    def test_stage_output_does_not_grab_disabled_panels(self):
        from metriq_visualizer_app import MainWindow
        from metriq_visualizer_stage_output import StageOutputConfig

        config = StageOutputConfig()
        for name in config.layout.order:
            config.layout.item(name).enabled = name == "geometry"
        widgets = {name: MagicMock() for name in ("source_panel", "spectrogram", "chromagram", "mfcc", "traces")}
        stage = SimpleNamespace(stage_output_config=config, viewport=MagicMock(), analysis_dock=SimpleNamespace(**widgets))
        stage.viewport.stack.currentWidget.return_value.grab.return_value.isNull.return_value = False
        result = MainWindow._stage_output_layers(stage)
        self.assertEqual(set(result), {"geometry"})
        for widget in widgets.values():
            widget.grab.assert_not_called()


if __name__ == "__main__":
    unittest.main()
