# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Export analyzed features and their mapped geometry as CSV or NPZ."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from metriq_visualizer_atomic import atomic_destination
from metriq_visualizer_core import AnalysisResult, GeometryResult, analyze_media, analysis_from_table_file, build_geometry, is_table_file

DATA_EXPORT_SCHEMA = "metriq.analysis-data"
DATA_EXPORT_VERSION = 1


def _columns(analysis: AnalysisResult, geometry: GeometryResult | None) -> tuple[list[str], list[np.ndarray]]:
    names = ["time_seconds", "source_frame"]
    arrays: list[np.ndarray] = [np.asarray(analysis.times, dtype=np.float64), np.arange(analysis.times.size, dtype=np.float64)]
    for name in sorted(analysis.features):
        if name not in {"time", "frame"}:
            names.append(name); arrays.append(np.asarray(analysis.features[name], dtype=np.float64))
    if geometry is not None:
        n = analysis.times.size
        for name, values in (("mapped_x", geometry.x_full), ("mapped_y", geometry.y_full), ("mapped_z", geometry.z_full),
                             ("mapped_color", geometry.color_full), ("mapped_size", geometry.size_full)):
            names.append(name); arrays.append(np.asarray(values, dtype=np.float64))
        included = np.zeros(n, dtype=np.float64)
        indices = np.asarray(geometry.plot_indices, dtype=np.int64)
        indices = indices[(indices >= 0) & (indices < n)]; included[indices] = 1.0
        names.append("included_in_geometry"); arrays.append(included)
    aligned = []
    for values in arrays:
        vector = np.asarray(values, dtype=np.float64).reshape(-1)
        if vector.size == analysis.times.size:
            aligned.append(vector)
        elif vector.size == 1:
            aligned.append(np.full(analysis.times.size, vector[0]))
        else:
            old = np.linspace(0, 1, max(1, vector.size)); new = np.linspace(0, 1, analysis.times.size)
            aligned.append(np.interp(new, old, vector) if vector.size else np.zeros(analysis.times.size))
    return names, aligned


def export_analysis_csv(path: str | Path, analysis: AnalysisResult, geometry: GeometryResult | None = None) -> Path:
    output = Path(path).expanduser()
    if output.suffix.lower() != ".csv": output = output.with_suffix(".csv")
    names, arrays = _columns(analysis, geometry)
    with atomic_destination(output) as temporary, temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle); writer.writerow(names)
        for index in range(analysis.times.size):
            writer.writerow(["" if not np.isfinite(column[index]) else f"{float(column[index]):.10g}" for column in arrays])
    return output.resolve()


def export_analysis_npz(path: str | Path, analysis: AnalysisResult, geometry: GeometryResult | None = None) -> Path:
    output = Path(path).expanduser()
    if output.suffix.lower() != ".npz": output = output.with_suffix(".npz")
    names, arrays = _columns(analysis, geometry)
    metadata = {"schema": DATA_EXPORT_SCHEMA, "schema_version": DATA_EXPORT_VERSION, "source_path": str(analysis.source_path),
                "source_kind": analysis.source_kind, "duration": analysis.duration, "columns": names,
                "mapping_formulas": dict(geometry.labels) if geometry is not None else {}}
    payload: dict[str, Any] = {f"column_{i:04d}": values for i, values in enumerate(arrays)}
    payload["__metadata__"] = np.asarray(json.dumps(metadata, separators=(",", ":")))
    with atomic_destination(output, suffix=".tmp.npz") as temporary:
        np.savez_compressed(temporary, **payload)
    return output.resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Audio, video, or supported table file")
    parser.add_argument("output", help="Destination .csv or .npz file")
    parser.add_argument("--x", default="pc1"); parser.add_argument("--y", default="pc2"); parser.add_argument("--z", default="pc3")
    parser.add_argument("--color", default="time"); parser.add_argument("--size", default="rms")
    parser.add_argument("--normalize", default="zscore"); parser.add_argument("--max-points", type=int, default=2500)
    args = parser.parse_args(argv)
    source = Path(args.source).expanduser()
    analysis = analysis_from_table_file(source) if is_table_file(source) else analyze_media(source)
    geometry = build_geometry(analysis, args.x, args.y, args.z, args.color, args.size, normalize_mode=args.normalize, max_points=args.max_points)
    suffix = Path(args.output).suffix.lower()
    if suffix == ".npz": result = export_analysis_npz(args.output, analysis, geometry)
    else: result = export_analysis_csv(args.output, analysis, geometry)
    print(result)
    return 0


__all__ = ["DATA_EXPORT_SCHEMA", "DATA_EXPORT_VERSION", "export_analysis_csv", "export_analysis_npz", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
