# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import matplotlib
import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

from metriq_visualizer_core import GeometryResult


@dataclass
class TrailVisualState:
    visible_idx: np.ndarray
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    point_rgba: np.ndarray
    point_sizes: np.ndarray
    segments: np.ndarray
    segment_rgba: np.ndarray
    segment_widths: np.ndarray
    comet_segments: np.ndarray
    comet_rgba: np.ndarray
    comet_widths: np.ndarray
    head_idx: int
    head_point: tuple[float, float, float]
    head_rgba: np.ndarray
    head_halo_rgba: np.ndarray
    head_size: float
    head_flash_rgba: np.ndarray
    head_flash_size: float


@dataclass
class TubeMeshData:
    vertices: np.ndarray
    faces: np.ndarray
    face_colors: np.ndarray


@dataclass
class PathRunSamples:
    points: np.ndarray
    colors: np.ndarray | None = None
    widths: np.ndarray | None = None
    times: np.ndarray | None = None
    radii: np.ndarray | None = None


EMPTY_SEGMENTS = np.empty((0, 2, 3), dtype=np.float64)
EMPTY_RGBA = np.empty((0, 4), dtype=np.float64)
EMPTY_FLOAT = np.empty(0, dtype=np.float64)
EMPTY_INT = np.empty(0, dtype=int)


def _mirror_endpoint(anchor: np.ndarray, neighbor: np.ndarray) -> np.ndarray:
    return np.asarray(anchor, dtype=np.float64) * 2.0 - np.asarray(neighbor, dtype=np.float64)


def _tj(ti: float, pi: np.ndarray, pj: np.ndarray, alpha: float = 0.5) -> float:
    dist = float(np.linalg.norm(np.asarray(pj, dtype=np.float64) - np.asarray(pi, dtype=np.float64)))
    return float(ti + max(dist, 1e-9) ** float(alpha))


def _centripetal_catmull_rom_segment(
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    p3: np.ndarray,
    t_values: np.ndarray,
    alpha: float = 0.5,
) -> np.ndarray:
    p0 = np.asarray(p0, dtype=np.float64)
    p1 = np.asarray(p1, dtype=np.float64)
    p2 = np.asarray(p2, dtype=np.float64)
    p3 = np.asarray(p3, dtype=np.float64)
    t_values = np.asarray(t_values, dtype=np.float64).reshape(-1)
    if t_values.size == 0:
        return np.empty((0, p1.size), dtype=np.float64)

    t0 = 0.0
    t1 = _tj(t0, p0, p1, alpha=alpha)
    t2 = _tj(t1, p1, p2, alpha=alpha)
    t3 = _tj(t2, p2, p3, alpha=alpha)

    if t2 - t1 < 1e-8:
        return p1[None, :] + (p2 - p1)[None, :] * t_values[:, None]

    def _blend(pa: np.ndarray, pb: np.ndarray, ta: float, tb: float, t: np.ndarray) -> np.ndarray:
        denom = max(tb - ta, 1e-8)
        t_col = t[:, None]
        return ((tb - t_col) / denom) * pa[None, :] + ((t_col - ta) / denom) * pb[None, :]

    t_eval = t1 + (t2 - t1) * t_values
    a1 = _blend(p0, p1, t0, t1, t_eval)
    a2 = _blend(p1, p2, t1, t2, t_eval)
    a3 = _blend(p2, p3, t2, t3, t_eval)
    b1 = ((t2 - t_eval)[:, None] / max(t2 - t0, 1e-8)) * a1 + ((t_eval - t0)[:, None] / max(t2 - t0, 1e-8)) * a2
    b2 = ((t3 - t_eval)[:, None] / max(t3 - t1, 1e-8)) * a2 + ((t_eval - t1)[:, None] / max(t3 - t1, 1e-8)) * a3
    c = ((t2 - t_eval)[:, None] / max(t2 - t1, 1e-8)) * b1 + ((t_eval - t1)[:, None] / max(t2 - t1, 1e-8)) * b2
    return c


def _effective_curve_samples(point_count: int, requested_samples: int, max_curve_points: int) -> int:
    requested = max(1, int(requested_samples))
    if point_count <= 2 or max_curve_points <= 0:
        return requested
    per_segment_cap = max(1, int((int(max_curve_points) - 1) / max(1, point_count - 1)))
    return max(1, min(requested, per_segment_cap))


def _interpolate_attribute(start: np.ndarray, end: np.ndarray, t_values: np.ndarray) -> np.ndarray:
    start = np.asarray(start, dtype=np.float64)
    end = np.asarray(end, dtype=np.float64)
    t_values = np.asarray(t_values, dtype=np.float64)
    if start.ndim == 0:
        return start + (end - start) * t_values
    return start[None, ...] + (end - start)[None, ...] * t_values[:, None]


def _resample_path_run(
    points: np.ndarray,
    *,
    colors: np.ndarray | None = None,
    widths: np.ndarray | None = None,
    times: np.ndarray | None = None,
    radii: np.ndarray | None = None,
    curve_mode: str = "Straight",
    curve_samples: int = 4,
    max_curve_points: int = 12000,
) -> PathRunSamples:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    count = pts.shape[0]
    attrs: dict[str, np.ndarray | None] = {
        "colors": None if colors is None else np.asarray(colors, dtype=np.float64),
        "widths": None if widths is None else np.asarray(widths, dtype=np.float64).reshape(-1),
        "times": None if times is None else np.asarray(times, dtype=np.float64).reshape(-1),
        "radii": None if radii is None else np.asarray(radii, dtype=np.float64).reshape(-1),
    }
    if count == 0:
        return PathRunSamples(points=np.empty((0, 3), dtype=np.float64), **attrs)

    smooth = str(curve_mode).strip().lower() not in {"", "straight", "linear", "off", "none"}
    effective_samples = _effective_curve_samples(count, curve_samples, max_curve_points)
    if not smooth or count < 3 or effective_samples <= 1:
        return PathRunSamples(points=pts.copy(), **attrs)

    out_points: list[np.ndarray] = []
    out_colors: list[np.ndarray] | None = [] if attrs["colors"] is not None else None
    out_widths: list[np.ndarray] | None = [] if attrs["widths"] is not None else None
    out_times: list[np.ndarray] | None = [] if attrs["times"] is not None else None
    out_radii: list[np.ndarray] | None = [] if attrs["radii"] is not None else None

    t_values = np.linspace(0.0, 1.0, num=effective_samples, endpoint=False, dtype=np.float64)
    last = count - 1
    for seg_idx in range(last):
        p1 = pts[seg_idx]
        p2 = pts[seg_idx + 1]
        p0 = pts[seg_idx - 1] if seg_idx > 0 else _mirror_endpoint(p1, p2)
        p3 = pts[seg_idx + 2] if seg_idx + 2 <= last else _mirror_endpoint(p2, p1)
        out_points.append(_centripetal_catmull_rom_segment(p0, p1, p2, p3, t_values))
        if out_colors is not None:
            out_colors.append(_interpolate_attribute(attrs["colors"][seg_idx], attrs["colors"][seg_idx + 1], t_values))
        if out_widths is not None:
            out_widths.append(_interpolate_attribute(attrs["widths"][seg_idx], attrs["widths"][seg_idx + 1], t_values))
        if out_times is not None:
            out_times.append(_interpolate_attribute(attrs["times"][seg_idx], attrs["times"][seg_idx + 1], t_values))
        if out_radii is not None:
            out_radii.append(_interpolate_attribute(attrs["radii"][seg_idx], attrs["radii"][seg_idx + 1], t_values))

    out_points.append(pts[-1][None, :])
    if out_colors is not None:
        out_colors.append(attrs["colors"][-1][None, :])
    if out_widths is not None:
        out_widths.append(np.array([attrs["widths"][-1]], dtype=np.float64))
    if out_times is not None:
        out_times.append(np.array([attrs["times"][-1]], dtype=np.float64))
    if out_radii is not None:
        out_radii.append(np.array([attrs["radii"][-1]], dtype=np.float64))

    return PathRunSamples(
        points=np.vstack(out_points).astype(np.float64, copy=False),
        colors=None if out_colors is None else np.vstack(out_colors).astype(np.float64, copy=False),
        widths=None if out_widths is None else np.concatenate(out_widths).astype(np.float64, copy=False),
        times=None if out_times is None else np.concatenate(out_times).astype(np.float64, copy=False),
        radii=None if out_radii is None else np.concatenate(out_radii).astype(np.float64, copy=False),
    )


def _segments_from_path_runs(path_runs: list[PathRunSamples]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    segment_parts: list[np.ndarray] = []
    color_parts: list[np.ndarray] = []
    width_parts: list[np.ndarray] = []
    time_parts: list[np.ndarray] = []

    for run in path_runs:
        pts = np.asarray(run.points, dtype=np.float64).reshape(-1, 3)
        if pts.shape[0] < 2:
            continue
        segment_parts.append(np.stack([pts[:-1], pts[1:]], axis=1))

        if run.colors is not None and len(run.colors) == pts.shape[0]:
            seg_colors = 0.5 * (np.asarray(run.colors[:-1], dtype=np.float64) + np.asarray(run.colors[1:], dtype=np.float64))
        else:
            seg_colors = np.tile(np.array([[1.0, 1.0, 1.0, 1.0]], dtype=np.float64), (pts.shape[0] - 1, 1))
        color_parts.append(seg_colors)

        if run.widths is not None and len(run.widths) == pts.shape[0]:
            seg_widths = 0.5 * (np.asarray(run.widths[:-1], dtype=np.float64) + np.asarray(run.widths[1:], dtype=np.float64))
        else:
            seg_widths = np.ones(pts.shape[0] - 1, dtype=np.float64)
        width_parts.append(seg_widths)

        if run.times is not None and len(run.times) == pts.shape[0]:
            seg_times = np.asarray(run.times[1:], dtype=np.float64)
        else:
            seg_times = np.zeros(pts.shape[0] - 1, dtype=np.float64)
        time_parts.append(seg_times)

    if not segment_parts:
        return EMPTY_SEGMENTS.copy(), EMPTY_RGBA.copy(), EMPTY_FLOAT.copy(), EMPTY_FLOAT.copy()

    return (
        np.concatenate(segment_parts, axis=0).astype(np.float64, copy=False),
        np.concatenate(color_parts, axis=0).astype(np.float64, copy=False),
        np.concatenate(width_parts, axis=0).astype(np.float64, copy=False),
        np.concatenate(time_parts, axis=0).astype(np.float64, copy=False),
    )


def prepare_color_mapping(color_values: np.ndarray, colormap: str) -> tuple[Normalize, np.ndarray, ScalarMappable]:
    values = np.asarray(color_values, dtype=np.float64).reshape(-1)
    finite_values = values[np.isfinite(values)]
    if finite_values.size == 0:
        vmin, vmax = 0.0, 1.0
    else:
        vmin = float(np.percentile(finite_values, 2.0))
        vmax = float(np.percentile(finite_values, 98.0))
        if abs(vmax - vmin) < 1e-12:
            vmin = float(np.min(finite_values))
            vmax = float(np.max(finite_values))
        if abs(vmax - vmin) < 1e-12:
            vmax = vmin + 1.0

    cmap = matplotlib.colormaps.get_cmap(colormap)
    norm = Normalize(vmin=vmin, vmax=vmax, clip=True)
    rgba = np.asarray(cmap(norm(np.nan_to_num(values, nan=vmin))), dtype=np.float64)
    scalar_map = ScalarMappable(norm=norm, cmap=cmap)
    scalar_map.set_array([])
    return norm, rgba, scalar_map


def compute_axis_limits(
    x_values: np.ndarray,
    y_values: np.ndarray,
    z_values: np.ndarray,
    low_pct: float = 1.0,
    high_pct: float = 99.0,
    pad_frac: float = 0.12,
) -> dict[str, tuple[float, float]]:
    limits: dict[str, tuple[float, float]] = {}
    for axis, arr in (("x", x_values), ("y", y_values), ("z", z_values)):
        values = np.asarray(arr, dtype=np.float64).reshape(-1)
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            limits[axis] = (-1.0, 1.0)
            continue
        lo = float(np.percentile(finite, low_pct))
        hi = float(np.percentile(finite, high_pct))
        if abs(hi - lo) < 1e-9:
            lo = float(np.min(finite))
            hi = float(np.max(finite))
        span = hi - lo
        if span < 1e-9:
            span = 1.0
        pad = span * pad_frac
        limits[axis] = (lo - pad, hi + pad)
    return limits


def zoom_limits(base_limits: Dict[str, Tuple[float, float]], zoom: float) -> dict[str, tuple[float, float]]:
    zoom = max(0.05, float(zoom))
    adjusted: dict[str, tuple[float, float]] = {}
    for axis, (lo, hi) in base_limits.items():
        center = 0.5 * (lo + hi)
        half_span = 0.5 * (hi - lo) / zoom
        if half_span < 1e-9:
            half_span = 0.5
        adjusted[axis] = (center - half_span, center + half_span)
    return adjusted


def latest_time_index(times: np.ndarray, target_time: float, active_mask: np.ndarray | None = None) -> int:
    if times.size == 0:
        return 0
    idx = int(np.searchsorted(times, target_time, side="right") - 1)
    idx = max(0, min(idx, int(times.size - 1)))
    if active_mask is None:
        return idx
    mask = np.asarray(active_mask, dtype=bool).reshape(-1)
    if mask.size != times.size or not np.any(mask):
        return idx
    active_before = np.flatnonzero(mask[: idx + 1])
    if active_before.size > 0:
        return int(active_before[-1])
    active_after = np.flatnonzero(mask[idx + 1 :])
    if active_after.size > 0:
        return int(idx + 1 + active_after[0])
    return idx


def _downsample_indices(indices: np.ndarray, max_points: int) -> np.ndarray:
    if indices.size <= max_points or max_points <= 0:
        return indices
    keep = np.linspace(0, indices.size - 1, num=max_points, dtype=int)
    return indices[keep]


def visible_geometry_indices(
    times: np.ndarray,
    current_time: float,
    history_mode: str,
    point_lifespan: float,
    max_points: int,
    active_mask: np.ndarray | None = None,
) -> np.ndarray:
    if times.size == 0:
        return EMPTY_INT

    if history_mode == "Full static":
        indices = np.arange(times.size, dtype=int)
        if active_mask is not None:
            mask = np.asarray(active_mask, dtype=bool).reshape(-1)
            if mask.size == times.size:
                indices = indices[mask]
        return _downsample_indices(indices, max_points)

    end = int(np.searchsorted(times, current_time, side="right"))
    if end <= 0:
        return EMPTY_INT

    if history_mode == "Cumulative reveal":
        start = 0
    else:
        lifespan = max(0.05, float(point_lifespan))
        start_time = max(0.0, float(current_time) - lifespan)
        start = int(np.searchsorted(times, start_time, side="left"))

    if end <= start:
        return EMPTY_INT

    indices = np.arange(start, end, dtype=int)
    if active_mask is not None:
        mask = np.asarray(active_mask, dtype=bool).reshape(-1)
        if mask.size == times.size:
            indices = indices[mask[indices]]
    return _downsample_indices(indices, max_points)


def _segment_keep_mask(indices: np.ndarray) -> np.ndarray:
    if indices.size <= 1:
        return np.zeros(0, dtype=bool)
    diffs = np.diff(np.asarray(indices, dtype=int))
    positive = diffs[diffs > 0]
    if positive.size == 0:
        return np.ones(diffs.size, dtype=bool)
    typical = max(1, int(np.median(positive)))
    threshold = max(2, typical * 3)
    return diffs <= threshold


def _make_segments(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    if x.size <= 1:
        return EMPTY_SEGMENTS.copy()
    pts = np.column_stack([x, y, z]).astype(np.float64, copy=False)
    return np.stack([pts[:-1], pts[1:]], axis=1)


def compute_trail_visual_state(
    geom: GeometryResult,
    rgba_full: np.ndarray,
    current_time: float,
    base_alpha: float,
    history_mode: str,
    point_lifespan: float,
    fade_curve: float,
    max_points: int,
    connect_lines: bool,
    line_width: float,
    comet_duration: float,
    flash_duration: float,
    curve_mode: str = "Straight",
    curve_samples: int = 4,
) -> TrailVisualState:
    active_mask = getattr(geom, "active_mask_full", None)
    if active_mask is not None:
        active_mask = np.asarray(active_mask, dtype=bool).reshape(-1)
        if active_mask.size != geom.times_full.size:
            active_mask = None

    visible_idx = visible_geometry_indices(
        geom.times_full,
        current_time=current_time,
        history_mode=history_mode,
        point_lifespan=point_lifespan,
        max_points=max_points,
        active_mask=active_mask,
    )

    if visible_idx.size > 0:
        x = geom.x_full[visible_idx]
        y = geom.y_full[visible_idx]
        z = geom.z_full[visible_idx]
        point_rgba = np.asarray(rgba_full[visible_idx], dtype=np.float64).copy()
        point_sizes = np.asarray(geom.size_display_full[visible_idx], dtype=np.float64).copy()

        if history_mode == "Trail fade":
            lifespan = max(0.05, float(point_lifespan))
            age = np.clip(current_time - geom.times_full[visible_idx], 0.0, lifespan)
            fade = np.clip(1.0 - age / lifespan, 0.0, 1.0)
            fade = fade ** max(0.05, float(fade_curve))
            point_rgba[:, 3] = np.clip(base_alpha * fade, 0.0, 1.0)
            point_sizes *= 0.52 + 0.98 * np.sqrt(fade)
        else:
            fade = np.ones(visible_idx.shape[0], dtype=np.float64)
            point_rgba[:, 3] = float(base_alpha)

        segment_times = EMPTY_FLOAT.copy()
        if connect_lines and visible_idx.size > 1 and float(line_width) > 0.01:
            line_point_rgba = point_rgba.copy()
            line_point_rgba[:, 3] = np.clip(line_point_rgba[:, 3] * (0.44 + 0.36 * np.clip(fade, 0.0, 1.0)), 0.0, 1.0)
            line_point_widths = float(line_width) * (0.55 + 0.85 * np.power(np.clip(fade, 0.0, 1.0), 0.65))
            curve_point_budget = max(512, min(12000, max(512, int(max_points)) * max(1, int(curve_samples))))
            path_runs: list[PathRunSamples] = []
            local_times = geom.times_full[visible_idx]
            for run in _split_contiguous_runs(visible_idx):
                run = np.asarray(run, dtype=int)
                if run.size < 2:
                    continue
                path_runs.append(
                    _resample_path_run(
                        np.column_stack([x[run], y[run], z[run]]),
                        colors=line_point_rgba[run],
                        widths=line_point_widths[run],
                        times=local_times[run],
                        curve_mode=curve_mode,
                        curve_samples=curve_samples,
                        max_curve_points=curve_point_budget,
                    )
                )
            if path_runs:
                segments, segment_rgba, segment_widths, segment_times = _segments_from_path_runs(path_runs)
            else:
                segments = EMPTY_SEGMENTS.copy()
                segment_rgba = EMPTY_RGBA.copy()
                segment_widths = EMPTY_FLOAT.copy()
                segment_times = EMPTY_FLOAT.copy()
        else:
            segments = EMPTY_SEGMENTS.copy()
            segment_rgba = EMPTY_RGBA.copy()
            segment_widths = EMPTY_FLOAT.copy()
            segment_times = EMPTY_FLOAT.copy()
    else:
        x = y = z = EMPTY_FLOAT.copy()
        point_rgba = EMPTY_RGBA.copy()
        point_sizes = EMPTY_FLOAT.copy()
        segments = EMPTY_SEGMENTS.copy()
        segment_rgba = EMPTY_RGBA.copy()
        segment_widths = EMPTY_FLOAT.copy()
        segment_times = EMPTY_FLOAT.copy()

    head_idx = latest_time_index(geom.times_full, current_time, active_mask=active_mask)
    hx = float(geom.x_full[head_idx])
    hy = float(geom.y_full[head_idx])
    hz = float(geom.z_full[head_idx])

    head_rgba = np.asarray(rgba_full[head_idx], dtype=np.float64).copy()
    head_rgba[3] = 1.0
    head_halo_rgba = head_rgba.copy()
    head_halo_rgba[:3] = np.clip(0.45 * head_halo_rgba[:3] + 0.62, 0.0, 1.0)
    head_halo_rgba[3] = 0.18
    head_size = float(geom.size_display_full[head_idx] * 1.38 + 36.0)

    flash_age = max(0.0, float(current_time) - float(geom.times_full[head_idx]))
    if flash_duration <= 1e-6:
        flash_strength = 0.0
    else:
        flash_strength = max(0.0, 1.0 - flash_age / max(1e-6, float(flash_duration)))
        flash_strength = flash_strength * flash_strength
    head_flash_rgba = np.array([1.0, 1.0, 1.0, 0.95 * flash_strength], dtype=np.float64)
    head_flash_size = head_size * (1.55 + 1.35 * flash_strength)

    if segments.shape[0] > 0 and comet_duration > 1e-6:
        if segment_times.size > 0:
            seg_age = np.clip(float(current_time) - segment_times, 0.0, None)
            mask = seg_age <= float(comet_duration)
            if np.any(mask):
                comet_segments = segments[mask]
                comet_strength = np.clip(1.0 - seg_age[mask] / float(comet_duration), 0.0, 1.0)
                comet_strength = comet_strength ** 1.35
                comet_rgba = np.ones((comet_segments.shape[0], 4), dtype=np.float64)
                comet_rgba[:, 3] = 0.90 * comet_strength
                comet_widths = float(line_width) * (1.45 + 2.45 * comet_strength)
            else:
                comet_segments = EMPTY_SEGMENTS.copy()
                comet_rgba = EMPTY_RGBA.copy()
                comet_widths = EMPTY_FLOAT.copy()
        else:
            comet_segments = EMPTY_SEGMENTS.copy()
            comet_rgba = EMPTY_RGBA.copy()
            comet_widths = EMPTY_FLOAT.copy()
    else:
        comet_segments = EMPTY_SEGMENTS.copy()
        comet_rgba = EMPTY_RGBA.copy()
        comet_widths = EMPTY_FLOAT.copy()

    return TrailVisualState(
        visible_idx=visible_idx,
        x=x,
        y=y,
        z=z,
        point_rgba=point_rgba,
        point_sizes=point_sizes,
        segments=segments,
        segment_rgba=segment_rgba,
        segment_widths=segment_widths,
        comet_segments=comet_segments,
        comet_rgba=comet_rgba,
        comet_widths=comet_widths,
        head_idx=head_idx,
        head_point=(hx, hy, hz),
        head_rgba=head_rgba,
        head_halo_rgba=head_halo_rgba,
        head_size=head_size,
        head_flash_rgba=head_flash_rgba,
        head_flash_size=head_flash_size,
    )


def compute_tube_radii(
    point_sizes: np.ndarray,
    max_span: float,
    radius_scale: float = 1.0,
    follow_point_size: bool = True,
    taper: float = 0.0,
) -> np.ndarray:
    sizes = np.asarray(point_sizes, dtype=np.float64).reshape(-1)
    if sizes.size == 0:
        return EMPTY_FLOAT.copy()

    scale = max(0.01, float(radius_scale))
    span = max(1e-6, float(max_span))
    base_radius = span * (0.0056 * scale)

    if follow_point_size:
        finite = sizes[np.isfinite(sizes)]
        ref = float(np.percentile(finite, 88.0)) if finite.size > 0 else 1.0
        ref = max(ref, 1e-6)
        norm = np.clip(np.nan_to_num(sizes / ref, nan=0.0, posinf=1.8, neginf=0.0), 0.05, 1.8)
        radii = base_radius * (0.35 + 0.85 * norm)
    else:
        radii = np.full(sizes.shape, base_radius, dtype=np.float64)

    taper_amount = float(np.clip(taper, 0.0, 0.95))
    if taper_amount > 1e-6 and radii.size > 1:
        progress = np.linspace(0.0, 1.0, num=radii.size, dtype=np.float64)
        profile = np.clip((1.0 - taper_amount) + taper_amount * progress, 0.1, 1.5)
        radii *= profile

    return np.maximum(radii, span * 0.00035)


def _normalize_vector(vec: np.ndarray) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float64)
    norm = float(np.linalg.norm(arr))
    if norm < 1e-12:
        return np.zeros_like(arr, dtype=np.float64)
    return arr / norm


def _rotate_vector(vec: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    axis = _normalize_vector(axis)
    if float(np.linalg.norm(axis)) < 1e-12:
        return np.asarray(vec, dtype=np.float64)
    vec = np.asarray(vec, dtype=np.float64)
    cos_a = float(np.cos(angle))
    sin_a = float(np.sin(angle))
    return vec * cos_a + np.cross(axis, vec) * sin_a + axis * np.dot(axis, vec) * (1.0 - cos_a)


def _compute_path_frames(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    n = pts.shape[0]
    if n < 2:
        return None

    diffs = np.diff(pts, axis=0)
    seg_norms = np.linalg.norm(diffs, axis=1)
    valid = seg_norms > 1e-10
    if not np.any(valid):
        return None

    tangents = np.zeros((n, 3), dtype=np.float64)
    first_valid = int(np.flatnonzero(valid)[0])
    last_valid = int(np.flatnonzero(valid)[-1])

    tangents[0] = _normalize_vector(diffs[first_valid])
    tangents[-1] = _normalize_vector(diffs[last_valid])
    for idx in range(1, n - 1):
        prev_vec = diffs[idx - 1] if valid[idx - 1] else tangents[idx - 1]
        next_vec = diffs[idx] if valid[idx] else prev_vec
        tangent = _normalize_vector(_normalize_vector(prev_vec) + _normalize_vector(next_vec))
        if float(np.linalg.norm(tangent)) < 1e-12:
            tangent = _normalize_vector(next_vec if float(np.linalg.norm(next_vec)) > 1e-12 else prev_vec)
        tangents[idx] = tangent

    for idx in range(n):
        if float(np.linalg.norm(tangents[idx])) < 1e-12:
            if idx > 0 and float(np.linalg.norm(tangents[idx - 1])) > 1e-12:
                tangents[idx] = tangents[idx - 1]
            else:
                tangents[idx] = tangents[0]

    ref = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    if abs(float(np.dot(tangents[0], ref))) > 0.85:
        ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    if abs(float(np.dot(tangents[0], ref))) > 0.85:
        ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)

    normal0 = _normalize_vector(np.cross(tangents[0], ref))
    if float(np.linalg.norm(normal0)) < 1e-12:
        normal0 = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    binormal0 = _normalize_vector(np.cross(tangents[0], normal0))
    normal0 = _normalize_vector(np.cross(binormal0, tangents[0]))

    normals = np.zeros((n, 3), dtype=np.float64)
    binormals = np.zeros((n, 3), dtype=np.float64)
    normals[0] = normal0
    binormals[0] = binormal0

    for idx in range(1, n):
        prev_t = tangents[idx - 1]
        curr_t = tangents[idx]
        axis = np.cross(prev_t, curr_t)
        axis_norm = float(np.linalg.norm(axis))
        if axis_norm < 1e-10:
            normal = normals[idx - 1]
        else:
            axis /= axis_norm
            dot = float(np.clip(np.dot(prev_t, curr_t), -1.0, 1.0))
            angle = float(np.arctan2(axis_norm, dot))
            normal = _rotate_vector(normals[idx - 1], axis, angle)
        binormal = _normalize_vector(np.cross(curr_t, normal))
        if float(np.linalg.norm(binormal)) < 1e-12:
            binormal = binormals[idx - 1]
        normal = _normalize_vector(np.cross(binormal, curr_t))
        normals[idx] = normal
        binormals[idx] = binormal

    return tangents, normals, binormals


def _split_contiguous_runs(visible_idx: np.ndarray) -> list[np.ndarray]:
    indices = np.asarray(visible_idx, dtype=int).reshape(-1)
    if indices.size == 0:
        return []
    if indices.size == 1:
        return [np.array([0], dtype=int)]

    diffs = np.diff(indices)
    positive = diffs[diffs > 0]
    typical = max(1, int(np.median(positive))) if positive.size > 0 else 1
    threshold = max(2, typical * 3)
    split_points = np.flatnonzero(diffs > threshold) + 1
    local = np.arange(indices.size, dtype=int)
    return [chunk for chunk in np.split(local, split_points) if chunk.size > 0]


def build_tube_mesh(
    pos: np.ndarray,
    rgba: np.ndarray,
    radii: np.ndarray,
    *,
    visible_idx: np.ndarray | None = None,
    sides: int = 12,
    curve_mode: str = "Straight",
    curve_samples: int = 4,
    max_curve_points: int = 12000,
) -> TubeMeshData | None:
    points = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
    colors = np.asarray(rgba, dtype=np.float64).reshape(-1, 4)
    radii = np.asarray(radii, dtype=np.float64).reshape(-1)
    if points.shape[0] < 2 or colors.shape[0] != points.shape[0] or radii.shape[0] != points.shape[0]:
        return None

    sides = int(np.clip(int(sides), 3, 48))
    runs = _split_contiguous_runs(np.arange(points.shape[0], dtype=int) if visible_idx is None else visible_idx)
    vertices_parts: list[np.ndarray] = []
    faces_parts: list[np.ndarray] = []
    color_parts: list[np.ndarray] = []
    vertex_offset = 0

    theta = np.linspace(0.0, 2.0 * np.pi, num=sides, endpoint=False, dtype=np.float64)

    for run in runs:
        run = np.asarray(run, dtype=int)
        if run.size < 2:
            continue
        sampled = _resample_path_run(
            points[run],
            colors=colors[run],
            radii=np.maximum(radii[run], 1e-9),
            curve_mode=curve_mode,
            curve_samples=curve_samples,
            max_curve_points=max_curve_points,
        )
        pts = sampled.points
        cols = colors[run] if sampled.colors is None else sampled.colors
        rad = np.maximum(radii[run], 1e-9) if sampled.radii is None else np.maximum(sampled.radii, 1e-9)
        if pts.shape[0] < 2:
            continue
        frames = _compute_path_frames(pts)
        if frames is None:
            continue
        _t, normals, binormals = frames
        rings = []
        for idx in range(pts.shape[0]):
            circle = (np.cos(theta)[:, None] * normals[idx][None, :] + np.sin(theta)[:, None] * binormals[idx][None, :])
            ring = pts[idx][None, :] + rad[idx] * circle
            rings.append(ring)
        rings_arr = np.stack(rings, axis=0)
        vertices = rings_arr.reshape(-1, 3)

        faces = []
        face_colors = []
        for seg_idx in range(pts.shape[0] - 1):
            seg_color = np.clip(0.5 * (cols[seg_idx] + cols[seg_idx + 1]), 0.0, 1.0)
            for side_idx in range(sides):
                a = seg_idx * sides + side_idx
                b = seg_idx * sides + (side_idx + 1) % sides
                c = (seg_idx + 1) * sides + side_idx
                d = (seg_idx + 1) * sides + (side_idx + 1) % sides
                faces.append((a, c, b))
                faces.append((b, c, d))
                face_colors.append(seg_color)
                face_colors.append(seg_color)

        if not faces:
            continue
        vertices_parts.append(vertices.astype(np.float32, copy=False))
        faces_parts.append(np.asarray(faces, dtype=np.int32) + vertex_offset)
        color_parts.append(np.asarray(face_colors, dtype=np.float32))
        vertex_offset += vertices.shape[0]

    if not vertices_parts or not faces_parts:
        return None

    return TubeMeshData(
        vertices=np.vstack(vertices_parts).astype(np.float32, copy=False),
        faces=np.vstack(faces_parts).astype(np.int32, copy=False),
        face_colors=np.vstack(color_parts).astype(np.float32, copy=False),
    )
