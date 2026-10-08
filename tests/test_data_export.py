import csv
import json
from pathlib import Path

import numpy as np

from metriq_visualizer_core import analysis_from_table_file, build_geometry
from metriq_visualizer_data_export import export_analysis_csv, export_analysis_npz, main


def test_csv_npz_and_cli_export_mapped_data(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source.csv"
    source.write_text("time,a,b\n0,1,4\n1,2,3\n2,4,2\n3,8,1\n", encoding="utf-8")
    analysis = analysis_from_table_file(source)
    geometry = build_geometry(analysis, "a", "b", "pc1", "a+b", "a", max_points=3)
    csv_path = export_analysis_csv(tmp_path / "mapped", analysis, geometry)
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == analysis.times.size + 1
    assert "mapped_x" in rows[0] and "included_in_geometry" in rows[0]
    npz_path = export_analysis_npz(tmp_path / "mapped_arrays", analysis, geometry)
    with np.load(npz_path, allow_pickle=False) as archive:
        meta = json.loads(str(archive["__metadata__"].item()))
        assert meta["schema"] == "metriq.analysis-data"
        assert meta["mapping_formulas"]["x"] == "a"
    assert main([str(source), str(tmp_path / "cli.csv"), "--x", "a", "--y", "b", "--z", "pc1", "--size", "a"]) == 0
    assert str(tmp_path / "cli.csv") in capsys.readouterr().out


def test_export_replaces_existing_destination_atomically(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("time,a\n0,1\n1,2\n", encoding="utf-8")
    analysis = analysis_from_table_file(source)
    destination = tmp_path / "mapped.csv"
    destination.write_text("old contents\n", encoding="utf-8")

    result = export_analysis_csv(destination, analysis)

    assert result == destination.resolve()
    with destination.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][:2] == ["time_seconds", "source_frame"]
    assert len(rows) == analysis.times.size + 1
    assert not list(tmp_path.glob("*.tmp"))
