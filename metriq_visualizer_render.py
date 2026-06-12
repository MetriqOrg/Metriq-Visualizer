# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageDraw

import matplotlib
matplotlib.use("Agg")
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.colors import LinearSegmentedColormap
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  # registers 3D projection
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

from metriq_visualizer_core import AnalysisResult, GeometryResult, is_video_file
from metriq_visualizer_layout import ExportLayoutSpec, LAYOUT_ITEM_ORDER, LAYOUT_ITEM_TITLES, default_export_layout
from metriq_visualizer_cinematics import camera_params_for_time, deserialize_keyframes
from metriq_visualizer_visuals import (
    EMPTY_FLOAT,
    EMPTY_RGBA,
    compute_axis_limits,
    build_tube_mesh,
    compute_trail_visual_state,
    compute_tube_radii,
    prepare_color_mapping,
    zoom_limits,
)

from metriq_visualizer_export_engine import (
    EXPORT_ENGINE_AUTO_LABEL,
    EXPORT_QUALITY_BALANCED_LABEL,
    build_ffmpeg_rawvideo_command,
    encoder_candidates_for_engine,
    find_ffmpeg,
    normalize_export_engine,
    normalize_export_quality,
    probe_encoder,
    sanitize_video_size,
)


METRIQ_SPECTROGRAM_COLORS = ["#0b1118", "#0d2330", "#0a4f60", "#06a269", "#5ed39c", "#5fa6f7", "#d9ecff"]
METRIQ_SPECTROGRAM_CMAP = LinearSegmentedColormap.from_list("metriq_spectrogram", METRIQ_SPECTROGRAM_COLORS)


@dataclass
class ExportOptions:
    output_path: str
    width: int = 1920
    height: int = 1080
    fps: int = 30
    layout: ExportLayoutSpec = field(default_factory=default_export_layout)
    include_preview: bool = True
    include_panels: bool = True
    base_alpha: float = 0.82
    history_mode: str = "Trail fade"
    point_lifespan: float = 3.0
    fade_curve: float = 1.35
    line_width: float = 1.35
    path_curve_mode: str = "Smooth spline"
    curve_detail: int = 4
    connect_lines: bool = True
    ghost_path: bool = False
    flash_duration: float = 0.18
    comet_duration: float = 0.45
    elev: float = 24.0
    azim: float = 35.0
    autorotate: bool = True
    rotation_speed: float = 16.0
    zoom: float = 1.0
    camera_keyframes: list[dict] | None = None
    camera_easing: str = "ease_in_out"
    point_size_scale: float = 0.4
    render_mode: str = "Points + line"
    tube_radius_scale: float = 1.0
    tube_sides: int = 12
    tube_follow_size: bool = True
    tube_taper: float = 0.2
    show_head_marker: bool = True
    head_size_scale: float = 0.24
    halo_size_scale: float = 0.45
    flash_size_scale: float = 0.05
    show_axes: bool = True
    show_axis_labels: bool = True
    point_label_mode: str = "Off"
    point_label_content: str = "Time + Hz"
    max_point_labels: int = 8
    show_colorbar: bool = False
    show_project_title: bool = True
    project_title: str = "Metriq Visualizer"
    project_subtitle: str = ""
    show_watermark: bool = False
    watermark_text: str = ""
    title: str = "Metriq Visualizer"
    export_engine: str = EXPORT_ENGINE_AUTO_LABEL
    export_quality: str = EXPORT_QUALITY_BALANCED_LABEL
    video_bitrate_mbps: float = 0.0
    start_time: float = 0.0
    end_time: float | None = None


APP_BG = (7, 11, 17)
FIG_BG_RGBA = (0.0, 0.0, 0.0, 0.0)
TEXT = "#e8edf5"
MUTED_TEXT = "#bec7d3"
GRID_RGBA = (0.85, 0.90, 0.98, 0.08)
GHOST_RGBA = (0.88, 0.92, 1.0, 0.11)
CARD_OUTLINE = (122, 146, 182, 170)
CARD_SHADOW = (0, 0, 0, 64)
TITLE_BACKDROP = (6, 10, 16, 88)


class VideoPreviewReader:
    def __init__(self, source_path: str):
        self.source_path = source_path
        self.cap: cv2.VideoCapture | None = None
        self.fps = 0.0
        self.frame_count = 0
        self.frame_index = -1
        self.width = 0
        self.height = 0

        if is_video_file(source_path):
            cap = cv2.VideoCapture(source_path)
            if cap.isOpened():
                self.cap = cap
                self.fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
                self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    def read_at(self, current_time: float) -> np.ndarray | None:
        if self.cap is None:
            return None
        frame_idx = int(round(max(0.0, current_time) * self.fps))
        if self.frame_count > 0:
            frame_idx = max(0, min(self.frame_count - 1, frame_idx))

        frame = None
        need_seek = self.frame_index < 0 or frame_idx < self.frame_index or frame_idx - self.frame_index > 4
        if need_seek:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = self.cap.read()
            if (not ok or frame is None) and self.fps > 1e-6:
                self.cap.set(cv2.CAP_PROP_POS_MSEC, frame_idx * 1000.0 / self.fps)
                ok, frame = self.cap.read()
            if not ok or frame is None:
                return None
            self.frame_index = frame_idx
        else:
            while self.frame_index < frame_idx:
                ok, next_frame = self.cap.read()
                if not ok or next_frame is None:
                    return None
                self.frame_index += 1
                frame = next_frame
            if frame is None:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ok, frame = self.cap.read()
                if not ok or frame is None:
                    return None
                self.frame_index = frame_idx

        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)

    def close(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None


class OffscreenGeometryRenderer:
    def __init__(self, analysis: AnalysisResult, geom: GeometryResult, options: ExportOptions):
        self.analysis = analysis
        self.geom = geom
        self.options = options
        self.base_limits = compute_axis_limits(geom.x_full, geom.y_full, geom.z_full)
        self.max_span = max(
            float(self.base_limits["x"][1] - self.base_limits["x"][0]),
            float(self.base_limits["y"][1] - self.base_limits["y"][0]),
            float(self.base_limits["z"][1] - self.base_limits["z"][0]),
            1.0,
        )
        self.norm, self.rgba_full, self.scalar_map = prepare_color_mapping(geom.color_full, geom.colormap)
        self._point_text_artists: list[object] = []

        fig_w = max(10.0, float(options.width) / 170.0)
        fig_h = max(7.0, float(options.height) / 170.0)
        self.fig = Figure(figsize=(fig_w, fig_h), dpi=140)
        self.fig.patch.set_alpha(0.0)
        self.canvas = FigureCanvasAgg(self.fig)
        self.ax = self.fig.add_subplot(111, projection="3d")
        self._build_artists()

    def _style_3d_axes(self) -> None:
        self.fig.set_facecolor(FIG_BG_RGBA)
        self.ax.set_facecolor(FIG_BG_RGBA)
        self.ax.title.set_color(TEXT)
        self.ax.xaxis.label.set_color(TEXT)
        self.ax.yaxis.label.set_color(TEXT)
        self.ax.zaxis.label.set_color(TEXT)

        if self.options.show_axes:
            self.ax.tick_params(colors=MUTED_TEXT, labelsize=8)
            try:
                for axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
                    axis.pane.set_facecolor((0.06, 0.08, 0.11, 0.04))
                    axis.pane.set_edgecolor((0.65, 0.70, 0.78, 0.14))
                    axis._axinfo["grid"]["color"] = GRID_RGBA
                    axis._axinfo["grid"]["linewidth"] = 0.6
                    axis._axinfo["axisline"]["color"] = (0.92, 0.95, 0.99, 0.25)
                    axis._axinfo["tick"]["color"] = (0.92, 0.95, 0.99, 0.55)
            except Exception:
                pass
        else:
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            self.ax.set_zticks([])
            try:
                for axis in (self.ax.xaxis, self.ax.yaxis, self.ax.zaxis):
                    axis.pane.set_facecolor((0.0, 0.0, 0.0, 0.0))
                    axis.pane.set_edgecolor((0.0, 0.0, 0.0, 0.0))
                    axis._axinfo["grid"]["color"] = (0.0, 0.0, 0.0, 0.0)
                    axis._axinfo["axisline"]["color"] = (0.0, 0.0, 0.0, 0.0)
                    axis._axinfo["tick"]["color"] = (0.0, 0.0, 0.0, 0.0)
            except Exception:
                pass

    def _build_artists(self) -> None:
        ax = self.ax
        ax.clear()
        self._style_3d_axes()

        ghost_x = np.asarray(self.geom.x_full, dtype=np.float64).copy()
        ghost_y = np.asarray(self.geom.y_full, dtype=np.float64).copy()
        ghost_z = np.asarray(self.geom.z_full, dtype=np.float64).copy()
        active_mask = np.asarray(getattr(self.geom, "active_mask_full", np.ones_like(ghost_x, dtype=bool)), dtype=bool).reshape(-1)
        if active_mask.size == ghost_x.size and np.any(~active_mask):
            ghost_x[~active_mask] = np.nan
            ghost_y[~active_mask] = np.nan
            ghost_z[~active_mask] = np.nan
        self.ghost_line, = ax.plot(
            ghost_x,
            ghost_y,
            ghost_z,
            linewidth=0.9,
            color=GHOST_RGBA,
            zorder=1,
        )
        self.ghost_line.set_visible(bool(self.options.ghost_path))

        self.tube_collection = Poly3DCollection([], linewidths=0.0)
        self.tube_collection.set_edgecolor((0.0, 0.0, 0.0, 0.0))
        self.tube_collection.set_facecolor((1.0, 1.0, 1.0, 0.0))
        self.tube_collection.set_visible(False)
        ax.add_collection3d(self.tube_collection)

        dummy_segments = np.zeros((1, 2, 3), dtype=np.float64)
        self.trail_collection = Line3DCollection(dummy_segments, linewidths=[0.0])
        self.comet_collection = Line3DCollection(dummy_segments, linewidths=[0.0])
        ax.add_collection3d(self.trail_collection)
        ax.add_collection3d(self.comet_collection)

        self.dynamic_scatter = ax.scatter([], [], [], s=[], depthshade=False, linewidths=0.0)
        self.head_halo = ax.scatter([], [], [], s=[], depthshade=False, linewidths=0.0)
        self.head_scatter = ax.scatter([], [], [], s=[], depthshade=False, linewidths=1.2)
        self.head_flash = ax.scatter([], [], [], s=[], depthshade=False, linewidths=0.0)
        self.hud_text = ax.text2D(0.02, 0.98, "", transform=ax.transAxes, color=TEXT, fontsize=10, va="top")

        ax.set_title("")
        if self.options.show_axis_labels:
            ax.set_xlabel(self.geom.labels["x"])
            ax.set_ylabel(self.geom.labels["y"])
            ax.set_zlabel(self.geom.labels["z"])
        else:
            ax.set_xlabel("")
            ax.set_ylabel("")
            ax.set_zlabel("")
        ax.view_init(elev=float(self.options.elev), azim=float(self.options.azim))
        try:
            ax.set_box_aspect((1, 1, 1))
        except Exception:
            pass

        limits = zoom_limits(self.base_limits, self.options.zoom)
        ax.set_xlim(*limits["x"])
        ax.set_ylim(*limits["y"])
        ax.set_zlim(*limits["z"])

        self.colorbar = None
        if bool(self.options.show_colorbar):
            self.colorbar = self.fig.colorbar(self.scalar_map, ax=ax, pad=0.03, shrink=0.78)
            self.colorbar.set_label(self.geom.labels["color"] if self.options.show_axis_labels else "", color=TEXT)
            self.colorbar.ax.yaxis.set_tick_params(color=MUTED_TEXT)
            for tick in self.colorbar.ax.get_yticklabels():
                tick.set_color(MUTED_TEXT)
            try:
                self.colorbar.outline.set_edgecolor("#6f7888")
                self.colorbar.ax.set_facecolor((0.0, 0.0, 0.0, 0.0))
            except Exception:
                pass

    def _broadcast_rgba(self, rgba: np.ndarray, n: int) -> np.ndarray:
        arr = np.asarray(rgba, dtype=np.float64).reshape(-1, 4)
        if arr.shape[0] == n:
            return arr
        if arr.shape[0] == 1 and n > 0:
            return np.repeat(arr, n, axis=0)
        if n == 0:
            return EMPTY_RGBA.copy()
        raise ValueError("RGBA array shape does not match point count.")

    def _set_scatter_data(
        self,
        scatter,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        face_rgba: np.ndarray,
        sizes: np.ndarray,
        edge_rgba: np.ndarray | None = None,
        linewidths: float | None = None,
    ) -> None:
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        y = np.asarray(y, dtype=np.float64).reshape(-1)
        z = np.asarray(z, dtype=np.float64).reshape(-1)
        sizes = np.asarray(sizes, dtype=np.float64).reshape(-1)
        face_arr = self._broadcast_rgba(face_rgba, x.size)
        edge_arr = face_arr if edge_rgba is None else self._broadcast_rgba(edge_rgba, x.size)

        scatter._offsets3d = (x, y, z)
        scatter.set_sizes(sizes)
        scatter.set_facecolors(face_arr)
        scatter.set_edgecolors(edge_arr)
        try:
            scatter._facecolor3d = face_arr
            scatter._edgecolor3d = edge_arr
            scatter._facecolors = face_arr
            scatter._edgecolors = edge_arr
        except Exception:
            pass
        if linewidths is not None:
            scatter.set_linewidths(linewidths)

    def _label_text_for_index(self, idx: int, content_mode: str) -> str:
        idx = max(0, min(int(idx), int(self.geom.times_full.size - 1)))
        time_text = f"{float(self.geom.times_full[idx]):0.2f}s"
        freq_text = ""
        if "dominant_freq_hz" in self.analysis.features:
            freq_text = f"{float(self.analysis.features['dominant_freq_hz'][idx]):0.0f} Hz"
        if content_mode == "Time":
            return time_text
        if content_mode == "Dominant Hz":
            return freq_text or time_text
        if content_mode == "Index":
            return f"#{idx}"
        if freq_text:
            return f"{time_text} • {freq_text}"
        return time_text

    def _clear_point_texts(self) -> None:
        for artist in self._point_text_artists:
            try:
                artist.remove()
            except Exception:
                pass
        self._point_text_artists.clear()

    def _render_point_labels(self, state) -> None:
        self._clear_point_texts()
        mode = str(self.options.point_label_mode)
        if mode == "Off" or self.geom.times_full.size == 0:
            return

        if mode == "Current point":
            label_indices = np.array([int(state.head_idx)], dtype=int)
        else:
            visible_idx = np.asarray(state.visible_idx, dtype=int)
            if visible_idx.size == 0:
                return
            max_labels = max(1, int(self.options.max_point_labels))
            keep = np.linspace(0, visible_idx.size - 1, num=min(max_labels, visible_idx.size), dtype=int)
            label_indices = np.unique(np.append(visible_idx[keep], int(state.head_idx)))

        base_x = self.base_limits["x"][1] - self.base_limits["x"][0]
        base_y = self.base_limits["y"][1] - self.base_limits["y"][0]
        base_z = self.base_limits["z"][1] - self.base_limits["z"][0]
        offset = np.array([0.018 * base_x, 0.018 * base_y, 0.018 * base_z], dtype=np.float64)

        for idx in label_indices:
            text = self._label_text_for_index(int(idx), str(self.options.point_label_content))
            color = TEXT if int(idx) == int(state.head_idx) else MUTED_TEXT
            artist = self.ax.text(
                float(self.geom.x_full[idx] + offset[0]),
                float(self.geom.y_full[idx] + offset[1]),
                float(self.geom.z_full[idx] + offset[2]),
                text,
                color=color,
                fontsize=9 if int(idx) == int(state.head_idx) else 8,
            )
            self._point_text_artists.append(artist)

    def _set_tube_mesh(self, mesh) -> None:
        if mesh is None or getattr(mesh, "faces", np.empty((0, 3))).size == 0:
            self.tube_collection.set_verts([])
            self.tube_collection.set_visible(False)
            return
        polys = np.asarray(mesh.vertices, dtype=np.float64)[np.asarray(mesh.faces, dtype=np.int32)]
        self.tube_collection.set_verts(polys)
        self.tube_collection.set_facecolor(np.asarray(mesh.face_colors, dtype=np.float64))
        self.tube_collection.set_edgecolor((0.0, 0.0, 0.0, 0.0))
        self.tube_collection.set_linewidths([0.0])
        self.tube_collection.set_visible(True)

    def render(self, current_time: float) -> np.ndarray:
        state = compute_trail_visual_state(
            self.geom,
            rgba_full=self.rgba_full,
            current_time=current_time,
            base_alpha=self.options.base_alpha,
            history_mode=self.options.history_mode,
            point_lifespan=self.options.point_lifespan,
            fade_curve=self.options.fade_curve,
            max_points=max(50, int(len(self.geom.times_full))),
            connect_lines=self.options.connect_lines,
            line_width=self.options.line_width,
            comet_duration=self.options.comet_duration,
            flash_duration=self.options.flash_duration,
            curve_mode=str(getattr(self.options, "path_curve_mode", "Straight")),
            curve_samples=max(1, int(getattr(self.options, "curve_detail", 4))),
        )

        point_scale = max(0.0, float(self.options.point_size_scale))
        render_mode = str(getattr(self.options, "render_mode", "Points + line") or "Points + line")
        show_tube = render_mode in {"Tube", "Tube + points"}
        show_points = render_mode in {"Points + line", "Points only", "Tube + points"}
        show_trail = render_mode == "Points + line"

        if state.x.size > 0 and show_points:
            self._set_scatter_data(
                self.dynamic_scatter,
                state.x,
                state.y,
                state.z,
                state.point_rgba,
                state.point_sizes * point_scale,
            )
            self.dynamic_scatter.set_visible(True)
        else:
            self._set_scatter_data(
                self.dynamic_scatter,
                EMPTY_FLOAT,
                EMPTY_FLOAT,
                EMPTY_FLOAT,
                EMPTY_RGBA,
                EMPTY_FLOAT,
            )
            self.dynamic_scatter.set_visible(False)

        if show_tube and state.x.size >= 2:
            radii = compute_tube_radii(
                state.point_sizes * point_scale,
                self.max_span,
                radius_scale=float(getattr(self.options, "tube_radius_scale", 1.0)),
                follow_point_size=bool(getattr(self.options, "tube_follow_size", True)),
                taper=float(getattr(self.options, "tube_taper", 0.2)),
            )
            tube_mesh = build_tube_mesh(
                np.column_stack([state.x, state.y, state.z]).astype(np.float32, copy=False),
                np.asarray(state.point_rgba, dtype=np.float32),
                np.asarray(radii, dtype=np.float32),
                visible_idx=np.asarray(state.visible_idx, dtype=np.int32),
                sides=int(getattr(self.options, "tube_sides", 12)),
                curve_mode=str(getattr(self.options, "path_curve_mode", "Straight")),
                curve_samples=max(1, int(getattr(self.options, "curve_detail", 4))),
            )
            self._set_tube_mesh(tube_mesh)
        else:
            self._set_tube_mesh(None)

        if state.segments.shape[0] > 0 and show_trail:
            self.trail_collection.set_segments(state.segments)
            self.trail_collection.set_color(state.segment_rgba)
            self.trail_collection.set_linewidths(state.segment_widths)
            self.trail_collection.set_visible(True)
        else:
            self.trail_collection.set_segments(np.zeros((1, 2, 3), dtype=np.float64))
            self.trail_collection.set_color(np.array([[1.0, 1.0, 1.0, 0.0]]))
            self.trail_collection.set_linewidths([0.0])
            self.trail_collection.set_visible(False)

        if state.comet_segments.shape[0] > 0:
            self.comet_collection.set_segments(state.comet_segments)
            self.comet_collection.set_color(state.comet_rgba)
            self.comet_collection.set_linewidths(state.comet_widths)
            self.comet_collection.set_visible(True)
        else:
            self.comet_collection.set_segments(np.zeros((1, 2, 3), dtype=np.float64))
            self.comet_collection.set_color(np.array([[1.0, 1.0, 1.0, 0.0]]))
            self.comet_collection.set_linewidths([0.0])
            self.comet_collection.set_visible(False)

        self.ghost_line.set_visible(bool(self.options.ghost_path))

        if self.options.show_head_marker:
            hx, hy, hz = state.head_point
            head_size = max(0.0, float(state.head_size) * float(self.options.head_size_scale))
            halo_size = max(0.0, float(state.head_size) * float(self.options.halo_size_scale))
            flash_size = max(0.0, float(state.head_flash_size) * float(self.options.flash_size_scale))
            self._set_scatter_data(
                self.head_halo,
                np.array([hx]),
                np.array([hy]),
                np.array([hz]),
                np.array([state.head_halo_rgba]),
                np.array([halo_size]),
                linewidths=0.0,
            )
            self._set_scatter_data(
                self.head_scatter,
                np.array([hx]),
                np.array([hy]),
                np.array([hz]),
                np.array([state.head_rgba]),
                np.array([head_size]),
                edge_rgba=np.array([[1.0, 1.0, 1.0, 0.95]]),
                linewidths=1.2,
            )

            if state.head_flash_rgba[3] > 1e-4 and flash_size > 1e-3:
                self._set_scatter_data(
                    self.head_flash,
                    np.array([hx]),
                    np.array([hy]),
                    np.array([hz]),
                    np.array([state.head_flash_rgba]),
                    np.array([flash_size]),
                    linewidths=0.0,
                )
                self.head_flash.set_visible(True)
            else:
                self._set_scatter_data(
                    self.head_flash,
                    EMPTY_FLOAT,
                    EMPTY_FLOAT,
                    EMPTY_FLOAT,
                    EMPTY_RGBA,
                    EMPTY_FLOAT,
                    linewidths=0.0,
                )
                self.head_flash.set_visible(False)
        else:
            self._set_scatter_data(self.head_halo, EMPTY_FLOAT, EMPTY_FLOAT, EMPTY_FLOAT, EMPTY_RGBA, EMPTY_FLOAT, linewidths=0.0)
            self._set_scatter_data(self.head_scatter, EMPTY_FLOAT, EMPTY_FLOAT, EMPTY_FLOAT, EMPTY_RGBA, EMPTY_FLOAT, linewidths=0.0)
            self._set_scatter_data(self.head_flash, EMPTY_FLOAT, EMPTY_FLOAT, EMPTY_FLOAT, EMPTY_RGBA, EMPTY_FLOAT, linewidths=0.0)
            self.head_flash.set_visible(False)

        self._render_point_labels(state)

        keyframes = deserialize_keyframes(self.options.camera_keyframes)
        elev, azim, zoom = camera_params_for_time(
            float(current_time),
            keyframes,
            default_elevation=float(self.options.elev),
            default_azimuth=float(self.options.azim),
            default_zoom=float(self.options.zoom),
            autorotate=bool(self.options.autorotate),
            rotation_speed=float(self.options.rotation_speed),
            easing=str(getattr(self.options, "camera_easing", "ease_in_out")),
            start_time=float(getattr(self.options, "start_time", 0.0)),
        )
        self.ax.view_init(elev=float(elev), azim=float(azim))
        limits = zoom_limits(self.base_limits, zoom)
        self.ax.set_xlim(*limits["x"])
        self.ax.set_ylim(*limits["y"])
        self.ax.set_zlim(*limits["z"])

        dominant_hz = 0.0
        if "dominant_freq_hz" in self.analysis.features:
            dominant_hz = float(self.analysis.features["dominant_freq_hz"][state.head_idx])
        self.hud_text.set_text(f"{current_time:6.2f} s   •   {dominant_hz:7.1f} Hz")

        self.canvas.draw()
        return _figure_to_rgba(self.canvas)


class AnalysisCardRenderer:
    def __init__(self, analysis: AnalysisResult, geom: GeometryResult, options: ExportOptions):
        self.analysis = analysis
        self.geom = geom
        self.options = options
        self.duration = float(analysis.times[-1]) if analysis.times.size > 0 else 0.0
        self._base_images = self._build_base_images()

    def _panel_canvas(self, width_px: int, height_px: int) -> tuple[Figure, FigureCanvasAgg, object]:
        fig = Figure(figsize=(max(1.0, width_px / 100.0), max(1.0, height_px / 100.0)), dpi=100)
        fig.patch.set_alpha(0.0)
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        _style_panel_axis(ax)
        fig.subplots_adjust(left=0.035, right=0.998, bottom=0.14, top=0.92)
        return fig, canvas, ax

    def _build_base_images(self) -> dict[str, np.ndarray]:
        width_px = max(760, int(self.options.width * 0.54))
        panel_h = max(220, int(self.options.height * 0.18))
        images = {
            "spectrogram": self._render_spectrogram(width_px, panel_h),
            "chromagram": self._render_chromagram(width_px, panel_h),
            "mfcc": self._render_mfcc(width_px, panel_h),
            "traces": self._render_traces(width_px, max(240, int(panel_h * 1.10))),
        }
        return images

    def _time_bounds(self) -> tuple[float, float]:
        t0 = float(self.analysis.times[0]) if self.analysis.times.size > 0 else 0.0
        t1 = float(self.analysis.times[-1]) if self.analysis.times.size > 0 else 1.0
        if t1 <= t0:
            t1 = t0 + 1.0
        return t0, t1

    def _render_spectrogram(self, width_px: int, height_px: int) -> np.ndarray:
        fig, canvas, ax = self._panel_canvas(width_px, height_px)
        t0, t1 = self._time_bounds()
        ax.imshow(
            self.analysis.spectrogram_db,
            origin="lower",
            aspect="auto",
            extent=[t0, t1, 0.0, self.analysis.sample_rate / 2.0],
            interpolation="nearest",
            cmap=METRIQ_SPECTROGRAM_CMAP,
        )
        ax.set_ylabel("Hz")
        ax.set_xlabel("Time (s)")
        canvas.draw()
        return _figure_to_rgba(canvas)

    def _render_chromagram(self, width_px: int, height_px: int) -> np.ndarray:
        fig, canvas, ax = self._panel_canvas(width_px, height_px)
        t0, t1 = self._time_bounds()
        ax.imshow(
            self.analysis.chromagram,
            origin="lower",
            aspect="auto",
            extent=[t0, t1, 0, self.analysis.chromagram.shape[0]],
            interpolation="nearest",
            cmap="viridis",
        )
        ax.set_yticks(np.arange(0.5, 12.5, 1.0))
        ax.set_yticklabels(["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"], fontsize=8)
        ax.set_xlabel("Time (s)")
        canvas.draw()
        return _figure_to_rgba(canvas)

    def _render_mfcc(self, width_px: int, height_px: int) -> np.ndarray:
        fig, canvas, ax = self._panel_canvas(width_px, height_px)
        t0, t1 = self._time_bounds()
        ax.imshow(
            self.analysis.mfcc,
            origin="lower",
            aspect="auto",
            extent=[t0, t1, 1, self.analysis.mfcc.shape[0] + 1],
            interpolation="nearest",
            cmap="coolwarm",
        )
        ax.set_ylabel("MFCC #")
        ax.set_xlabel("Time (s)")
        canvas.draw()
        return _figure_to_rgba(canvas)

    def _render_traces(self, width_px: int, height_px: int) -> np.ndarray:
        fig, canvas, ax = self._panel_canvas(width_px, height_px)
        active_mask = np.asarray(getattr(self.geom, "active_mask_full", np.ones_like(self.analysis.times, dtype=bool)), dtype=bool).reshape(-1)
        if active_mask.size != self.analysis.times.size:
            active_mask = np.ones_like(self.analysis.times, dtype=bool)

        def _masked(values: np.ndarray) -> np.ndarray:
            arr = np.asarray(values, dtype=np.float64).copy()
            arr[~active_mask] = np.nan
            return arr

        ax.plot(self.analysis.times, _masked(self.geom.x_full), label=f"X: {self.geom.labels['x']}", linewidth=1.0)
        ax.plot(self.analysis.times, _masked(self.geom.y_full), label=f"Y: {self.geom.labels['y']}", linewidth=1.0)
        ax.plot(self.analysis.times, _masked(self.geom.z_full), label=f"Z: {self.geom.labels['z']}", linewidth=1.0)
        ax.plot(self.analysis.times, _masked(self.geom.color_full), label=f"Color: {self.geom.labels['color']}", linewidth=0.9, alpha=0.8)
        ax.plot(self.analysis.times, _masked(self.geom.size_full), label=f"Size: {self.geom.labels['size']}", linewidth=0.9, alpha=0.8)
        ax.set_xlabel("Time (s)")
        ax.legend(loc="upper right", fontsize=8, ncol=2)
        canvas.draw()
        return _figure_to_rgba(canvas)

    def render_card(self, name: str, current_time: float) -> np.ndarray | None:
        base = self._base_images.get(name)
        if base is None:
            return None
        image = Image.fromarray(base, mode="RGBA")
        draw = ImageDraw.Draw(image)
        w, h = image.size
        if self.duration > 1e-9:
            frac = min(1.0, max(0.0, current_time / self.duration))
        else:
            frac = 0.0
        x = int(round(frac * (w - 1)))
        draw.line([(x, 0), (x, h)], fill=(248, 251, 255, 235), width=max(1, int(round(w / 900))))
        return np.asarray(image, dtype=np.uint8)


class ExportPreviewSession:
    def __init__(self, analysis: AnalysisResult, geom: GeometryResult, options: ExportOptions):
        self.analysis = analysis
        self.geom = geom
        self.options = options
        self.geometry_renderer = OffscreenGeometryRenderer(analysis, geom, options)
        panel_names = ("spectrogram", "chromagram", "mfcc", "traces")
        need_panels = bool(options.include_panels) and any(options.layout.item(name).enabled for name in panel_names)
        self.panel_renderer = AnalysisCardRenderer(analysis, geom, options) if need_panels else None
        need_preview = bool(options.include_preview) and bool(options.layout.preview.enabled)
        self.preview_reader = VideoPreviewReader(analysis.source_path) if need_preview else None
        self._last_request_key: tuple | None = None
        self._last_frame: np.ndarray | None = None

    def render_frame(self, current_time: float, layout: ExportLayoutSpec | None = None, output_size: tuple[int, int] | None = None) -> np.ndarray:
        effective_layout = (layout or self.options.layout).clone().clamp()
        effective_output = output_size or (int(self.options.width), int(self.options.height))
        request_key = (
            round(float(current_time), 3),
            int(effective_output[0]),
            int(effective_output[1]),
            tuple((name, tuple(vars(effective_layout.item(name)).values())) for name in LAYOUT_ITEM_ORDER),
        )
        if self._last_request_key == request_key and self._last_frame is not None:
            return np.asarray(self._last_frame, dtype=np.uint8)
        cards = {
            "geometry": self.geometry_renderer.render(current_time) if effective_layout.geometry.enabled else None,
            "preview": None,
            "spectrogram": None,
            "chromagram": None,
            "mfcc": None,
            "traces": None,
        }
        if self.preview_reader is not None and effective_layout.preview.enabled:
            cards["preview"] = self.preview_reader.read_at(current_time)
        if self.panel_renderer is not None:
            for name in ("spectrogram", "chromagram", "mfcc", "traces"):
                if effective_layout.item(name).enabled:
                    cards[name] = self.panel_renderer.render_card(name, current_time)
        frame_rgba = compose_export_frame_rgba(
            cards=cards,
            output_size=effective_output,
            layout=effective_layout,
            source_path=self.analysis.source_path if self.preview_reader is not None and effective_layout.preview.enabled else "",
            options=self.options,
            current_time=current_time,
            analysis=self.analysis,
        )
        self._last_request_key = request_key
        self._last_frame = np.asarray(frame_rgba, dtype=np.uint8)
        return np.asarray(self._last_frame, dtype=np.uint8)

    def close(self) -> None:
        if self.preview_reader is not None:
            self.preview_reader.close()



def _style_panel_axis(ax) -> None:
    ax.set_facecolor((0.0, 0.0, 0.0, 0.0))
    ax.title.set_color(TEXT)
    ax.xaxis.label.set_color(MUTED_TEXT)
    ax.yaxis.label.set_color(MUTED_TEXT)
    ax.tick_params(colors=MUTED_TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#48505f")
    ax.grid(False)



def _figure_to_rgba(canvas: FigureCanvasAgg) -> np.ndarray:
    width, height = canvas.get_width_height()
    buf = np.frombuffer(canvas.buffer_rgba(), dtype=np.uint8).reshape(height, width, 4)
    return np.asarray(buf, dtype=np.uint8).copy()



def _ensure_rgba(image: np.ndarray | Image.Image) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGBA")
    array = np.asarray(image)
    if array.ndim != 3:
        raise ValueError("Image array must be HxWxC.")
    if array.shape[2] == 3:
        alpha = np.full(array.shape[:2] + (1,), 255, dtype=np.uint8)
        array = np.concatenate([array.astype(np.uint8), alpha], axis=2)
    elif array.shape[2] != 4:
        raise ValueError("Image array must have 3 or 4 channels.")
    return Image.fromarray(array.astype(np.uint8), mode="RGBA")



def _paste_rgba(base: Image.Image, overlay: Image.Image, xy: tuple[int, int]) -> None:
    if overlay.mode != "RGBA":
        overlay = overlay.convert("RGBA")
    x, y = xy
    base.alpha_composite(overlay, dest=(x, y))



def _trim_transparent_bounds(image: Image.Image, alpha_threshold: int = 3) -> Image.Image:
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    mask = alpha > int(alpha_threshold)
    if not np.any(mask):
        return image
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return image
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1
    return image.crop((left, top, right, bottom))



def _fit_image_rgba(
    image: np.ndarray | Image.Image,
    target_size: tuple[int, int],
    content_scale: float = 1.0,
    fit_mode: str = "contain",
    *,
    trim: bool = True,
) -> Image.Image:
    target_w, target_h = target_size
    image_pil = _ensure_rgba(image)
    if trim:
        image_pil = _trim_transparent_bounds(image_pil)
    src_w, src_h = image_pil.size
    if fit_mode == "stretch":
        resized = image_pil.resize((target_w, target_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        _paste_rgba(canvas, resized, (0, 0))
        return canvas

    contain_scale = min(target_w / max(1, src_w), target_h / max(1, src_h))
    fill_scale = max(target_w / max(1, src_w), target_h / max(1, src_h))
    base_scale = fill_scale if fit_mode == "fill" else contain_scale
    scale = base_scale * max(0.35, float(content_scale))
    resized = image_pil.resize(
        (max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    x0 = (target_w - resized.size[0]) // 2
    y0 = (target_h - resized.size[1]) // 2
    _paste_rgba(canvas, resized, (x0, y0))
    return canvas



def _audio_only_preview(target_size: tuple[int, int], source_path: str) -> Image.Image:
    image = Image.new("RGBA", target_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    w, h = image.size
    box = [12, 12, w - 12, h - 12]
    draw.rounded_rectangle(box, radius=18, fill=(12, 18, 28, 42))
    label = f"Audio only\n{Path(source_path).name}"
    draw.multiline_text((w * 0.08, h * 0.22), label, fill=(232, 237, 245, 235), spacing=8)
    return image



def _card_layer(size: tuple[int, int], background_alpha: float, title: str | None = None, show_title: bool = True) -> Image.Image:
    w, h = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    radius = max(12, int(round(min(w, h) * 0.03)))
    bg_alpha = int(round(max(0.0, min(1.0, background_alpha)) * 255.0))
    fill = (11, 16, 24, bg_alpha)
    if bg_alpha > 0:
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle([6, 8, w - 2, h - 1], radius=radius, fill=CARD_SHADOW)
        layer = Image.alpha_composite(layer, shadow)
    draw = ImageDraw.Draw(layer)
    if bg_alpha > 0:
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=fill)
    if show_title and title:
        title_w = min(w - 16, max(90, int(round(len(title) * 7.2 + 24))))
        title_h = 24
        draw.rounded_rectangle([10, 8, 10 + title_w, 8 + title_h], radius=10, fill=TITLE_BACKDROP)
        draw.text((20, 12), title, fill=(232, 237, 245, 235))
    return layer



def _layout_rect_px(layout_rect, inner_rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    inner_x, inner_y, inner_w, inner_h = inner_rect
    x = inner_x + int(round(layout_rect.x * inner_w))
    y = inner_y + int(round(layout_rect.y * inner_h))
    w = max(64, int(round(layout_rect.w * inner_w)))
    h = max(64, int(round(layout_rect.h * inner_h)))
    return x, y, w, h



def _draw_project_overlay(base: Image.Image, options: ExportOptions, current_time: float, analysis: AnalysisResult | None) -> None:
    draw = ImageDraw.Draw(base)
    width, height = base.size
    if bool(options.show_project_title) and (str(options.project_title).strip() or str(options.project_subtitle).strip()):
        title = str(options.project_title or "").strip()
        subtitle = str(options.project_subtitle or "").strip()
        box_h = 52 if subtitle else 34
        draw.rounded_rectangle([18, 16, min(width - 18, 720), 16 + box_h], radius=16, fill=(6, 10, 16, 140))
        if title:
            draw.text((30, 24), title, fill=(242, 246, 255, 245))
        if subtitle:
            draw.text((30, 42), subtitle, fill=(190, 199, 211, 225))

    if bool(options.show_watermark) and str(options.watermark_text).strip():
        text = str(options.watermark_text).strip()
        w_box = min(width - 20, max(120, int(len(text) * 7.6 + 28)))
        x0 = width - w_box - 18
        y0 = height - 36
        draw.rounded_rectangle([x0, y0, x0 + w_box, y0 + 24], radius=12, fill=(6, 10, 16, 138))
        draw.text((x0 + 14, y0 + 6), text, fill=(232, 237, 245, 228))



def compose_export_frame_rgba(
    cards: dict[str, np.ndarray | None],
    output_size: tuple[int, int],
    layout: ExportLayoutSpec,
    source_path: str,
    options: ExportOptions,
    current_time: float,
    analysis: AnalysisResult | None,
) -> np.ndarray:
    width, height = output_size
    margin = max(20, int(round(min(width, height) * 0.018)))
    base = Image.new("RGBA", (width, height), APP_BG + (255,))
    inner_rect = (margin, margin, width - 2 * margin, height - 2 * margin)
    layout = layout.clone().clamp()

    for name in LAYOUT_ITEM_ORDER:
        rect = layout.item(name)
        if not rect.enabled:
            continue
        x, y, w, h = _layout_rect_px(rect, inner_rect)
        payload = cards.get(name)
        if payload is None:
            if name == "preview" and source_path:
                overlay = _audio_only_preview((w, h), source_path)
            else:
                continue
        else:
            overlay = _fit_image_rgba(
                payload,
                (w, h),
                content_scale=float(rect.content_scale),
                fit_mode=str(getattr(rect, "fit_mode", "contain")),
                trim=bool(name != "geometry"),
            )
        card = _card_layer((w, h), float(rect.background_alpha), title=LAYOUT_ITEM_TITLES[name], show_title=bool(rect.show_title))
        card = Image.alpha_composite(card, overlay)
        _paste_rgba(base, card, (x, y))

    _draw_project_overlay(base, options, current_time, analysis)
    return np.asarray(base, dtype=np.uint8)



def _mux_audio_if_possible(
    video_path: str,
    audio_path: str,
    output_path: str,
    *,
    start_time: float = 0.0,
    duration: float | None = None,
) -> bool:
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return False

    trim_args: list[str] = []
    if float(start_time) > 1e-6:
        trim_args.extend(["-ss", f"{float(start_time):0.6f}"])
    if duration is not None and float(duration) > 1e-6:
        trim_args.extend(["-t", f"{float(duration):0.6f}"])

    cmd_copy = [
        ffmpeg_path,
        "-y",
        "-i",
        video_path,
        *trim_args,
        "-i",
        audio_path,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        output_path,
    ]
    proc = subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode == 0 and Path(output_path).exists():
        return True

    cmd_reencode = [
        ffmpeg_path,
        "-y",
        "-i",
        video_path,
        *trim_args,
        "-i",
        audio_path,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        output_path,
    ]
    proc = subprocess.run(cmd_reencode, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode == 0 and Path(output_path).exists()




def _render_export_video_ffmpeg(
    analysis: AnalysisResult,
    geom: GeometryResult,
    options: ExportOptions,
    *,
    ffmpeg_path: str,
    encoder,
    quality: str,
    clip_start: float,
    clip_end: float,
    clip_duration: float,
    total_frames: int,
    progress_callback: Callable[[float, str], None] | None = None,
) -> str:
    output_path = str(options.output_path)
    temp_root = Path(tempfile.mkdtemp(prefix="metriq_visualizer_render_ffmpeg_"))
    encoded_video = str(temp_root / "encoded_render.mp4")
    session = ExportPreviewSession(analysis, geom, options)
    stderr_text = b""
    proc: subprocess.Popen | None = None
    try:
        cmd = build_ffmpeg_rawvideo_command(
            ffmpeg_path=ffmpeg_path,
            encoder=encoder,
            output_path=encoded_video,
            width=int(options.width),
            height=int(options.height),
            fps=max(1, int(options.fps)),
            quality=quality,
            bitrate_mbps=float(getattr(options, "video_bitrate_mbps", 0.0) or 0.0),
            audio_path=analysis.audio_path if Path(analysis.audio_path).exists() else None,
            audio_start_time=clip_start,
            audio_duration=clip_duration if clip_duration > 1e-6 else None,
        )
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if progress_callback is not None:
            progress_callback(0.0, f"Encoding with {encoder.label} (FFmpeg)…")
        for frame_idx in range(total_frames):
            current_time = min(clip_end, clip_start + frame_idx / float(max(1, int(options.fps))))
            frame_rgba = session.render_frame(current_time=current_time)
            frame_rgb = np.ascontiguousarray(frame_rgba[:, :, :3], dtype=np.uint8)
            if proc.stdin is None:
                raise RuntimeError("FFmpeg stdin pipe was not available.")
            try:
                proc.stdin.write(frame_rgb.tobytes())
            except BrokenPipeError as exc:
                try:
                    stderr_text = proc.stderr.read() if proc.stderr is not None else b""
                except Exception:
                    stderr_text = b""
                raise RuntimeError(_format_ffmpeg_failure(encoder.label, proc.returncode, stderr_text)) from exc

            if progress_callback is not None and (frame_idx == 0 or frame_idx == total_frames - 1 or frame_idx % max(1, int(options.fps) // 2) == 0):
                progress = frame_idx / max(1, total_frames - 1)
                progress_callback(progress, f"Rendering frame {frame_idx + 1} / {total_frames} → {encoder.label}")

        if proc.stdin is not None:
            proc.stdin.close()
        return_code = proc.wait()
        try:
            stderr_text = proc.stderr.read() if proc.stderr is not None else b""
        except Exception:
            stderr_text = b""
        if return_code != 0:
            raise RuntimeError(_format_ffmpeg_failure(encoder.label, return_code, stderr_text))
        if not Path(encoded_video).exists() or Path(encoded_video).stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg did not create an output file with {encoder.label}.")

        if Path(output_path).exists():
            try:
                Path(output_path).unlink()
            except Exception:
                pass
        Path(encoded_video).replace(output_path)
        if progress_callback is not None:
            if clip_duration > 1e-6:
                progress_callback(1.0, f"Export finished with {encoder.label} ({clip_start:0.02f}s → {clip_end:0.02f}s): {output_path}")
            else:
                progress_callback(1.0, f"Export finished with {encoder.label}: {output_path}")
        return output_path
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        session.close()
        shutil.rmtree(temp_root, ignore_errors=True)


def _format_ffmpeg_failure(encoder_label: str, return_code: int | None, stderr_text: bytes | str) -> str:
    if isinstance(stderr_text, bytes):
        text = stderr_text.decode("utf-8", errors="replace")
    else:
        text = str(stderr_text or "")
    text = text.strip()
    if len(text) > 1800:
        text = text[-1800:]
    if text:
        return f"FFmpeg export failed with {encoder_label} (exit {return_code}):\n{text}"
    return f"FFmpeg export failed with {encoder_label} (exit {return_code})."


def _render_export_video_legacy_opencv(
    analysis: AnalysisResult,
    geom: GeometryResult,
    options: ExportOptions,
    *,
    clip_start: float,
    clip_end: float,
    clip_duration: float,
    total_frames: int,
    progress_callback: Callable[[float, str], None] | None = None,
) -> str:
    output_path = str(options.output_path)
    session = ExportPreviewSession(analysis, geom, options)
    temp_root = Path(tempfile.mkdtemp(prefix="metriq_visualizer_render_opencv_"))
    silent_video = str(temp_root / "silent_render.mp4")

    writer = cv2.VideoWriter(
        silent_video,
        cv2.VideoWriter_fourcc(*"mp4v"),
        float(max(1, int(options.fps))),
        (int(options.width), int(options.height)),
    )
    if not writer.isOpened():
        session.close()
        shutil.rmtree(temp_root, ignore_errors=True)
        raise RuntimeError("Could not open the MP4 writer for export.")

    try:
        if progress_callback is not None:
            progress_callback(0.0, "Encoding with legacy OpenCV MP4 writer…")
        for frame_idx in range(total_frames):
            current_time = min(clip_end, clip_start + frame_idx / float(max(1, int(options.fps))))
            frame_rgba = session.render_frame(current_time=current_time)
            frame_rgb = np.asarray(frame_rgba[:, :, :3], dtype=np.uint8)
            writer.write(cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))

            if progress_callback is not None and (frame_idx == 0 or frame_idx == total_frames - 1 or frame_idx % max(1, int(options.fps) // 2) == 0):
                progress = frame_idx / max(1, total_frames - 1)
                progress_callback(progress, f"Rendering frame {frame_idx + 1} / {total_frames}")
    finally:
        writer.release()
        session.close()

    if Path(output_path).exists():
        try:
            Path(output_path).unlink()
        except Exception:
            pass

    muxed = False
    if Path(analysis.audio_path).exists():
        muxed = _mux_audio_if_possible(
            silent_video,
            analysis.audio_path,
            output_path,
            start_time=clip_start,
            duration=clip_duration if clip_duration > 1e-6 else None,
        )

    if not muxed:
        Path(silent_video).replace(output_path)

    shutil.rmtree(temp_root, ignore_errors=True)
    if progress_callback is not None:
        if clip_duration > 1e-6:
            progress_callback(1.0, f"Export finished (legacy, {clip_start:0.02f}s → {clip_end:0.02f}s): {output_path}")
        else:
            progress_callback(1.0, f"Export finished (legacy): {output_path}")
    return output_path


def render_export_video(
    analysis: AnalysisResult,
    geom: GeometryResult,
    options: ExportOptions,
    progress_callback: Callable[[float, str], None] | None = None,
) -> str:
    output_path = str(options.output_path)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    width, height = sanitize_video_size(int(options.width), int(options.height))
    render_options = options if (width == int(options.width) and height == int(options.height)) else replace(options, width=width, height=height)

    full_duration = float(analysis.times[-1]) if analysis.times.size > 0 else 0.0
    clip_start = max(0.0, min(float(getattr(render_options, "start_time", 0.0) or 0.0), full_duration))
    opt_end = getattr(render_options, "end_time", None)
    if opt_end is None or float(opt_end) <= clip_start:
        clip_end = full_duration
    else:
        clip_end = max(clip_start, min(float(opt_end), full_duration))
    clip_duration = max(0.0, clip_end - clip_start)

    fps = max(1, int(render_options.fps))
    total_frames = max(1, int(math.ceil(clip_duration * fps)) + 1)

    engine = normalize_export_engine(getattr(render_options, "export_engine", EXPORT_ENGINE_AUTO_LABEL))
    quality = normalize_export_quality(getattr(render_options, "export_quality", EXPORT_QUALITY_BALANCED_LABEL))
    errors: list[str] = []
    ffmpeg_path = find_ffmpeg()

    if engine != "legacy" and ffmpeg_path:
        candidates = encoder_candidates_for_engine(engine, ffmpeg_path)
        for candidate in candidates:
            if not probe_encoder(ffmpeg_path, candidate.name, quality):
                errors.append(f"{candidate.label} was listed by FFmpeg but failed its encoder probe.")
                continue
            try:
                return _render_export_video_ffmpeg(
                    analysis,
                    geom,
                    render_options,
                    ffmpeg_path=ffmpeg_path,
                    encoder=candidate,
                    quality=quality,
                    clip_start=clip_start,
                    clip_end=clip_end,
                    clip_duration=clip_duration,
                    total_frames=total_frames,
                    progress_callback=progress_callback,
                )
            except Exception as exc:
                errors.append(str(exc))
                if engine in {"gpu", "cpu"}:
                    continue
        if engine == "gpu":
            detail = "\n".join(errors[-4:]) if errors else "No supported hardware H.264 encoder was found."
            raise RuntimeError(f"GPU export was requested, but no hardware encoder completed successfully.\n{detail}")
    elif engine != "legacy" and not ffmpeg_path:
        errors.append("FFmpeg was not found on PATH.")

    if progress_callback is not None and engine == "auto" and errors:
        progress_callback(0.0, "GPU/FFmpeg export unavailable; falling back to the legacy OpenCV writer.")

    return _render_export_video_legacy_opencv(
        analysis,
        geom,
        render_options,
        clip_start=clip_start,
        clip_end=clip_end,
        clip_duration=clip_duration,
        total_frames=total_frames,
        progress_callback=progress_callback,
    )
