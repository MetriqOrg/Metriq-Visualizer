from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

from metriq_visualizer_export_pipeline import (
    EXPORT_PROFILE_VERSION,
    ExportCancelled,
    ExportProfile,
    _iter_frames,
    _write_sequence,
    _write_video,
    build_ffmpeg_command,
    load_export_profile,
    output_path_for_profile,
    save_export_profile,
    select_video_encoder,
)


class ExportProfileTests(unittest.TestCase):
    def test_built_in_export_profiles_are_current_and_loadable(self) -> None:
        root = Path(__file__).resolve().parents[1] / "export_profiles"
        profiles = sorted(root.glob("*.mvexport"))
        self.assertGreaterEqual(len(profiles), 4)
        for path in profiles:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], EXPORT_PROFILE_VERSION)
            loaded = load_export_profile(path)
            self.assertIsInstance(loaded.show_scene_hud, bool)
            self.assertIsInstance(loaded.show_timecode, bool)

    def test_validation_preserves_exact_gif_dimensions_and_capabilities(self) -> None:
        profile = ExportProfile(width=1919, height=1079, fps=500, format_key="gif", include_audio=True).validate()
        self.assertEqual((profile.width, profile.height), (1919, 1079))
        self.assertEqual(profile.fps, 240.0)
        self.assertFalse(profile.include_audio)

    def test_chroma_subsampled_video_dimensions_are_even(self) -> None:
        profile = ExportProfile(width=1919, height=1079, format_key="mp4_h264").validate()
        self.assertEqual((profile.width, profile.height), (1920, 1080))

    def test_profile_round_trip_includes_presentation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = ExportProfile(
                name="Vertical creator",
                width=1080,
                height=1920,
                fps=29.97,
                format_key="webm_vp9",
                quality=24,
                layout={"geometry": {"enabled": True}},
                encoder_mode="software",
                show_project_title=False,
                project_subtitle="Field recording",
                show_watermark=True,
                watermark_text="METRIQ / LOCAL",
                show_axes=False,
                show_axis_labels=False,
                show_colorbar=True,
                show_scene_hud=False,
                show_timecode=True,
            )
            path = save_export_profile(Path(temp_dir) / "creator", source)
            loaded = load_export_profile(path)
            self.assertEqual(path.suffix, ".mvexport")
            self.assertEqual(loaded.name, source.name)
            self.assertEqual((loaded.width, loaded.height), (1080, 1920))
            self.assertEqual(loaded.layout, source.layout)
            self.assertEqual(loaded.encoder_mode, "software")
            self.assertFalse(loaded.show_project_title)
            self.assertEqual(loaded.project_subtitle, "Field recording")
            self.assertTrue(loaded.show_watermark)
            self.assertEqual(loaded.watermark_text, "METRIQ / LOCAL")
            self.assertFalse(loaded.show_axes)
            self.assertFalse(loaded.show_axis_labels)
            self.assertTrue(loaded.show_colorbar)
            self.assertFalse(loaded.show_scene_hud)
            self.assertTrue(loaded.show_timecode)

    def test_output_extension_is_enforced(self) -> None:
        profile = ExportProfile(format_key="webm_vp9").validate()
        self.assertEqual(output_path_for_profile("sample.mp4", profile).suffix, ".webm")


class FFmpegCommandTests(unittest.TestCase):
    def test_h264_command_maps_optional_audio(self) -> None:
        profile = ExportProfile(format_key="mp4_h264", include_audio=True, start_time=2.5).validate()
        command = build_ffmpeg_command(
            "ffmpeg",
            "out",
            profile,
            audio_path="source.wav",
            duration=4.0,
        )
        self.assertIn("libx264", command)
        self.assertIn("1:a:0?", command)
        self.assertIn("-shortest", command)
        self.assertEqual(command[-1], "out.mp4")

    def test_explicit_nvenc_command_uses_selected_encoder(self) -> None:
        profile = ExportProfile(format_key="mp4_h264", encoder_mode="nvidia").validate()
        command = build_ffmpeg_command(
            "ffmpeg",
            "out",
            profile,
            audio_path=None,
            duration=1.0,
            video_encoder="h264_nvenc",
        )
        self.assertIn("h264_nvenc", command)
        self.assertIn("-cq", command)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_software_mode_resolves_software_encoder(self) -> None:
        executable = shutil.which("ffmpeg")
        assert executable is not None
        profile = ExportProfile(format_key="mp4_h264", encoder_mode="software").validate()
        self.assertEqual(select_video_encoder(profile, executable), "libx264")

    def test_gif_maps_filter_output_and_has_no_audio(self) -> None:
        profile = ExportProfile(format_key="gif", include_audio=True).validate()
        command = build_ffmpeg_command(
            "ffmpeg",
            "loop",
            profile,
            audio_path="source.wav",
            duration=1.0,
        )
        self.assertIn("[outv]", command)
        self.assertNotIn("1:a:0?", command)
        self.assertNotIn("0:v:0", command)
        self.assertEqual(command[-1], "loop.gif")


class WriterTests(unittest.TestCase):
    @staticmethod
    def frames(width: int, height: int, count: int):
        for index in range(count):
            frame = np.zeros((height, width, 4), dtype=np.uint8)
            frame[:, :, 0] = index * 20
            frame[:, :, 1] = np.arange(width, dtype=np.uint8)[None, :]
            frame[:, :, 3] = 255
            yield frame

    def test_png_sequence_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "frames"
            profile = ExportProfile(width=161, height=163, fps=5, format_key="png_sequence").validate()
            result = _write_sequence(output, profile, self.frames(161, 163, 3), 3, None)
            self.assertEqual(result, output)
            self.assertTrue((output / "frame_000001.png").is_file())
            self.assertTrue((output / "frame_000003.png").is_file())
            self.assertTrue((output / "sequence.json").is_file())

    def test_nonempty_sequence_directory_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "frames"
            output.mkdir()
            sentinel = output / "creator-note.txt"
            sentinel.write_text("keep", encoding="utf-8")
            profile = ExportProfile(width=160, height=160, fps=5, format_key="jpeg_sequence").validate()
            result = _write_sequence(output, profile, self.frames(160, 160, 2), 2, None)
            self.assertNotEqual(result, output)
            self.assertEqual(result.parent, output)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertTrue((result / "frame_000001.jpg").is_file())

    def test_short_sequence_is_rejected_and_partial_files_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "frames"
            profile = ExportProfile(width=160, height=160, fps=5, format_key="png_sequence").validate()
            with self.assertRaisesRegex(RuntimeError, "expected 3"):
                _write_sequence(output, profile, self.frames(160, 160, 2), 3, None)
            self.assertFalse(output.exists())

    def test_extra_sequence_frames_are_rejected_and_partial_files_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "frames"
            profile = ExportProfile(width=160, height=160, fps=5, format_key="png_sequence").validate()
            with self.assertRaisesRegex(RuntimeError, "more than"):
                _write_sequence(output, profile, self.frames(160, 160, 3), 2, None)
            self.assertFalse(output.exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_streamed_h264_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "stream.mp4"
            profile = ExportProfile(
                width=160,
                height=160,
                fps=5,
                format_key="mp4_h264",
                include_audio=False,
                quality=28,
                encoder_mode="software",
            ).validate()
            result = _write_video(
                output,
                profile,
                self.frames(160, 160, 5),
                5,
                audio_path=None,
                duration=1.0,
                cancel_event=threading.Event(),
                progress_callback=None,
            )
            self.assertEqual(result, output)
            self.assertGreater(output.stat().st_size, 100)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_failed_video_export_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "existing.mp4"
            original = b"creator's previous export"
            output.write_bytes(original)
            profile = ExportProfile(
                width=160,
                height=160,
                fps=5,
                format_key="mp4_h264",
                include_audio=False,
                encoder_mode="software",
            ).validate()

            def broken_frames():
                yield from self.frames(160, 160, 1)
                raise RuntimeError("synthetic renderer failure")

            with self.assertRaisesRegex(RuntimeError, "synthetic renderer failure"):
                _write_video(
                    output,
                    profile,
                    broken_frames(),
                    2,
                    audio_path=None,
                    duration=0.4,
                    cancel_event=threading.Event(),
                    progress_callback=None,
                )
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(list(output.parent.glob(f".{output.name}.*.mp4")), [])

    def test_frame_iteration_honors_cancel(self) -> None:
        class Session:
            def render_frame(self, **_kwargs):
                return np.zeros((160, 160, 4), dtype=np.uint8)

        event = threading.Event()
        event.set()
        profile = ExportProfile(width=160, height=160, fps=5).validate()
        iterator = _iter_frames(Session(), profile, None, 1, 0.0, event, None)
        with self.assertRaises(ExportCancelled):
            next(iter(iterator))


if __name__ == "__main__":
    unittest.main()
