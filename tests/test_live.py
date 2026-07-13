from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from metriq_visualizer_live import LiveAudioEngine, LiveScopeWidget  # noqa: E402


class LiveAudioEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_ring_buffer_is_bounded_and_snapshot_is_padded(self) -> None:
        engine = LiveAudioEngine(sample_rate=10, block_size=6, seconds=1.0)
        engine._callback(np.arange(6, dtype=np.float32).reshape(-1, 1), 6, None, None)
        engine._callback(np.arange(10, 16, dtype=np.float32).reshape(-1, 1), 6, None, None)

        snapshot = engine.snapshot(1.0)
        self.assertEqual(snapshot.shape, (10,))
        np.testing.assert_array_equal(snapshot[-6:], np.arange(10, 16, dtype=np.float32))
        self.assertEqual(engine.last_status, "Listening")

    def test_recorded_count_avoids_materializing_the_capture(self) -> None:
        engine = LiveAudioEngine(sample_rate=48_000)
        engine.set_recording(True)
        engine._callback(np.ones((128, 1), dtype=np.float32), 128, None, None)
        engine._callback(np.ones((64, 1), dtype=np.float32), 64, None, "input overflow")

        self.assertEqual(engine.recorded_sample_count, 192)
        self.assertEqual(engine.recorded_audio().size, 192)
        self.assertEqual(engine.last_status, "input overflow")
        engine.clear_recording()
        self.assertEqual(engine.recorded_sample_count, 0)

    def test_enabling_a_new_recording_resets_previous_take(self) -> None:
        engine = LiveAudioEngine()
        engine.set_recording(True)
        engine._callback(np.ones((32, 1), dtype=np.float32), 32, None, None)
        engine.set_recording(False)
        engine.set_recording(True)
        self.assertEqual(engine.recorded_sample_count, 0)

    def test_start_failure_does_not_leave_engine_active_and_retry_works(self) -> None:
        failed_streams: list[object] = []

        class FailingStream:
            closed = False

            def start(self) -> None:
                raise RuntimeError("device busy")

            def close(self) -> None:
                self.closed = True

        class FailingSoundDevice:
            @staticmethod
            def InputStream(**_kwargs):  # noqa: N802 - mirrors sounddevice
                stream = FailingStream()
                failed_streams.append(stream)
                return stream

        engine = LiveAudioEngine()
        with (
            patch("metriq_visualizer_live.sd", FailingSoundDevice),
            self.assertRaisesRegex(RuntimeError, "device busy"),
        ):
            engine.start()
        self.assertFalse(engine.active)
        self.assertEqual(engine.last_status, "Start failed")
        self.assertTrue(failed_streams[0].closed)  # type: ignore[attr-defined]

        class GoodStream:
            started = False
            closed = False

            def start(self) -> None:
                self.started = True

            def stop(self) -> None:
                self.started = False

            def close(self) -> None:
                self.closed = True

        good_stream = GoodStream()

        class GoodSoundDevice:
            @staticmethod
            def InputStream(**_kwargs):  # noqa: N802 - mirrors sounddevice
                return good_stream

        with patch("metriq_visualizer_live.sd", GoodSoundDevice):
            engine.start()
            self.assertTrue(engine.active)
            self.assertTrue(good_stream.started)
            engine.stop()
        self.assertFalse(engine.active)
        self.assertTrue(good_stream.closed)

    def test_silence_is_not_reported_as_a_full_scale_spectrum(self) -> None:
        scope = LiveScopeWidget()
        scope.set_audio(np.zeros(8_192, dtype=np.float32), 48_000)
        self.assertLessEqual(float(np.max(scope.spectrum_db)), -99.0)
        self.assertEqual(scope.metrics["Dominant"], "0 Hz")
        self.assertEqual(scope.metrics["Centroid"], "0 Hz")
        self.assertTrue(scope.spectrogram)
        self.assertLessEqual(float(np.max(scope.spectrogram[-1])), -99.0)
        scope.close()


if __name__ == "__main__":
    unittest.main()
