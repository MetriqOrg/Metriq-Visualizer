from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_SETTINGS_ROOT = Path(tempfile.mkdtemp(prefix="metriq-visualizer-test-settings-"))
os.environ.setdefault("METRIQ_SETTINGS_PATH", str(_TEST_SETTINGS_ROOT / "settings.ini"))

from PySide6.QtCore import QSettings, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from metriq_visualizer_app import APP_VERSION, MainWindow  # noqa: E402
from metriq_visualizer_export_pipeline import ExportProfile  # noqa: E402
from metriq_visualizer_export_studio import ExportStudioDialog  # noqa: E402
from metriq_visualizer_live import LiveInputDialog  # noqa: E402


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["XDG_CONFIG_HOME"] = str(Path(cls.temp.name) / "config")
        os.environ["XDG_CACHE_HOME"] = str(Path(cls.temp.name) / "cache")
        os.environ["METRIQ_CACHE_DIR"] = str(Path(cls.temp.name) / "analysis-cache")
        settings = QSettings(os.environ["METRIQ_SETTINGS_PATH"], QSettings.Format.IniFormat)
        settings.clear()
        settings.sync()
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.processEvents()
        cls.temp.cleanup()

    def _wait_for_analysis(self, window: MainWindow, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while window.analysis_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertIsNone(window.analysis_thread, "analysis did not finish")
        self.assertIsNotNone(window.analysis)
        self.assertIsNotNone(window.geometry)
        window._render_preview()
        self.app.processEvents()

    def test_main_export_and_live_dialogs_initialize(self) -> None:
        source = Path(self.temp.name) / "gui-source.csv"
        source.write_text(
            "time,a,b,c\n" + "\n".join(f"{index / 10},{index},{index % 7},{index % 11}" for index in range(50)),
            encoding="utf-8",
        )
        window = MainWindow()
        self.assertEqual(APP_VERSION, "1.12.5")
        window.autorotate_check.setChecked(True)
        window._viewport_interaction_started()
        self.assertTrue(window.autorotate_check.isChecked())
        window._start_analysis(source)
        self._wait_for_analysis(window)
        self.assertIsNotNone(window.viewport.scene)
        self.assertFalse(window.viewport.realtime.isHidden())
        window.show()
        self.app.processEvents()
        self.assertTrue(window.analysis_dock.is_collapsed)
        collapsed_sizes = window.workspace_splitter.sizes()
        self.assertLessEqual(collapsed_sizes[1], 60)
        self.assertGreater(collapsed_sizes[0], collapsed_sizes[1] * 5)
        self.assertEqual(window.analysis_dock.tabs.count(), 5)

        window.analysis_dock.toggle_collapsed()
        self.app.processEvents()
        expanded_sizes = window.workspace_splitter.sizes()
        self.assertFalse(window.analysis_dock.is_collapsed)
        self.assertGreaterEqual(expanded_sizes[1], 180)
        window.analysis_dock.toggle_collapsed()
        self.app.processEvents()

        assert window.analysis is not None and window.geometry is not None
        studio = ExportStudioDialog(
            window.analysis,
            window.geometry,
            window._make_render_options(width=640, height=360),
            window,
        )
        studio._render_preview()
        self.app.processEvents()
        pixmap = studio.preview_label.pixmap()
        self.assertIsNotNone(pixmap)
        assert pixmap is not None
        self.assertFalse(pixmap.isNull())
        self.assertEqual(studio._collect_profile().encoder_mode, "auto")
        studio.close()
        self.app.processEvents()

        live = LiveInputDialog(window)
        self.assertEqual(live.windowTitle(), "Metriq Live Input")
        self.assertGreaterEqual(live.device_combo.count(), 1)
        live.close()
        window.close()
        self.app.processEvents()

    def test_light_scene_legacy_presets_and_live_quality_profiles(self) -> None:
        source = Path(self.temp.name) / "theme-preset-source.csv"
        source.write_text(
            "time,a,b,c\n"
            + "\n".join(
                f"{index / 20:.4f},{np.sin(index / 5):.6f},{np.cos(index / 7):.6f},{index % 13}"
                for index in range(120)
            ),
            encoding="utf-8",
        )
        window = MainWindow()
        window._start_analysis(source)
        self._wait_for_analysis(window)

        self.assertGreaterEqual(window.preset_combo.findText("Birdsong"), 0)
        self.assertGreaterEqual(window.visual_preset_combo.findText("Glowstick"), 0)
        self.assertGreaterEqual(window.visual_preset_combo.findText("Neon Lights"), 0)

        original_mapping = (
            window.x_edit.text(),
            window.y_edit.text(),
            window.z_edit.text(),
            window.color_edit.text(),
            window.size_edit.text(),
        )
        original_performance = (
            window.performance_combo.currentText(),
            window.live_points_spin.value(),
            window.live_fps_spin.value(),
        )
        window.visual_preset_combo.setCurrentText("Glowstick")
        self.app.processEvents()
        self.assertEqual(
            original_mapping,
            (
                window.x_edit.text(),
                window.y_edit.text(),
                window.z_edit.text(),
                window.color_edit.text(),
                window.size_edit.text(),
            ),
        )
        self.assertEqual(
            original_performance,
            (
                window.performance_combo.currentText(),
                window.live_points_spin.value(),
                window.live_fps_spin.value(),
            ),
        )

        window._set_theme("light", persist=False)
        self.app.processEvents()
        figure_rgb = np.asarray(window.viewport.figure.get_facecolor()[:3])
        axis_rgb = np.asarray(window.viewport.scene.ax.get_facecolor()[:3])
        self.assertGreater(float(np.mean(figure_rgb)), 0.9)
        self.assertGreater(float(np.mean(axis_rgb)), 0.9)

        window.render_mode_combo.setCurrentText("Tube")
        window.curve_combo.setCurrentText("Smooth spline")
        window.curve_detail_spin.setValue(16)
        window.tube_sides_spin.setValue(20)
        window.colorbar_check.setChecked(True)
        window.performance_combo.setCurrentText("Fast preview")
        self.app.processEvents()
        self.assertEqual(window.live_points_spin.value(), 650)
        self.assertEqual(window.live_fps_spin.value(), 15)
        self.assertEqual(window.playback_render_timer.interval(), 67)

        # The selected profile remains active while paused by default, so
        # ordinary computers do not rebuild a full tube/spline scene after
        # every stop. Full idle refinement remains an explicit opt-in.
        self.assertFalse(window.refine_idle_check.isChecked())
        paused_proxy_options = window._make_render_options(live_preview=True)
        self.assertEqual(paused_proxy_options.render_mode, "Points + line")
        self.assertEqual(paused_proxy_options.path_curve_mode, "Smooth spline")

        window._playing = True
        live_options = window._make_render_options(live_preview=True)
        window.preview_session_dirty = True
        window._render_preview()
        self.app.processEvents()
        self.assertEqual(window.viewport.canvas.pixel_ratio_cap, 1.0)

        window._playing = False
        window.autorotate_check.setChecked(False)
        window.refine_idle_check.setChecked(True)
        refined_idle_options = window._make_render_options(live_preview=True)
        window.preview_session_dirty = True
        window._render_preview()
        self.app.processEvents()
        self.assertEqual(refined_idle_options.render_mode, "Tube")
        self.assertIsNone(window.viewport.canvas.pixel_ratio_cap)

        export_options = window._make_render_options(live_preview=False)
        self.assertEqual(live_options.render_mode, "Points + line")
        self.assertEqual(live_options.path_curve_mode, "Smooth spline")
        self.assertFalse(live_options.show_colorbar)
        self.assertEqual(export_options.render_mode, "Tube")
        self.assertEqual(export_options.path_curve_mode, "Smooth spline")
        self.assertTrue(export_options.show_colorbar)

        window.close()
        self.app.processEvents()

    def test_playback_advances_and_renders_while_play_is_active(self) -> None:
        source = Path(self.temp.name) / "playback-source.csv"
        source.write_text(
            "time,a,b,c\n"
            + "\n".join(
                f"{index / 30:.6f},{np.sin(index / 8):.6f},{np.cos(index / 11):.6f},{index % 17}"
                for index in range(180)
            ),
            encoding="utf-8",
        )
        window = MainWindow()
        window._start_analysis(source)
        self._wait_for_analysis(window)
        self.assertIsNotNone(window.viewport.scene)
        original_update = window.viewport.update_time
        render_count = 0

        def counted_update(*args, **kwargs):
            nonlocal render_count
            render_count += 1
            return original_update(*args, **kwargs)

        window.viewport.update_time = counted_update  # type: ignore[method-assign]
        start_time = window.current_time
        window.toggle_playback()
        deadline = time.monotonic() + 0.45
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        window.stop_playback()
        self.app.processEvents()

        self.assertGreater(window.current_time, start_time + 0.15)
        self.assertGreaterEqual(render_count, 2)
        self.assertEqual(window.play_button.text(), "Play")
        window.close()
        self.app.processEvents()

    def test_playback_keeps_autorotation_live_without_redrawing_analysis_at_clock_rate(self) -> None:
        source = Path(self.temp.name) / "autorotate-playback-source.csv"
        source.write_text(
            "time,a,b,c\n"
            + "\n".join(
                f"{index / 30:.6f},{np.sin(index / 8):.6f},{np.cos(index / 11):.6f},{index % 17}"
                for index in range(180)
            ),
            encoding="utf-8",
        )
        window = MainWindow()
        window._start_analysis(source)
        self._wait_for_analysis(window)
        window.autorotate_check.setChecked(True)
        window.rotation_speed_spin.setValue(120.0)
        window._render_preview()
        self.app.processEvents()
        self.assertTrue(window.viewport.autorotate_timer.isActive())

        with patch.object(window.analysis_dock, "set_time", wraps=window.analysis_dock.set_time) as set_time:
            start_azimuth = window.viewport.camera()[1]
            window.toggle_playback()
            deadline = time.monotonic() + 0.22
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.005)
            window.stop_playback()
            self.app.processEvents()

        self.assertTrue(window.autorotate_check.isChecked())
        self.assertTrue(window.viewport.autorotate_timer.isActive())
        self.assertGreater(abs(window.viewport.camera()[1] - start_azimuth), 5.0)
        self.assertTrue(any(call.kwargs.get("draw") is False for call in set_time.call_args_list))
        window.close()
        self.app.processEvents()

    def test_audio_source_is_connected_to_qt_media_player(self) -> None:
        path = Path(self.temp.name) / "audible-tone.wav"
        sample_rate = 22_050
        time_axis = np.arange(int(sample_rate * 0.8), dtype=np.float64) / sample_rate
        samples = (0.25 * np.sin(2 * np.pi * 440.0 * time_axis)).astype(np.float32)
        wavfile.write(path, sample_rate, samples)

        window = MainWindow()
        window._start_analysis(path)
        self._wait_for_analysis(window)
        self.assertEqual(window.media_player.source(), QUrl.fromLocalFile(str(path.resolve())))
        self.assertEqual(window._media_source_path, path.resolve())
        self.assertTrue(window.volume_slider.isEnabled())
        self.assertTrue(window.mute_check.isEnabled())

        play_calls = 0
        original_play = window.media_player.play

        def counted_play() -> None:
            nonlocal play_calls
            play_calls += 1
            original_play()

        window.media_player.play = counted_play  # type: ignore[method-assign]
        window.toggle_playback()
        self.app.processEvents()
        self.assertEqual(play_calls, 1)
        self.assertTrue(window._playing)
        window.stop_playback()
        window.close()
        self.app.processEvents()



    def test_paused_timeline_scrubbing_seeks_the_connected_media_source(self) -> None:
        path = Path(self.temp.name) / "scrub-tone.wav"
        sample_rate = 22_050
        time_axis = np.arange(sample_rate, dtype=np.float64) / sample_rate
        samples = (0.2 * np.sin(2 * np.pi * 330.0 * time_axis)).astype(np.float32)
        wavfile.write(path, sample_rate, samples)

        window = MainWindow()
        window._start_analysis(path)
        self._wait_for_analysis(window)
        requested: list[int] = []
        original = window.media_player.setPosition

        def capture(position: int) -> None:
            requested.append(int(position))
            original(position)

        window.media_player.setPosition = capture  # type: ignore[method-assign]
        window._timeline_changed(window.time_slider.maximum() // 2)
        self.app.processEvents()
        self.assertTrue(requested)
        self.assertAlmostEqual(requested[-1], round(window.analysis.duration * 500.0), delta=5)
        window.close()
        self.app.processEvents()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_silent_video_is_connected_to_the_media_player_for_source_preview(self) -> None:
        path = Path(self.temp.name) / "gui-silent-video.mp4"
        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x90:rate=8:duration=0.8",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )
        window = MainWindow()
        window._start_analysis(path)
        self._wait_for_analysis(window)
        self.assertIsNotNone(window.analysis)
        assert window.analysis is not None
        self.assertTrue(window.analysis.has_video)
        self.assertIsNone(window.analysis.audio_path)
        self.assertEqual(window.media_player.source(), QUrl.fromLocalFile(str(path.resolve())))
        self.assertEqual(window._media_source_path, path.resolve())
        self.assertFalse(window._media_has_audio)
        self.assertFalse(window.volume_slider.isEnabled())
        self.assertFalse(window.mute_check.isEnabled())
        self.assertIs(window.analysis_dock.source_panel.stack.currentWidget(), window.analysis_dock.source_panel.video_widget)
        window.close()
        self.app.processEvents()

    def test_export_format_switch_preserves_video_and_jpeg_quality(self) -> None:
        source = Path(self.temp.name) / "format-source.csv"
        source.write_text("time,a,b\n0,1,2\n1,2,3\n2,3,5\n", encoding="utf-8")
        window = MainWindow()
        window._start_analysis(source)
        self._wait_for_analysis(window)
        assert window.analysis is not None and window.geometry is not None
        studio = ExportStudioDialog(
            window.analysis,
            window.geometry,
            window._make_render_options(width=640, height=360),
            window,
        )

        h264_index = studio.format_combo.findData("mp4_h264")
        jpeg_index = studio.format_combo.findData("jpeg_sequence")
        studio.format_combo.setCurrentIndex(h264_index)
        studio.quality_spin.setValue(27)
        studio.format_combo.setCurrentIndex(jpeg_index)
        self.assertEqual(studio.quality_spin.value(), 92)
        studio.quality_spin.setValue(87)
        studio.format_combo.setCurrentIndex(h264_index)
        self.assertEqual(studio.quality_spin.value(), 27)
        studio.format_combo.setCurrentIndex(jpeg_index)
        self.assertEqual(studio.quality_spin.value(), 87)

        profile = ExportProfile(
            format_key="mp4_h264",
            include_audio=True,
            show_project_title=False,
            title="Saved title",
            project_subtitle="Saved subtitle",
            show_watermark=True,
            watermark_text="LOCAL",
            show_axes=False,
            show_axis_labels=False,
            show_colorbar=True,
        ).validate()
        studio._apply_profile(profile)
        self.assertTrue(studio.preview_dirty)
        self.assertFalse(studio.audio_check.isEnabled())
        self.assertFalse(studio.audio_check.isChecked())
        self.assertFalse(studio.show_title_check.isChecked())
        self.assertEqual(studio.project_title_edit.text(), "Saved title")
        self.assertEqual(studio.project_subtitle_edit.text(), "Saved subtitle")
        self.assertTrue(studio.watermark_check.isChecked())
        self.assertEqual(studio.watermark_edit.text(), "LOCAL")
        self.assertFalse(studio.axes_check.isChecked())
        self.assertFalse(studio.axis_labels_check.isChecked())
        self.assertTrue(studio.colorbar_check.isChecked())

        studio.close()
        window.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
