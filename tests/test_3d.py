from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from metriq_visualizer_3d import Matplotlib3DFrameRenderer, compute_trail_state
from metriq_visualizer_core import GeometryResult, analysis_from_table_file, build_geometry
from metriq_visualizer_render import ExportOptions


class ThreeDimensionalRendererTests(unittest.TestCase):
    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "orbit.csv"
        time_values = np.linspace(0.0, 8.0, 320)
        x_values = np.sin(time_values * 1.7) + 0.15 * np.sin(time_values * 5.2)
        y_values = np.cos(time_values * 1.1) + 0.2 * np.cos(time_values * 3.3)
        z_values = np.sin(time_values * 0.63) + 0.1 * np.cos(time_values * 4.4)
        energy = np.abs(np.sin(time_values * 2.0))
        rows = zip(time_values, x_values, y_values, z_values, energy, strict=True)
        path.write_text(
            "time,x,y,z,energy\n"
            + "\n".join(f"{t:.6f},{x:.6f},{y:.6f},{z:.6f},{e:.6f}" for t, x, y, z, e in rows),
            encoding="utf-8",
        )
        analysis = analysis_from_table_file(path)
        geometry = build_geometry(analysis, "x", "y", "z", "time", "energy", max_points=320)
        return temporary, analysis, geometry

    def test_offscreen_scene_is_true_3d_and_camera_changes_projection(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        options = ExportOptions(
            width=640,
            height=480,
            history_mode="Full static",
            autorotate=False,
            elev=18.0,
            azim=25.0,
            show_project_title=False,
        )
        renderer = Matplotlib3DFrameRenderer(analysis, geometry, options, width=640, height=480)
        self.addCleanup(renderer.close)
        first = renderer.render_frame(analysis.duration)
        self.assertEqual(first.shape, (480, 640, 4))
        self.assertGreater(float(np.std(first[:, :, :3])), 5.0)
        self.assertEqual(renderer.scene.ax.name, "3d")

        options.elev = 55.0
        options.azim = -70.0
        renderer.update_options(options)
        second = renderer.render_frame(analysis.duration)
        difference = np.mean(np.abs(first.astype(np.int16) - second.astype(np.int16)))
        self.assertGreater(float(difference), 3.0)

    def test_trail_state_preserves_xyz_depth_and_head_position(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        options = ExportOptions(history_mode="Cumulative reveal", autorotate=False)
        state = compute_trail_state(geometry, 4.0, options)
        self.assertEqual(state.points.ndim, 2)
        self.assertEqual(state.points.shape[1], 3)
        self.assertGreater(float(np.ptp(state.points[:, 2])), 0.25)
        np.testing.assert_allclose(state.head_point, state.points[-1], rtol=1e-6, atol=1e-6)
        self.assertGreater(state.segments.shape[0], 1)
        self.assertEqual(state.segments.shape[1:], (2, 3))

    def test_filtered_temporal_gap_is_not_connected(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        keep = np.r_[0:80, 150:230]
        gap_geometry = GeometryResult(
            x_full=geometry.x_full[keep],
            y_full=geometry.y_full[keep],
            z_full=geometry.z_full[keep],
            color_full=geometry.color_full[keep],
            size_full=geometry.size_full[keep],
            times_full=geometry.times_full[keep],
            rgba_full=geometry.rgba_full[keep],
            source_indices_full=geometry.source_indices_full[keep],
            x_plot=geometry.x_full[keep],
            y_plot=geometry.y_full[keep],
            z_plot=geometry.z_full[keep],
            color_plot=geometry.color_full[keep],
            size_plot=geometry.size_full[keep],
            times_plot=geometry.times_full[keep],
            rgba_plot=geometry.rgba_full[keep],
            source_indices_plot=geometry.source_indices_full[keep],
            formulas=geometry.formulas,
            normalize_mode=geometry.normalize_mode,
            colormap=geometry.colormap,
        )
        options = SimpleNamespace(
            history_mode="Full static",
            point_lifespan=3.0,
            fade_curve=1.0,
            base_alpha=1.0,
            point_size_scale=0.4,
            line_width=1.0,
            connect_lines=True,
            render_mode="Points + line",
            path_curve_mode="Straight",
            curve_detail=1,
        )
        state = compute_trail_state(gap_geometry, analysis.duration, options)
        self.assertEqual(state.segments.shape[0], keep.size - 2)
        gap_left = gap_geometry.source_indices_full[79]
        gap_right = gap_geometry.source_indices_full[80]
        self.assertGreater(int(gap_right - gap_left), 3)
        self.assertFalse(
            np.any(
                np.all(np.isclose(state.segments[:, 0], state.points[79]), axis=1)
                & np.all(np.isclose(state.segments[:, 1], state.points[80]), axis=1)
            )
        )


    def test_full_static_scene_keeps_all_points_but_head_tracks_playback_time(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        options = ExportOptions(history_mode="Full static", autorotate=False)
        current_time = 2.35
        state = compute_trail_state(geometry, current_time, options, maximum_points=1000)
        self.assertEqual(state.visible_idx.size, geometry.times_full.size)
        self.assertLess(state.head_index, geometry.times_full.size - 1)
        self.assertLessEqual(float(geometry.times_full[state.head_index]), current_time + 1e-9)
        if state.head_index + 1 < geometry.times_full.size:
            self.assertGreater(float(geometry.times_full[state.head_index + 1]), current_time)
        np.testing.assert_allclose(state.head_point, np.column_stack((geometry.x_full, geometry.y_full, geometry.z_full))[state.head_index])

    def test_current_point_label_modes_use_the_playback_head_and_source_index(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        keep = np.arange(40, 220, 2, dtype=np.int64)
        filtered = GeometryResult(
            x_full=geometry.x_full[keep],
            y_full=geometry.y_full[keep],
            z_full=geometry.z_full[keep],
            color_full=geometry.color_full[keep],
            size_full=geometry.size_full[keep],
            times_full=geometry.times_full[keep],
            rgba_full=geometry.rgba_full[keep],
            source_indices_full=geometry.source_indices_full[keep],
            x_plot=geometry.x_full[keep],
            y_plot=geometry.y_full[keep],
            z_plot=geometry.z_full[keep],
            color_plot=geometry.color_full[keep],
            size_plot=geometry.size_full[keep],
            times_plot=geometry.times_full[keep],
            rgba_plot=geometry.rgba_full[keep],
            source_indices_plot=geometry.source_indices_full[keep],
            formulas=geometry.formulas,
            normalize_mode=geometry.normalize_mode,
            colormap=geometry.colormap,
        )
        options = ExportOptions(
            width=480,
            height=320,
            history_mode="Full static",
            autorotate=False,
            point_label_mode="Current point",
            point_label_content="Index",
            max_point_labels=12,
        )
        renderer = Matplotlib3DFrameRenderer(analysis, filtered, options, width=480, height=320)
        self.addCleanup(renderer.close)
        current_time = float(filtered.times_full[23])
        renderer.render_frame(current_time)
        self.assertEqual(len(renderer.scene._text_artists), 1)
        expected_source_index = int(filtered.source_indices_full[23])
        self.assertEqual(renderer.scene._text_artists[0].get_text(), f"#{expected_source_index + 1:,}")
        self.assertIn(f"ROW {expected_source_index + 1:,}/", renderer.scene.hud_text.get_text())


    def test_scene_hud_can_be_removed_for_clean_creator_exports(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        options = ExportOptions(
            width=420,
            height=280,
            history_mode="Full static",
            autorotate=False,
            show_scene_hud=False,
        )
        renderer = Matplotlib3DFrameRenderer(analysis, geometry, options, width=420, height=280)
        self.addCleanup(renderer.close)
        renderer.render_frame(2.0)
        self.assertFalse(renderer.scene.hud_text.get_visible())
        self.assertFalse(renderer.scene.help_text.get_visible())

    def test_comet_and_head_flash_restore_temporal_direction_cues(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        frame_step = float(geometry.times_full[121] - geometry.times_full[120])
        options = ExportOptions(
            history_mode="Full static",
            autorotate=False,
            comet_duration=0.6,
            flash_duration=frame_step * 0.5,
        )
        sample_index = 120
        current_time = float(geometry.times_full[sample_index]) + frame_step * 0.05
        state = compute_trail_state(geometry, current_time, options, maximum_points=500)
        self.assertGreater(state.comet_segments.shape[0], 2)
        self.assertEqual(state.comet_segments.shape[1:], (2, 3))
        self.assertEqual(state.comet_rgba.shape[0], state.comet_segments.shape[0])
        self.assertEqual(state.comet_widths.shape[0], state.comet_segments.shape[0])
        self.assertGreater(float(state.comet_rgba[-1, 3]), float(state.comet_rgba[0, 3]))
        self.assertGreater(float(state.comet_widths[-1]), float(state.comet_widths[0]))
        self.assertGreater(float(state.head_flash_rgba[3]), 0.0)
        self.assertGreater(state.head_flash_size, state.head_size)

        decayed = compute_trail_state(
            geometry,
            float(geometry.times_full[sample_index]) + frame_step * 0.9,
            options,
            maximum_points=500,
        )
        self.assertEqual(float(decayed.head_flash_rgba[3]), 0.0)
        self.assertEqual(decayed.head_flash_size, 0.0)

    def test_head_halo_and_flash_scales_materially_change_the_3d_artists(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        options = ExportOptions(
            width=420,
            height=280,
            history_mode="Full static",
            autorotate=False,
            comet_duration=0.45,
            flash_duration=0.25,
            head_size_scale=0.24,
            halo_size_scale=0.45,
            flash_size_scale=0.05,
        )
        renderer = Matplotlib3DFrameRenderer(analysis, geometry, options, width=420, height=280)
        self.addCleanup(renderer.close)
        current_time = float(geometry.times_full[75]) + 0.01
        renderer.render_frame(current_time)
        head_default = float(renderer.scene.head_scatter.get_sizes()[0])
        halo_default = float(renderer.scene.head_halo.get_sizes()[0])
        flash_default = float(renderer.scene.head_flash.get_sizes()[0])
        self.assertTrue(renderer.scene.comet_collection.get_visible())
        self.assertTrue(renderer.scene.head_flash.get_visible())

        options.head_size_scale = 0.48
        options.halo_size_scale = 0.9
        options.flash_size_scale = 0.1
        renderer.update_options(options)
        renderer.render_frame(current_time)
        self.assertAlmostEqual(float(renderer.scene.head_scatter.get_sizes()[0]), head_default * 2.0, delta=0.01)
        self.assertAlmostEqual(float(renderer.scene.head_halo.get_sizes()[0]), halo_default * 2.0, delta=0.01)
        self.assertAlmostEqual(float(renderer.scene.head_flash.get_sizes()[0]), flash_default * 2.0, delta=0.01)

    def test_tube_radius_scales_with_world_geometry(self) -> None:
        temporary, _analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        scaled = GeometryResult(
            x_full=geometry.x_full * 1000.0,
            y_full=geometry.y_full * 1000.0,
            z_full=geometry.z_full * 1000.0,
            color_full=geometry.color_full,
            size_full=geometry.size_full,
            times_full=geometry.times_full,
            rgba_full=geometry.rgba_full,
            source_indices_full=geometry.source_indices_full,
            x_plot=geometry.x_plot * 1000.0,
            y_plot=geometry.y_plot * 1000.0,
            z_plot=geometry.z_plot * 1000.0,
            color_plot=geometry.color_plot,
            size_plot=geometry.size_plot,
            times_plot=geometry.times_plot,
            rgba_plot=geometry.rgba_plot,
            source_indices_plot=geometry.source_indices_plot,
            formulas=geometry.formulas,
            normalize_mode="raw",
            colormap=geometry.colormap,
        )
        options = ExportOptions(
            history_mode="Full static",
            render_mode="Tube",
            tube_radius_scale=1.0,
            tube_sides=8,
            autorotate=False,
        )
        state = compute_trail_state(scaled, float(scaled.times_full[-1]), options, maximum_points=400)
        self.assertGreater(state.tube_faces.shape[0], 10)
        face_extent = np.max(np.ptp(state.tube_faces.reshape(-1, 3), axis=0))
        self.assertGreater(float(face_extent), 100.0)

    def test_scene_theme_changes_the_actual_3d_canvas_background(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        options = ExportOptions(width=360, height=240, show_colorbar=True, autorotate=False)
        renderer = Matplotlib3DFrameRenderer(analysis, geometry, options, width=360, height=240)
        self.addCleanup(renderer.close)

        renderer.scene.set_theme("light", draw=False)
        light = np.asarray(renderer.scene.figure.get_facecolor()[:3])
        light_axis = np.asarray(renderer.scene.ax.get_facecolor()[:3])
        self.assertGreater(float(np.mean(light)), 0.9)
        self.assertGreater(float(np.mean(light_axis)), 0.9)

        renderer.scene.set_theme("dark", draw=False)
        dark = np.asarray(renderer.scene.figure.get_facecolor()[:3])
        dark_axis = np.asarray(renderer.scene.ax.get_facecolor()[:3])
        self.assertLess(float(np.mean(dark)), 0.15)
        self.assertLess(float(np.mean(dark_axis)), 0.15)


if __name__ == "__main__":
    unittest.main()
