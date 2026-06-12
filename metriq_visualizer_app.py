# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import csv
import faulthandler
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import matplotlib
import numpy as np
import pyqtgraph as pg
try:
    import pyqtgraph.opengl as gl
except ModuleNotFoundError as exc:
    if getattr(exc, "name", "") == "OpenGL":
        raise SystemExit(
            "Missing dependency: PyOpenGL. Activate the app venv and run 'pip install PyOpenGL PyOpenGL_accelerate' "
            "or reinstall from requirements.txt."
        ) from exc
    raise
from PySide6.QtCore import QLibraryInfo, QObject, QRectF, QSize, QSignalBlocker, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon, QImage, QKeySequence, QPainter, QPalette, QPen, QPixmap, QShortcut, QSurfaceFormat, QVector4D
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from metriq_visualizer_core import (
    DEFAULT_PRESETS,
    DEFAULT_PRESET_DISPLAY_LABELS,
    FEATURE_FRIENDLY_LABELS,
    AnalysisResult,
    GeometryResult,
    analysis_from_table_file,
    analyze_media,
    build_geometry,
    format_feature_reference,
    is_table_file,
    is_video_file,
    nearest_time_index,
)
from metriq_visualizer_layout import (
    FIT_MODES,
    LAYOUT_ITEM_ORDER,
    LAYOUT_ITEM_TITLES,
    ExportLayoutSpec,
    analysis_focus_export_layout,
    balanced_export_layout,
    default_export_layout,
    geometry_focus_export_layout,
    overlay_export_layout,
)
from metriq_visualizer_export_presets import EXPORT_PRESET_MAP, EXPORT_PRESETS, EXPORT_PRESET_TITLE_TO_KEY
from metriq_visualizer_profiles import DIAGNOSTIC_LOG_PATH, FREEZE_LOG_PATH, PERF_LOG_PATH, ProfileStore
from metriq_visualizer_projects import LEGACY_PROJECT_EXTENSIONS, PROJECT_EXTENSION, build_project_payload, load_project, save_project
from metriq_visualizer_preset_files import PRESET_EXTENSION, build_preset_payload, load_preset, save_preset
from metriq_visualizer_cinematics import (
    Bookmark,
    CameraKeyframe,
    TimeRegion,
    camera_params_for_time,
    deserialize_bookmarks,
    deserialize_keyframes,
    deserialize_regions,
    serialize_bookmarks,
    serialize_keyframes,
    serialize_regions,
)
from metriq_visualizer_export_queue import ExportQueueJob, deserialize_queue, serialize_queue
from metriq_visualizer_export_engine import EXPORT_ENGINE_AUTO_LABEL, EXPORT_ENGINE_CHOICES, EXPORT_QUALITY_BALANCED_LABEL, EXPORT_QUALITY_CHOICES
from metriq_visualizer_visuals import (
    EMPTY_FLOAT,
    EMPTY_RGBA,
    build_tube_mesh,
    compute_axis_limits,
    compute_trail_visual_state,
    compute_tube_radii,
    prepare_color_mapping,
)
from metriq_visualizer_diagnostics import (
    BaselineModel,
    LiveDiagnosticsEngine,
    available_input_devices,
    deserialize_baseline_model,
    serialize_baseline_model,
    snapshot_to_analysis_geometry,
)

APP_NAME = "Metriq Visualizer"
APP_VERSION = "1.10.18"
APP_TITLE = APP_NAME
APP_WINDOW_TITLE = f"{APP_NAME} v{APP_VERSION}"
FUN_EDITION = True
FUN_EXPORT_WIDTH = 1920
FUN_EXPORT_HEIGHT = 1920
FUN_EXPORT_FPS = 30
TABLE_PRESET_NAME = "Table / PCA explorer"

DEFAULT_STARTUP_PRESET = "Pitch/Timbre/Motion"
DEFAULT_POINT_LIFESPAN = 3.0
DEFAULT_POINT_SIZE_SCALE = 0.4
DEFAULT_HEAD_SIZE_SCALE = 0.24
DEFAULT_HALO_SIZE_SCALE = 0.45
DEFAULT_FLASH_SIZE_SCALE = 0.05

VISUALIZER_BEHAVIOR_CUSTOM_LABEL = "Custom"
VISUALIZER_BEHAVIOR_PRESET_DIRECTORY_NAME = "presets"
DEFAULT_VISUALIZER_BEHAVIOR_PRESET = None
VISUALIZER_BEHAVIOR_STATE_KEYS = (
    "history_mode",
    "point_lifespan",
    "fade_curve",
    "line_width",
    "comet_duration",
    "flash_duration",
    "alpha",
    "point_size_scale",
    "path_curve_mode",
    "curve_detail",
    "render_mode",
    "tube_radius_scale",
    "tube_sides",
    "tube_follow_size",
    "tube_taper",
    "connect_lines",
    "ghost_path",
    "show_head_marker",
    "head_size_scale",
    "halo_size_scale",
    "flash_size_scale",
    "show_axes",
    "show_axis_labels",
    "point_label_mode",
    "point_label_content",
    "max_point_labels",
)

DEFAULT_THEME_KEY = "dark"
THEMES = {
    "dark": {
        "label": "Dark",
        "app_bg": "#0a1016",
        "surface_bg": "#101722",
        "panel_bg": "#131c27",
        "input_bg": "#16212d",
        "input_active_bg": "#1d2a39",
        "text": "#e9f1fa",
        "muted_text": "#a8b4c5",
        "accent": "#34d1ad",
        "warning": "#ffcf7d",
        "error": "#ff8f8f",
        "outline": "#243547",
        "axis_line": "#425468",
        "selection_text": "#081018",
    },
}
METRIQ_SPECTROGRAM_GRADIENT = {
    "dark": ["#0b1118", "#0d2330", "#0a4f60", "#06a269", "#5ed39c", "#5fa6f7", "#d9ecff"],
}
CURRENT_THEME_KEY = DEFAULT_THEME_KEY


def _project_file_dialog_filter(include_legacy: bool = True) -> str:
    patterns = [f"*{PROJECT_EXTENSION}"]
    if include_legacy:
        patterns.extend(f"*{ext}" for ext in LEGACY_PROJECT_EXTENSIONS)
    return f"Metriq Visualizer project ({' '.join(patterns)})"


def _preset_file_dialog_filter() -> str:
    return f"Metriq Visualizer preset (*{PRESET_EXTENSION})"


def _safe_dialog_stem(text: str, fallback: str) -> str:
    value = (text or "").strip() or fallback
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return value or fallback


def _set_theme_globals(theme_key: str) -> str:
    global CURRENT_THEME_KEY, APP_BG, SURFACE_BG, PANEL_BG, INPUT_BG, INPUT_ACTIVE_BG, TEXT, MUTED_TEXT, ACCENT, WARNING, ERROR, OUTLINE, AXIS_LINE, SELECTION_TEXT
    resolved = theme_key if theme_key in THEMES else DEFAULT_THEME_KEY
    theme = THEMES[resolved]
    CURRENT_THEME_KEY = resolved
    APP_BG = str(theme["app_bg"])
    SURFACE_BG = str(theme["surface_bg"])
    PANEL_BG = str(theme["panel_bg"])
    INPUT_BG = str(theme["input_bg"])
    INPUT_ACTIVE_BG = str(theme["input_active_bg"])
    TEXT = str(theme["text"])
    MUTED_TEXT = str(theme["muted_text"])
    ACCENT = str(theme["accent"])
    WARNING = str(theme["warning"])
    ERROR = str(theme["error"])
    OUTLINE = str(theme["outline"])
    AXIS_LINE = str(theme["axis_line"])
    SELECTION_TEXT = str(theme["selection_text"])
    return resolved

_set_theme_globals(DEFAULT_THEME_KEY)

def _brand_asset_path(filename: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / filename

def _load_brand_pixmap(filename: str) -> QPixmap | None:
    path = _brand_asset_path(filename)
    if not path.exists():
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    return pixmap


class ResponsivePixmapLabel(QLabel):
    def __init__(
        self,
        pixmap: QPixmap,
        *,
        preferred_height: int = 46,
        min_width: int = 84,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_pixmap = QPixmap(pixmap)
        self._preferred_height = max(1, int(preferred_height))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.setContentsMargins(0, 0, 0, 0)
        self.setMinimumWidth(max(1, int(min_width)))
        self.setMinimumHeight(self._preferred_height)
        self.setMaximumHeight(self._preferred_height + 6)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._update_scaled_pixmap()

    def setSourcePixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = QPixmap(pixmap)
        self._update_scaled_pixmap()

    def preferredWidth(self) -> int:
        if self._source_pixmap.isNull():
            return max(self.minimumWidth(), 96)
        scaled = self._source_pixmap.scaledToHeight(self._preferred_height, Qt.TransformationMode.SmoothTransformation)
        return max(self.minimumWidth(), scaled.width())

    def sizeHint(self):  # noqa: D401
        return QSize(self.preferredWidth(), self._preferred_height)

    def minimumSizeHint(self):  # noqa: D401
        return QSize(max(60, min(self.minimumWidth(), self.preferredWidth())), self._preferred_height)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self) -> None:
        if self._source_pixmap.isNull():
            return
        rect = self.contentsRect()
        avail_w = max(1, rect.width())
        avail_h = max(1, min(rect.height(), self._preferred_height))
        target = self._source_pixmap.scaled(
            avail_w,
            avail_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        super().setPixmap(target)


class ElidedLabel(QLabel):
    def __init__(
        self,
        text: str = "",
        *,
        elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._elide_mode = elide_mode
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(120)
        self.setWordWrap(False)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = str(text)
        self._apply_elided_text()

    def fullText(self) -> str:
        return self._full_text

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_elided_text()

    def _apply_elided_text(self) -> None:
        width = max(24, self.contentsRect().width())
        metrics = self.fontMetrics()
        rendered = metrics.elidedText(self._full_text, self._elide_mode, width)
        super().setText(rendered)
        self.setToolTip(self._full_text if rendered != self._full_text else "")


def _hex_to_rgba_float(hex_color: str) -> np.ndarray:
    value = str(hex_color).strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Unsupported color: {hex_color}")
    return np.array([
        int(value[0:2], 16) / 255.0,
        int(value[2:4], 16) / 255.0,
        int(value[4:6], 16) / 255.0,
        1.0,
    ], dtype=np.float32)


def _gradient_lut(colors: list[str], size: int = 256) -> np.ndarray:
    if len(colors) < 2:
        raise ValueError("At least two colors are required to build a LUT.")
    rgba = np.stack([_hex_to_rgba_float(color) for color in colors], axis=0)
    stops = np.linspace(0.0, 1.0, num=rgba.shape[0], dtype=np.float32)
    xs = np.linspace(0.0, 1.0, num=max(2, int(size)), dtype=np.float32)
    lut = np.empty((xs.size, 4), dtype=np.float32)
    for idx in range(4):
        lut[:, idx] = np.interp(xs, stops, rgba[:, idx])
    return np.clip(lut * 255.0, 0.0, 255.0).astype(np.ubyte)


def _metriq_spectrogram_lut(theme_key: str | None = None, size: int = 256) -> np.ndarray:
    resolved = theme_key if theme_key in METRIQ_SPECTROGRAM_GRADIENT else CURRENT_THEME_KEY
    colors = METRIQ_SPECTROGRAM_GRADIENT.get(resolved, METRIQ_SPECTROGRAM_GRADIENT["dark"])
    return _gradient_lut(colors, size=size)


EMPTY_POS = np.zeros((1, 3), dtype=np.float32)
EMPTY_COLOR = np.array([[1.0, 1.0, 1.0, 0.0]], dtype=np.float32)
EMPTY_SIZE = np.array([0.0], dtype=np.float32)

PERFORMANCE_PRESETS = {
    "Stable": {
        "live_fps": 20,
        "live_point_budget": 1400,
        "preview_scale": 0.52,
    },
    "Balanced": {
        "live_fps": 28,
        "live_point_budget": 2400,
        "preview_scale": 0.68,
    },
    "Quality": {
        "live_fps": 40,
        "live_point_budget": 3800,
        "preview_scale": 0.86,
    },
}

ANALYSIS_IMAGE_MAX_COLUMNS = 3200
ANALYSIS_TRACE_MAX_POINTS = 5200
PERF_LOG_INTERVAL_SECONDS = 12.0


@dataclass
class ViewParams:
    current_time: float
    base_alpha: float
    history_mode: str
    point_lifespan: float
    fade_curve: float
    max_points: int
    connect_lines: bool
    line_width: float
    path_curve_mode: str
    curve_detail: int
    comet_duration: float
    flash_duration: float
    ghost_path: bool
    autorotate: bool
    rotation_speed: float
    elevation: float
    zoom: float
    point_size_scale: float
    render_mode: str
    tube_radius_scale: float
    tube_sides: int
    tube_follow_size: bool
    tube_taper: float
    show_head_marker: bool
    head_size_scale: float
    halo_size_scale: float
    flash_size_scale: float
    show_axes: bool
    show_axis_labels: bool
    point_label_mode: str
    point_label_content: str
    max_point_labels: int


class WorkerBridge(QObject):
    analysisFinished = Signal(int, object)
    analysisError = Signal(int, str)
    geometryFinished = Signal(int, object)
    geometryError = Signal(int, str)
    exportFinished = Signal(str)
    exportError = Signal(str)
    exportProgress = Signal(float, str)
    batchFinished = Signal(str)
    batchError = Signal(str)
    previewFrameReady = Signal(object, object)
    previewFrameError = Signal(object, str)


class SilentFreezeWatchdog:
    def __init__(self, store: ProfileStore, timeout_seconds: float = 3.0, cooldown_seconds: float = 12.0):
        self.store = store
        self.timeout_seconds = float(timeout_seconds)
        self.cooldown_seconds = float(cooldown_seconds)
        self.last_heartbeat = time.time()
        self._last_dump = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        self.last_heartbeat = time.time()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            now = time.time()
            if now - self.last_heartbeat <= self.timeout_seconds:
                continue
            if now - self._last_dump < self.cooldown_seconds:
                continue
            self._last_dump = now
            try:
                self._dump(now)
            except Exception:
                pass

    def _dump(self, now: float) -> None:
        parts = [
            f"[{datetime.now().isoformat(timespec='seconds')}] UI heartbeat stalled for {now - self.last_heartbeat:0.2f}s\n",
        ]
        for thread_id, frame in sys._current_frames().items():
            parts.append(f"\n--- Thread {thread_id} ---\n")
            parts.extend(traceback.format_stack(frame))
        self.store.append_freeze_log("".join(parts))


class GeometryGLView(gl.GLViewWidget):
    cameraChanged = Signal()
    resetRequested = Signal()
    pointPicked = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent=parent)
        self.setBackgroundColor(pg.mkColor(APP_BG))
        self.opts["fov"] = 60
        self._base_limits = {"x": (-1.0, 1.0), "y": (-1.0, 1.0), "z": (-1.0, 1.0)}
        self._center = pg.Vector(0.0, 0.0, 0.0)
        self._base_distance = 8.0
        self._full_path_pos = np.zeros((0, 3), dtype=np.float32)
        self._ghost_colors = np.zeros((0, 4), dtype=np.float32)
        self._analysis: AnalysisResult | None = None
        self._geometry: GeometryResult | None = None
        self._active_mask_full = np.ones((0,), dtype=bool)
        self._max_span = 1.0
        self._label_offset = 0.05
        self._axis_label_positions: dict[str, np.ndarray] = {
            "x": np.zeros(3, dtype=np.float32),
            "y": np.zeros(3, dtype=np.float32),
            "z": np.zeros(3, dtype=np.float32),
        }
        self._text_supported = hasattr(gl, "GLTextItem")
        self._axis_label_font = QFont("Helvetica", 11)
        self._point_label_font = QFont("Helvetica", 10)
        self._axis_label_items: dict[str, object] = {}
        self._point_label_items: list[object] = []
        self._last_camera_signature: tuple[float, float, float] | None = None
        self._last_axis_label_visibility: bool | None = None
        self._cached_state_key: tuple | None = None
        self._cached_state = None
        self._last_data_signature: tuple | None = None
        self._ghost_budget_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self._visible_positions = np.zeros((0, 3), dtype=np.float32)
        self._visible_indices = np.zeros((0,), dtype=np.int32)
        self._selected_pick_idx: int | None = None
        self._selected_pick_pos = np.zeros(3, dtype=np.float32)
        self._press_pos = None

        self.axis_item = gl.GLAxisItem()
        self.grid_item = gl.GLGridItem()
        self.addItem(self.grid_item)
        self.addItem(self.axis_item)

        self.ghost_item = gl.GLLinePlotItem(mode="line_strip", antialias=True)
        self.ghost_item.setGLOptions("translucent")
        self.addItem(self.ghost_item)

        try:
            self.tube_item = gl.GLMeshItem(drawEdges=False, drawFaces=True, smooth=False, shader="shaded")
        except TypeError:
            self.tube_item = gl.GLMeshItem()
        self.tube_item.setGLOptions("translucent")
        self.tube_item.setVisible(False)
        self.addItem(self.tube_item)

        self.trail_item = gl.GLLinePlotItem(mode="line_strip", antialias=True)
        self.trail_item.setGLOptions("translucent")
        self.addItem(self.trail_item)

        self.comet_item = gl.GLLinePlotItem(mode="line_strip", antialias=True)
        self.comet_item.setGLOptions("additive")
        self.addItem(self.comet_item)

        self.points_item = gl.GLScatterPlotItem(pxMode=True)
        self.points_item.setGLOptions("translucent")
        self.addItem(self.points_item)

        self.halo_item = gl.GLScatterPlotItem(pxMode=True)
        self.halo_item.setGLOptions("additive")
        self.addItem(self.halo_item)

        self.head_item = gl.GLScatterPlotItem(pxMode=True)
        self.head_item.setGLOptions("translucent")
        self.addItem(self.head_item)

        self.flash_item = gl.GLScatterPlotItem(pxMode=True)
        self.flash_item.setGLOptions("additive")
        self.addItem(self.flash_item)

        self.pick_item = gl.GLScatterPlotItem(pxMode=True)
        self.pick_item.setGLOptions("additive")
        self.pick_item.setVisible(False)
        self.addItem(self.pick_item)

        if self._text_supported:
            for axis in ("x", "y", "z"):
                item = gl.GLTextItem(pos=np.zeros(3, dtype=np.float32), color=pg.mkColor(TEXT), text="", font=self._axis_label_font)
                item.setVisible(False)
                self.addItem(item)
                self._axis_label_items[axis] = item

    def _invalidate_render_cache(self) -> None:
        self._last_camera_signature = None
        self._last_data_signature = None

    def _request_repaint_burst(self) -> None:
        self._invalidate_render_cache()
        try:
            self.update()
        except Exception:
            return
        for delay_ms in (0, 60, 180):
            QTimer.singleShot(delay_ms, self._safe_update)

    def _safe_update(self) -> None:
        try:
            self.update()
        except Exception:
            pass

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._request_repaint_burst()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._request_repaint_burst()

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.resetRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        try:
            self._press_pos = event.position()
        except Exception:
            self._press_pos = None
        super().mousePressEvent(event)

    def wheelEvent(self, event):  # noqa: N802
        super().wheelEvent(event)
        self.cameraChanged.emit()

    def mouseReleaseEvent(self, event):  # noqa: N802
        moved_far = False
        try:
            if self._press_pos is not None:
                delta = event.position() - self._press_pos
                moved_far = abs(float(delta.x())) > 6.0 or abs(float(delta.y())) > 6.0
        except Exception:
            moved_far = False
        super().mouseReleaseEvent(event)
        self.cameraChanged.emit()
        if event.button() == Qt.MouseButton.LeftButton and not moved_far:
            self._pick_point_at_event(event)

    def set_scene_geometry(
        self,
        geom: GeometryResult,
        rgba_full: np.ndarray,
        analysis: AnalysisResult | None = None,
        reset_camera: bool = False,
    ) -> None:
        self._analysis = analysis
        self._geometry = geom
        self._base_limits = compute_axis_limits(geom.x_full, geom.y_full, geom.z_full)
        xlo, xhi = self._base_limits["x"]
        ylo, yhi = self._base_limits["y"]
        zlo, zhi = self._base_limits["z"]
        center = (
            0.5 * (xlo + xhi),
            0.5 * (ylo + yhi),
            0.5 * (zlo + zhi),
        )
        self._center = pg.Vector(*center)
        max_span = max(xhi - xlo, yhi - ylo, zhi - zlo, 1.0)
        self._max_span = max_span
        self._label_offset = max_span * 0.032
        self._base_distance = max_span * 2.8
        self._last_camera_signature = None
        self._last_axis_label_visibility = None
        self._cached_state_key = None
        self._cached_state = None
        self._last_data_signature = None
        self._ghost_budget_cache.clear()
        self._visible_positions = np.zeros((0, 3), dtype=np.float32)
        self._visible_indices = np.zeros((0,), dtype=np.int32)
        self._selected_pick_idx = None
        self._set_scatter(self.pick_item, EMPTY_POS, EMPTY_COLOR, EMPTY_SIZE, visible=False)
        self._set_tube_mesh(None, visible=False)

        self._full_path_pos = np.column_stack([geom.x_full, geom.y_full, geom.z_full]).astype(np.float32, copy=False)
        self._active_mask_full = np.asarray(getattr(geom, "active_mask_full", np.ones(geom.times_full.size, dtype=bool)), dtype=bool).reshape(-1)
        if self._active_mask_full.size != geom.times_full.size:
            self._active_mask_full = np.ones(geom.times_full.size, dtype=bool)
        ghost = np.asarray(rgba_full, dtype=np.float32).copy()
        if ghost.shape[0] > 0:
            ghost[:, :3] = np.clip(0.62 * ghost[:, :3] + 0.14, 0.0, 1.0)
            ghost[:, 3] = 0.075
            if self._active_mask_full.size == ghost.shape[0]:
                ghost[~self._active_mask_full, 3] = 0.0
        self._ghost_colors = ghost

        self.axis_item.resetTransform()
        axis_size = max_span * 0.55
        self.axis_item.setSize(axis_size, axis_size, axis_size)
        self.axis_item.translate(center[0], center[1], center[2])
        self._axis_label_positions = {
            "x": np.array([center[0] + axis_size * 1.05, center[1], center[2]], dtype=np.float32),
            "y": np.array([center[0], center[1] + axis_size * 1.05, center[2]], dtype=np.float32),
            "z": np.array([center[0], center[1], center[2] + axis_size * 1.05], dtype=np.float32),
        }

        self.grid_item.resetTransform()
        grid_size = max_span * 1.5
        grid_spacing = max(max_span / 12.0, 0.001)
        self.grid_item.setSize(grid_size, grid_size)
        self.grid_item.setSpacing(grid_spacing, grid_spacing)
        self.grid_item.translate(center[0], center[1], zlo)

        if reset_camera:
            self.reset_camera(elevation=24.0, azimuth=35.0, zoom=1.0)
        self._request_repaint_burst()

    def _set_camera_center_compat(self, distance: float, elevation: float, azimuth: float) -> None:
        self.opts["center"] = pg.Vector(self._center)
        try:
            self.setCameraPosition(pos=self._center, distance=distance, elevation=elevation, azimuth=azimuth)
            return
        except TypeError:
            pass
        try:
            self.setCameraPosition(center=self._center, distance=distance, elevation=elevation, azimuth=azimuth)
            return
        except TypeError:
            pass
        self.opts["distance"] = float(distance)
        self.opts["elevation"] = float(elevation)
        self.opts["azimuth"] = float(azimuth)
        self.update()

    def reset_camera(self, elevation: float, azimuth: float, zoom: float) -> None:
        distance = self.distance_for_zoom(zoom)
        self._last_camera_signature = None
        self._set_camera_center_compat(distance=distance, elevation=elevation, azimuth=azimuth)
        self._last_camera_signature = (round(float(distance), 4), round(float(elevation), 4), round(float(azimuth), 4))
        self.cameraChanged.emit()

    def distance_for_zoom(self, zoom: float) -> float:
        zoom = max(0.15, float(zoom))
        return float(self._base_distance / zoom)

    def zoom_for_distance(self, distance: float) -> float:
        if distance <= 1e-6:
            return 1.0
        return float(self._base_distance / distance)

    def current_distance(self) -> float:
        return float(self.opts.get("distance", self._base_distance))

    def set_view_params(self, elevation: float, azimuth: float, zoom: float) -> None:
        distance = self.distance_for_zoom(zoom)
        signature = (round(float(distance), 4), round(float(elevation), 4), round(float(azimuth), 4))
        if self._last_camera_signature == signature:
            return
        self._set_camera_center_compat(
            distance=distance,
            elevation=float(elevation),
            azimuth=float(azimuth),
        )
        self._last_camera_signature = signature

    def _segment_keep_mask(self, indices: np.ndarray) -> np.ndarray:
        if indices.size <= 1:
            return np.zeros(0, dtype=bool)
        diffs = np.diff(np.asarray(indices, dtype=int))
        positive = diffs[diffs > 0]
        if positive.size == 0:
            return np.ones(diffs.size, dtype=bool)
        typical = max(1, int(np.median(positive)))
        threshold = max(2, typical * 3)
        return diffs <= threshold

    def _ghost_path_for_budget(self, budget: int) -> tuple[np.ndarray, np.ndarray]:
        budget = max(64, int(budget))
        cached = self._ghost_budget_cache.get(budget)
        if cached is not None:
            return cached
        count = int(self._full_path_pos.shape[0])
        if count <= 1:
            payload = (EMPTY_POS, EMPTY_COLOR)
        else:
            indices = np.arange(count, dtype=int)
            if self._active_mask_full.size == count:
                indices = indices[self._active_mask_full]
            if indices.size <= 1:
                payload = (EMPTY_POS, EMPTY_COLOR)
            else:
                if indices.size > budget:
                    keep = np.linspace(0, indices.size - 1, num=budget, dtype=int)
                    indices = indices[keep]
                segment_keep = self._segment_keep_mask(indices)
                if not np.any(segment_keep):
                    payload = (EMPTY_POS, EMPTY_COLOR)
                else:
                    seg = np.stack([self._full_path_pos[indices[:-1]], self._full_path_pos[indices[1:]]], axis=1)[segment_keep]
                    colors = np.repeat(self._ghost_colors[indices[1:]][segment_keep], 2, axis=0)
                    payload = (seg.reshape(-1, 3).astype(np.float32, copy=False), colors.astype(np.float32, copy=False))
        self._ghost_budget_cache[budget] = payload
        return payload

    def _state_cache_key(self, geom: GeometryResult, params: ViewParams) -> tuple:
        head_idx = 0 if geom.times_full.size == 0 else int(np.searchsorted(geom.times_full, float(params.current_time), side="right") - 1)
        head_idx = max(0, min(head_idx, int(max(0, geom.times_full.size - 1))))
        return (
            head_idx,
            str(params.history_mode),
            round(float(params.point_lifespan), 3),
            round(float(params.fade_curve), 3),
            int(max(32, int(params.max_points))),
            bool(params.connect_lines),
            round(float(params.line_width), 3),
            str(getattr(params, "path_curve_mode", "Straight")),
            int(getattr(params, "curve_detail", 4)),
            round(float(params.comet_duration), 3),
            round(float(params.flash_duration), 3),
            round(float(params.base_alpha), 3),
        )

    def _cached_visual_state_for_params(self, geom: GeometryResult, rgba_full: np.ndarray, params: ViewParams):
        key = self._state_cache_key(geom, params)
        if self._cached_state_key != key or self._cached_state is None:
            head_idx = int(key[0])
            if geom.times_full.size == 0:
                feature_time = 0.0
            else:
                feature_time = float(geom.times_full[head_idx])
            self._cached_state = compute_trail_visual_state(
                geom,
                rgba_full=rgba_full,
                current_time=feature_time,
                base_alpha=float(params.base_alpha),
                history_mode=str(params.history_mode),
                point_lifespan=float(params.point_lifespan),
                fade_curve=float(params.fade_curve),
                max_points=max(32, int(params.max_points)),
                connect_lines=bool(params.connect_lines),
                line_width=max(0.0, float(params.line_width)),
                comet_duration=max(0.0, float(params.comet_duration)),
                flash_duration=max(0.0, float(params.flash_duration)),
                curve_mode=str(getattr(params, "path_curve_mode", "Straight")),
                curve_samples=max(1, int(getattr(params, "curve_detail", 4))),
            )
            self._cached_state_key = key
        return self._cached_state_key, self._cached_state

    def _set_tube_mesh(self, mesh, visible: bool) -> None:
        if not visible or mesh is None or getattr(mesh, "faces", np.empty((0, 3))).size == 0:
            try:
                self.tube_item.setVisible(False)
            except Exception:
                pass
            return
        vertices = np.asarray(mesh.vertices, dtype=np.float32)
        faces = np.asarray(mesh.faces, dtype=np.int32)
        face_colors = np.asarray(mesh.face_colors, dtype=np.float32)
        try:
            meshdata = gl.MeshData(vertexes=vertices, faces=faces, faceColors=face_colors)
            self.tube_item.setMeshData(meshdata=meshdata)
        except TypeError:
            self.tube_item.setMeshData(vertexes=vertices, faces=faces, faceColors=face_colors)
        self.tube_item.setVisible(True)

    def _set_scatter(self, item: gl.GLScatterPlotItem, pos: np.ndarray, color: np.ndarray, size: np.ndarray, visible: bool) -> None:
        if not visible or pos.size == 0:
            item.setData(pos=EMPTY_POS, color=EMPTY_COLOR, size=EMPTY_SIZE)
            item.setVisible(False)
            return
        item.setData(
            pos=np.asarray(pos, dtype=np.float32),
            color=np.asarray(color, dtype=np.float32),
            size=np.asarray(size, dtype=np.float32),
        )
        item.setVisible(True)

    def _set_line(self, item: gl.GLLinePlotItem, pos: np.ndarray, color: np.ndarray, width: float, visible: bool, mode: str = "line_strip") -> None:
        if not visible or pos.shape[0] < 2:
            item.setData(pos=EMPTY_POS, color=EMPTY_COLOR, width=1.0, mode=mode)
            item.setVisible(False)
            return
        item.setData(
            pos=np.asarray(pos, dtype=np.float32),
            color=np.asarray(color, dtype=np.float32),
            width=max(1.0, float(width)),
            mode=mode,
            antialias=True,
        )
        item.setVisible(True)

    def _ensure_point_label_items(self, count: int) -> None:
        if not self._text_supported:
            return
        count = max(0, int(count))
        while len(self._point_label_items) < count:
            item = gl.GLTextItem(pos=np.zeros(3, dtype=np.float32), color=pg.mkColor(TEXT), text="", font=self._point_label_font)
            item.setVisible(False)
            self.addItem(item)
            self._point_label_items.append(item)
        for item in self._point_label_items[count:]:
            item.setVisible(False)

    def _hide_all_point_labels(self) -> None:
        if not self._text_supported:
            return
        for item in self._point_label_items:
            item.setVisible(False)

    def _label_text_for_index(self, geom: GeometryResult, idx: int, content_mode: str) -> str:
        idx = max(0, min(int(idx), int(geom.times_full.size - 1)))
        time_text = f"{float(geom.times_full[idx]):0.2f}s"
        freq_text = ""
        if self._analysis is not None and "dominant_freq_hz" in self._analysis.features:
            freq_text = f"{float(self._analysis.features['dominant_freq_hz'][idx]):0.0f} Hz"
        if content_mode == "Time":
            return time_text
        if content_mode == "Dominant Hz":
            return freq_text or time_text
        if content_mode == "Index":
            return f"#{idx}"
        if freq_text:
            return f"{time_text} • {freq_text}"
        return time_text

    def _update_axis_labels(self, geom: GeometryResult, visible: bool) -> None:
        if not self._text_supported:
            return
        for axis, item in self._axis_label_items.items():
            if visible:
                label = f"{axis.upper()}: {geom.labels[axis]}"
                item.setData(pos=self._axis_label_positions[axis], color=pg.mkColor(TEXT), text=label, font=self._axis_label_font)
                item.setVisible(True)
            else:
                item.setVisible(False)

    def _update_point_labels(
        self,
        geom: GeometryResult,
        state,
        label_mode: str,
        content_mode: str,
        max_labels: int,
    ) -> None:
        if not self._text_supported or label_mode == "Off" or geom.times_full.size == 0:
            self._hide_all_point_labels()
            return

        if label_mode == "Current point":
            label_indices = np.array([int(state.head_idx)], dtype=int)
        else:
            visible_idx = np.asarray(state.visible_idx, dtype=int)
            if visible_idx.size == 0:
                self._hide_all_point_labels()
                return
            max_labels = max(1, int(max_labels))
            keep = np.linspace(0, visible_idx.size - 1, num=min(max_labels, visible_idx.size), dtype=int)
            label_indices = np.unique(np.append(visible_idx[keep], int(state.head_idx)))

        self._ensure_point_label_items(label_indices.size)
        offset = np.array([self._label_offset, self._label_offset, self._label_offset], dtype=np.float32)
        for item, idx in zip(self._point_label_items, label_indices, strict=False):
            pos = np.array([geom.x_full[idx], geom.y_full[idx], geom.z_full[idx]], dtype=np.float32) + offset
            color = TEXT if int(idx) == int(state.head_idx) else MUTED_TEXT
            font = self._axis_label_font if int(idx) == int(state.head_idx) else self._point_label_font
            item.setData(pos=pos, color=pg.mkColor(color), text=self._label_text_for_index(geom, int(idx), content_mode), font=font)
            item.setVisible(True)
        for item in self._point_label_items[label_indices.size:]:
            item.setVisible(False)

    def render_state(self, geom: GeometryResult, rgba_full: np.ndarray, params: ViewParams, base_azimuth: float) -> int:
        state_key, state = self._cached_visual_state_for_params(geom, rgba_full, params)

        azimuth = float(base_azimuth)
        if params.autorotate:
            azimuth += float(params.rotation_speed) * float(params.current_time)
        self.set_view_params(elevation=float(params.elevation), azimuth=azimuth, zoom=float(params.zoom))

        self.axis_item.setVisible(bool(params.show_axes))
        self.grid_item.setVisible(bool(params.show_axes))
        axis_labels_visible = bool(params.show_axis_labels)
        if self._last_axis_label_visibility != axis_labels_visible:
            self._update_axis_labels(geom, visible=axis_labels_visible)
            self._last_axis_label_visibility = axis_labels_visible

        ghost_budget = max(256, int(params.max_points) * 3)
        ghost_pos, ghost_colors = self._ghost_path_for_budget(ghost_budget)

        data_signature = (
            state_key,
            int(max(0, self.width())),
            int(max(0, self.height())),
            round(float(params.point_size_scale), 3),
            str(getattr(params, "render_mode", "Points + line")),
            round(float(getattr(params, "tube_radius_scale", 1.0)), 3),
            int(getattr(params, "tube_sides", 12)),
            bool(getattr(params, "tube_follow_size", True)),
            round(float(getattr(params, "tube_taper", 0.2)), 3),
            bool(params.show_head_marker),
            round(float(params.head_size_scale), 3),
            round(float(params.halo_size_scale), 3),
            round(float(params.flash_size_scale), 3),
            bool(params.ghost_path),
            bool(params.connect_lines),
            round(float(params.line_width), 3),
            str(getattr(params, "path_curve_mode", "Straight")),
            int(getattr(params, "curve_detail", 4)),
            bool(params.show_axes),
            bool(axis_labels_visible),
            str(params.point_label_mode),
            str(params.point_label_content),
            int(params.max_point_labels),
            int(ghost_budget),
        )
        if self._last_data_signature == data_signature:
            self.update()
            return int(state.head_idx)
        self._last_data_signature = data_signature

        self._set_line(
            self.ghost_item,
            ghost_pos,
            ghost_colors,
            width=1.0,
            visible=bool(params.ghost_path and ghost_pos.shape[0] >= 2),
            mode="lines",
        )

        if state.x.size > 0:
            pos = np.column_stack([state.x, state.y, state.z]).astype(np.float32, copy=False)
            self._visible_positions = pos.copy()
            self._visible_indices = np.asarray(state.visible_idx, dtype=np.int32).copy()
            render_mode = str(getattr(params, "render_mode", "Points + line") or "Points + line")
            show_tube = render_mode in {"Tube", "Tube + points"}
            show_points = render_mode in {"Points + line", "Points only", "Tube + points"}
            show_trail = render_mode == "Points + line" and params.connect_lines and params.line_width > 0.02
            show_comet = render_mode in {"Points + line", "Tube", "Tube + points"}

            scaled_point_sizes = np.asarray(state.point_sizes * float(params.point_size_scale), dtype=np.float32)
            self._set_scatter(
                self.points_item,
                pos,
                np.asarray(state.point_rgba, dtype=np.float32),
                scaled_point_sizes,
                visible=show_points,
            )

            if show_tube:
                radii = compute_tube_radii(
                    scaled_point_sizes,
                    self._max_span,
                    radius_scale=float(getattr(params, "tube_radius_scale", 1.0)),
                    follow_point_size=bool(getattr(params, "tube_follow_size", True)),
                    taper=float(getattr(params, "tube_taper", 0.2)),
                )
                tube_mesh = build_tube_mesh(
                    pos,
                    np.asarray(state.point_rgba, dtype=np.float32),
                    np.asarray(radii, dtype=np.float32),
                    visible_idx=np.asarray(state.visible_idx, dtype=np.int32),
                    sides=int(getattr(params, "tube_sides", 12)),
                    curve_mode=str(getattr(params, "path_curve_mode", "Straight")),
                    curve_samples=max(1, int(getattr(params, "curve_detail", 4))),
                )
                self._set_tube_mesh(tube_mesh, visible=tube_mesh is not None)
            else:
                self._set_tube_mesh(None, visible=False)

            if state.segments.shape[0] > 0 and show_trail:
                trail_pos = state.segments.reshape(-1, 3).astype(np.float32, copy=False)
                trail_color = np.repeat(np.asarray(state.segment_rgba, dtype=np.float32), 2, axis=0)
                trail_width = float(np.mean(state.segment_widths)) if state.segment_widths.size > 0 else float(params.line_width) * 1.6
                self._set_line(
                    self.trail_item,
                    trail_pos,
                    trail_color,
                    width=max(1.0, trail_width * 1.6),
                    visible=True,
                    mode="lines",
                )
            else:
                self._set_line(self.trail_item, EMPTY_POS, EMPTY_COLOR, width=1.0, visible=False, mode="lines")

            if state.comet_segments.shape[0] > 0 and show_comet:
                comet_pos = state.comet_segments.reshape(-1, 3).astype(np.float32, copy=False)
                comet_color = np.repeat(np.asarray(state.comet_rgba, dtype=np.float32), 2, axis=0)
                comet_width = float(np.mean(state.comet_widths)) if state.comet_widths.size > 0 else float(params.line_width) * 3.0
                self._set_line(
                    self.comet_item,
                    comet_pos,
                    comet_color,
                    width=max(2.0, comet_width),
                    visible=True,
                    mode="lines",
                )
            else:
                self._set_line(self.comet_item, EMPTY_POS, EMPTY_COLOR, width=1.0, visible=False, mode="lines")
        else:
            self._visible_positions = np.zeros((0, 3), dtype=np.float32)
            self._visible_indices = np.zeros((0,), dtype=np.int32)
            self._set_scatter(self.points_item, EMPTY_POS, EMPTY_COLOR, EMPTY_SIZE, visible=False)
            self._set_tube_mesh(None, visible=False)
            self._set_line(self.trail_item, EMPTY_POS, EMPTY_COLOR, width=1.0, visible=False, mode="lines")
            self._set_line(self.comet_item, EMPTY_POS, EMPTY_COLOR, width=1.0, visible=False, mode="lines")

        hx, hy, hz = state.head_point
        head_pos = np.array([[hx, hy, hz]], dtype=np.float32)
        if params.show_head_marker:
            halo_size = max(0.0, float(state.head_size) * float(params.halo_size_scale))
            head_size = max(0.0, float(state.head_size) * float(params.head_size_scale))
            flash_age = max(0.0, float(params.current_time) - float(geom.times_full[int(state.head_idx)])) if geom.times_full.size > 0 else 0.0
            if float(params.flash_duration) <= 1e-6:
                flash_strength = 0.0
            else:
                flash_strength = max(0.0, 1.0 - flash_age / max(1e-6, float(params.flash_duration)))
                flash_strength = flash_strength * flash_strength
            head_flash_rgba = np.array([1.0, 1.0, 1.0, 0.95 * flash_strength], dtype=np.float32)
            flash_size = max(0.0, float(state.head_size) * (1.55 + 1.35 * flash_strength) * float(params.flash_size_scale))
            self._set_scatter(
                self.halo_item,
                head_pos,
                np.array([state.head_halo_rgba], dtype=np.float32),
                np.array([halo_size], dtype=np.float32),
                visible=halo_size > 0.02,
            )
            self._set_scatter(
                self.head_item,
                head_pos,
                np.array([state.head_rgba], dtype=np.float32),
                np.array([head_size], dtype=np.float32),
                visible=head_size > 0.02,
            )
            flash_visible = float(head_flash_rgba[3]) > 1e-4 and flash_size > 0.02
            self._set_scatter(
                self.flash_item,
                head_pos,
                np.array([head_flash_rgba], dtype=np.float32),
                np.array([flash_size], dtype=np.float32),
                visible=flash_visible,
            )
        else:
            self._set_scatter(self.halo_item, EMPTY_POS, EMPTY_COLOR, EMPTY_SIZE, visible=False)
            self._set_scatter(self.head_item, EMPTY_POS, EMPTY_COLOR, EMPTY_SIZE, visible=False)
            self._set_scatter(self.flash_item, EMPTY_POS, EMPTY_COLOR, EMPTY_SIZE, visible=False)

        if self._selected_pick_idx is not None and self._geometry is not None and 0 <= int(self._selected_pick_idx) < int(self._geometry.times_full.size):
            pick_pos = np.array([[self._geometry.x_full[int(self._selected_pick_idx)], self._geometry.y_full[int(self._selected_pick_idx)], self._geometry.z_full[int(self._selected_pick_idx)]]], dtype=np.float32)
            self._set_scatter(
                self.pick_item,
                pick_pos,
                np.array([[1.0, 0.88, 0.26, 0.92]], dtype=np.float32),
                np.array([16.0], dtype=np.float32),
                visible=True,
            )
        else:
            self._set_scatter(self.pick_item, EMPTY_POS, EMPTY_COLOR, EMPTY_SIZE, visible=False)

        self._update_point_labels(
            geom,
            state,
            label_mode=str(params.point_label_mode),
            content_mode=str(params.point_label_content),
            max_labels=int(params.max_point_labels),
        )
        self.update()
        return int(state.head_idx)


    def _project_points_to_widget(self, pos: np.ndarray) -> np.ndarray | None:
        if pos.size == 0:
            return np.zeros((0, 2), dtype=np.float32)
        try:
            proj = self.projectionMatrix()
            view = self.viewMatrix()
            combined = proj * view
            width = max(1.0, float(self.width()))
            height = max(1.0, float(self.height()))
            out = np.zeros((pos.shape[0], 2), dtype=np.float32)
            for idx, (x, y, z) in enumerate(np.asarray(pos, dtype=float)):
                vec = QVector4D(float(x), float(y), float(z), 1.0)
                try:
                    clip = combined * vec
                except Exception:
                    clip = combined.map(vec)
                w = float(clip.w()) if hasattr(clip, 'w') else 1.0
                if abs(w) < 1e-8:
                    return None
                ndc_x = float(clip.x()) / w
                ndc_y = float(clip.y()) / w
                out[idx, 0] = (ndc_x + 1.0) * 0.5 * width
                out[idx, 1] = (1.0 - (ndc_y + 1.0) * 0.5) * height
            return out
        except Exception:
            return None

    def _pick_point_at_event(self, event) -> None:
        if self._visible_positions.size == 0 or self._geometry is None:
            return
        try:
            pos = event.position()
            click = np.array([float(pos.x()), float(pos.y())], dtype=np.float32)
        except Exception:
            return
        projected = self._project_points_to_widget(self._visible_positions)
        if projected is None or projected.size == 0:
            return
        distances = np.linalg.norm(projected - click[None, :], axis=1)
        if distances.size == 0:
            return
        nearest = int(np.argmin(distances))
        if float(distances[nearest]) > 18.0:
            return
        global_idx = int(self._visible_indices[nearest])
        self._selected_pick_idx = global_idx
        self._selected_pick_pos = np.asarray(self._visible_positions[nearest], dtype=np.float32)
        self._set_scatter(
            self.pick_item,
            np.array([self._selected_pick_pos], dtype=np.float32),
            np.array([[1.0, 0.88, 0.26, 0.92]], dtype=np.float32),
            np.array([16.0], dtype=np.float32),
            visible=True,
        )
        payload = {
            'index': global_idx,
            'time': float(self._geometry.times_full[global_idx]) if self._geometry.times_full.size > 0 else 0.0,
            'position': self._selected_pick_pos.copy(),
        }
        if self._analysis is not None and 'dominant_freq_hz' in self._analysis.features:
            payload['dominant_hz'] = float(self._analysis.features['dominant_freq_hz'][global_idx])
        self.pointPicked.emit(payload)


class AnalysisTabs(QWidget):
    timeClicked = Signal(float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._analysis: AnalysisResult | None = None
        self._geometry: GeometryResult | None = None
        self._active_mask_full = np.ones((0,), dtype=bool)
        self._duration = 0.0
        self._last_playhead_bucket: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        self.spectrogram_plot, self.spectrogram_img, self.spectrogram_line = self._make_image_tab("Spectrogram", "Hz")
        self.chroma_plot, self.chroma_img, self.chroma_line = self._make_image_tab("Chromagram", "Pitch class")
        self.mfcc_plot, self.mfcc_img, self.mfcc_line = self._make_image_tab("MFCC / Cepstrogram", "MFCC")

        self.traces_plot = pg.PlotWidget()
        self._style_plot(self.traces_plot.getPlotItem())
        self.traces_plot.setBackground(pg.mkColor(PANEL_BG))
        self.traces_plot.getPlotItem().addLegend(offset=(8, 8))
        self.traces_plot.getPlotItem().setLabel("left", "Value")
        self.traces_plot.getPlotItem().setLabel("bottom", "Time", units="s")
        self.traces_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((248, 251, 255, 220), width=1.0))
        self.traces_plot.addItem(self.traces_line)
        self.tabs.addTab(self.traces_plot, "Mapped traces")

        self.trace_curves: list[pg.PlotDataItem] = []
        self._set_image_luts()
        self._connect_click_to_hear()

    def _connect_click_to_hear(self) -> None:
        for plot in (self.spectrogram_plot, self.chroma_plot, self.mfcc_plot, self.traces_plot):
            try:
                plot.scene().sigMouseClicked.connect(lambda event, p=plot: self._scene_clicked(event, p))
            except Exception:
                pass

    def _scene_clicked(self, event, plot: pg.PlotWidget) -> None:
        if event is None or self._analysis is None or self._analysis.times.size == 0:
            return
        button = getattr(event, 'button', lambda: None)()
        if button != Qt.MouseButton.LeftButton:
            return
        scene_pos = event.scenePos()
        vb = plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(scene_pos):
            return
        mouse_point = vb.mapSceneToView(scene_pos)
        t = float(mouse_point.x())
        t0 = float(self._analysis.times[0]) if self._analysis.times.size > 0 else 0.0
        t1 = float(self._analysis.times[-1]) if self._analysis.times.size > 0 else 0.0
        if t1 < t0:
            t0, t1 = t1, t0
        t = max(t0, min(t1, t))
        self.timeClicked.emit(t)
        try:
            event.accept()
        except Exception:
            pass

    def _style_plot(self, plot_item: pg.PlotItem) -> None:
        plot_item.showGrid(x=True, y=True, alpha=0.12)
        plot_item.getAxis("left").setTextPen(pg.mkColor(MUTED_TEXT))
        plot_item.getAxis("bottom").setTextPen(pg.mkColor(MUTED_TEXT))
        plot_item.getAxis("left").setPen(pg.mkColor("#48505f"))
        plot_item.getAxis("bottom").setPen(pg.mkColor("#48505f"))
        plot_item.setLabel("bottom", "Time", units="s")
        plot_item.getViewBox().setBackgroundColor(pg.mkColor(PANEL_BG))

    def _make_image_tab(self, title: str, y_label: str):
        plot = pg.PlotWidget()
        item = plot.getPlotItem()
        self._style_plot(item)
        plot.setBackground(pg.mkColor(PANEL_BG))
        item.setLabel("left", y_label)
        image = pg.ImageItem(axisOrder="row-major")
        item.addItem(image)
        line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((248, 251, 255, 220), width=1.0))
        item.addItem(line)
        self.tabs.addTab(plot, title)
        return plot, image, line

    def _lut_from_mpl(self, cmap_name: str) -> np.ndarray:
        cmap = matplotlib.colormaps.get_cmap(cmap_name)
        lut = (cmap(np.linspace(0.0, 1.0, 256))[:, :4] * 255.0).astype(np.ubyte)
        return lut

    def _set_image_luts(self) -> None:
        self.spectrogram_img.setLookupTable(_metriq_spectrogram_lut(CURRENT_THEME_KEY))
        self.chroma_img.setLookupTable(self._lut_from_mpl("viridis"))
        self.mfcc_img.setLookupTable(self._lut_from_mpl("coolwarm"))

    def _downsample_matrix_cols(self, matrix: np.ndarray, target_cols: int = ANALYSIS_IMAGE_MAX_COLUMNS) -> np.ndarray:
        arr = np.asarray(matrix, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] <= target_cols:
            return arr
        keep = np.linspace(0, arr.shape[1] - 1, num=max(64, int(target_cols)), dtype=int)
        return np.ascontiguousarray(arr[:, keep], dtype=np.float32)

    def _decimate_series(self, x: np.ndarray, y: np.ndarray, target_points: int = ANALYSIS_TRACE_MAX_POINTS) -> tuple[np.ndarray, np.ndarray]:
        xs = np.asarray(x, dtype=np.float32).reshape(-1)
        ys = np.asarray(y, dtype=np.float32).reshape(-1)
        if xs.size <= target_points or target_points <= 0:
            return xs, ys
        keep = np.linspace(0, xs.size - 1, num=max(128, int(target_points)), dtype=int)
        return np.ascontiguousarray(xs[keep], dtype=np.float32), np.ascontiguousarray(ys[keep], dtype=np.float32)

    def clear(self) -> None:
        self._analysis = None
        self._geometry = None
        self._last_playhead_bucket = None
        self.spectrogram_img.clear()
        self.chroma_img.clear()
        self.mfcc_img.clear()
        self.traces_plot.clear()
        self.traces_plot.addItem(self.traces_line)
        self.trace_curves.clear()

    def set_data(self, analysis: AnalysisResult, geometry: GeometryResult) -> None:
        self._analysis = analysis
        self._geometry = geometry
        self._last_playhead_bucket = None
        self._duration = float(analysis.times[-1]) if analysis.times.size > 0 else 0.0
        t0 = float(analysis.times[0]) if analysis.times.size > 0 else 0.0
        t1 = float(analysis.times[-1]) if analysis.times.size > 0 else 1.0

        spec = self._downsample_matrix_cols(analysis.spectrogram_db)
        self.spectrogram_img.setImage(spec, autoLevels=True)
        self.spectrogram_img.setRect(QRectF(t0, 0.0, max(1e-6, t1 - t0), float(analysis.sample_rate) / 2.0))
        self.spectrogram_plot.getPlotItem().setLimits(xMin=t0, xMax=t1)
        self.spectrogram_plot.getPlotItem().setYRange(0.0, float(analysis.sample_rate) / 2.0, padding=0.0)

        chroma = self._downsample_matrix_cols(analysis.chromagram)
        self.chroma_img.setImage(chroma, autoLevels=True)
        self.chroma_img.setRect(QRectF(t0, 0.0, max(1e-6, t1 - t0), 12.0))
        self.chroma_plot.getPlotItem().setLimits(xMin=t0, xMax=t1)
        self.chroma_plot.getPlotItem().setYRange(0.0, 12.0, padding=0.0)
        self.chroma_plot.getPlotItem().getAxis("left").setTicks([
            [(i + 0.5, label) for i, label in enumerate(["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"])]
        ])

        mfcc = self._downsample_matrix_cols(analysis.mfcc)
        self.mfcc_img.setImage(mfcc, autoLevels=True)
        self.mfcc_img.setRect(QRectF(t0, 1.0, max(1e-6, t1 - t0), float(mfcc.shape[0])))
        self.mfcc_plot.getPlotItem().setLimits(xMin=t0, xMax=t1)
        self.mfcc_plot.getPlotItem().setYRange(1.0, float(mfcc.shape[0]) + 1.0, padding=0.0)

        plot = self.traces_plot.getPlotItem()
        plot.clear()
        plot.addItem(self.traces_line)
        self.trace_curves.clear()
        active_mask = np.asarray(getattr(geometry, "active_mask_full", np.ones_like(analysis.times, dtype=bool)), dtype=bool).reshape(-1)
        if active_mask.size != analysis.times.size:
            active_mask = np.ones_like(analysis.times, dtype=bool)
        trace_specs = [
            (geometry.x_full, f"X: {geometry.labels['x']}", ACCENT),
            (geometry.y_full, f"Y: {geometry.labels['y']}", "#66d9ef"),
            (geometry.z_full, f"Z: {geometry.labels['z']}", "#c792ea"),
            (geometry.color_full, f"Color: {geometry.labels['color']}", "#ffcb6b"),
            (geometry.size_full, f"Size: {geometry.labels['size']}", "#f07178"),
        ]
        for values, label, color in trace_specs:
            series = np.asarray(values, dtype=np.float32).copy()
            series[~active_mask] = np.nan
            xs, ys = self._decimate_series(analysis.times, series)
            curve = plot.plot(xs, ys, pen=pg.mkPen(color, width=1.45), name=label)
            self.trace_curves.append(curve)
        plot.setLimits(xMin=t0, xMax=t1)
        plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        self.set_playhead(float(t0), force=True)

    def set_playhead(self, current_time: float, force: bool = False) -> None:
        x = float(current_time)
        if not force:
            bucket = int(round(x * 18.0))
            if self._last_playhead_bucket == bucket:
                return
            self._last_playhead_bucket = bucket
        else:
            self._last_playhead_bucket = int(round(x * 18.0))
        self.spectrogram_line.setValue(x)
        self.chroma_line.setValue(x)
        self.mfcc_line.setValue(x)
        self.traces_line.setValue(x)



class DiagnosticsDashboard(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("DiagnosticsStatusBadge")
        self.score_label = QLabel("Score 0.00")
        self.score_label.setObjectName("DiagnosticsScoreLabel")
        self.baseline_label = QLabel("No baseline")
        self.baseline_label.setObjectName("DiagnosticsBaselineLabel")
        self.feature_label = QLabel("Diagnostics")
        self.feature_label.setWordWrap(True)
        top.addWidget(self.status_label, 0)
        top.addWidget(self.score_label, 0)
        top.addWidget(self.baseline_label, 0)
        top.addStretch(1)
        outer.addLayout(top)

        self.plot = pg.PlotWidget()
        self.plot.setBackground(pg.mkColor(PANEL_BG))
        plot_item = self.plot.getPlotItem()
        plot_item.showGrid(x=True, y=True, alpha=0.12)
        plot_item.setLabel("left", "Anomaly score")
        plot_item.setLabel("bottom", "Live window", units="s")
        plot_item.getAxis("left").setTextPen(pg.mkColor(MUTED_TEXT))
        plot_item.getAxis("bottom").setTextPen(pg.mkColor(MUTED_TEXT))
        plot_item.getAxis("left").setPen(pg.mkColor("#48505f"))
        plot_item.getAxis("bottom").setPen(pg.mkColor("#48505f"))
        self.score_curve = plot_item.plot([], [], pen=pg.mkPen(ACCENT, width=1.8), name="Score")
        self.warn_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((255, 207, 125, 180), width=1.0, style=Qt.PenStyle.DashLine))
        self.alarm_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((255, 120, 120, 200), width=1.0, style=Qt.PenStyle.DashLine))
        self.now_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((248, 251, 255, 120), width=1.0))
        plot_item.addItem(self.warn_line)
        plot_item.addItem(self.alarm_line)
        plot_item.addItem(self.now_line)
        self.plot.setMinimumHeight(180)
        outer.addWidget(self.plot, 1)
        outer.addWidget(self.feature_label)

    def clear(self) -> None:
        self.score_curve.setData([], [])
        self.status_label.setText("Idle")
        self.status_label.setStyleSheet("color: %s; font-weight: 700;" % MUTED_TEXT)
        self.score_label.setText("Score 0.00")
        self.baseline_label.setText("No baseline")
        self.feature_label.setText("Diagnostics")

    def set_snapshot(self, snapshot: dict | None, warn_threshold: float, alarm_threshold: float) -> None:
        if not snapshot:
            self.clear()
            return
        times = np.asarray(snapshot.get("times", []), dtype=np.float64).reshape(-1)
        scores = np.asarray(snapshot.get("scores", []), dtype=np.float64).reshape(-1)
        self.warn_line.setValue(float(warn_threshold))
        self.alarm_line.setValue(float(alarm_threshold))
        self.now_line.setValue(0.0)
        if times.size == 0 or scores.size == 0:
            self.clear()
            return
        rel = times - float(times[-1])
        self.score_curve.setData(rel, scores)
        xmin = float(np.min(rel)) if rel.size else -10.0
        xmax = 0.0
        ymax = max(float(alarm_threshold) * 1.25, float(np.nanmax(scores)) * 1.15 + 0.05, 1.0)
        self.plot.getPlotItem().setXRange(xmin, xmax, padding=0.02)
        self.plot.getPlotItem().setYRange(0.0, ymax, padding=0.02)

        status = str(snapshot.get("current_status", "Idle") or "Idle")
        score = float(snapshot.get("current_score", 0.0))
        color = MUTED_TEXT
        if status == "Alarm":
            color = ERROR
        elif status == "Warning":
            color = WARNING
        elif status in {"Normal", "Listening"}:
            color = ACCENT
        elif status == "Uncalibrated":
            color = "#7fd4ff"
        self.status_label.setText(status)
        self.status_label.setStyleSheet("color: %s; font-weight: 700;" % color)
        self.score_label.setText(f"Score {score:0.2f}")
        self.baseline_label.setText("Baseline ready" if snapshot.get("baseline") else "No baseline")
        features = snapshot.get("features") or {}
        dominant = float(np.asarray(features.get("dominant_freq_hz", [0.0]), dtype=np.float64).reshape(-1)[-1]) if features else 0.0
        centroid = float(np.asarray(features.get("spectral_centroid_hz", [0.0]), dtype=np.float64).reshape(-1)[-1]) if features else 0.0
        rms_db = float(np.asarray(features.get("rms_db", [-120.0]), dtype=np.float64).reshape(-1)[-1]) if features else -120.0
        overflow = int(snapshot.get("overflow_count", 0))
        self.feature_label.setText(
            f"Dominant {dominant:0.0f} Hz • Centroid {centroid:0.0f} Hz • RMS {rms_db:0.1f} dBFS"
            + (f" • overflows {overflow}" if overflow else "")
        )


def _numpy_rgba_to_qimage(frame: np.ndarray) -> QImage:
    array = np.ascontiguousarray(frame, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] not in (3, 4):
        raise ValueError("Preview frame must be HxWx3 or HxWx4.")
    if array.shape[2] == 3:
        alpha = np.full(array.shape[:2] + (1,), 255, dtype=np.uint8)
        array = np.concatenate([array, alpha], axis=2)
    h, w, _ = array.shape
    image = QImage(array.data, w, h, array.strides[0], QImage.Format.Format_RGBA8888)
    return image.copy()


class ExportComposerCanvas(QWidget):
    layoutEdited = Signal()
    selectedItemChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._layout_spec = default_export_layout()
        self._preview_image: QImage | None = None
        self._output_ratio = 16.0 / 9.0
        self.selected_name = "geometry"
        self._drag_mode: str | None = None
        self._drag_origin = None
        self._drag_start_layout: ExportLayoutSpec | None = None
        self.snap_enabled = True
        self.snap_step = 0.01
        self.show_grid = True
        self.setMouseTracking(True)
        self.setMinimumSize(680, 420)

    def set_layout_spec(self, layout_spec: ExportLayoutSpec) -> None:
        self._layout_spec = layout_spec.clone().clamp()
        self._ensure_selected_visible()
        self.update()

    def layout_spec(self) -> ExportLayoutSpec:
        return self._layout_spec.clone().clamp()

    def set_output_aspect(self, width: int, height: int) -> None:
        width = max(1, int(width))
        height = max(1, int(height))
        self._output_ratio = float(width) / float(height)
        self.update()

    def set_preview_frame(self, frame: np.ndarray | None) -> None:
        self._preview_image = None if frame is None else _numpy_rgba_to_qimage(frame)
        self.update()

    def set_snap(self, enabled: bool, step_percent: float) -> None:
        self.snap_enabled = bool(enabled)
        self.snap_step = max(0.001, float(step_percent) / 100.0)
        self.update()

    def _ensure_selected_visible(self) -> None:
        if self.selected_name in LAYOUT_ITEM_ORDER and self._layout_spec.item(self.selected_name).enabled:
            return
        for name in LAYOUT_ITEM_ORDER:
            if self._layout_spec.item(name).enabled:
                self.selected_name = name
                return
        self.selected_name = "geometry"

    def select_item(self, name: str) -> None:
        if name not in LAYOUT_ITEM_ORDER:
            return
        self.selected_name = name
        self._ensure_selected_visible()
        self.selectedItemChanged.emit(self.selected_name)
        self.update()

    def _display_rect(self) -> QRectF:
        outer = QRectF(self.rect()).adjusted(12.0, 12.0, -12.0, -12.0)
        if outer.width() <= 4.0 or outer.height() <= 4.0:
            return outer
        ratio = max(1e-6, float(self._output_ratio))
        if outer.width() / outer.height() > ratio:
            height = outer.height()
            width = height * ratio
            x = outer.left() + 0.5 * (outer.width() - width)
            return QRectF(x, outer.top(), width, height)
        width = outer.width()
        height = width / ratio
        y = outer.top() + 0.5 * (outer.height() - height)
        return QRectF(outer.left(), y, width, height)

    def _item_rect(self, name: str) -> QRectF:
        display = self._display_rect()
        spec = self._layout_spec.item(name)
        return QRectF(
            display.left() + spec.x * display.width(),
            display.top() + spec.y * display.height(),
            spec.w * display.width(),
            spec.h * display.height(),
        )

    def _handle_rects(self, rect: QRectF) -> dict[str, QRectF]:
        size = 12.0
        half = size / 2.0
        points = {
            "nw": rect.topLeft(),
            "n": (rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": (rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": (rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": (rect.left(), rect.center().y()),
        }
        out: dict[str, QRectF] = {}
        for key, point in points.items():
            if hasattr(point, "x"):
                x = point.x()
                y = point.y()
            else:
                x, y = point
            out[key] = QRectF(x - half, y - half, size, size)
        return out

    def _cursor_for_mode(self, mode: str | None):
        mapping = {
            "move": Qt.CursorShape.SizeAllCursor,
            "n": Qt.CursorShape.SizeVerCursor,
            "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor,
            "w": Qt.CursorShape.SizeHorCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor,
            "sw": Qt.CursorShape.SizeBDiagCursor,
            "nw": Qt.CursorShape.SizeFDiagCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
        }
        self.setCursor(mapping.get(mode, Qt.CursorShape.ArrowCursor))

    def _hit_test(self, pos) -> tuple[str | None, str | None]:
        if self.selected_name in LAYOUT_ITEM_ORDER and self._layout_spec.item(self.selected_name).enabled:
            rect = self._item_rect(self.selected_name)
            for handle_name, handle_rect in self._handle_rects(rect).items():
                if handle_rect.contains(pos):
                    return self.selected_name, handle_name
        for name in reversed(LAYOUT_ITEM_ORDER):
            spec = self._layout_spec.item(name)
            if not spec.enabled:
                continue
            rect = self._item_rect(name)
            if rect.contains(pos):
                return name, "move"
        return None, None

    def _snap_value(self, value: float) -> float:
        if not self.snap_enabled:
            return float(value)
        step = max(0.001, float(self.snap_step))
        return round(float(value) / step) * step

    def _snap_rect(self, spec) -> None:
        spec.x = self._snap_value(spec.x)
        spec.y = self._snap_value(spec.y)
        spec.w = max(0.05, self._snap_value(spec.w))
        spec.h = max(0.05, self._snap_value(spec.h))
        self._layout_spec.clamp()

    def align_selected(self, mode: str) -> None:
        if self.selected_name not in LAYOUT_ITEM_ORDER:
            return
        spec = self._layout_spec.item(self.selected_name)
        if mode == "left":
            spec.x = 0.0
        elif mode == "hcenter":
            spec.x = max(0.0, (1.0 - spec.w) * 0.5)
        elif mode == "right":
            spec.x = max(0.0, 1.0 - spec.w)
        elif mode == "top":
            spec.y = 0.0
        elif mode == "vcenter":
            spec.y = max(0.0, (1.0 - spec.h) * 0.5)
        elif mode == "bottom":
            spec.y = max(0.0, 1.0 - spec.h)
        elif mode == "center":
            spec.x = max(0.0, (1.0 - spec.w) * 0.5)
            spec.y = max(0.0, (1.0 - spec.h) * 0.5)
        elif mode == "full_width":
            spec.x = 0.0
            spec.w = 1.0
        elif mode == "full_height":
            spec.y = 0.0
            spec.h = 1.0
        else:
            return
        self._snap_rect(spec)
        self.layoutEdited.emit()
        self.update()

    def reset_selected_to_default(self) -> None:
        if self.selected_name not in LAYOUT_ITEM_ORDER:
            return
        default_spec = default_export_layout().item(self.selected_name).copy()
        target = self._layout_spec.item(self.selected_name)
        target.enabled = default_spec.enabled
        target.x = default_spec.x
        target.y = default_spec.y
        target.w = default_spec.w
        target.h = default_spec.h
        target.content_scale = default_spec.content_scale
        target.fit_mode = default_spec.fit_mode
        target.background_alpha = default_spec.background_alpha
        target.show_title = default_spec.show_title
        self._layout_spec.clamp()
        self.layoutEdited.emit()
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(APP_BG))

        display = self._display_rect()
        painter.setPen(QPen(QColor("#314157"), 1.5))
        painter.setBrush(QBrush(QColor(SURFACE_BG)))
        painter.drawRoundedRect(display, 14, 14)

        if self._preview_image is not None:
            painter.drawImage(display, self._preview_image)
        else:
            painter.setPen(QColor(MUTED_TEXT))
            painter.drawText(display, Qt.AlignmentFlag.AlignCenter, "Preview will appear here")

        if self.show_grid and self.snap_enabled:
            step = max(0.02, float(self.snap_step))
            grid_pen = QPen(QColor(88, 111, 142, 62), 1.0)
            painter.setPen(grid_pen)
            columns = min(50, max(1, int(round(1.0 / step)) - 1))
            rows = min(50, max(1, int(round(1.0 / step)) - 1))
            for idx in range(1, columns + 1):
                x = display.left() + min(1.0, idx * step) * display.width()
                if x < display.right():
                    painter.drawLine(int(round(x)), int(round(display.top())), int(round(x)), int(round(display.bottom())))
            for idx in range(1, rows + 1):
                y = display.top() + min(1.0, idx * step) * display.height()
                if y < display.bottom():
                    painter.drawLine(int(round(display.left())), int(round(y)), int(round(display.right())), int(round(y)))

        for name in LAYOUT_ITEM_ORDER:
            spec = self._layout_spec.item(name)
            if not spec.enabled:
                continue
            rect = self._item_rect(name)
            pen = QPen(QColor(ACCENT), 2.2) if name == self.selected_name else QPen(QColor("#b4c3da"), 1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 10, 10)

            label_fill = QColor(APP_BG)
            label_fill.setAlpha(168)
            painter.setBrush(QBrush(label_fill))
            painter.setPen(Qt.PenStyle.NoPen)
            label_rect = QRectF(rect.left() + 8, rect.top() + 8, min(rect.width() - 16, 148), 22)
            painter.drawRoundedRect(label_rect, 10, 10)
            painter.setPen(QColor(TEXT))
            painter.setFont(QFont("Helvetica", 9, QFont.Weight.DemiBold))
            painter.drawText(label_rect.adjusted(10, 2, -8, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, LAYOUT_ITEM_TITLES[name])

        if self.selected_name in LAYOUT_ITEM_ORDER and self._layout_spec.item(self.selected_name).enabled:
            rect = self._item_rect(self.selected_name)
            painter.setBrush(QBrush(QColor(ACCENT)))
            painter.setPen(QPen(QColor("#dbe7ff"), 1.0))
            for handle_rect in self._handle_rects(rect).values():
                painter.drawRect(handle_rect)

        painter.end()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        name, mode = self._hit_test(event.position())
        if name is None or mode is None:
            super().mousePressEvent(event)
            return
        self.select_item(name)
        self._drag_mode = mode
        self._drag_origin = event.position()
        self._drag_start_layout = self._layout_spec.clone()
        self._cursor_for_mode(mode)
        event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_mode is None or self._drag_start_layout is None or self._drag_origin is None:
            _, mode = self._hit_test(event.position())
            self._cursor_for_mode(mode)
            super().mouseMoveEvent(event)
            return
        self._apply_drag(event.position())
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_mode = None
        self._drag_origin = None
        self._drag_start_layout = None
        self._cursor_for_mode(None)
        super().mouseReleaseEvent(event)

    def _apply_drag(self, pos) -> None:
        if self._drag_start_layout is None or self._drag_origin is None or self.selected_name not in LAYOUT_ITEM_ORDER:
            return
        display = self._display_rect()
        if display.width() <= 1e-6 or display.height() <= 1e-6:
            return
        dx = float(pos.x() - self._drag_origin.x()) / float(display.width())
        dy = float(pos.y() - self._drag_origin.y()) / float(display.height())
        start_spec = self._drag_start_layout.item(self.selected_name)
        spec = self._layout_spec.item(self.selected_name)
        spec.enabled = start_spec.enabled
        spec.content_scale = start_spec.content_scale
        spec.fit_mode = getattr(start_spec, "fit_mode", "contain")
        spec.background_alpha = start_spec.background_alpha
        spec.show_title = start_spec.show_title

        if self._drag_mode == "move":
            spec.x = start_spec.x + dx
            spec.y = start_spec.y + dy
            spec.w = start_spec.w
            spec.h = start_spec.h
        else:
            left = start_spec.x
            right = start_spec.x + start_spec.w
            top = start_spec.y
            bottom = start_spec.y + start_spec.h
            if "w" in self._drag_mode:
                left = start_spec.x + dx
            if "e" in self._drag_mode:
                right = start_spec.x + start_spec.w + dx
            if "n" in self._drag_mode:
                top = start_spec.y + dy
            if "s" in self._drag_mode:
                bottom = start_spec.y + start_spec.h + dy
            spec.x = min(left, right)
            spec.y = min(top, bottom)
            spec.w = abs(right - left)
            spec.h = abs(bottom - top)
        self._layout_spec.clamp()
        if self.snap_enabled:
            self._snap_rect(spec)
        self.layoutEdited.emit()
        self.update()


class ExportLayoutDialog(QDialog):
    def __init__(
        self,
        layout_spec: ExportLayoutSpec,
        width: int,
        height: int,
        fps: int,
        parent: QWidget | None = None,
        preview_provider: Callable[[ExportLayoutSpec, int, int, float], np.ndarray | None] | None = None,
        time_provider: Callable[[], float] | None = None,
        duration_seconds: float = 0.0,
        preview_scale: float = 1.0,
    ):
        super().__init__(parent)
        self.setWindowTitle("Compose export layout")
        self.resize(1320, 900)
        self.layout_spec = layout_spec.clone().clamp()
        self.preview_provider = preview_provider
        self.time_provider = time_provider
        self.duration_seconds = max(0.0, float(duration_seconds))
        self.preview_scale = max(0.35, min(1.0, float(preview_scale)))
        self._syncing = False
        self.item_controls: dict[str, dict[str, QWidget]] = {}

        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setSingleShot(True)
        self._preview_refresh_timer.timeout.connect(self._refresh_preview)

        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(360)
        self._follow_timer.timeout.connect(self._refresh_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        intro = QLabel(
            "Live export composer: drag blocks to move them, drag edges or corners to resize them, and use the layout tools for snapping, centering, or filling space before rendering."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        top = QHBoxLayout()
        top.setSpacing(12)
        root.addLayout(top, 1)

        preview_col = QVBoxLayout()
        preview_col.setSpacing(8)
        top.addLayout(preview_col, 7)
        preview_col.addWidget(QLabel("Live export canvas"))

        self.preview_canvas = ExportComposerCanvas()
        self.preview_canvas.set_layout_spec(self.layout_spec)
        self.preview_canvas.set_output_aspect(int(width), int(height))
        self.preview_canvas.layoutEdited.connect(self._canvas_layout_changed)
        self.preview_canvas.selectedItemChanged.connect(self._selected_block_changed)
        preview_col.addWidget(self.preview_canvas, 1)

        helper = QLabel("Tip: set block background alpha near zero when you want geometry to remain visible behind spectrogram or trace overlays.")
        helper.setWordWrap(True)
        preview_col.addWidget(helper)

        preset_row = QHBoxLayout()
        for title, factory in (
            ("Balanced", balanced_export_layout),
            ("Geometry focus", geometry_focus_export_layout),
            ("Analysis focus", analysis_focus_export_layout),
            ("Overlay", overlay_export_layout),
        ):
            btn = QPushButton(title)
            btn.clicked.connect(lambda _checked=False, f=factory: self._apply_preset(f()))
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        preview_col.addLayout(preset_row)

        transport_row = QHBoxLayout()
        self.follow_playhead_checkbox = QCheckBox("Follow live playhead")
        self.follow_playhead_checkbox.setChecked(True)
        self.follow_playhead_checkbox.toggled.connect(self._follow_playhead_toggled)
        self.preview_time_slider = QSlider(Qt.Orientation.Horizontal)
        self.preview_time_slider.setRange(0, max(0, int(round(self.duration_seconds * 1000.0))))
        self.preview_time_slider.valueChanged.connect(self._manual_time_changed)
        self.preview_time_label = QLabel("0.00 s")
        refresh_btn = QPushButton("Refresh preview")
        refresh_btn.clicked.connect(self._queue_preview_refresh)
        transport_row.addWidget(self.follow_playhead_checkbox)
        transport_row.addWidget(self.preview_time_slider, 1)
        transport_row.addWidget(self.preview_time_label)
        transport_row.addWidget(refresh_btn)
        preview_col.addLayout(transport_row)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        controls_host = QWidget()
        controls_layout = QVBoxLayout(controls_host)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)
        controls_scroll.setWidget(controls_host)
        top.addWidget(controls_scroll, 5)

        tools_group = QGroupBox("Layout tools")
        tools_layout = QVBoxLayout(tools_group)
        tools_layout.setContentsMargins(8, 8, 8, 8)
        tools_layout.setSpacing(10)
        self.selected_block_label = QLabel("Selected block: Geometry")
        snap_row = QHBoxLayout()
        self.snap_checkbox = QCheckBox("Snap to grid")
        self.snap_checkbox.setChecked(True)
        self.snap_checkbox.toggled.connect(self._snap_controls_changed)
        self.snap_step_spin = QDoubleSpinBox()
        self.snap_step_spin.setRange(0.5, 10.0)
        self.snap_step_spin.setDecimals(1)
        self.snap_step_spin.setSingleStep(0.5)
        self.snap_step_spin.setValue(1.0)
        self.snap_step_spin.valueChanged.connect(self._snap_controls_changed)
        snap_row.addWidget(self.snap_checkbox)
        snap_row.addWidget(QLabel("Step %"))
        snap_row.addWidget(self.snap_step_spin)
        snap_row.addStretch(1)
        tools_layout.addWidget(self.selected_block_label)
        tools_layout.addLayout(snap_row)

        align_grid = QGridLayout()
        align_buttons = [
            ("Align left", "left", 0, 0),
            ("H-center", "hcenter", 0, 1),
            ("Align right", "right", 0, 2),
            ("Align top", "top", 1, 0),
            ("V-center", "vcenter", 1, 1),
            ("Align bottom", "bottom", 1, 2),
            ("Center block", "center", 2, 0),
            ("Fill width", "full_width", 2, 1),
            ("Fill height", "full_height", 2, 2),
        ]
        for title, mode, row, col in align_buttons:
            btn = QPushButton(title)
            btn.clicked.connect(lambda _checked=False, m=mode: self._align_selected(m))
            align_grid.addWidget(btn, row, col)
        tools_layout.addLayout(align_grid)
        reset_selected_btn = QPushButton("Reset selected block to preset default")
        reset_selected_btn.clicked.connect(self._reset_selected_block)
        tools_layout.addWidget(reset_selected_btn)
        controls_layout.addWidget(tools_group)

        output_group = QGroupBox("Output")
        output_form = QFormLayout(output_group)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(640, 7680)
        self.width_spin.setSingleStep(160)
        self.width_spin.setValue(int(width))
        self.width_spin.valueChanged.connect(self._output_controls_changed)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(360, 4320)
        self.height_spin.setSingleStep(90)
        self.height_spin.setValue(int(height))
        self.height_spin.valueChanged.connect(self._output_controls_changed)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(12, 120)
        self.fps_spin.setValue(int(fps))
        output_form.addRow("Width", self.width_spin)
        output_form.addRow("Height", self.height_spin)
        output_form.addRow("FPS", self.fps_spin)
        controls_layout.addWidget(output_group)

        for name in LAYOUT_ITEM_ORDER:
            spec = self.layout_spec.item(name)
            group = QGroupBox(LAYOUT_ITEM_TITLES[name])
            grid = QGridLayout(group)
            enabled = QCheckBox("Include in export")
            enabled.setChecked(bool(spec.enabled))
            x_spin = QDoubleSpinBox()
            y_spin = QDoubleSpinBox()
            w_spin = QDoubleSpinBox()
            h_spin = QDoubleSpinBox()
            content_scale_spin = QDoubleSpinBox()
            background_alpha_spin = QDoubleSpinBox()
            fit_mode_combo = QComboBox()
            fit_mode_combo.addItems(list(FIT_MODES))
            show_title = QCheckBox("Show block title")
            show_title.setChecked(bool(spec.show_title))
            for spin in (x_spin, y_spin, w_spin, h_spin):
                spin.setRange(0.0, 100.0)
                spin.setDecimals(1)
                spin.setSingleStep(1.0)
            content_scale_spin.setRange(0.35, 5.0)
            content_scale_spin.setDecimals(2)
            content_scale_spin.setSingleStep(0.05)
            background_alpha_spin.setRange(0.0, 1.0)
            background_alpha_spin.setDecimals(2)
            background_alpha_spin.setSingleStep(0.02)

            x_spin.setValue(spec.x * 100.0)
            y_spin.setValue(spec.y * 100.0)
            w_spin.setValue(spec.w * 100.0)
            h_spin.setValue(spec.h * 100.0)
            content_scale_spin.setValue(spec.content_scale)
            background_alpha_spin.setValue(spec.background_alpha)
            if fit_mode_combo.findText(getattr(spec, "fit_mode", "contain")) >= 0:
                fit_mode_combo.setCurrentText(getattr(spec, "fit_mode", "contain"))

            grid.addWidget(enabled, 0, 0, 1, 2)
            grid.addWidget(QLabel("X %"), 1, 0)
            grid.addWidget(x_spin, 1, 1)
            grid.addWidget(QLabel("Y %"), 2, 0)
            grid.addWidget(y_spin, 2, 1)
            grid.addWidget(QLabel("W %"), 3, 0)
            grid.addWidget(w_spin, 3, 1)
            grid.addWidget(QLabel("H %"), 4, 0)
            grid.addWidget(h_spin, 4, 1)
            grid.addWidget(QLabel("Content zoom"), 5, 0)
            grid.addWidget(content_scale_spin, 5, 1)
            grid.addWidget(QLabel("Fit mode"), 6, 0)
            grid.addWidget(fit_mode_combo, 6, 1)
            grid.addWidget(QLabel("Block bg alpha"), 7, 0)
            grid.addWidget(background_alpha_spin, 7, 1)
            grid.addWidget(show_title, 8, 0, 1, 2)

            self.item_controls[name] = {
                "enabled": enabled,
                "x": x_spin,
                "y": y_spin,
                "w": w_spin,
                "h": h_spin,
                "content_scale": content_scale_spin,
                "fit_mode": fit_mode_combo,
                "background_alpha": background_alpha_spin,
                "show_title": show_title,
            }

            enabled.toggled.connect(self._controls_changed)
            x_spin.valueChanged.connect(self._controls_changed)
            y_spin.valueChanged.connect(self._controls_changed)
            w_spin.valueChanged.connect(self._controls_changed)
            h_spin.valueChanged.connect(self._controls_changed)
            content_scale_spin.valueChanged.connect(self._controls_changed)
            fit_mode_combo.currentTextChanged.connect(self._controls_changed)
            background_alpha_spin.valueChanged.connect(self._controls_changed)
            show_title.toggled.connect(self._controls_changed)

            controls_layout.addWidget(group)

        controls_layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._sync_controls_from_layout()
        self._snap_controls_changed()
        self._selected_block_changed(self.preview_canvas.selected_name)
        self._follow_playhead_toggled(self.follow_playhead_checkbox.isChecked())
        self._update_time_label()
        self._queue_preview_refresh()

    def _current_preview_time(self) -> float:
        if self.follow_playhead_checkbox.isChecked() and self.time_provider is not None:
            try:
                return max(0.0, float(self.time_provider()))
            except Exception:
                pass
        return max(0.0, float(self.preview_time_slider.value()) / 1000.0)

    def _update_time_label(self) -> None:
        self.preview_time_label.setText(f"{self._current_preview_time():0.02f} s")

    def _queue_preview_refresh(self) -> None:
        self._preview_refresh_timer.start(70)

    def _refresh_preview(self) -> None:
        self._update_time_label()
        if self.preview_provider is None:
            self.preview_canvas.set_preview_frame(None)
            return
        width = max(1, int(self.width_spin.value()))
        height = max(1, int(self.height_spin.value()))
        ratio = float(width) / float(height)
        avail_w = max(420, self.preview_canvas.width() - 24)
        avail_h = max(240, self.preview_canvas.height() - 24)
        if avail_w / avail_h > ratio:
            preview_h = avail_h
            preview_w = max(320, int(round(preview_h * ratio)))
        else:
            preview_w = avail_w
            preview_h = max(180, int(round(preview_w / max(ratio, 1e-6))))
        preview_w = max(240, int(round(preview_w * self.preview_scale)))
        preview_h = max(135, int(round(preview_h * self.preview_scale)))
        frame = self.preview_provider(self.layout_spec.clone().clamp(), int(preview_w), int(preview_h), self._current_preview_time())
        self.preview_canvas.set_preview_frame(frame)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._queue_preview_refresh()

    def _output_controls_changed(self) -> None:
        self.preview_canvas.set_output_aspect(int(self.width_spin.value()), int(self.height_spin.value()))
        self._queue_preview_refresh()

    def _follow_playhead_toggled(self, checked: bool) -> None:
        self.preview_time_slider.setEnabled(not checked)
        if checked and self.time_provider is not None:
            self._follow_timer.start()
        else:
            self._follow_timer.stop()
        self._queue_preview_refresh()

    def _manual_time_changed(self, _value: int) -> None:
        if not self.follow_playhead_checkbox.isChecked():
            self._update_time_label()
            self._queue_preview_refresh()

    def _apply_preset(self, layout_spec: ExportLayoutSpec) -> None:
        self.layout_spec = layout_spec.clone().clamp()
        self.preview_canvas.set_layout_spec(self.layout_spec)
        self._sync_controls_from_layout()
        self._queue_preview_refresh()

    def _sync_controls_from_layout(self) -> None:
        self._syncing = True
        try:
            for name in LAYOUT_ITEM_ORDER:
                spec = self.layout_spec.item(name)
                controls = self.item_controls[name]
                controls["enabled"].setChecked(bool(spec.enabled))
                controls["x"].setValue(spec.x * 100.0)
                controls["y"].setValue(spec.y * 100.0)
                controls["w"].setValue(spec.w * 100.0)
                controls["h"].setValue(spec.h * 100.0)
                controls["content_scale"].setValue(spec.content_scale)
                if controls["fit_mode"].findText(getattr(spec, "fit_mode", "contain")) >= 0:
                    controls["fit_mode"].setCurrentText(getattr(spec, "fit_mode", "contain"))
                controls["background_alpha"].setValue(spec.background_alpha)
                controls["show_title"].setChecked(bool(spec.show_title))
                controls["show_title"].setEnabled(bool(spec.enabled))
        finally:
            self._syncing = False

    def _controls_changed(self) -> None:
        if self._syncing:
            return
        for name in LAYOUT_ITEM_ORDER:
            controls = self.item_controls[name]
            spec = self.layout_spec.item(name)
            spec.enabled = bool(controls["enabled"].isChecked())
            spec.x = float(controls["x"].value()) / 100.0
            spec.y = float(controls["y"].value()) / 100.0
            spec.w = float(controls["w"].value()) / 100.0
            spec.h = float(controls["h"].value()) / 100.0
            spec.content_scale = float(controls["content_scale"].value())
            spec.fit_mode = str(controls["fit_mode"].currentText())
            spec.background_alpha = float(controls["background_alpha"].value())
            spec.show_title = bool(controls["show_title"].isChecked()) if spec.enabled else False
        self.layout_spec.clamp()
        self.preview_canvas.set_layout_spec(self.layout_spec)
        self.preview_canvas._ensure_selected_visible()
        self._sync_controls_from_layout()
        self._queue_preview_refresh()

    def _canvas_layout_changed(self) -> None:
        self.layout_spec = self.preview_canvas.layout_spec()
        self._sync_controls_from_layout()
        self._queue_preview_refresh()

    def _snap_controls_changed(self) -> None:
        self.preview_canvas.set_snap(bool(self.snap_checkbox.isChecked()), float(self.snap_step_spin.value()))
        self._queue_preview_refresh()

    def _selected_block_changed(self, name: str) -> None:
        self.selected_block_label.setText(f"Selected block: {LAYOUT_ITEM_TITLES.get(name, name.title())}")

    def _align_selected(self, mode: str) -> None:
        self.preview_canvas.align_selected(mode)
        self._canvas_layout_changed()

    def _reset_selected_block(self) -> None:
        self.preview_canvas.reset_selected_to_default()
        self._canvas_layout_changed()

    def result_payload(self) -> tuple[ExportLayoutSpec, int, int, int]:
        return self.layout_spec.clone().clamp(), int(self.width_spin.value()), int(self.height_spin.value()), int(self.fps_spin.value())


class MainWindow(QMainWindow):
    def __init__(self, initial_file: str | None = None):
        super().__init__()
        self.setWindowTitle(APP_WINDOW_TITLE)
        self.resize(1920, 1180)
        self.setMinimumSize(1500, 920)

        self.analysis: AnalysisResult | None = None
        self.geometry_data: GeometryResult | None = None
        self.file_path: str | None = None
        self.current_time: float = 0.0
        self.current_duration: float = 0.0
        self.project_path: str | None = None
        self.preset_path: str | None = None
        self._analysis_job_id = 0
        self._analysis_running = False
        self._geometry_job_id = 0
        self._geometry_running = False
        self._export_running = False
        self._batch_running = False
        self._closing = False
        self._dragging_timeline = False
        self._resume_after_seek = False
        self._syncing_slider = False
        self._applying_state = False
        self._pending_scrub_time = 0.0
        self._project_dirty = False
        self._refresh_in_progress = False
        self._refresh_pending = False
        self._base_azimuth = 35.0
        self._rgba_full: np.ndarray | None = None
        self._last_head_idx: int = 0
        self.export_layout_spec = default_export_layout()
        self.profile_store = ProfileStore()
        self._freeze_watchdog = SilentFreezeWatchdog(self.profile_store)
        self._recent_files: list[str] = self.profile_store.load_recent_files()
        self._recent_projects: list[str] = self.profile_store.load_recent_projects()
        self._recent_presets: list[str] = self.profile_store.load_recent_presets()
        self._visualizer_behavior_presets: dict[str, dict] = {}
        self._visualizer_behavior_preset_paths: dict[str, str] = {}
        self._active_profile_name: str | None = None
        self._active_export_preset_key: str = EXPORT_PRESETS[0].key if EXPORT_PRESETS else ""
        self.camera_keyframes: list[CameraKeyframe] = []
        self.camera_keyframe_easing: str = "ease_in_out"
        self.bookmarks: list[Bookmark] = []
        self.regions: list[TimeRegion] = []
        self.export_queue_jobs: list[ExportQueueJob] = []
        self._export_job_counter: int = 0
        self._queue_running: bool = False
        self._region_mark_start: float | None = None
        self._preview_session = None
        self._preview_session_signature = None
        self._active_export_dialog = None
        self._preview_async_lock = threading.Lock()
        self._preview_async_pending_job: dict | None = None
        self._preview_async_pending_key: tuple | None = None
        self._preview_async_worker_running = False
        self._preview_async_last_frame: np.ndarray | None = None
        self._preview_async_last_key: tuple | None = None
        self._preview_async_generation = 0
        self._last_visual_refresh_perf = 0.0
        self._last_analysis_playhead_perf = 0.0
        self._last_slow_render_log = 0.0
        self._applying_performance_preset = False
        self._applying_visual_behavior_preset = False
        self._perf_window_started = time.perf_counter()
        self._perf_stats = {
            "visual_requests": 0,
            "visual_executed": 0,
            "visual_deferred": 0,
            "visual_timer_collisions": 0,
            "preview_submitted": 0,
            "preview_dropped": 0,
            "panel_updates": 0,
        }

        self.live_monitor = LiveDiagnosticsEngine(sample_rate=22050, frame_size=2048, hop_length=1024, history_seconds=30.0)
        self._live_snapshot: dict | None = None
        self._live_analysis: AnalysisResult | None = None
        self._live_geometry_data: GeometryResult | None = None
        self._live_rgba_full: np.ndarray | None = None
        self._live_current_time: float = 0.0
        self._live_saved_baseline_model: BaselineModel | None = None
        self._live_recent_event_count: int = 0

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(800)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._save_last_session_safely)

        self._scrub_preview_timer = QTimer(self)
        self._scrub_preview_timer.setSingleShot(True)
        self._scrub_preview_timer.setInterval(28)
        self._scrub_preview_timer.timeout.connect(self._apply_scrub_preview)

        self._audition_stop_timer = QTimer(self)
        self._audition_stop_timer.setSingleShot(True)
        self._audition_stop_timer.timeout.connect(self._stop_click_audition)

        self._ui_heartbeat_timer = QTimer(self)
        self._ui_heartbeat_timer.setInterval(250)
        self._ui_heartbeat_timer.timeout.connect(self._beat_ui_heartbeat)

        self._visual_refresh_timer = QTimer(self)
        self._visual_refresh_timer.setSingleShot(True)
        self._visual_refresh_timer.timeout.connect(self._perform_visual_refresh)

        self._diagnostics_timer = QTimer(self)
        self._diagnostics_timer.setInterval(120)
        self._diagnostics_timer.timeout.connect(self._poll_live_diagnostics)

        self.worker_bridge = WorkerBridge(self)
        self.worker_bridge.analysisFinished.connect(self._on_analysis_finished)
        self.worker_bridge.analysisError.connect(self._on_analysis_error)
        self.worker_bridge.geometryFinished.connect(self._on_geometry_finished)
        self.worker_bridge.geometryError.connect(self._on_geometry_error)
        self.worker_bridge.exportFinished.connect(self._on_export_finished)
        self.worker_bridge.exportError.connect(self._on_export_error)
        self.worker_bridge.exportProgress.connect(self._on_export_progress)
        self.worker_bridge.batchFinished.connect(self._on_batch_finished)
        self.worker_bridge.batchError.connect(self._on_batch_error)
        self.worker_bridge.previewFrameReady.connect(self._on_preview_frame_ready)
        self.worker_bridge.previewFrameError.connect(self._on_preview_frame_error)

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.media_player.errorOccurred.connect(self._on_media_error)

        self.frame_timer = QTimer(self)
        self.frame_timer.setInterval(33)
        self.frame_timer.timeout.connect(self._on_frame_tick)

        self._build_ui()
        self._apply_window_branding()
        self._apply_theme_mode(DEFAULT_THEME_KEY, announce=False)
        self._update_brand_identity_layout()
        self._refresh_live_device_list()
        self.diagnostics_dashboard.clear()
        self._install_shortcuts()
        self._apply_performance_preset(self.performance_mode_combo.currentText(), set_status=False)
        self._apply_frame_timer_settings()
        self._load_preset(DEFAULT_STARTUP_PRESET if DEFAULT_STARTUP_PRESET in DEFAULT_PRESETS else next(iter(DEFAULT_PRESETS)))
        if DEFAULT_VISUALIZER_BEHAVIOR_PRESET:
            self._apply_visualizer_behavior_preset(DEFAULT_VISUALIZER_BEHAVIOR_PRESET, set_status=False)
        else:
            self._sync_visualizer_behavior_preset_combo()
        self._reset_layout()
        self._apply_export_preset(self._active_export_preset_key, set_status=False)
        self._update_transport_text()
        self._refresh_profile_list()
        self._refresh_recent_files_menu()
        self._refresh_recent_projects_menu()
        self._refresh_recent_presets_menu()
        self._refresh_project_label()
        self._refresh_keyframe_list()
        self._refresh_bookmark_list()
        self._refresh_region_list()
        self._refresh_export_queue_list()
        self._ui_heartbeat_timer.start()
        self._freeze_watchdog.start()
        self._set_status("Open a video or audio file to begin.")

        restored = False
        if initial_file:
            self.load_file(initial_file)
        else:
            restored = self._maybe_offer_recovery_restore()
            if not restored:
                restored = self._restore_last_session_if_enabled()
        if not restored and not initial_file:
            self._schedule_autosave()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_brand_identity_layout()


    def _update_brand_identity_layout(self) -> None:
        brand_widget = getattr(self, "brand_identity_widget", None)
        logo_label = getattr(self, "brand_logo_label", None)
        if brand_widget is None or logo_label is None:
            return
        try:
            panel_width = max(0, self.left_panel_root.width())
        except Exception:
            panel_width = 0
        if panel_width <= 0:
            try:
                panel_width = max(0, self.body_splitter.sizes()[0])
            except Exception:
                panel_width = 0
        if panel_width <= 0:
            panel_width = max(260, int(self.width() * 0.24))
        inner_width = max(180, panel_width - 36)
        if hasattr(logo_label, "preferredWidth"):
            try:
                preferred_width = int(logo_label.preferredWidth())
            except Exception:
                preferred_width = inner_width
        else:
            preferred_width = inner_width
        logo_width = max(180, min(preferred_width, inner_width))
        try:
            logo_label.setFixedWidth(logo_width)
            if hasattr(logo_label, "_update_scaled_pixmap"):
                logo_label._update_scaled_pixmap()
            logo_label.updateGeometry()
            brand_widget.updateGeometry()
            brand_widget.update()
        except Exception:
            pass

    # ---------- UI ----------


    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.body_splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.body_splitter, 1)

        self.body_splitter.addWidget(self._build_left_panel())
        self.body_splitter.addWidget(self._build_right_panel())
        self.body_splitter.setStretchFactor(0, 0)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.setSizes([420, 1480])

        root.addWidget(self._build_transport_bar())

        status = QStatusBar()
        status.setStyleSheet("QStatusBar { background: %s; color: %s; }" % (PANEL_BG, MUTED_TEXT))
        self.setStatusBar(status)

        self.file_menu = self.menuBar().addMenu("&File")
        new_project_action = QAction("New project", self)
        new_project_action.triggered.connect(self.new_project)
        open_action = QAction("Open media…", self)
        open_action.triggered.connect(self.open_file_dialog)
        open_project_action = QAction(f"Open project ({PROJECT_EXTENSION})…", self)
        open_project_action.triggered.connect(self.open_project_dialog)
        open_preset_action = QAction(f"Open preset ({PRESET_EXTENSION})…", self)
        open_preset_action.triggered.connect(self.open_preset_dialog)
        save_project_action = QAction("Save project", self)
        save_project_action.triggered.connect(self.save_project)
        save_project_as_action = QAction("Save project as…", self)
        save_project_as_action.triggered.connect(self.save_project_as)
        save_preset_action = QAction("Save preset", self)
        save_preset_action.triggered.connect(self.save_preset)
        save_preset_as_action = QAction("Save preset as…", self)
        save_preset_as_action.triggered.connect(self.save_preset_as)
        self.file_menu.addAction(new_project_action)
        self.file_menu.addAction(open_action)
        self.file_menu.addAction(open_project_action)
        self.file_menu.addAction(open_preset_action)
        self.recent_projects_menu = self.file_menu.addMenu("Open recent project")
        self.recent_presets_menu = self.file_menu.addMenu("Open recent preset")
        self.recent_files_menu = self.file_menu.addMenu("Open recent media")
        self.file_menu.addSeparator()
        self.file_menu.addAction(save_project_action)
        self.file_menu.addAction(save_project_as_action)
        self.file_menu.addAction(save_preset_action)
        self.file_menu.addAction(save_preset_as_action)
        batch_action = QAction("Batch export…", self)
        batch_action.triggered.connect(self.batch_export_dialog)
        export_action = QAction("Export render…", self)
        export_action.triggered.connect(self.export_render_dialog)
        self.file_menu.addAction(batch_action)
        self.file_menu.addAction(export_action)
        self.file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        self.file_menu.addAction(quit_action)

    def _build_toolbar(self) -> QWidget:

        bar = QFrame()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        open_btn = QPushButton("Open media…")
        open_btn.setObjectName("AccentAction")
        open_btn.clicked.connect(self.open_file_dialog)
        save_project_btn = QPushButton("Save project")
        save_project_btn.clicked.connect(self.save_project)
        analyze_btn = QPushButton("Analyze / reload")
        analyze_btn.clicked.connect(self.analyze_current_file)
        apply_btn = QPushButton("Apply mapping")
        apply_btn.clicked.connect(lambda: self._rebuild_geometry(reset_camera=False))
        batch_btn = QPushButton("Batch export…")
        batch_btn.clicked.connect(self.batch_export_dialog)
        focus_btn = QPushButton("Focus geometry")
        focus_btn.clicked.connect(self._focus_geometry)
        refresh_view_btn = QPushButton("Refresh viewport")
        refresh_view_btn.clicked.connect(lambda: self._force_live_viewport_refresh(burst=True))
        reset_btn = QPushButton("Reset layout")
        reset_btn.clicked.connect(self._reset_layout)

        self.show_preview_checkbox = QCheckBox("Show source preview")
        self.show_preview_checkbox.setChecked(True)
        self.show_preview_checkbox.toggled.connect(self._sync_layout_visibility)
        self._connect_setting_widget(self.show_preview_checkbox)
        self.show_panels_checkbox = QCheckBox("Show analysis panels")
        self.show_panels_checkbox.setChecked(True)
        self.show_panels_checkbox.toggled.connect(self._sync_layout_visibility)
        self._connect_setting_widget(self.show_panels_checkbox)

        layout.addWidget(self._build_brand_identity(), 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        controls_widget = QWidget()
        controls_widget.setObjectName("TopBarControls")
        controls_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)

        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(6)
        for widget in (open_btn, save_project_btn, analyze_btn, apply_btn, batch_btn, focus_btn, refresh_view_btn, reset_btn):
            buttons_row.addWidget(widget)
        buttons_row.addStretch(1)

        toggles_row = QHBoxLayout()
        toggles_row.setContentsMargins(0, 0, 0, 0)
        toggles_row.setSpacing(12)
        toggles_row.addWidget(self.show_preview_checkbox)
        toggles_row.addWidget(self.show_panels_checkbox)
        toggles_row.addStretch(1)

        controls_layout.addLayout(buttons_row)
        controls_layout.addLayout(toggles_row)

        layout.addWidget(controls_widget, 1)
        return bar

    def _build_brand_identity(self) -> QWidget:
        widget = QFrame()
        self.brand_identity_widget = widget
        widget.setObjectName("BrandCard")
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        logo = _load_brand_pixmap("metriq_logo_color.png")
        if logo is not None:
            self.brand_logo_label = ResponsivePixmapLabel(logo, preferred_height=74, min_width=180)
            self.brand_logo_label.setObjectName("BrandWordmark")
            self.brand_logo_label.setMinimumWidth(0)
        else:
            self.brand_logo_label = QLabel("METRIQ VISUALIZER")
            self.brand_logo_label.setObjectName("BrandWordmark")
            self.brand_logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.brand_logo_label.setMinimumWidth(180)

        divider = QFrame()
        divider.setObjectName("BrandCardDivider")
        divider.setFixedWidth(1)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)

        self.toolbar_file_caption = QLabel("Current file")
        self.toolbar_file_caption.setObjectName("BrandCardCaption")

        self.toolbar_file_label = ElidedLabel("No File Loaded")
        self.toolbar_file_label.setObjectName("ToolbarFileLabel")
        self.toolbar_file_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.toolbar_file_label.setMinimumWidth(120)

        text_layout.addWidget(self.toolbar_file_caption, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        text_layout.addWidget(self.toolbar_file_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        layout.addWidget(self.brand_logo_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(text_layout, 1)
        return widget

    def _apply_window_branding(self) -> None:
        icon_path = _brand_asset_path("metriq_mark_color.png")
        if icon_path.exists():
            try:
                self.setWindowIcon(QIcon(str(icon_path)))
            except Exception:
                pass
        if hasattr(self, "video_placeholder"):
            self.video_placeholder.setText(
                "Metriq Visualizer\n\n"
                "Open audio, video, CSV, TSV, or XLSX data to explore it as geometry."
            )
        if hasattr(self, "preview_meta"):
            self.preview_meta.setText("Local audio, video, and tabular data visualizer.")

    def _apply_theme_mode(self, theme_key: str, announce: bool = False) -> None:
        resolved = apply_app_theme(QApplication.instance(), DEFAULT_THEME_KEY)
        if hasattr(self, "_set_image_luts"):
            try:
                self._set_image_luts()
            except Exception:
                pass
        self.update()
        if self.analysis is not None and self.geometry_data is not None:
            try:
                self._rebuild_geometry(reset_camera=False)
            except Exception:
                try:
                    self._refresh_visuals(immediate=True)
                except Exception:
                    pass
        elif hasattr(self, "geometry_view"):
            try:
                self.geometry_view.update()
            except Exception:
                pass
        if announce:
            self._set_status("Dark mode active.", color=ACCENT)


    def _build_sidebar_brand_card(self) -> QWidget:
        widget = QFrame()
        self.brand_identity_widget = widget
        widget.setObjectName("SidebarBrandCard")
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(0)

        logo = _load_brand_pixmap("metriq_logo_color.png")
        if logo is not None:
            self.brand_logo_label = ResponsivePixmapLabel(logo, preferred_height=94, min_width=220)
            self.brand_logo_label.setObjectName("BrandWordmark")
            self.brand_logo_label.setMinimumWidth(0)
        else:
            self.brand_logo_label = QLabel(APP_TITLE)
            self.brand_logo_label.setObjectName("BrandWordmark")
            self.brand_logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self.brand_logo_label.setMinimumWidth(220)

        layout.addWidget(self.brand_logo_label, 0, Qt.AlignmentFlag.AlignCenter)
        return widget

    def _build_sidebar_quick_actions(self) -> QWidget:
        widget = QFrame()
        widget.setObjectName("QuickActionCard")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        open_btn = QPushButton("Open file…")
        open_btn.setObjectName("AccentAction")
        open_btn.clicked.connect(self.open_file_dialog)

        analyze_btn = QPushButton("Analyze / reload")
        analyze_btn.clicked.connect(self.analyze_current_file)

        apply_btn = QPushButton("Apply mapping")
        apply_btn.clicked.connect(lambda: self._rebuild_geometry(reset_camera=False))

        focus_btn = QPushButton("Focus geometry")
        focus_btn.clicked.connect(self._focus_geometry)

        self.show_preview_checkbox = QCheckBox("Show source preview")
        self.show_preview_checkbox.setChecked(True)
        self.show_preview_checkbox.toggled.connect(self._sync_layout_visibility)
        self._connect_setting_widget(self.show_preview_checkbox)

        self.show_panels_checkbox = QCheckBox("Show analysis panels")
        self.show_panels_checkbox.setChecked(True)
        self.show_panels_checkbox.toggled.connect(self._sync_layout_visibility)
        self._connect_setting_widget(self.show_panels_checkbox)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        top_row.addWidget(open_btn, 1)
        top_row.addWidget(analyze_btn, 1)

        second_row = QHBoxLayout()
        second_row.setContentsMargins(0, 0, 0, 0)
        second_row.setSpacing(6)
        second_row.addWidget(apply_btn, 1)
        second_row.addWidget(focus_btn, 1)

        toggles_row = QHBoxLayout()
        toggles_row.setContentsMargins(0, 0, 0, 0)
        toggles_row.setSpacing(12)
        toggles_row.addWidget(self.show_preview_checkbox)
        toggles_row.addWidget(self.show_panels_checkbox)
        toggles_row.addStretch(1)

        self.toolbar_file_caption = QLabel("Current file")
        self.toolbar_file_caption.setObjectName("BrandCardCaption")

        self.toolbar_file_label = ElidedLabel("No File Loaded")
        self.toolbar_file_label.setObjectName("ToolbarFileLabel")
        self.toolbar_file_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.toolbar_file_label.setMinimumWidth(0)
        self.file_label = self.toolbar_file_label

        layout.addLayout(top_row)
        layout.addLayout(second_row)
        layout.addLayout(toggles_row)
        layout.addWidget(self.toolbar_file_caption)
        layout.addWidget(self.toolbar_file_label)
        return widget

    def _build_feature_reference_group(self) -> QGroupBox:
        group = QGroupBox("Feature reference")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        help_label = QLabel("Features available in formulas. Combine multiple features per dimension.")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.feature_text = QPlainTextEdit()
        self.feature_text.setReadOnly(True)
        self.feature_text.setPlainText("Load a file and analyze it to populate the feature reference.")
        layout.addWidget(self.feature_text, 1)
        return group

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        self.left_panel_root = panel
        panel.setObjectName("LeftPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(self._build_sidebar_brand_card())
        layout.addWidget(self._build_sidebar_quick_actions())

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        controls_host = QWidget()
        controls_layout = QVBoxLayout(controls_host)
        controls_layout.setContentsMargins(8, 8, 8, 8)
        controls_layout.setSpacing(10)

        controls_layout.addWidget(self._build_extraction_group())
        controls_layout.addWidget(self._build_performance_group())
        controls_layout.addWidget(self._build_mapping_group())
        controls_layout.addWidget(self._build_visual_group())
        controls_layout.addWidget(self._build_motion_group())
        controls_layout.addWidget(self._build_branding_group())
        controls_layout.addWidget(self._build_export_group())
        controls_layout.addWidget(self._build_status_group())
        controls_layout.addWidget(self._build_feature_reference_group())
        controls_layout.addStretch(1)

        controls_scroll.setWidget(controls_host)
        layout.addWidget(controls_scroll, 1)

        return panel

    def _build_right_panel(self) -> QWidget:

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.right_splitter, 1)

        self.top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.right_splitter.addWidget(self.top_splitter)

        self.geometry_group = QGroupBox("GPU geometry")
        geom_layout = QVBoxLayout(self.geometry_group)
        geom_layout.setContentsMargins(6, 6, 6, 6)
        geom_layout.setSpacing(4)
        self.geometry_view = GeometryGLView()
        self.geometry_view.setMinimumSize(420, 300)
        geom_layout.addWidget(self.geometry_view, 1)
        self.geometry_hud = QLabel("0.00 s • 0.0 Hz")
        self.geometry_hud.setObjectName("GeometryHud")
        geom_layout.addWidget(self.geometry_hud, 0, Qt.AlignmentFlag.AlignLeft)
        self.top_splitter.addWidget(self.geometry_group)

        self.preview_group = QGroupBox("Source preview")
        preview_layout = QVBoxLayout(self.preview_group)
        preview_layout.setContentsMargins(6, 6, 6, 6)
        preview_layout.setSpacing(6)
        self.preview_tabs = QTabWidget()
        self.video_widget = QVideoWidget()
        self.video_placeholder = QLabel("No source loaded")
        self.video_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_placeholder.setWordWrap(True)
        self.video_placeholder.setMinimumHeight(220)
        self.preview_tabs.addTab(self.video_widget, "Video")
        self.preview_tabs.addTab(self.video_placeholder, "Info")
        preview_layout.addWidget(self.preview_tabs, 1)
        self.preview_meta = QLabel("Load a video or audio file.")
        self.preview_meta.setWordWrap(True)
        preview_layout.addWidget(self.preview_meta)
        self.top_splitter.addWidget(self.preview_group)

        self.analysis_group = QGroupBox("Analysis panels")
        analysis_layout = QVBoxLayout(self.analysis_group)
        analysis_layout.setContentsMargins(6, 6, 6, 6)
        analysis_layout.setSpacing(0)
        self.analysis_tabs = AnalysisTabs()
        self.analysis_tabs.timeClicked.connect(self._audition_time_at)
        analysis_layout.addWidget(self.analysis_tabs, 1)
        self.diagnostics_dashboard = DiagnosticsDashboard()
        analysis_layout.addWidget(self.diagnostics_dashboard, 0)
        self.right_splitter.addWidget(self.analysis_group)

        self.top_splitter.setStretchFactor(0, 8)
        self.top_splitter.setStretchFactor(1, 3)
        self.top_splitter.setSizes([1200, 360])
        self.right_splitter.setStretchFactor(0, 5)
        self.right_splitter.setStretchFactor(1, 2)
        self.right_splitter.setSizes([820, 260])

        self.media_player.setVideoOutput(self.video_widget)
        self.geometry_view.resetRequested.connect(self._reset_camera)
        self.geometry_view.cameraChanged.connect(self._sync_camera_controls_from_view)
        self.geometry_view.pointPicked.connect(self._on_geometry_point_picked)
        return panel

    def _build_transport_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TransportBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)
        reset_btn = QPushButton("Reset")
        reset_btn.clicked.connect(self.reset_time)
        step_back_btn = QPushButton("Step −")
        step_back_btn.clicked.connect(lambda: self.seek_relative(-0.10))
        step_forward_btn = QPushButton("Step +")
        step_forward_btn.clicked.connect(lambda: self.seek_relative(0.10))
        self.loop_checkbox = QCheckBox("Loop")
        self.loop_checkbox.setChecked(True)

        self.timeline_slider = QSlider(Qt.Orientation.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.sliderPressed.connect(self._on_slider_pressed)
        self.timeline_slider.sliderReleased.connect(self._on_slider_released)
        self.timeline_slider.sliderMoved.connect(self._on_slider_moved)

        self.time_label = QLabel("0.00 s / 0.00 s")
        self.jump_to_spin = QDoubleSpinBox()
        self.jump_to_spin.setDecimals(2)
        self.jump_to_spin.setSingleStep(0.25)
        self.jump_to_spin.setRange(0.0, 0.0)
        self.jump_to_spin.setKeyboardTracking(False)
        self.jump_to_spin.setFixedWidth(92)
        jump_btn = QPushButton("Go")
        jump_btn.clicked.connect(self._jump_to_time_from_spin)
        self.jump_to_spin.editingFinished.connect(self._jump_to_time_from_spin)

        self.transport_info = QLabel("Qt Multimedia")
        self.transport_info.setObjectName("TransportInfo")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(85)
        self.volume_slider.setFixedWidth(120)
        self.volume_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(float(v) / 100.0))
        self._connect_setting_widget(self.loop_checkbox)
        self._connect_setting_widget(self.volume_slider)

        layout.addWidget(self.play_button)
        layout.addWidget(reset_btn)
        layout.addWidget(step_back_btn)
        layout.addWidget(step_forward_btn)
        layout.addWidget(self.loop_checkbox)
        layout.addWidget(self.timeline_slider, 1)
        layout.addWidget(self.time_label)
        layout.addWidget(QLabel("Jump"))
        layout.addWidget(self.jump_to_spin)
        layout.addWidget(jump_btn)
        layout.addWidget(QLabel("Volume"))
        layout.addWidget(self.volume_slider)
        layout.addWidget(self.transport_info)
        return bar

    def _build_source_group(self) -> QGroupBox:
        group = QGroupBox("Source")
        layout = QVBoxLayout(group)
        open_btn = QPushButton("Open file…")
        open_btn.clicked.connect(self.open_file_dialog)
        reload_btn = QPushButton("Analyze / reload")
        reload_btn.clicked.connect(self.analyze_current_file)
        self.file_label = QLabel("No file loaded")
        self.file_label.setWordWrap(True)
        layout.addWidget(open_btn)
        layout.addWidget(reload_btn)
        layout.addWidget(self.file_label)
        return group

    def _build_project_group(self) -> QGroupBox:
        group = QGroupBox("Project")
        layout = QVBoxLayout(group)

        self.project_label = QLabel("No project file")
        self.project_label.setWordWrap(True)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        new_btn = QPushButton("New")
        open_btn = QPushButton("Open…")
        save_btn = QPushButton("Save")
        save_as_btn = QPushButton("Save as…")
        new_btn.clicked.connect(self.new_project)
        open_btn.clicked.connect(self.open_project_dialog)
        save_btn.clicked.connect(self.save_project)
        save_as_btn.clicked.connect(self.save_project_as)
        row_layout.addWidget(new_btn)
        row_layout.addWidget(open_btn)
        row_layout.addWidget(save_btn)
        row_layout.addWidget(save_as_btn)

        layout.addWidget(self.project_label)
        layout.addWidget(row)
        return group

    def _build_profile_group(self) -> QGroupBox:
        group = QGroupBox("Profiles + session")
        form = QFormLayout(group)

        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        self.profile_combo = QComboBox()
        load_btn = QPushButton("Load")
        save_btn = QPushButton("Save")
        save_as_btn = QPushButton("Save as…")
        delete_btn = QPushButton("Delete")
        load_btn.clicked.connect(self.load_selected_profile)
        save_btn.clicked.connect(self.save_current_profile)
        save_as_btn.clicked.connect(self.save_current_profile_as)
        delete_btn.clicked.connect(self.delete_selected_profile)
        row_layout.addWidget(self.profile_combo, 1)
        row_layout.addWidget(load_btn)
        row_layout.addWidget(save_btn)
        row_layout.addWidget(save_as_btn)
        row_layout.addWidget(delete_btn)

        self.restore_last_session_checkbox = QCheckBox("Restore last session on launch")
        self.restore_last_session_checkbox.setChecked(True)
        self.restore_last_session_checkbox.toggled.connect(self._schedule_autosave)

        form.addRow("Saved profile", row)
        form.addRow(self.restore_last_session_checkbox)
        return group

    def _build_diagnostics_group(self) -> QGroupBox:
        group = QGroupBox("Diagnostics mode")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        self.live_monitor_status_label = QLabel("Idle")
        self.live_monitor_status_label.setObjectName("LiveMonitorStatus")
        self.live_monitor_score_label = QLabel("Score 0.00")
        self.live_monitor_baseline_label = QLabel("No baseline")
        status_row.addWidget(self.live_monitor_status_label)
        status_row.addWidget(self.live_monitor_score_label)
        status_row.addWidget(self.live_monitor_baseline_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        device_row = QWidget()
        device_layout = QHBoxLayout(device_row)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(6)
        self.live_device_combo = QComboBox()
        refresh_btn = QPushButton("Refresh devices")
        refresh_btn.clicked.connect(self._refresh_live_device_list)
        device_layout.addWidget(self.live_device_combo, 1)
        device_layout.addWidget(refresh_btn)
        layout.addWidget(QLabel("Input device"))
        layout.addWidget(device_row)

        control_row = QWidget()
        control_layout = QHBoxLayout(control_row)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(6)
        self.live_start_button = QPushButton("Start live input")
        self.live_stop_button = QPushButton("Stop")
        self.live_stop_button.setEnabled(False)
        self.live_start_button.clicked.connect(self._start_live_monitor)
        self.live_stop_button.clicked.connect(self._stop_live_monitor)
        self.live_drive_visualizer_checkbox = QCheckBox("Drive main visualizer from live input")
        self.live_drive_visualizer_checkbox.setChecked(True)
        self.live_drive_visualizer_checkbox.toggled.connect(self._on_live_drive_visualizer_toggled)
        self._connect_setting_widget(self.live_drive_visualizer_checkbox)
        control_layout.addWidget(self.live_start_button)
        control_layout.addWidget(self.live_stop_button)
        control_layout.addWidget(self.live_drive_visualizer_checkbox)
        control_layout.addStretch(1)
        layout.addWidget(control_row)

        form = QFormLayout()
        self.live_history_spin = self._make_double_spin(5.0, 180.0, 30.0, step=1.0)
        self.live_history_spin.setSuffix(" s")
        self.live_history_spin.valueChanged.connect(self._on_live_settings_changed)
        self.live_baseline_window_spin = self._make_double_spin(2.0, 60.0, 8.0, step=1.0)
        self.live_baseline_window_spin.setSuffix(" s")
        self.live_baseline_window_spin.valueChanged.connect(self._schedule_autosave)
        self.live_monitor_gate_spin = self._make_double_spin(0.0, 60.0, 15.0, step=1.0)
        self.live_monitor_gate_spin.setSuffix(" dB")
        self.live_monitor_gate_spin.setToolTip("Ignore frames quieter than this many dB below the recent peak in the live window.")
        self.live_monitor_gate_spin.valueChanged.connect(self._on_live_settings_changed)
        self.live_warn_threshold_spin = self._make_double_spin(0.5, 20.0, 2.0, step=0.1)
        self.live_warn_threshold_spin.valueChanged.connect(self._on_live_settings_changed)
        self.live_alarm_threshold_spin = self._make_double_spin(0.6, 30.0, 4.0, step=0.1)
        self.live_alarm_threshold_spin.valueChanged.connect(self._on_live_settings_changed)
        self.live_min_event_spin = self._make_double_spin(0.05, 10.0, 0.55, step=0.05)
        self.live_min_event_spin.setSuffix(" s")
        self.live_min_event_spin.valueChanged.connect(self._on_live_settings_changed)
        for widget in (
            self.live_history_spin,
            self.live_baseline_window_spin,
            self.live_monitor_gate_spin,
            self.live_warn_threshold_spin,
            self.live_alarm_threshold_spin,
            self.live_min_event_spin,
            self.live_device_combo,
        ):
            self._connect_setting_widget(widget)
        form.addRow("History window", self.live_history_spin)
        form.addRow("Baseline capture window", self.live_baseline_window_spin)
        form.addRow("Relative low-volume cutoff", self.live_monitor_gate_spin)
        form.addRow("Warning threshold", self.live_warn_threshold_spin)
        form.addRow("Alarm threshold", self.live_alarm_threshold_spin)
        form.addRow("Min event length", self.live_min_event_spin)
        layout.addLayout(form)

        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)
        capture_btn = QPushButton("Capture baseline from recent audio")
        clear_baseline_btn = QPushButton("Clear baseline")
        clear_events_btn = QPushButton("Clear events")
        capture_btn.clicked.connect(self._capture_live_baseline)
        clear_baseline_btn.clicked.connect(self._clear_live_baseline)
        clear_events_btn.clicked.connect(self._clear_live_events)
        action_layout.addWidget(capture_btn)
        action_layout.addWidget(clear_baseline_btn)
        action_layout.addWidget(clear_events_btn)
        layout.addWidget(action_row)

        self.live_event_list = QListWidget()
        self.live_event_list.setMinimumHeight(180)
        layout.addWidget(QLabel("Detected events"))
        layout.addWidget(self.live_event_list, 1)

        help_label = QLabel(
            "Use live input for diagnostics mode. Capture a healthy baseline from recent audio, then monitor anomaly score and events in real time."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        return group

    def _build_branding_group(self) -> QGroupBox:
        group = QGroupBox("Branding + product")
        form = QFormLayout(group)
        self.project_title_edit = QLineEdit(APP_TITLE)
        self.project_subtitle_edit = QLineEdit()
        self.project_subtitle_edit.setPlaceholderText("Optional subtitle / client context")
        self.watermark_edit = QLineEdit()
        self.watermark_edit.setPlaceholderText("Optional watermark, brand, or studio tag")
        self.show_export_title_checkbox = QCheckBox("Show project title on export")
        self.show_export_title_checkbox.setChecked(True)
        self.show_export_watermark_checkbox = QCheckBox("Show watermark on export")
        self.show_export_colorbar_checkbox = QCheckBox("Show geometry colorbar in export")
        self.show_export_colorbar_checkbox.setChecked(False)

        for widget in (
            self.project_title_edit,
            self.project_subtitle_edit,
            self.watermark_edit,
            self.show_export_title_checkbox,
            self.show_export_watermark_checkbox,
            self.show_export_colorbar_checkbox,
        ):
            self._connect_setting_widget(widget)

        form.addRow("Project title", self.project_title_edit)
        form.addRow("Subtitle", self.project_subtitle_edit)
        form.addRow("Watermark", self.watermark_edit)
        form.addRow(self.show_export_title_checkbox)
        form.addRow(self.show_export_watermark_checkbox)
        form.addRow(self.show_export_colorbar_checkbox)
        return group

    def _build_extraction_group(self) -> QGroupBox:
        group = QGroupBox("Extraction settings")
        form = QFormLayout(group)
        self.sample_rate_spin = self._make_spin(8000, 96000, 22050, step=1000)
        self.n_fft_spin = self._make_spin(256, 16384, 2048, step=256)
        self.hop_length_spin = self._make_spin(64, 4096, 256, step=64)
        self.max_points_spin = self._make_spin(200, 50000, 3500, step=100)
        self.low_volume_cutoff_spin = self._make_double_spin(0.0, 80.0, 0.0, step=1.0)
        self.low_volume_cutoff_spin.setSuffix(" dB")
        self.low_volume_cutoff_spin.setToolTip("Hide geometry/traces below this RMS level relative to the loudest frame. 0 dB disables the gate.")
        self.max_points_spin.valueChanged.connect(self._refresh_visuals)
        self.low_volume_cutoff_spin.valueChanged.connect(self._on_low_volume_cutoff_changed)
        for widget in (self.sample_rate_spin, self.n_fft_spin, self.hop_length_spin, self.max_points_spin, self.low_volume_cutoff_spin):
            self._connect_setting_widget(widget)
        form.addRow("Sample rate", self.sample_rate_spin)
        form.addRow("FFT size", self.n_fft_spin)
        form.addRow("Hop length", self.hop_length_spin)
        form.addRow("Max visible points", self.max_points_spin)
        form.addRow("Low-volume cutoff", self.low_volume_cutoff_spin)
        return group

    def _build_performance_group(self) -> QGroupBox:
        group = QGroupBox("Performance + stability")
        form = QFormLayout(group)

        self.performance_mode_combo = QComboBox()
        self.performance_mode_combo.addItems(list(PERFORMANCE_PRESETS.keys()))
        self.performance_mode_combo.setCurrentText("Balanced")
        self.live_redraw_fps_spin = self._make_spin(12, 60, 30, step=1)
        self.live_point_budget_spin = self._make_spin(600, 12000, 2800, step=100)
        self.preview_scale_spin = self._make_double_spin(0.35, 1.0, 0.72, 0.05)

        self.performance_mode_combo.currentTextChanged.connect(self._apply_performance_preset)
        self.live_redraw_fps_spin.valueChanged.connect(self._apply_frame_timer_settings)
        self.live_redraw_fps_spin.valueChanged.connect(self._refresh_visuals)
        self.live_point_budget_spin.valueChanged.connect(self._refresh_visuals)
        self.preview_scale_spin.valueChanged.connect(self._clear_export_preview_session)

        for widget in (
            self.performance_mode_combo,
            self.live_redraw_fps_spin,
            self.live_point_budget_spin,
            self.preview_scale_spin,
        ):
            self._connect_setting_widget(widget)

        helper = QLabel(
            "Stable/Balanced/Quality only changes live playback and export-preview pressure. Final offline renders keep full export quality."
        )
        helper.setWordWrap(True)

        form.addRow("Mode", self.performance_mode_combo)
        form.addRow("Live redraw cap (FPS)", self.live_redraw_fps_spin)
        form.addRow("Live point budget", self.live_point_budget_spin)
        form.addRow("Export preview scale", self.preview_scale_spin)
        form.addRow(helper)
        return group

    def _build_mapping_group(self) -> QGroupBox:
        group = QGroupBox("Geometry mapping")
        form = QFormLayout(group)
        top_row = QWidget()
        top_layout = QHBoxLayout(top_row)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(list(DEFAULT_PRESETS.keys()))
        self._connect_setting_widget(self.preset_combo)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(lambda: self._load_preset(self.preset_combo.currentText()))
        top_layout.addWidget(self.preset_combo, 1)
        top_layout.addWidget(load_btn)

        self.x_edit = QLineEdit()
        self.y_edit = QLineEdit()
        self.z_edit = QLineEdit()
        self.color_edit = QLineEdit()
        self.size_edit = QLineEdit()
        for widget in (self.x_edit, self.y_edit, self.z_edit, self.color_edit, self.size_edit):
            widget.setPlaceholderText("Enter a formula")
            widget.setClearButtonEnabled(True)
            widget.returnPressed.connect(lambda: self._rebuild_geometry(reset_camera=False))
            self._connect_setting_widget(widget)

        self.x_label_edit = QLineEdit()
        self.y_label_edit = QLineEdit()
        self.z_label_edit = QLineEdit()
        self.color_label_edit = QLineEdit()
        self.size_label_edit = QLineEdit()
        for widget in (self.x_label_edit, self.y_label_edit, self.z_label_edit, self.color_label_edit, self.size_label_edit):
            widget.setPlaceholderText("Optional plain-English label")
            widget.setClearButtonEnabled(True)
            widget.editingFinished.connect(self._apply_display_labels_to_current_geometry)
            self._connect_setting_widget(widget)

        self.normalize_combo = QComboBox()
        self.normalize_combo.addItems(["zscore", "minmax", "raw"])
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(["plasma", "viridis", "inferno", "magma", "cividis", "turbo"])
        self._connect_setting_widget(self.normalize_combo)
        self._connect_setting_widget(self.colormap_combo)

        apply_btn = QPushButton("Apply mapping")
        apply_btn.clicked.connect(lambda: self._rebuild_geometry(reset_camera=False))

        form.addRow("Preset", top_row)
        form.addRow("X", self.x_edit)
        form.addRow("X label", self.x_label_edit)
        form.addRow("Y", self.y_edit)
        form.addRow("Y label", self.y_label_edit)
        form.addRow("Z", self.z_edit)
        form.addRow("Z label", self.z_label_edit)
        form.addRow("Color", self.color_edit)
        form.addRow("Color label", self.color_label_edit)
        form.addRow("Size", self.size_edit)
        form.addRow("Size label", self.size_label_edit)
        form.addRow("Normalize", self.normalize_combo)
        form.addRow("Colormap", self.colormap_combo)
        form.addRow(apply_btn)
        return group

    def _build_visual_group(self) -> QGroupBox:
        group = QGroupBox("Visualizer behavior")
        form = QFormLayout(group)
        self.visual_behavior_preset_combo = QComboBox()
        self.visual_behavior_preset_combo.addItem(VISUALIZER_BEHAVIOR_CUSTOM_LABEL)
        self.visual_behavior_preset_combo.setToolTip(f"Drop {PRESET_EXTENSION} files into the {VISUALIZER_BEHAVIOR_PRESET_DIRECTORY_NAME}/ folder next to the app.")
        self._reload_visualizer_behavior_presets()
        self.visual_behavior_apply_btn = QPushButton("Apply")
        self.visual_behavior_apply_btn.setEnabled(bool(self._visualizer_behavior_presets))
        self.visual_behavior_apply_btn.clicked.connect(
            lambda: self._apply_visualizer_behavior_preset(self.visual_behavior_preset_combo.currentText())
        )
        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(6)
        preset_layout.addWidget(self.visual_behavior_preset_combo, 1)
        preset_layout.addWidget(self.visual_behavior_apply_btn)

        self.history_combo = QComboBox()
        self.history_combo.addItems(["Trail fade", "Cumulative reveal", "Full static"])
        self.point_lifespan_spin = self._make_double_spin(0.25, 20.0, 3.0, 0.25)
        self.fade_curve_spin = self._make_double_spin(0.25, 3.0, 1.35, 0.05)
        self.line_width_spin = self._make_double_spin(0.0, 6.0, 1.35, 0.05)
        self.comet_duration_spin = self._make_double_spin(0.02, 1.8, 0.48, 0.02)
        self.flash_duration_spin = self._make_double_spin(0.02, 0.8, 0.18, 0.02)
        self.alpha_spin = self._make_double_spin(0.05, 1.0, 0.82, 0.01)
        self.point_size_scale_spin = self._make_double_spin(0.05, 4.0, 0.4, 0.05)
        self.path_curve_mode_combo = QComboBox()
        self.path_curve_mode_combo.addItems(["Straight", "Smooth spline"])
        self.path_curve_mode_combo.setCurrentText("Smooth spline")
        self.curve_detail_spin = self._make_spin(2, 10, 4, step=1)
        self.render_mode_combo = QComboBox()
        self.render_mode_combo.addItems(["Points + line", "Points only", "Tube", "Tube + points"])
        self.tube_radius_scale_spin = self._make_double_spin(0.1, 4.0, 1.0, 0.05)
        self.tube_sides_spin = self._make_spin(3, 32, 12, step=1)
        self.tube_follow_size_checkbox = QCheckBox("Tube radius follows point size / loudness")
        self.tube_follow_size_checkbox.setChecked(True)
        self.tube_taper_spin = self._make_double_spin(0.0, 0.95, 0.2, 0.05)
        self.connect_lines_checkbox = QCheckBox("Connect points in time order")
        self.connect_lines_checkbox.setChecked(True)
        self.ghost_path_checkbox = QCheckBox("Show faint full-path guide")
        self.ghost_path_checkbox.setChecked(False)
        self.show_head_checkbox = QCheckBox("Show current head marker")
        self.show_head_checkbox.setChecked(True)
        self.head_size_scale_spin = self._make_double_spin(0.0, 2.0, 0.24, 0.05)
        self.halo_size_scale_spin = self._make_double_spin(0.0, 5.0, 0.45, 0.05)
        self.flash_size_scale_spin = self._make_double_spin(0.0, 4.0, 0.05, 0.05)
        self.show_axes_checkbox = QCheckBox("Show axis tripod + grid")
        self.show_axes_checkbox.setChecked(True)
        self.show_axis_labels_checkbox = QCheckBox("Show axis labels")
        self.show_axis_labels_checkbox.setChecked(True)
        self.point_label_mode_combo = QComboBox()
        self.point_label_mode_combo.addItems(["Off", "Current point", "Sparse visible"])
        self.point_label_content_combo = QComboBox()
        self.point_label_content_combo.addItems(["Time + Hz", "Time", "Dominant Hz", "Index"])
        self.point_label_count_spin = self._make_spin(1, 24, 8, step=1)

        for widget in (
            self.history_combo,
            self.point_lifespan_spin,
            self.fade_curve_spin,
            self.line_width_spin,
            self.comet_duration_spin,
            self.flash_duration_spin,
            self.alpha_spin,
            self.point_size_scale_spin,
            self.path_curve_mode_combo,
            self.curve_detail_spin,
            self.render_mode_combo,
            self.tube_radius_scale_spin,
            self.tube_sides_spin,
            self.tube_follow_size_checkbox,
            self.tube_taper_spin,
            self.connect_lines_checkbox,
            self.ghost_path_checkbox,
            self.show_head_checkbox,
            self.head_size_scale_spin,
            self.halo_size_scale_spin,
            self.flash_size_scale_spin,
            self.show_axes_checkbox,
            self.show_axis_labels_checkbox,
            self.point_label_mode_combo,
            self.point_label_content_combo,
            self.point_label_count_spin,
        ):
            self._connect_visual_control(widget)
            self._connect_visual_behavior_tracking(widget)

        form.addRow("Behavior preset", preset_row)
        form.addRow("History mode", self.history_combo)
        form.addRow("Point lifespan (s)", self.point_lifespan_spin)
        form.addRow("Fade curve", self.fade_curve_spin)
        form.addRow("Trail line width", self.line_width_spin)
        form.addRow("Comet duration (s)", self.comet_duration_spin)
        form.addRow("Point flash duration (s)", self.flash_duration_spin)
        form.addRow("Point alpha", self.alpha_spin)
        form.addRow("Point size scale", self.point_size_scale_spin)
        form.addRow("Path style", self.path_curve_mode_combo)
        form.addRow("Curve detail", self.curve_detail_spin)
        form.addRow("Render mode", self.render_mode_combo)
        form.addRow("Tube radius scale", self.tube_radius_scale_spin)
        form.addRow("Tube sides", self.tube_sides_spin)
        form.addRow(self.tube_follow_size_checkbox)
        form.addRow("Tube taper", self.tube_taper_spin)
        form.addRow(self.connect_lines_checkbox)
        form.addRow(self.ghost_path_checkbox)
        form.addRow(self.show_head_checkbox)
        form.addRow("Head size scale", self.head_size_scale_spin)
        form.addRow("Halo size scale", self.halo_size_scale_spin)
        form.addRow("Flash size scale", self.flash_size_scale_spin)
        form.addRow(self.show_axes_checkbox)
        form.addRow(self.show_axis_labels_checkbox)
        form.addRow("Point label mode", self.point_label_mode_combo)
        form.addRow("Point label text", self.point_label_content_combo)
        form.addRow("Max sparse labels", self.point_label_count_spin)
        return group

    def _build_motion_group(self) -> QGroupBox:
        group = QGroupBox("Camera + motion")
        form = QFormLayout(group)
        self.autorotate_checkbox = QCheckBox("Auto-rotate geometry")
        self.autorotate_checkbox.setChecked(True)
        self.rotation_speed_spin = self._make_double_spin(-120.0, 120.0, 16.0, 1.0)
        self.elevation_spin = self._make_double_spin(-15.0, 89.0, 24.0, 1.0)
        self.zoom_spin = self._make_double_spin(0.25, 6.0, 1.0, 0.05)
        self.zoom_spin.valueChanged.connect(self._refresh_visuals)
        self.elevation_spin.valueChanged.connect(self._refresh_visuals)
        self.rotation_speed_spin.valueChanged.connect(self._refresh_visuals)
        self.autorotate_checkbox.toggled.connect(self._refresh_visuals)
        for widget in (self.autorotate_checkbox, self.rotation_speed_spin, self.elevation_spin, self.zoom_spin):
            self._connect_setting_widget(widget)

        camera_row = QWidget()
        row = QHBoxLayout(camera_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        zoom_in_btn = QPushButton("Zoom +")
        zoom_out_btn = QPushButton("Zoom −")
        reset_btn = QPushButton("Reset camera")
        zoom_in_btn.clicked.connect(lambda: self.zoom_spin.setValue(min(self.zoom_spin.maximum(), self.zoom_spin.value() * 1.18)))
        zoom_out_btn.clicked.connect(lambda: self.zoom_spin.setValue(max(self.zoom_spin.minimum(), self.zoom_spin.value() / 1.18)))
        reset_btn.clicked.connect(self._reset_camera)
        row.addWidget(zoom_in_btn)
        row.addWidget(zoom_out_btn)
        row.addWidget(reset_btn)

        form.addRow(self.autorotate_checkbox)
        form.addRow("Rotation speed (deg/s)", self.rotation_speed_spin)
        form.addRow("Elevation", self.elevation_spin)
        form.addRow("Zoom", self.zoom_spin)
        form.addRow(camera_row)
        return group

    def _build_keyframes_group(self) -> QGroupBox:
        group = QGroupBox("Camera keyframes")
        layout = QVBoxLayout(group)
        self.use_keyframes_checkbox = QCheckBox("Use camera keyframes on export")
        self.use_keyframes_checkbox.setChecked(False)
        self.keyframe_easing_combo = QComboBox()
        self.keyframe_easing_combo.addItems(["ease_in_out", "linear", "ease_in", "ease_out"])
        self.keyframe_list = QListWidget()
        self.keyframe_list.itemDoubleClicked.connect(self._jump_to_selected_keyframe)
        for widget in (self.use_keyframes_checkbox, self.keyframe_easing_combo):
            self._connect_setting_widget(widget)
        self.use_keyframes_checkbox.toggled.connect(self._clear_export_preview_session)
        self.keyframe_easing_combo.currentTextChanged.connect(self._clear_export_preview_session)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        add_btn = QPushButton("Add current")
        update_btn = QPushButton("Update selected")
        remove_btn = QPushButton("Remove")
        clear_btn = QPushButton("Clear")
        orbit_btn = QPushButton("Bake orbit")
        add_btn.clicked.connect(self._add_camera_keyframe_current)
        update_btn.clicked.connect(self._update_selected_camera_keyframe)
        remove_btn.clicked.connect(self._remove_selected_camera_keyframe)
        clear_btn.clicked.connect(self._clear_camera_keyframes)
        orbit_btn.clicked.connect(self._bake_orbit_keyframes)
        for button in (add_btn, update_btn, remove_btn, clear_btn, orbit_btn):
            row_layout.addWidget(button)
        form = QFormLayout()
        form.addRow(self.use_keyframes_checkbox)
        form.addRow("Keyframe easing", self.keyframe_easing_combo)
        layout.addLayout(form)
        layout.addWidget(self.keyframe_list)
        layout.addWidget(row)
        return group

    def _build_annotation_group(self) -> QGroupBox:
        group = QGroupBox("Regions + bookmarks")
        layout = QVBoxLayout(group)

        bookmark_label = QLabel("Bookmarks")
        self.bookmark_list = QListWidget()
        self.bookmark_list.itemDoubleClicked.connect(self._jump_to_selected_bookmark)
        bookmark_row = QWidget()
        bookmark_row_layout = QHBoxLayout(bookmark_row)
        bookmark_row_layout.setContentsMargins(0, 0, 0, 0)
        bookmark_row_layout.setSpacing(6)
        add_bookmark_btn = QPushButton("Add @ playhead")
        remove_bookmark_btn = QPushButton("Remove")
        add_bookmark_btn.clicked.connect(self._add_bookmark_current)
        remove_bookmark_btn.clicked.connect(self._remove_selected_bookmark)
        bookmark_row_layout.addWidget(add_bookmark_btn)
        bookmark_row_layout.addWidget(remove_bookmark_btn)

        region_label = QLabel("Timeline regions")
        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(6)
        self.region_start_spin = self._make_double_spin(0.0, 86400.0, 0.0, 0.05)
        self.region_end_spin = self._make_double_spin(0.0, 86400.0, 0.0, 0.05)
        for widget in (self.region_start_spin, self.region_end_spin):
            self._connect_setting_widget(widget)
        range_layout.addWidget(QLabel("Start"))
        range_layout.addWidget(self.region_start_spin, 1)
        range_layout.addWidget(QLabel("End"))
        range_layout.addWidget(self.region_end_spin, 1)

        region_mark_row = QWidget()
        region_mark_layout = QHBoxLayout(region_mark_row)
        region_mark_layout.setContentsMargins(0, 0, 0, 0)
        region_mark_layout.setSpacing(6)
        start_btn = QPushButton("Start ← playhead")
        end_btn = QPushButton("End ← playhead")
        add_region_btn = QPushButton("Add region")
        use_region_btn = QPushButton("Use selected for export")
        start_btn.clicked.connect(lambda: self.region_start_spin.setValue(float(self.current_time)))
        end_btn.clicked.connect(lambda: self.region_end_spin.setValue(float(self.current_time)))
        add_region_btn.clicked.connect(self._add_region_from_spins)
        use_region_btn.clicked.connect(self._apply_selected_region_to_export)
        for button in (start_btn, end_btn, add_region_btn, use_region_btn):
            region_mark_layout.addWidget(button)

        self.region_list = QListWidget()
        self.region_list.itemDoubleClicked.connect(self._jump_to_selected_region)
        region_list_row = QWidget()
        region_list_layout = QHBoxLayout(region_list_row)
        region_list_layout.setContentsMargins(0, 0, 0, 0)
        region_list_layout.setSpacing(6)
        remove_region_btn = QPushButton("Remove region")
        remove_region_btn.clicked.connect(self._remove_selected_region)
        region_list_layout.addWidget(remove_region_btn)
        region_list_layout.addStretch(1)

        layout.addWidget(bookmark_label)
        layout.addWidget(self.bookmark_list)
        layout.addWidget(bookmark_row)
        layout.addSpacing(4)
        layout.addWidget(region_label)
        layout.addWidget(range_row)
        layout.addWidget(region_mark_row)
        layout.addWidget(self.region_list)
        layout.addWidget(region_list_row)
        return group

    def _build_queue_group(self) -> QGroupBox:
        group = QGroupBox("Export queue")
        layout = QVBoxLayout(group)
        self.export_queue_list = QListWidget()
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        add_btn = QPushButton("Queue current render…")
        run_btn = QPushButton("Run queue")
        remove_btn = QPushButton("Remove")
        clear_btn = QPushButton("Clear")
        add_btn.clicked.connect(self.queue_current_export_dialog)
        run_btn.clicked.connect(self.run_export_queue)
        remove_btn.clicked.connect(self._remove_selected_queue_job)
        clear_btn.clicked.connect(self._clear_export_queue)
        for button in (add_btn, run_btn, remove_btn, clear_btn):
            row_layout.addWidget(button)
        layout.addWidget(self.export_queue_list)
        layout.addWidget(row)
        return group

    def _build_export_group(self) -> QGroupBox:
        group = QGroupBox("Export render")
        form = QFormLayout(group)
        self.export_preset_combo = QComboBox()
        self.export_preset_combo.addItems([preset.title for preset in EXPORT_PRESETS])
        apply_preset_btn = QPushButton("Apply preset")
        apply_preset_btn.clicked.connect(lambda: self._apply_export_preset_by_title(self.export_preset_combo.currentText()))
        preset_row = QWidget()
        preset_layout = QHBoxLayout(preset_row)
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(6)
        preset_layout.addWidget(self.export_preset_combo, 1)
        preset_layout.addWidget(apply_preset_btn)

        self.render_width_spin = self._make_spin(640, 7680, 1920, step=160)
        self.render_height_spin = self._make_spin(360, 4320, 1080, step=90)
        self.render_fps_spin = self._make_spin(12, 120, 30, step=1)
        self.export_engine_combo = QComboBox()
        self.export_engine_combo.addItems(list(EXPORT_ENGINE_CHOICES))
        self.export_engine_combo.setCurrentText(EXPORT_ENGINE_AUTO_LABEL)
        self.export_engine_combo.setToolTip(
            "Auto uses FFmpeg hardware H.264 encoders when available, then CPU libx264, then the legacy OpenCV writer."
        )
        self.export_quality_combo = QComboBox()
        self.export_quality_combo.addItems(list(EXPORT_QUALITY_CHOICES))
        self.export_quality_combo.setCurrentText(EXPORT_QUALITY_BALANCED_LABEL)
        self.export_quality_combo.setToolTip("Controls encoder speed/quality tradeoff. The visual frame renderer remains full quality.")
        self.limit_export_range_checkbox = QCheckBox("Limit export / CSV to time range")
        self.limit_export_range_checkbox.setChecked(False)
        self.export_start_spin = self._make_double_spin(0.0, 86400.0, 0.0, 0.05)
        self.export_end_spin = self._make_double_spin(0.0, 86400.0, 0.0, 0.05)
        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(6)
        start_btn = QPushButton("Start ← playhead")
        end_btn = QPushButton("End ← playhead")
        full_btn = QPushButton("Full clip")
        start_btn.clicked.connect(lambda: self._set_export_range_from_playhead("start"))
        end_btn.clicked.connect(lambda: self._set_export_range_from_playhead("end"))
        full_btn.clicked.connect(self._reset_export_range_to_full)
        range_layout.addWidget(start_btn)
        range_layout.addWidget(end_btn)
        range_layout.addWidget(full_btn)

        for widget in (
            self.export_preset_combo,
            self.render_width_spin,
            self.render_height_spin,
            self.render_fps_spin,
            self.export_engine_combo,
            self.export_quality_combo,
            self.limit_export_range_checkbox,
            self.export_start_spin,
            self.export_end_spin,
        ):
            self._connect_setting_widget(widget)

        arrange_btn = QPushButton("Arrange export layout…")
        arrange_btn.clicked.connect(self.configure_export_layout)
        csv_btn = QPushButton("Export mapped data CSV…")
        csv_btn.clicked.connect(self.export_mapped_data_csv_dialog)
        batch_btn = QPushButton("Batch export…")
        batch_btn.clicked.connect(self.batch_export_dialog)
        queue_btn = QPushButton("Queue current render…")
        queue_btn.clicked.connect(self.queue_current_export_dialog)
        export_btn = QPushButton("Export MP4 render…")
        export_btn.clicked.connect(self.export_render_dialog)

        form.addRow("Preset", preset_row)
        form.addRow("Width", self.render_width_spin)
        form.addRow("Height", self.render_height_spin)
        form.addRow("FPS", self.render_fps_spin)
        form.addRow("Engine", self.export_engine_combo)
        form.addRow("Encoder quality", self.export_quality_combo)
        form.addRow(self.limit_export_range_checkbox)
        form.addRow("Start (s)", self.export_start_spin)
        form.addRow("End (s)", self.export_end_spin)
        form.addRow(range_row)
        form.addRow(arrange_btn)
        form.addRow(csv_btn)
        form.addRow(batch_btn)
        form.addRow(queue_btn)
        form.addRow(export_btn)
        return group

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Status")
        layout = QVBoxLayout(group)
        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        return group

    def _refresh_keyframe_list(self) -> None:
        if not hasattr(self, 'keyframe_list'):
            return
        self.keyframe_list.clear()
        self.camera_keyframes = deserialize_keyframes(serialize_keyframes(self.camera_keyframes))
        for keyframe in self.camera_keyframes:
            item = QListWidgetItem(f"{keyframe.time:0.02f}s • elev {keyframe.elevation:0.1f} • az {keyframe.azimuth:0.1f} • zoom {keyframe.zoom:0.2f}")
            item.setData(Qt.ItemDataRole.UserRole, keyframe.to_dict())
            self.keyframe_list.addItem(item)

    def _selected_keyframe_row(self) -> int:
        return int(self.keyframe_list.currentRow()) if hasattr(self, 'keyframe_list') else -1

    def _add_camera_keyframe_current(self) -> None:
        self.camera_keyframes.append(CameraKeyframe(time=float(self.current_time), elevation=float(self.elevation_spin.value()), azimuth=float(self._base_azimuth), zoom=float(self.zoom_spin.value())))
        self.camera_keyframes = deserialize_keyframes(serialize_keyframes(self.camera_keyframes))
        self._refresh_keyframe_list()
        self._schedule_autosave()
        self._clear_export_preview_session()
        self._set_status(f"Added camera keyframe at {self.current_time:0.02f}s.", color=ACCENT)

    def _update_selected_camera_keyframe(self) -> None:
        row = self._selected_keyframe_row()
        if row < 0 or row >= len(self.camera_keyframes):
            return
        self.camera_keyframes[row] = CameraKeyframe(time=float(self.current_time), elevation=float(self.elevation_spin.value()), azimuth=float(self._base_azimuth), zoom=float(self.zoom_spin.value()))
        self.camera_keyframes = deserialize_keyframes(serialize_keyframes(self.camera_keyframes))
        self._refresh_keyframe_list()
        self._schedule_autosave()
        self._clear_export_preview_session()

    def _remove_selected_camera_keyframe(self) -> None:
        row = self._selected_keyframe_row()
        if row < 0 or row >= len(self.camera_keyframes):
            return
        self.camera_keyframes.pop(row)
        self._refresh_keyframe_list()
        self._schedule_autosave()
        self._clear_export_preview_session()

    def _clear_camera_keyframes(self) -> None:
        self.camera_keyframes = []
        self._refresh_keyframe_list()
        self._schedule_autosave()
        self._clear_export_preview_session()

    def _jump_to_selected_keyframe(self, item=None) -> None:
        row = self._selected_keyframe_row()
        if row < 0 or row >= len(self.camera_keyframes):
            return
        keyframe = self.camera_keyframes[row]
        self.current_time = float(keyframe.time)
        self.elevation_spin.setValue(float(keyframe.elevation))
        self._base_azimuth = float(keyframe.azimuth)
        self.zoom_spin.setValue(float(keyframe.zoom))
        self.media_player.setPosition(int(round(self.current_time * 1000.0)))
        self._refresh_visuals(immediate=True)

    def _bake_orbit_keyframes(self) -> None:
        duration = max(0.01, self._current_duration_seconds())
        steps = [0.0, duration * 0.33, duration * 0.66, duration]
        base = float(self._base_azimuth)
        elev = float(self.elevation_spin.value())
        zoom = float(self.zoom_spin.value())
        azimuths = [base, base + 120.0, base + 240.0, base + 360.0]
        self.camera_keyframes = [CameraKeyframe(time=t, elevation=elev, azimuth=a, zoom=zoom) for t, a in zip(steps, azimuths)]
        self.use_keyframes_checkbox.setChecked(True)
        self._refresh_keyframe_list()
        self._schedule_autosave()
        self._clear_export_preview_session()
        self._set_status("Baked orbit camera keyframes from the current camera.", color=ACCENT)

    def _refresh_bookmark_list(self) -> None:
        if not hasattr(self, 'bookmark_list'):
            return
        self.bookmark_list.clear()
        self.bookmarks = deserialize_bookmarks(serialize_bookmarks(self.bookmarks))
        for bookmark in self.bookmarks:
            item = QListWidgetItem(f"{bookmark.time:0.02f}s • {bookmark.label}")
            item.setData(Qt.ItemDataRole.UserRole, bookmark.to_dict())
            self.bookmark_list.addItem(item)

    def _add_bookmark_current(self, label: str | None = None, point_index: int | None = None) -> None:
        if label is None:
            text, ok = QInputDialog.getText(self, APP_TITLE, 'Bookmark label:', text=f'Bookmark {len(self.bookmarks) + 1}')
            if not ok:
                return
            label = text.strip() or f'Bookmark {len(self.bookmarks) + 1}'
        self.bookmarks.append(Bookmark(time=float(self.current_time), label=str(label), point_index=point_index))
        self._refresh_bookmark_list()
        self._schedule_autosave()

    def _remove_selected_bookmark(self) -> None:
        row = self.bookmark_list.currentRow() if hasattr(self, 'bookmark_list') else -1
        if row < 0 or row >= len(self.bookmarks):
            return
        self.bookmarks.pop(row)
        self._refresh_bookmark_list()
        self._schedule_autosave()

    def _jump_to_selected_bookmark(self, item=None) -> None:
        row = self.bookmark_list.currentRow() if hasattr(self, 'bookmark_list') else -1
        if row < 0 or row >= len(self.bookmarks):
            return
        bookmark = self.bookmarks[row]
        self._audition_time_at(float(bookmark.time))

    def _refresh_region_list(self) -> None:
        if not hasattr(self, 'region_list'):
            return
        self.region_list.clear()
        self.regions = deserialize_regions(serialize_regions(self.regions))
        for region in self.regions:
            item = QListWidgetItem(f"{region.start:0.02f}s → {region.end:0.02f}s • {region.label}")
            item.setData(Qt.ItemDataRole.UserRole, region.to_dict())
            self.region_list.addItem(item)

    def _add_region_from_spins(self) -> None:
        start = float(self.region_start_spin.value())
        end = float(self.region_end_spin.value())
        if abs(end - start) < 1e-6:
            return
        text, ok = QInputDialog.getText(self, APP_TITLE, 'Region label:', text=f'Region {len(self.regions) + 1}')
        if not ok:
            return
        region = TimeRegion(start=start, end=end, label=text.strip() or f'Region {len(self.regions) + 1}').normalized()
        self.regions.append(region)
        self._refresh_region_list()
        self._schedule_autosave()

    def _remove_selected_region(self) -> None:
        row = self.region_list.currentRow() if hasattr(self, 'region_list') else -1
        if row < 0 or row >= len(self.regions):
            return
        self.regions.pop(row)
        self._refresh_region_list()
        self._schedule_autosave()

    def _jump_to_selected_region(self, item=None) -> None:
        row = self.region_list.currentRow() if hasattr(self, 'region_list') else -1
        if row < 0 or row >= len(self.regions):
            return
        region = self.regions[row]
        self.current_time = float(region.start)
        self.media_player.setPosition(int(round(self.current_time * 1000.0)))
        self._refresh_visuals(immediate=True)

    def _apply_selected_region_to_export(self) -> None:
        row = self.region_list.currentRow() if hasattr(self, 'region_list') else -1
        if row < 0 or row >= len(self.regions):
            return
        region = self.regions[row]
        self.export_start_spin.setValue(float(region.start))
        self.export_end_spin.setValue(float(region.end))
        self.limit_export_range_checkbox.setChecked(True)
        self._set_status(f"Using region '{region.label}' for export range.", color=ACCENT)

    def _refresh_export_queue_list(self) -> None:
        if not hasattr(self, 'export_queue_list'):
            return
        self.export_queue_list.clear()
        for job in self.export_queue_jobs:
            item = QListWidgetItem(job.display_label())
            item.setData(Qt.ItemDataRole.UserRole, job.to_dict())
            self.export_queue_list.addItem(item)

    def _make_queue_job_payload(self, output_path: str) -> ExportQueueJob:
        self._export_job_counter += 1
        label = self.project_title_edit.text().strip() or Path(self.file_path or output_path).stem
        state = self._collect_state(include_source=True)
        state['export_queue'] = {'jobs': [], 'job_counter': int(self._export_job_counter)}
        return ExportQueueJob(
            job_id=int(self._export_job_counter),
            label=label,
            source_path=str(self.file_path or ''),
            output_path=str(output_path),
            state=state,
        )

    def queue_current_export_dialog(self) -> None:
        if self.analysis is None or self.geometry_data is None or not self.file_path:
            QMessageBox.information(self, APP_TITLE, 'Analyze a file first.')
            return
        start_dir = str(Path(self.file_path).with_suffix('.mp4')) if self.file_path else str(Path.home() / 'metriq_visualizer_render.mp4')
        path, _ = QFileDialog.getSaveFileName(self, 'Queue export output', start_dir, 'MP4 video (*.mp4)')
        if not path:
            return
        job = self._make_queue_job_payload(path)
        self.export_queue_jobs.append(job)
        self._refresh_export_queue_list()
        self._schedule_autosave()
        self._set_status(f'Queued export #{job.job_id}.', color=ACCENT)

    def _remove_selected_queue_job(self) -> None:
        row = self.export_queue_list.currentRow() if hasattr(self, 'export_queue_list') else -1
        if row < 0 or row >= len(self.export_queue_jobs):
            return
        self.export_queue_jobs.pop(row)
        self._refresh_export_queue_list()
        self._schedule_autosave()

    def _clear_export_queue(self) -> None:
        self.export_queue_jobs = []
        self._refresh_export_queue_list()
        self._schedule_autosave()

    def _build_export_options_from_state(self, payload: dict, output_path: str):
        from metriq_visualizer_render import ExportOptions
        visual = payload.get('visual', {})
        motion = payload.get('motion', {})
        branding = payload.get('branding', {})
        export = payload.get('export', {})
        cinematics = payload.get('cinematics', {})
        layout = ExportLayoutSpec.from_dict(export.get('layout'))
        start_time = float(export.get('start_time', 0.0))
        end_time = float(export.get('end_time', 0.0)) if export.get('limit_range') else None
        return ExportOptions(
            output_path=str(output_path),
            width=int(export.get('width', self.render_width_spin.value())),
            height=int(export.get('height', self.render_height_spin.value())),
            fps=int(export.get('fps', self.render_fps_spin.value())),
            export_engine=str(export.get('engine', EXPORT_ENGINE_AUTO_LABEL)),
            export_quality=str(export.get('quality', EXPORT_QUALITY_BALANCED_LABEL)),
            layout=layout,
            base_alpha=float(visual.get('alpha', self.alpha_spin.value())),
            history_mode=str(visual.get('history_mode', self.history_combo.currentText())),
            point_lifespan=float(visual.get('point_lifespan', self.point_lifespan_spin.value())),
            fade_curve=float(visual.get('fade_curve', self.fade_curve_spin.value())),
            line_width=float(visual.get('line_width', self.line_width_spin.value())),
            path_curve_mode=str(visual.get('path_curve_mode', self.path_curve_mode_combo.currentText())),
            curve_detail=int(visual.get('curve_detail', self.curve_detail_spin.value())),
            connect_lines=bool(visual.get('connect_lines', self.connect_lines_checkbox.isChecked())),
            ghost_path=bool(visual.get('ghost_path', self.ghost_path_checkbox.isChecked())),
            flash_duration=float(visual.get('flash_duration', self.flash_duration_spin.value())),
            comet_duration=float(visual.get('comet_duration', self.comet_duration_spin.value())),
            elev=float(motion.get('elevation', self.elevation_spin.value())),
            azim=float(motion.get('base_azimuth', self._base_azimuth)),
            autorotate=bool(motion.get('autorotate', self.autorotate_checkbox.isChecked())),
            rotation_speed=float(motion.get('rotation_speed', self.rotation_speed_spin.value())),
            zoom=float(motion.get('zoom', self.zoom_spin.value())),
            point_size_scale=float(visual.get('point_size_scale', self.point_size_scale_spin.value())),
            render_mode=str(visual.get('render_mode', self.render_mode_combo.currentText())),
            tube_radius_scale=float(visual.get('tube_radius_scale', self.tube_radius_scale_spin.value())),
            tube_sides=int(visual.get('tube_sides', self.tube_sides_spin.value())),
            tube_follow_size=bool(visual.get('tube_follow_size', self.tube_follow_size_checkbox.isChecked())),
            tube_taper=float(visual.get('tube_taper', self.tube_taper_spin.value())),
            show_head_marker=bool(visual.get('show_head_marker', self.show_head_checkbox.isChecked())),
            head_size_scale=float(visual.get('head_size_scale', self.head_size_scale_spin.value())),
            halo_size_scale=float(visual.get('halo_size_scale', self.halo_size_scale_spin.value())),
            flash_size_scale=float(visual.get('flash_size_scale', self.flash_size_scale_spin.value())),
            show_axes=bool(visual.get('show_axes', self.show_axes_checkbox.isChecked())),
            show_axis_labels=bool(visual.get('show_axis_labels', self.show_axis_labels_checkbox.isChecked())),
            point_label_mode=str(visual.get('point_label_mode', self.point_label_mode_combo.currentText())),
            point_label_content=str(visual.get('point_label_content', self.point_label_content_combo.currentText())),
            max_point_labels=int(visual.get('max_point_labels', self.point_label_count_spin.value())),
            show_colorbar=bool(branding.get('show_export_colorbar', self.show_export_colorbar_checkbox.isChecked())),
            show_project_title=bool(branding.get('show_export_title', self.show_export_title_checkbox.isChecked())),
            project_title=str(branding.get('project_title', self.project_title_edit.text().strip() or APP_TITLE)),
            project_subtitle=str(branding.get('project_subtitle', self.project_subtitle_edit.text().strip())),
            show_watermark=bool(branding.get('show_export_watermark', self.show_export_watermark_checkbox.isChecked())),
            watermark_text=str(branding.get('watermark_text', self.watermark_edit.text().strip())),
            title=str(branding.get('project_title', self.project_title_edit.text().strip() or APP_TITLE)),
            start_time=float(start_time),
            end_time=None if end_time is None else float(end_time),
            camera_keyframes=list(cinematics.get('keyframes') or []) if bool(cinematics.get('use_keyframes', False)) else None,
            camera_easing=str(cinematics.get('easing', 'ease_in_out')),
        )

    def run_export_queue(self) -> None:
        if self._export_running or self._batch_running:
            QMessageBox.information(self, APP_TITLE, 'Another export job is already running.')
            return
        if self._queue_running:
            QMessageBox.information(self, APP_TITLE, 'Export queue is already running.')
            return
        if not self.export_queue_jobs:
            QMessageBox.information(self, APP_TITLE, 'No queued exports yet.')
            return
        self._queue_running = True
        self._set_status(f'Starting export queue ({len(self.export_queue_jobs)} job(s))…', color=WARNING)
        jobs = [ExportQueueJob.from_dict(job.to_dict()) for job in self.export_queue_jobs]

        def task() -> None:
            try:
                from metriq_visualizer_render import render_export_video
                report_lines: list[str] = []
                total = max(1, len(jobs))
                for index, job in enumerate(jobs, start=1):
                    try:
                        state = dict(job.state or {})
                        extraction = dict(state.get('extraction') or {})
                        mapping = dict(state.get('mapping') or {})
                        session = dict(state.get('session') or {})
                        source_path = str(session.get('file_path') or job.source_path)
                        self.worker_bridge.exportProgress.emit((index - 1) / total, f'Queue {index}/{total}: analyzing {Path(source_path).name}')
                        analysis = analyze_media(
                            source_path,
                            sample_rate=int(extraction.get('sample_rate', 22050)),
                            n_fft=int(extraction.get('n_fft', 2048)),
                            hop_length=int(extraction.get('hop_length', 256)),
                        )
                        geom = build_geometry(
                            analysis,
                            x_expression=str(mapping.get('x', 'pc1')),
                            y_expression=str(mapping.get('y', 'pc2')),
                            z_expression=str(mapping.get('z', 'pc3')),
                            color_expression=str(mapping.get('color', 'dominant_freq_hz')),
                            size_expression=str(mapping.get('size', 'rms')),
                            normalize_mode=str(mapping.get('normalize', 'zscore')),
                            max_points=int(extraction.get('max_points', 3500)),
                            low_volume_cutoff_db=float(extraction.get('low_volume_cutoff_db', 0.0)),
                            colormap=str(mapping.get('colormap', 'plasma')),
                        )
                        geom = self._apply_display_labels_to_geom(
                            geom,
                            formulas={
                                'x': str(mapping.get('x', 'pc1')),
                                'y': str(mapping.get('y', 'pc2')),
                                'z': str(mapping.get('z', 'pc3')),
                                'color': str(mapping.get('color', 'dominant_freq_hz')),
                                'size': str(mapping.get('size', 'rms')),
                            },
                            custom_labels={
                                'x': str(mapping.get('x_label', '')),
                                'y': str(mapping.get('y_label', '')),
                                'z': str(mapping.get('z_label', '')),
                                'color': str(mapping.get('color_label', '')),
                                'size': str(mapping.get('size_label', '')),
                            },
                        )
                        options = self._build_export_options_from_state(state, job.output_path)
                        render_export_video(
                            analysis,
                            geom,
                            options,
                            progress_callback=lambda progress, message, offset=index - 1: self.worker_bridge.exportProgress.emit((offset + progress) / total, message),
                        )
                        report_lines.append(f'OK  {job.display_label()}')
                    except Exception as exc:
                        report_lines.append(f'FAIL  {job.display_label()} -> {exc}')
                report_path = Path.home() / 'metriq_visualizer_export_queue_report.txt'
                report_path.write_text('\n'.join(report_lines) + ('\n' if report_lines else ''), encoding='utf-8')
                self.worker_bridge.batchFinished.emit(f'Export queue finished. Report: {report_path}')
            except Exception:
                self.worker_bridge.batchError.emit(traceback.format_exc())
            finally:
                self._queue_running = False

        threading.Thread(target=task, daemon=True).start()

    def _on_geometry_point_picked(self, payload: dict) -> None:
        time_value = float(payload.get('time', 0.0))
        idx = int(payload.get('index', 0))
        dominant_hz = float(payload.get('dominant_hz', 0.0)) if payload.get('dominant_hz') is not None else 0.0
        self.current_time = time_value
        self.media_player.setPosition(int(round(time_value * 1000.0)))
        self._refresh_visuals(immediate=True)
        self._set_status(f'Picked point #{idx} at {time_value:0.02f}s • {dominant_hz:0.1f} Hz', color=ACCENT)
        self._audition_time_at(time_value)

    def _make_spin(self, low: int, high: int, value: int, step: int = 1) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(step)
        widget.setValue(value)
        return widget

    def _make_double_spin(self, low: float, high: float, value: float, step: float = 0.1) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(low, high)
        widget.setDecimals(3 if step < 0.1 else 2)
        widget.setSingleStep(step)
        widget.setValue(value)
        return widget

    def _connect_setting_widget(self, widget: QWidget) -> None:
        if isinstance(widget, (QDoubleSpinBox, QSpinBox, QSlider)):
            widget.valueChanged.connect(self._schedule_autosave)
        elif isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(self._schedule_autosave)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(self._schedule_autosave)
        elif isinstance(widget, QLineEdit):
            widget.textChanged.connect(self._schedule_autosave)

    def _connect_visual_control(self, widget: QWidget) -> None:
        self._connect_setting_widget(widget)
        if isinstance(widget, (QDoubleSpinBox, QSpinBox, QSlider)):
            widget.valueChanged.connect(self._refresh_visuals)
        elif isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(self._refresh_visuals)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(self._refresh_visuals)

    def _connect_visual_behavior_tracking(self, widget: QWidget) -> None:
        if isinstance(widget, (QDoubleSpinBox, QSpinBox, QSlider)):
            widget.valueChanged.connect(self._sync_visualizer_behavior_preset_combo)
        elif isinstance(widget, QComboBox):
            widget.currentTextChanged.connect(self._sync_visualizer_behavior_preset_combo)
        elif isinstance(widget, QCheckBox):
            widget.toggled.connect(self._sync_visualizer_behavior_preset_combo)

    def _current_visualizer_behavior_state(self) -> dict:
        return {
            "history_mode": self.history_combo.currentText(),
            "point_lifespan": float(self.point_lifespan_spin.value()),
            "fade_curve": float(self.fade_curve_spin.value()),
            "line_width": float(self.line_width_spin.value()),
            "comet_duration": float(self.comet_duration_spin.value()),
            "flash_duration": float(self.flash_duration_spin.value()),
            "alpha": float(self.alpha_spin.value()),
            "point_size_scale": float(self.point_size_scale_spin.value()),
            "path_curve_mode": self.path_curve_mode_combo.currentText(),
            "curve_detail": int(self.curve_detail_spin.value()),
            "render_mode": self.render_mode_combo.currentText(),
            "tube_radius_scale": float(self.tube_radius_scale_spin.value()),
            "tube_sides": int(self.tube_sides_spin.value()),
            "tube_follow_size": bool(self.tube_follow_size_checkbox.isChecked()),
            "tube_taper": float(self.tube_taper_spin.value()),
            "connect_lines": bool(self.connect_lines_checkbox.isChecked()),
            "ghost_path": bool(self.ghost_path_checkbox.isChecked()),
            "show_head_marker": bool(self.show_head_checkbox.isChecked()),
            "head_size_scale": float(self.head_size_scale_spin.value()),
            "halo_size_scale": float(self.halo_size_scale_spin.value()),
            "flash_size_scale": float(self.flash_size_scale_spin.value()),
            "show_axes": bool(self.show_axes_checkbox.isChecked()),
            "show_axis_labels": bool(self.show_axis_labels_checkbox.isChecked()),
            "point_label_mode": self.point_label_mode_combo.currentText(),
            "point_label_content": self.point_label_content_combo.currentText(),
            "max_point_labels": int(self.point_label_count_spin.value()),
        }

    def _visualizer_behavior_presets_dir(self) -> Path:
        base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        path = base_dir / VISUALIZER_BEHAVIOR_PRESET_DIRECTORY_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _reload_visualizer_behavior_presets(self, announce: bool = False) -> None:
        preset_dir = self._visualizer_behavior_presets_dir()
        preset_map: dict[str, dict] = {}
        preset_paths: dict[str, str] = {}

        for preset_path in sorted(preset_dir.glob(f"*{PRESET_EXTENSION}"), key=lambda p: p.name.lower()):
            try:
                payload = load_preset(preset_path)
            except Exception:
                continue
            state = dict(payload.get("state") or {})
            visual = state.get("visual")
            if not isinstance(visual, dict):
                continue
            cleaned = {key: visual[key] for key in VISUALIZER_BEHAVIOR_STATE_KEYS if key in visual}
            if not cleaned:
                continue
            display_name = str(payload.get("preset_name") or preset_path.stem).strip() or preset_path.stem
            if display_name == VISUALIZER_BEHAVIOR_CUSTOM_LABEL or display_name in preset_map:
                base_name = preset_path.stem or "Preset"
                display_name = base_name
                suffix = 2
                while display_name == VISUALIZER_BEHAVIOR_CUSTOM_LABEL or display_name in preset_map:
                    display_name = f"{base_name} {suffix}"
                    suffix += 1
            preset_map[display_name] = cleaned
            preset_paths[display_name] = str(preset_path.resolve())

        self._visualizer_behavior_presets = preset_map
        self._visualizer_behavior_preset_paths = preset_paths

        if hasattr(self, "visual_behavior_preset_combo"):
            try:
                current_text = self.visual_behavior_preset_combo.currentText().strip() or VISUALIZER_BEHAVIOR_CUSTOM_LABEL
            except RuntimeError:
                current_text = VISUALIZER_BEHAVIOR_CUSTOM_LABEL
            blocker = QSignalBlocker(self.visual_behavior_preset_combo)
            self.visual_behavior_preset_combo.clear()
            self.visual_behavior_preset_combo.addItem(VISUALIZER_BEHAVIOR_CUSTOM_LABEL)
            for name in preset_map.keys():
                self.visual_behavior_preset_combo.addItem(name)
            if current_text in preset_map or current_text == VISUALIZER_BEHAVIOR_CUSTOM_LABEL:
                self.visual_behavior_preset_combo.setCurrentText(current_text)
            else:
                self.visual_behavior_preset_combo.setCurrentText(VISUALIZER_BEHAVIOR_CUSTOM_LABEL)
            del blocker

        if hasattr(self, "visual_behavior_apply_btn"):
            self.visual_behavior_apply_btn.setEnabled(bool(preset_map))

        if not self._applying_visual_behavior_preset and hasattr(self, "history_combo"):
            self._sync_visualizer_behavior_preset_combo()

        if announce:
            message = f"Loaded {len(preset_map)} visualizer preset file(s) from {preset_dir.name}/"
            self._set_status(message, color=ACCENT if preset_map else WARNING)

    def _visualizer_behavior_matches_preset(self, state: dict, preset: dict) -> bool:
        for key, expected in preset.items():
            current = state.get(key)
            if isinstance(expected, float):
                try:
                    if abs(float(current) - float(expected)) > 1e-6:
                        return False
                except Exception:
                    return False
            else:
                if current != expected:
                    return False
        return True

    def _set_visualizer_behavior_preset_combo(self, value: str) -> None:
        if not hasattr(self, "visual_behavior_preset_combo"):
            return
        resolved = value if self.visual_behavior_preset_combo.findText(value) >= 0 else VISUALIZER_BEHAVIOR_CUSTOM_LABEL
        blocker = QSignalBlocker(self.visual_behavior_preset_combo)
        self.visual_behavior_preset_combo.setCurrentText(resolved)
        del blocker

    def _sync_visualizer_behavior_preset_combo(self, *_args) -> None:
        if self._applying_visual_behavior_preset or not hasattr(self, "visual_behavior_preset_combo"):
            return
        state = self._current_visualizer_behavior_state()
        resolved = VISUALIZER_BEHAVIOR_CUSTOM_LABEL
        for name, preset in getattr(self, "_visualizer_behavior_presets", {}).items():
            if self._visualizer_behavior_matches_preset(state, preset):
                resolved = name
                break
        self._set_visualizer_behavior_preset_combo(resolved)

    def _apply_visualizer_behavior_preset(self, preset_name: str, set_status: bool = True) -> None:
        preset_name = str(preset_name)
        preset = getattr(self, "_visualizer_behavior_presets", {}).get(preset_name)
        if preset is None and preset_name and preset_name != VISUALIZER_BEHAVIOR_CUSTOM_LABEL:
            self._reload_visualizer_behavior_presets()
            preset = getattr(self, "_visualizer_behavior_presets", {}).get(preset_name)
        if preset is None:
            if set_status and preset_name and preset_name != VISUALIZER_BEHAVIOR_CUSTOM_LABEL:
                self._set_status(f"Preset not found in {self._visualizer_behavior_presets_dir().name}/: {preset_name}", color=WARNING)
            return
        controls = (
            self.history_combo,
            self.point_lifespan_spin,
            self.fade_curve_spin,
            self.line_width_spin,
            self.comet_duration_spin,
            self.flash_duration_spin,
            self.alpha_spin,
            self.point_size_scale_spin,
            self.path_curve_mode_combo,
            self.curve_detail_spin,
            self.render_mode_combo,
            self.tube_radius_scale_spin,
            self.tube_sides_spin,
            self.tube_follow_size_checkbox,
            self.tube_taper_spin,
            self.connect_lines_checkbox,
            self.ghost_path_checkbox,
            self.show_head_checkbox,
            self.head_size_scale_spin,
            self.halo_size_scale_spin,
            self.flash_size_scale_spin,
            self.show_axes_checkbox,
            self.show_axis_labels_checkbox,
            self.point_label_mode_combo,
            self.point_label_content_combo,
            self.point_label_count_spin,
        )
        self._applying_visual_behavior_preset = True
        blockers = [QSignalBlocker(widget) for widget in controls]
        try:
            self.history_combo.setCurrentText(str(preset.get("history_mode", self.history_combo.currentText())))
            self.point_lifespan_spin.setValue(float(preset.get("point_lifespan", self.point_lifespan_spin.value())))
            self.fade_curve_spin.setValue(float(preset.get("fade_curve", self.fade_curve_spin.value())))
            self.line_width_spin.setValue(float(preset.get("line_width", self.line_width_spin.value())))
            self.comet_duration_spin.setValue(float(preset.get("comet_duration", self.comet_duration_spin.value())))
            self.flash_duration_spin.setValue(float(preset.get("flash_duration", self.flash_duration_spin.value())))
            self.alpha_spin.setValue(float(preset.get("alpha", self.alpha_spin.value())))
            self.point_size_scale_spin.setValue(float(preset.get("point_size_scale", self.point_size_scale_spin.value())))
            path_curve_mode = str(preset.get("path_curve_mode", self.path_curve_mode_combo.currentText()))
            if self.path_curve_mode_combo.findText(path_curve_mode) >= 0:
                self.path_curve_mode_combo.setCurrentText(path_curve_mode)
            self.curve_detail_spin.setValue(int(preset.get("curve_detail", self.curve_detail_spin.value())))
            render_mode = str(preset.get("render_mode", self.render_mode_combo.currentText()))
            if self.render_mode_combo.findText(render_mode) >= 0:
                self.render_mode_combo.setCurrentText(render_mode)
            self.tube_radius_scale_spin.setValue(float(preset.get("tube_radius_scale", self.tube_radius_scale_spin.value())))
            self.tube_sides_spin.setValue(int(preset.get("tube_sides", self.tube_sides_spin.value())))
            self.tube_follow_size_checkbox.setChecked(bool(preset.get("tube_follow_size", self.tube_follow_size_checkbox.isChecked())))
            self.tube_taper_spin.setValue(float(preset.get("tube_taper", self.tube_taper_spin.value())))
            self.connect_lines_checkbox.setChecked(bool(preset.get("connect_lines", self.connect_lines_checkbox.isChecked())))
            self.ghost_path_checkbox.setChecked(bool(preset.get("ghost_path", self.ghost_path_checkbox.isChecked())))
            self.show_head_checkbox.setChecked(bool(preset.get("show_head_marker", self.show_head_checkbox.isChecked())))
            self.head_size_scale_spin.setValue(float(preset.get("head_size_scale", self.head_size_scale_spin.value())))
            self.halo_size_scale_spin.setValue(float(preset.get("halo_size_scale", self.halo_size_scale_spin.value())))
            self.flash_size_scale_spin.setValue(float(preset.get("flash_size_scale", self.flash_size_scale_spin.value())))
            self.show_axes_checkbox.setChecked(bool(preset.get("show_axes", self.show_axes_checkbox.isChecked())))
            self.show_axis_labels_checkbox.setChecked(bool(preset.get("show_axis_labels", self.show_axis_labels_checkbox.isChecked())))
            point_label_mode = str(preset.get("point_label_mode", self.point_label_mode_combo.currentText()))
            if self.point_label_mode_combo.findText(point_label_mode) >= 0:
                self.point_label_mode_combo.setCurrentText(point_label_mode)
            point_label_content = str(preset.get("point_label_content", self.point_label_content_combo.currentText()))
            if self.point_label_content_combo.findText(point_label_content) >= 0:
                self.point_label_content_combo.setCurrentText(point_label_content)
            self.point_label_count_spin.setValue(int(preset.get("max_point_labels", self.point_label_count_spin.value())))
        finally:
            blockers.clear()
            self._applying_visual_behavior_preset = False

        self._set_visualizer_behavior_preset_combo(str(preset_name))
        self._schedule_autosave()
        self._refresh_visuals(immediate=True)
        if set_status:
            self._set_status(f"Applied visualizer behavior preset: {preset_name}", color=ACCENT)

    def _on_low_volume_cutoff_changed(self, _value: float) -> None:
        self._clear_export_preview_session()
        if self._applying_state:
            return
        if self.analysis is not None:
            self._rebuild_geometry(reset_camera=False)

    def _apply_performance_preset(self, name: str, set_status: bool = True) -> None:
        preset = PERFORMANCE_PRESETS.get(str(name))
        if preset is None:
            return
        if getattr(self, "_applying_performance_preset", False):
            return
        self._applying_performance_preset = True
        try:
            self.performance_mode_combo.setCurrentText(str(name))
            self.live_redraw_fps_spin.setValue(int(preset["live_fps"]))
            self.live_point_budget_spin.setValue(int(preset["live_point_budget"]))
            self.preview_scale_spin.setValue(float(preset["preview_scale"]))
        finally:
            self._applying_performance_preset = False
        self._apply_frame_timer_settings()
        self._clear_export_preview_session()
        self._refresh_visuals()
        if set_status:
            self._set_status(f"Applied {name.lower()} live-performance profile.", color=ACCENT)

    def _apply_frame_timer_settings(self, *_args) -> None:
        fps = max(12, int(self.live_redraw_fps_spin.value())) if hasattr(self, "live_redraw_fps_spin") else 30
        interval_ms = max(12, int(round(1000.0 / float(fps))))
        self.frame_timer.setInterval(interval_ms)
        if self._visual_refresh_timer.isActive() and interval_ms < self._visual_refresh_timer.interval():
            self._visual_refresh_timer.start(interval_ms)

    def _current_live_point_budget(self) -> int:
        base = max(1, int(self.max_points_spin.value()))
        if not hasattr(self, "live_point_budget_spin"):
            return base
        budget = int(self.live_point_budget_spin.value())
        frame_count = int(self.geometry_data.times_full.size) if self.geometry_data is not None else 0
        if frame_count > 4800:
            scale = max(0.35, min(1.0, 4800.0 / float(frame_count)))
            budget = max(600, int(round(budget * scale)))
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState or self._dragging_timeline:
            return max(200, min(base, budget))
        return max(400, min(base, int(round(max(budget, base * 0.75))))) if frame_count > 0 else base

    def _target_visual_refresh_interval_ms(self) -> int:
        fps = max(12, int(self.live_redraw_fps_spin.value())) if hasattr(self, "live_redraw_fps_spin") else 30
        return max(12, int(round(1000.0 / float(fps))))

    def _target_analysis_playhead_interval_ms(self) -> int:
        mode = self.performance_mode_combo.currentText() if hasattr(self, "performance_mode_combo") else "Balanced"
        return {"Stable": 90, "Balanced": 60, "Quality": 40}.get(mode, 60)

    def _record_perf_stat(self, key: str, amount: int = 1) -> None:
        if key in self._perf_stats:
            self._perf_stats[key] += int(amount)

    def _maybe_flush_perf_log(self, *, force: bool = False) -> None:
        elapsed = time.perf_counter() - self._perf_window_started
        if not force and elapsed < PERF_LOG_INTERVAL_SECONDS:
            return
        snapshot = dict(self._perf_stats)
        if not force and not any(snapshot.values()):
            self._perf_window_started = time.perf_counter()
            return
        self.profile_store.append_perf_log(
            f"[{datetime.now().isoformat(timespec='seconds')}] {snapshot} over {elapsed:0.2f}s"
        )
        for key in self._perf_stats:
            self._perf_stats[key] = 0
        self._perf_window_started = time.perf_counter()

    def _refresh_visuals(self, *_args, immediate: bool = False) -> None:
        if self._closing:
            return
        self._record_perf_stat("visual_requests")
        if self._refresh_in_progress:
            self._refresh_pending = True
            self._record_perf_stat("visual_deferred")
            return
        delay_ms = 0
        if not immediate:
            interval_ms = self._target_visual_refresh_interval_ms()
            if self._last_visual_refresh_perf > 0.0:
                elapsed_ms = max(0.0, (time.perf_counter() - self._last_visual_refresh_perf) * 1000.0)
                delay_ms = max(0, int(round(interval_ms - elapsed_ms)))
            else:
                delay_ms = 0
        if immediate:
            if self._visual_refresh_timer.isActive():
                self._visual_refresh_timer.stop()
            self._visual_refresh_timer.start(0)
            return
        if self._visual_refresh_timer.isActive():
            self._record_perf_stat("visual_timer_collisions")
            return
        self._visual_refresh_timer.start(delay_ms)

    def _perform_visual_refresh(self) -> None:
        if self._closing:
            return
        if self._refresh_in_progress:
            self._refresh_pending = True
            self._record_perf_stat("visual_deferred")
            return
        self._refresh_in_progress = True
        started = time.perf_counter()
        try:
            use_live = self._using_live_visualizer()
            geom = self._live_geometry_data if use_live else self.geometry_data
            rgba = self._live_rgba_full if use_live else self._rgba_full
            if geom is None or rgba is None:
                self._update_transport_text()
                return
            params = self._current_view_params()
            if use_live:
                params = replace(params, current_time=float(self._live_current_time))
            head_idx = self.geometry_view.render_state(geom, rgba, params, base_azimuth=self._base_azimuth)
            self._last_head_idx = head_idx
            now_perf = time.perf_counter()
            is_playing = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and not self._dragging_timeline
            if (not use_live) and ((not is_playing) or ((now_perf - self._last_analysis_playhead_perf) * 1000.0 >= self._target_analysis_playhead_interval_ms())):
                self.analysis_tabs.set_playhead(self.current_time, force=not is_playing)
                self._last_analysis_playhead_perf = now_perf
                self._record_perf_stat("panel_updates")
            if not use_live:
                self._sync_timeline_range()
                if not self._dragging_timeline:
                    self._syncing_slider = True
                    self.timeline_slider.setValue(int(round(self.current_time * 1000.0)))
                    self._syncing_slider = False
            self._update_transport_text()
            self._record_perf_stat("visual_executed")
        except Exception as exc:
            self.profile_store.append_diagnostic_log(
                f"[{datetime.now().isoformat(timespec='seconds')}] render update failed: {exc}\n{traceback.format_exc()}"
            )
            self._set_status(f"Render update failed: {exc}", color=ERROR)
        finally:
            elapsed = time.perf_counter() - started
            self._last_visual_refresh_perf = time.perf_counter()
            self._refresh_in_progress = False
            if elapsed >= 0.08 and (time.perf_counter() - self._last_slow_render_log) >= 2.0:
                self._last_slow_render_log = time.perf_counter()
                clock_time = self._live_current_time if self._using_live_visualizer() else self.current_time
                self.profile_store.append_diagnostic_log(
                    f"[{datetime.now().isoformat(timespec='seconds')}] slow render frame: {elapsed:0.3f}s at t={clock_time:0.2f}s"
                )
            self._maybe_flush_perf_log()
            if self._refresh_pending and not self._closing:
                self._refresh_pending = False
                self._refresh_visuals()

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self.toggle_playback)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self.seek_relative(-0.25))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self.seek_relative(0.25))
        QShortcut(QKeySequence(Qt.Key.Key_Home), self, activated=self.reset_time)
        QShortcut(QKeySequence.StandardKey.Open, self, activated=self.open_file_dialog)

    def _refresh_live_device_list(self) -> None:
        if not hasattr(self, "live_device_combo"):
            return
        devices = available_input_devices()
        current_text = self.live_device_combo.currentText()
        self.live_device_combo.blockSignals(True)
        self.live_device_combo.clear()
        self.live_device_combo.addItem("Default input", None)
        for item in devices:
            label = str(item.get("name", "Input"))
            samplerate = float(item.get("default_samplerate", 0.0))
            if samplerate > 0.0:
                label = f"{label} ({samplerate:0.0f} Hz)"
            self.live_device_combo.addItem(label, item.get("index"))
        if current_text:
            idx = self.live_device_combo.findText(current_text)
            if idx >= 0:
                self.live_device_combo.setCurrentIndex(idx)
        self.live_device_combo.blockSignals(False)

    def _selected_live_device(self):
        if not hasattr(self, "live_device_combo"):
            return None
        return self.live_device_combo.currentData()

    def _set_live_status_badge(self, text: str, color: str | None = None) -> None:
        color = color or MUTED_TEXT
        if hasattr(self, "live_monitor_status_label"):
            self.live_monitor_status_label.setText(text)
            self.live_monitor_status_label.setStyleSheet(f"color: {color}; font-weight: 700;")

    def _start_live_monitor(self) -> None:
        try:
            baseline = self._live_saved_baseline_model
            self.live_monitor.stop()
        except Exception:
            pass
        self.live_monitor = LiveDiagnosticsEngine(
            sample_rate=int(self.sample_rate_spin.value()),
            frame_size=max(256, int(self.n_fft_spin.value())),
            hop_length=max(64, int(self.hop_length_spin.value())),
            history_seconds=float(self.live_history_spin.value()),
        )
        self.live_monitor.configure(
            gate_db=float(self.live_monitor_gate_spin.value()),
            warn_threshold=float(self.live_warn_threshold_spin.value()),
            alarm_threshold=float(self.live_alarm_threshold_spin.value()),
            min_event_seconds=float(self.live_min_event_spin.value()),
        )
        if baseline is not None:
            self.live_monitor.set_baseline(baseline)
        try:
            self.live_monitor.start(self._selected_live_device())
        except Exception as exc:
            self._set_live_status_badge("Input error", ERROR)
            self.profile_store.append_diagnostic_log(
                f"[{datetime.now().isoformat(timespec='seconds')}] live input start failed: {exc}"
            )
            QMessageBox.warning(self, APP_TITLE, str(exc))
            return
        self.live_start_button.setEnabled(False)
        self.live_stop_button.setEnabled(True)
        self._diagnostics_timer.start()
        self._set_live_status_badge("Listening", ACCENT)
        self._set_status("Live diagnostics input started.", color=ACCENT)
        self._schedule_autosave()

    def _stop_live_monitor(self) -> None:
        self._diagnostics_timer.stop()
        try:
            self.live_monitor.stop()
        except Exception:
            pass
        self.live_start_button.setEnabled(True)
        self.live_stop_button.setEnabled(False)
        self._set_live_status_badge("Stopped", MUTED_TEXT)
        self._set_status("Live diagnostics input stopped.", color=MUTED_TEXT)
        self._live_snapshot = None
        self._live_analysis = None
        self._live_geometry_data = None
        self._live_rgba_full = None
        self.diagnostics_dashboard.clear()
        if self.geometry_data is not None and self._rgba_full is not None:
            self.geometry_view.set_scene_geometry(self.geometry_data, self._rgba_full, analysis=self.analysis, reset_camera=False)
            self._refresh_visuals(immediate=True)
        self._schedule_autosave()

    def _on_live_settings_changed(self, *_args) -> None:
        if self.live_monitor.is_running:
            self.live_monitor.configure(
                history_seconds=float(self.live_history_spin.value()),
                gate_db=float(self.live_monitor_gate_spin.value()),
                warn_threshold=float(self.live_warn_threshold_spin.value()),
                alarm_threshold=float(self.live_alarm_threshold_spin.value()),
                min_event_seconds=float(self.live_min_event_spin.value()),
            )
        self.diagnostics_dashboard.set_snapshot(self._live_snapshot, float(self.live_warn_threshold_spin.value()), float(self.live_alarm_threshold_spin.value()))
        self._schedule_autosave()

    def _capture_live_baseline(self) -> None:
        try:
            model = self.live_monitor.capture_baseline(float(self.live_baseline_window_spin.value()))
        except Exception as exc:
            QMessageBox.information(self, APP_TITLE, str(exc))
            return
        self._live_saved_baseline_model = model
        self.live_monitor_baseline_label.setText(f"Baseline {model.window_seconds:0.0f}s")
        self._set_live_status_badge("Baseline ready", ACCENT)
        self._schedule_autosave()
        self._poll_live_diagnostics()

    def _clear_live_baseline(self) -> None:
        self._live_saved_baseline_model = None
        self.live_monitor.clear_baseline()
        self.live_monitor_baseline_label.setText("No baseline")
        self._schedule_autosave()
        self._poll_live_diagnostics()

    def _clear_live_events(self) -> None:
        self.live_monitor.clear_events()
        self.live_event_list.clear()
        self._live_recent_event_count = 0

    def _refresh_live_event_list(self, snapshot: dict | None) -> None:
        events = list((snapshot or {}).get("events") or [])
        if len(events) == self._live_recent_event_count:
            return
        self._live_recent_event_count = len(events)
        self.live_event_list.clear()
        for payload in reversed(events[-64:]):
            label = (
                f"{str(payload.get('severity', 'warning')).title()} • "
                f"{float(payload.get('start_time', 0.0)):0.1f}s–{float(payload.get('end_time', 0.0)):0.1f}s • "
                f"peak {float(payload.get('peak_score', 0.0)):0.2f}"
            )
            item = QListWidgetItem(label)
            severity = str(payload.get('severity', 'warning'))
            item.setForeground(pg.mkColor(ERROR if severity == 'alarm' else WARNING))
            self.live_event_list.addItem(item)

    def _using_live_visualizer(self) -> bool:
        return bool(
            hasattr(self, "live_drive_visualizer_checkbox")
            and self.live_drive_visualizer_checkbox.isChecked()
            and self.live_monitor.is_running
            and self._live_geometry_data is not None
            and self._live_rgba_full is not None
        )

    def _on_live_drive_visualizer_toggled(self, enabled: bool) -> None:
        if enabled and self.live_monitor.is_running:
            self._poll_live_diagnostics()
            self._refresh_visuals(immediate=True)
            return
        if not enabled and self.geometry_data is not None and self._rgba_full is not None:
            self.geometry_view.set_scene_geometry(self.geometry_data, self._rgba_full, analysis=self.analysis, reset_camera=False)
            self._refresh_visuals(immediate=True)

    def _poll_live_diagnostics(self) -> None:
        if not self.live_monitor.is_running:
            return
        snapshot = self.live_monitor.snapshot(max_points=max(256, int(self.live_history_spin.value() * 20.0)))
        self._live_snapshot = snapshot
        self._live_current_time = float(snapshot.get("current_time", self._live_current_time))
        current_status = str(snapshot.get("current_status", snapshot.get("status", "Idle")))
        current_score = float(snapshot.get("current_score", 0.0))
        color = MUTED_TEXT
        if current_status == "Alarm":
            color = ERROR
        elif current_status == "Warning":
            color = WARNING
        elif current_status in {"Normal", "Listening"}:
            color = ACCENT
        elif current_status == "Uncalibrated":
            color = "#7fd4ff"
        self._set_live_status_badge(current_status, color)
        self.live_monitor_score_label.setText(f"Score {current_score:0.2f}")
        self.live_monitor_baseline_label.setText("Baseline ready" if snapshot.get("baseline") else "No baseline")
        self.diagnostics_dashboard.set_snapshot(snapshot, float(self.live_warn_threshold_spin.value()), float(self.live_alarm_threshold_spin.value()))
        self._refresh_live_event_list(snapshot)

        if self.live_drive_visualizer_checkbox.isChecked():
            converted = snapshot_to_analysis_geometry(
                snapshot,
                sample_rate=int(self.sample_rate_spin.value()),
                max_points=int(self._current_live_point_budget()),
                colormap=self.colormap_combo.currentText(),
            )
            if converted is not None:
                self._live_analysis, self._live_geometry_data = converted
                _, rgba_full, _ = prepare_color_mapping(self._live_geometry_data.color_full, self._live_geometry_data.colormap)
                self._live_rgba_full = np.asarray(rgba_full, dtype=np.float32)
                self.geometry_view.set_scene_geometry(self._live_geometry_data, self._live_rgba_full, analysis=self._live_analysis, reset_camera=False)
                self._refresh_visuals()
        self._update_transport_text()

    # ---------- State + helpers ----------
    def _current_mapping_formulas(self) -> dict[str, str]:
        return {
            "x": self.x_edit.text().strip(),
            "y": self.y_edit.text().strip(),
            "z": self.z_edit.text().strip(),
            "color": self.color_edit.text().strip(),
            "size": self.size_edit.text().strip(),
        }

    def _current_custom_display_labels(self) -> dict[str, str]:
        return {
            "x": self.x_label_edit.text().strip(),
            "y": self.y_label_edit.text().strip(),
            "z": self.z_label_edit.text().strip(),
            "color": self.color_label_edit.text().strip(),
            "size": self.size_label_edit.text().strip(),
        }

    def _friendly_labels_for_mapping(self, formulas: dict[str, str]) -> dict[str, str]:
        normalized = {key: str(value).strip() for key, value in formulas.items()}
        for preset_name, preset in DEFAULT_PRESETS.items():
            if all(normalized.get(axis, "") == str(preset.get(axis, "")).strip() for axis in ("x", "y", "z", "color", "size")):
                preset_labels = DEFAULT_PRESET_DISPLAY_LABELS.get(preset_name)
                if preset_labels:
                    return dict(preset_labels)
        friendly: dict[str, str] = {}
        for axis, expr in normalized.items():
            label = FEATURE_FRIENDLY_LABELS.get(expr)
            if label:
                friendly[axis] = label
        return friendly

    def _resolved_display_labels(self, formulas: dict[str, str] | None = None, custom_labels: dict[str, str] | None = None) -> dict[str, str]:
        formulas = dict(formulas or self._current_mapping_formulas())
        custom_labels = dict(custom_labels or self._current_custom_display_labels())
        friendly = self._friendly_labels_for_mapping(formulas)
        resolved: dict[str, str] = {}
        for axis in ("x", "y", "z", "color", "size"):
            custom_value = str(custom_labels.get(axis, "") or "").strip()
            fallback = str(friendly.get(axis, "") or formulas.get(axis, "") or "0").strip() or "0"
            resolved[axis] = custom_value or fallback
        return resolved

    def _apply_display_labels_to_geom(
        self,
        geom: GeometryResult,
        formulas: dict[str, str] | None = None,
        custom_labels: dict[str, str] | None = None,
    ) -> GeometryResult:
        geom.labels = self._resolved_display_labels(formulas=formulas, custom_labels=custom_labels)
        return geom

    def _set_display_label_fields(self, labels: dict[str, str] | None) -> None:
        labels = labels or {}
        self.x_label_edit.setText(str(labels.get("x", "")))
        self.y_label_edit.setText(str(labels.get("y", "")))
        self.z_label_edit.setText(str(labels.get("z", "")))
        self.color_label_edit.setText(str(labels.get("color", "")))
        self.size_label_edit.setText(str(labels.get("size", "")))

    def _apply_display_labels_to_current_geometry(self) -> None:
        if self._applying_state:
            return
        self._clear_export_preview_session()
        if self.geometry_data is None or self.analysis is None:
            self._schedule_autosave()
            return
        self._apply_display_labels_to_geom(self.geometry_data)
        self.analysis_tabs.set_data(self.analysis, self.geometry_data)
        try:
            self.geometry_view._last_axis_label_visibility = None
        except Exception:
            pass
        self._refresh_visuals(immediate=True)
        self._force_live_viewport_refresh(burst=True)
        self._set_status("Display labels updated.", color=ACCENT)

    def _load_preset(self, preset_name: str) -> None:
        preset = DEFAULT_PRESETS.get(preset_name)
        if not preset:
            return
        self.preset_combo.setCurrentText(preset_name)
        self.x_edit.setText(preset["x"])
        self.y_edit.setText(preset["y"])
        self.z_edit.setText(preset["z"])
        self.color_edit.setText(preset["color"])
        self.size_edit.setText(preset["size"])
        self._set_display_label_fields(DEFAULT_PRESET_DISPLAY_LABELS.get(preset_name, {}))
        if self.analysis is not None:
            self._rebuild_geometry(reset_camera=False)

    def _apply_export_preset_by_title(self, title: str) -> None:
        key = EXPORT_PRESET_TITLE_TO_KEY.get(title)
        if key:
            self._apply_export_preset(key)

    def _apply_export_preset(self, preset_key: str, set_status: bool = True) -> None:
        preset = EXPORT_PRESET_MAP.get(str(preset_key))
        if preset is None:
            return
        self._active_export_preset_key = preset.key
        if hasattr(self, "export_preset_combo") and self.export_preset_combo.findText(preset.title) >= 0 and self.export_preset_combo.currentText() != preset.title:
            self.export_preset_combo.blockSignals(True)
            self.export_preset_combo.setCurrentText(preset.title)
            self.export_preset_combo.blockSignals(False)
        if hasattr(self, "render_width_spin"):
            self.render_width_spin.setValue(int(preset.width))
            self.render_height_spin.setValue(int(preset.height))
            self.render_fps_spin.setValue(int(preset.fps))
        self.export_layout_spec = preset.layout_factory().clone().clamp()
        self._clear_export_preview_session()
        self._schedule_autosave()
        if set_status:
            self._set_status(f"Applied export preset: {preset.title}", color=ACCENT)

    def _beat_ui_heartbeat(self) -> None:
        try:
            self._freeze_watchdog.beat()
        except Exception:
            pass

    def _stop_click_audition(self) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()

    def _audition_time_at(self, seconds: float) -> None:
        if not self.file_path:
            return
        duration = self._current_duration_seconds()
        target = max(0.0, min(float(seconds), duration if duration > 0 else float(seconds)))
        was_playing = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.current_time = target
        self.media_player.setPosition(int(round(target * 1000.0)))
        self._refresh_visuals()
        self.media_player.play()
        if was_playing:
            self._audition_stop_timer.stop()
            self._set_status(f"Jumped to {target:0.02f}s.", color=ACCENT)
            return
        self._audition_stop_timer.start(850)
        self._set_status(f"Auditioning from {target:0.02f}s (click-to-hear).", color=ACCENT)

    def _set_status(self, text: str, color: str = MUTED_TEXT) -> None:
        self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.setText(text)
        self.statusBar().showMessage(text)

    def _mark_dirty(self, reason: str = "ui") -> None:
        self._project_dirty = True
        self.profile_store.mark_runtime_dirty(True, reason=str(reason), updated_at=datetime.now().isoformat(timespec="seconds"))

    def _mark_clean(self, reason: str = "saved") -> None:
        self._project_dirty = False
        self.profile_store.mark_runtime_dirty(False, reason=str(reason), updated_at=datetime.now().isoformat(timespec="seconds"), clean_exit=True)

    def _source_filter_string(self) -> str:
        return "Supported files (*.mp4 *.mov *.avi *.mkv *.webm *.wav *.mp3 *.flac *.ogg *.m4a *.aac *.wma *.opus *.csv *.tsv *.txt *.xlsx);;Media files (*.mp4 *.mov *.avi *.mkv *.webm *.wav *.mp3 *.flac *.ogg *.m4a *.aac *.wma *.opus);;Tabular files (*.csv *.tsv *.txt *.xlsx);;All files (*)"

    def _maybe_offer_recovery_restore(self) -> bool:
        runtime_state = self.profile_store.load_runtime_state()
        recovery_payload = self.profile_store.load_recovery_session()
        if not runtime_state.get("dirty") or not recovery_payload:
            return False
        state = dict(recovery_payload.get("state") or recovery_payload)
        if not state:
            return False
        response = QMessageBox.question(
            self,
            APP_TITLE,
            "A previous session did not close cleanly. Restore the most recent autosaved recovery snapshot?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if response == QMessageBox.StandardButton.Yes:
            self._apply_state(state, restore_file=True)
            self._set_status("Recovered autosaved session after an unclean exit.", color=ACCENT)
            return True
        self.profile_store.clear_recovery_session()
        self._mark_clean(reason="recovery_discarded")
        return False

    def _schedule_autosave(self, *_args) -> None:
        if self._closing or self._applying_state:
            return
        self._mark_dirty(reason="autosave")
        self._autosave_timer.start()

    def _collect_state(self, include_source: bool = False) -> dict:
        payload = {
            "schema_version": 84,
            "restore_last_session_enabled": (
                bool(self.restore_last_session_checkbox.isChecked())
                if hasattr(self, "restore_last_session_checkbox")
                else bool(getattr(self, "_restore_last_session_enabled", True))
            ),
            "active_profile_name": self._active_profile_name,
            "extraction": {
                "sample_rate": int(self.sample_rate_spin.value()),
                "n_fft": int(self.n_fft_spin.value()),
                "hop_length": int(self.hop_length_spin.value()),
                "max_points": int(self.max_points_spin.value()),
                "low_volume_cutoff_db": float(self.low_volume_cutoff_spin.value()),
            },
            "mapping": {
                "preset_name": self.preset_combo.currentText(),
                "x": self.x_edit.text(),
                "y": self.y_edit.text(),
                "z": self.z_edit.text(),
                "color": self.color_edit.text(),
                "size": self.size_edit.text(),
                "x_label": self.x_label_edit.text(),
                "y_label": self.y_label_edit.text(),
                "z_label": self.z_label_edit.text(),
                "color_label": self.color_label_edit.text(),
                "size_label": self.size_label_edit.text(),
                "normalize": self.normalize_combo.currentText(),
                "colormap": self.colormap_combo.currentText(),
            },
            "performance": {
                "mode": self.performance_mode_combo.currentText(),
                "live_redraw_fps": int(self.live_redraw_fps_spin.value()),
                "live_point_budget": int(self.live_point_budget_spin.value()),
                "preview_scale": float(self.preview_scale_spin.value()),
            },
            "visual": {
                "history_mode": self.history_combo.currentText(),
                "point_lifespan": float(self.point_lifespan_spin.value()),
                "fade_curve": float(self.fade_curve_spin.value()),
                "line_width": float(self.line_width_spin.value()),
                "path_curve_mode": self.path_curve_mode_combo.currentText(),
                "curve_detail": int(self.curve_detail_spin.value()),
                "comet_duration": float(self.comet_duration_spin.value()),
                "flash_duration": float(self.flash_duration_spin.value()),
                "alpha": float(self.alpha_spin.value()),
                "point_size_scale": float(self.point_size_scale_spin.value()),
                "render_mode": self.render_mode_combo.currentText(),
                "tube_radius_scale": float(self.tube_radius_scale_spin.value()),
                "tube_sides": int(self.tube_sides_spin.value()),
                "tube_follow_size": bool(self.tube_follow_size_checkbox.isChecked()),
                "tube_taper": float(self.tube_taper_spin.value()),
                "connect_lines": bool(self.connect_lines_checkbox.isChecked()),
                "ghost_path": bool(self.ghost_path_checkbox.isChecked()),
                "show_head_marker": bool(self.show_head_checkbox.isChecked()),
                "head_size_scale": float(self.head_size_scale_spin.value()),
                "halo_size_scale": float(self.halo_size_scale_spin.value()),
                "flash_size_scale": float(self.flash_size_scale_spin.value()),
                "show_axes": bool(self.show_axes_checkbox.isChecked()),
                "show_axis_labels": bool(self.show_axis_labels_checkbox.isChecked()),
                "point_label_mode": self.point_label_mode_combo.currentText(),
                "point_label_content": self.point_label_content_combo.currentText(),
                "max_point_labels": int(self.point_label_count_spin.value()),
            },
            "motion": {
                "autorotate": bool(self.autorotate_checkbox.isChecked()),
                "rotation_speed": float(self.rotation_speed_spin.value()),
                "elevation": float(self.elevation_spin.value()),
                "zoom": float(self.zoom_spin.value()),
                "base_azimuth": float(self._base_azimuth),
            },
            "ui": {
                "show_preview": bool(self.show_preview_checkbox.isChecked()),
                "show_panels": bool(self.show_panels_checkbox.isChecked()),
            },
            "branding": {
                "project_title": self.project_title_edit.text(),
                "project_subtitle": self.project_subtitle_edit.text(),
                "watermark_text": self.watermark_edit.text(),
                "show_export_title": bool(self.show_export_title_checkbox.isChecked()),
                "show_export_watermark": bool(self.show_export_watermark_checkbox.isChecked()),
                "show_export_colorbar": bool(self.show_export_colorbar_checkbox.isChecked()),
            },
            "export": {
                "preset_key": self._active_export_preset_key,
                "width": int(self.render_width_spin.value()),
                "height": int(self.render_height_spin.value()),
                "fps": int(self.render_fps_spin.value()),
                "engine": self.export_engine_combo.currentText() if hasattr(self, "export_engine_combo") else EXPORT_ENGINE_AUTO_LABEL,
                "quality": self.export_quality_combo.currentText() if hasattr(self, "export_quality_combo") else EXPORT_QUALITY_BALANCED_LABEL,
                "layout": self.export_layout_spec.clone().clamp().to_dict(),
                "limit_range": bool(self.limit_export_range_checkbox.isChecked()),
                "start_time": float(self.export_start_spin.value()),
                "end_time": float(self.export_end_spin.value()),
            },
            "cinematics": {
                "use_keyframes": bool(self.use_keyframes_checkbox.isChecked()) if hasattr(self, 'use_keyframes_checkbox') else False,
                "easing": self.keyframe_easing_combo.currentText() if hasattr(self, 'keyframe_easing_combo') else 'ease_in_out',
                "keyframes": serialize_keyframes(self.camera_keyframes),
            },
            "annotations": {
                "bookmarks": serialize_bookmarks(self.bookmarks),
                "regions": serialize_regions(self.regions),
            },
            "export_queue": {
                "jobs": serialize_queue(self.export_queue_jobs),
                "job_counter": int(self._export_job_counter),
            },
            "diagnostics": {
                "drive_visualizer": bool(self.live_drive_visualizer_checkbox.isChecked()) if hasattr(self, "live_drive_visualizer_checkbox") else True,
                "history_seconds": float(self.live_history_spin.value()) if hasattr(self, "live_history_spin") else 30.0,
                "baseline_window_seconds": float(self.live_baseline_window_spin.value()) if hasattr(self, "live_baseline_window_spin") else 8.0,
                "gate_db": float(self.live_monitor_gate_spin.value()) if hasattr(self, "live_monitor_gate_spin") else 15.0,
                "warn_threshold": float(self.live_warn_threshold_spin.value()) if hasattr(self, "live_warn_threshold_spin") else 2.0,
                "alarm_threshold": float(self.live_alarm_threshold_spin.value()) if hasattr(self, "live_alarm_threshold_spin") else 4.0,
                "min_event_seconds": float(self.live_min_event_spin.value()) if hasattr(self, "live_min_event_spin") else 0.55,
                "device_label": self.live_device_combo.currentText() if hasattr(self, "live_device_combo") else "",
                "baseline_model": serialize_baseline_model(self._live_saved_baseline_model),
            },
        }
        if include_source:
            payload["session"] = {
                "file_path": self.file_path,
                "current_time": float(self.current_time),
            }
        if self.project_path:
            payload["project_path"] = self.project_path
        return payload

    def _collect_preset_state(self) -> dict:
        state = self._collect_state(include_source=False)
        allowed = (
            "schema_version",
            "extraction",
            "mapping",
            "performance",
            "visual",
            "motion",
            "ui",
            "export",
        )
        return {key: state[key] for key in allowed if key in state}

    def _apply_loaded_preset_state(self, payload: dict) -> None:
        state = dict(payload.get("state") or {})
        self._apply_state(state, restore_file=False)
        self.preset_path = str(payload.get("preset_path") or self.preset_path or "") or None
        self._recent_presets = self.profile_store.append_recent_preset(self.preset_path) if self.preset_path else self._recent_presets
        self._refresh_recent_presets_menu()
        if self.analysis is not None:
            self._rebuild_geometry(reset_camera=False)
        else:
            self._refresh_visuals(immediate=True)
        self._mark_dirty(reason="preset_loaded")

    def _apply_state(self, payload: dict, restore_file: bool = False) -> None:
        extraction = payload.get("extraction", {})
        mapping = payload.get("mapping", {})
        performance = payload.get("performance", {})
        visual = payload.get("visual", {})
        motion = payload.get("motion", {})
        ui_state = payload.get("ui", {})
        branding = payload.get("branding", {})
        export = payload.get("export", {})
        cinematics = payload.get("cinematics", {})
        annotations = payload.get("annotations", {})
        export_queue = payload.get("export_queue", {})
        diagnostics = payload.get("diagnostics", {})

        self._applying_state = True
        try:
            self.sample_rate_spin.setValue(int(extraction.get("sample_rate", self.sample_rate_spin.value())))
            self.n_fft_spin.setValue(int(extraction.get("n_fft", self.n_fft_spin.value())))
            self.hop_length_spin.setValue(int(extraction.get("hop_length", self.hop_length_spin.value())))
            self.max_points_spin.setValue(int(extraction.get("max_points", self.max_points_spin.value())))
            self.low_volume_cutoff_spin.setValue(float(extraction.get("low_volume_cutoff_db", self.low_volume_cutoff_spin.value())))

            preset_name = str(mapping.get("preset_name", self.preset_combo.currentText()))
            if preset_name and self.preset_combo.findText(preset_name) >= 0:
                self.preset_combo.setCurrentText(preset_name)

            perf_mode = str(performance.get("mode", self.performance_mode_combo.currentText()))
            if self.performance_mode_combo.findText(perf_mode) >= 0:
                self.performance_mode_combo.setCurrentText(perf_mode)
            self.live_redraw_fps_spin.setValue(int(performance.get("live_redraw_fps", self.live_redraw_fps_spin.value())))
            self.live_point_budget_spin.setValue(int(performance.get("live_point_budget", self.live_point_budget_spin.value())))
            self.preview_scale_spin.setValue(float(performance.get("preview_scale", self.preview_scale_spin.value())))
            self._apply_frame_timer_settings()

            self.x_edit.setText(str(mapping.get("x", self.x_edit.text())))
            self.y_edit.setText(str(mapping.get("y", self.y_edit.text())))
            self.z_edit.setText(str(mapping.get("z", self.z_edit.text())))
            self.color_edit.setText(str(mapping.get("color", self.color_edit.text())))
            self.size_edit.setText(str(mapping.get("size", self.size_edit.text())))
            self.x_label_edit.setText(str(mapping.get("x_label", "")))
            self.y_label_edit.setText(str(mapping.get("y_label", "")))
            self.z_label_edit.setText(str(mapping.get("z_label", "")))
            self.color_label_edit.setText(str(mapping.get("color_label", "")))
            self.size_label_edit.setText(str(mapping.get("size_label", "")))
            normalize = str(mapping.get("normalize", self.normalize_combo.currentText()))
            if self.normalize_combo.findText(normalize) >= 0:
                self.normalize_combo.setCurrentText(normalize)
            colormap = str(mapping.get("colormap", self.colormap_combo.currentText()))
            if self.colormap_combo.findText(colormap) >= 0:
                self.colormap_combo.setCurrentText(colormap)

            self.history_combo.setCurrentText(str(visual.get("history_mode", self.history_combo.currentText())))
            self.point_lifespan_spin.setValue(float(visual.get("point_lifespan", self.point_lifespan_spin.value())))
            self.fade_curve_spin.setValue(float(visual.get("fade_curve", self.fade_curve_spin.value())))
            self.line_width_spin.setValue(float(visual.get("line_width", self.line_width_spin.value())))
            curve_mode = str(visual.get("path_curve_mode", self.path_curve_mode_combo.currentText()))
            if self.path_curve_mode_combo.findText(curve_mode) >= 0:
                self.path_curve_mode_combo.setCurrentText(curve_mode)
            self.curve_detail_spin.setValue(int(visual.get("curve_detail", self.curve_detail_spin.value())))
            self.comet_duration_spin.setValue(float(visual.get("comet_duration", self.comet_duration_spin.value())))
            self.flash_duration_spin.setValue(float(visual.get("flash_duration", self.flash_duration_spin.value())))
            self.alpha_spin.setValue(float(visual.get("alpha", self.alpha_spin.value())))
            self.point_size_scale_spin.setValue(float(visual.get("point_size_scale", self.point_size_scale_spin.value())))
            render_mode = str(visual.get("render_mode", self.render_mode_combo.currentText()))
            if self.render_mode_combo.findText(render_mode) >= 0:
                self.render_mode_combo.setCurrentText(render_mode)
            self.tube_radius_scale_spin.setValue(float(visual.get("tube_radius_scale", self.tube_radius_scale_spin.value())))
            self.tube_sides_spin.setValue(int(visual.get("tube_sides", self.tube_sides_spin.value())))
            self.tube_follow_size_checkbox.setChecked(bool(visual.get("tube_follow_size", self.tube_follow_size_checkbox.isChecked())))
            self.tube_taper_spin.setValue(float(visual.get("tube_taper", self.tube_taper_spin.value())))
            self.connect_lines_checkbox.setChecked(bool(visual.get("connect_lines", self.connect_lines_checkbox.isChecked())))
            self.ghost_path_checkbox.setChecked(bool(visual.get("ghost_path", self.ghost_path_checkbox.isChecked())))
            self.show_head_checkbox.setChecked(bool(visual.get("show_head_marker", self.show_head_checkbox.isChecked())))
            self.head_size_scale_spin.setValue(float(visual.get("head_size_scale", self.head_size_scale_spin.value())))
            self.halo_size_scale_spin.setValue(float(visual.get("halo_size_scale", self.halo_size_scale_spin.value())))
            self.flash_size_scale_spin.setValue(float(visual.get("flash_size_scale", self.flash_size_scale_spin.value())))
            self.show_axes_checkbox.setChecked(bool(visual.get("show_axes", self.show_axes_checkbox.isChecked())))
            self.show_axis_labels_checkbox.setChecked(bool(visual.get("show_axis_labels", self.show_axis_labels_checkbox.isChecked())))
            point_label_mode = str(visual.get("point_label_mode", self.point_label_mode_combo.currentText()))
            if self.point_label_mode_combo.findText(point_label_mode) >= 0:
                self.point_label_mode_combo.setCurrentText(point_label_mode)
            point_label_content = str(visual.get("point_label_content", self.point_label_content_combo.currentText()))
            if self.point_label_content_combo.findText(point_label_content) >= 0:
                self.point_label_content_combo.setCurrentText(point_label_content)
            self.point_label_count_spin.setValue(int(visual.get("max_point_labels", self.point_label_count_spin.value())))

            self.autorotate_checkbox.setChecked(bool(motion.get("autorotate", self.autorotate_checkbox.isChecked())))
            self.rotation_speed_spin.setValue(float(motion.get("rotation_speed", self.rotation_speed_spin.value())))
            self.elevation_spin.setValue(float(motion.get("elevation", self.elevation_spin.value())))
            self.zoom_spin.setValue(float(motion.get("zoom", self.zoom_spin.value())))
            self._base_azimuth = float(motion.get("base_azimuth", self._base_azimuth))

            self.show_preview_checkbox.setChecked(bool(ui_state.get("show_preview", self.show_preview_checkbox.isChecked())))
            self.show_panels_checkbox.setChecked(bool(ui_state.get("show_panels", self.show_panels_checkbox.isChecked())))
            self._sync_layout_visibility()

            self.project_title_edit.setText(str(branding.get("project_title", self.project_title_edit.text())))
            self.project_subtitle_edit.setText(str(branding.get("project_subtitle", self.project_subtitle_edit.text())))
            self.watermark_edit.setText(str(branding.get("watermark_text", self.watermark_edit.text())))
            self.show_export_title_checkbox.setChecked(bool(branding.get("show_export_title", self.show_export_title_checkbox.isChecked())))
            self.show_export_watermark_checkbox.setChecked(bool(branding.get("show_export_watermark", self.show_export_watermark_checkbox.isChecked())))
            self.show_export_colorbar_checkbox.setChecked(bool(branding.get("show_export_colorbar", self.show_export_colorbar_checkbox.isChecked())))

            preset_key = str(export.get("preset_key", self._active_export_preset_key))
            if preset_key in EXPORT_PRESET_MAP:
                self._active_export_preset_key = preset_key
                if hasattr(self, "export_preset_combo") and self.export_preset_combo.findText(EXPORT_PRESET_MAP[preset_key].title) >= 0:
                    self.export_preset_combo.setCurrentText(EXPORT_PRESET_MAP[preset_key].title)
            self.render_width_spin.setValue(int(export.get("width", self.render_width_spin.value())))
            self.render_height_spin.setValue(int(export.get("height", self.render_height_spin.value())))
            self.render_fps_spin.setValue(int(export.get("fps", self.render_fps_spin.value())))
            export_engine = str(export.get("engine", self.export_engine_combo.currentText() if hasattr(self, "export_engine_combo") else EXPORT_ENGINE_AUTO_LABEL))
            if hasattr(self, "export_engine_combo") and self.export_engine_combo.findText(export_engine) >= 0:
                self.export_engine_combo.setCurrentText(export_engine)
            export_quality = str(export.get("quality", self.export_quality_combo.currentText() if hasattr(self, "export_quality_combo") else EXPORT_QUALITY_BALANCED_LABEL))
            if hasattr(self, "export_quality_combo") and self.export_quality_combo.findText(export_quality) >= 0:
                self.export_quality_combo.setCurrentText(export_quality)
            self.limit_export_range_checkbox.setChecked(bool(export.get("limit_range", self.limit_export_range_checkbox.isChecked())))
            self.export_start_spin.setValue(float(export.get("start_time", self.export_start_spin.value())))
            self.export_end_spin.setValue(float(export.get("end_time", self.export_end_spin.value())))
            self.export_layout_spec = ExportLayoutSpec.from_dict(export.get("layout", self.export_layout_spec.to_dict()))

            self.camera_keyframe_easing = str(cinematics.get("easing", self.camera_keyframe_easing))
            self.camera_keyframes = deserialize_keyframes(cinematics.get("keyframes"))
            if hasattr(self, "use_keyframes_checkbox"):
                self.use_keyframes_checkbox.setChecked(bool(cinematics.get("use_keyframes", self.use_keyframes_checkbox.isChecked())))
            if hasattr(self, "keyframe_easing_combo") and self.keyframe_easing_combo.findText(self.camera_keyframe_easing) >= 0:
                self.keyframe_easing_combo.setCurrentText(self.camera_keyframe_easing)
            self.bookmarks = deserialize_bookmarks(annotations.get("bookmarks"))
            self.regions = deserialize_regions(annotations.get("regions"))
            self.export_queue_jobs = deserialize_queue(export_queue.get("jobs"))
            self._export_job_counter = int(export_queue.get("job_counter", self._export_job_counter))
            if hasattr(self, "region_start_spin") and self.regions:
                self.region_start_spin.setValue(float(self.regions[0].start))
                self.region_end_spin.setValue(float(self.regions[0].end))

            if hasattr(self, "live_drive_visualizer_checkbox"):
                self.live_drive_visualizer_checkbox.setChecked(bool(diagnostics.get("drive_visualizer", self.live_drive_visualizer_checkbox.isChecked())))
                self.live_history_spin.setValue(float(diagnostics.get("history_seconds", self.live_history_spin.value())))
                self.live_baseline_window_spin.setValue(float(diagnostics.get("baseline_window_seconds", self.live_baseline_window_spin.value())))
                self.live_monitor_gate_spin.setValue(float(diagnostics.get("gate_db", self.live_monitor_gate_spin.value())))
                self.live_warn_threshold_spin.setValue(float(diagnostics.get("warn_threshold", self.live_warn_threshold_spin.value())))
                self.live_alarm_threshold_spin.setValue(float(diagnostics.get("alarm_threshold", self.live_alarm_threshold_spin.value())))
                self.live_min_event_spin.setValue(float(diagnostics.get("min_event_seconds", self.live_min_event_spin.value())))
                self._live_saved_baseline_model = deserialize_baseline_model(diagnostics.get("baseline_model"))
                device_label = str(diagnostics.get("device_label", ""))
                if device_label and self.live_device_combo.findText(device_label) >= 0:
                    self.live_device_combo.setCurrentText(device_label)
                self.live_monitor.set_baseline(self._live_saved_baseline_model)

            current_restore_enabled = (
                bool(self.restore_last_session_checkbox.isChecked())
                if hasattr(self, "restore_last_session_checkbox")
                else bool(getattr(self, "_restore_last_session_enabled", True))
            )
            restore_enabled = bool(payload.get("restore_last_session_enabled", current_restore_enabled))
            self._restore_last_session_enabled = restore_enabled
            if hasattr(self, "restore_last_session_checkbox"):
                self.restore_last_session_checkbox.setChecked(restore_enabled)
            self._active_profile_name = payload.get("active_profile_name") or self._active_profile_name
            self.project_path = payload.get("project_path") or self.project_path
            self._refresh_profile_list(selected=self._active_profile_name)
            self._refresh_project_label()
        finally:
            self._applying_state = False

        if restore_file:
            session = payload.get("session", {})
            file_path = session.get("file_path")
            target_time = float(session.get("current_time", 0.0))
            if file_path and Path(file_path).exists():
                self.load_file(str(file_path))
                if target_time > 0.0:
                    self.current_time = target_time
                    self.media_player.setPosition(int(round(target_time * 1000.0)))
            else:
                self._refresh_visuals()
        else:
            self._refresh_visuals()
        self._sync_timeline_range()
        self._refresh_keyframe_list()
        self._refresh_bookmark_list()
        self._refresh_region_list()
        self._refresh_export_queue_list()
        self._sync_visualizer_behavior_preset_combo()
        self._mark_clean(reason="state_applied")

    def _save_last_session_safely(self) -> None:
        try:
            payload = self._collect_state(include_source=True)
            self.profile_store.save_last_session(payload)
            self.profile_store.save_recovery_session({
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "state": payload,
            })
        except Exception:
            pass

    def _restore_last_session_if_enabled(self) -> bool:
        try:
            payload = self.profile_store.load_last_session()
        except Exception:
            return False
        if not payload:
            return False
        if not bool(payload.get("restore_last_session_enabled", True)):
            return False
        self._apply_state(payload, restore_file=True)
        self._set_status("Restored last session.", color=ACCENT)
        return True

    def _refresh_profile_list(self, selected: str | None = None) -> None:
        if not hasattr(self, "profile_combo"):
            return
        names = self.profile_store.list_profiles()
        target = selected or self._active_profile_name or self.profile_combo.currentText()
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(names)
        self.profile_combo.blockSignals(False)
        if target and self.profile_combo.findText(target) >= 0:
            self.profile_combo.setCurrentText(target)
        elif names:
            self.profile_combo.setCurrentIndex(0)
        self.profile_combo.setEnabled(bool(names))

    def save_current_profile(self) -> None:
        name = (self.profile_combo.currentText() if hasattr(self, "profile_combo") else "") or self._active_profile_name
        if not name:
            self.save_current_profile_as()
            return
        payload = self._collect_state(include_source=False)
        self.profile_store.save_profile(name, payload)
        self._active_profile_name = name
        self._refresh_profile_list(selected=name)
        self._set_status(f"Saved profile '{name}'.", color=ACCENT)

    def save_current_profile_as(self) -> None:
        name, ok = QInputDialog.getText(self, APP_TITLE, "Profile name")
        name = str(name).strip()
        if not ok or not name:
            return
        payload = self._collect_state(include_source=False)
        self.profile_store.save_profile(name, payload)
        self._active_profile_name = name
        self._refresh_profile_list(selected=name)
        self._set_status(f"Saved profile '{name}'.", color=ACCENT)

    def load_selected_profile(self) -> None:
        name = self.profile_combo.currentText().strip() if hasattr(self, "profile_combo") else ""
        if not name:
            QMessageBox.information(self, APP_TITLE, "No saved profile is selected.")
            return
        payload = self.profile_store.load_profile(name)
        if not payload:
            QMessageBox.warning(self, APP_TITLE, f"Could not load profile '{name}'.")
            return
        self._active_profile_name = name
        self._apply_state(payload, restore_file=False)
        self._set_status(f"Loaded profile '{name}'.", color=ACCENT)

    def delete_selected_profile(self) -> None:
        name = self.profile_combo.currentText().strip() if hasattr(self, "profile_combo") else ""
        if not name:
            return
        if QMessageBox.question(self, APP_TITLE, f"Delete profile '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        self.profile_store.delete_profile(name)
        if self._active_profile_name == name:
            self._active_profile_name = None
        self._refresh_profile_list()
        self._set_status(f"Deleted profile '{name}'.", color=WARNING)

    def _refresh_project_label(self) -> None:
        if not hasattr(self, "project_label"):
            return
        if self.project_path:
            label = f"Project\n{self.project_path}"
        else:
            label = "No project file"
        self.project_label.setText(label)

    def _refresh_recent_projects_menu(self) -> None:
        if not hasattr(self, "file_menu"):
            return

        insert_before = None
        existing_actions = list(self.file_menu.actions())
        for action in existing_actions:
            try:
                action_text = action.text().strip().lower().replace("…", "...")
            except RuntimeError:
                continue
            if insert_before is None and (
                action.isSeparator()
                or action_text.startswith("save project")
                or action_text.startswith("save preset")
                or action_text.startswith("export render")
                or action_text.startswith("export mp4 visual")
                or action_text.startswith("quit")
            ):
                insert_before = action
            if action_text.startswith("open recent project"):
                try:
                    submenu = action.menu()
                except RuntimeError:
                    submenu = None
                try:
                    self.file_menu.removeAction(action)
                except RuntimeError:
                    pass
                try:
                    action.deleteLater()
                except RuntimeError:
                    pass
                if submenu is not None:
                    try:
                        submenu.deleteLater()
                    except RuntimeError:
                        pass

        menu = QMenu("Open recent project", self.file_menu)
        if insert_before is not None:
            self.file_menu.insertMenu(insert_before, menu)
        else:
            self.file_menu.addMenu(menu)
        self.recent_projects_menu = menu

        valid_files = [path for path in self._recent_projects if Path(path).exists()]
        if not valid_files:
            action = QAction("No recent projects", menu)
            action.setEnabled(False)
            menu.addAction(action)
            return

        for file_path in valid_files:
            display_name = Path(file_path).name
            parent_name = Path(file_path).parent.name
            label = f"{display_name}  —  {parent_name}" if parent_name else display_name
            action = QAction(label, menu)
            action.triggered.connect(lambda _checked=False, p=file_path: self._open_recent_project(p))
            menu.addAction(action)

    def _refresh_recent_presets_menu(self) -> None:
        if not hasattr(self, "file_menu"):
            return

        insert_before = None
        existing_actions = list(self.file_menu.actions())
        for action in existing_actions:
            try:
                action_text = action.text().strip().lower().replace("…", "...")
            except RuntimeError:
                continue
            if insert_before is None and (
                action.isSeparator()
                or action_text.startswith("save project")
                or action_text.startswith("save preset")
                or action_text.startswith("export render")
                or action_text.startswith("export mp4 visual")
                or action_text.startswith("quit")
            ):
                insert_before = action
            if action_text.startswith("open recent preset"):
                try:
                    submenu = action.menu()
                except RuntimeError:
                    submenu = None
                try:
                    self.file_menu.removeAction(action)
                except RuntimeError:
                    pass
                try:
                    action.deleteLater()
                except RuntimeError:
                    pass
                if submenu is not None:
                    try:
                        submenu.deleteLater()
                    except RuntimeError:
                        pass

        menu = QMenu("Open recent preset", self.file_menu)
        if insert_before is not None:
            self.file_menu.insertMenu(insert_before, menu)
        else:
            self.file_menu.addMenu(menu)
        self.recent_presets_menu = menu

        valid_files = [path for path in self._recent_presets if Path(path).exists()]
        if not valid_files:
            action = QAction("No recent presets", menu)
            action.setEnabled(False)
            menu.addAction(action)
            return

        for file_path in valid_files:
            display_name = Path(file_path).name
            parent_name = Path(file_path).parent.name
            label = f"{display_name}  —  {parent_name}" if parent_name else display_name
            action = QAction(label, menu)
            action.triggered.connect(lambda _checked=False, p=file_path: self._open_recent_preset(p))
            menu.addAction(action)

    def new_project(self) -> None:
        self.project_path = None
        self._refresh_project_label()
        self._mark_dirty(reason="new_project")
        self._set_status("Started a new unsaved project.", color=ACCENT)

    def open_project_dialog(self) -> None:
        start_dir = str(Path(self.project_path).parent) if self.project_path else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, f"Open project ({PROJECT_EXTENSION})", start_dir, _project_file_dialog_filter(include_legacy=True))
        if path:
            self.load_project_file(path)

    def save_project(self) -> None:
        if not self.project_path:
            self.save_project_as()
            return
        payload = build_project_payload(Path(self.project_path).stem, self._collect_state(include_source=True), self.project_path)
        payload["app"] = APP_NAME
        payload["app_version"] = APP_VERSION
        saved = save_project(self.project_path, payload)
        self.project_path = str(saved)
        self._recent_projects = self.profile_store.append_recent_project(self.project_path)
        self._refresh_recent_projects_menu()
        self._refresh_project_label()
        self._save_last_session_safely()
        self.profile_store.clear_recovery_session()
        self._mark_clean(reason="project_saved")
        self._set_status(f"Saved project: {self.project_path}", color=ACCENT)

    def save_project_as(self) -> None:
        suggested_name = Path(self.project_path).stem if self.project_path else (self.project_title_edit.text().strip() or (Path(self.file_path).stem if self.file_path else "metriq_visualizer_project"))
        suggested_name = _safe_dialog_stem(suggested_name, "metriq_visualizer_project")
        start_path = str(Path.home() / f"{suggested_name}{PROJECT_EXTENSION}")
        path, _ = QFileDialog.getSaveFileName(self, f"Save project as {PROJECT_EXTENSION}", start_path, _project_file_dialog_filter(include_legacy=False))
        if not path:
            return
        if not path.lower().endswith(PROJECT_EXTENSION):
            path += PROJECT_EXTENSION
        self.project_path = path
        self.save_project()

    def load_project_file(self, path: str) -> None:
        try:
            payload = load_project(path)
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Could not open project:\n\n{exc}")
            return
        self.project_path = str(payload.get("project_path") or path)
        self._recent_projects = self.profile_store.append_recent_project(self.project_path)
        self._refresh_recent_projects_menu()
        self._refresh_project_label()
        state = dict(payload.get("state") or {})
        self._apply_state(state, restore_file=True)
        self._set_status(f"Loaded project: {Path(self.project_path).name}", color=ACCENT)

    def open_preset_dialog(self) -> None:
        start_dir = str(Path(self.preset_path).parent) if self.preset_path else str(self._visualizer_behavior_presets_dir())
        path, _ = QFileDialog.getOpenFileName(self, f"Open preset ({PRESET_EXTENSION})", start_dir, _preset_file_dialog_filter())
        if path:
            self.load_preset_file(path)

    def save_preset(self) -> None:
        if not self.preset_path:
            self.save_preset_as()
            return
        preset_name = Path(self.preset_path).stem or self.preset_combo.currentText().strip() or "metriq_visualizer_preset"
        payload = build_preset_payload(preset_name, self._collect_preset_state())
        payload["app"] = APP_NAME
        payload["app_version"] = APP_VERSION
        saved = save_preset(self.preset_path, payload)
        self.preset_path = str(saved)
        self._recent_presets = self.profile_store.append_recent_preset(self.preset_path)
        self._refresh_recent_presets_menu()
        self._reload_visualizer_behavior_presets()
        self._set_status(f"Saved preset: {self.preset_path}", color=ACCENT)

    def save_preset_as(self) -> None:
        suggested_name = Path(self.preset_path).stem if self.preset_path else (self.preset_combo.currentText().strip() or (Path(self.file_path).stem if self.file_path else "metriq_visualizer_preset"))
        suggested_name = _safe_dialog_stem(suggested_name, "metriq_visualizer_preset")
        start_path = str(self._visualizer_behavior_presets_dir() / f"{suggested_name}{PRESET_EXTENSION}")
        path, _ = QFileDialog.getSaveFileName(self, f"Save preset as {PRESET_EXTENSION}", start_path, _preset_file_dialog_filter())
        if not path:
            return
        if not path.lower().endswith(PRESET_EXTENSION):
            path += PRESET_EXTENSION
        self.preset_path = path
        self.save_preset()

    def load_preset_file(self, path: str) -> None:
        try:
            payload = load_preset(path)
            self._apply_loaded_preset_state(payload)
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Could not open preset:\n\n{exc}")
            return
        self._reload_visualizer_behavior_presets()
        self._set_status(f"Loaded preset: {Path(self.preset_path or path).name}", color=ACCENT)

    def _open_recent_project(self, path: str) -> None:
        if not Path(path).exists():
            self._recent_projects = [p for p in self._recent_projects if p != path]
            self.profile_store.save_recent_projects(self._recent_projects)
            self._refresh_recent_projects_menu()
            QMessageBox.warning(self, APP_TITLE, f"Recent project no longer exists:\n\n{path}")
            return
        self.load_project_file(path)

    def _open_recent_preset(self, path: str) -> None:
        if not Path(path).exists():
            self._recent_presets = [p for p in self._recent_presets if p != path]
            self.profile_store.save_recent_presets(self._recent_presets)
            self._refresh_recent_presets_menu()
            QMessageBox.warning(self, APP_TITLE, f"Recent preset no longer exists:\n\n{path}")
            return
        self.load_preset_file(path)

    def _refresh_recent_files_menu(self) -> None:
        if not hasattr(self, "file_menu"):
            return

        insert_before = None
        existing_actions = list(self.file_menu.actions())
        for action in existing_actions:
            try:
                action_text = action.text().strip().lower().replace("…", "...")
            except RuntimeError:
                continue
            if insert_before is None and (
                action.isSeparator()
                or action_text.startswith("save project")
                or action_text.startswith("save preset")
                or action_text.startswith("export render")
                or action_text.startswith("export mp4 visual")
                or action_text.startswith("quit")
            ):
                insert_before = action
            if action_text.startswith("open recent media") or action_text.startswith("open recent source"):
                try:
                    submenu = action.menu()
                except RuntimeError:
                    submenu = None
                try:
                    self.file_menu.removeAction(action)
                except RuntimeError:
                    pass
                try:
                    action.deleteLater()
                except RuntimeError:
                    pass
                if submenu is not None:
                    try:
                        submenu.deleteLater()
                    except RuntimeError:
                        pass

        menu = QMenu("Open recent source", self.file_menu)
        if insert_before is not None:
            self.file_menu.insertMenu(insert_before, menu)
        else:
            self.file_menu.addMenu(menu)
        self.recent_files_menu = menu

        valid_files = [path for path in self._recent_files if Path(path).exists()]
        if not valid_files:
            action = QAction("No recent files", menu)
            action.setEnabled(False)
            menu.addAction(action)
            return

        for file_path in valid_files:
            display_name = Path(file_path).name
            parent_name = Path(file_path).parent.name
            label = f"{display_name}  —  {parent_name}" if parent_name else display_name
            action = QAction(label, menu)
            action.triggered.connect(lambda _checked=False, p=file_path: self._open_recent_file(p))
            menu.addAction(action)

    def _open_recent_file(self, path: str) -> None:
        if not Path(path).exists():
            self._recent_files = [p for p in self._recent_files if p != path]
            self.profile_store.save_recent_files(self._recent_files)
            self._refresh_recent_files_menu()
            QMessageBox.warning(self, APP_TITLE, f"Recent file no longer exists:\n\n{path}")
            return
        self.load_file(path)

    def _current_view_params(self) -> ViewParams:
        return ViewParams(
            current_time=float(self.current_time),
            base_alpha=float(self.alpha_spin.value()),
            history_mode=self.history_combo.currentText(),
            point_lifespan=float(self.point_lifespan_spin.value()),
            fade_curve=float(self.fade_curve_spin.value()),
            max_points=int(self._current_live_point_budget()),
            connect_lines=bool(self.connect_lines_checkbox.isChecked()),
            line_width=float(self.line_width_spin.value()),
            path_curve_mode=self.path_curve_mode_combo.currentText(),
            curve_detail=int(self.curve_detail_spin.value()),
            comet_duration=float(self.comet_duration_spin.value()),
            flash_duration=float(self.flash_duration_spin.value()),
            ghost_path=bool(self.ghost_path_checkbox.isChecked()),
            autorotate=bool(self.autorotate_checkbox.isChecked()),
            rotation_speed=float(self.rotation_speed_spin.value()),
            elevation=float(self.elevation_spin.value()),
            zoom=float(self.zoom_spin.value()),
            point_size_scale=float(self.point_size_scale_spin.value()),
            render_mode=self.render_mode_combo.currentText(),
            tube_radius_scale=float(self.tube_radius_scale_spin.value()),
            tube_sides=int(self.tube_sides_spin.value()),
            tube_follow_size=bool(self.tube_follow_size_checkbox.isChecked()),
            tube_taper=float(self.tube_taper_spin.value()),
            show_head_marker=bool(self.show_head_checkbox.isChecked()),
            head_size_scale=float(self.head_size_scale_spin.value()),
            halo_size_scale=float(self.halo_size_scale_spin.value()),
            flash_size_scale=float(self.flash_size_scale_spin.value()),
            show_axes=bool(self.show_axes_checkbox.isChecked()),
            show_axis_labels=bool(self.show_axis_labels_checkbox.isChecked()),
            point_label_mode=self.point_label_mode_combo.currentText(),
            point_label_content=self.point_label_content_combo.currentText(),
            max_point_labels=int(self.point_label_count_spin.value()),
        )

    def _current_duration_seconds(self) -> float:
        if self._using_live_visualizer() and not self.file_path:
            return max(float(self.live_history_spin.value()) if hasattr(self, "live_history_spin") else 0.0, float(self._live_current_time))
        media_duration = float(self.media_player.duration()) / 1000.0
        return max(media_duration, self.current_duration, float(self.analysis.duration) if self.analysis is not None else 0.0)

    def _update_transport_text(self) -> None:
        if self._using_live_visualizer() and self._live_snapshot is not None:
            features = self._live_snapshot.get("features") or {}
            dominant_arr = np.asarray(features.get("dominant_freq_hz", [0.0]), dtype=np.float64).reshape(-1)
            dominant_hz = float(dominant_arr[-1]) if dominant_arr.size else 0.0
            score = float(self._live_snapshot.get("current_score", 0.0))
            status = str(self._live_snapshot.get("current_status", "Live"))
            self.time_label.setText(f"Live {self._live_current_time:0.02f} s • {status} • score {score:0.2f}")
            self.geometry_hud.setText(f"Live • {dominant_hz:0.1f} Hz • {status}")
            if hasattr(self, "transport_info"):
                event_count = len(self._live_snapshot.get("events") or [])
                self.transport_info.setText(f"Live diagnostics • {event_count} events")
            return
        duration = self._current_duration_seconds()
        dominant_hz = 0.0
        if self.analysis is not None and self.analysis.times.size > 0 and "dominant_freq_hz" in self.analysis.features:
            idx = nearest_time_index(self.analysis.times, self.current_time)
            idx = max(0, min(idx, int(self.analysis.times.size - 1)))
            dominant_hz = float(self.analysis.features["dominant_freq_hz"][idx])
        self.time_label.setText(f"{self.current_time:0.02f} s / {duration:0.02f} s")
        self.geometry_hud.setText(f"{self.current_time:0.02f} s • {dominant_hz:0.1f} Hz")

    def _sync_timeline_range(self) -> None:
        duration = self._current_duration_seconds()
        duration_ms = int(round(duration * 1000.0))
        self.timeline_slider.setRange(0, max(0, duration_ms))
        if hasattr(self, "jump_to_spin"):
            self.jump_to_spin.setRange(0.0, max(0.0, duration))
            self.jump_to_spin.setValue(max(0.0, min(self.jump_to_spin.value(), duration)))
        if hasattr(self, "export_start_spin"):
            self.export_start_spin.setRange(0.0, max(0.0, duration))
            self.export_end_spin.setRange(0.0, max(0.0, duration))
            if self.export_end_spin.value() <= 0.0 or self.export_end_spin.value() > duration + 1e-6:
                self.export_end_spin.setValue(duration)
            self.export_start_spin.setValue(max(0.0, min(self.export_start_spin.value(), duration)))
            self.export_end_spin.setValue(max(self.export_start_spin.value(), min(self.export_end_spin.value(), duration)))
        if hasattr(self, "region_start_spin"):
            self.region_start_spin.setRange(0.0, max(0.0, duration))
            self.region_end_spin.setRange(0.0, max(0.0, duration))
            self.region_start_spin.setValue(max(0.0, min(self.region_start_spin.value(), duration)))
            self.region_end_spin.setValue(max(0.0, min(self.region_end_spin.value(), duration)))

    def _jump_to_time_from_spin(self) -> None:
        if not hasattr(self, "jump_to_spin"):
            return
        target = max(0.0, min(float(self.jump_to_spin.value()), self._current_duration_seconds()))
        target_ms = int(round(target * 1000.0))
        self.media_player.setPosition(target_ms)
        self.current_time = target
        self._refresh_visuals(immediate=True)

    def _set_export_range_from_playhead(self, which: str) -> None:
        target = max(0.0, min(float(self.current_time), self._current_duration_seconds()))
        if which == "start":
            self.export_start_spin.setValue(target)
            if self.export_end_spin.value() < target:
                self.export_end_spin.setValue(target)
        else:
            self.export_end_spin.setValue(target)
            if self.export_start_spin.value() > target:
                self.export_start_spin.setValue(target)
        self.limit_export_range_checkbox.setChecked(True)

    def _reset_export_range_to_full(self) -> None:
        duration = self._current_duration_seconds()
        self.export_start_spin.setValue(0.0)
        self.export_end_spin.setValue(duration)
        self.limit_export_range_checkbox.setChecked(False)

    def _selected_export_range(self) -> tuple[float, float | None]:
        duration = self._current_duration_seconds()
        if not getattr(self, "limit_export_range_checkbox", None) or not self.limit_export_range_checkbox.isChecked():
            return 0.0, None
        start = max(0.0, min(float(self.export_start_spin.value()), duration))
        end = max(start, min(float(self.export_end_spin.value()), duration))
        if end - start <= 1e-6:
            return 0.0, None
        return start, end

    def _sync_layout_visibility(self) -> None:
        preview_visible = self.show_preview_checkbox.isChecked()
        panels_visible = self.show_panels_checkbox.isChecked()
        self.preview_group.setVisible(preview_visible)
        self.analysis_group.setVisible(panels_visible)
        if preview_visible:
            self.top_splitter.setSizes([1200, 360])
        else:
            self.top_splitter.setSizes([1, 0])
        if panels_visible:
            self.right_splitter.setSizes([820, 260])
        else:
            self.right_splitter.setSizes([1, 0])
        QTimer.singleShot(0, lambda: self._force_live_viewport_refresh(burst=True))

    def _focus_geometry(self) -> None:
        self.show_preview_checkbox.setChecked(False)
        self.show_panels_checkbox.setChecked(False)
        self._sync_layout_visibility()

    def _reset_layout(self) -> None:
        self.show_preview_checkbox.setChecked(True)
        self.show_panels_checkbox.setChecked(True)
        self.body_splitter.setSizes([420, 1480])
        self.top_splitter.setSizes([1200, 360])
        self.right_splitter.setSizes([820, 260])
        self._sync_layout_visibility()

    def _reset_camera(self) -> None:
        self._base_azimuth = 35.0
        self.elevation_spin.setValue(24.0)
        self.zoom_spin.setValue(1.0)
        self.geometry_view.reset_camera(elevation=24.0, azimuth=self._base_azimuth, zoom=1.0)
        self._refresh_visuals()

    def _force_live_viewport_refresh(self, burst: bool = False) -> None:
        try:
            self.geometry_view._invalidate_render_cache()
            self.geometry_view.show()
            self.geometry_view.updateGeometry()
            self.geometry_view.update()
            self.geometry_group.update()
            self.top_splitter.update()
            self.right_splitter.update()
            self._refresh_visuals(immediate=True)
            self.profile_store.append_diagnostic_log(
                f"[{datetime.now().isoformat(timespec='seconds')}] live viewport refresh requested size={self.geometry_view.width()}x{self.geometry_view.height()} visible={self.geometry_view.isVisible()}"
            )
        except Exception:
            self.profile_store.append_diagnostic_log(
                f"[{datetime.now().isoformat(timespec='seconds')}] live viewport refresh failed\n{traceback.format_exc()}"
            )
            return
        if burst:
            for delay_ms in (0, 90, 260):
                QTimer.singleShot(delay_ms, lambda: self._force_live_viewport_refresh(burst=False))

    def _sync_camera_controls_from_view(self) -> None:
        distance = self.geometry_view.current_distance()
        zoom = self.geometry_view.zoom_for_distance(distance)
        view_azimuth = float(self.geometry_view.opts.get("azimuth", self._base_azimuth))
        view_elevation = float(self.geometry_view.opts.get("elevation", self.elevation_spin.value()))
        if self.autorotate_checkbox.isChecked():
            view_azimuth -= float(self.rotation_speed_spin.value()) * float(self.current_time)
        self._base_azimuth = view_azimuth

        self.zoom_spin.blockSignals(True)
        self.zoom_spin.setValue(max(self.zoom_spin.minimum(), min(self.zoom_spin.maximum(), zoom)))
        self.zoom_spin.blockSignals(False)
        self.elevation_spin.blockSignals(True)
        self.elevation_spin.setValue(max(self.elevation_spin.minimum(), min(self.elevation_spin.maximum(), view_elevation)))
        self.elevation_spin.blockSignals(False)

    # ---------- File loading + analysis ----------
    def open_file_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open source", str(Path.home()), self._source_filter_string())
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        self._clear_export_preview_session()
        self.file_path = path
        self.file_label.setText(path)
        self.toolbar_file_label.setText(Path(path).name)
        self.preview_meta.setText(Path(path).name)
        self.video_placeholder.setText("Video preview will appear here.\n\nAudio-only files still play with synchronized geometry.")
        self._recent_files = self.profile_store.append_recent_file(path)
        self._refresh_recent_files_menu()
        self.reset_time()
        self._prepare_media_source(path)
        self.analyze_current_file()
        self._schedule_autosave()

    def _prepare_media_source(self, path: str) -> None:
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self.media_player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        if is_video_file(path):
            self.preview_tabs.setCurrentWidget(self.video_widget)
            self.preview_meta.setText(f"Video source\n{Path(path).name}")
        else:
            self.preview_tabs.setCurrentWidget(self.video_placeholder)
            self.preview_meta.setText(f"Audio-only source\n{Path(path).name}")
        self._sync_timeline_range()

    def analyze_current_file(self) -> None:
        if not self.file_path:
            QMessageBox.information(self, APP_TITLE, "Choose a source file first.")
            return
        self._analysis_job_id += 1
        job_id = self._analysis_job_id
        self._analysis_running = True
        self._set_status("Analyzing source and extracting feature geometry…", color=WARNING)
        sample_rate = int(self.sample_rate_spin.value())
        n_fft = int(self.n_fft_spin.value())
        hop_length = int(self.hop_length_spin.value())
        source_path = self.file_path

        def task() -> None:
            try:
                result = analyze_media(source_path, sample_rate=sample_rate, n_fft=n_fft, hop_length=hop_length)
            except Exception:
                self.worker_bridge.analysisError.emit(job_id, traceback.format_exc())
            else:
                self.worker_bridge.analysisFinished.emit(job_id, result)

        threading.Thread(target=task, daemon=True).start()

    def _on_analysis_finished(self, job_id: int, result: object) -> None:
        if self._closing or job_id != self._analysis_job_id:
            return
        self._analysis_running = False
        self.analysis = result  # type: ignore[assignment]
        self.current_duration = float(self.analysis.times[-1]) if self.analysis.times.size > 0 else float(self.analysis.duration)
        self.feature_text.setPlainText(format_feature_reference(self.analysis))
        self._sync_timeline_range()
        self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] analysis complete: {self.current_duration:0.2f}s, {self.analysis.times.size} frames")
        self._clear_export_preview_session()
        self._set_status(
            f"Analysis complete. Duration {self.current_duration:0.2f}s, {self.analysis.times.size} frames.",
            color=ACCENT,
        )
        self._rebuild_geometry(reset_camera=True)

    def _on_analysis_error(self, job_id: int, trace: str) -> None:
        if self._closing or job_id != self._analysis_job_id:
            return
        self._analysis_running = False
        self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] analysis failed\n{trace}")
        self._set_status("Analysis failed. See the error dialog for details.", color=ERROR)
        QMessageBox.critical(self, APP_TITLE, trace[-6000:])

    # ---------- Geometry ----------
    def _rebuild_geometry(self, reset_camera: bool = False) -> None:
        if self.analysis is None:
            return
        self._geometry_job_id += 1
        job_id = self._geometry_job_id
        self._geometry_running = True
        analysis = self.analysis
        x_expression = self.x_edit.text()
        y_expression = self.y_edit.text()
        z_expression = self.z_edit.text()
        color_expression = self.color_edit.text()
        size_expression = self.size_edit.text()
        normalize_mode = self.normalize_combo.currentText()
        max_points = max(1, int(self.max_points_spin.value()))
        low_volume_cutoff_db = float(self.low_volume_cutoff_spin.value())
        colormap = self.colormap_combo.currentText()
        started = time.perf_counter()
        self._set_status("Building geometry mapping…", color=WARNING)

        def task() -> None:
            try:
                geom = build_geometry(
                    analysis,
                    x_expression=x_expression,
                    y_expression=y_expression,
                    z_expression=z_expression,
                    color_expression=color_expression,
                    size_expression=size_expression,
                    normalize_mode=normalize_mode,
                    max_points=max_points,
                    low_volume_cutoff_db=low_volume_cutoff_db,
                    colormap=colormap,
                )
                _, rgba_full, _ = prepare_color_mapping(geom.color_full, geom.colormap)
                payload = {
                    "geom": geom,
                    "rgba_full": np.asarray(rgba_full, dtype=np.float32),
                    "reset_camera": bool(reset_camera),
                    "elapsed": float(time.perf_counter() - started),
                }
                self.worker_bridge.geometryFinished.emit(job_id, payload)
            except Exception:
                self.worker_bridge.geometryError.emit(job_id, traceback.format_exc())

        threading.Thread(target=task, daemon=True).start()

    def _on_geometry_finished(self, job_id: int, payload: object) -> None:
        if self._closing or job_id != self._geometry_job_id:
            return
        self._geometry_running = False
        data = payload if isinstance(payload, dict) else {}
        geom = data.get("geom")
        rgba_full = data.get("rgba_full")
        if geom is None or rgba_full is None or self.analysis is None:
            return
        geom = self._apply_display_labels_to_geom(geom)
        self.geometry_data = geom
        self._rgba_full = np.asarray(rgba_full, dtype=np.float32)
        self.analysis_tabs.set_data(self.analysis, geom)
        self._clear_export_preview_session()
        self.geometry_view.set_scene_geometry(geom, self._rgba_full, analysis=self.analysis, reset_camera=bool(data.get("reset_camera", False)))
        if bool(data.get("reset_camera", False)):
            self._base_azimuth = 35.0
            self.elevation_spin.blockSignals(True)
            self.zoom_spin.blockSignals(True)
            self.elevation_spin.setValue(24.0)
            self.zoom_spin.setValue(1.0)
            self.elevation_spin.blockSignals(False)
            self.zoom_spin.blockSignals(False)
            self.geometry_view.reset_camera(elevation=24.0, azimuth=self._base_azimuth, zoom=1.0)
        elapsed = float(data.get("elapsed", 0.0))
        if elapsed >= 0.35:
            self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] geometry rebuild {elapsed:0.3f}s")
        self._refresh_visuals(immediate=True)
        self._force_live_viewport_refresh(burst=True)
        self._set_status("Geometry mapping updated.", color=ACCENT)

    def _on_geometry_error(self, job_id: int, trace: str) -> None:
        if self._closing or job_id != self._geometry_job_id:
            return
        self._geometry_running = False
        self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] geometry rebuild failed\n{trace}")
        self._set_status("Could not apply mapping. See the warning dialog for details.", color=ERROR)
        QMessageBox.warning(self, APP_TITLE, trace[-6000:])

    # ---------- Playback ----------
    def toggle_playback(self) -> None:
        if not self.file_path:
            return
        state = self.media_player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def reset_time(self) -> None:
        self.media_player.pause()
        self.media_player.setPosition(0)
        self.current_time = 0.0
        if hasattr(self, "jump_to_spin"):
            self.jump_to_spin.setValue(0.0)
        self._refresh_visuals(immediate=True)

    def seek_relative(self, delta_seconds: float) -> None:
        target_ms = int(round((self.current_time + float(delta_seconds)) * 1000.0))
        target_ms = max(0, min(target_ms, self.timeline_slider.maximum()))
        self.media_player.setPosition(target_ms)
        self.current_time = target_ms / 1000.0
        if hasattr(self, "jump_to_spin"):
            self.jump_to_spin.setValue(self.current_time)
        self._refresh_visuals(immediate=True)

    def _on_position_changed(self, position_ms: int) -> None:
        if self._dragging_timeline:
            return
        self.current_time = float(position_ms) / 1000.0
        if hasattr(self, "jump_to_spin") and not self.jump_to_spin.hasFocus():
            self.jump_to_spin.setValue(self.current_time)
        if self.media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._refresh_visuals(immediate=True)

    def _on_duration_changed(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self.current_duration = max(self.current_duration, float(duration_ms) / 1000.0)
        self._sync_timeline_range()
        self._update_transport_text()

    def _on_playback_state_changed(self, _state) -> None:
        is_playing = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("Pause" if is_playing else "Play")
        self._apply_frame_timer_settings()
        self._last_analysis_playhead_perf = 0.0
        self.analysis_tabs.set_playhead(self.current_time, force=True)
        if is_playing and not self.frame_timer.isActive():
            self.frame_timer.start()
        elif not is_playing and self.frame_timer.isActive():
            self.frame_timer.stop()
            self._refresh_visuals(immediate=True)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.loop_checkbox.isChecked():
                self.media_player.setPosition(0)
                self.current_time = 0.0
                self.media_player.play()
            else:
                self.media_player.pause()
                self._refresh_visuals()

    def _on_media_error(self, error, error_string: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        message = error_string or "Qt Multimedia could not open or decode this file."
        self._set_status(message, color=ERROR)

    def _on_frame_tick(self) -> None:
        if self._dragging_timeline:
            return
        try:
            self.current_time = float(self.media_player.position()) / 1000.0
            self._refresh_visuals()
        except Exception as exc:
            self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] playback update failed: {exc}")
            self._set_status(f"Playback update failed: {exc}", color=ERROR)
            self.frame_timer.stop()

    def _on_slider_pressed(self) -> None:
        self._dragging_timeline = True
        self._resume_after_seek = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.media_player.pause()
        self._scrub_preview_timer.stop()

    def _apply_scrub_preview(self) -> None:
        if not self._dragging_timeline:
            return
        self.current_time = float(self._pending_scrub_time)
        self._refresh_visuals(immediate=True)

    def _on_slider_moved(self, value: int) -> None:
        if self._syncing_slider:
            return
        self.current_time = float(value) / 1000.0
        self._pending_scrub_time = self.current_time
        if hasattr(self, "jump_to_spin") and not self.jump_to_spin.hasFocus():
            self.jump_to_spin.setValue(self.current_time)
        self._update_transport_text()
        self._scrub_preview_timer.start()

    def _on_slider_released(self) -> None:
        self._scrub_preview_timer.stop()
        value = self.timeline_slider.value()
        self.current_time = float(value) / 1000.0
        self.media_player.setPosition(value)
        self._dragging_timeline = False
        if hasattr(self, "jump_to_spin"):
            self.jump_to_spin.setValue(self.current_time)
        self._refresh_visuals(immediate=True)
        if self._resume_after_seek:
            self.media_player.play()
        self._resume_after_seek = False

    # ---------- Export ----------
    def _clear_export_preview_session(self, *_args) -> None:
        with self._preview_async_lock:
            self._preview_async_pending_job = None
            self._preview_async_pending_key = None
            self._preview_async_last_frame = None
            self._preview_async_last_key = None
            self._preview_async_generation += 1

    def _build_export_options(
        self,
        output_path: str,
        *,
        width: int | None = None,
        height: int | None = None,
        fps: int | None = None,
        layout_spec: ExportLayoutSpec | None = None,
    ):
        from metriq_visualizer_render import ExportOptions

        start_time, end_time = self._selected_export_range()
        return ExportOptions(
            output_path=output_path,
            width=int(width if width is not None else self.render_width_spin.value()),
            height=int(height if height is not None else self.render_height_spin.value()),
            fps=int(fps if fps is not None else self.render_fps_spin.value()),
            export_engine=self.export_engine_combo.currentText() if hasattr(self, "export_engine_combo") else EXPORT_ENGINE_AUTO_LABEL,
            export_quality=self.export_quality_combo.currentText() if hasattr(self, "export_quality_combo") else EXPORT_QUALITY_BALANCED_LABEL,
            layout=(layout_spec or self.export_layout_spec).clone().clamp(),
            base_alpha=float(self.alpha_spin.value()),
            history_mode=self.history_combo.currentText(),
            point_lifespan=float(self.point_lifespan_spin.value()),
            fade_curve=float(self.fade_curve_spin.value()),
            line_width=float(self.line_width_spin.value()),
            path_curve_mode=self.path_curve_mode_combo.currentText(),
            curve_detail=int(self.curve_detail_spin.value()),
            connect_lines=bool(self.connect_lines_checkbox.isChecked()),
            ghost_path=bool(self.ghost_path_checkbox.isChecked()),
            flash_duration=float(self.flash_duration_spin.value()),
            comet_duration=float(self.comet_duration_spin.value()),
            elev=float(self.elevation_spin.value()),
            azim=float(self._base_azimuth),
            autorotate=bool(self.autorotate_checkbox.isChecked()),
            rotation_speed=float(self.rotation_speed_spin.value()),
            zoom=float(self.zoom_spin.value()),
            point_size_scale=float(self.point_size_scale_spin.value()),
            render_mode=self.render_mode_combo.currentText(),
            tube_radius_scale=float(self.tube_radius_scale_spin.value()),
            tube_sides=int(self.tube_sides_spin.value()),
            tube_follow_size=bool(self.tube_follow_size_checkbox.isChecked()),
            tube_taper=float(self.tube_taper_spin.value()),
            show_head_marker=bool(self.show_head_checkbox.isChecked()),
            head_size_scale=float(self.head_size_scale_spin.value()),
            halo_size_scale=float(self.halo_size_scale_spin.value()),
            flash_size_scale=float(self.flash_size_scale_spin.value()),
            show_axes=bool(self.show_axes_checkbox.isChecked()),
            show_axis_labels=bool(self.show_axis_labels_checkbox.isChecked()),
            point_label_mode=self.point_label_mode_combo.currentText(),
            point_label_content=self.point_label_content_combo.currentText(),
            max_point_labels=int(self.point_label_count_spin.value()),
            show_colorbar=bool(self.show_export_colorbar_checkbox.isChecked()),
            show_project_title=bool(self.show_export_title_checkbox.isChecked()),
            project_title=self.project_title_edit.text().strip() or APP_TITLE,
            project_subtitle=self.project_subtitle_edit.text().strip(),
            show_watermark=bool(self.show_export_watermark_checkbox.isChecked()),
            watermark_text=self.watermark_edit.text().strip(),
            title=self.project_title_edit.text().strip() or APP_TITLE,
            start_time=float(start_time),
            end_time=None if end_time is None else float(end_time),
        )

    def _preview_signature(self, layout_spec: ExportLayoutSpec, width: int, height: int) -> tuple:
        enabled_flags = tuple((name, bool(layout_spec.item(name).enabled)) for name in LAYOUT_ITEM_ORDER)
        return (
            self.file_path,
            int(width),
            int(height),
            enabled_flags,
            self.history_combo.currentText(),
            float(self.point_lifespan_spin.value()),
            float(self.fade_curve_spin.value()),
            float(self.line_width_spin.value()),
            self.path_curve_mode_combo.currentText(),
            int(self.curve_detail_spin.value()),
            float(self.comet_duration_spin.value()),
            float(self.flash_duration_spin.value()),
            float(self.alpha_spin.value()),
            float(self.point_size_scale_spin.value()),
            self.render_mode_combo.currentText(),
            float(self.tube_radius_scale_spin.value()),
            int(self.tube_sides_spin.value()),
            bool(self.tube_follow_size_checkbox.isChecked()),
            float(self.tube_taper_spin.value()),
            bool(self.connect_lines_checkbox.isChecked()),
            bool(self.ghost_path_checkbox.isChecked()),
            float(self.elevation_spin.value()),
            float(self._base_azimuth),
            bool(self.autorotate_checkbox.isChecked()),
            float(self.rotation_speed_spin.value()),
            float(self.zoom_spin.value()),
            bool(self.show_head_checkbox.isChecked()),
            float(self.head_size_scale_spin.value()),
            float(self.halo_size_scale_spin.value()),
            float(self.flash_size_scale_spin.value()),
            bool(self.show_axes_checkbox.isChecked()),
            bool(self.show_axis_labels_checkbox.isChecked()),
            self.point_label_mode_combo.currentText(),
            self.point_label_content_combo.currentText(),
            int(self.point_label_count_spin.value()),
            bool(self.show_export_colorbar_checkbox.isChecked()),
            bool(self.show_export_title_checkbox.isChecked()),
            self.project_title_edit.text(),
            self.project_subtitle_edit.text(),
            bool(self.show_export_watermark_checkbox.isChecked()),
            self.watermark_edit.text(),
            self.x_label_edit.text(),
            self.y_label_edit.text(),
            self.z_label_edit.text(),
            self.color_label_edit.text(),
            self.size_label_edit.text(),
            bool(self.use_keyframes_checkbox.isChecked()) if hasattr(self, 'use_keyframes_checkbox') else False,
            self.keyframe_easing_combo.currentText() if hasattr(self, 'keyframe_easing_combo') else 'ease_in_out',
            tuple((round(float(kf.time), 3), round(float(kf.elevation), 3), round(float(kf.azimuth), 3), round(float(kf.zoom), 3)) for kf in self.camera_keyframes),
        )

    def _submit_export_preview_job(self, job: dict) -> None:
        should_start = False
        with self._preview_async_lock:
            if self._preview_async_pending_job is not None:
                self._record_perf_stat("preview_dropped")
            self._preview_async_pending_job = job
            self._preview_async_pending_key = job["request_key"]
            self._record_perf_stat("preview_submitted")
            if not self._preview_async_worker_running:
                self._preview_async_worker_running = True
                should_start = True
        if should_start:
            threading.Thread(target=self._preview_worker_loop, daemon=True).start()

    def _preview_worker_loop(self) -> None:
        session = None
        session_signature = None
        try:
            while True:
                with self._preview_async_lock:
                    job = self._preview_async_pending_job
                    self._preview_async_pending_job = None
                    self._preview_async_pending_key = None
                    if job is None:
                        self._preview_async_worker_running = False
                        break
                try:
                    from metriq_visualizer_render import ExportPreviewSession

                    if session is None or session_signature != job["session_signature"]:
                        if session is not None:
                            try:
                                session.close()
                            except Exception:
                                pass
                        session = ExportPreviewSession(job["analysis"], job["geometry"], job["options"])
                        session_signature = job["session_signature"]
                    frame = session.render_frame(
                        float(job["time"]),
                        layout=job["layout"].clone().clamp(),
                        output_size=(int(job["width"]), int(job["height"])),
                    )
                    self.worker_bridge.previewFrameReady.emit(job["request_key"], frame)
                except Exception:
                    self.worker_bridge.previewFrameError.emit(job["request_key"], traceback.format_exc())
                    if session is not None:
                        try:
                            session.close()
                        except Exception:
                            pass
                    session = None
                    session_signature = None
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    def _on_preview_frame_ready(self, request_key: object, frame: object) -> None:
        if not isinstance(request_key, tuple) or not request_key:
            return
        if int(request_key[0]) != int(self._preview_async_generation):
            return
        array = np.asarray(frame, dtype=np.uint8)
        with self._preview_async_lock:
            self._preview_async_last_key = request_key
            self._preview_async_last_frame = array
        self._maybe_flush_perf_log()
        dialog = self._active_export_dialog
        if dialog is not None and dialog.isVisible():
            QTimer.singleShot(0, dialog._queue_preview_refresh)

    def _on_preview_frame_error(self, request_key: object, trace: str) -> None:
        if isinstance(request_key, tuple) and request_key and int(request_key[0]) != int(self._preview_async_generation):
            return
        self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] export preview failed\n{trace}")

    def _render_export_preview_frame(self, layout_spec: ExportLayoutSpec, width: int, height: int, preview_time: float) -> np.ndarray | None:
        if self.analysis is None or self.geometry_data is None:
            return None

        layout = layout_spec.clone().clamp()
        session_signature = self._preview_signature(layout, width, height)
        request_key = (
            int(self._preview_async_generation),
            session_signature,
            int(width),
            int(height),
            round(float(preview_time), 3),
        )

        with self._preview_async_lock:
            cached_key = self._preview_async_last_key
            cached_frame = self._preview_async_last_frame
            pending_key = self._preview_async_pending_key

        if cached_key != request_key and pending_key != request_key:
            preview_options = self._build_export_options(
                output_path=str(Path.home() / ".metriq_visualizer_preview.mp4"),
                width=int(width),
                height=int(height),
                layout_spec=layout,
            )
            self._submit_export_preview_job(
                {
                    "request_key": request_key,
                    "session_signature": session_signature,
                    "analysis": self.analysis,
                    "geometry": self.geometry_data,
                    "options": preview_options,
                    "layout": layout,
                    "width": int(width),
                    "height": int(height),
                    "time": float(preview_time),
                }
            )

        if cached_key == request_key and cached_frame is not None:
            return np.asarray(cached_frame, dtype=np.uint8)
        if cached_frame is not None:
            return np.asarray(cached_frame, dtype=np.uint8)
        return None

    def configure_export_layout(self) -> None:
        dialog = ExportLayoutDialog(
            self.export_layout_spec,
            width=int(self.render_width_spin.value()),
            height=int(self.render_height_spin.value()),
            fps=int(self.render_fps_spin.value()),
            parent=self,
            preview_provider=self._render_export_preview_frame,
            time_provider=lambda: float(self.current_time),
            duration_seconds=self._current_duration_seconds(),
            preview_scale=float(self.preview_scale_spin.value()),
        )
        self._active_export_dialog = dialog
        result = dialog.exec()
        self._active_export_dialog = None
        self._clear_export_preview_session()
        if result == QDialog.DialogCode.Accepted:
            layout_spec, width, height, fps = dialog.result_payload()
            self.export_layout_spec = layout_spec
            self.render_width_spin.setValue(width)
            self.render_height_spin.setValue(height)
            self.render_fps_spin.setValue(fps)
            self._schedule_autosave()
            self._set_status("Export layout updated.", color=ACCENT)

    def export_mapped_data_csv_dialog(self) -> None:
        if self.analysis is None or self.geometry_data is None:
            QMessageBox.information(self, APP_TITLE, "Analyze a file first.")
            return
        start_dir = str(Path(self.file_path).with_suffix(".csv")) if self.file_path else str(Path.home() / "metriq_visualizer_data.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export mapped data CSV", start_dir, "CSV files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        analysis = self.analysis
        geom = self.geometry_data
        start_time, end_time = self._selected_export_range()
        active_mask = np.asarray(getattr(geom, "active_mask_full", np.ones_like(geom.times_full, dtype=bool)), dtype=bool).reshape(-1)
        if active_mask.size != geom.times_full.size:
            active_mask = np.ones_like(geom.times_full, dtype=bool)
        if end_time is None:
            mask = active_mask.copy()
        else:
            mask = ((geom.times_full >= start_time - 1e-9) & (geom.times_full <= end_time + 1e-9) & active_mask)
        indices = np.flatnonzero(mask)
        feature_names = [name for name, values in sorted(analysis.features.items()) if np.asarray(values).shape[0] == analysis.times.shape[0]]
        headers = [
            "time_s",
            "x",
            "y",
            "z",
            "color_value",
            "size_value",
            "size_display",
        ] + feature_names

        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(headers)
                for idx in indices.tolist():
                    row = [
                        float(geom.times_full[idx]),
                        float(geom.x_full[idx]),
                        float(geom.y_full[idx]),
                        float(geom.z_full[idx]),
                        float(geom.color_full[idx]),
                        float(geom.size_full[idx]),
                        float(geom.size_display_full[idx]),
                    ]
                    for feature_name in feature_names:
                        row.append(float(np.asarray(analysis.features[feature_name])[idx]))
                    writer.writerow(row)
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Could not export CSV:\n\n{exc}")
            return

        if end_time is None:
            self._set_status(f"Exported mapped data CSV: {path}", color=ACCENT)
        else:
            self._set_status(f"Exported mapped data CSV ({start_time:0.02f}s → {end_time:0.02f}s): {path}", color=ACCENT)


    def export_render_dialog(self) -> None:
        if self.analysis is None or self.geometry_data is None:
            QMessageBox.information(self, APP_TITLE, "Analyze a file first.")
            return
        if self._export_running or self._batch_running or self._queue_running:
            QMessageBox.information(self, APP_TITLE, "An export job is already running.")
            return

        layout_dialog = ExportLayoutDialog(
            self.export_layout_spec,
            width=int(self.render_width_spin.value()),
            height=int(self.render_height_spin.value()),
            fps=int(self.render_fps_spin.value()),
            parent=self,
            preview_provider=self._render_export_preview_frame,
            time_provider=lambda: float(self.current_time),
            duration_seconds=self._current_duration_seconds(),
            preview_scale=float(self.preview_scale_spin.value()),
        )
        self._active_export_dialog = layout_dialog
        if layout_dialog.exec() != QDialog.DialogCode.Accepted:
            self._active_export_dialog = None
            self._clear_export_preview_session()
            return
        self._active_export_dialog = None
        self.export_layout_spec, width, height, fps = layout_dialog.result_payload()
        self.render_width_spin.setValue(width)
        self.render_height_spin.setValue(height)
        self.render_fps_spin.setValue(fps)
        self._clear_export_preview_session()
        self._schedule_autosave()

        start_dir = str(Path(self.file_path).with_suffix(".mp4")) if self.file_path else str(Path.home() / "metriq_visualizer_render.mp4")
        path, _ = QFileDialog.getSaveFileName(self, "Export MP4 render", start_dir, "MP4 video (*.mp4)")
        if not path:
            return
        options = self._build_export_options(path)
        analysis = self.analysis
        geometry = self.geometry_data
        self._export_running = True
        self._set_status("Starting export render…", color=WARNING)

        def task() -> None:
            try:
                from metriq_visualizer_render import render_export_video

                render_export_video(analysis, geometry, options, progress_callback=self.worker_bridge.exportProgress.emit)
            except Exception:
                self.worker_bridge.exportError.emit(traceback.format_exc())
            else:
                self.worker_bridge.exportFinished.emit(path)

        threading.Thread(target=task, daemon=True).start()


    def batch_export_dialog(self) -> None:
        if self._export_running or self._batch_running or self._queue_running:
            QMessageBox.information(self, APP_TITLE, "An export job is already running.")
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Choose media for batch export", str(Path.home()), self._source_filter_string())
        if not files:
            return
        output_dir = QFileDialog.getExistingDirectory(self, "Choose batch export output folder", str(Path(files[0]).parent))
        if not output_dir:
            return
        self._batch_running = True
        self._set_status(f"Starting batch export for {len(files)} file(s)…", color=WARNING)
        output_dir_path = Path(output_dir)
        analysis_settings = {
            "sample_rate": int(self.sample_rate_spin.value()),
            "n_fft": int(self.n_fft_spin.value()),
            "hop_length": int(self.hop_length_spin.value()),
            "x_expression": self.x_edit.text(),
            "y_expression": self.y_edit.text(),
            "z_expression": self.z_edit.text(),
            "color_expression": self.color_edit.text(),
            "size_expression": self.size_edit.text(),
            "x_label": self.x_label_edit.text(),
            "y_label": self.y_label_edit.text(),
            "z_label": self.z_label_edit.text(),
            "color_label": self.color_label_edit.text(),
            "size_label": self.size_label_edit.text(),
            "normalize_mode": self.normalize_combo.currentText(),
            "max_points": max(1, int(self.max_points_spin.value())),
            "low_volume_cutoff_db": float(self.low_volume_cutoff_spin.value()),
            "colormap": self.colormap_combo.currentText(),
        }
        export_template = self._build_export_options(
            output_path=str(output_dir_path / "_template.mp4"),
            width=int(self.render_width_spin.value()),
            height=int(self.render_height_spin.value()),
            fps=int(self.render_fps_spin.value()),
            layout_spec=self.export_layout_spec.clone().clamp(),
        )

        def task() -> None:
            report_lines: list[str] = []
            successes = 0
            try:
                from metriq_visualizer_render import render_export_video

                for idx, source_path in enumerate(files, start=1):
                    try:
                        self.worker_bridge.exportProgress.emit((idx - 1) / max(1, len(files)), f"Batch {idx}/{len(files)}: analyzing {Path(source_path).name}")
                        analysis = analyze_media(
                            source_path,
                            sample_rate=analysis_settings["sample_rate"],
                            n_fft=analysis_settings["n_fft"],
                            hop_length=analysis_settings["hop_length"],
                        )
                        geom = build_geometry(
                            analysis,
                            x_expression=analysis_settings["x_expression"],
                            y_expression=analysis_settings["y_expression"],
                            z_expression=analysis_settings["z_expression"],
                            color_expression=analysis_settings["color_expression"],
                            size_expression=analysis_settings["size_expression"],
                            normalize_mode=analysis_settings["normalize_mode"],
                            max_points=analysis_settings["max_points"],
                            low_volume_cutoff_db=analysis_settings["low_volume_cutoff_db"],
                            colormap=analysis_settings["colormap"],
                        )
                        geom = self._apply_display_labels_to_geom(
                            geom,
                            formulas={
                                'x': analysis_settings['x_expression'],
                                'y': analysis_settings['y_expression'],
                                'z': analysis_settings['z_expression'],
                                'color': analysis_settings['color_expression'],
                                'size': analysis_settings['size_expression'],
                            },
                            custom_labels={
                                'x': analysis_settings['x_label'],
                                'y': analysis_settings['y_label'],
                                'z': analysis_settings['z_label'],
                                'color': analysis_settings['color_label'],
                                'size': analysis_settings['size_label'],
                            },
                        )
                        out_name = f"{Path(source_path).stem}__geometry.mp4"
                        out_path = str(output_dir_path / out_name)
                        options = replace(export_template, output_path=out_path)
                        render_export_video(
                            analysis,
                            geom,
                            options,
                            progress_callback=lambda progress, message, offset=idx - 1: self.worker_bridge.exportProgress.emit((offset + progress) / max(1, len(files)), message),
                        )
                        successes += 1
                        report_lines.append(f"OK  {source_path} -> {out_path}")
                    except Exception as exc:
                        report_lines.append(f"FAIL  {source_path} -> {exc}")
                report_path = output_dir_path / "batch_export_report.txt"
                report_path.write_text("\n".join(report_lines) + ("\n" if report_lines else ""), encoding="utf-8")
                self.worker_bridge.batchFinished.emit(f"Batch export finished: {successes}/{len(files)} succeeded. Report: {report_path}")
            except Exception:
                self.worker_bridge.batchError.emit(traceback.format_exc())

        threading.Thread(target=task, daemon=True).start()

    def _on_export_progress(self, _progress: float, message: str) -> None:
        self._set_status(message, color=WARNING)

    def _on_export_finished(self, output_path: str) -> None:
        self._export_running = False
        self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] export finished: {output_path}")
        self._set_status(f"Export finished: {output_path}", color=ACCENT)
        QMessageBox.information(self, APP_TITLE, f"Export finished:\n\n{output_path}")

    def _on_export_error(self, trace: str) -> None:
        self._export_running = False
        self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] export failed\n{trace}")
        self._set_status("Export failed. See the error dialog for details.", color=ERROR)
        QMessageBox.critical(self, APP_TITLE, trace[-6000:])

    def _on_batch_finished(self, message: str) -> None:
        self._batch_running = False
        self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] {message}")
        self._set_status(message, color=ACCENT)
        QMessageBox.information(self, APP_TITLE, message)

    def _on_batch_error(self, trace: str) -> None:
        self._batch_running = False
        self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] batch export failed\n{trace}")
        self._set_status("Batch export failed. See the error dialog for details.", color=ERROR)
        QMessageBox.critical(self, APP_TITLE, trace[-6000:])

    # ---------- Qt ----------
    def closeEvent(self, event):  # noqa: N802
        self._closing = True
        self.frame_timer.stop()
        self._ui_heartbeat_timer.stop()
        self._diagnostics_timer.stop()
        self._freeze_watchdog.stop()
        self._save_last_session_safely()
        self.profile_store.clear_recovery_session()
        self._mark_clean(reason="clean_close")
        self._clear_export_preview_session()
        self._maybe_flush_perf_log(force=True)
        try:
            self.live_monitor.stop()
        except Exception:
            pass
        try:
            self.media_player.stop()
        except Exception:
            pass
        super().closeEvent(event)


# ---------- Standalone visualizer behavior ----------
def _fun_visual_export_layout() -> ExportLayoutSpec:
    spec = geometry_focus_export_layout()
    spec.preview.enabled = False
    spec.spectrogram.enabled = False
    spec.chromagram.enabled = False
    spec.mfcc.enabled = False
    spec.traces.enabled = False
    spec.geometry.x = 0.03
    spec.geometry.y = 0.04
    spec.geometry.w = 0.94
    spec.geometry.h = 0.92
    spec.geometry.content_scale = 1.18
    spec.geometry.fit_mode = "contain"
    spec.geometry.background_alpha = 0.0
    spec.geometry.show_title = False
    return spec.clamp()


def _fun_notice_text(feature_name: str) -> str:
    return f"{feature_name} is not part of Metriq Visualizer."


def _fun_unavailable(self, feature_name: str) -> None:
    QMessageBox.information(self, APP_TITLE, _fun_notice_text(feature_name))


def _fun_normalize_text(text: str) -> str:
    return (text or "").replace("&", "").replace("…", "...").strip().lower()


def _fun_find_group_box(self, title: str) -> QGroupBox | None:
    for group in self.findChildren(QGroupBox):
        if group.title() == title:
            return group
    return None


def _fun_hide_left_tab(self, title: str) -> None:
    if not hasattr(self, "left_tabs"):
        return
    for index in range(self.left_tabs.count() - 1, -1, -1):
        if self.left_tabs.tabText(index) == title:
            self.left_tabs.removeTab(index)
            break


def _fun_hide_button_texts(self, texts: set[str]) -> None:
    normalized = {_fun_normalize_text(text) for text in texts}
    for button in self.findChildren(QPushButton):
        if _fun_normalize_text(button.text()) in normalized:
            button.hide()


def _fun_hide_file_menu_actions(self) -> None:
    if not hasattr(self, "file_menu"):
        return
    blocked_prefixes = (
        "batch export",
    )
    for action in list(self.file_menu.actions()):
        action_text = _fun_normalize_text(action.text())
        submenu = action.menu()
        if any(action_text.startswith(prefix) for prefix in blocked_prefixes):
            action.setVisible(False)
            if submenu is not None:
                submenu.menuAction().setVisible(False)
            continue
        if action_text.startswith("export render"):
            action.setText("Export MP4 visual…")
        elif action_text.startswith("open media"):
            action.setText("Open file…")
        elif action_text.startswith("open recent media"):
            action.setText("Open recent source")


def _fun_hide_mapping_formula_rows(self) -> None:
    group = _fun_find_group_box(self, "Geometry mapping")
    if group is None:
        return
    group.setTitle("Visual mapping")
    hidden_row_labels = {
        "X",
        "X label",
        "Y",
        "Y label",
        "Z",
        "Z label",
        "Color",
        "Color label",
        "Size",
        "Size label",
    }
    for label in group.findChildren(QLabel):
        if label.text() in hidden_row_labels:
            label.hide()
    for widget in (
        self.x_edit,
        self.y_edit,
        self.z_edit,
        self.color_edit,
        self.size_edit,
        self.x_label_edit,
        self.y_label_edit,
        self.z_label_edit,
        self.color_label_edit,
        self.size_label_edit,
    ):
        widget.setEnabled(False)
        widget.hide()
    for button in group.findChildren(QPushButton):
        if _fun_normalize_text(button.text()) == "apply mapping":
            button.hide()
    manual_idx = self.preset_combo.findText("Manual starter")
    if manual_idx >= 0:
        self.preset_combo.removeItem(manual_idx)
    note = QLabel(
        "Choose a preset style and tweak the look."
    )
    note.setWordWrap(True)
    layout = group.layout()
    if isinstance(layout, QFormLayout):
        layout.addRow(note)
    else:
        layout.addWidget(note)




def _fun_hide_form_row(form: QFormLayout, field: QWidget) -> None:
    label = form.labelForField(field)
    if label is not None:
        label.hide()
    field.hide()


def _fun_simplify_export_group(self) -> None:
    group = _fun_find_group_box(self, "Export render")
    if group is None:
        return
    group.setTitle("MP4 export")
    if EXPORT_PRESETS:
        preset = EXPORT_PRESETS[0]
        self.export_layout_spec = preset.layout_factory().clone().clamp()
        self._active_export_preset_key = preset.key
    else:
        self.export_layout_spec = _fun_visual_export_layout()

    if hasattr(self, "render_width_spin"):
        self.render_width_spin.setRange(640, FUN_EXPORT_WIDTH)
        self.render_width_spin.setEnabled(False)
    if hasattr(self, "render_height_spin"):
        self.render_height_spin.setRange(360, FUN_EXPORT_HEIGHT)
        self.render_height_spin.setEnabled(False)
    if hasattr(self, "render_fps_spin"):
        self.render_fps_spin.setRange(12, FUN_EXPORT_FPS)
        self.render_fps_spin.setEnabled(False)

    self.limit_export_range_checkbox.setText("Optionally limit MP4 export to time range")
    self.limit_export_range_checkbox.setChecked(False)
    self.export_start_spin.setValue(0.0)
    self.export_end_spin.setValue(self._current_duration_seconds())

    if hasattr(self, "export_preset_combo"):
        self.export_preset_combo.blockSignals(True)
        self.export_preset_combo.clear()
        for preset in EXPORT_PRESETS:
            self.export_preset_combo.addItem(preset.title)
        self.export_preset_combo.blockSignals(False)
        self.export_preset_combo.setEnabled(True)
        try:
            self.export_preset_combo.currentTextChanged.disconnect()
        except Exception:
            pass
        self.export_preset_combo.currentTextChanged.connect(self._apply_export_preset_by_title)
        if EXPORT_PRESETS:
            self.export_preset_combo.setCurrentText(EXPORT_PRESETS[0].title)
            self._apply_export_preset(EXPORT_PRESETS[0].key, set_status=False)

    form = group.layout()
    if isinstance(form, QFormLayout):
        _fun_hide_form_row(form, self.render_width_spin)
        _fun_hide_form_row(form, self.render_height_spin)
        _fun_hide_form_row(form, self.render_fps_spin)

    for button in group.findChildren(QPushButton):
        if _fun_normalize_text(button.text()) in {
            "apply preset",
            "arrange export layout...",
            "export mapped data csv...",
            "batch export...",
            "queue current render...",
        }:
            button.hide()

    self.show_export_title_checkbox.setChecked(False)
    self.show_export_title_checkbox.hide()
    self.show_export_watermark_checkbox.setChecked(False)
    self.show_export_watermark_checkbox.hide()
    self.show_export_colorbar_checkbox.setChecked(False)
    self.show_export_colorbar_checkbox.hide()
    self.project_title_edit.setText(APP_TITLE)
    self.project_subtitle_edit.clear()
    self.watermark_edit.clear()

    note = QLabel(
        f"Export MP4 videos in 720p, 1080p, or vertical 1080×1920 at {FUN_EXPORT_FPS} fps."
    )
    note.setWordWrap(True)
    if isinstance(form, QFormLayout):
        form.addRow(note)
    else:
        form.addWidget(note)


def _fun_selected_export_range(self) -> tuple[float, float | None]:
    duration = self._current_duration_seconds()
    if not getattr(self, "limit_export_range_checkbox", None) or not self.limit_export_range_checkbox.isChecked():
        return 0.0, None
    start = max(0.0, min(float(self.export_start_spin.value()), duration))
    end = max(start, min(float(self.export_end_spin.value()), duration))
    if end - start <= 1e-6:
        return 0.0, None
    return start, end

def _apply_standalone_visualizer_mode(self) -> None:
    if not getattr(self, "_fun_edition", False):
        return

    self.setWindowTitle(APP_WINDOW_TITLE)
    self._fun_visual_export_layout = _fun_visual_export_layout

    _fun_hide_left_tab(self, "Diagnostics")

    for title in (
        "Project",
        "Profiles + session",
        "Branding + product",
        "Camera keyframes",
        "Regions + bookmarks",
        "Export queue",
    ):
        group = _fun_find_group_box(self, title)
        if group is not None:
            group.hide()

    mapping_group = _fun_find_group_box(self, "Geometry mapping")
    if mapping_group is not None:
        mapping_group.setTitle("Visual mapping")

    _fun_simplify_export_group(self)
    _fun_hide_file_menu_actions(self)

    _fun_hide_button_texts(
        self,
        {
            "Batch export…",
            "Batch export...",
        },
    )

    for button in self.findChildren(QPushButton):
        normalized = _fun_normalize_text(button.text())
        if normalized == "open media...":
            button.setText("Open file…")
        elif normalized == "export mp4...":
            button.setText("Export MP4 visual…")

    if hasattr(self, "diagnostics_dashboard"):
        self.diagnostics_dashboard.hide()
    if hasattr(self, "show_panels_checkbox"):
        self.show_panels_checkbox.setChecked(True)
    if hasattr(self, "preset_combo") and self.preset_combo.findText(DEFAULT_STARTUP_PRESET) >= 0:
        self.preset_combo.setCurrentText(DEFAULT_STARTUP_PRESET)
        self._load_preset(DEFAULT_STARTUP_PRESET)
    if hasattr(self, "history_combo") and self.history_combo.findText("Trail fade") >= 0:
        self.history_combo.setCurrentText("Trail fade")
    self.point_lifespan_spin.setValue(float(DEFAULT_POINT_LIFESPAN))
    self.point_size_scale_spin.setValue(float(DEFAULT_POINT_SIZE_SCALE))
    self.head_size_scale_spin.setValue(float(DEFAULT_HEAD_SIZE_SCALE))
    self.halo_size_scale_spin.setValue(float(DEFAULT_HALO_SIZE_SCALE))
    self.flash_size_scale_spin.setValue(float(DEFAULT_FLASH_SIZE_SCALE))
    self._apply_theme_mode(DEFAULT_THEME_KEY, announce=False)
    self._set_status(
        "Open audio, video, CSV, TSV, or XLSX data to explore it as geometry and export an MP4.",
        color=ACCENT,
    )


def _fun_export_render_dialog(self) -> None:
    if self.analysis is None or self.geometry_data is None:
        QMessageBox.information(self, APP_TITLE, "Analyze a file first.")
        return
    if self._export_running or self._batch_running or self._queue_running:
        QMessageBox.information(self, APP_TITLE, "An export job is already running.")
        return

    preset = EXPORT_PRESET_MAP.get(getattr(self, "_active_export_preset_key", "")) or (EXPORT_PRESETS[0] if EXPORT_PRESETS else None)
    if preset is None:
        QMessageBox.warning(self, APP_TITLE, "No export presets are configured.")
        return

    default_name = Path(self.file_path).with_suffix(".mp4") if self.file_path else (Path.home() / "metriq_visualizer.mp4")
    path, _ = QFileDialog.getSaveFileName(self, "Export MP4 visual", str(default_name), "MP4 video (*.mp4)")
    if not path:
        return
    if not path.lower().endswith(".mp4"):
        path += ".mp4"

    layout = preset.layout_factory()
    self.export_layout_spec = layout.clone().clamp()
    options = self._build_export_options(path, layout_spec=layout)
    options = replace(
        options,
        width=int(preset.width),
        height=int(preset.height),
        fps=int(preset.fps),
        include_preview=False,
        include_panels=False,
        show_project_title=False,
        show_colorbar=False,
        show_watermark=False,
        project_title=APP_TITLE,
        project_subtitle="",
        watermark_text="",
        title=APP_TITLE,
    )

    analysis = self.analysis
    geometry = self.geometry_data
    self._export_running = True
    self._set_status(f"Starting visual MP4 export at {preset.width}×{preset.height} / {preset.fps} fps…", color=WARNING)

    def task() -> None:
        try:
            from metriq_visualizer_render import render_export_video

            render_export_video(analysis, geometry, options, progress_callback=self.worker_bridge.exportProgress.emit)
        except Exception:
            self.worker_bridge.exportError.emit(traceback.format_exc())
        else:
            self.worker_bridge.exportFinished.emit(path)

    threading.Thread(target=task, daemon=True).start()


def _fun_disable_restore_prompt(self, *args, **kwargs) -> bool:
    return False


def _fun_make_unavailable_wrapper(method_name: str, feature_name: str):
    original = getattr(MainWindow, method_name)

    def wrapper(self, *args, **kwargs):
        if getattr(self, "_fun_edition", False):
            _fun_unavailable(self, feature_name)
            return None
        return original(self, *args, **kwargs)

    wrapper.__name__ = method_name
    return wrapper


def _core_is_table_source(self) -> bool:
    if self.analysis is not None and getattr(self.analysis, "source_kind", "media") == "table":
        return True
    return bool(self.file_path) and is_table_file(self.file_path)


def _core_set_table_playing(self, playing: bool) -> None:
    self._table_transport_playing = bool(playing)
    self._table_transport_last_tick_perf = time.perf_counter()
    if hasattr(self, "play_button"):
        self.play_button.setText("Pause" if self._table_transport_playing else "Play")
    self._apply_frame_timer_settings()
    if self._table_transport_playing:
        if not self.frame_timer.isActive():
            self.frame_timer.start()
    else:
        if self.frame_timer.isActive():
            self.frame_timer.stop()
        self._refresh_visuals(immediate=True)
    self.analysis_tabs.set_playhead(self.current_time, force=True)


def _core_prepare_table_source(self, path: str) -> None:
    self.media_player.stop()
    self.media_player.setSource(QUrl())
    self._table_transport_playing = False
    self._table_transport_last_tick_perf = 0.0
    self.preview_tabs.setCurrentWidget(self.video_placeholder)
    self.preview_meta.setText(f"Tabular dataset\n{Path(path).name}")
    self.video_placeholder.setText(
        "Tabular dataset loaded.\n\n"
        "Scrub the timeline, press play to animate rows over time, or export a geometry-only MP4."
    )
    self._sync_timeline_range()


def _core_analyze_current_table(self) -> None:
    if not self.file_path:
        QMessageBox.information(self, APP_TITLE, "Choose a source file first.")
        return
    self._analysis_job_id += 1
    job_id = self._analysis_job_id
    self._analysis_running = True
    source_path = self.file_path
    self._set_status("Importing table and extracting feature geometry…", color=WARNING)

    def task() -> None:
        try:
            result = analysis_from_table_file(source_path)
        except Exception:
            self.worker_bridge.analysisError.emit(job_id, traceback.format_exc())
        else:
            self.worker_bridge.analysisFinished.emit(job_id, result)

    threading.Thread(target=task, daemon=True).start()


def _core_apply_default_mapping_for_analysis(self, analysis: AnalysisResult) -> None:
    if getattr(analysis, "source_kind", "media") != "table":
        return
    feature_names = list(analysis.features.keys())
    reserved = {
        "time",
        "t",
        "row_index",
        "column_mean",
        "column_spread",
        "magnitude",
        "delta_magnitude",
    }
    imported = [
        name
        for name in feature_names
        if name not in reserved and not name.startswith("pc") and not name.startswith("input_")
    ]
    x_name = imported[0] if len(imported) >= 1 else "pc1"
    y_name = imported[1] if len(imported) >= 2 else ("pc2" if "pc2" in analysis.features else x_name)
    z_name = imported[2] if len(imported) >= 3 else ("pc3" if "pc3" in analysis.features else "delta_magnitude")
    color_name = imported[3] if len(imported) >= 4 else "time"
    size_name = imported[4] if len(imported) >= 5 else "magnitude"

    self.x_edit.setText(x_name)
    self.y_edit.setText(y_name)
    self.z_edit.setText(z_name)
    self.color_edit.setText(color_name)
    self.size_edit.setText(size_name)
    self._set_display_label_fields({
        "x": FEATURE_FRIENDLY_LABELS.get(x_name, x_name.replace("_", " ").title()),
        "y": FEATURE_FRIENDLY_LABELS.get(y_name, y_name.replace("_", " ").title()),
        "z": FEATURE_FRIENDLY_LABELS.get(z_name, z_name.replace("_", " ").title()),
        "color": FEATURE_FRIENDLY_LABELS.get(color_name, color_name.replace("_", " ").title()),
        "size": FEATURE_FRIENDLY_LABELS.get(size_name, size_name.replace("_", " ").title()),
    })
    if hasattr(self, "preset_combo") and self.preset_combo.findText(TABLE_PRESET_NAME) >= 0:
        self.preset_combo.setCurrentText(TABLE_PRESET_NAME)


def _core_on_analysis_finished(self, job_id: int, result: object) -> None:
    if self._closing or job_id != self._analysis_job_id:
        return
    self._analysis_running = False
    self.analysis = result  # type: ignore[assignment]
    self.current_duration = float(self.analysis.times[-1]) if self.analysis.times.size > 0 else float(self.analysis.duration)
    if getattr(self.analysis, "source_kind", "media") == "table":
        self._core_apply_default_mapping_for_analysis(self.analysis)
        self.preview_tabs.setCurrentWidget(self.video_placeholder)
        self.preview_meta.setText(f"Tabular dataset\n{Path(self.file_path or self.analysis.source_path).name}")
    self.feature_text.setPlainText(format_feature_reference(self.analysis))
    self._sync_timeline_range()
    self.profile_store.append_diagnostic_log(f"[{datetime.now().isoformat(timespec='seconds')}] analysis complete: {self.current_duration:0.2f}s, {self.analysis.times.size} frames")
    self._clear_export_preview_session()
    if getattr(self.analysis, "source_kind", "media") == "table":
        self._set_status(
            f"Table import complete. Duration {self.current_duration:0.2f}s, {self.analysis.times.size} rows.",
            color=ACCENT,
        )
    else:
        self._set_status(
            f"Analysis complete. Duration {self.current_duration:0.2f}s, {self.analysis.times.size} frames.",
            color=ACCENT,
        )
    self._rebuild_geometry(reset_camera=True)


def _core_load_file(self, path: str) -> None:
    if not is_table_file(path):
        _original_load_file(self, path)
        return
    self._clear_export_preview_session()
    self.file_path = path
    self.file_label.setText(path)
    self.toolbar_file_label.setText(Path(path).name)
    self.preview_meta.setText(Path(path).name)
    self._recent_files = self.profile_store.append_recent_file(path)
    self._refresh_recent_files_menu()
    self.reset_time()
    self._core_prepare_table_source(path)
    self.analyze_current_table()
    self._schedule_autosave()


def _core_toggle_playback(self) -> None:
    if self._core_is_table_source():
        if self.analysis is None:
            return
        self._core_set_table_playing(not bool(getattr(self, "_table_transport_playing", False)))
        return
    _original_toggle_playback(self)


def _core_reset_time(self) -> None:
    if self._core_is_table_source():
        self._core_set_table_playing(False)
        self.current_time = 0.0
        if hasattr(self, "jump_to_spin"):
            self.jump_to_spin.setValue(0.0)
        self._refresh_visuals(immediate=True)
        return
    _original_reset_time(self)


def _core_seek_relative(self, delta_seconds: float) -> None:
    if self._core_is_table_source():
        duration = self._current_duration_seconds()
        target = max(0.0, min(float(self.current_time + float(delta_seconds)), duration))
        self.current_time = target
        if hasattr(self, "jump_to_spin"):
            self.jump_to_spin.setValue(self.current_time)
        self._refresh_visuals(immediate=True)
        return
    _original_seek_relative(self, delta_seconds)


def _core_jump_to_time_from_spin(self) -> None:
    if self._core_is_table_source():
        if not hasattr(self, "jump_to_spin"):
            return
        target = max(0.0, min(float(self.jump_to_spin.value()), self._current_duration_seconds()))
        self.current_time = target
        self._refresh_visuals(immediate=True)
        return
    _original_jump_to_time_from_spin(self)


def _core_on_slider_pressed(self) -> None:
    if self._core_is_table_source():
        self._dragging_timeline = True
        self._resume_after_seek = bool(getattr(self, "_table_transport_playing", False))
        self._core_set_table_playing(False)
        self._scrub_preview_timer.stop()
        return
    _original_on_slider_pressed(self)


def _core_on_slider_released(self) -> None:
    if self._core_is_table_source():
        self._scrub_preview_timer.stop()
        value = self.timeline_slider.value()
        self.current_time = float(value) / 1000.0
        self._dragging_timeline = False
        if hasattr(self, "jump_to_spin"):
            self.jump_to_spin.setValue(self.current_time)
        self._refresh_visuals(immediate=True)
        if self._resume_after_seek:
            self._core_set_table_playing(True)
        self._resume_after_seek = False
        return
    _original_on_slider_released(self)


def _core_on_frame_tick(self) -> None:
    if self._core_is_table_source() and bool(getattr(self, "_table_transport_playing", False)):
        if self._dragging_timeline:
            return
        now = time.perf_counter()
        last = float(getattr(self, "_table_transport_last_tick_perf", 0.0) or 0.0)
        delta = 1.0 / 30.0 if last <= 0.0 else max(1.0 / 120.0, min(0.15, now - last))
        self._table_transport_last_tick_perf = now
        duration = self._current_duration_seconds()
        self.current_time = min(duration, float(self.current_time) + float(delta))
        if hasattr(self, "jump_to_spin") and not self.jump_to_spin.hasFocus():
            self.jump_to_spin.setValue(self.current_time)
        if self.current_time >= duration - 1e-6:
            if self.loop_checkbox.isChecked() and duration > 0.0:
                self.current_time = 0.0
                self._table_transport_last_tick_perf = now
            else:
                self._core_set_table_playing(False)
        self._refresh_visuals()
        return
    _original_on_frame_tick(self)


def _core_update_transport_text(self) -> None:
    if self._core_is_table_source() and self.analysis is not None:
        duration = self._current_duration_seconds()
        idx = nearest_time_index(self.analysis.times, self.current_time) if self.analysis.times.size > 0 else 0
        idx = max(0, min(idx, int(max(0, self.analysis.times.size - 1))))
        self.time_label.setText(f"{self.current_time:0.02f} s / {duration:0.02f} s")
        self.geometry_hud.setText(f"{self.current_time:0.02f} s • row {idx + 1}/{max(1, self.analysis.times.size)}")
        if hasattr(self, "transport_info"):
            self.transport_info.setText(f"Tabular data • {len(self.analysis.features)} mapped features")
        return
    _original_update_transport_text(self)


def _core_stop_click_audition(self) -> None:
    if self._core_is_table_source():
        self._core_set_table_playing(False)
        return
    _original_stop_click_audition(self)


def _core_audition_time_at(self, seconds: float) -> None:
    if self._core_is_table_source():
        duration = self._current_duration_seconds()
        target = max(0.0, min(float(seconds), duration if duration > 0 else float(seconds)))
        self.current_time = target
        if hasattr(self, "jump_to_spin"):
            self.jump_to_spin.setValue(self.current_time)
        self._refresh_visuals(immediate=True)
        self._set_status(f"Jumped to {target:0.02f}s.", color=ACCENT)
        return
    _original_audition_time_at(self, seconds)


def _core_analysis_tabs_set_data(self, analysis: AnalysisResult, geometry: GeometryResult) -> None:
    if getattr(analysis, "source_kind", "media") != "table":
        self.tabs.setTabText(0, "Spectrogram")
        self.tabs.setTabText(1, "Chromagram")
        self.tabs.setTabText(2, "MFCC / Cepstrogram")
        return _original_analysis_tabs_set_data(self, analysis, geometry)

    self._analysis = analysis
    self._geometry = geometry
    self._last_playhead_bucket = None
    self._duration = float(analysis.times[-1]) if analysis.times.size > 0 else 0.0
    t0 = float(analysis.times[0]) if analysis.times.size > 0 else 0.0
    t1 = float(analysis.times[-1]) if analysis.times.size > 0 else max(1.0, float(analysis.duration))

    self.tabs.setTabText(0, "Column heatmap")
    self.tabs.setTabText(1, "Top 12 columns")
    self.tabs.setTabText(2, "Top 13 columns")
    self.tabs.setTabText(3, "Mapped traces")

    spec = self._downsample_matrix_cols(analysis.spectrogram_db)
    self.spectrogram_img.setImage(spec, autoLevels=True)
    self.spectrogram_img.setRect(QRectF(t0, 0.0, max(1e-6, t1 - t0), float(spec.shape[0])))
    self.spectrogram_plot.getPlotItem().setLimits(xMin=t0, xMax=t1)
    self.spectrogram_plot.getPlotItem().setYRange(0.0, float(spec.shape[0]), padding=0.0)
    self.spectrogram_plot.getPlotItem().setLabel("left", "Column")

    top12 = self._downsample_matrix_cols(analysis.chromagram)
    self.chroma_img.setImage(top12, autoLevels=True)
    self.chroma_img.setRect(QRectF(t0, 0.0, max(1e-6, t1 - t0), float(top12.shape[0])))
    self.chroma_plot.getPlotItem().setLimits(xMin=t0, xMax=t1)
    self.chroma_plot.getPlotItem().setYRange(0.0, float(top12.shape[0]), padding=0.0)
    self.chroma_plot.getPlotItem().setLabel("left", "Column")
    self.chroma_plot.getPlotItem().getAxis("left").setTicks([])

    top13 = self._downsample_matrix_cols(analysis.mfcc)
    self.mfcc_img.setImage(top13, autoLevels=True)
    self.mfcc_img.setRect(QRectF(t0, 0.0, max(1e-6, t1 - t0), float(top13.shape[0])))
    self.mfcc_plot.getPlotItem().setLimits(xMin=t0, xMax=t1)
    self.mfcc_plot.getPlotItem().setYRange(0.0, float(top13.shape[0]), padding=0.0)
    self.mfcc_plot.getPlotItem().setLabel("left", "Column")

    plot = self.traces_plot.getPlotItem()
    plot.clear()
    plot.addItem(self.traces_line)
    self.trace_curves.clear()
    active_mask = np.asarray(getattr(geometry, "active_mask_full", np.ones_like(analysis.times, dtype=bool)), dtype=bool).reshape(-1)
    if active_mask.size != analysis.times.size:
        active_mask = np.ones_like(analysis.times, dtype=bool)
    trace_specs = [
        (geometry.x_full, f"X: {geometry.labels['x']}", ACCENT),
        (geometry.y_full, f"Y: {geometry.labels['y']}", "#66d9ef"),
        (geometry.z_full, f"Z: {geometry.labels['z']}", "#c792ea"),
        (geometry.color_full, f"Color: {geometry.labels['color']}", "#ffcb6b"),
        (geometry.size_full, f"Size: {geometry.labels['size']}", "#f07178"),
    ]
    for values, label, color in trace_specs:
        series = np.asarray(values, dtype=np.float32).copy()
        series[~active_mask] = np.nan
        xs, ys = self._decimate_series(analysis.times, series)
        curve = plot.plot(xs, ys, pen=pg.mkPen(color, width=1.45), name=label)
        self.trace_curves.append(curve)
    plot.setLimits(xMin=t0, xMax=t1)
    plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
    self.set_playhead(float(t0), force=True)


MainWindow._apply_fun_edition = _apply_standalone_visualizer_mode

if FUN_EDITION:
    _original_mainwindow_init = MainWindow.__init__
    _original_load_file = MainWindow.load_file
    _original_toggle_playback = MainWindow.toggle_playback
    _original_reset_time = MainWindow.reset_time
    _original_seek_relative = MainWindow.seek_relative
    _original_jump_to_time_from_spin = MainWindow._jump_to_time_from_spin
    _original_on_slider_pressed = MainWindow._on_slider_pressed
    _original_on_slider_released = MainWindow._on_slider_released
    _original_on_frame_tick = MainWindow._on_frame_tick
    _original_update_transport_text = MainWindow._update_transport_text
    _original_stop_click_audition = MainWindow._stop_click_audition
    _original_audition_time_at = MainWindow._audition_time_at
    _original_analysis_tabs_set_data = AnalysisTabs.set_data

    def _fun_mainwindow_init(self, *args, **kwargs):
        self._fun_edition = True
        self._table_transport_playing = False
        self._table_transport_last_tick_perf = 0.0
        _original_mainwindow_init(self, *args, **kwargs)
        self._apply_fun_edition()

    MainWindow.__init__ = _fun_mainwindow_init
    MainWindow.export_render_dialog = _fun_export_render_dialog
    MainWindow._selected_export_range = _fun_selected_export_range
    MainWindow._maybe_offer_recovery_restore = _fun_disable_restore_prompt
    MainWindow._restore_last_session_if_enabled = _fun_disable_restore_prompt
    MainWindow._core_is_table_source = _core_is_table_source
    MainWindow._core_set_table_playing = _core_set_table_playing
    MainWindow._core_prepare_table_source = _core_prepare_table_source
    MainWindow._core_apply_default_mapping_for_analysis = _core_apply_default_mapping_for_analysis
    MainWindow.analyze_current_table = _core_analyze_current_table
    MainWindow._on_analysis_finished = _core_on_analysis_finished
    MainWindow.load_file = _core_load_file
    MainWindow.toggle_playback = _core_toggle_playback
    MainWindow.reset_time = _core_reset_time
    MainWindow.seek_relative = _core_seek_relative
    MainWindow._jump_to_time_from_spin = _core_jump_to_time_from_spin
    MainWindow._on_slider_pressed = _core_on_slider_pressed
    MainWindow._on_slider_released = _core_on_slider_released
    MainWindow._on_frame_tick = _core_on_frame_tick
    MainWindow._update_transport_text = _core_update_transport_text
    MainWindow._stop_click_audition = _core_stop_click_audition
    MainWindow._audition_time_at = _core_audition_time_at
    AnalysisTabs.set_data = _core_analysis_tabs_set_data

    for _method_name, _feature_name in (
        ("save_current_profile", "This command"),
        ("save_current_profile_as", "This command"),
        ("load_selected_profile", "This command"),
        ("delete_selected_profile", "This command"),
        ("configure_export_layout", "This command"),
        ("export_mapped_data_csv_dialog", "This command"),
        ("queue_current_export_dialog", "This command"),
        ("run_export_queue", "This command"),
        ("batch_export_dialog", "This command"),
        ("_start_live_monitor", "This command"),
        ("_stop_live_monitor", "This command"),
        ("_capture_live_baseline", "This command"),
        ("_clear_live_baseline", "This command"),
        ("_clear_live_events", "This command"),
    ):
        setattr(MainWindow, _method_name, _fun_make_unavailable_wrapper(_method_name, _feature_name))


# ---------- App startup ----------
def configure_qt_plugin_paths() -> None:
    plugin_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    if plugin_path:
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugin_path
        os.environ["QT_PLUGIN_PATH"] = plugin_path


def install_exception_hook() -> None:
    store = ProfileStore()
    try:
        faulthandler.enable()
    except Exception:
        pass

    def excepthook(exc_type, exc_value, exc_tb):
        trace = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(trace, file=sys.stderr)
        try:
            store.append_crash_log(trace)
        except Exception:
            pass
        try:
            QMessageBox.critical(None, APP_TITLE, trace[-6000:])
        except Exception:
            pass
    sys.excepthook = excepthook


def apply_app_theme(app: QApplication | None, theme_key: str = DEFAULT_THEME_KEY) -> str:
    resolved = _set_theme_globals(theme_key)
    if app is None:
        return resolved

    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(APP_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(INPUT_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_BG))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE_BG))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(INPUT_BG))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(SELECTION_TEXT))
    app.setPalette(palette)

    accent_start = ACCENT
    accent_end = "#5198ff"
    app.setStyleSheet(
        f"""
        QWidget {{
            background: {APP_BG};
            color: {TEXT};
        }}
        QMainWindow::separator {{
            background: {OUTLINE};
            width: 1px;
            height: 1px;
        }}
        QMenuBar, QMenu, QStatusBar {{
            background: {SURFACE_BG};
            color: {TEXT};
        }}
        QGroupBox {{
            border: 1px solid {OUTLINE};
            border-radius: 8px;
            margin-top: 12px;
            padding: 10px 8px 8px 8px;
            background: {PANEL_BG};
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: {TEXT};
        }}
        QTabWidget::pane {{
            border: 1px solid {OUTLINE};
            border-radius: 8px;
            background: {PANEL_BG};
        }}
        QTabBar::tab {{
            background: {SURFACE_BG};
            color: {MUTED_TEXT};
            border: 1px solid {OUTLINE};
            padding: 8px 12px;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background: {INPUT_BG};
            color: {TEXT};
        }}
        QPushButton, QToolButton {{
            background: {INPUT_BG};
            border: 1px solid {OUTLINE};
            border-radius: 7px;
            padding: 6px 10px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background: {INPUT_ACTIVE_BG};
        }}
        QPushButton:pressed, QToolButton:pressed {{
            background: {INPUT_ACTIVE_BG};
        }}
        QPushButton#AccentAction, QToolButton#AccentAction {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent_start}, stop:1 {accent_end});
            color: {SELECTION_TEXT};
            border: none;
            font-weight: 700;
            padding: 7px 12px;
        }}
        QPushButton#AccentAction:hover, QToolButton#AccentAction:hover {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent_start}, stop:1 #5198ff);
        }}
        QLabel, QCheckBox {{
            background: transparent;
        }}
        QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
            background: {INPUT_BG};
            border: 1px solid {OUTLINE};
            border-radius: 6px;
            padding: 5px 6px;
            selection-background-color: {ACCENT};
            selection-color: {SELECTION_TEXT};
        }}
        QScrollArea, QSplitter, QFrame#LeftPanel {{
            background: transparent;
            border: none;
        }}
        QFrame#TopBar, QFrame#TransportBar {{
            background: {SURFACE_BG};
            border: 1px solid {OUTLINE};
            border-radius: 10px;
        }}
        QFrame#SidebarBrandCard, QFrame#QuickActionCard, QFrame#BrandCard {{
            background: {PANEL_BG};
            border: 1px solid {OUTLINE};
            border-radius: 8px;
        }}
        QFrame#BrandCardDivider {{
            background: {OUTLINE};
            border: none;
            min-width: 1px;
            max-width: 1px;
            margin: 6px 0;
        }}
        QScrollBar:vertical {{
            background: {INPUT_BG};
            width: 12px;
            margin: 2px 0 2px 0;
            border-radius: 6px;
        }}
        QScrollBar::handle:vertical {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {accent_start}, stop:1 {accent_end});
            min-height: 28px;
            border-radius: 6px;
            margin: 1px;
        }}
        QScrollBar:horizontal {{
            background: {INPUT_BG};
            height: 12px;
            margin: 0 2px 0 2px;
            border-radius: 6px;
        }}
        QScrollBar::handle:horizontal {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent_start}, stop:1 {accent_end});
            min-width: 28px;
            border-radius: 6px;
            margin: 1px;
        }}
        QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
            background: transparent;
            border: none;
        }}
        QSplitter::handle {{
            background: {INPUT_BG};
            border-radius: 4px;
        }}
        QSplitter::handle:hover {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {accent_start}, stop:1 {accent_end});
        }}
        QSplitter::handle:horizontal {{
            width: 8px;
            margin: 0 2px;
        }}
        QSplitter::handle:vertical {{
            height: 8px;
            margin: 2px 0;
        }}
        QSlider::groove:horizontal {{
            border: none;
            height: 8px;
            background: {INPUT_BG};
            border-radius: 4px;
        }}
        QSlider::sub-page:horizontal {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent_start}, stop:1 {accent_end});
            border-radius: 4px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            margin: -5px 0;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent_start}, stop:1 {accent_end});
            border: 1px solid rgba(255, 255, 255, 0.18);
            border-radius: 8px;
        }}
        QCheckBox {{
            spacing: 8px;
        }}
        QLabel#GeometryHud {{
            color: {TEXT};
            background: transparent;
            padding-left: 4px;
        }}
        QLabel#TransportInfo {{
            color: {MUTED_TEXT};
        }}
        QLabel#ToolbarFileLabel {{
            color: {TEXT};
            font-weight: 600;
        }}
        QLabel#BrandCardCaption {{
            color: {MUTED_TEXT};
            font-size: 11px;
            font-weight: 600;
        }}
        QLabel#BrandWordmark {{
            background: transparent;
        }}
        QToolTip {{
            background: {SURFACE_BG};
            color: {TEXT};
            border: 1px solid {OUTLINE};
        }}
        """
    )
    return resolved

def configure_opengl() -> None:
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(2)
    QSurfaceFormat.setDefaultFormat(fmt)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    use_software_gl = os.environ.get("METRIQ_VISUALIZER_SOFTWARE_GL", "0") == "1"
    cleaned_argv = [argv[0]]
    for arg in argv[1:]:
        if arg == "--software-gl":
            use_software_gl = True
        else:
            cleaned_argv.append(arg)
    argv = cleaned_argv
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")
    pg.setConfigOptions(antialias=True, imageAxisOrder="row-major")
    configure_qt_plugin_paths()
    configure_opengl()
    install_exception_hook()
    if use_software_gl:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    else:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_CompressHighFrequencyEvents, True)
    except Exception:
        pass
    app = QApplication(argv)
    apply_app_theme(app, DEFAULT_THEME_KEY)
    initial_file = argv[1] if len(argv) > 1 else None
    window = MainWindow(initial_file=initial_file)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
