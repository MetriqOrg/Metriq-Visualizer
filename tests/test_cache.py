from pathlib import Path

import numpy as np

from metriq_visualizer_cache import AnalysisSettings, fingerprint_source, load_cached_analysis, prune_cache, save_cached_analysis
from metriq_visualizer_core import analysis_from_table_file


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
