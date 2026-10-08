"""Without FFmpeg, audio is decoded with libsndfile and scipy; librosa is not needed."""

import sys

import numpy as np
import soundfile as sf

import metriq_visualizer_core as core


def test_stereo_44k_file_is_analyzed_without_ffmpeg_or_librosa(tmp_path, monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda name: None)
    rate, seconds = 44100, 3
    t = np.arange(rate * seconds) / rate
    left = 0.4 * np.sin(2 * np.pi * 440 * t)
    path = tmp_path / "stereo_440.wav"
    sf.write(path, np.stack([left, left], axis=1), rate, subtype="PCM_16")

    result = core.analyze_media(path, temp_dir=tmp_path / "decode", use_cache=False)

    assert result.sample_rate == 22050
    assert abs(result.duration - seconds) < 0.05
    dominant = float(np.median(result.features["dominant_freq_hz"]))
    assert abs(dominant - 440) < 25
    assert "librosa" not in sys.modules


def test_undecodable_file_gives_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda name: None)
    path = tmp_path / "not_audio.wav"
    path.write_bytes(b"this is not audio")
    try:
        core.analyze_media(path, temp_dir=tmp_path / "decode", use_cache=False)
    except ValueError as exc:
        assert "FFmpeg" in str(exc) or "audio" in str(exc).lower()
    else:
        raise AssertionError("expected a ValueError")
