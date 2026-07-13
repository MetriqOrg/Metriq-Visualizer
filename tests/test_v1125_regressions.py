from __future__ import annotations

import os
import plistlib
import runpy
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from scipy.io import wavfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

from PySide6.QtWidgets import QApplication, QGroupBox  # noqa: E402

_build_helpers = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'build' / 'build_pyinstaller.py'))
MICROPHONE_USAGE = _build_helpers['MICROPHONE_USAGE']
patch_macos_bundle_metadata = _build_helpers['patch_macos_bundle_metadata']
from metriq_visualizer_3d import (  # noqa: E402
    Interactive3DViewport,
    Matplotlib3DFrameRenderer,
    compute_trail_state,
)
from metriq_visualizer_app import MainWindow  # noqa: E402
from metriq_visualizer_cache import fingerprint_source, load_cached_analysis, save_cached_analysis  # noqa: E402
from metriq_visualizer_core import (  # noqa: E402
    AnalysisSettings,
    analysis_from_table_file,
    analyze_media,
    build_geometry,
)
from metriq_visualizer_live import LiveAudioEngine, LiveInputPanel  # noqa: E402
from metriq_visualizer_realtime import Realtime3DCanvas, advance_azimuth, camera_after_drag  # noqa: E402
from metriq_visualizer_render import ExportOptions  # noqa: E402


class CameraAndMotionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _fixture() -> tuple[tempfile.TemporaryDirectory, object, object]:
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "camera.csv"
        times = np.linspace(0.0, 4.0, 180)
        path.write_text(
            "time,x,y,z,energy\n"
            + "\n".join(
                f"{value:.6f},{np.sin(value * 1.7):.6f},{np.cos(value * 1.2):.6f},"
                f"{np.sin(value * 0.7):.6f},{abs(np.sin(value * 2.3)):.6f}"
                for value in times
            ),
            encoding="utf-8",
        )
        analysis = analysis_from_table_file(path)
        geometry = build_geometry(analysis, "x", "y", "z", "time", "energy", max_points=180)
        return temporary, analysis, geometry

    def test_drag_math_is_bounded_and_uses_logical_pixel_deltas(self) -> None:
        elev, azim = camera_after_drag(24.0, 35.0, 100.0, -50.0)
        self.assertAlmostEqual(elev, 31.0)
        self.assertAlmostEqual(azim, 21.0)
        extreme_elev, extreme_azim = camera_after_drag(0.0, 0.0, 100_000.0, -100_000.0)
        self.assertEqual(extreme_elev, 90.0)
        self.assertGreaterEqual(extreme_azim, -180.0)
        self.assertLess(extreme_azim, 180.0)

        top_down, _ = camera_after_drag(89.0, 0.0, 0.0, -100.0)
        level, _ = camera_after_drag(-89.0, 0.0, 0.0, 100.0)
        self.assertEqual(top_down, 90.0)
        self.assertEqual(level, -90.0)

    def test_realtime_camera_elevation_uses_top_down_at_ninety_degrees(self) -> None:
        top_down = Realtime3DCanvas._rotation_matrix(90.0, 0.0)
        level = Realtime3DCanvas._rotation_matrix(0.0, 0.0)
        np.testing.assert_allclose(top_down, np.eye(3), atol=1e-12)
        self.assertFalse(np.allclose(level, np.eye(3)))

    def test_autorotate_uses_elapsed_wall_clock_even_without_media(self) -> None:
        self.assertAlmostEqual(advance_azimuth(10.0, 20.0, 0.5), 20.0)
        viewport = Interactive3DViewport()
        viewport.resize(640, 360)
        points = np.column_stack(
            (
                np.linspace(-1.0, 1.0, 64),
                np.sin(np.linspace(0.0, 5.0, 64)),
                np.cos(np.linspace(0.0, 5.0, 64)),
            )
        )
        options = ExportOptions(autorotate=True, rotation_speed=120.0)
        viewport.set_live_trajectory(points, options=options)
        viewport.set_motion_frame_interval(67)
        self.assertEqual(viewport.autorotate_timer.interval(), 67)
        start = viewport.camera()[1]
        deadline = time.monotonic() + 0.18
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        end = viewport.camera()[1]
        self.assertGreater(abs(end - start), 3.0)
        viewport.clear_scene()

    def test_realtime_drag_latches_fast_canvas_until_release(self) -> None:
        viewport = Interactive3DViewport()
        viewport.options = ExportOptions(autorotate=False)
        viewport._realtime_interaction_started()
        self.assertTrue(viewport._fast_active())
        viewport._realtime_interaction_finished()
        self.assertFalse(viewport._fast_active())
        viewport.clear_scene()

    def test_zoom_scales_grid_and_data_through_the_same_transform(self) -> None:
        canvas = Realtime3DCanvas()
        canvas.resize(800, 600)
        point = np.asarray([[0.75, 0.25, -0.15]], dtype=np.float64)
        grid_point = np.asarray([[1.0, -1.0, -1.0]], dtype=np.float64)
        canvas.set_camera(24.0, 35.0, 1.0)
        first_data = canvas.project_normalized(point)[0, :2]
        first_grid = canvas.project_normalized(grid_point)[0, :2]
        center = np.asarray([400.0, 306.0])
        canvas.set_camera(24.0, 35.0, 1.8)
        second_data = canvas.project_normalized(point)[0, :2]
        second_grid = canvas.project_normalized(grid_point)[0, :2]
        data_ratio = np.linalg.norm(second_data - center) / np.linalg.norm(first_data - center)
        grid_ratio = np.linalg.norm(second_grid - center) / np.linalg.norm(first_grid - center)
        self.assertAlmostEqual(data_ratio, 1.8, delta=0.03)
        self.assertAlmostEqual(grid_ratio, 1.8, delta=0.03)

    def test_exact_zoom_preserves_limits_and_scales_coordinate_box(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        options = ExportOptions(autorotate=False, zoom=1.0, history_mode="Full static")
        renderer = Matplotlib3DFrameRenderer(analysis, geometry, options, width=420, height=320)
        self.addCleanup(renderer.close)
        limits = (renderer.scene.ax.get_xlim3d(), renderer.scene.ax.get_ylim3d(), renderer.scene.ax.get_zlim3d())
        aspect_one = np.asarray(renderer.scene.ax.get_box_aspect())
        options.zoom = 1.7
        renderer.update_options(options)
        aspect_two = np.asarray(renderer.scene.ax.get_box_aspect())
        self.assertEqual(
            limits, (renderer.scene.ax.get_xlim3d(), renderer.scene.ax.get_ylim3d(), renderer.scene.ax.get_zlim3d())
        )
        np.testing.assert_allclose(aspect_two / aspect_one, 1.7, rtol=1e-5)

        options.show_grid = False
        renderer.update_options(options)
        for axis in (renderer.scene.ax.xaxis, renderer.scene.ax.yaxis, renderer.scene.ax.zaxis):
            self.assertEqual(axis._axinfo["grid"]["linewidth"], 0.0)  # noqa: SLF001
            self.assertEqual(axis.pane.get_facecolor()[3], 0.0)

    def test_realtime_proxy_honors_axis_and_label_visibility(self) -> None:
        canvas = Realtime3DCanvas()
        canvas.resize(640, 360)
        hidden_painter = MagicMock()
        canvas.options = SimpleNamespace(show_axes=False, show_axis_labels=True)
        canvas._draw_grid(hidden_painter)
        hidden_painter.setPen.assert_not_called()
        hidden_painter.drawLine.assert_not_called()

        visible_painter = MagicMock()
        canvas.options = SimpleNamespace(show_axes=True, show_axis_labels=False)
        canvas._draw_grid(visible_painter)
        self.assertTrue(visible_painter.drawLine.called)
        visible_painter.drawText.assert_not_called()

        grid_hidden_painter = MagicMock()
        canvas.options = SimpleNamespace(show_axes=True, show_grid=False, show_axis_labels=False)
        canvas._draw_grid(grid_hidden_painter)
        self.assertEqual(grid_hidden_painter.drawLine.call_count, 3)

    def test_spline_and_motion_accents_change_the_render_state(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        straight = ExportOptions(
            history_mode="Full static",
            autorotate=False,
            path_curve_mode="Straight",
            curve_detail=1,
            comet_duration=0.0,
            flash_duration=0.0,
        )
        smooth = ExportOptions(
            history_mode="Full static",
            autorotate=False,
            path_curve_mode="Smooth spline",
            curve_detail=6,
            comet_duration=0.7,
            flash_duration=0.25,
        )
        straight_state = compute_trail_state(geometry, 2.0, straight, maximum_points=500)
        smooth_state = compute_trail_state(geometry, 2.0, smooth, maximum_points=500)
        self.assertGreater(smooth_state.segments.shape[0], straight_state.segments.shape[0] * 3)
        self.assertGreater(smooth_state.comet_segments.shape[0], 0)
        self.assertGreater(float(smooth_state.head_flash_rgba[3]), 0.0)
        self.assertEqual(straight_state.comet_segments.shape[0], 0)


class AnalysisControlRegressionTests(unittest.TestCase):
    def test_audio_settings_change_real_extraction_and_cache_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "tone.wav"
            sample_rate = 48_000
            times = np.arange(sample_rate, dtype=np.float64) / sample_rate
            samples = (0.3 * np.sin(2.0 * np.pi * 1750.0 * times)).astype(np.float32)
            wavfile.write(source, sample_rate, samples)
            settings = AnalysisSettings(
                sample_rate=22_050,
                n_fft=1024,
                hop_length=128,
                min_frequency=300.0,
                max_frequency=5000.0,
                max_frames=180,
                n_mels=40,
                mfcc_count=12,
            )
            result = analyze_media(source, settings)
            self.assertEqual(result.sample_rate, 22_050)
            self.assertEqual(result.metadata["n_fft"], 1024)
            self.assertEqual(result.metadata["requested_hop_length"], 128)
            self.assertLessEqual(result.times.size, 180)
            self.assertEqual(result.spectrogram.shape[0], 40)
            self.assertEqual(result.mfcc.shape[0], 12)
            self.assertGreaterEqual(float(np.min(result.spectrogram_frequencies)), 300.0)
            self.assertLessEqual(float(np.max(result.spectrogram_frequencies)), 5000.0)

            fingerprint = fingerprint_source(source)
            cache_root = root / "cache"
            save_cached_analysis(result, fingerprint, root=cache_root, settings=settings)
            self.assertIsNotNone(load_cached_analysis(source, fingerprint, root=cache_root, settings=settings))
            changed = AnalysisSettings(**{**settings.to_dict(), "n_fft": 2048})
            self.assertIsNone(load_cached_analysis(source, fingerprint, root=cache_root, settings=changed))

    def test_main_window_exposes_fine_controls_and_persists_extraction(self) -> None:
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        self.assertEqual(window.lifespan_spin.singleStep(), 0.05)
        self.assertEqual(window.elev_spin.singleStep(), 0.5)
        self.assertEqual(window.azim_spin.singleStep(), 0.5)
        self.assertEqual(window.zoom_spin.singleStep(), 0.02)
        self.assertEqual(window.rotation_speed_spin.singleStep(), 0.25)
        self.assertEqual(window.time_label.minimumWidth(), window.time_label.maximumWidth())
        self.assertEqual(window.preview_status.minimumWidth(), window.preview_status.maximumWidth())
        parent = window.colormap_combo.parentWidget()
        self.assertIsInstance(parent, QGroupBox)
        self.assertEqual(parent.title(), "Visual style")
        window.analysis_profile_combo.setCurrentText("Legacy v1.10")
        app.processEvents()
        self.assertEqual(window.sample_rate_combo.currentData(), 22_050)
        self.assertEqual(window.fft_size_combo.currentData(), 2048)
        self.assertEqual(window.hop_length_spin.value(), 256)
        payload = window._capture_state(include_session=False)
        self.assertEqual(payload["extraction"]["sample_rate"], 22_050)
        self.assertEqual(payload["extraction"]["hop_length"], 256)
        window.min_frequency_spin.setValue(1_000.0)
        window.max_frequency_spin.setValue(500.0)
        app.processEvents()
        self.assertEqual(window.max_frequency_spin.value(), 1_010.0)
        window.close()


class LiveInputRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_default_device_route_survives_enumeration_failure(self) -> None:
        streams: list[object] = []

        class FakeStream:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.samplerate = kwargs["samplerate"]
                streams.append(self)

            def start(self):
                return None

            def stop(self):
                return None

            def close(self):
                return None

        fake_sd = SimpleNamespace(
            query_devices=lambda device=None, kind=None: (
                {"default_samplerate": 48_000, "max_input_channels": 1}
                if kind == "input"
                else (_ for _ in ()).throw(RuntimeError("enumeration unavailable"))
            ),
            check_input_settings=lambda **_kwargs: None,
            InputStream=FakeStream,
        )
        with patch("metriq_visualizer_live.sd", fake_sd):
            engine = LiveAudioEngine(sample_rate=0, block_size=0)
            engine.start(None)
            self.assertTrue(engine.active)
            self.assertEqual(engine.sample_rate, 48_000)
            self.assertIsNone(streams[0].kwargs["device"])
            engine.stop()
            self.assertFalse(engine.active)

    def test_inactive_stale_microphone_stream_can_be_retried(self) -> None:
        created: list[object] = []

        class StaleStream:
            active = False

            def __init__(self):
                self.closed = False

            def stop(self):
                return None

            def close(self):
                self.closed = True

        class FreshStream:
            active = True

            def __init__(self, **kwargs):
                self.samplerate = kwargs["samplerate"]
                created.append(self)

            def start(self):
                return None

            def stop(self):
                return None

            def close(self):
                return None

        stale = StaleStream()
        fake_sd = SimpleNamespace(
            query_devices=lambda device=None, kind=None: {"default_samplerate": 48_000, "max_input_channels": 1},
            check_input_settings=lambda **_kwargs: None,
            InputStream=FreshStream,
        )
        with patch("metriq_visualizer_live.sd", fake_sd):
            engine = LiveAudioEngine(sample_rate=48_000)
            engine._stream = stale  # noqa: SLF001 - host-disconnect regression fixture
            engine.start(None)
            self.assertTrue(stale.closed)
            self.assertTrue(engine.active)
            self.assertEqual(len(created), 1)
            engine.stop()

    def test_embedded_panel_emits_a_live_3d_trajectory(self) -> None:
        panel = LiveInputPanel()
        sample_rate = 48_000
        times = np.arange(int(sample_rate * 0.75), dtype=np.float64) / sample_rate
        samples = (0.25 * np.sin(2.0 * np.pi * 880.0 * times)).astype(np.float32)
        panel.engine._stream = object()  # noqa: SLF001 - deterministic no-hardware smoke test
        panel.engine.sample_rate = sample_rate
        panel.engine.snapshot = lambda _seconds=0.75: samples  # type: ignore[method-assign]
        received: list[np.ndarray] = []
        panel.trajectoryUpdated.connect(lambda points, _colors, _sizes: received.append(np.asarray(points)))
        panel._refresh()
        self.app.processEvents()
        self.assertTrue(received)
        self.assertEqual(received[-1].shape[1], 3)
        self.assertGreater(received[-1].shape[0], 0)
        panel.engine._stream = None  # noqa: SLF001
        panel.shutdown()

    def test_macos_bundle_declares_microphone_purpose(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app_bundle = Path(temp_dir) / "Metriq Visualizer.app"
            contents = app_bundle / "Contents"
            contents.mkdir(parents=True)
            plist = contents / "Info.plist"
            with plist.open("wb") as handle:
                plistlib.dump({"CFBundleName": "Metriq Visualizer"}, handle)
            patch_macos_bundle_metadata(app_bundle, "1.12.5")
            with plist.open("rb") as handle:
                payload = plistlib.load(handle)
            self.assertEqual(payload["NSMicrophoneUsageDescription"], MICROPHONE_USAGE)
            self.assertEqual(payload["CFBundleShortVersionString"], "1.12.5")
            self.assertEqual(payload["CFBundleIdentifier"], "org.metriq.visualizer")


if __name__ == "__main__":
    unittest.main()
