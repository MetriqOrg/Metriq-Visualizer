# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Bottom-docked scientific and source panels for Metriq Visualizer."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from metriq_visualizer_core import AnalysisResult, GeometryResult

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
except ImportError:  # pragma: no cover
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg  # type: ignore[no-redef]

try:
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
except ImportError:  # pragma: no cover - minimal PySide installations
    QMediaPlayer = Any  # type: ignore[misc,assignment]
    QVideoWidget = None  # type: ignore[assignment]

BACKGROUND = "#070b11"
SURFACE = "#0b121b"
TEXT = "#dce8ed"
MUTED = "#8299a4"
GRID = "#233743"
CURSOR = "#4ce3ad"
TRACE_COLORS = ("#5fa6f7", "#4ce3ad", "#e9bd55", "#c48bf2", "#ef7f7f")
METRIQ_CMAP = LinearSegmentedColormap.from_list(
    "metriq_spectrum",
    ["#070b11", "#0c2633", "#07566b", "#07966a", "#53d39a", "#5fa6f7", "#e6f2ff"],
)


def _normalized(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 0:
        return array
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return np.zeros_like(array)
    low, high = np.percentile(finite, [2.0, 98.0])
    if high <= low + 1e-12:
        return np.zeros_like(array)
    return np.clip((np.nan_to_num(array, nan=low) - low) / (high - low), 0.0, 1.0)


class AnalysisCanvas(FigureCanvasQTAgg):
    """Compact Matplotlib panel with a synchronized time cursor."""

    def __init__(self, mode: str, parent: QWidget | None = None) -> None:
        self.mode = str(mode)
        self.figure = Figure(figsize=(8.0, 2.1), dpi=100)
        self.figure.patch.set_facecolor(BACKGROUND)
        super().__init__(self.figure)
        self.setParent(parent)
        self.setMinimumHeight(150)
        self.analysis: AnalysisResult | None = None
        self.geometry: GeometryResult | None = None
        self.cursor: Any = None
        self.axis: Any = None
        self._last_time = -1.0
        self._pending_data: tuple | None = None
        self._pending_cursor_time = 0.0
        self._cursor_background = None
        self._cursor_background_key = None
        self._cursor_artists: list[Any] = []
        self._cursor_dirty = False
        self.cursor_cache_limit_bytes = 8 * 1024 * 1024
        self.mpl_connect("draw_event", self._capture_cursor_background)
        self.mpl_connect("resize_event", self._invalidate_cursor_background)
        self._build_empty("NO ANALYSIS")

    def _style_axis(self, axis: Any) -> None:
        axis.set_facecolor(BACKGROUND)
        axis.tick_params(colors=MUTED, labelsize=7, length=2)
        axis.xaxis.label.set_color(MUTED)
        axis.yaxis.label.set_color(MUTED)
        axis.title.set_color(TEXT)
        for spine in axis.spines.values():
            spine.set_color(GRID)
        axis.grid(False)

    def _build_empty(self, message: str) -> None:
        self._invalidate_cursor_background()
        self._cursor_artists = []
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        self._style_axis(axis)
        axis.set_xticks([])
        axis.set_yticks([])
        axis.text(0.5, 0.5, message, transform=axis.transAxes, ha="center", va="center", color=MUTED, family="monospace")
        self.axis = axis
        self.cursor = None
        self.figure.subplots_adjust(left=0.025, right=0.995, bottom=0.12, top=0.92)
        self.draw_idle()

    def set_data(self, analysis: AnalysisResult | None, geometry: GeometryResult | None = None) -> None:
        self._pending_data = None
        self._invalidate_cursor_background()
        self._cursor_artists = []
        self.analysis = analysis
        self.geometry = geometry
        if analysis is None:
            self._build_empty("NO ANALYSIS")
            return
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        self.axis = axis
        self._style_axis(axis)
        duration = max(0.001, float(analysis.duration))
        mode = self.mode.casefold()
        if mode == "waveform":
            values = np.asarray(analysis.waveform, dtype=np.float64).reshape(-1)
            if values.size:
                maximum = 60_000
                if values.size > maximum:
                    indices = np.linspace(0, values.size - 1, maximum, dtype=np.int64)
                    values = values[indices]
                times = np.linspace(0.0, duration, values.size)
                axis.plot(times, values, color=TRACE_COLORS[1], linewidth=0.65, alpha=0.86)
                axis.fill_between(times, 0.0, values, color=TRACE_COLORS[1], alpha=0.08)
            axis.set_ylim(-1.05, 1.05)
            axis.set_ylabel("AMPLITUDE", fontsize=7)
        elif mode == "spectrogram":
            matrix = np.asarray(analysis.spectrogram, dtype=np.float64)
            if matrix.size:
                frequencies = np.asarray(analysis.spectrogram_frequencies, dtype=np.float64).reshape(-1)
                top = float(frequencies[-1]) if frequencies.size else float(matrix.shape[0])
                axis.imshow(matrix, origin="lower", aspect="auto", extent=(0.0, duration, 0.0, top), cmap=METRIQ_CMAP, interpolation="bilinear")
                axis.set_ylabel("HZ", fontsize=7)
            else:
                axis.text(0.5, 0.5, "SPECTROGRAM UNAVAILABLE", transform=axis.transAxes, ha="center", va="center", color=MUTED)
        elif mode == "chromagram":
            matrix = np.asarray(analysis.chromagram, dtype=np.float64)
            if matrix.size:
                axis.imshow(matrix, origin="lower", aspect="auto", extent=(0.0, duration, 1.0, 12.0), cmap=METRIQ_CMAP, interpolation="nearest")
                axis.set_yticks([1, 4, 7, 10, 12])
                axis.set_ylabel("PITCH CLASS", fontsize=7)
            else:
                axis.text(0.5, 0.5, "CHROMAGRAM UNAVAILABLE", transform=axis.transAxes, ha="center", va="center", color=MUTED)
        elif mode == "mfcc":
            matrix = np.asarray(analysis.mfcc, dtype=np.float64)
            if matrix.size:
                axis.imshow(matrix, origin="lower", aspect="auto", extent=(0.0, duration, 1.0, float(matrix.shape[0])), cmap=METRIQ_CMAP, interpolation="nearest")
                axis.set_ylabel("COEFFICIENT", fontsize=7)
            else:
                axis.text(0.5, 0.5, "MFCC UNAVAILABLE", transform=axis.transAxes, ha="center", va="center", color=MUTED)
        elif mode == "traces":
            if geometry is not None and geometry.times_full.size:
                series = (
                    (geometry.x_full, "X"),
                    (geometry.y_full, "Y"),
                    (geometry.z_full, "Z"),
                    (geometry.color_full, "COLOR"),
                )
                for index, (values, label) in enumerate(series):
                    axis.plot(geometry.times_full, _normalized(values), linewidth=0.8, alpha=0.85, label=label, color=TRACE_COLORS[index])
                legend = axis.legend(loc="upper right", frameon=False, ncol=4, fontsize=6.5, handlelength=1.2)
                for text in legend.get_texts():
                    text.set_color(MUTED)
                axis.set_ylim(-0.03, 1.03)
                axis.set_ylabel("NORMALIZED", fontsize=7)
            else:
                axis.text(0.5, 0.5, "MAPPED TRACES APPEAR AFTER GEOMETRY BUILD", transform=axis.transAxes, ha="center", va="center", color=MUTED)
        axis.set_xlim(0.0, duration)
        axis.set_xlabel("TIME / SECONDS", fontsize=7)
        self.cursor = axis.axvline(0.0, color=CURSOR, linewidth=1.05, alpha=0.94)
        self.figure.subplots_adjust(left=0.055, right=0.995, bottom=0.23, top=0.94)
        self._last_time = 0.0
        self._cursor_dirty = True
        if self.supports_blit:
            # Preserve normal artist stacking: spines, titles and the trace
            # legend are foreground too, not baked twice beneath the cursor.
            artists = sorted(axis.get_children(), key=lambda artist: artist.get_zorder())
            self._cursor_artists = artists[artists.index(self.cursor):]
            for artist in self._cursor_artists:
                artist.set_animated(True)
        self.draw_idle()

    def defer_data(self, analysis: AnalysisResult | None, geometry: GeometryResult | None = None) -> None:
        """Retain source references, not unused hidden plot rasters/artists."""
        self.analysis, self.geometry = analysis, geometry
        self._cursor_artists = []
        self.cursor = self.axis = None
        self._invalidate_cursor_background()
        self.figure.clear()
        self._pending_data = (analysis, geometry)
        self._pending_cursor_time = 0.0
        # Any old queued idle draw now has nothing useful to render. Showing,
        # grabbing or explicitly drawing this panel will materialize it below.
        self._draw_pending = False

    def _materialize_data(self) -> None:
        if self._pending_data is not None:
            data, seconds = self._pending_data, self._pending_cursor_time
            self._pending_data = None
            self.set_data(*data)
            self.set_time(seconds, draw=False)

    def draw(self) -> None:
        self._materialize_data()
        super().draw()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        self._materialize_data()
        super().paintEvent(event)

    def _invalidate_cursor_background(self, _event: Any = None) -> None:
        self._cursor_background = None
        self._cursor_background_key = None
        self._cursor_dirty = True

    def _cursor_raster_key(self) -> tuple:
        return (*self.figure.bbox.bounds, self.figure.dpi, self.device_pixel_ratio)

    def _capture_cursor_background(self, _event: Any) -> None:
        if self.is_saving() or not self._cursor_artists:
            self._invalidate_cursor_background()
            return
        self._cursor_background = None
        if self.figure.bbox.width * self.figure.bbox.height * 4 <= self.cursor_cache_limit_bytes:
            self._cursor_background = self.copy_from_bbox(self.figure.bbox)
            self._cursor_background_key = self._cursor_raster_key()
        # The artists are excluded from the normal draw. Draw them once in
        # their original order, including when caching is over the memory cap.
        for artist in self._cursor_artists:
            self.figure.draw_artist(artist)
        self._cursor_dirty = False
        self.figure.stale = False

    def _paint_cursor(self) -> None:
        if not self._cursor_dirty:
            return
        if (self._cursor_background is None or self.figure.stale
                or getattr(self, "_draw_pending", False)
                or self._cursor_background_key != self._cursor_raster_key()):
            self.draw_idle()
            return
        self.restore_region(self._cursor_background)
        for artist in self._cursor_artists:
            self.figure.draw_artist(artist)
        self._cursor_dirty = False
        self.figure.stale = False
        self.blit(self.figure.bbox)

    def ensure_current(self) -> None:
        """Flush deferred plot/cursor work only when a consumer needs pixels."""
        self._materialize_data()
        if getattr(self, "_draw_pending", False):
            self._draw_idle()
        if self.figure.stale or (self._cursor_dirty and self._cursor_background is None):
            self.draw()
        elif self._cursor_dirty:
            self._paint_cursor()

    def snapshot(self):
        """Materialize a deferred cursor for stage output, including hidden tabs."""
        self.ensure_current()
        return self.grab()

    def set_time(self, seconds: float, *, draw: bool = True) -> None:
        if self._pending_data is not None:
            self._pending_cursor_time = max(0.0, float(seconds))
            if not draw:
                return
            self._materialize_data()
        if self.cursor is None:
            return
        value = max(0.0, float(seconds))
        if abs(value - self._last_time) >= 1e-4:
            self._last_time = value
            self.cursor.set_xdata([value, value])
            self._cursor_dirty = True
        # draw=False may have advanced the state already. A later draw=True at
        # that same time must still paint the deferred cursor.
        if draw:
            self._paint_cursor()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._invalidate_cursor_background()
        super().hideEvent(event)


class SourcePanel(QWidget):
    """Video output when present, waveform otherwise."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stack = QStackedWidget(self)
        self.waveform = AnalysisCanvas("waveform", self)
        self.message = QLabel("SOURCE PREVIEW UNAVAILABLE")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setObjectName("Subtle")
        self.video_widget: Any = None
        if QVideoWidget is not None:
            self.video_widget = QVideoWidget(self)
            self.video_widget.setMinimumHeight(150)
            self.video_widget.setStyleSheet("background:#070b11;")
            self.stack.addWidget(self.video_widget)
        self.stack.addWidget(self.waveform)
        self.stack.addWidget(self.message)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)
        self.analysis: AnalysisResult | None = None
        self.media_player: Any = None

    def set_media_player(self, player: Any) -> None:
        self.media_player = player
        if self.video_widget is not None and player is not None:
            with suppress(Exception):
                player.setVideoOutput(self.video_widget)

    def set_data(self, analysis: AnalysisResult | None, geometry: GeometryResult | None = None) -> None:
        self.analysis = analysis
        if analysis is not None and analysis.has_video and self.video_widget is not None:
            self.waveform.defer_data(analysis, geometry)
        else:
            self.waveform.set_data(analysis, geometry)
        if analysis is None:
            self.stack.setCurrentWidget(self.message)
        elif bool(analysis.has_video) and self.video_widget is not None:
            self.stack.setCurrentWidget(self.video_widget)
        else:
            self.stack.setCurrentWidget(self.waveform)

    def snapshot(self):
        if self.stack.currentWidget() is self.waveform:
            self.waveform.ensure_current()
        return self.grab()

    def set_time(self, seconds: float, *, draw: bool = True) -> None:
        self.waveform.set_time(seconds, draw=draw and self.stack.currentWidget() is self.waveform)


class AnalysisDockWidget(QWidget):
    """A compact, collapsible panel dock placed below the 3D viewport."""

    collapsedChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AnalysisDock")
        self.setMinimumHeight(190)
        self._expanded_height = 245
        self._collapsed = False
        self._current_time = 0.0

        header = QHBoxLayout()
        header.setContentsMargins(8, 2, 8, 2)
        self.title = QLabel("ANALYSIS DOCK / SOURCE + SCIENTIFIC PANELS")
        self.title.setObjectName("Eyebrow")
        header.addWidget(self.title)
        header.addStretch(1)
        self.collapse_button = QPushButton("Collapse")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        header.addWidget(self.collapse_button)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.source_panel = SourcePanel(self)
        self.spectrogram = AnalysisCanvas("spectrogram", self)
        self.chromagram = AnalysisCanvas("chromagram", self)
        self.mfcc = AnalysisCanvas("mfcc", self)
        self.traces = AnalysisCanvas("traces", self)
        self.tabs.addTab(self.source_panel, "Source")
        self.tabs.addTab(self.spectrogram, "Spectrogram")
        self.tabs.addTab(self.chromagram, "Chromagram")
        self.tabs.addTab(self.mfcc, "MFCC")
        self.tabs.addTab(self.traces, "Mapped traces")
        self.tabs.currentChanged.connect(self._tab_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)

    def set_media_player(self, player: Any) -> None:
        self.source_panel.set_media_player(player)

    def set_data(self, analysis: AnalysisResult | None, geometry: GeometryResult | None = None) -> None:
        self.source_panel.set_data(analysis, geometry)
        current = self.tabs.currentWidget()
        for panel in (self.spectrogram, self.chromagram, self.mfcc, self.traces):
            if panel is current and not self._collapsed:
                panel.set_data(analysis, geometry)
            else:
                panel.defer_data(analysis, geometry)

    def update_geometry(self, analysis: AnalysisResult | None, geometry: GeometryResult | None) -> None:
        if self.tabs.currentWidget() is self.traces and not self._collapsed:
            self.traces.set_data(analysis, geometry)
        else:
            self.traces.defer_data(analysis, geometry)
        # A changed mapping does not change the source waveform.
        if self.source_panel.waveform.analysis is not analysis:
            self.source_panel.waveform.set_data(analysis, geometry)

    def set_time(self, seconds: float, *, draw: bool = True) -> None:
        self._current_time = max(0.0, float(seconds))
        current = self.tabs.currentWidget()
        draw = draw and not self._collapsed
        if current is self.source_panel:
            self.source_panel.set_time(self._current_time, draw=draw)
        elif isinstance(current, AnalysisCanvas):
            current.set_time(self._current_time, draw=draw)
        # Keep every cursor correct when the user changes tabs, without forcing
        # five canvas redraws for every playback frame.
        for panel in (self.spectrogram, self.chromagram, self.mfcc, self.traces):
            if panel is not current:
                panel.set_time(self._current_time, draw=False)

    @Slot(int)
    def _tab_changed(self, _index: int) -> None:
        current = self.tabs.currentWidget()
        if current is self.source_panel:
            self.source_panel.set_time(self._current_time)
            if self.source_panel.stack.currentWidget() is self.source_panel.waveform:
                self.source_panel.waveform.draw_idle()
        elif isinstance(current, AnalysisCanvas):
            current._materialize_data()
            current.set_time(self._current_time, draw=False)
            current.draw_idle()

    @Slot()
    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        self.tabs.setVisible(not self._collapsed)
        self.collapse_button.setText("Expand" if self._collapsed else "Collapse")
        if self._collapsed:
            self._expanded_height = max(self._expanded_height, self.height())
            self.setMinimumHeight(34)
            self.setMaximumHeight(42)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setMaximumHeight(16_777_215)
            self.setMinimumHeight(190)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.updateGeometry()
        self.collapsedChanged.emit(self._collapsed)

    @property
    def is_collapsed(self) -> bool:
        return bool(self._collapsed)

    @property
    def preferred_expanded_height(self) -> int:
        return int(max(190, self._expanded_height))


__all__ = ["AnalysisCanvas", "AnalysisDockWidget", "SourcePanel"]
