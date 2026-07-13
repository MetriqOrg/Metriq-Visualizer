from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from metriq_visualizer_core import analysis_from_table_file, build_geometry
from metriq_visualizer_data_export import export_analysis_csv, export_analysis_npz


class DataExportTests(unittest.TestCase):
    def test_csv_and_npz_include_mapped_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.csv"
            source.write_text("time,a,b\n0,1,4\n1,2,3\n2,4,2\n3,8,1\n", encoding="utf-8")
            analysis = analysis_from_table_file(source)
            geometry = build_geometry(analysis, "a", "b", "pc1", "a+b", "a", max_points=3)

            csv_path = export_analysis_csv(root / "analysis", analysis, geometry)
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(len(rows), analysis.times.size + 1)
            self.assertIn("mapped_x", rows[0])
            self.assertIn("included_in_geometry", rows[0])

            npz_path = export_analysis_npz(root / "analysis_arrays", analysis, geometry)
            with np.load(npz_path, allow_pickle=False) as archive:
                metadata = json.loads(str(archive["__metadata__"].item()))
                self.assertEqual(metadata["schema"], "metriq.analysis-data")
                self.assertIn("mapped_z", metadata["columns"])
                self.assertEqual(metadata["mapping_formulas"]["x"], "a")
                self.assertEqual(archive["column_0000"].size, analysis.times.size)


if __name__ == "__main__":
    unittest.main()
