# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Professional three-dimensional geometry viewport and export renderer.

This module deliberately keeps Metriq Visualizer's geometry in a real
Matplotlib 3D scene.  The desktop viewport and the off-screen export renderer
share the same scene model so the interactive view and final video do not
quietly diverge into different visual products.
"""

from __future__ import annotations

import math
from contextlib import suppress
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection
from PIL import Image
from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedLayout, QWidget

from metriq_visualizer_core import AnalysisResult, GeometryResult
from metriq_visualizer_visuals import compute_tube_radii, geometry_limits

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
except ImportError:  # pragma: no cover - old Matplotlib compatibility
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg  # type: ignore[no-redef]


class PerformanceFigureCanvas(FigureCanvasQTAgg):
    """QtAgg canvas with an optional live-only high-DPI ratio ceiling.

    A Retina-class 2x canvas contains four times as many raster pixels as the
    same logical viewport at 1x.  Capping the ratio during playback is a
    substantial workload reduction while leaving widget geometry, interaction,
    idle refinement, and offscreen export unchanged.
    """

    def __init__(self, figure: Figure) -> None:
        self._pixel_ratio_cap: float | None = None
        super().__init__(figure)

    @property
    def pixel_ratio_cap(self) -> float | None:
        return self._pixel_ratio_cap

    def set_pixel_ratio_cap(self, cap: float | None) -> bool:
        normalized = None if cap is None else max(1.0, float(cap))
        if normalized == self._pixel_ratio_cap:
            return False
        self._pixel_ratio_cap = normalized
        self._update_pixel_ratio()
        return True

    def _update_pixel_ratio(self) -> None:
        ratio = float(self.devicePixelRatioF() or 1.0)
        if self._pixel_ratio_cap is not None:
            ratio = min(ratio, self._pixel_ratio_cap)
        if self._set_device_pixel_ratio(ratio):
            self.resizeEvent(QResizeEvent(self.size(), self.size()))


FIGURE_BACKGROUND = "#070b11"
AXIS_BACKGROUND = "#070b11"
TEXT_COLOR = "#dce8ed"
MUTED_TEXT = "#849aa5"
GRID_COLOR = (0.32, 0.43, 0.48, 0.22)
PANE_FACE = (0.035, 0.055, 0.075, 0.14)
PANE_EDGE = (0.47, 0.62, 0.68, 0.28)
GHOST_COLOR = (0.35, 0.52, 0.58, 0.16)
HEAD_EDGE = (0.33, 0.95, 0.77, 1.0)
HEAD_FILL = (0.86, 1.0, 0.96, 1.0)
HEAD_HALO = (0.16, 0.92, 0.68, 0.16)
EMPTY_SEGMENTS = np.empty((0, 2, 3), dtype=np.float64)
EMPTY_RGBA = np.empty((0, 4), dtype=np.float64)


@dataclass(frozen=True, slots=True)
class ScenePalette:
    figure_background: str
    axis_background: str
    text: str
    muted_text: str
    grid: tuple[float, float, float, float]
    pane_face: tuple[float, float, float, float]
    pane_edge: tuple[float, float, float, float]
    ghost: tuple[float, float, float, float]
    head_edge: tuple[float, float, float, float]
    placeholder_border: str


DARK_SCENE_PALETTE = ScenePalette(
    figure_background=FIGURE_BACKGROUND,
    axis_background=AXIS_BACKGROUND,
    text=TEXT_COLOR,
    muted_text=MUTED_TEXT,
    grid=GRID_COLOR,
    pane_face=PANE_FACE,
    pane_edge=PANE_EDGE,
    ghost=GHOST_COLOR,
    head_edge=HEAD_EDGE,
    placeholder_border="#263847",
)
LIGHT_SCENE_PALETTE = ScenePalette(
    figure_background="#f4f7f9",
    axis_background="#f8fafb",
    text="#132832",
    muted_text="#526a76",
    grid=(0.22, 0.34, 0.39, 0.20),
    pane_face=(0.82, 0.90, 0.93, 0.22),
    pane_edge=(0.25, 0.39, 0.44, 0.32),
    ghost=(0.13, 0.39, 0.39, 0.18),
    head_edge=(0.02, 0.45, 0.35, 1.0),
    placeholder_border="#b7c6cd",
)


def scene_palette(theme_name: str | None) -> ScenePalette:
    return LIGHT_SCENE_PALETTE if str(theme_name or "").strip().casefold() == "light" else DARK_SCENE_PALETTE


@dataclass(slots=True)
class TrailState:
    """Materialized visual state for one playback time."""

    visible_idx: np.ndarray
    points: np.ndarray
    point_rgba: np.ndarray
    point_sizes: np.ndarray
    segments: np.ndarray
    segment_rgba: np.ndarray
    segment_widths: np.ndarray
    comet_segments: np.ndarray
    comet_rgba: np.ndarray
    comet_widths: np.ndarray
    tube_faces: np.ndarray
    tube_rgba: np.ndarray
    head_index: int
    head_point: np.ndarray
    head_rgba: np.ndarray
    head_halo_rgba: np.ndarray
    head_flash_rgba: np.ndarray
    head_size: float
    head_flash_size: float


def _finite_points(geometry: GeometryResult) -> np.ndarray:
    points = np.column_stack((geometry.x_full, geometry.y_full, geometry.z_full)).astype(np.float64, copy=False)
    return np.nan_to_num(points, nan=0.0, posinf=0.0, neginf=0.0)


def _finite_plot_points(geometry: GeometryResult) -> np.ndarray:
    points = np.column_stack((geometry.x_plot, geometry.y_plot, geometry.z_plot)).astype(np.float64, copy=False)
    return np.nan_to_num(points, nan=0.0, posinf=0.0, neginf=0.0)


def _path_segments(points: np.ndarray, source_indices: np.ndarray) -> np.ndarray:
    chunks: list[np.ndarray] = []
    for run in _contiguous_runs(source_indices):
        if run.size >= 2:
            chunks.append(np.stack((points[run[:-1]], points[run[1:]]), axis=1))
    return np.concatenate(chunks, axis=0) if chunks else EMPTY_SEGMENTS.copy()


def _downsample(indices: np.ndarray, maximum: int) -> np.ndarray:
    maximum = int(maximum)
    if maximum <= 0 or indices.size <= maximum:
        return indices
    positions = np.linspace(0, indices.size - 1, maximum, dtype=np.int64)
    return indices[np.unique(positions)]


def _head_index(times: np.ndarray, current_time: float) -> int:
    """Return the latest time-aligned index without scanning every frame.

    Analysis and geometry timestamps are monotonic by construction.  Using
    ``searchsorted`` avoids two full-array masks on every live viewport draw.
    """

    values = np.asarray(times, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return 0
    index = int(np.searchsorted(values, float(current_time) + 1e-9, side="right") - 1)
    if index >= 0:
        return min(index, values.size - 1)
    return 0


def _visible_indices(
    times: np.ndarray,
    current_time: float,
    history_mode: str,
    point_lifespan: float,
    maximum: int,
) -> np.ndarray:
    values = np.asarray(times, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return np.empty(0, dtype=np.int64)
    now = float(current_time)
    stop = int(np.searchsorted(values, now + 1e-9, side="right"))
    head_index = max(0, min(values.size - 1, stop - 1))
    if stop <= 0:
        return np.asarray([head_index], dtype=np.int64)
    mode = str(history_mode or "Trail fade").casefold()
    if "full" in mode:
        start, stop = 0, values.size
    elif "cumulative" in mode:
        start = 0
    else:
        lifespan = max(0.02, float(point_lifespan))
        start = int(np.searchsorted(values, now - lifespan, side="left"))
        if start >= stop:
            start = max(0, stop - 1)
    indices = np.arange(start, stop, dtype=np.int64)
    indices = _downsample(indices, maximum)
    if head_index not in indices:
        if maximum > 0 and indices.size >= maximum:
            replace = int(np.argmin(np.abs(indices - head_index)))
            indices = indices.copy()
            indices[replace] = head_index
        else:
            indices = np.append(indices, head_index)
        indices = np.unique(indices)
    return indices


def _contiguous_segment_mask(source_indices: np.ndarray) -> np.ndarray:
    indices = np.asarray(source_indices, dtype=np.int64).reshape(-1)
    if indices.size <= 1:
        return np.empty(0, dtype=bool)
    differences = np.diff(indices)
    positive = differences[differences > 0]
    typical = max(1, int(np.median(positive))) if positive.size else 1
    return differences <= max(2, typical * 3)


def _contiguous_runs(source_indices: np.ndarray) -> list[np.ndarray]:
    """Return local-index runs without bridging filtered temporal gaps."""

    indices = np.asarray(source_indices, dtype=np.int64).reshape(-1)
    if indices.size == 0:
        return []
    mask = _contiguous_segment_mask(indices)
    breaks = np.flatnonzero(~mask) + 1 if mask.size else np.empty(0, dtype=np.int64)
    return [run for run in np.split(np.arange(indices.size, dtype=np.int64), breaks) if run.size]


def _catmull_rom(
    points: np.ndarray,
    colors: np.ndarray,
    widths: np.ndarray,
    detail: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate a path while preserving aligned colors and widths."""

    values = np.asarray(points, dtype=np.float64)
    rgba = np.asarray(colors, dtype=np.float64)
    size_values = np.asarray(widths, dtype=np.float64).reshape(-1)
    detail = max(1, int(detail))
    if values.shape[0] < 4 or detail <= 1:
        return values, rgba, size_values
    # Avoid pathological export meshes on very long paths.
    max_output = 6000
    detail = max(1, min(detail, max_output // max(1, values.shape[0] - 1)))
    if detail <= 1:
        return values, rgba, size_values
    padded = np.vstack((values[0], values, values[-1]))
    padded_rgba = np.vstack((rgba[0], rgba, rgba[-1]))
    padded_widths = np.concatenate(([size_values[0]], size_values, [size_values[-1]]))
    out_points: list[np.ndarray] = []
    out_rgba: list[np.ndarray] = []
    out_widths: list[float] = []
    for index in range(1, padded.shape[0] - 2):
        p0, p1, p2, p3 = padded[index - 1 : index + 3]
        c1, c2 = padded_rgba[index], padded_rgba[index + 1]
        w1, w2 = padded_widths[index], padded_widths[index + 1]
        for step in range(detail):
            t = step / detail
            t2 = t * t
            t3 = t2 * t
            point = 0.5 * (
                (2.0 * p1)
                + (-p0 + p2) * t
                + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
            )
            out_points.append(point)
            out_rgba.append((1.0 - t) * c1 + t * c2)
            out_widths.append(float((1.0 - t) * w1 + t * w2))
    out_points.append(values[-1])
    out_rgba.append(rgba[-1])
    out_widths.append(float(size_values[-1]))
    return np.asarray(out_points), np.asarray(out_rgba), np.asarray(out_widths)


def interpolate_spline(
    points: np.ndarray,
    colors: np.ndarray,
    widths: np.ndarray,
    detail: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Public, aligned Catmull-Rom path interpolation used by both live renderers."""

    return _catmull_rom(points, colors, widths, detail)


def _tube_faces(
    points: np.ndarray,
    rgba: np.ndarray,
    radii: np.ndarray,
    *,
    sides: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Build parallel-transport tube faces for a contiguous 3D path."""

    values = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    colors = np.asarray(rgba, dtype=np.float64).reshape(-1, 4)
    radius_values = np.asarray(radii, dtype=np.float64).reshape(-1)
    if values.shape[0] < 2 or colors.shape[0] != values.shape[0] or radius_values.size != values.shape[0]:
        return np.empty((0, 4, 3), dtype=np.float64), EMPTY_RGBA.copy()
    sides = int(np.clip(sides, 3, 36))
    # Tube meshes are the expensive path. Preserve the full temporal shape but
    # bound the number of rings for responsive playback and predictable export.
    if values.shape[0] > 700:
        keep = np.unique(np.linspace(0, values.shape[0] - 1, 700, dtype=np.int64))
        values = values[keep]
        colors = colors[keep]
        radius_values = radius_values[keep]

    tangents = np.gradient(values, axis=0)
    tangent_norm = np.linalg.norm(tangents, axis=1, keepdims=True)
    tangents = tangents / np.maximum(tangent_norm, 1e-12)

    normals = np.empty_like(values)
    binormals = np.empty_like(values)
    reference = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(reference, tangents[0]))) > 0.9:
        reference = np.array([0.0, 1.0, 0.0])
    initial = np.cross(tangents[0], reference)
    initial /= max(float(np.linalg.norm(initial)), 1e-12)
    normals[0] = initial
    binormals[0] = np.cross(tangents[0], normals[0])
    binormals[0] /= max(float(np.linalg.norm(binormals[0])), 1e-12)

    for index in range(1, values.shape[0]):
        tangent = tangents[index]
        normal = normals[index - 1] - tangent * float(np.dot(normals[index - 1], tangent))
        norm = float(np.linalg.norm(normal))
        if norm < 1e-9:
            fallback = np.array([1.0, 0.0, 0.0])
            if abs(float(np.dot(fallback, tangent))) > 0.9:
                fallback = np.array([0.0, 1.0, 0.0])
            normal = np.cross(tangent, fallback)
            norm = float(np.linalg.norm(normal))
        normal /= max(norm, 1e-12)
        binormal = np.cross(tangent, normal)
        binormal /= max(float(np.linalg.norm(binormal)), 1e-12)
        normals[index] = normal
        binormals[index] = binormal

    angles = np.linspace(0.0, 2.0 * math.pi, sides, endpoint=False)
    circles = (
        np.cos(angles)[None, :, None] * normals[:, None, :] + np.sin(angles)[None, :, None] * binormals[:, None, :]
    )
    rings = values[:, None, :] + radius_values[:, None, None] * circles

    faces: list[np.ndarray] = []
    face_colors: list[np.ndarray] = []
    for index in range(values.shape[0] - 1):
        color = np.clip(0.5 * (colors[index] + colors[index + 1]), 0.0, 1.0)
        for side in range(sides):
            next_side = (side + 1) % sides
            faces.append(
                np.asarray(
                    (rings[index, side], rings[index + 1, side], rings[index + 1, next_side], rings[index, next_side])
                )
            )
            face_colors.append(color)
    if not faces:
        return np.empty((0, 4, 3), dtype=np.float64), EMPTY_RGBA.copy()
    return np.asarray(faces, dtype=np.float64), np.asarray(face_colors, dtype=np.float64)


def compute_trail_state(
    geometry: GeometryResult,
    current_time: float,
    options: Any,
    *,
    maximum_points: int = 2200,
    prepared_points: np.ndarray | None = None,
) -> TrailState:
    """Compute the display state used by both Qt and export canvases."""

    indices = _visible_indices(
        geometry.times_full,
        current_time,
        str(getattr(options, "history_mode", "Trail fade")),
        float(getattr(options, "point_lifespan", 3.0)),
        maximum_points,
    )
    if indices.size == 0:
        return TrailState(
            visible_idx=indices,
            points=np.empty((0, 3), dtype=np.float64),
            point_rgba=EMPTY_RGBA.copy(),
            point_sizes=np.empty(0, dtype=np.float64),
            segments=EMPTY_SEGMENTS.copy(),
            segment_rgba=EMPTY_RGBA.copy(),
            segment_widths=np.empty(0, dtype=np.float64),
            comet_segments=EMPTY_SEGMENTS.copy(),
            comet_rgba=EMPTY_RGBA.copy(),
            comet_widths=np.empty(0, dtype=np.float64),
            tube_faces=np.empty((0, 4, 3), dtype=np.float64),
            tube_rgba=EMPTY_RGBA.copy(),
            head_index=0,
            head_point=np.zeros(3, dtype=np.float64),
            head_rgba=np.asarray(HEAD_FILL, dtype=np.float64),
            head_halo_rgba=np.asarray(HEAD_HALO, dtype=np.float64),
            head_flash_rgba=np.zeros(4, dtype=np.float64),
            head_size=50.0,
            head_flash_size=0.0,
        )

    if prepared_points is not None:
        candidate = np.asarray(prepared_points, dtype=np.float64)
        all_points = candidate if candidate.shape == (geometry.x_full.size, 3) else _finite_points(geometry)
    else:
        all_points = _finite_points(geometry)
    points = all_points[indices]
    rgba = np.asarray(geometry.rgba_full[indices], dtype=np.float64).copy()
    sizes = np.asarray(geometry.size_full[indices], dtype=np.float64).copy()
    base_alpha = float(np.clip(getattr(options, "base_alpha", 0.82), 0.01, 1.0))
    mode = str(getattr(options, "history_mode", "Trail fade")).casefold()
    if "trail" in mode:
        lifespan = max(0.02, float(getattr(options, "point_lifespan", 3.0)))
        age = np.clip(float(current_time) - geometry.times_full[indices], 0.0, lifespan)
        fade = np.power(np.clip(1.0 - age / lifespan, 0.0, 1.0), max(0.05, float(getattr(options, "fade_curve", 1.35))))
    else:
        fade = np.ones(indices.size, dtype=np.float64)
    rgba[:, 3] = np.clip(base_alpha * fade, 0.0, 1.0)
    point_scale = max(0.02, float(getattr(options, "point_size_scale", 0.4)))
    point_sizes = np.maximum(2.0, (9.0 + 26.0 * sizes) * point_scale * (0.55 + 0.75 * np.sqrt(fade)))

    line_width = max(0.1, float(getattr(options, "line_width", 1.35)))
    connect_lines = bool(getattr(options, "connect_lines", True))
    render_mode = str(getattr(options, "render_mode", "Points + line")).casefold()
    smooth_path = "smooth" in str(getattr(options, "path_curve_mode", "Straight")).casefold()
    curve_detail = max(1, int(getattr(options, "curve_detail", 4)))

    segment_chunks: list[np.ndarray] = []
    segment_color_chunks: list[np.ndarray] = []
    segment_width_chunks: list[np.ndarray] = []
    tube_face_chunks: list[np.ndarray] = []
    tube_color_chunks: list[np.ndarray] = []

    axis_span = float(np.max(np.ptp(all_points, axis=0))) if all_points.size else 1.0
    axis_span = max(axis_span, 1e-6)
    tube_base_radius = axis_span * 0.0065

    if connect_lines:
        source_values = geometry.source_indices_full[indices]
        for run in _contiguous_runs(source_values):
            if run.size < 2:
                continue
            run_points = points[run]
            run_rgba = rgba[run]
            run_widths = np.full(run.size, line_width, dtype=np.float64)
            run_sizes = sizes[run]
            if smooth_path and run.size >= 4:
                run_points, run_rgba, run_widths = _catmull_rom(
                    run_points,
                    run_rgba,
                    run_widths,
                    curve_detail,
                )
                run_sizes = np.interp(
                    np.linspace(0.0, 1.0, run_points.shape[0]),
                    np.linspace(0.0, 1.0, run.size),
                    run_sizes,
                )

            if "tube" in render_mode:
                radii = compute_tube_radii(
                    run_sizes,
                    base_radius=tube_base_radius,
                    scale=max(0.05, float(getattr(options, "tube_radius_scale", 1.0))),
                    follow_size=bool(getattr(options, "tube_follow_size", True)),
                    taper=float(getattr(options, "tube_taper", 0.2)),
                ).astype(np.float64)
                faces, face_rgba = _tube_faces(
                    run_points,
                    run_rgba,
                    radii,
                    sides=int(getattr(options, "tube_sides", 12)),
                )
                if faces.size:
                    tube_face_chunks.append(faces)
                    tube_color_chunks.append(face_rgba)
            else:
                run_segments = np.stack((run_points[:-1], run_points[1:]), axis=1)
                run_segment_rgba = np.clip(0.5 * (run_rgba[:-1] + run_rgba[1:]), 0.0, 1.0)
                run_segment_rgba[:, 3] *= 0.88
                run_segment_widths = np.maximum(0.1, 0.5 * (run_widths[:-1] + run_widths[1:]))
                segment_chunks.append(run_segments)
                segment_color_chunks.append(run_segment_rgba)
                segment_width_chunks.append(run_segment_widths)

    segments = np.concatenate(segment_chunks, axis=0) if segment_chunks else EMPTY_SEGMENTS.copy()
    segment_rgba = np.concatenate(segment_color_chunks, axis=0) if segment_color_chunks else EMPTY_RGBA.copy()
    segment_widths = (
        np.concatenate(segment_width_chunks, axis=0) if segment_width_chunks else np.empty(0, dtype=np.float64)
    )
    tube_faces = np.concatenate(tube_face_chunks, axis=0) if tube_face_chunks else np.empty((0, 4, 3), dtype=np.float64)
    tube_rgba = np.concatenate(tube_color_chunks, axis=0) if tube_color_chunks else EMPTY_RGBA.copy()

    head_index = _head_index(geometry.times_full, current_time)
    head_rgba = np.asarray(geometry.rgba_full[head_index], dtype=np.float64).copy()
    head_rgba[3] = 1.0
    head_matches = np.flatnonzero(indices == head_index)
    head_local = int(head_matches[-1]) if head_matches.size else int(np.argmin(np.abs(indices - head_index)))
    head_size = max(42.0, float(point_sizes[head_local]) * 2.3)

    # The original visualizer's moving comet and impact flash are more than
    # decoration: they make temporal direction legible in a dense 3D field.
    # Build them from the full time-aligned geometry so Trail/Cumulative/Static
    # modes all retain the same playback head semantics.
    comet_segments = EMPTY_SEGMENTS.copy()
    comet_rgba = EMPTY_RGBA.copy()
    comet_widths = np.empty(0, dtype=np.float64)
    comet_duration = max(0.0, float(getattr(options, "comet_duration", 0.45)))
    if connect_lines and comet_duration > 0.0:
        times = np.asarray(geometry.times_full, dtype=np.float64)
        comet_start = int(np.searchsorted(times, float(current_time) - comet_duration - 1e-9, side="left"))
        comet_stop = int(np.searchsorted(times, float(current_time) + 1e-9, side="right"))
        comet_indices = np.arange(comet_start, comet_stop, dtype=np.int64)
        if head_index not in comet_indices:
            comet_indices = np.unique(np.append(comet_indices, head_index))
        comet_indices = _downsample(comet_indices, min(maximum_points, 600))
        comet_segment_chunks: list[np.ndarray] = []
        comet_color_chunks: list[np.ndarray] = []
        comet_width_chunks: list[np.ndarray] = []
        if comet_indices.size >= 2:
            comet_points = all_points[comet_indices]
            comet_colors = np.asarray(geometry.rgba_full[comet_indices], dtype=np.float64).copy()
            comet_sources = geometry.source_indices_full[comet_indices]
            for run in _contiguous_runs(comet_sources):
                if run.size < 2:
                    continue
                run_points = comet_points[run]
                run_colors = comet_colors[run]
                run_times = times[comet_indices[run]]
                run_segments = np.stack((run_points[:-1], run_points[1:]), axis=1)
                segment_times = 0.5 * (run_times[:-1] + run_times[1:])
                progress = np.clip(
                    (segment_times - (float(current_time) - comet_duration)) / max(comet_duration, 1e-9),
                    0.0,
                    1.0,
                )
                colors = np.clip(0.5 * (run_colors[:-1] + run_colors[1:]), 0.0, 1.0)
                # A restrained white lift at the head keeps the accent readable
                # across every colormap without turning it into a neon overlay.
                lift = (0.08 + 0.24 * np.power(progress, 2.0))[:, None]
                colors[:, :3] = np.clip(colors[:, :3] * (1.0 - lift) + lift, 0.0, 1.0)
                colors[:, 3] = np.clip(base_alpha * (0.04 + 0.96 * np.power(progress, 1.65)), 0.0, 1.0)
                widths = line_width * (0.75 + 2.35 * np.power(progress, 1.35))
                comet_segment_chunks.append(run_segments)
                comet_color_chunks.append(colors)
                comet_width_chunks.append(widths)
        if comet_segment_chunks:
            comet_segments = np.concatenate(comet_segment_chunks, axis=0)
            comet_rgba = np.concatenate(comet_color_chunks, axis=0)
            comet_widths = np.concatenate(comet_width_chunks, axis=0)

    flash_duration = max(0.0, float(getattr(options, "flash_duration", 0.18)))
    head_sample_time = float(geometry.times_full[head_index])
    head_age = max(0.0, float(current_time) - head_sample_time)
    flash_phase = float(np.clip(1.0 - head_age / flash_duration, 0.0, 1.0)) if flash_duration > 1e-9 else 0.0
    head_halo_rgba = head_rgba.copy()
    head_halo_rgba[3] = 0.11 + 0.13 * flash_phase
    head_flash_rgba = np.empty(4, dtype=np.float64)
    head_flash_rgba[:3] = np.clip(0.35 * head_rgba[:3] + 0.65, 0.0, 1.0)
    head_flash_rgba[3] = 0.42 * flash_phase * flash_phase
    head_flash_size = head_size * (3.5 + 4.5 * flash_phase) if flash_phase > 0.0 else 0.0
    return TrailState(
        visible_idx=indices,
        points=points,
        point_rgba=rgba,
        point_sizes=point_sizes,
        segments=segments,
        segment_rgba=segment_rgba,
        segment_widths=segment_widths,
        comet_segments=comet_segments,
        comet_rgba=comet_rgba,
        comet_widths=comet_widths,
        tube_faces=tube_faces,
        tube_rgba=tube_rgba,
        head_index=head_index,
        head_point=all_points[head_index],
        head_rgba=head_rgba,
        head_halo_rgba=head_halo_rgba,
        head_flash_rgba=head_flash_rgba,
        head_size=head_size,
        head_flash_size=head_flash_size,
    )


class Matplotlib3DScene:
    """Reusable artist graph for a Matplotlib 3D canvas."""

    def __init__(
        self,
        figure: Figure,
        canvas: Any,
        analysis: AnalysisResult,
        geometry: GeometryResult,
        options: Any,
        *,
        interactive: bool = False,
        theme_name: str = "dark",
    ) -> None:
        self.figure = figure
        self.canvas = canvas
        self.analysis = analysis
        self.geometry = geometry
        self.options = options
        self.interactive = bool(interactive)
        self.theme_name = "light" if str(theme_name).casefold() == "light" else "dark"
        self.palette = scene_palette(self.theme_name)
        self.current_time = 0.0
        self.camera_elev = float(getattr(options, "elev", 24.0))
        self.camera_azim = float(getattr(options, "azim", 35.0))
        self.zoom = float(getattr(options, "zoom", 1.0))
        self._last_applied_view: tuple[float, float] | None = None
        self._last_applied_zoom: float | None = None
        self._colorbar: Any = None
        self._text_artists: list[Any] = []
        preview_points = max(1, int(self.geometry.x_plot.size))
        self.maximum_points = (
            preview_points if self.interactive else max(preview_points, min(12_000, int(self.geometry.x_full.size)))
        )
        self._full_points = _finite_points(geometry)
        self.ax = self.figure.add_subplot(111, projection="3d")
        if self.interactive:
            with suppress(Exception):
                self.ax.disable_mouse_rotation()
        self.base_limits = geometry_limits(self._full_points, padding=0.12)
        self._build_artists()

    def _build_artists(self) -> None:
        self.figure.patch.set_facecolor(self.palette.figure_background)
        self.ax.set_facecolor(self.palette.axis_background)
        self.figure.subplots_adjust(left=0.015, right=0.985, bottom=0.015, top=0.985)
        self._style_axes()

        plot_points = _finite_plot_points(self.geometry)
        ghost_segments = _path_segments(plot_points, self.geometry.source_indices_plot)
        self.ghost_line = Line3DCollection(ghost_segments, colors=[self.palette.ghost], linewidths=0.8, zorder=1)
        self.ax.add_collection3d(self.ghost_line, autolim=False)
        self.trail_collection = Line3DCollection(EMPTY_SEGMENTS, linewidths=[])
        self.ax.add_collection3d(self.trail_collection, autolim=False)
        self.comet_collection = Line3DCollection(EMPTY_SEGMENTS, linewidths=[], zorder=6)
        self.ax.add_collection3d(self.comet_collection, autolim=False)
        self.tube_collection = Poly3DCollection([], linewidths=0.0, edgecolors="none")
        self.ax.add_collection3d(self.tube_collection, autolim=False)
        self.dynamic_scatter = self.ax.scatter([], [], [], s=[], depthshade=False, linewidths=0.0, zorder=5)
        self.head_flash = self.ax.scatter([], [], [], s=[], depthshade=False, linewidths=0.0, zorder=6)
        self.head_halo = self.ax.scatter([], [], [], s=[], depthshade=False, linewidths=0.0, zorder=7)
        self.head_scatter = self.ax.scatter([], [], [], s=[], depthshade=False, linewidths=1.2, zorder=8)
        self.hud_text = self.ax.text2D(
            0.018,
            0.982,
            "",
            transform=self.ax.transAxes,
            color=self.palette.text,
            fontsize=9.5,
            va="top",
            ha="left",
            family="monospace",
        )
        self.help_text = self.ax.text2D(
            0.982,
            0.018,
            "DRAG ORBIT  ·  SCROLL ZOOM" if self.interactive else "METRIQ / 3D",
            transform=self.ax.transAxes,
            color=self.palette.muted_text,
            fontsize=7.5,
            va="bottom",
            ha="right",
            family="monospace",
            alpha=0.72,
        )
        self._update_colorbar()
        self.update_options(self.options, redraw=False)

    def _style_axes(self) -> None:
        # Keep grid and pane decoration below the data artists. The panes stay
        # transparent, so a plane can never cover the visualizer as the camera
        # crosses it.
        self.ax.set_axisbelow(True)
        self.ax.computed_zorder = False
        self.ax.tick_params(colors=self.palette.muted_text, labelsize=7, pad=0)
        self.ax.xaxis.label.set_color(self.palette.text)
        self.ax.yaxis.label.set_color(self.palette.text)
        self.ax.zaxis.label.set_color(self.palette.text)
        for axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
            try:
                axis.pane.set_facecolor((*self.palette.pane_face[:3], 0.0))
                axis.pane.set_edgecolor((*self.palette.pane_edge[:3], 0.0))
                axis.pane.set_alpha(0.0)
                axis._axinfo["grid"]["color"] = self.palette.grid  # noqa: SLF001 - Matplotlib exposes no public 3D grid API
                axis._axinfo["grid"]["linewidth"] = 0.55  # noqa: SLF001
                axis._axinfo["axisline"]["color"] = self.palette.pane_edge  # noqa: SLF001
            except Exception:
                pass
        with suppress(Exception):
            self.ax.set_proj_type("persp", focal_length=0.92)
        with suppress(Exception):
            self.ax.set_box_aspect((1.0, 1.0, 1.0))

    def set_theme(self, theme_name: str, *, draw: bool = True) -> None:
        """Apply the application theme to the complete 3D scene."""

        self.theme_name = "light" if str(theme_name).casefold() == "light" else "dark"
        self.palette = scene_palette(self.theme_name)
        self.figure.patch.set_facecolor(self.palette.figure_background)
        self.ax.set_facecolor(self.palette.axis_background)
        self.hud_text.set_color(self.palette.text)
        self.help_text.set_color(self.palette.muted_text)
        self.ghost_line.set_color([self.palette.ghost])
        self._style_axes()
        for artist in self._text_artists:
            with suppress(Exception):
                artist.set_color(self.palette.text)
        self._update_colorbar()
        if draw:
            self.canvas.draw_idle() if hasattr(self.canvas, "draw_idle") else self.canvas.draw()

    def _formula_label(self, axis: str) -> str:
        formula = str(getattr(self.geometry, "formulas", {}).get(axis, axis.upper())).strip()
        return formula if len(formula) <= 28 else f"{formula[:25]}…"

    def _update_colorbar(self) -> None:
        show = bool(getattr(self.options, "show_colorbar", False))
        if self._colorbar is not None:
            with suppress(Exception):
                self._colorbar.remove()
            self._colorbar = None
        if not show or self.geometry.color_full.size == 0:
            return
        values = np.asarray(self.geometry.color_full, dtype=np.float64)
        finite = values[np.isfinite(values)]
        if finite.size:
            low, high = np.percentile(finite, [1.0, 99.0])
        else:
            low, high = 0.0, 1.0
        if high <= low + 1e-12:
            high = low + 1.0
        try:
            from matplotlib import colormaps

            cmap = colormaps.get_cmap(str(getattr(self.geometry, "colormap", "plasma")))
            scalar = ScalarMappable(norm=Normalize(low, high), cmap=cmap)
            scalar.set_array([])
            self._colorbar = self.figure.colorbar(scalar, ax=self.ax, pad=0.012, shrink=0.62, aspect=24)
            self._colorbar.ax.tick_params(colors=self.palette.muted_text, labelsize=7)
            self._colorbar.outline.set_edgecolor(self.palette.pane_edge)
            self._colorbar.set_label(self._formula_label("color"), color=self.palette.text, fontsize=8)
        except Exception:
            self._colorbar = None

    def update_options(self, options: Any, *, redraw: bool = True) -> None:
        previous_colorbar = bool(getattr(self.options, "show_colorbar", False)) if hasattr(self, "options") else False
        self.options = options
        self.camera_elev = float(getattr(options, "elev", self.camera_elev))
        self.camera_azim = float(getattr(options, "azim", self.camera_azim))
        self.zoom = max(0.05, float(getattr(options, "zoom", self.zoom)))
        show_axes = bool(getattr(options, "show_axes", True))
        self.ax.set_axis_on() if show_axes else self.ax.set_axis_off()
        show_grid = show_axes and bool(getattr(options, "show_grid", True))
        for axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
            with suppress(Exception):
                axis._axinfo["grid"]["linewidth"] = 0.55 if show_grid else 0.0  # noqa: SLF001
        show_labels = bool(getattr(options, "show_axis_labels", True)) and show_axes
        self.ax.set_xlabel(self._formula_label("x") if show_labels else "", labelpad=4, fontsize=8)
        self.ax.set_ylabel(self._formula_label("y") if show_labels else "", labelpad=4, fontsize=8)
        self.ax.set_zlabel(self._formula_label("z") if show_labels else "", labelpad=4, fontsize=8)
        self.ghost_line.set_visible(bool(getattr(options, "ghost_path", False)))
        show_scene_hud = bool(getattr(options, "show_scene_hud", True))
        self.hud_text.set_visible(show_scene_hud)
        self.help_text.set_visible(show_scene_hud)
        if previous_colorbar != bool(getattr(options, "show_colorbar", False)):
            self._update_colorbar()
        self._apply_camera(self.current_time)
        if redraw:
            self.update_time(self.current_time, draw=True)

    def _apply_camera(self, current_time: float) -> None:
        azimuth = self.camera_azim
        # Off-screen export remains deterministic from source time. Interactive
        # autorotation is driven by the viewport's wall-clock camera timer.
        if not self.interactive and bool(getattr(self.options, "autorotate", False)):
            start = float(getattr(self.options, "start_time", 0.0))
            azimuth += max(0.0, float(current_time) - start) * float(getattr(self.options, "rotation_speed", 16.0))
        view = (round(float(self.camera_elev), 6), round(float(azimuth), 6))
        if view != self._last_applied_view:
            self.ax.view_init(elev=view[0], azim=view[1])
            self._last_applied_view = view
        zoom = round(float(self.zoom), 8)
        if zoom != self._last_applied_zoom:
            with suppress(Exception):
                self.ax.set_box_aspect((1.0, 1.0, 1.0), zoom=float(np.clip(self.zoom, 0.2, 5.0)))
            self._last_applied_zoom = zoom

    def set_camera(self, elev: float, azim: float, zoom: float | None = None, *, draw: bool = True) -> None:
        self.camera_elev = float(np.clip(elev, -90.0, 90.0))
        self.camera_azim = float(azim)
        if zoom is not None:
            self.zoom = max(0.05, float(zoom))
        self._apply_camera(self.current_time)
        if draw:
            self.canvas.draw_idle() if hasattr(self.canvas, "draw_idle") else self.canvas.draw()

    def update_time(self, current_time: float, *, draw: bool = True) -> TrailState:
        self.current_time = float(max(0.0, current_time))
        state = compute_trail_state(
            self.geometry,
            self.current_time,
            self.options,
            maximum_points=self.maximum_points,
            prepared_points=self._full_points,
        )
        mode = str(getattr(self.options, "render_mode", "Points + line")).casefold()
        show_points = "points" in mode or "tube" not in mode
        show_line = (
            bool(getattr(self.options, "connect_lines", True)) and "points only" not in mode and "tube" not in mode
        )
        show_tube = "tube" in mode and state.tube_faces.size > 0

        if state.points.size and show_points:
            self.dynamic_scatter._offsets3d = (state.points[:, 0], state.points[:, 1], state.points[:, 2])  # noqa: SLF001
            self.dynamic_scatter.set_sizes(state.point_sizes)
            self.dynamic_scatter.set_facecolors(state.point_rgba)
            self.dynamic_scatter.set_edgecolors(state.point_rgba)
            self.dynamic_scatter.set_visible(True)
        else:
            self.dynamic_scatter._offsets3d = ([], [], [])  # noqa: SLF001
            self.dynamic_scatter.set_visible(False)

        self.trail_collection.set_segments(state.segments if show_line else EMPTY_SEGMENTS)
        self.trail_collection.set_color(state.segment_rgba if show_line else EMPTY_RGBA)
        self.trail_collection.set_linewidth(state.segment_widths if show_line else [])
        self.trail_collection.set_visible(show_line and state.segments.size > 0)

        self.tube_collection.set_verts(state.tube_faces if show_tube else [])
        if show_tube:
            self.tube_collection.set_facecolor(state.tube_rgba)
        self.tube_collection.set_visible(show_tube)

        show_comet = (
            bool(getattr(self.options, "connect_lines", True))
            and "points only" not in mode
            and state.comet_segments.size > 0
        )
        self.comet_collection.set_segments(state.comet_segments if show_comet else EMPTY_SEGMENTS)
        self.comet_collection.set_color(state.comet_rgba if show_comet else EMPTY_RGBA)
        self.comet_collection.set_linewidth(state.comet_widths if show_comet else [])
        self.comet_collection.set_visible(show_comet)

        show_head = bool(getattr(self.options, "show_head_marker", True)) and state.visible_idx.size > 0
        if show_head:
            point = state.head_point
            head_scale = max(0.0, float(getattr(self.options, "head_size_scale", 0.24))) / 0.24
            halo_scale = max(0.0, float(getattr(self.options, "halo_size_scale", 0.45))) / 0.45
            flash_scale = max(0.0, float(getattr(self.options, "flash_size_scale", 0.05))) / 0.05

            self.head_halo._offsets3d = ([point[0]], [point[1]], [point[2]])  # noqa: SLF001
            self.head_halo.set_sizes([state.head_size * 3.4 * halo_scale])
            halo_color = np.asarray(state.head_halo_rgba, dtype=np.float64).reshape(1, 4)
            self.head_halo.set_facecolors(halo_color)
            self.head_halo.set_edgecolors(halo_color)
            self.head_halo.set_visible(halo_scale > 0.0 and float(halo_color[0, 3]) > 0.0)

            self.head_flash._offsets3d = ([point[0]], [point[1]], [point[2]])  # noqa: SLF001
            self.head_flash.set_sizes([state.head_flash_size * flash_scale])
            flash_color = np.asarray(state.head_flash_rgba, dtype=np.float64).reshape(1, 4)
            self.head_flash.set_facecolors(flash_color)
            self.head_flash.set_edgecolors(flash_color)
            self.head_flash.set_visible(
                flash_scale > 0.0 and state.head_flash_size > 0.0 and float(flash_color[0, 3]) > 0.0
            )

            self.head_scatter._offsets3d = ([point[0]], [point[1]], [point[2]])  # noqa: SLF001
            self.head_scatter.set_sizes([state.head_size * head_scale])
            fill = np.asarray(state.head_rgba, dtype=np.float64).reshape(1, 4)
            self.head_scatter.set_facecolors(fill)
            self.head_scatter.set_edgecolors(np.asarray(self.palette.head_edge, dtype=np.float64).reshape(1, 4))
            self.head_scatter.set_visible(head_scale > 0.0)
        else:
            self.head_flash._offsets3d = ([], [], [])  # noqa: SLF001
            self.head_halo._offsets3d = ([], [], [])  # noqa: SLF001
            self.head_scatter._offsets3d = ([], [], [])  # noqa: SLF001
            self.head_flash.set_visible(False)
            self.head_halo.set_visible(False)
            self.head_scatter.set_visible(False)

        self._update_point_labels(state)
        self._apply_camera(self.current_time)
        source_kind = str(getattr(self.analysis, "source_kind", "media")).casefold()
        source_index = 0
        if state.visible_idx.size and self.geometry.source_indices_full.size:
            geometry_index = int(np.clip(state.head_index, 0, self.geometry.source_indices_full.size - 1))
            source_index = int(self.geometry.source_indices_full[geometry_index])
        if source_kind == "table":
            frame_number = source_index + 1 if state.visible_idx.size else 0
            total_rows = max(
                int(self.analysis.times.size),
                int(np.max(self.geometry.source_indices_full)) + 1 if self.geometry.source_indices_full.size else 0,
            )
            self.hud_text.set_text(f"{self.current_time:7.2f} s   ·   ROW {frame_number:,}/{total_rows:,}")
        else:
            dominant = 0.0
            feature = self.analysis.features.get("dominant_frequency")
            if feature is None:
                feature = self.analysis.features.get("dominant_freq_hz")
            if feature is not None and np.asarray(feature).size:
                feature_values = np.asarray(feature).reshape(-1)
                feature_index = int(np.clip(source_index, 0, feature_values.size - 1))
                dominant = float(feature_values[feature_index])
            self.hud_text.set_text(f"{self.current_time:7.2f} s   ·   {dominant:8.1f} Hz")
        if draw:
            self.canvas.draw_idle() if hasattr(self.canvas, "draw_idle") else self.canvas.draw()
        return state

    def _update_point_labels(self, state: TrailState) -> None:
        for artist in self._text_artists:
            with suppress(Exception):
                artist.remove()
        self._text_artists.clear()
        mode = str(getattr(self.options, "point_label_mode", "Off")).casefold()
        if mode == "off" or state.visible_idx.size == 0:
            return
        maximum = max(1, int(getattr(self.options, "max_point_labels", 8)))
        count = min(maximum, state.visible_idx.size)
        local_indices = np.unique(np.linspace(0, state.visible_idx.size - 1, count, dtype=np.int64))
        if "current" in mode or "head" in mode:
            matches = np.flatnonzero(state.visible_idx == state.head_index)
            local_indices = np.asarray(
                [int(matches[-1]) if matches.size else int(np.argmin(np.abs(state.visible_idx - state.head_index)))],
                dtype=np.int64,
            )
        content = str(getattr(self.options, "point_label_content", "Time + Hz")).casefold()
        frequency = self.analysis.features.get("dominant_frequency")
        if frequency is None:
            frequency = self.analysis.features.get("dominant_freq_hz")
        for local in local_indices:
            geometry_index = int(state.visible_idx[local])
            point = state.points[local]
            seconds = float(self.geometry.times_full[geometry_index])
            source_index = (
                int(self.geometry.source_indices_full[geometry_index])
                if geometry_index < self.geometry.source_indices_full.size
                else geometry_index
            )
            hz: float | None = None
            if frequency is not None and np.asarray(frequency).size:
                frequency_values = np.asarray(frequency).reshape(-1)
                hz = float(frequency_values[min(source_index, frequency_values.size - 1)])
            if content == "index":
                label = f"#{source_index + 1:,}"
            elif content == "dominant hz":
                label = f"{hz:.0f} Hz" if hz is not None and np.isfinite(hz) else "— Hz"
            elif content == "time":
                label = f"{seconds:.2f}s"
            elif hz is not None and np.isfinite(hz):
                label = f"{seconds:.2f}s / {hz:.0f} Hz"
            else:
                label = f"{seconds:.2f}s"
            artist = self.ax.text(
                point[0], point[1], point[2], label, color=self.palette.text, fontsize=6.5, alpha=0.82
            )
            self._text_artists.append(artist)

    def camera(self) -> tuple[float, float, float]:
        return float(self.ax.elev), float(self.ax.azim), float(self.zoom)

    def close(self) -> None:
        with suppress(Exception):
            self.figure.clear()


class Matplotlib3DFrameRenderer:
    """Off-screen RGBA renderer used by Export Studio."""

    def __init__(
        self,
        analysis: AnalysisResult,
        geometry: GeometryResult,
        options: Any,
        *,
        width: int,
        height: int,
    ) -> None:
        self.analysis = analysis
        self.geometry = geometry
        self.options = options
        self.width = max(2, int(width))
        self.height = max(2, int(height))
        self.dpi = 100.0
        self.figure = Figure(figsize=(self.width / self.dpi, self.height / self.dpi), dpi=self.dpi)
        self.canvas = FigureCanvasAgg(self.figure)
        self.scene = Matplotlib3DScene(self.figure, self.canvas, analysis, geometry, options, interactive=False)

    def _resize(self, width: int, height: int) -> None:
        width = max(2, int(width))
        height = max(2, int(height))
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self.figure.set_size_inches(width / self.dpi, height / self.dpi, forward=True)

    def update_options(self, options: Any) -> None:
        self.options = options
        self.scene.update_options(options, redraw=False)

    def render_frame(self, current_time: float, *, width: int | None = None, height: int | None = None) -> np.ndarray:
        self._resize(width or self.width, height or self.height)
        self.scene.update_time(current_time, draw=False)
        self.canvas.draw()
        buffer = np.asarray(self.canvas.buffer_rgba(), dtype=np.uint8).copy()
        if buffer.ndim == 1:
            actual_width, actual_height = self.canvas.get_width_height()
            buffer = buffer.reshape(actual_height, actual_width, 4)
        if buffer.shape[:2] != (self.height, self.width):
            buffer = np.asarray(
                Image.fromarray(buffer, mode="RGBA").resize(
                    (self.width, self.height),
                    Image.Resampling.LANCZOS,
                ),
                dtype=np.uint8,
            )
        return np.ascontiguousarray(buffer)

    def close(self) -> None:
        self.scene.close()


class Interactive3DViewport(QWidget):
    """Exact 3D inspection plus a low-latency moving-scene renderer."""

    cameraChanged = Signal(float, float, float)
    interactionStarted = Signal()
    frameRendered = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Import lazily so the realtime module can reuse compute_trail_state
        # without creating an import cycle while this module is initialized.
        from metriq_visualizer_realtime import Realtime3DCanvas

        self.setObjectName("Interactive3DViewport")
        self.setMinimumSize(580, 360)
        self.figure = Figure(figsize=(8.4, 5.8), dpi=100)
        self.canvas = PerformanceFigureCanvas(self.figure)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.canvas.setFocus()
        self.realtime = Realtime3DCanvas(self)
        self.scene: Matplotlib3DScene | None = None
        self.analysis: AnalysisResult | None = None
        self.geometry: GeometryResult | None = None
        self.options: Any = None
        self.theme_name = "dark"
        self._render_pending = False
        self._draw_started_at: float | None = None
        self._live_point_budget = 2200
        self._motion_mode = False
        self._live_mode = False
        self._realtime_drag_active = False
        self._exact_drag_origin: tuple[float, float] | None = None
        self._exact_drag_camera = (24.0, 35.0)
        self._autorotate_emit_elapsed = 0.0

        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.canvas)
        self.stack.addWidget(self.realtime)

        self.placeholder = QLabel(
            "OPEN LOCAL MEDIA OR TABLE DATA\n\nThe three-dimensional field will appear here.",
            self,
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setObjectName("ViewportPlaceholder")
        self._apply_placeholder_theme()
        self.stack.addWidget(self.placeholder)
        self.stack.setCurrentWidget(self.placeholder)

        self.canvas.mpl_connect("button_press_event", self._button_press)
        self.canvas.mpl_connect("motion_notify_event", self._button_move)
        self.canvas.mpl_connect("button_release_event", self._button_release)
        self.canvas.mpl_connect("scroll_event", self._scroll)
        self.canvas.mpl_connect("draw_event", self._draw_completed)
        self.realtime.cameraChanged.connect(self._realtime_camera_changed)
        self.realtime.interactionStarted.connect(self._realtime_interaction_started)
        self.realtime.interactionFinished.connect(self._realtime_interaction_finished)
        self.realtime.frameRendered.connect(self.frameRendered.emit)

        self.autorotate_timer = QTimer(self)
        self.autorotate_timer.setInterval(33)
        self.autorotate_timer.timeout.connect(self._autorotate_tick)
        self.autorotate_clock = QElapsedTimer()

    def _apply_placeholder_theme(self) -> None:
        palette = scene_palette(self.theme_name)
        self.placeholder.setStyleSheet(
            f"background:{palette.figure_background}; color:{palette.muted_text}; "
            f"border:1px solid {palette.placeholder_border}; font-family:monospace; letter-spacing:1px;"
        )

    def set_theme(self, theme_name: str) -> None:
        self.theme_name = "light" if str(theme_name).casefold() == "light" else "dark"
        self._apply_placeholder_theme()
        self.realtime.set_theme(self.theme_name)
        if self.scene is not None:
            self.scene.set_theme(self.theme_name, draw=not self._fast_active())

    def set_render_pixel_ratio_cap(self, cap: float | None) -> bool:
        return self.canvas.set_pixel_ratio_cap(cap)

    def render_pixel_ratio(self) -> float:
        return float(self.canvas.device_pixel_ratio)

    def set_live_point_budget(self, maximum_points: int) -> None:
        self._live_point_budget = max(100, int(maximum_points))
        self.realtime.maximum_points = self._live_point_budget
        if self.scene is not None:
            available = int(self.geometry.x_full.size) if self.geometry is not None else self._live_point_budget
            self.scene.maximum_points = max(100, min(self._live_point_budget, available))

    def _draw_completed(self, _event: Any) -> None:
        self._render_pending = False
        started_at = self._draw_started_at
        self._draw_started_at = None
        if started_at is not None:
            self.frameRendered.emit(max(0.0, (perf_counter() - started_at) * 1000.0))

    def draw_pending(self) -> bool:
        if self._fast_active():
            return False
        return bool(self._render_pending or getattr(self.canvas, "_draw_pending", False))

    def _fast_active(self) -> bool:
        return bool(
            self._motion_mode
            or self._live_mode
            or self._realtime_drag_active
            or (self.options is not None and bool(getattr(self.options, "autorotate", False)))
        )

    def _sync_display_mode(self) -> None:
        fast = self._fast_active()
        if fast and (self.scene is not None or self.realtime.live_active):
            self.stack.setCurrentWidget(self.realtime)
            self.realtime.update()
        elif self.scene is not None:
            self.stack.setCurrentWidget(self.canvas)
            self.scene.update_time(self.scene.current_time, draw=True)
        else:
            self.stack.setCurrentWidget(self.placeholder)
        self._sync_autorotate_timer()

    def _sync_autorotate_timer(self) -> None:
        enabled = bool(
            self.options is not None
            and getattr(self.options, "autorotate", False)
            and (self.scene is not None or self.realtime.live_active)
            and not self._realtime_drag_active
            and self._exact_drag_origin is None
        )
        if enabled and not self.autorotate_timer.isActive():
            self.autorotate_clock.start()
            self._autorotate_emit_elapsed = 0.0
            self.autorotate_timer.start()
        elif not enabled and self.autorotate_timer.isActive():
            self.autorotate_timer.stop()

    def _autorotate_tick(self) -> None:
        if self.options is None or not bool(getattr(self.options, "autorotate", False)):
            self._sync_autorotate_timer()
            return
        if not self.autorotate_clock.isValid():
            self.autorotate_clock.start()
            return
        elapsed = max(0.0, self.autorotate_clock.restart() / 1000.0)
        if elapsed <= 0.0:
            return
        from metriq_visualizer_realtime import advance_azimuth

        elevation, azimuth, zoom = self.camera()
        azimuth = advance_azimuth(azimuth, float(getattr(self.options, "rotation_speed", 16.0)), elapsed)
        self.set_camera(elevation, azimuth, zoom, draw=False, emit=False)
        self._autorotate_emit_elapsed += elapsed
        if self._autorotate_emit_elapsed >= 0.10:
            self._autorotate_emit_elapsed = 0.0
            self.cameraChanged.emit(elevation, azimuth, zoom)

    def set_motion_mode(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._motion_mode:
            return
        self._motion_mode = value
        self._sync_display_mode()

    def set_motion_frame_interval(self, interval_ms: int) -> None:
        """Use the selected live profile's cadence for autorotation too."""

        self.autorotate_timer.setInterval(max(16, min(1_000, int(interval_ms))))

    def set_live_mode(self, enabled: bool) -> None:
        value = bool(enabled)
        if value == self._live_mode:
            return
        self._live_mode = value
        if not value:
            self.realtime.clear_live_trajectory()
        self._sync_display_mode()

    def set_live_trajectory(
        self,
        points: np.ndarray,
        rgba: np.ndarray | None = None,
        sizes: np.ndarray | None = None,
        *,
        options: Any | None = None,
    ) -> None:
        if options is not None:
            self.options = options
            self.realtime.set_options(options, maximum_points=self._live_point_budget)
        self.realtime.set_live_trajectory(points, rgba, sizes)
        self._live_mode = True
        self._sync_display_mode()

    def clear_live_trajectory(self) -> None:
        self.realtime.clear_live_trajectory()
        self._live_mode = False
        self._sync_display_mode()

    def set_scene(self, analysis: AnalysisResult, geometry: GeometryResult, options: Any) -> None:
        if self.scene is not None:
            self.scene.close()
        self.analysis = analysis
        self.geometry = geometry
        self.options = options
        self.figure.clear()
        self.scene = Matplotlib3DScene(
            self.figure,
            self.canvas,
            analysis,
            geometry,
            options,
            interactive=True,
            theme_name=self.theme_name,
        )
        self.scene.maximum_points = max(100, min(self._live_point_budget, int(geometry.x_full.size)))
        self.realtime.set_scene(
            analysis,
            geometry,
            options,
            maximum_points=min(self._live_point_budget, max(100, int(geometry.x_full.size))),
        )
        self.scene.update_time(0.0, draw=False)
        self._sync_display_mode()

    def clear_scene(self, message: str | None = None) -> None:
        if self.scene is not None:
            self.scene.close()
        self.scene = None
        self._render_pending = False
        self._draw_started_at = None
        self.analysis = None
        self.geometry = None
        self.figure.clear()
        self.realtime.clear_scene()
        self._live_mode = False
        self.placeholder.setText(
            message or "OPEN LOCAL MEDIA OR TABLE DATA\n\nThe three-dimensional field will appear here."
        )
        self._sync_display_mode()

    def update_options(self, options: Any, *, draw: bool = True) -> None:
        self.options = options
        self.realtime.set_options(options, maximum_points=self._live_point_budget)
        if self.scene is not None:
            self.scene.update_options(options, redraw=draw and not self._fast_active())
            self.realtime.set_camera(*self.scene.camera(), emit=False)
        self._sync_display_mode()

    def update_time(self, current_time: float, *, draw: bool = True, force: bool = False) -> bool:
        self.realtime.set_time(current_time)
        if self._fast_active():
            return True
        if self.scene is None:
            return False
        if draw and not force and self.draw_pending():
            return False
        if draw:
            self._render_pending = True
            self._draw_started_at = perf_counter()
        self.scene.update_time(current_time, draw=draw)
        return True

    def set_camera(
        self,
        elev: float,
        azim: float,
        zoom: float | None = None,
        *,
        draw: bool = True,
        emit: bool = True,
    ) -> None:
        current_zoom = self.camera()[2] if zoom is None else float(zoom)
        elevation = float(np.clip(elev, -90.0, 90.0))
        azimuth = (float(azim) + 180.0) % 360.0 - 180.0
        current_zoom = float(np.clip(current_zoom, 0.2, 5.0))
        if self.scene is not None:
            self.scene.set_camera(elevation, azimuth, current_zoom, draw=draw and not self._fast_active())
        self.realtime.set_camera(elevation, azimuth, current_zoom, emit=False)
        if emit:
            self.cameraChanged.emit(elevation, azimuth, current_zoom)

    def reset_camera(self) -> None:
        self.set_camera(24.0, 35.0, 1.0, draw=True, emit=True)

    def camera(self) -> tuple[float, float, float]:
        if self.scene is not None:
            return self.scene.camera()
        return self.realtime.camera()

    def _realtime_interaction_started(self) -> None:
        # Keep the low-latency canvas active and temporarily pause automatic
        # camera motion for the whole drag.  The selected autorotate state is
        # preserved and resumes once the pointer is released.
        self._realtime_drag_active = True
        self._sync_autorotate_timer()
        self.interactionStarted.emit()

    def _realtime_interaction_finished(self) -> None:
        self._realtime_drag_active = False
        self._sync_display_mode()

    def _realtime_camera_changed(self, elev: float, azim: float, zoom: float) -> None:
        if self.scene is not None:
            self.scene.set_camera(elev, azim, zoom, draw=False)
        self.cameraChanged.emit(elev, azim, zoom)

    def _button_press(self, event: Any) -> None:
        if self.scene is None or event.inaxes is None or int(getattr(event, "button", 0) or 0) != 1:
            return
        ratio = max(1.0, float(self.canvas.device_pixel_ratio))
        self._exact_drag_origin = (float(event.x) / ratio, float(event.y) / ratio)
        self._exact_drag_camera = (float(self.scene.ax.elev), float(self.scene.ax.azim))
        self._sync_autorotate_timer()
        self.interactionStarted.emit()

    def _button_move(self, event: Any) -> None:
        if self.scene is None or self._exact_drag_origin is None or event.inaxes is None:
            return
        from metriq_visualizer_realtime import camera_after_drag

        ratio = max(1.0, float(self.canvas.device_pixel_ratio))
        x = float(event.x) / ratio
        y = float(event.y) / ratio
        delta_x = x - self._exact_drag_origin[0]
        # Matplotlib event y grows upward; camera_after_drag expects screen-down.
        delta_y = -(y - self._exact_drag_origin[1])
        elevation, azimuth = camera_after_drag(
            self._exact_drag_camera[0],
            self._exact_drag_camera[1],
            delta_x,
            delta_y,
        )
        self.set_camera(elevation, azimuth, self.camera()[2], draw=True, emit=True)

    def _button_release(self, _event: Any) -> None:
        self._exact_drag_origin = None
        self._sync_autorotate_timer()

    def _scroll(self, event: Any) -> None:
        if self.scene is None or event.inaxes is None:
            return
        factor = 1.10 if float(event.step) > 0 else 1.0 / 1.10
        elev, azim, zoom = self.camera()
        self.interactionStarted.emit()
        self.set_camera(elev, azim, zoom * factor, draw=True, emit=True)


class ViewportCommandStrip(QWidget):
    """Small, optional command strip for the 3D viewport."""

    resetRequested = Signal()
    fitRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.mode_label = QLabel("3D SCENE / INTERACTIVE")
        self.mode_label.setObjectName("Eyebrow")
        layout.addWidget(self.mode_label)
        layout.addStretch(1)
        self.reset_button = QPushButton("Reset camera")
        self.reset_button.clicked.connect(self.resetRequested)
        layout.addWidget(self.reset_button)


__all__ = [
    "Interactive3DViewport",
    "Matplotlib3DFrameRenderer",
    "PerformanceFigureCanvas",
    "Matplotlib3DScene",
    "TrailState",
    "ViewportCommandStrip",
    "compute_trail_state",
    "interpolate_spline",
    "scene_palette",
]
