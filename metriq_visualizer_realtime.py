# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Low-latency motion renderer for the interactive Metriq viewport.

The exact Matplotlib 3D scene remains the paused-inspection and export renderer.
This widget reuses the mapped XYZ geometry, camera, trail semantics, spline
state, and motion accents while audio or microphone input is moving. It avoids
rebuilding a CPU-rasterized Matplotlib axes graph on every playback tick.
"""

from __future__ import annotations

import math
from time import perf_counter
from typing import Any

import numpy as np
from PySide6.QtCore import QLineF, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

from metriq_visualizer_3d import compute_trail_state, interpolate_spline, scene_palette
from metriq_visualizer_core import AnalysisResult, GeometryResult


def camera_after_drag(
    elevation: float,
    azimuth: float,
    delta_x: float,
    delta_y: float,
    *,
    sensitivity: float = 0.14,
) -> tuple[float, float]:
    """Return a stable orbit from logical-pixel mouse movement."""

    elev = float(np.clip(float(elevation) - float(delta_y) * sensitivity, -90.0, 90.0))
    azim = (float(azimuth) - float(delta_x) * sensitivity + 180.0) % 360.0 - 180.0
    return elev, azim


def advance_azimuth(azimuth: float, rotation_speed: float, elapsed_seconds: float) -> float:
    """Advance an interactive camera using elapsed wall-clock time."""

    value = float(azimuth) + float(rotation_speed) * max(0.0, float(elapsed_seconds))
    return (value + 180.0) % 360.0 - 180.0


def _grid_segments(
    x_plane: float = -1.0,
    y_plane: float = -1.0,
    z_plane: float = -1.0,
    *,
    divisions: int = 4,
) -> np.ndarray:
    """Return three unit-cube grid planes placed at the supplied back edges."""

    values = np.linspace(-1.0, 1.0, max(2, int(divisions)) + 1)
    segments: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for value in values:
        # XY floor, XZ rear plane, and YZ side plane.
        segments.extend(
            [
                ((-1.0, value, z_plane), (1.0, value, z_plane)),
                ((value, -1.0, z_plane), (value, 1.0, z_plane)),
                ((-1.0, y_plane, value), (1.0, y_plane, value)),
                ((value, y_plane, -1.0), (value, y_plane, 1.0)),
                ((x_plane, -1.0, value), (x_plane, 1.0, value)),
                ((x_plane, value, -1.0), (x_plane, value, 1.0)),
            ]
        )
    return np.asarray(segments, dtype=np.float64)


def _axis_segments(x_plane: float, y_plane: float, z_plane: float) -> np.ndarray:
    """Return axes anchored to the camera-facing back corner."""

    return np.asarray(
        [
            ((-1.0, y_plane, z_plane), (1.0, y_plane, z_plane)),
            ((x_plane, -1.0, z_plane), (x_plane, 1.0, z_plane)),
            ((x_plane, y_plane, -1.0), (x_plane, y_plane, 1.0)),
        ],
        dtype=np.float64,
    )


def _qcolor(rgba: np.ndarray | tuple[float, ...], *, alpha_scale: float = 1.0) -> QColor:
    values = np.asarray(rgba, dtype=np.float64).reshape(-1)
    if values.size < 3:
        values = np.asarray((0.2, 0.9, 0.7, 1.0), dtype=np.float64)
    alpha = float(values[3]) if values.size > 3 else 1.0
    return QColor.fromRgbF(
        float(np.clip(values[0], 0.0, 1.0)),
        float(np.clip(values[1], 0.0, 1.0)),
        float(np.clip(values[2], 0.0, 1.0)),
        float(np.clip(alpha * alpha_scale, 0.0, 1.0)),
    )


def media_path_segments(
    points: np.ndarray,
    rgba: np.ndarray,
    *,
    line_width: float,
    smooth: bool,
    detail: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build the responsive media path, interpolating every smooth trail.

    ``compute_trail_state`` keeps separate segment buffers for the exact
    Matplotlib renderer.  The realtime canvas instead derives its line from
    the visible point trail so that selecting ``Smooth spline`` can never be
    bypassed by a precomputed straight-segment buffer.
    """

    path_points = np.asarray(points, dtype=np.float64)
    path_rgba = np.asarray(rgba, dtype=np.float64)
    if path_points.ndim != 2 or path_points.shape[0] < 2 or path_rgba.shape[0] != path_points.shape[0]:
        return (
            np.empty((0, 2, 3), dtype=np.float64),
            np.empty((0, 4), dtype=np.float64),
            np.empty(0, dtype=np.float64),
        )

    widths = np.full(path_points.shape[0], max(0.1, float(line_width)), dtype=np.float64)
    if smooth and path_points.shape[0] >= 4:
        # Four samples per original edge makes the curvature unmistakable in
        # the live canvas while remaining safely below its segment budget.
        path_points, path_rgba, widths = interpolate_spline(
            path_points,
            path_rgba,
            widths,
            max(4, int(detail)),
        )
    return (
        np.stack((path_points[:-1], path_points[1:]), axis=1),
        0.5 * (path_rgba[:-1] + path_rgba[1:]),
        0.5 * (widths[:-1] + widths[1:]),
    )


class Realtime3DCanvas(QWidget):
    """Cached QPainter projection for moving media and microphone trajectories."""

    cameraChanged = Signal(float, float, float)
    interactionStarted = Signal()
    interactionFinished = Signal()
    frameRendered = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Realtime3DCanvas")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.analysis: AnalysisResult | None = None
        self.geometry: GeometryResult | None = None
        self.options: Any = None
        self.theme_name = "dark"
        self.current_time = 0.0
        self.maximum_points = 1200
        self.elevation = 24.0
        self.azimuth = 35.0
        self.zoom = 1.0
        self._center = np.zeros(3, dtype=np.float64)
        self._half_span = np.ones(3, dtype=np.float64)
        self._drag_origin: QPointF | None = None
        self._drag_camera = (self.elevation, self.azimuth)
        self._live_points = np.empty((0, 3), dtype=np.float64)
        self._live_rgba = np.empty((0, 4), dtype=np.float64)
        self._live_sizes = np.empty(0, dtype=np.float64)
        self._live_active = False
        self._grid_back_planes = (-1.0, -1.0, -1.0)
        self._grid_segments = _grid_segments(*self._grid_back_planes)
        self._axis_segments = _axis_segments(*self._grid_back_planes)

    # -------------------------------------------------------------- scene data
    def set_theme(self, theme_name: str) -> None:
        self.theme_name = "light" if str(theme_name).casefold() == "light" else "dark"
        self.update()

    def set_scene(
        self,
        analysis: AnalysisResult,
        geometry: GeometryResult,
        options: Any,
        *,
        maximum_points: int = 1200,
    ) -> None:
        self.analysis = analysis
        self.geometry = geometry
        self.options = options
        self.maximum_points = max(100, int(maximum_points))
        self._configure_bounds(
            np.column_stack((geometry.x_full, geometry.y_full, geometry.z_full)).astype(np.float64, copy=False)
        )
        self.set_camera(
            float(getattr(options, "elev", self.elevation)),
            float(getattr(options, "azim", self.azimuth)),
            float(getattr(options, "zoom", self.zoom)),
            emit=False,
        )
        self.update()

    def clear_scene(self) -> None:
        self.analysis = None
        self.geometry = None
        self.options = None
        self._live_points = np.empty((0, 3), dtype=np.float64)
        self._live_rgba = np.empty((0, 4), dtype=np.float64)
        self._live_sizes = np.empty(0, dtype=np.float64)
        self._live_active = False
        self.update()

    def set_options(self, options: Any, *, maximum_points: int | None = None) -> None:
        self.options = options
        if maximum_points is not None:
            self.maximum_points = max(100, int(maximum_points))
        self.update()

    def set_time(self, current_time: float) -> None:
        self.current_time = max(0.0, float(current_time))
        self.update()

    def set_camera(
        self,
        elevation: float,
        azimuth: float,
        zoom: float | None = None,
        *,
        emit: bool = False,
    ) -> None:
        self.elevation = float(np.clip(elevation, -90.0, 90.0))
        self.azimuth = (float(azimuth) + 180.0) % 360.0 - 180.0
        if zoom is not None:
            self.zoom = float(np.clip(zoom, 0.2, 5.0))
        self.update()
        if emit:
            self.cameraChanged.emit(self.elevation, self.azimuth, self.zoom)

    def camera(self) -> tuple[float, float, float]:
        return self.elevation, self.azimuth, self.zoom

    def set_live_trajectory(
        self,
        points: np.ndarray,
        rgba: np.ndarray | None = None,
        sizes: np.ndarray | None = None,
    ) -> None:
        values = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        finite = np.all(np.isfinite(values), axis=1)
        values = values[finite]
        if values.shape[0] > self.maximum_points:
            values = values[-self.maximum_points :]
            finite_indices = np.flatnonzero(finite)[-self.maximum_points :]
        else:
            finite_indices = np.flatnonzero(finite)
        self._live_points = values
        if rgba is None or np.asarray(rgba).size == 0:
            progress = np.linspace(0.0, 1.0, values.shape[0], dtype=np.float64)
            self._live_rgba = np.column_stack(
                (
                    0.12 + 0.58 * progress,
                    0.72 + 0.24 * progress,
                    0.72 - 0.28 * progress,
                    0.18 + 0.82 * progress,
                )
            )
        else:
            colors = np.asarray(rgba, dtype=np.float64).reshape(-1, 4)
            self._live_rgba = (
                colors[finite_indices]
                if colors.shape[0] > int(np.max(finite_indices, initial=-1))
                else colors[-values.shape[0] :]
            )
        if sizes is None or np.asarray(sizes).size == 0:
            self._live_sizes = np.ones(values.shape[0], dtype=np.float64)
        else:
            scale = np.asarray(sizes, dtype=np.float64).reshape(-1)
            self._live_sizes = (
                scale[finite_indices]
                if scale.size > int(np.max(finite_indices, initial=-1))
                else scale[-values.shape[0] :]
            )
        self._live_active = values.size > 0
        if self._live_active:
            self._configure_bounds(values, preserve=True)
        self.update()

    def clear_live_trajectory(self) -> None:
        self._live_points = np.empty((0, 3), dtype=np.float64)
        self._live_rgba = np.empty((0, 4), dtype=np.float64)
        self._live_sizes = np.empty(0, dtype=np.float64)
        self._live_active = False
        if self.geometry is not None:
            self._configure_bounds(
                np.column_stack((self.geometry.x_full, self.geometry.y_full, self.geometry.z_full)).astype(
                    np.float64, copy=False
                )
            )
        self.update()

    @property
    def live_active(self) -> bool:
        return self._live_active

    def _configure_bounds(self, points: np.ndarray, *, preserve: bool = False) -> None:
        values = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if not values.size:
            return
        finite = values[np.all(np.isfinite(values), axis=1)]
        if not finite.size:
            return
        low = np.percentile(finite, 1.0, axis=0)
        high = np.percentile(finite, 99.0, axis=0)
        center = 0.5 * (low + high)
        half_span = np.maximum(0.5 * (high - low), 1e-6)
        # A gentle live bound blend avoids the microphone trajectory breathing
        # wildly whenever one new point extends the rolling range.
        if preserve and self._live_points.shape[0] > 8:
            self._center = 0.88 * self._center + 0.12 * center
            self._half_span = np.maximum(0.88 * self._half_span + 0.12 * half_span, 1e-6)
        else:
            self._center = center
            self._half_span = half_span

    # --------------------------------------------------------------- projection
    @staticmethod
    def _rotation_matrix(elevation: float, azimuth: float) -> np.ndarray:
        # Match Matplotlib's 3D camera convention used by the exact scene:
        # 90° is a top-down view of the XY plane and 0° is level with it.
        elev = math.radians(90.0 - float(elevation))
        azim = math.radians(float(azimuth))
        ca, sa = math.cos(azim), math.sin(azim)
        ce, se = math.cos(elev), math.sin(elev)
        rotate_z = np.asarray(((ca, -sa, 0.0), (sa, ca, 0.0), (0.0, 0.0, 1.0)), dtype=np.float64)
        rotate_x = np.asarray(((1.0, 0.0, 0.0), (0.0, ce, -se), (0.0, se, ce)), dtype=np.float64)
        return rotate_z @ rotate_x

    def normalize_points(self, points: np.ndarray) -> np.ndarray:
        values = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        return np.clip((values - self._center[None, :]) / self._half_span[None, :], -1.6, 1.6)

    def project_normalized(self, points: np.ndarray) -> np.ndarray:
        values = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        rotated = values @ self._rotation_matrix(self.elevation, self.azimuth)
        depth = rotated[:, 2]
        perspective = 1.0 / np.clip(2.75 - depth * 0.38, 1.45, 4.0)
        logical_width = max(1.0, float(self.width()))
        logical_height = max(1.0, float(self.height()))
        scale = min(logical_width, logical_height) * 0.82 * float(self.zoom)
        projected = np.empty((values.shape[0], 3), dtype=np.float64)
        projected[:, 0] = logical_width * 0.5 + rotated[:, 0] * perspective * scale
        projected[:, 1] = logical_height * 0.51 - rotated[:, 1] * perspective * scale
        projected[:, 2] = depth
        return projected

    def project(self, points: np.ndarray) -> np.ndarray:
        return self.project_normalized(self.normalize_points(points))

    def _place_grid_behind_scene(self) -> tuple[float, float, float]:
        """Keep the three coordinate planes on the camera's far cube edges.

        A fixed ``x = -1`` or ``y = -1`` plane can become foreground geometry
        after orbiting around the scene.  Selecting each plane's signed edge
        from the camera depth vector keeps XY, XZ, and YZ behind the mapped
        visualizer while preserving their expected coordinate orientation.
        """

        depth_axis = self._rotation_matrix(self.elevation, self.azimuth)[:, 2]
        planes = tuple(-1.0 if component >= 0.0 else 1.0 for component in depth_axis)
        if planes != self._grid_back_planes:
            self._grid_back_planes = planes
            self._grid_segments = _grid_segments(*planes)
            self._axis_segments = _axis_segments(*planes)
        return planes

    # ---------------------------------------------------------------- painting
    def _draw_segments(
        self,
        painter: QPainter,
        segments: np.ndarray,
        colors: np.ndarray,
        widths: np.ndarray,
        *,
        maximum: int = 2400,
    ) -> None:
        values = np.asarray(segments, dtype=np.float64).reshape(-1, 2, 3)
        if not values.size:
            return
        if values.shape[0] > maximum:
            keep = np.unique(np.linspace(0, values.shape[0] - 1, maximum, dtype=np.int64))
            values = values[keep]
            colors = np.asarray(colors)[keep]
            widths = np.asarray(widths)[keep]
        projected = self.project(values.reshape(-1, 3)).reshape(-1, 2, 3)
        rgba = np.asarray(colors, dtype=np.float64).reshape(-1, 4)
        line_widths = np.asarray(widths, dtype=np.float64).reshape(-1)

        # Crossing the Python/Qt boundary once per segment dominates the live
        # renderer on ordinary computers.  Draw consecutive gradient groups in
        # batches instead.  The proxy keeps up to 64 color/width steps and
        # orders those groups by mean depth; paused inspection and export still
        # use the exact Matplotlib artists.
        segment_count = projected.shape[0]
        group_count = min(64, segment_count)
        group_size = max(1, int(math.ceil(segment_count / group_count)))
        groups: list[tuple[float, int, int]] = []
        for start in range(0, segment_count, group_size):
            end = min(segment_count, start + group_size)
            mean_depth = float(np.mean(projected[start:end, :, 2]))
            groups.append((mean_depth, start, end))
        groups.sort(key=lambda item: item[0])

        for _depth, start, end in groups:
            pen = QPen(_qcolor(np.mean(rgba[start:end], axis=0)))
            pen.setWidthF(float(np.clip(np.mean(line_widths[start:end]), 0.35, 10.0)))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            lines = [
                QLineF(
                    projected[index, 0, 0],
                    projected[index, 0, 1],
                    projected[index, 1, 0],
                    projected[index, 1, 1],
                )
                for index in range(start, end)
            ]
            painter.drawLines(lines)

    def _draw_points(
        self,
        painter: QPainter,
        points: np.ndarray,
        colors: np.ndarray,
        sizes: np.ndarray,
        *,
        maximum: int = 900,
    ) -> None:
        values = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if not values.size:
            return
        if values.shape[0] > maximum:
            keep = np.unique(np.linspace(0, values.shape[0] - 1, maximum, dtype=np.int64))
            values = values[keep]
            colors = np.asarray(colors)[keep]
            sizes = np.asarray(sizes)[keep]
        projected = self.project(values)
        rgba = np.asarray(colors, dtype=np.float64).reshape(-1, 4)
        scale = np.asarray(sizes, dtype=np.float64).reshape(-1)

        # Batch point sprites for the same reason as path segments.  A rounded
        # QPen renders compact circular markers while avoiding hundreds of
        # Python-level setBrush/drawEllipse calls per frame.
        point_count = projected.shape[0]
        group_count = min(48, point_count)
        group_size = max(1, int(math.ceil(point_count / group_count)))
        groups: list[tuple[float, int, int]] = []
        for start in range(0, point_count, group_size):
            end = min(point_count, start + group_size)
            groups.append((float(np.mean(projected[start:end, 2])), start, end))
        groups.sort(key=lambda item: item[0])

        painter.setBrush(Qt.BrushStyle.NoBrush)
        for _depth, start, end in groups:
            radii = np.clip(np.sqrt(np.maximum(1.0, scale[start:end])) * 0.14, 1.0, 6.0)
            pen = QPen(_qcolor(np.mean(rgba[start:end], axis=0)))
            pen.setWidthF(float(2.0 * np.mean(radii)))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPoints(
                [QPointF(projected[index, 0], projected[index, 1]) for index in range(start, end)]
            )

    def _draw_grid(self, painter: QPainter) -> None:
        # The realtime proxy must honor the same visibility controls as the
        # exact scene. Otherwise playback appears to ignore Appearance.
        if self.options is not None and not bool(getattr(self.options, "show_axes", True)):
            return
        palette = scene_palette(self.theme_name)
        self._place_grid_behind_scene()
        show_grid = self.options is None or bool(getattr(self.options, "show_grid", True))
        if show_grid:
            grid = self.project_normalized(self._grid_segments.reshape(-1, 3)).reshape(-1, 2, 3)
            pen = QPen(_qcolor(palette.grid))
            pen.setWidthF(0.7)
            painter.setPen(pen)
            for segment in grid:
                painter.drawLine(QPointF(segment[0, 0], segment[0, 1]), QPointF(segment[1, 0], segment[1, 1]))
        axes = self.project_normalized(self._axis_segments.reshape(-1, 3)).reshape(-1, 2, 3)
        axis_pen = QPen(QColor(palette.muted_text))
        axis_pen.setWidthF(1.15)
        painter.setPen(axis_pen)
        for segment in axes:
            painter.drawLine(QPointF(segment[0, 0], segment[0, 1]), QPointF(segment[1, 0], segment[1, 1]))
        show_labels = self.options is None or bool(getattr(self.options, "show_axis_labels", True))
        if not show_labels:
            return
        painter.setPen(QColor(palette.muted_text))
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF()))
        font.setFamily("monospace")
        painter.setFont(font)
        labels = ("X", "Y", "Z")
        for label, segment in zip(labels, axes, strict=True):
            painter.drawText(QPointF(segment[1, 0] + 5.0, segment[1, 1] - 3.0), label)

    def _draw_live(self, painter: QPainter) -> None:
        values = self._live_points
        if values.shape[0] == 0:
            return
        rgba = self._live_rgba
        sizes = self._live_sizes
        detail = max(1, int(getattr(self.options, "curve_detail", 4))) if self.options is not None else 4
        smooth = "smooth" in str(getattr(self.options, "path_curve_mode", "Smooth spline")).casefold()
        mode = (
            str(getattr(self.options, "render_mode", "Points + line")).casefold()
            if self.options is not None
            else "points + line"
        )
        show_line = bool(getattr(self.options, "connect_lines", True)) if self.options is not None else True
        show_line = show_line and "points only" not in mode
        show_points = "points" in mode
        if show_line:
            segments, segment_rgba, segment_widths = media_path_segments(
                values,
                rgba,
                line_width=float(getattr(self.options, "line_width", 1.35)),
                smooth=smooth,
                detail=detail,
            )
            self._draw_segments(painter, segments, segment_rgba, segment_widths)
        if show_points:
            point_scale = float(getattr(self.options, "point_size_scale", 0.4)) if self.options is not None else 0.4
            self._draw_points(painter, values, rgba, (9.0 + 20.0 * sizes) * max(0.05, point_scale), maximum=500)
        if bool(getattr(self.options, "show_head_marker", True)) if self.options is not None else True:
            projected = self.project(values[-1:])[0]
            color = _qcolor(rgba[-1])
            painter.setPen(Qt.PenStyle.NoPen)
            halo_scale = float(getattr(self.options, "halo_size_scale", 0.45)) if self.options is not None else 0.45
            head_scale = float(getattr(self.options, "head_size_scale", 0.24)) if self.options is not None else 0.24
            if halo_scale > 0.0:
                halo = QColor(color)
                halo.setAlphaF(0.16)
                painter.setBrush(halo)
                halo_radius = float(np.clip(4.0 + halo_scale * 24.0, 4.0, 40.0))
                painter.drawEllipse(QPointF(projected[0], projected[1]), halo_radius, halo_radius)
            if head_scale > 0.0:
                painter.setBrush(color)
                head_radius = float(np.clip(2.0 + head_scale * 12.0, 2.0, 18.0))
                painter.drawEllipse(QPointF(projected[0], projected[1]), head_radius, head_radius)

    def _draw_media(self, painter: QPainter) -> None:
        if self.geometry is None or self.options is None:
            return
        state = compute_trail_state(
            self.geometry,
            self.current_time,
            self.options,
            maximum_points=self.maximum_points,
        )
        mode = str(getattr(self.options, "render_mode", "Points + line")).casefold()
        show_points = "points" in mode or "tube" not in mode
        show_line = bool(getattr(self.options, "connect_lines", True)) and "points only" not in mode
        if show_line:
            # Do not consume ``state.segments`` here.  Its purpose is to
            # support the exact renderer and may describe a simplified trail.
            # The live canvas always rebuilds a spline from the points it is
            # displaying, so the Smooth spline control visibly changes media
            # playback as well as microphone input.
            segments, segment_rgba, segment_widths = media_path_segments(
                state.points,
                state.point_rgba,
                line_width=max(1.0, float(getattr(self.options, "line_width", 1.35)) * 2.0),
                smooth="smooth" in str(getattr(self.options, "path_curve_mode", "Straight")).casefold(),
                detail=max(4, int(getattr(self.options, "curve_detail", 4))),
            )
            self._draw_segments(painter, segments, segment_rgba, segment_widths)
        if state.comet_segments.size:
            self._draw_segments(
                painter,
                state.comet_segments,
                state.comet_rgba,
                state.comet_widths,
                maximum=700,
            )
        if show_points and state.points.size:
            self._draw_points(painter, state.points, state.point_rgba, state.point_sizes)
        if bool(getattr(self.options, "show_head_marker", True)) and state.visible_idx.size:
            point = self.project(state.head_point.reshape(1, 3))[0]
            painter.setPen(Qt.PenStyle.NoPen)
            flash = _qcolor(state.head_flash_rgba)
            if flash.alphaF() > 0.0 and state.head_flash_size > 0.0:
                painter.setBrush(flash)
                radius = float(np.clip(math.sqrt(state.head_flash_size) * 0.65, 5.0, 42.0))
                painter.drawEllipse(QPointF(point[0], point[1]), radius, radius)
            painter.setBrush(_qcolor(state.head_halo_rgba))
            halo = float(np.clip(math.sqrt(state.head_size) * 1.15, 6.0, 24.0))
            painter.drawEllipse(QPointF(point[0], point[1]), halo, halo)
            painter.setBrush(_qcolor(state.head_rgba))
            head = float(np.clip(math.sqrt(state.head_size) * 0.46, 2.5, 10.0))
            painter.drawEllipse(QPointF(point[0], point[1]), head, head)

    def paintEvent(self, _event: Any) -> None:  # type: ignore[override]
        started = perf_counter()
        palette = scene_palette(self.theme_name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(palette.figure_background))
        self._draw_grid(painter)
        if self._live_active:
            self._draw_live(painter)
        else:
            self._draw_media(painter)
        painter.setPen(QColor(palette.muted_text))
        font = painter.font()
        font.setFamily("monospace")
        font.setPointSizeF(7.5)
        painter.setFont(font)
        painter.drawText(12, max(18, self.height() - 12), "DRAG ORBIT  ·  SCROLL ZOOM  ·  EXACT SCENE WHEN PAUSED")
        painter.end()
        self.frameRendered.emit(max(0.0, (perf_counter() - started) * 1000.0))

    # ------------------------------------------------------------- interaction
    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position()
            self._drag_camera = (self.elevation, self.azimuth)
            self.interactionStarted.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.position() - self._drag_origin
            elevation, azimuth = camera_after_drag(
                self._drag_camera[0],
                self._drag_camera[1],
                delta.x(),
                delta.y(),
            )
            self.set_camera(elevation, azimuth, emit=True)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            self._drag_origin = None
            self.interactionFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        steps = event.angleDelta().y() / 120.0
        if steps:
            factor = math.pow(1.10, steps)
            self.set_camera(self.elevation, self.azimuth, self.zoom * factor, emit=True)
            self.interactionStarted.emit()
            event.accept()
            return
        super().wheelEvent(event)


__all__ = [
    "Realtime3DCanvas",
    "advance_azimuth",
    "camera_after_drag",
]
