# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Toolkit-neutral visual preparation helpers.

The desktop application and the creator export renderer use these small,
well-tested transformations instead of duplicating normalization, path
smoothing, radius, and camera-bound calculations.  The module intentionally
has no Qt dependency so it remains usable in notebooks and headless exports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

EMPTY_FLOAT = np.empty(0, dtype=np.float32)
EMPTY_RGBA = np.empty((0, 4), dtype=np.float32)


def finite_vector(values: Any) -> np.ndarray:
    """Return a one-dimensional finite ``float32`` array.

    NaN and infinite values are replaced with zero.  Scalars become a
    one-element vector; empty or missing values remain empty.
    """

    if values is None:
        return EMPTY_FLOAT.copy()
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if not array.size:
        return EMPTY_FLOAT.copy()
    return np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def normalize_values(values: Any, mode: str = "minmax") -> np.ndarray:
    """Normalize values using ``minmax``, ``zscore``, or ``raw`` mode."""

    array = finite_vector(values).astype(np.float64)
    if not array.size:
        return EMPTY_FLOAT.copy()
    normalized_mode = str(mode or "minmax").strip().lower().replace("-", "")
    if normalized_mode in {"raw", "none"}:
        result = array
    elif normalized_mode in {"z", "zscore", "standard"}:
        center = float(np.mean(array))
        spread = float(np.std(array))
        result = (array - center) / (spread if spread > 1e-12 else 1.0)
    else:
        low = float(np.min(array))
        high = float(np.max(array))
        result = (array - low) / (high - low) if high - low > 1e-12 else np.zeros_like(array)
    return np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


def smooth_curve(values: Any, window: int = 5) -> np.ndarray:
    """Edge-preserving moving-average smoothing for one-dimensional data."""

    array = finite_vector(values)
    if array.size < 3:
        return array
    size = max(1, min(int(window), int(array.size)))
    if size <= 1:
        return array.copy()
    kernel = np.ones(size, dtype=np.float64) / float(size)
    left = size // 2
    right = size - 1 - left
    padded = np.pad(array.astype(np.float64), (left, right), mode="edge")
    return np.convolve(padded, kernel, mode="valid").astype(np.float32)


def compute_tube_radii(
    size_values: Any,
    *,
    base_radius: float = 0.02,
    scale: float = 1.0,
    follow_size: bool = True,
    taper: float = 0.0,
) -> np.ndarray:
    """Build stable per-point tube radii for line/tube rendering."""

    values = finite_vector(size_values)
    if not values.size:
        return EMPTY_FLOAT.copy()
    if follow_size:
        normalized = normalize_values(values, "minmax").astype(np.float64)
        radii = float(base_radius) * float(scale) * (0.35 + 1.65 * normalized)
    else:
        radii = np.full(values.size, float(base_radius) * float(scale), dtype=np.float64)
    taper_amount = min(1.0, max(0.0, float(taper)))
    if taper_amount > 0.0 and values.size > 1:
        edge = np.minimum(np.linspace(0.0, 1.0, values.size), np.linspace(1.0, 0.0, values.size)) * 2.0
        radii *= (1.0 - taper_amount) + taper_amount * np.sqrt(np.clip(edge, 0.0, 1.0))
    return np.maximum(radii, 1e-6).astype(np.float32)


def prepare_color_mapping(values: Any, colormap: str = "viridis") -> tuple[np.ndarray, np.ndarray, str]:
    """Return normalized scalar values and RGBA colors from Matplotlib.

    The fallback palette is deterministic when Matplotlib is unavailable.
    """

    normalized = normalize_values(values, "minmax")
    if not normalized.size:
        return EMPTY_FLOAT.copy(), EMPTY_RGBA.copy(), str(colormap or "viridis")
    name = str(colormap or "viridis")
    try:
        from matplotlib import colormaps

        cmap = colormaps.get_cmap(name)
        rgba = np.asarray(cmap(normalized), dtype=np.float32)
        resolved = str(getattr(cmap, "name", name))
    except Exception:  # pragma: no cover - only used in minimal installations
        t = normalized.astype(np.float32)
        rgba = np.column_stack((0.05 + 0.25 * t, 0.35 + 0.55 * t, 0.65 + 0.30 * t, np.ones_like(t)))
        resolved = "metriq-fallback"
    return normalized.astype(np.float32), rgba.astype(np.float32), resolved


def path_segments(points: Any) -> np.ndarray:
    """Return adjacent 3D point pairs as an ``(N-1, 2, 3)`` array."""

    array = np.asarray(points, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != 3 or array.shape[0] < 2:
        return np.empty((0, 2, 3), dtype=np.float32)
    return np.stack((array[:-1], array[1:]), axis=1)


def geometry_limits(points: Any, *, padding: float = 0.08) -> dict[str, tuple[float, float]]:
    """Compute padded X/Y/Z limits that remain valid for flat datasets."""

    array = np.asarray(points, dtype=np.float64)
    if array.ndim != 2 or array.shape[1] != 3 or not array.size:
        return {"x": (-1.0, 1.0), "y": (-1.0, 1.0), "z": (-1.0, 1.0)}
    result: dict[str, tuple[float, float]] = {}
    pad_fraction = max(0.0, float(padding))
    for index, axis in enumerate(("x", "y", "z")):
        values = np.nan_to_num(array[:, index], nan=0.0, posinf=0.0, neginf=0.0)
        low = float(np.min(values))
        high = float(np.max(values))
        span = high - low
        if span <= 1e-12:
            span = max(1.0, abs(low) * 0.1)
            low -= span * 0.5
            high += span * 0.5
        pad = span * pad_fraction
        result[axis] = (low - pad, high + pad)
    return result


def zoom_limits(
    base_limits: Mapping[str, tuple[float, float]],
    zoom: float,
) -> dict[str, tuple[float, float]]:
    """Scale axis limits around their centers; larger zoom means closer view."""

    factor = max(0.05, float(zoom))
    result: dict[str, tuple[float, float]] = {}
    for axis in ("x", "y", "z"):
        low, high = base_limits.get(axis, (-1.0, 1.0))
        center = (float(low) + float(high)) * 0.5
        half = max(1e-9, (float(high) - float(low)) * 0.5 / factor)
        result[axis] = (center - half, center + half)
    return result


__all__ = [
    "EMPTY_FLOAT",
    "EMPTY_RGBA",
    "compute_tube_radii",
    "finite_vector",
    "geometry_limits",
    "normalize_values",
    "path_segments",
    "prepare_color_mapping",
    "smooth_curve",
    "zoom_limits",
]
