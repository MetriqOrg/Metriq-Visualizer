from pathlib import Path

import numpy as np
import pytest
import shutil

import metriq_visualizer_cache as cache
from metriq_visualizer_cache import AnalysisSettings, fingerprint_source, load_cached_analysis, prune_cache, save_cached_analysis
from metriq_visualizer_core import analysis_from_table_file
from metriq_visualizer_core import analyze_media
from audio_fixtures import write_fixture


def test_cache_round_trip_and_all_analysis_settings_in_key(tmp_path: Path) -> None:
    source = tmp_path / "data.csv"
    source.write_text("time,a,b\n0,1,4\n1,2,5\n2,3,6\n", encoding="utf-8")
    result = analysis_from_table_file(source)
    fp = fingerprint_source(source)
    root = tmp_path / "cache"
    settings = AnalysisSettings(22050, 2048, 256)
    saved = save_cached_analysis(result, fp, root=root, settings=settings)
    assert load_cached_analysis(source, fp, root=root, settings=settings) is not None
    assert load_cached_analysis(source, fp, root=root, settings=AnalysisSettings(44100, 2048, 256)) is None
    assert load_cached_analysis(source, fp, root=root, settings=AnalysisSettings(22050, 4096, 256)) is None
    assert load_cached_analysis(source, fp, root=root, settings=AnalysisSettings(22050, 2048, 512)) is None
    with np.load(saved, allow_pickle=False) as archive:
        assert archive["times"].size == result.times.size


def test_fingerprint_and_pruning(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * 800000)
    first = fingerprint_source(source)
    with source.open("r+b") as handle:
        handle.seek(400000); handle.write(b"changed")
    assert fingerprint_source(source).sample_hash != first.sample_hash
    root = tmp_path / "cache"
    root.mkdir()
    for index in range(3):
        (root / f"{index}.npz").write_bytes(bytes([index]) * 100)
    assert prune_cache(root=root, max_bytes=150) == 2


def test_public_media_analysis_caches_every_setting_and_source_change(tmp_path, monkeypatch):
    source = tmp_path / "audio.wav"
    write_fixture(source, "tones_noise")
    root = tmp_path / "cache"
    settings = [(22050, 2048, 256), (44100, 2048, 256),
                (22050, 4096, 256), (22050, 2048, 512)]
    results = [analyze_media(source, *setting, cache_root=root,
                             temp_dir=tmp_path / str(index))
               for index, setting in enumerate(settings)]
    assert len(list(root.glob("*.npz"))) == len(settings)

    def unexpected_analysis(*args, **kwargs):
        pytest.fail("A cache hit invoked audio analysis")

    monkeypatch.setattr(cache, "_analyze_media_uncached", unexpected_analysis)
    for setting, original in zip(settings, results):
        loaded = analyze_media(source, *setting, cache_root=root)
        assert loaded.features.keys() == original.features.keys()
        for name in original.features:
            np.testing.assert_array_equal(loaded.features[name], original.features[name])
        np.testing.assert_array_equal(loaded.spectrogram_db, original.spectrogram_db)

    # Both source changes and engine changes must miss the old result.
    monkeypatch.setattr(cache, "ANALYSIS_ENGINE_VERSION", "different-engine")
    assert cache.load_cached_analysis(source, root=root) is None
    monkeypatch.undo()
    write_fixture(source, "silence")
    assert cache.load_cached_analysis(source, root=root) is None
    changed = analyze_media(source, cache_root=root, temp_dir=tmp_path / "changed")
    assert not np.any(changed.features["rms"])


def test_corrupt_media_cache_recovers_and_cache_can_be_disabled(tmp_path, monkeypatch):
    source = tmp_path / "audio.wav"
    write_fixture(source, "tones_noise")
    root = tmp_path / "cache"
    analyze_media(source, cache_root=root, temp_dir=tmp_path / "first")
    saved, = root.glob("*.npz")
    saved.write_bytes(b"not an archive")
    recovered = analyze_media(source, cache_root=root, temp_dir=tmp_path / "recovered")
    loaded = cache.load_cached_analysis(source, root=root)
    np.testing.assert_array_equal(loaded.features["rms"], recovered.features["rms"])
    monkeypatch.setattr(cache, "load_cached_analysis", lambda *a, **k: pytest.fail("Cache was disabled"))
    uncached = analyze_media(source, use_cache=False, temp_dir=tmp_path / "disabled")
    np.testing.assert_array_equal(uncached.features["rms"], recovered.features["rms"])


def test_cached_audio_survives_scratch_cleanup_and_is_pruned(tmp_path):
    source = tmp_path / "audio.wav"
    write_fixture(source, "tones_noise")
    root = tmp_path / "cache"
    scratch = tmp_path / "scratch"
    result = analyze_media(source, cache_root=root, temp_dir=scratch)
    audio_bytes = Path(result.audio_path).read_bytes()
    shutil.rmtree(scratch)
    loaded = analyze_media(source, cache_root=root)
    assert Path(loaded.audio_path).read_bytes() == audio_bytes
    assert Path(loaded.audio_path).parent == root
    # Count the paired WAV towards the budget; removing an entry removes both.
    archive, = root.glob("*.npz")
    assert cache.prune_cache(root=root, max_bytes=archive.stat().st_size) == 1
    assert not list(root.iterdir())
    analyze_media(source, cache_root=root, temp_dir=scratch)
    assert cache.clear_cache(root=root) == 1
    assert not list(root.iterdir())
