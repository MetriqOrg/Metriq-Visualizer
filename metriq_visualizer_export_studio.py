# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Professional, local-only export and composition studio."""

from __future__ import annotations

import threading
import traceback
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSignalBlocker, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import metriq_visualizer_layout as layout_module
from metriq_visualizer_export_pipeline import (
    ENCODER_MODES,
    FORMAT_BY_KEY,
    FORMAT_DEFINITIONS,
    RESOLUTION_PRESETS,
    ExportCancelled,
    ExportProfile,
    export_visualization,
    load_export_profile,
    output_path_for_profile,
    save_export_profile,
)
from metriq_visualizer_layout import LAYOUT_ITEM_ORDER, LAYOUT_ITEM_TITLES, ExportLayoutSpec
from metriq_visualizer_render import ExportOptions, ExportPreviewSession
from metriq_visualizer_theme import CutCornerFrame, current_palette, cut_corner_path

LAYOUT_PRESETS: tuple[tuple[str, str], ...] = (
    ("Balanced", "balanced_export_layout"),
    ("Geometry focus", "geometry_focus_export_layout"),
    ("Analysis focus", "analysis_focus_export_layout"),
    ("Overlay", "overlay_export_layout"),
    ("Social vertical", "social_vertical_export_layout"),
)

ITEM_ACCENTS = {
    "geometry": "#06a269",
    "preview": "#5fa6f7",
    "spectrogram": "#0a91a7",
    "chromagram": "#8bcf70",
    "mfcc": "#8e84df",
    "traces": "#e6bd62",
}


def _image_from_rgba(frame: np.ndarray) -> QImage:
    array = np.ascontiguousarray(frame, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] != 4:
        raise ValueError("Preview frame must be RGBA.")
    height, width, _channels = array.shape
    return QImage(array.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def _layout_factory(function_name: str) -> ExportLayoutSpec:
    factory = getattr(layout_module, function_name, None)
    if callable(factory):
        return factory()
    return layout_module.default_export_layout()


class LayoutCanvas(QWidget):
    """Direct-manipulation editor for normalized ExportLayoutSpec rectangles."""

    layoutChanged = Signal(object)
    selectionChanged = Signal(str)
    interactionStarted = Signal()
    interactionFinished = Signal()

    HANDLE_RADIUS = 10.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.layout_spec = layout_module.default_export_layout()
        self.output_width = 1920
        self.output_height = 1080
        self.selected = "geometry"
        self.snap_enabled = True
        self.snap_divisions = 24
        self.show_safe_area = True
        self._operation: str | None = None
        self._press_point = QPointF()
        self._origin: tuple[float, float, float, float] | None = None

    def set_layout_spec(self, layout: ExportLayoutSpec, *, emit: bool = False) -> None:
        self.layout_spec = layout.clone().clamp()
        if self.selected not in LAYOUT_ITEM_ORDER:
            self.selected = "geometry"
        self.update()
        if emit:
            self.layoutChanged.emit(self.layout_spec.clone())

    def set_output_size(self, width: int, height: int) -> None:
        self.output_width = max(1, int(width))
        self.output_height = max(1, int(height))
        self.update()

    def select_item(self, name: str) -> None:
        if name not in LAYOUT_ITEM_ORDER:
            return
        self.selected = name
        self.selectionChanged.emit(name)
        self.update()

    def _canvas_rect(self) -> QRectF:
        available = QRectF(self.rect()).adjusted(24, 24, -24, -24)
        aspect = self.output_width / max(1, self.output_height)
        if available.width() / max(1.0, available.height()) > aspect:
            height = available.height()
            width = height * aspect
        else:
            width = available.width()
            height = width / aspect
        return QRectF(
            available.center().x() - width / 2,
            available.center().y() - height / 2,
            width,
            height,
        )

    def _screen_rect(self, name: str) -> QRectF:
        canvas = self._canvas_rect()
        item = self.layout_spec.item(name)
        return QRectF(
            canvas.left() + item.x * canvas.width(),
            canvas.top() + item.y * canvas.height(),
            item.w * canvas.width(),
            item.h * canvas.height(),
        )

    def _hit_item(self, point: QPointF) -> str | None:
        # Reverse fixed render order so the visually top-most panel wins.
        for name in reversed(self.layout_spec.order):
            item = self.layout_spec.item(name)
            if item.enabled and self._screen_rect(name).contains(point):
                return name
        return None

    def _is_resize_handle(self, point: QPointF, name: str) -> bool:
        rect = self._screen_rect(name)
        handle = QRectF(
            rect.right() - self.HANDLE_RADIUS,
            rect.bottom() - self.HANDLE_RADIUS,
            self.HANDLE_RADIUS * 2,
            self.HANDLE_RADIUS * 2,
        )
        return handle.contains(point)

    def _snap(self, value: float) -> float:
        if not self.snap_enabled:
            return value
        return round(value * self.snap_divisions) / self.snap_divisions

    def _commit_change(self) -> None:
        self.layout_spec.clamp()
        self.layoutChanged.emit(self.layout_spec.clone())
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = event.position()
        hit = self._hit_item(point)
        if hit is None:
            return
        self.select_item(hit)
        item = self.layout_spec.item(hit)
        self._operation = "resize" if self._is_resize_handle(point, hit) else "move"
        self._press_point = point
        self._origin = (item.x, item.y, item.w, item.h)
        self.interactionStarted.emit()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._operation is None or self._origin is None:
            hit = self._hit_item(event.position())
            if hit and self._is_resize_handle(event.position(), hit):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()
            return
        canvas = self._canvas_rect()
        if canvas.width() <= 0 or canvas.height() <= 0:
            return
        dx = (event.position().x() - self._press_point.x()) / canvas.width()
        dy = (event.position().y() - self._press_point.y()) / canvas.height()
        x, y, w, h = self._origin
        item = self.layout_spec.item(self.selected)
        if self._operation == "move":
            item.x = self._snap(x + dx)
            item.y = self._snap(y + dy)
        else:
            item.w = self._snap(w + dx)
            item.h = self._snap(h + dy)
        item.clamp()
        self._commit_change()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            had_operation = self._operation is not None
            self._operation = None
            self._origin = None
            if had_operation:
                self.interactionFinished.emit()
            event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            super().keyPressEvent(event)
            return
        item = self.layout_spec.item(self.selected)
        step = 0.001 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.01
        if event.key() == Qt.Key.Key_Left:
            item.x -= step
        elif event.key() == Qt.Key.Key_Right:
            item.x += step
        elif event.key() == Qt.Key.Key_Up:
            item.y -= step
        else:
            item.y += step
        item.clamp()
        self._commit_change()
        event.accept()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(p.background))
        canvas = self._canvas_rect()
        painter.fillPath(cut_corner_path(canvas, 12), QColor(p.surface_raised))
        painter.setPen(QPen(QColor(p.border_strong), 1.2))
        painter.drawPath(cut_corner_path(canvas, 12))
        painter.save()
        painter.setClipRect(canvas)
        painter.setPen(QPen(QColor(p.grid), 1))
        for index in range(1, self.snap_divisions):
            x = canvas.left() + canvas.width() * index / self.snap_divisions
            painter.drawLine(QPointF(x, canvas.top()), QPointF(x, canvas.bottom()))
        for index in range(1, max(2, round(self.snap_divisions / max(0.5, self.output_width / self.output_height)))):
            divisions_y = max(2, round(self.snap_divisions / max(0.5, self.output_width / self.output_height)))
            y = canvas.top() + canvas.height() * index / divisions_y
            painter.drawLine(QPointF(canvas.left(), y), QPointF(canvas.right(), y))
        painter.restore()
        if self.show_safe_area and self.layout_spec.safe_area_percent > 0:
            inset_x = canvas.width() * self.layout_spec.safe_area_percent / 100.0
            inset_y = canvas.height() * self.layout_spec.safe_area_percent / 100.0
            safe_rect = canvas.adjusted(inset_x, inset_y, -inset_x, -inset_y)
            painter.setPen(QPen(QColor("#e6bd62"), 1.0, Qt.PenStyle.DashLine))
            painter.drawRect(safe_rect)

        for index, name in enumerate(self.layout_spec.order, start=1):
            item = self.layout_spec.item(name)
            if not item.enabled:
                continue
            rect = self._screen_rect(name)
            accent = QColor(ITEM_ACCENTS[name])
            fill = QColor(accent)
            fill.setAlpha(35 if name != self.selected else 58)
            painter.fillRect(rect, fill)
            painter.setPen(QPen(accent if name == self.selected else QColor(p.border_strong), 2.0 if name == self.selected else 1.0))
            painter.drawRect(rect)
            painter.setPen(QColor(p.text))
            painter.drawText(
                rect.adjusted(8, 5, -8, -5),
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                f"{index:02d} / {LAYOUT_ITEM_TITLES.get(name, name).upper()}",
            )
            if name == self.selected:
                painter.fillRect(
                    QRectF(rect.right() - 7, rect.bottom() - 7, 14, 14),
                    accent,
                )
        painter.setPen(QColor(p.muted))
        painter.drawText(
            QRectF(0, self.height() - 22, self.width(), 18),
            Qt.AlignmentFlag.AlignCenter,
            f"{self.output_width} × {self.output_height} · drag to move · lower-right handle to resize",
        )
        painter.end()


class ExportWorker(QObject):
    progress = Signal(float, str)
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        analysis: Any,
        geometry: Any,
        render_options: ExportOptions,
        profile: ExportProfile,
        output_path: Path,
    ) -> None:
        super().__init__()
        self.analysis = analysis
        self.geometry = geometry
        self.render_options = render_options
        self.profile = profile
        self.output_path = output_path
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = export_visualization(
                self.analysis,
                self.geometry,
                self.render_options,
                self.profile,
                self.output_path,
                cancel_event=self.cancel_event,
                progress_callback=lambda value, text: self.progress.emit(float(value), str(text)),
            )
            self.finished.emit(str(result))
        except ExportCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.failed.emit(details[-7000:])


class ExportStudioDialog(QDialog):
    """Layout, preview, profile, range, codec, and export controls in one dialog."""

    def __init__(
        self,
        analysis: Any,
        geometry: Any,
        render_options: ExportOptions,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.analysis = analysis
        self.geometry = geometry
        audio_path = Path(str(getattr(analysis, "audio_path", "") or "")).expanduser()
        self.has_source_audio = bool(str(audio_path) and audio_path.is_file())
        self._audio_preference = self.has_source_audio
        self.base_options = deepcopy(render_options)
        self.layout_spec = render_options.layout.clone().clamp()
        self._undo_stack: list[ExportLayoutSpec] = []
        self._redo_stack: list[ExportLayoutSpec] = []
        self._history_guard = False
        self._canvas_interaction_active = False
        self.preview_session: ExportPreviewSession | None = None
        self.preview_dirty = True
        self.thread: QThread | None = None
        self.worker: ExportWorker | None = None
        self.output_path: Path | None = None
        self._video_quality = 18
        self._jpeg_quality = 92
        self._last_format_key: str | None = None
        self.setWindowTitle("Metriq Export Studio")
        self.resize(1480, 900)
        self.setMinimumSize(1120, 700)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(140)
        self._preview_timer.timeout.connect(self._render_preview)
        self._build_ui()
        self._populate_from_options()
        self._schedule_preview(recreate=True)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = CutCornerFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 10, 18, 10)
        title_box = QVBoxLayout()
        eyebrow = QLabel("EXPORT / LOCAL RENDER PIPELINE")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Composition & Delivery")
        title.setObjectName("Title")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)
        self.load_profile_button = QPushButton("Load profile")
        self.load_profile_button.clicked.connect(self._load_profile)
        self.save_profile_button = QPushButton("Save profile")
        self.save_profile_button.clicked.connect(self._save_profile)
        header_layout.addWidget(self.load_profile_button)
        header_layout.addWidget(self.save_profile_button)
        root.addWidget(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_settings_panel())
        splitter.addWidget(self._build_workspace())
        splitter.addWidget(self._build_inspector())
        splitter.setSizes([350, 790, 300])
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        footer = CutCornerFrame()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 9, 14, 9)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("Ready")
        self.progress.setMinimumWidth(380)
        self.cancel_button = QPushButton("Cancel render")
        self.cancel_button.setProperty("danger", True)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_export)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        self.export_button = QPushButton("Export")
        self.export_button.setProperty("accent", True)
        self.export_button.clicked.connect(self._begin_export)
        footer_layout.addWidget(self.progress, 1)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.close_button)
        footer_layout.addWidget(self.export_button)
        root.addWidget(footer)

    def _build_settings_panel(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._build_output_tab(), "Output")
        tabs.addTab(self._build_elements_tab(), "Elements")
        tabs.addTab(self._build_presentation_tab(), "Presentation")
        return tabs

    def _scroll_form(self) -> tuple[QScrollArea, QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        scroll.setWidget(content)
        return scroll, content, layout

    def _build_output_tab(self) -> QWidget:
        scroll, _content, layout = self._scroll_form()
        format_group = QGroupBox("Delivery")
        form = QFormLayout(format_group)
        self.resolution_combo = QComboBox()
        for preset in RESOLUTION_PRESETS:
            self.resolution_combo.addItem(f"{preset.name} · {preset.description}", (preset.width, preset.height))
        self.resolution_combo.addItem("Custom", None)
        self.resolution_combo.currentIndexChanged.connect(self._resolution_selected)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(160, 7680)
        self.width_spin.setSingleStep(2)
        self.width_spin.valueChanged.connect(self._output_size_changed)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(160, 7680)
        self.height_spin.setSingleStep(2)
        self.height_spin.valueChanged.connect(self._output_size_changed)
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(1.0, 240.0)
        self.fps_spin.setDecimals(3)
        self.fps_spin.setValue(30.0)
        self.fps_spin.valueChanged.connect(self._schedule_preview)
        self.format_combo = QComboBox()
        for definition in FORMAT_DEFINITIONS:
            self.format_combo.addItem(f"{definition.label} — {definition.description}", definition.key)
        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self.encoder_combo = QComboBox()
        for key, label in ENCODER_MODES:
            self.encoder_combo.addItem(label, key)
        self.encoder_combo.setCurrentIndex(0)
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(0, 51)
        self.quality_spin.setValue(18)
        self.quality_spin.valueChanged.connect(self._quality_changed)
        self.quality_label = QLabel("CRF quality")
        self.audio_check = QCheckBox("Include source audio")
        self.audio_check.setChecked(self.has_source_audio)
        if not self.has_source_audio:
            self.audio_check.setToolTip("This source does not contain an audio track.")
        self.audio_bitrate = QSpinBox()
        self.audio_bitrate.setRange(64, 512)
        self.audio_bitrate.setSingleStep(32)
        self.audio_bitrate.setValue(192)
        self.audio_bitrate.setSuffix(" kbps")
        self.audio_check.toggled.connect(self._audio_toggled)
        form.addRow("Preset", self.resolution_combo)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Frame rate", self.fps_spin)
        form.addRow("Format", self.format_combo)
        form.addRow("Encoder", self.encoder_combo)
        form.addRow(self.quality_label, self.quality_spin)
        form.addRow("Audio", self.audio_check)
        form.addRow("Audio rate", self.audio_bitrate)
        layout.addWidget(format_group)

        range_group = QGroupBox("Time range")
        range_form = QFormLayout(range_group)
        duration = float(max(0.0, getattr(self.analysis, "duration", 0.0)))
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0.0, max(0.001, duration))
        self.start_spin.setDecimals(3)
        self.start_spin.setSuffix(" s")
        self.start_spin.valueChanged.connect(self._range_changed)
        self.end_spin = QDoubleSpinBox()
        self.end_spin.setRange(0.0, max(0.001, duration))
        self.end_spin.setDecimals(3)
        self.end_spin.setValue(duration)
        self.end_spin.setSuffix(" s")
        self.end_spin.valueChanged.connect(self._range_changed)
        range_form.addRow("Start", self.start_spin)
        range_form.addRow("End", self.end_spin)
        layout.addWidget(range_group)

        estimate_group = QGroupBox("Estimate")
        estimate_layout = QVBoxLayout(estimate_group)
        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setObjectName("Subtle")
        estimate_layout.addWidget(self.estimate_label)
        layout.addWidget(estimate_group)
        layout.addStretch(1)
        self._update_estimate()
        return scroll

    def _build_elements_tab(self) -> QWidget:
        scroll, _content, layout = self._scroll_form()
        preset_group = QGroupBox("Composition preset")
        preset_layout = QVBoxLayout(preset_group)
        self.layout_preset_combo = QComboBox()
        for title, function_name in LAYOUT_PRESETS:
            self.layout_preset_combo.addItem(title, function_name)
        self.layout_preset_combo.setCurrentText("Balanced")
        apply_button = QPushButton("Apply layout preset")
        apply_button.clicked.connect(self._apply_layout_preset)
        self.snap_check = QCheckBox("Snap to layout grid")
        self.snap_check.setChecked(True)
        self.snap_check.toggled.connect(self._set_snap)
        self.safe_area_check = QCheckBox("Show export-safe area")
        self.safe_area_check.setChecked(True)
        self.safe_area_check.toggled.connect(self._set_safe_area_visible)
        self.safe_area_spin = QDoubleSpinBox()
        self.safe_area_spin.setRange(0.0, 20.0)
        self.safe_area_spin.setSingleStep(0.5)
        self.safe_area_spin.setValue(self.layout_spec.safe_area_percent)
        self.safe_area_spin.setSuffix(" %")
        self.safe_area_spin.valueChanged.connect(self._safe_area_changed)
        preset_layout.addWidget(self.layout_preset_combo)
        preset_layout.addWidget(apply_button)
        preset_layout.addWidget(self.snap_check)
        preset_layout.addWidget(self.safe_area_check)
        preset_layout.addWidget(self.safe_area_spin)
        layout.addWidget(preset_group)

        elements_group = QGroupBox("Included layers")
        elements_layout = QVBoxLayout(elements_group)
        self.element_checks: dict[str, QCheckBox] = {}
        for index, name in enumerate(LAYOUT_ITEM_ORDER, start=1):
            check = QCheckBox(f"{index:02d} / {LAYOUT_ITEM_TITLES.get(name, name)}")
            check.setChecked(self.layout_spec.item(name).enabled)
            check.toggled.connect(lambda enabled, item_name=name: self._element_toggled(item_name, enabled))
            self.element_checks[name] = check
            elements_layout.addWidget(check)
        layout.addWidget(elements_group)
        note = QLabel(
            "Layers can be moved forward or backward from the inspector. Use position, size, opacity, fit mode, "
            "visibility, snapping, and export-safe guides to organize the final frame."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)
        return scroll

    def _build_presentation_tab(self) -> QWidget:
        scroll, _content, layout = self._scroll_form()
        labels_group = QGroupBox("Titles and marks")
        form = QFormLayout(labels_group)
        self.show_title_check = QCheckBox("Show project title")
        self.show_title_check.setChecked(bool(self.base_options.show_project_title))
        self.project_title_edit = QLineEdit(str(self.base_options.project_title or "Metriq Visualizer"))
        self.project_subtitle_edit = QLineEdit(str(self.base_options.project_subtitle or ""))
        self.watermark_check = QCheckBox("Show watermark")
        self.watermark_check.setChecked(bool(self.base_options.show_watermark))
        self.watermark_edit = QLineEdit(str(self.base_options.watermark_text or ""))
        form.addRow("Project", self.show_title_check)
        form.addRow("Title", self.project_title_edit)
        form.addRow("Subtitle", self.project_subtitle_edit)
        form.addRow("Watermark", self.watermark_check)
        form.addRow("Mark text", self.watermark_edit)
        layout.addWidget(labels_group)

        scene_group = QGroupBox("Scene annotations")
        scene_layout = QVBoxLayout(scene_group)
        self.axes_check = QCheckBox("Show 3D axes")
        self.axes_check.setChecked(bool(self.base_options.show_axes))
        self.axis_labels_check = QCheckBox("Show axis labels")
        self.axis_labels_check.setChecked(bool(self.base_options.show_axis_labels))
        self.colorbar_check = QCheckBox("Show color scale")
        self.colorbar_check.setChecked(bool(self.base_options.show_colorbar))
        self.scene_hud_check = QCheckBox("Show 3D time / frequency HUD")
        self.scene_hud_check.setChecked(bool(self.base_options.show_scene_hud))
        self.timecode_check = QCheckBox("Show export timecode")
        self.timecode_check.setChecked(bool(self.base_options.show_timecode))
        scene_layout.addWidget(self.axes_check)
        scene_layout.addWidget(self.axis_labels_check)
        scene_layout.addWidget(self.colorbar_check)
        scene_layout.addWidget(self.scene_hud_check)
        scene_layout.addWidget(self.timecode_check)
        layout.addWidget(scene_group)
        layout.addStretch(1)
        for widget in (
            self.show_title_check,
            self.project_title_edit,
            self.project_subtitle_edit,
            self.watermark_check,
            self.watermark_edit,
            self.axes_check,
            self.axis_labels_check,
            self.colorbar_check,
            self.scene_hud_check,
            self.timecode_check,
        ):
            if hasattr(widget, "textChanged"):
                widget.textChanged.connect(lambda *_: self._schedule_preview(recreate=True))
            elif hasattr(widget, "toggled"):
                widget.toggled.connect(lambda *_: self._schedule_preview(recreate=True))
        return scroll

    def _build_workspace(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        mode_bar = QHBoxLayout()
        label = QLabel("FRAME COMPOSER")
        label.setObjectName("Eyebrow")
        mode_bar.addWidget(label)
        self.undo_button = QPushButton("Undo layout")
        self.undo_button.clicked.connect(self._undo_layout)
        self.redo_button = QPushButton("Redo layout")
        self.redo_button.clicked.connect(self._redo_layout)
        mode_bar.addWidget(self.undo_button)
        mode_bar.addWidget(self.redo_button)
        mode_bar.addStretch(1)
        self.preview_status = QLabel("Preview queued")
        self.preview_status.setObjectName("Subtle")
        mode_bar.addWidget(self.preview_status)
        layout.addLayout(mode_bar)

        vertical = QSplitter(Qt.Orientation.Vertical)
        self.preview_label = QLabel("Rendering preview…")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(230)
        self.preview_label.setStyleSheet("border:1px solid palette(mid); background:#070b11;")
        vertical.addWidget(self.preview_label)
        self.canvas = LayoutCanvas()
        self.canvas.set_layout_spec(self.layout_spec)
        self.canvas.layoutChanged.connect(self._layout_changed)
        self.canvas.selectionChanged.connect(self._selection_changed)
        self.canvas.interactionStarted.connect(self._canvas_interaction_started)
        self.canvas.interactionFinished.connect(self._canvas_interaction_finished)
        vertical.addWidget(self.canvas)
        vertical.setSizes([330, 430])
        layout.addWidget(vertical, 1)

        timeline = QHBoxLayout()
        self.time_label = QLabel("00:00.000")
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 10_000)
        self.time_slider.valueChanged.connect(self._schedule_preview)
        timeline.addWidget(self.time_label)
        timeline.addWidget(self.time_slider, 1)
        layout.addLayout(timeline)
        return frame

    def _build_inspector(self) -> QWidget:
        scroll, _content, layout = self._scroll_form()
        title = QLabel("LAYER INSPECTOR")
        title.setObjectName("Eyebrow")
        layout.addWidget(title)
        self.selected_label = QLabel("Geometry")
        self.selected_label.setObjectName("Title")
        layout.addWidget(self.selected_label)

        transform_group = QGroupBox("Normalized frame")
        transform_form = QFormLayout(transform_group)
        self.x_spin = self._percent_spin()
        self.y_spin = self._percent_spin()
        self.w_spin = self._percent_spin(minimum=5.0)
        self.h_spin = self._percent_spin(minimum=5.0)
        transform_form.addRow("X", self.x_spin)
        transform_form.addRow("Y", self.y_spin)
        transform_form.addRow("Width", self.w_spin)
        transform_form.addRow("Height", self.h_spin)
        layout.addWidget(transform_group)

        content_group = QGroupBox("Content")
        content_form = QFormLayout(content_group)
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["contain", "cover", "stretch"])
        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(0.35, 5.0)
        self.scale_spin.setSingleStep(0.05)
        self.scale_spin.setDecimals(2)
        self.alpha_spin = QDoubleSpinBox()
        self.alpha_spin.setRange(0.0, 1.0)
        self.alpha_spin.setSingleStep(0.05)
        self.alpha_spin.setDecimals(2)
        self.content_alpha_spin = QDoubleSpinBox()
        self.content_alpha_spin.setRange(0.0, 1.0)
        self.content_alpha_spin.setSingleStep(0.05)
        self.content_alpha_spin.setDecimals(2)
        self.item_title_check = QCheckBox("Show panel title")
        content_form.addRow("Fit", self.fit_combo)
        content_form.addRow("Scale", self.scale_spin)
        content_form.addRow("Background", self.alpha_spin)
        content_form.addRow("Content opacity", self.content_alpha_spin)
        content_form.addRow("Label", self.item_title_check)
        layout.addWidget(content_group)

        button_grid = QGridLayout()
        fill_button = QPushButton("Fill frame")
        fill_button.clicked.connect(self._fill_selected)
        center_button = QPushButton("Center")
        center_button.clicked.connect(self._center_selected)
        reset_button = QPushButton("Reset layer")
        reset_button.clicked.connect(self._reset_selected)
        backward_button = QPushButton("Move backward")
        backward_button.clicked.connect(lambda: self._move_selected_layer(-1))
        forward_button = QPushButton("Move forward")
        forward_button.clicked.connect(lambda: self._move_selected_layer(1))
        button_grid.addWidget(fill_button, 0, 0)
        button_grid.addWidget(center_button, 0, 1)
        button_grid.addWidget(backward_button, 1, 0)
        button_grid.addWidget(forward_button, 1, 1)
        button_grid.addWidget(reset_button, 2, 0, 1, 2)
        layout.addLayout(button_grid)
        help_label = QLabel("Tip: Shift + arrow keys nudges by 0.1%; arrows nudge by 1%.")
        help_label.setWordWrap(True)
        help_label.setObjectName("Subtle")
        layout.addWidget(help_label)
        layout.addStretch(1)

        for widget in (self.x_spin, self.y_spin, self.w_spin, self.h_spin, self.scale_spin, self.alpha_spin, self.content_alpha_spin):
            widget.valueChanged.connect(self._inspector_changed)
        self.fit_combo.currentTextChanged.connect(self._inspector_changed)
        self.item_title_check.toggled.connect(self._inspector_changed)
        self._update_inspector("geometry")
        return scroll

    @staticmethod
    def _percent_spin(*, minimum: float = 0.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, 100.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.5)
        spin.setSuffix(" %")
        return spin

    def _populate_from_options(self) -> None:
        self.width_spin.setValue(int(self.base_options.width))
        self.height_spin.setValue(int(self.base_options.height))
        self.fps_spin.setValue(float(self.base_options.fps))
        matched = False
        for index in range(self.resolution_combo.count() - 1):
            if self.resolution_combo.itemData(index) == (self.base_options.width, self.base_options.height):
                self.resolution_combo.setCurrentIndex(index)
                matched = True
                break
        if not matched:
            self.resolution_combo.setCurrentIndex(self.resolution_combo.count() - 1)
        self.canvas.set_output_size(self.width_spin.value(), self.height_spin.value())
        self._sync_element_checks()
        self._format_changed()
        self._update_estimate()

    def _resolution_selected(self) -> None:
        value = self.resolution_combo.currentData()
        if not value:
            return
        width, height = value
        with QSignalBlocker(self.width_spin), QSignalBlocker(self.height_spin):
            self.width_spin.setValue(width)
            self.height_spin.setValue(height)
        self._output_size_changed()

    def _output_size_changed(self) -> None:
        size = (self.width_spin.value(), self.height_spin.value())
        matched_index = self.resolution_combo.count() - 1
        for index in range(self.resolution_combo.count() - 1):
            if self.resolution_combo.itemData(index) == size:
                matched_index = index
                break
        with QSignalBlocker(self.resolution_combo):
            self.resolution_combo.setCurrentIndex(matched_index)
        self.canvas.set_output_size(*size)
        self._update_estimate()
        self._schedule_preview(recreate=True)

    def _audio_toggled(self, enabled: bool) -> None:
        if self.audio_check.isEnabled():
            self._audio_preference = bool(enabled)
        definition = FORMAT_BY_KEY[str(self.format_combo.currentData())]
        self.audio_bitrate.setEnabled(bool(enabled) and definition.supports_audio and self.has_source_audio)

    def _quality_changed(self, value: int) -> None:
        key = str(self.format_combo.currentData() or self._last_format_key or "mp4_h264")
        if key == "jpeg_sequence":
            self._jpeg_quality = int(min(100, max(20, value)))
        elif key not in {"mov_prores422", "mov_prores4444", "png_sequence", "gif"}:
            self._video_quality = int(min(51, max(0, value)))

    def _format_changed(self, *_args) -> None:
        if self._last_format_key == "jpeg_sequence":
            self._jpeg_quality = int(min(100, max(20, self.quality_spin.value())))
        elif self._last_format_key not in {None, "mov_prores422", "mov_prores4444", "png_sequence", "gif"}:
            self._video_quality = int(min(51, max(0, self.quality_spin.value())))

        definition = FORMAT_BY_KEY[str(self.format_combo.currentData())]
        self.encoder_combo.setEnabled(definition.key in {"mp4_h264", "mp4_h265"})
        audio_enabled = definition.supports_audio and self.has_source_audio
        with QSignalBlocker(self.audio_check):
            self.audio_check.setChecked(self._audio_preference if audio_enabled else False)
        self.audio_check.setEnabled(audio_enabled)
        self.audio_bitrate.setEnabled(audio_enabled and self.audio_check.isChecked())
        if definition.key in {"mov_prores422", "mov_prores4444"}:
            self.quality_label.setText("Codec profile")
            self.quality_spin.setEnabled(False)
        elif definition.key == "jpeg_sequence":
            self.quality_label.setText("JPEG quality")
            self.quality_spin.setEnabled(True)
            with QSignalBlocker(self.quality_spin):
                self.quality_spin.setRange(20, 100)
                self.quality_spin.setValue(self._jpeg_quality)
        elif definition.key == "png_sequence":
            self.quality_label.setText("Lossless")
            self.quality_spin.setEnabled(False)
        elif definition.key == "gif":
            self.quality_label.setText("256-color palette")
            self.quality_spin.setEnabled(False)
        else:
            self.quality_label.setText("CRF quality")
            self.quality_spin.setEnabled(True)
            with QSignalBlocker(self.quality_spin):
                self.quality_spin.setRange(0, 51)
                self.quality_spin.setValue(self._video_quality)
        self._last_format_key = definition.key
        self._update_estimate()

    def _range_changed(self) -> None:
        if self.end_spin.value() < self.start_spin.value():
            with QSignalBlocker(self.end_spin):
                self.end_spin.setValue(self.start_spin.value())
        self._update_estimate()
        self._schedule_preview()

    def _update_estimate(self) -> None:
        if not hasattr(self, "estimate_label"):
            return
        duration = max(0.0, self.end_spin.value() - self.start_spin.value())
        frames = max(1, round(duration * self.fps_spin.value()))
        pixels = frames * self.width_spin.value() * self.height_spin.value()
        raw_gib = pixels * 4 / (1024**3)
        self.estimate_label.setText(
            f"{duration:.3f} seconds · approximately {frames:,} frames. "
            f"Frames are streamed; the pipeline does not need the full {raw_gib:.1f} GiB raw sequence in memory."
        )

    def _set_snap(self, enabled: bool) -> None:
        self.canvas.snap_enabled = bool(enabled)

    def _set_safe_area_visible(self, enabled: bool) -> None:
        self.canvas.show_safe_area = bool(enabled)
        self.canvas.update()

    def _safe_area_changed(self, value: float) -> None:
        self._push_undo()
        self.layout_spec.safe_area_percent = float(value)
        self.canvas.set_layout_spec(self.layout_spec)

    def _push_undo(self) -> None:
        if self._history_guard:
            return
        current = self.layout_spec.clone().clamp()
        if self._undo_stack and self._undo_stack[-1].to_dict() == current.to_dict():
            return
        self._undo_stack.append(current)
        if len(self._undo_stack) > 80:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(bool(self._undo_stack))
            self.redo_button.setEnabled(bool(self._redo_stack))

    def _undo_layout(self) -> None:
        if not self._undo_stack:
            return
        self._history_guard = True
        try:
            self._redo_stack.append(self.layout_spec.clone())
            self._set_layout(self._undo_stack.pop(), record=False)
        finally:
            self._history_guard = False
        self._update_history_buttons()

    def _redo_layout(self) -> None:
        if not self._redo_stack:
            return
        self._history_guard = True
        try:
            self._undo_stack.append(self.layout_spec.clone())
            self._set_layout(self._redo_stack.pop(), record=False)
        finally:
            self._history_guard = False
        self._update_history_buttons()

    def _apply_layout_preset(self) -> None:
        function_name = str(self.layout_preset_combo.currentData())
        layout = _layout_factory(function_name)
        self._set_layout(layout)

    def _set_layout(self, layout: ExportLayoutSpec, *, record: bool = True) -> None:
        if record:
            self._push_undo()
        self.layout_spec = layout.clone().clamp()
        self.canvas.set_layout_spec(self.layout_spec)
        self._sync_element_checks()
        if hasattr(self, "safe_area_spin"):
            with QSignalBlocker(self.safe_area_spin):
                self.safe_area_spin.setValue(self.layout_spec.safe_area_percent)
        self._update_inspector(self.canvas.selected)
        self._schedule_preview()
        self._update_history_buttons()

    def _sync_element_checks(self) -> None:
        if not hasattr(self, "element_checks"):
            return
        for name, check in self.element_checks.items():
            with QSignalBlocker(check):
                check.setChecked(bool(self.layout_spec.item(name).enabled))

    def _element_toggled(self, name: str, enabled: bool) -> None:
        self._push_undo()
        self.layout_spec.item(name).enabled = bool(enabled)
        self.layout_spec.clamp()
        self.canvas.set_layout_spec(self.layout_spec)
        if enabled:
            self.canvas.select_item(name)
        self._schedule_preview()

    def _layout_changed(self, layout: ExportLayoutSpec) -> None:
        if not self._canvas_interaction_active and layout.to_dict() != self.layout_spec.to_dict():
            self._push_undo()
        self.layout_spec = layout.clone().clamp()
        self._update_inspector(self.canvas.selected)
        self._schedule_preview()

    def _canvas_interaction_started(self) -> None:
        self._push_undo()
        self._canvas_interaction_active = True

    def _canvas_interaction_finished(self) -> None:
        self._canvas_interaction_active = False

    def _selection_changed(self, name: str) -> None:
        self._update_inspector(name)

    def _update_inspector(self, name: str) -> None:
        if not hasattr(self, "x_spin"):
            return
        item = self.layout_spec.item(name)
        self.selected_label.setText(LAYOUT_ITEM_TITLES.get(name, name).title())
        widgets = (self.x_spin, self.y_spin, self.w_spin, self.h_spin, self.scale_spin, self.alpha_spin, self.content_alpha_spin, self.fit_combo, self.item_title_check)
        blockers = [QSignalBlocker(widget) for widget in widgets]
        self.x_spin.setValue(item.x * 100)
        self.y_spin.setValue(item.y * 100)
        self.w_spin.setValue(item.w * 100)
        self.h_spin.setValue(item.h * 100)
        self.scale_spin.setValue(item.content_scale)
        self.alpha_spin.setValue(item.background_alpha)
        self.content_alpha_spin.setValue(item.content_alpha)
        self.fit_combo.setCurrentText(item.fit_mode)
        self.item_title_check.setChecked(item.show_title)
        del blockers

    def _inspector_changed(self, *_args) -> None:
        if not hasattr(self, "canvas"):
            return
        self._push_undo()
        item = self.layout_spec.item(self.canvas.selected)
        item.x = self.x_spin.value() / 100
        item.y = self.y_spin.value() / 100
        item.w = self.w_spin.value() / 100
        item.h = self.h_spin.value() / 100
        item.content_scale = self.scale_spin.value()
        item.background_alpha = self.alpha_spin.value()
        item.content_alpha = self.content_alpha_spin.value()
        item.fit_mode = self.fit_combo.currentText()
        item.show_title = self.item_title_check.isChecked()
        item.clamp()
        self.canvas.set_layout_spec(self.layout_spec)
        self._schedule_preview()

    def _fill_selected(self) -> None:
        self._push_undo()
        item = self.layout_spec.item(self.canvas.selected)
        item.x = item.y = 0.0
        item.w = item.h = 1.0
        item.clamp()
        self.canvas.set_layout_spec(self.layout_spec, emit=True)

    def _center_selected(self) -> None:
        self._push_undo()
        item = self.layout_spec.item(self.canvas.selected)
        item.x = (1.0 - item.w) / 2
        item.y = (1.0 - item.h) / 2
        item.clamp()
        self.canvas.set_layout_spec(self.layout_spec, emit=True)

    def _reset_selected(self) -> None:
        self._push_undo()
        reference = layout_module.default_export_layout().item(self.canvas.selected)
        target = self.layout_spec.item(self.canvas.selected)
        target.enabled = reference.enabled
        target.x = reference.x
        target.y = reference.y
        target.w = reference.w
        target.h = reference.h
        target.content_scale = reference.content_scale
        target.fit_mode = reference.fit_mode
        target.background_alpha = reference.background_alpha
        target.content_alpha = reference.content_alpha
        target.show_title = reference.show_title
        target.clamp()
        self.canvas.set_layout_spec(self.layout_spec, emit=True)
        self._sync_element_checks()

    def _move_selected_layer(self, delta: int) -> None:
        self._push_undo()
        self.layout_spec.move_layer(self.canvas.selected, delta)
        self.canvas.set_layout_spec(self.layout_spec, emit=True)

    def _current_time(self) -> float:
        start = self.start_spin.value()
        end = max(start, self.end_spin.value())
        ratio = self.time_slider.value() / max(1, self.time_slider.maximum())
        return start + (end - start) * ratio

    def _schedule_preview(self, *_args, recreate: bool = False) -> None:
        if recreate:
            self.preview_dirty = True
        if hasattr(self, "time_label"):
            seconds = self._current_time()
            minutes = int(seconds // 60)
            remainder = seconds - minutes * 60
            self.time_label.setText(f"{minutes:02d}:{remainder:06.3f}")
        self._preview_timer.start()

    def _preview_options(self) -> ExportOptions:
        return self._render_options()

    def _render_options(self) -> ExportOptions:
        options = deepcopy(self.base_options)
        options.width = int(self.width_spin.value())
        options.height = int(self.height_spin.value())
        options.fps = int(round(self.fps_spin.value()))
        options.layout = self.layout_spec.clone().clamp()
        options.start_time = float(self.start_spin.value())
        options.end_time = float(self.end_spin.value())
        options.show_project_title = self.show_title_check.isChecked()
        options.project_title = self.project_title_edit.text().strip()
        options.project_subtitle = self.project_subtitle_edit.text().strip()
        options.show_watermark = self.watermark_check.isChecked()
        options.watermark_text = self.watermark_edit.text().strip()
        options.show_axes = self.axes_check.isChecked()
        options.show_axis_labels = self.axis_labels_check.isChecked()
        options.show_colorbar = self.colorbar_check.isChecked()
        options.show_scene_hud = self.scene_hud_check.isChecked()
        options.show_timecode = self.timecode_check.isChecked()
        return options

    def _render_preview(self) -> None:
        if self.thread is not None:
            return
        try:
            if self.preview_session is None or self.preview_dirty:
                if self.preview_session is not None:
                    self.preview_session.close()
                self.preview_session = ExportPreviewSession(self.analysis, self.geometry, self._preview_options())
                self.preview_dirty = False
            available_w = max(320, self.preview_label.width() - 12)
            available_h = max(180, self.preview_label.height() - 12)
            aspect = self.width_spin.value() / max(1, self.height_spin.value())
            if available_w / available_h > aspect:
                height = available_h
                width = round(height * aspect)
            else:
                width = available_w
                height = round(width / aspect)
            width = max(160, width - width % 2)
            height = max(90, height - height % 2)
            frame = self.preview_session.render_frame(
                current_time=self._current_time(),
                layout=self.layout_spec,
                output_size=(width, height),
            )
            pixmap = QPixmap.fromImage(_image_from_rgba(frame))
            self.preview_label.setPixmap(pixmap)
            self.preview_status.setText(f"Preview · {width} × {height}")
        except Exception as exc:  # noqa: BLE001
            self.preview_label.setText(f"Preview unavailable\n{exc}")
            self.preview_status.setText("Preview error")

    def _collect_profile(self) -> ExportProfile:
        format_key = str(self.format_combo.currentData())
        self._quality_changed(self.quality_spin.value())
        return ExportProfile(
            name=f"{self.width_spin.value()}×{self.height_spin.value()} {FORMAT_BY_KEY[format_key].label}",
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            fps=self.fps_spin.value(),
            format_key=format_key,
            quality=self._video_quality,
            include_audio=self.audio_check.isChecked(),
            audio_bitrate_kbps=self.audio_bitrate.value(),
            jpeg_quality=self._jpeg_quality,
            start_time=self.start_spin.value(),
            end_time=self.end_spin.value(),
            title=self.project_title_edit.text().strip() or "Metriq Visualizer",
            layout=self.layout_spec.to_dict(),
            encoder_mode=str(self.encoder_combo.currentData() or "auto"),
            show_project_title=self.show_title_check.isChecked(),
            project_subtitle=self.project_subtitle_edit.text().strip(),
            show_watermark=self.watermark_check.isChecked(),
            watermark_text=self.watermark_edit.text().strip(),
            show_axes=self.axes_check.isChecked(),
            show_axis_labels=self.axis_labels_check.isChecked(),
            show_colorbar=self.colorbar_check.isChecked(),
            show_scene_hud=self.scene_hud_check.isChecked(),
            show_timecode=self.timecode_check.isChecked(),
        ).validate()

    def _save_profile(self) -> None:
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Save export profile",
            str(Path.home() / "metriq_export.mvexport"),
            "Metriq export profile (*.mvexport)",
        )
        if not path_text:
            return
        try:
            path = save_export_profile(path_text, self._collect_profile())
            self.progress.setFormat(f"Saved profile: {path.name}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save profile", str(exc))

    def _load_profile(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "Load export profile",
            str(Path.home()),
            "Metriq export profile (*.mvexport)",
        )
        if not path_text:
            return
        try:
            profile = load_export_profile(path_text)
            self._apply_profile(profile)
            self.progress.setFormat(f"Loaded profile: {Path(path_text).name}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not load profile", str(exc))

    def _apply_profile(self, profile: ExportProfile) -> None:
        with QSignalBlocker(self.width_spin), QSignalBlocker(self.height_spin), QSignalBlocker(self.fps_spin):
            self.width_spin.setValue(profile.width)
            self.height_spin.setValue(profile.height)
            self.fps_spin.setValue(profile.fps)
        self._video_quality = int(profile.quality)
        self._jpeg_quality = int(profile.jpeg_quality)
        index = self.format_combo.findData(profile.format_key)
        if index >= 0:
            self.format_combo.setCurrentIndex(index)
        self._format_changed()
        definition = FORMAT_BY_KEY[str(self.format_combo.currentData())]
        can_include_audio = bool(self.has_source_audio and definition.supports_audio)
        self._audio_preference = bool(profile.include_audio and self.has_source_audio)
        with QSignalBlocker(self.audio_check):
            self.audio_check.setChecked(bool(profile.include_audio and can_include_audio))
        self.audio_check.setEnabled(can_include_audio)
        self.audio_bitrate.setEnabled(can_include_audio and self.audio_check.isChecked())
        self.audio_bitrate.setValue(profile.audio_bitrate_kbps)
        encoder_index = self.encoder_combo.findData(profile.encoder_mode)
        if encoder_index >= 0:
            self.encoder_combo.setCurrentIndex(encoder_index)
        self.start_spin.setValue(profile.start_time)
        self.end_spin.setValue(float(getattr(self.analysis, "duration", 0.0)) if profile.end_time is None else profile.end_time)
        with QSignalBlocker(self.show_title_check):
            self.show_title_check.setChecked(profile.show_project_title)
        self.project_title_edit.setText(profile.title)
        self.project_subtitle_edit.setText(profile.project_subtitle)
        with QSignalBlocker(self.watermark_check):
            self.watermark_check.setChecked(profile.show_watermark)
        self.watermark_edit.setText(profile.watermark_text)
        with QSignalBlocker(self.axes_check):
            self.axes_check.setChecked(profile.show_axes)
        with QSignalBlocker(self.axis_labels_check):
            self.axis_labels_check.setChecked(profile.show_axis_labels)
        with QSignalBlocker(self.colorbar_check):
            self.colorbar_check.setChecked(profile.show_colorbar)
        with QSignalBlocker(self.scene_hud_check):
            self.scene_hud_check.setChecked(profile.show_scene_hud)
        with QSignalBlocker(self.timecode_check):
            self.timecode_check.setChecked(profile.show_timecode)
        if profile.layout:
            self._set_layout(ExportLayoutSpec.from_dict(profile.layout))
        self.canvas.set_output_size(profile.width, profile.height)
        self._output_size_changed()
        self.preview_dirty = True
        self._schedule_preview(recreate=True)

    def _choose_output(self, profile: ExportProfile) -> Path | None:
        definition = profile.definition
        if definition.kind == "sequence":
            selected = QFileDialog.getExistingDirectory(
                self,
                "Select image-sequence folder",
                str(Path.home() / "Metriq Export Frames"),
            )
            if not selected:
                return None
            output = Path(selected)
            # Never erase or merge into an existing non-empty folder.
            if output.exists() and any(output.iterdir()):
                stem = f"metriq_frames_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                candidate = output / stem
                suffix = 2
                while candidate.exists():
                    candidate = output / f"{stem}_{suffix}"
                    suffix += 1
                output = candidate
            return output
        filter_text = f"{definition.label} (*{definition.extension})"
        suggested = Path.home() / f"metriq_visualizer{definition.extension}"
        selected, _ = QFileDialog.getSaveFileName(self, "Export visualization", str(suggested), filter_text)
        if not selected:
            return None
        return output_path_for_profile(selected, profile)

    def _begin_export(self) -> None:
        if self.thread is not None:
            return
        try:
            profile = self._collect_profile()
            output = self._choose_output(profile)
            if output is None:
                return
            options = self._render_options()
            self.output_path = output
            self.worker = ExportWorker(self.analysis, self.geometry, options, profile, output)
            self.thread = QThread(self)
            self.worker.moveToThread(self.thread)
            self.thread.started.connect(self.worker.run)
            self.worker.progress.connect(self._export_progress)
            self.worker.finished.connect(self._export_finished)
            self.worker.failed.connect(self._export_failed)
            self.worker.cancelled.connect(self._export_cancelled)
            self.worker.finished.connect(self.thread.quit)
            self.worker.failed.connect(self.thread.quit)
            self.worker.cancelled.connect(self.thread.quit)
            self.thread.finished.connect(self._thread_finished)
            self.export_button.setEnabled(False)
            self.close_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.load_profile_button.setEnabled(False)
            self.save_profile_button.setEnabled(False)
            self.progress.setValue(0)
            self.progress.setFormat("Preparing renderer")
            self.thread.start()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not start export", str(exc))

    def _cancel_export(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.progress.setFormat("Cancelling…")

    @Slot(float, str)
    def _export_progress(self, value: float, message: str) -> None:
        self.progress.setValue(round(max(0.0, min(1.0, value)) * 1000))
        self.progress.setFormat(f"{int(value * 100):3d}% · {message}")

    @Slot(str)
    def _export_finished(self, result: str) -> None:
        self.progress.setValue(1000)
        self.progress.setFormat(f"Saved: {result}")
        QMessageBox.information(self, "Export complete", f"Saved to:\n{result}")

    @Slot(str)
    def _export_failed(self, details: str) -> None:
        self.progress.setFormat("Export failed")
        QMessageBox.critical(self, "Export failed", details)

    @Slot()
    def _export_cancelled(self) -> None:
        self.progress.setFormat("Export cancelled")

    @Slot()
    def _thread_finished(self) -> None:
        thread = self.thread
        worker = self.worker
        self.thread = None
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()
        self.export_button.setEnabled(True)
        self.close_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.load_profile_button.setEnabled(True)
        self.save_profile_button.setEnabled(True)
        self._schedule_preview()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self.thread is not None:
            QMessageBox.information(self, "Export in progress", "Cancel the current export before closing this window.")
            event.ignore()
            return
        if self.preview_session is not None:
            self.preview_session.close()
            self.preview_session = None
        super().closeEvent(event)


__all__ = ["ExportStudioDialog", "ExportWorker", "LayoutCanvas"]
