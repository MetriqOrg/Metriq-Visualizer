from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from metriq_visualizer_cache import (
    clear_cache,
    fingerprint_source,
    load_cached_analysis,
    prune_cache,
    save_cached_analysis,
)
from metriq_visualizer_core import analysis_from_table_file


class CacheTests(unittest.TestCase):
    def test_cache_round_trip_sets_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "data.csv"
            source.write_text("time,a,b\n0,1,4\n1,2,5\n2,3,6\n", encoding="utf-8")
            analysis = analysis_from_table_file(source)
            fingerprint = fingerprint_source(source)
            cache_root = root / "cache"
            cache_path = save_cached_analysis(analysis, fingerprint, root=cache_root)
            self.assertTrue(cache_path.is_file())
            loaded = load_cached_analysis(source, fingerprint, root=cache_root)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertTrue(loaded.metadata["cache_hit"])
            self.assertEqual(set(loaded.features), set(analysis.features))
            self.assertEqual(clear_cache(root=cache_root), 1)

    def test_middle_content_change_invalidates_sample_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "large.bin"
            path.write_bytes(b"a" * (1024 * 1024))
            original_stat = path.stat()
            first = fingerprint_source(path)
            with path.open("r+b") as handle:
                handle.seek(512 * 1024)
                handle.write(b"different-middle-block")
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            second = fingerprint_source(path)
            self.assertEqual(first.size, second.size)
            self.assertEqual(first.modified_ns, second.modified_ns)
            self.assertNotEqual(first.sample_hash, second.sample_hash)

    def test_prune_removes_old_entries_to_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(3):
                target = root / f"{index}.npz"
                target.write_bytes(bytes([index]) * 100)
                os.utime(target, (index + 1, index + 1))
            removed = prune_cache(root=root, max_bytes=150)
            self.assertEqual(removed, 2)
            self.assertEqual(len(list(root.glob("*.npz"))), 1)


if __name__ == "__main__":
    unittest.main()
