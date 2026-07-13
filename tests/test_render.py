from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from metriq_visualizer_core import analysis_from_table_file, analyze_media, build_geometry
from metriq_visualizer_layout import balanced_export_layout, geometry_focus_export_layout
from metriq_visualizer_render import ExportOptions, ExportPreviewSession


class RenderTests(unittest.TestCase):
    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        path = Path(temporary.name) / "data.csv"
        path.write_text(
            "time,a,b,c\n"
            + "\n".join(f"{index / 20:.3f},{np.sin(index/6):.6f},{np.cos(index/9):.6f},{index % 13}" for index in range(160)),
            encoding="utf-8",
        )
        analysis = analysis_from_table_file(path)
        geometry = build_geometry(analysis, "pc1", "pc2", "pc3", "c", "abs(a)", max_points=120)
        return temporary, analysis, geometry

    def test_balanced_frame_is_rgba_and_nonempty(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        options = ExportOptions(width=640, height=360, layout=balanced_export_layout(), show_project_title=True)
        with ExportPreviewSession(analysis, geometry, options) as session:
            frame = session.render_frame(current_time=2.0)
        self.assertEqual(frame.shape, (360, 640, 4))
        self.assertEqual(frame.dtype, np.uint8)
        self.assertGreater(float(np.std(frame[:, :, :3])), 4.0)
        self.assertTrue(np.all(frame[:, :, 3] > 0))

    def test_geometry_focus_and_closed_session(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        layout = geometry_focus_export_layout()
        layout.geometry.content_alpha = 0.5
        options = ExportOptions(width=320, height=240, layout=layout, show_project_title=False)
        session = ExportPreviewSession(analysis, geometry, options)
        frame = session.render_frame(current_time=1.0, output_size=(320, 240))
        self.assertEqual(frame.shape, (240, 320, 4))
        session.close()
        with self.assertRaises(RuntimeError):
            session.render_frame(current_time=1.0)


    def test_export_timecode_is_opt_in_not_forced_onto_creator_output(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        layout = geometry_focus_export_layout()
        for name in ("geometry", "preview", "spectrogram", "chromagram", "mfcc", "traces"):
            layout.item(name).enabled = False
        clean = ExportOptions(
            width=360,
            height=220,
            layout=layout,
            show_project_title=False,
            show_watermark=False,
            show_timecode=False,
        )
        marked = ExportOptions(
            width=360,
            height=220,
            layout=layout,
            show_project_title=False,
            show_watermark=False,
            show_timecode=True,
        )
        with ExportPreviewSession(analysis, geometry, clean) as session:
            clean_frame = session.render_frame(current_time=2.0)
        with ExportPreviewSession(analysis, geometry, marked) as session:
            marked_frame = session.render_frame(current_time=2.0)
        self.assertEqual(np.unique(clean_frame.reshape(-1, 4), axis=0).shape[0], 1)
        self.assertGreater(float(np.mean(np.abs(marked_frame.astype(np.int16) - clean_frame.astype(np.int16)))), 0.01)

    def test_tube_controls_change_the_rendered_geometry(self) -> None:
        temporary, analysis, geometry = self._fixture()
        self.addCleanup(temporary.cleanup)
        layout = geometry_focus_export_layout()
        thin = ExportOptions(
            width=420,
            height=280,
            layout=layout,
            render_mode="Tube",
            history_mode="Full static",
            autorotate=False,
            tube_radius_scale=0.25,
            tube_sides=4,
            tube_follow_size=False,
            tube_taper=0.0,
            show_project_title=False,
        )
        thick = ExportOptions(
            width=420,
            height=280,
            layout=layout,
            render_mode="Tube",
            history_mode="Full static",
            autorotate=False,
            tube_radius_scale=3.0,
            tube_sides=24,
            tube_follow_size=True,
            tube_taper=0.7,
            show_project_title=False,
        )
        with ExportPreviewSession(analysis, geometry, thin) as session:
            thin_frame = session.render_frame(current_time=analysis.duration)
        with ExportPreviewSession(analysis, geometry, thick) as session:
            thick_frame = session.render_frame(current_time=analysis.duration)
        difference = np.mean(np.abs(thin_frame.astype(np.int16) - thick_frame.astype(np.int16)))
        self.assertGreater(float(difference), 0.25)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_source_video_preview_changes_with_playback_time(self) -> None:
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV is not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "motion.mp4"
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
                    "testsrc2=size=240x135:rate=12:duration=1",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(path),
                ],
                check=True,
            )
            analysis = analyze_media(path)
            geometry = build_geometry(analysis, "pc1", "pc2", "pc3", "time", "magnitude")
            layout = geometry_focus_export_layout()
            layout.geometry.enabled = False
            layout.preview.enabled = True
            layout.preview.x = 0.0
            layout.preview.y = 0.0
            layout.preview.w = 1.0
            layout.preview.h = 1.0
            layout.preview.show_title = False
            options = ExportOptions(
                width=320,
                height=180,
                layout=layout,
                include_preview=True,
                show_project_title=False,
                autorotate=False,
            )
            with ExportPreviewSession(analysis, geometry, options) as session:
                first = session.render_frame(current_time=0.05)
                later = session.render_frame(current_time=0.75)
            difference = np.mean(np.abs(first.astype(np.int16) - later.astype(np.int16)))
            self.assertGreater(float(difference), 4.0)


if __name__ == "__main__":
    unittest.main()
