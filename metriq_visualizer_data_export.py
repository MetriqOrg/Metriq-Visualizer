# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Scientific/educational data export for analyzed features and mapped geometry."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from metriq_visualizer_atomic import atomic_destination
from metriq_visualizer_core import AnalysisResult, GeometryResult

DATA_EXPORT_SCHEMA = "metriq.analysis-data"
DATA_EXPORT_VERSION = 1


def _columns(analysis: AnalysisResult, geometry: GeometryResult | None) -> tuple[list[str], list[np.ndarray]]:
    names = ["time_seconds", "source_frame"]
    arrays: list[np.ndarray] = [
        np.asarray(analysis.times, dtype=np.float64),
        np.arange(analysis.times.size, dtype=np.float64),
    ]
    for name in sorted(analysis.features):
        if name in {"time", "frame"}:
            continue
        names.append(name)
        arrays.append(np.asarray(analysis.features[name], dtype=np.float64))
    if geometry is not None:
        length = analysis.times.size
        mapped = {
            "mapped_x": np.full(length, np.nan),
            "mapped_y": np.full(length, np.nan),
            "mapped_z": np.full(length, np.nan),
            "mapped_color": np.full(length, np.nan),
            "mapped_size": np.full(length, np.nan),
            "included_in_geometry": np.zeros(length),
        }
        indices = np.asarray(geometry.source_indices_full, dtype=np.int64)
        valid = (indices >= 0) & (indices < length)
        indices = indices[valid]
        for key, values in (
            ("mapped_x", geometry.x_full),
            ("mapped_y", geometry.y_full),
            ("mapped_z", geometry.z_full),
            ("mapped_color", geometry.color_full),
            ("mapped_size", geometry.size_full),
        ):
            mapped[key][indices] = np.asarray(values, dtype=np.float64)[valid]
        mapped["included_in_geometry"][indices] = 1.0
        for key, values in mapped.items():
            names.append(key)
            arrays.append(values)
    aligned = []
    target = analysis.times.size
    for values in arrays:
        vector = np.asarray(values, dtype=np.float64).reshape(-1)
        if vector.size == target:
            aligned.append(vector)
        elif vector.size == 1:
            aligned.append(np.full(target, vector[0]))
        else:
            old = np.linspace(0, 1, max(1, vector.size))
            new = np.linspace(0, 1, target)
            aligned.append(np.interp(new, old, vector) if vector.size else np.zeros(target))
    return names, aligned


def export_analysis_csv(path: str | Path, analysis: AnalysisResult, geometry: GeometryResult | None = None) -> Path:
    output = Path(path).expanduser()
    if output.suffix.lower() != ".csv":
        output = output.with_suffix(".csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    names, arrays = _columns(analysis, geometry)
    with atomic_destination(output) as temporary, temporary.open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(names)
        for row_index in range(analysis.times.size):
            row: list[Any] = []
            for values in arrays:
                value = values[row_index]
                row.append("" if not np.isfinite(value) else f"{float(value):.10g}")
            writer.writerow(row)
    return output.resolve()


def export_analysis_npz(path: str | Path, analysis: AnalysisResult, geometry: GeometryResult | None = None) -> Path:
    output = Path(path).expanduser()
    if output.suffix.lower() != ".npz":
        output = output.with_suffix(".npz")
    output.parent.mkdir(parents=True, exist_ok=True)
    names, arrays = _columns(analysis, geometry)
    metadata = {
        "schema": DATA_EXPORT_SCHEMA,
        "schema_version": DATA_EXPORT_VERSION,
        "source_path": str(analysis.source_path),
        "source_kind": analysis.source_kind,
        "duration": analysis.duration,
        "columns": names,
        "mapping_formulas": dict(geometry.formulas) if geometry is not None else {},
    }
    payload = {f"column_{index:04d}": values for index, values in enumerate(arrays)}
    payload["__metadata__"] = np.asarray(json.dumps(metadata, separators=(",", ":")))
    with atomic_destination(output, suffix=".tmp.npz") as temporary:
        np.savez_compressed(temporary, **payload)
    return output.resolve()


__all__ = ["DATA_EXPORT_SCHEMA", "DATA_EXPORT_VERSION", "export_analysis_csv", "export_analysis_npz"]
