"""Exact-output, bounded-memory and lifecycle checks for the second pass."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from metriq_visualizer_core import AnalysisResult, GeometryResult  # noqa: E402
from metriq_visualizer_panels import AnalysisCanvas, AnalysisDockWidget  # noqa: E402
from metriq_visualizer_realtime import Realtime3DCanvas  # noqa: E402
from metriq_visualizer_render import ExportOptions  # noqa: E402
from metriq_visualizer_stage_output import StageOutputConfig, StageOutputWindow  # noqa: E402


def fixture(count=300):
    t = np.linspace(0, 10, count)
    rng = np.random.default_rng(7)
    analysis = AnalysisResult(
        source_path=Path("fixture.csv"), source_kind="table", times=t, duration=10,
        features={"time": t}, waveform=np.sin(t * 5),
        spectrogram=rng.random((128, count)), spectrogram_frequencies=np.linspace(0, 8000, 128),
        chromagram=rng.random((12, count)), mfcc=rng.normal(size=(13, count)),
    )
    arrays = dict(x=np.sin(t), y=np.cos(t * .7), z=np.sin(t * .3), color=t,
                  size=np.ones(count), times=t, rgba=np.column_stack((t/10, 1-t/10, t*0+.5, t*0+1)),
                  source_indices=np.arange(count))
    geometry = GeometryResult(**{f"{k}_{suffix}": v for suffix in ("full", "plot") for k, v in arrays.items()})
    return analysis, geometry


def pixels(pixmap):
    image = pixmap.toImage()
    return bytes(image.constBits())


class LightweightRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def canvas(self):
        canvas = Realtime3DCanvas()
        canvas.resize(720, 440)
        canvas.set_scene(*fixture(), ExportOptions(autorotate=False, history_mode="Full static"))
        canvas.set_time(5.0)
        self.addCleanup(canvas.close)
        return canvas

    def panel(self, mode="spectrogram"):
        panel = AnalysisCanvas(mode)
        panel.resize(780, 220)
        panel.set_data(*fixture())
        panel.show()
        self.app.processEvents()
        panel.ensure_current()
        self.addCleanup(panel.close)
        return panel

    def test_repeated_viewport_and_stage_frames_reuse_one_exact_raster(self):
        canvas = self.canvas()
        with patch.object(canvas, "_paint_scene", wraps=canvas._paint_scene) as paint, \
             patch.object(canvas, "frameRendered") as signal:
            first = canvas.snapshot()
            for _ in range(8):
                self.assertEqual(pixels(canvas.snapshot()), pixels(first))
                self.assertEqual(pixels(canvas.grab()), pixels(first))
            self.assertEqual(paint.call_count, 1)
            self.assertEqual(signal.emit.call_count, 1)
        # Zero budget is an uncached oracle using exactly the same painter.
        canvas.frame_cache_limit_bytes = 0
        self.assertEqual(pixels(canvas.grab()), pixels(first))
        self.assertTrue(canvas._frame_pixmap.isNull())

    def test_frame_cache_invalidates_on_scene_camera_style_size_and_clear(self):
        canvas = self.canvas()
        actions = [lambda: canvas.set_time(6), lambda: canvas.set_camera(45, -80, 1.2),
                   lambda: canvas.set_theme("light"), lambda: setattr(canvas.options, "show_grid", False),
                   lambda: canvas.resize(735, 452), lambda: canvas.setFont(QFont("monospace", 14)),
                   lambda: canvas.set_options(ExportOptions(render_mode="Points only")),
                   lambda: canvas.set_scene(*fixture(401), ExportOptions(autorotate=False)), canvas.clear_scene]
        with patch.object(canvas, "_paint_scene", wraps=canvas._paint_scene) as paint:
            canvas.snapshot()
            for index, action in enumerate(actions):
                action()
                canvas.snapshot()
                self.assertEqual(paint.call_count, index + 2)
        self.assertEqual(canvas._prepared_points.size, 0)

    def test_live_cache_replaces_buffers_and_snapshot_copy_cannot_corrupt_viewport(self):
        canvas = self.canvas()
        t = np.linspace(0, 8, 100)
        data = np.column_stack((t, np.sin(t), np.cos(t)))
        canvas.set_live_trajectory(data)
        original = canvas.snapshot()
        original_bytes = pixels(original)
        original.fill(Qt.GlobalColor.red)
        self.assertEqual(pixels(canvas.snapshot()), original_bytes)
        canvas.set_live_trajectory(data[::-1])
        changed = canvas.snapshot()
        self.assertNotEqual(pixels(changed), original_bytes)
        key = canvas._frame_key
        with self.assertRaises(ValueError):
            canvas.set_live_trajectory(data, sizes=np.ones(3))
        self.assertEqual(canvas._frame_key, key)
        canvas.clear_live_trajectory()
        self.assertNotEqual(pixels(canvas.snapshot()), pixels(changed))

    def test_cache_over_budget_preserves_resolution_and_dpi(self):
        canvas = self.canvas()
        cached = canvas.snapshot()
        canvas.frame_cache_limit_bytes = 1
        direct = canvas.snapshot()
        self.assertEqual(direct.size(), cached.size())
        self.assertEqual(direct.devicePixelRatio(), cached.devicePixelRatio())
        self.assertEqual(pixels(direct), pixels(cached))
        self.assertTrue(canvas._frame_pixmap.isNull())

    def test_blitted_panels_match_full_draw_including_legend_and_spines(self):
        for mode in ("waveform", "spectrogram", "chromagram", "mfcc", "traces"):
            with self.subTest(mode=mode):
                panel = self.panel(mode)
                for seconds in (0, 2.31, 8.42, 10):
                    panel.set_time(seconds)
                    self.app.processEvents()
                    actual = np.asarray(panel.buffer_rgba()).copy()
                    # Normal non-animated Matplotlib draw is the pixel oracle.
                    artists = panel._cursor_artists
                    panel._cursor_artists = []
                    for artist in artists:
                        artist.set_animated(False)
                    panel.draw()
                    expected = np.asarray(panel.buffer_rgba()).copy()
                    np.testing.assert_array_equal(actual, expected)
                    panel._cursor_artists = artists
                    for artist in artists:
                        artist.set_animated(True)
                    panel.draw()
                panel.close()

    def test_cursor_deferred_same_timestamp_and_snapshot_do_not_redraw_plot(self):
        panel = self.panel()
        with patch.object(panel.figure, "draw", wraps=panel.figure.draw) as full:
            panel.set_time(4.0, draw=False)
            panel.set_time(4.0, draw=True)
            self.assertFalse(panel._cursor_dirty)
            panel.set_time(7.0, draw=False)
            output = panel.snapshot()
            self.assertFalse(output.isNull())
            self.assertFalse(panel._cursor_dirty)
            self.assertEqual(full.call_count, 0)
            self.assertEqual(panel.cursor.get_xdata(), [7.0, 7.0])

    def test_cursor_cache_resize_new_data_stale_plot_and_memory_fallback(self):
        panel = self.panel("traces")
        panel.resize(900, 260)
        self.app.processEvents()
        panel.set_time(3.1)
        panel.ensure_current()
        self.assertEqual(panel._cursor_background_key, panel._cursor_raster_key())
        panel.axis.set_ylim(-1, 2)
        panel.set_time(4.0)
        panel.ensure_current()
        self.assertFalse(panel.figure.stale)
        panel.cursor_cache_limit_bytes = 0
        panel.draw()
        self.assertIsNone(panel._cursor_background)
        panel.set_time(7.0)
        panel.ensure_current()
        self.assertFalse(panel._cursor_dirty)
        panel.set_data(None)
        panel.ensure_current()
        self.assertIsNone(panel._cursor_background)
        self.assertEqual(panel._cursor_artists, [])

    def test_collapsed_dock_does_not_draw_cursor_but_preserves_time(self):
        dock = AnalysisDockWidget()
        self.addCleanup(dock.close)
        dock.set_data(*fixture())
        dock.toggle_collapsed()
        with patch.object(dock.source_panel.waveform, "_paint_cursor") as draw:
            dock.set_time(7.8)
            draw.assert_not_called()
        self.assertEqual(dock._current_time, 7.8)

    def test_stage_color_background_has_no_multimedia_pipeline_and_hidden_timer_stops(self):
        pixmap = QPixmap(10, 10)
        pixmap.fill()
        with patch("metriq_visualizer_stage_output.QMediaPlayer") as player:
            window = StageOutputWindow(lambda: {"geometry": pixmap}, StageOutputConfig(fullscreen=False))
            self.assertIsNone(window._video_player)
            player.assert_not_called()
            self.assertFalse(window._refresh_timer.isActive())
            window.show()
            self.app.processEvents()
            self.assertTrue(window._refresh_timer.isActive())
            window.hide()
            self.assertFalse(window._refresh_timer.isActive())
            window.show()
            self.assertTrue(window._refresh_timer.isActive())
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))
            window.close()
            self.assertFalse(window._refresh_timer.isActive())

    def test_hidden_panels_materialize_on_tab_or_stage_with_latest_time(self):
        dock = AnalysisDockWidget()
        self.addCleanup(dock.close)
        dock.set_data(*fixture())
        dock.set_time(6.7, draw=False)
        for panel in (dock.spectrogram, dock.chromagram, dock.mfcc, dock.traces):
            self.assertIsNotNone(panel._pending_data)
            self.assertIsNone(panel.axis)
            self.assertIsNone(panel._cursor_background)
        # Stage consumes a hidden layer without forcing other tabs to allocate.
        self.assertFalse(dock.spectrogram.snapshot().isNull())
        self.assertIsNone(dock.spectrogram._pending_data)
        self.assertEqual(dock.spectrogram.cursor.get_xdata(), [6.7, 6.7])
        self.assertIsNotNone(dock.chromagram._pending_data)
        dock.tabs.setCurrentWidget(dock.traces)
        dock.traces.ensure_current()
        self.assertIsNone(dock.traces._pending_data)
        self.assertEqual(dock.traces.cursor.get_xdata(), [6.7, 6.7])
        self.assertGreater(len(dock.traces.axis.lines), 1)

    def test_replacing_deferred_data_and_geometry_does_not_rebuild_source(self):
        dock = AnalysisDockWidget()
        self.addCleanup(dock.close)
        first = fixture(200)
        second = fixture(500)
        dock.set_data(*first)
        dock.set_data(*second)
        dock.set_time(8.0, draw=False)
        dock.mfcc.ensure_current()
        self.assertIs(dock.mfcc.analysis, second[0])
        self.assertEqual(dock.mfcc.cursor.get_xdata(), [8, 8])
        with patch.object(dock.source_panel.waveform, "set_data") as source:
            dock.update_geometry(second[0], first[1])
            source.assert_not_called()
        self.assertIs(dock.traces._pending_data[1], first[1])
        dock.set_data(None)
        dock.mfcc.ensure_current()
        self.assertIsNone(dock.mfcc.cursor)

    def test_video_background_is_created_on_demand_and_released_when_switched(self):
        import tempfile
        from unittest.mock import MagicMock

        from PySide6.QtCore import QObject, Signal
        from PySide6.QtMultimedia import QMediaPlayer as RealPlayer

        class Sink(QObject):
            videoFrameChanged = Signal(object)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "background.mp4"
            path.write_bytes(b"fixture; decoder calls are mocked in this lifecycle test")
            player = MagicMock()
            with patch("metriq_visualizer_stage_output.QMediaPlayer", return_value=player) as factory, \
                 patch("metriq_visualizer_stage_output.QAudioOutput"), \
                 patch("metriq_visualizer_stage_output.QVideoSink", side_effect=lambda parent: Sink(parent)):
                factory.Loops = RealPlayer.Loops
                window = StageOutputWindow(lambda: {}, StageOutputConfig(fullscreen=False))
                factory.assert_not_called()
                config = StageOutputConfig(background_kind="video", background_path=str(path), fullscreen=False)
                window.set_config(config)
                factory.assert_called_once()
                player.setSource.assert_called_once()
                window.show()
                self.assertTrue(player.play.called)
                window.hide()
                self.assertTrue(player.pause.called)
                window.set_config(StageOutputConfig())
                self.assertIsNone(window._video_player)
                player.stop.assert_called()
                player.deleteLater.assert_called_once()
                self.assertTrue(player.setSource.call_args.args[0].isEmpty())
                window.close()
