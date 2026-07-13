from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from metriq_visualizer_core import (
    DEFAULT_PRESETS,
    FormulaError,
    _bounded_stft,
    analysis_from_table_file,
    analyze_media,
    build_geometry,
    evaluate_formula,
    is_table_file,
)


class FormulaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.features = {
            "a": np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float32),
            "b": np.asarray([4.0, 3.0, 2.0, 1.0], dtype=np.float32),
        }

    def test_arithmetic_and_helpers_are_vectorized(self) -> None:
        result = evaluate_formula("smooth(a * 2 + b, 3)", self.features)
        self.assertEqual(result.shape, (4,))
        self.assertTrue(np.isfinite(result).all())
        aggregate = evaluate_formula("mean(a, b)", self.features)
        np.testing.assert_allclose(aggregate, 2.5)

    def test_arbitrary_python_is_rejected(self) -> None:
        for expression in ("__import__('os')", "a.__class__", "open('x')", "[x for x in a]"):
            with self.subTest(expression=expression), self.assertRaises(FormulaError):
                evaluate_formula(expression, self.features)

    def test_unknown_feature_has_clear_error(self) -> None:
        with self.assertRaisesRegex(FormulaError, "Unknown feature"):
            evaluate_formula("missing + 1", self.features)


class AnalysisAndGeometryTests(unittest.TestCase):
    def test_table_analysis_and_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "experiment.csv"
            path.write_text(
                "time,temperature,humidity,signal\n"
                + "\n".join(f"{index / 10:.1f},{20 + index * .2:.2f},{50 - index * .1:.2f},{np.sin(index / 3):.6f}" for index in range(80)),
                encoding="utf-8",
            )
            self.assertTrue(is_table_file(path))
            analysis = analysis_from_table_file(path)
            self.assertEqual(analysis.source_kind, "table")
            self.assertEqual(analysis.times.size, 80)
            self.assertIn("temperature", analysis.features)
            self.assertIn("pc1", analysis.features)
            self.assertEqual(analysis.chromagram.shape, (12, 80))

            geometry = build_geometry(
                analysis,
                "pc1",
                "pc2",
                "pc3",
                "temperature",
                "abs(signal)",
                normalize_mode="zscore",
                max_points=25,
                colormap="viridis",
            )
            self.assertEqual(geometry.x_full.size, 80)
            self.assertLessEqual(geometry.x_plot.size, 25)
            self.assertEqual(geometry.rgba_full.shape, (80, 4))
            self.assertEqual(geometry.formulas["size"], "abs(signal)")

            for alias in (
                "t",
                "row_index",
                "zcr",
                "spectral_centroid_hz",
                "spectral_bandwidth_hz",
                "spectral_rolloff_hz",
                "dominant_freq_hz",
                "f0_hz",
                "chroma_entropy",
                "spectral_contrast_mean",
            ):
                self.assertIn(alias, analysis.features)
                self.assertEqual(analysis.features[alias].shape, analysis.times.shape)


    def test_stft_frame_count_is_bounded_for_long_sources(self) -> None:
        samples = np.zeros(400_000, dtype=np.float32)
        magnitude, rms, zcr, times, hop = _bounded_stft(
            samples,
            48_000,
            n_fft=2048,
            base_hop=256,
            max_frames=64,
        )
        self.assertLessEqual(magnitude.shape[1], 64)
        self.assertEqual(magnitude.shape[1], rms.size)
        self.assertEqual(rms.size, zcr.size)
        self.assertEqual(zcr.size, times.size)
        self.assertGreater(hop, 256)


    @staticmethod
    def _assert_all_presets_build(analysis) -> None:
        for name, preset in DEFAULT_PRESETS.items():
            with unittest.TestCase.subTest(unittest.TestCase(), preset=name):
                geometry = build_geometry(
                    analysis,
                    preset["x"],
                    preset["y"],
                    preset["z"],
                    preset["color"],
                    preset["size"],
                    max_points=128,
                )
                if geometry.x_plot.size < 1:
                    raise AssertionError(f"{name} produced no geometry")

    def test_all_builtin_presets_work_for_table_and_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            table = root / "data.csv"
            table.write_text(
                "time,a,b,c\n"
                + "\n".join(
                    f"{index / 20:.3f},{np.sin(index / 5):.6f},{np.cos(index / 7):.6f},{index % 11}"
                    for index in range(160)
                ),
                encoding="utf-8",
            )
            sample_rate = 22_050
            time = np.arange(sample_rate, dtype=np.float64) / sample_rate
            audio = (0.25 * np.sin(2 * np.pi * 440.0 * time)).astype(np.float32)
            wav = root / "tone.wav"
            wavfile.write(wav, sample_rate, audio)

            for analysis in (analysis_from_table_file(table), analyze_media(wav)):
                for name, preset in DEFAULT_PRESETS.items():
                    with self.subTest(source=analysis.source_kind, preset=name):
                        geometry = build_geometry(
                            analysis,
                            preset["x"],
                            preset["y"],
                            preset["z"],
                            preset["color"],
                            preset["size"],
                            max_points=128,
                        )
                        self.assertGreater(geometry.x_plot.size, 0)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_all_builtin_presets_work_for_silent_video(self) -> None:
        try:
            import cv2  # noqa: F401
        except ImportError:
            self.skipTest("OpenCV is not installed")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "silent.mp4"
            command = [
                shutil.which("ffmpeg") or "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=160x90:rate=10:duration=1",
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ]
            subprocess.run(command, check=True)
            analysis = analyze_media(path)
            self.assertIsNone(analysis.audio_path)
            self.assertTrue(analysis.has_video)
            for name, preset in DEFAULT_PRESETS.items():
                with self.subTest(preset=name):
                    geometry = build_geometry(
                        analysis,
                        preset["x"],
                        preset["y"],
                        preset["z"],
                        preset["color"],
                        preset["size"],
                        max_points=128,
                    )
                    self.assertGreater(geometry.x_plot.size, 0)


    def test_short_wav_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tone.wav"
            sample_rate = 22_050
            duration = 0.6
            time = np.arange(int(sample_rate * duration), dtype=np.float64) / sample_rate
            samples = (0.35 * np.sin(2 * np.pi * 440.0 * time)).astype(np.float32)
            wavfile.write(path, sample_rate, samples)
            analysis = analyze_media(path)
            self.assertEqual(analysis.source_kind, "audio")
            self.assertGreater(analysis.times.size, 2)
            self.assertAlmostEqual(analysis.duration, duration, delta=0.08)
            self.assertIn("spectral_centroid", analysis.features)
            self.assertIn("dominant_frequency", analysis.features)
            self.assertEqual(analysis.chromagram.shape[0], 12)
            self.assertEqual(analysis.mfcc.shape[0], 20)


if __name__ == "__main__":
    unittest.main()
