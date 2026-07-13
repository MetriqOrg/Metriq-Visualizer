# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Low-latency configurable stage output for a second display.

The stage window composites lightweight snapshots of the already-rendered live
viewport and analysis panels.  It intentionally does not create another 3D
renderer or analysis pipeline, so a DJ can keep operating the studio window
while the audience display follows the same realtime scene.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPaintEvent, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from metriq_visualizer_export_studio import LayoutCanvas
from metriq_visualizer_layout import (
    LAYOUT_ITEM_ORDER,
    LAYOUT_ITEM_TITLES,
    ExportLayoutSpec,
    analysis_focus_export_layout,
    balanced_export_layout,
    geometry_focus_export_layout,
    overlay_export_layout,
)

STAGE_OUTPUT_SCHEMA = "metriq.stage-output"
STAGE_OUTPUT_VERSION = 1
BACKGROUND_KINDS = ("color", "image", "video")


@dataclass(slots=True)
class StageOutputConfig:
    """Persistent audience-display settings independent of export settings."""

    layout: ExportLayoutSpec = field(default_factory=geometry_focus_export_layout)
    screen_name: str = ""
    fullscreen: bool = True
    refresh_fps: int = 15
    background_kind: str = "color"
    background_color: str = "#070b11"
    background_path: str = ""

    def clamp(self) -> StageOutputConfig:
        self.layout = self.layout.clone().clamp()
        self.screen_name = str(self.screen_name)
        self.fullscreen = bool(self.fullscreen)
        self.refresh_fps = min(30, max(5, int(self.refresh_fps)))
        self.background_kind = str(self.background_kind).casefold()
        if self.background_kind not in BACKGROUND_KINDS:
            self.background_kind = "color"
        self.background_color = str(self.background_color or "#070b11")
        self.background_path = str(self.background_path)
        return self

    def clone(self) -> StageOutputConfig:
        return StageOutputConfig.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return {
            "schema": STAGE_OUTPUT_SCHEMA,
            "schema_version": STAGE_OUTPUT_VERSION,
            "layout": self.layout.to_dict(),
            "screen_name": self.screen_name,
            "fullscreen": self.fullscreen,
            "refresh_fps": self.refresh_fps,
            "background_kind": self.background_kind,
            "background_color": self.background_color,
            "background_path": self.background_path,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> StageOutputConfig:
        if not isinstance(payload, Mapping):
            return cls().clamp()
        layout_payload = payload.get("layout") if isinstance(payload.get("layout"), Mapping) else None
        return cls(
            layout=ExportLayoutSpec.from_dict(layout_payload),
            screen_name=str(payload.get("screen_name", "")),
            fullscreen=bool(payload.get("fullscreen", True)),
            refresh_fps=int(payload.get("refresh_fps", 15)),
            background_kind=str(payload.get("background_kind", "color")),
            background_color=str(payload.get("background_color", "#070b11")),
            background_path=str(payload.get("background_path", "")),
        ).clamp()


def _fit_rect(source_width: int, source_height: int, target: QRectF, mode: str) -> tuple[QRectF, QRectF]:
    """Return destination and source rectangles for contain/cover/stretch."""

    source = QRectF(0.0, 0.0, max(1, source_width), max(1, source_height))
    if mode == "stretch":
        return target, source
    source_ratio = source.width() / source.height()
    target_ratio = target.width() / max(1.0, target.height())
    if mode == "cover":
        if source_ratio > target_ratio:
            cropped_width = source.height() * target_ratio
            source.setLeft((source.width() - cropped_width) / 2.0)
            source.setWidth(cropped_width)
        else:
            cropped_height = source.width() / target_ratio
            source.setTop((source.height() - cropped_height) / 2.0)
            source.setHeight(cropped_height)
        return target, source
    if source_ratio > target_ratio:
        width = target.width()
        height = width / source_ratio
    else:
        height = target.height()
        width = height * source_ratio
    return QRectF(target.x() + (target.width() - width) / 2.0, target.y() + (target.height() - height) / 2.0, width, height), source


class StageOutputWindow(QWidget):
    """Audience-facing composition window that keeps the studio controls free."""

    closed = Signal()

    def __init__(self, layer_provider: Callable[[], Mapping[str, QPixmap]], config: StageOutputConfig) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setObjectName("MetriqStageOutput")
        self.setWindowTitle("Metriq Visualizer · Stage Output")
        self.setMinimumSize(640, 360)
        self._layer_provider = layer_provider
        self.config = config.clone()
        self._background_image = QImage()
        self._video_frame = QImage()
        self._video_audio = QAudioOutput(self)
        self._video_audio.setVolume(0.0)
        self._video_player = QMediaPlayer(self)
        self._video_player.setAudioOutput(self._video_audio)
        self._video_sink = QVideoSink(self)
        self._video_sink.videoFrameChanged.connect(self._video_frame_changed)
        self._video_player.setVideoOutput(self._video_sink)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.update)
        self.set_config(config)

    def set_config(self, config: StageOutputConfig) -> None:
        self.config = config.clone().clamp()
        self._background_image = QImage()
        self._video_frame = QImage()
        self._video_player.stop()
        path = Path(self.config.background_path).expanduser()
        if self.config.background_kind == "image" and path.is_file():
            self._background_image = QImage(str(path))
        elif self.config.background_kind == "video" and path.is_file():
            self._video_player.setSource(QUrl.fromLocalFile(str(path.resolve())))
            self._video_player.setLoops(QMediaPlayer.Loops.Infinite)
            self._video_player.play()
        self._refresh_timer.start(max(33, round(1000 / self.config.refresh_fps)))
        self.update()

    def show_on_selected_screen(self) -> None:
        from PySide6.QtGui import QGuiApplication

        screens = QGuiApplication.screens()
        screen = next((item for item in screens if item.name() == self.config.screen_name), QGuiApplication.primaryScreen())
        if screen is not None:
            geometry = screen.availableGeometry()
            self.setGeometry(geometry)
            self.move(geometry.topLeft())
        if self.config.fullscreen:
            self.showFullScreen()
        else:
            self.show()
        self.raise_()

    def _video_frame_changed(self, frame: Any) -> None:
        if frame is not None and frame.isValid():
            self._video_frame = frame.toImage()
            self.update()

    def _draw_background(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor(self.config.background_color))
        image = self._video_frame if self.config.background_kind == "video" else self._background_image
        if not image.isNull():
            target = QRectF(self.rect())
            destination, source = _fit_rect(image.width(), image.height(), target, "cover")
            painter.drawImage(destination, image, source)

    def paintEvent(self, _event: QPaintEvent) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_background(painter)
        try:
            layers = self._layer_provider()
        except Exception:  # noqa: BLE001 - a stage display must not crash the operator UI
            layers = {}
        layout = self.config.layout.clone().clamp()
        width, height = max(1, self.width()), max(1, self.height())
        for name in layout.order:
            item = layout.item(name)
            if not item.enabled:
                continue
            target = QRectF(item.x * width, item.y * height, item.w * width, item.h * height)
            if target.width() < 4 or target.height() < 4:
                continue
            panel_color = QColor(8, 14, 21, round(255 * item.background_alpha))
            painter.fillRect(target, panel_color)
            painter.setPen(QColor(55, 126, 112, 190))
            painter.drawRect(target)
            title_height = max(0.0, min(30.0, target.height() * 0.14)) if item.show_title else 0.0
            if item.show_title:
                painter.setPen(QColor(210, 236, 231, 230))
                painter.drawText(
                    QRectF(target.left() + 8.0, target.top(), max(4.0, target.width() - 16.0), title_height),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    LAYOUT_ITEM_TITLES.get(name, name.title()).upper(),
                )
            content = QRectF(target.left(), target.top() + title_height, target.width(), max(1.0, target.height() - title_height))
            pixmap = layers.get(name)
            if pixmap is None or pixmap.isNull():
                continue
            painter.save()
            painter.setOpacity(item.content_alpha)
            destination, source = _fit_rect(pixmap.width(), pixmap.height(), content, item.fit_mode)
            painter.drawPixmap(destination, pixmap, source)
            painter.restore()
        painter.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._refresh_timer.stop()
        self._video_player.stop()
        self.closed.emit()
        super().closeEvent(event)


class StageOutputDialog(QDialog):
    """Configures display, background, layers, and placement before going live."""

    def __init__(self, config: StageOutputConfig, *, export_layout: ExportLayoutSpec | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Stage Output Setup")
        self.resize(1050, 700)
        self._config = config.clone()
        self._export_layout = (export_layout or balanced_export_layout()).clone().clamp()
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        controls = QVBoxLayout()
        controls.setSpacing(10)
        output_group = QGroupBox("Display")
        output_form = QFormLayout(output_group)
        self.screen_combo = QComboBox()
        from PySide6.QtGui import QGuiApplication

        for screen in QGuiApplication.screens():
            self.screen_combo.addItem(screen.name() or "Display", screen.name())
        self.fullscreen_check = QCheckBox("Fullscreen audience display")
        self.refresh_spin = QSpinBox()
        self.refresh_spin.setRange(5, 30)
        self.refresh_spin.setSuffix(" fps")
        output_form.addRow("Screen", self.screen_combo)
        output_form.addRow(self.fullscreen_check)
        output_form.addRow("Refresh", self.refresh_spin)
        controls.addWidget(output_group)

        background_group = QGroupBox("Background")
        background_form = QFormLayout(background_group)
        self.background_combo = QComboBox()
        self.background_combo.addItem("Solid color", "color")
        self.background_combo.addItem("Static image", "image")
        self.background_combo.addItem("Video loop", "video")
        self.background_path = QLineEdit()
        self.background_path.setReadOnly(True)
        self.choose_background_button = QPushButton("Choose…")
        self.choose_background_button.clicked.connect(self._choose_background)
        self.color_button = QPushButton()
        self.color_button.clicked.connect(self._choose_color)
        background_form.addRow("Type", self.background_combo)
        background_form.addRow("Asset", self.background_path)
        background_form.addRow("Browse", self.choose_background_button)
        background_form.addRow("Color", self.color_button)
        controls.addWidget(background_group)

        layout_group = QGroupBox("Composition")
        layout_form = QFormLayout(layout_group)
        self.layout_preset_combo = QComboBox()
        self.layout_preset_combo.addItem("Geometry focus", "geometry")
        self.layout_preset_combo.addItem("Balanced analysis", "balanced")
        self.layout_preset_combo.addItem("Analysis focus", "analysis")
        self.layout_preset_combo.addItem("Overlay", "overlay")
        self.layout_preset_combo.addItem("Mirror Export Studio", "export")
        apply_layout_button = QPushButton("Apply composition")
        apply_layout_button.clicked.connect(self._apply_layout_preset)
        layout_form.addRow(self.layout_preset_combo)
        layout_form.addRow(apply_layout_button)
        controls.addWidget(layout_group)

        layers_group = QGroupBox("Visible data")
        layers_layout = QGridLayout(layers_group)
        self.layer_checks: dict[str, QCheckBox] = {}
        for index, name in enumerate(LAYOUT_ITEM_ORDER):
            check = QCheckBox(LAYOUT_ITEM_TITLES[name])
            check.toggled.connect(self._layer_visibility_changed)
            self.layer_checks[name] = check
            layers_layout.addWidget(check, index // 2, index % 2)
        controls.addWidget(layers_group)
        controls.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        controls.addWidget(buttons)
        root.addLayout(controls, 0)

        preview = QVBoxLayout()
        preview.addWidget(QLabel("Drag and resize the same composition layers used by Export Studio."))
        self.canvas = LayoutCanvas()
        self.canvas.setMinimumSize(580, 430)
        self.canvas.layoutChanged.connect(self._layout_changed)
        preview.addWidget(self.canvas, 1)
        root.addLayout(preview, 1)

    def _populate(self) -> None:
        self.screen_combo.setCurrentIndex(max(0, self.screen_combo.findData(self._config.screen_name)))
        self.fullscreen_check.setChecked(self._config.fullscreen)
        self.refresh_spin.setValue(self._config.refresh_fps)
        self.background_combo.setCurrentIndex(max(0, self.background_combo.findData(self._config.background_kind)))
        self.background_path.setText(self._config.background_path)
        self._set_color_button(self._config.background_color)
        self.canvas.set_layout_spec(self._config.layout)
        for name, check in self.layer_checks.items():
            check.setChecked(self._config.layout.item(name).enabled)

    def _set_color_button(self, color_text: str) -> None:
        color = QColor(color_text)
        if not color.isValid():
            color = QColor("#070b11")
        self.color_button.setText(color.name().upper())
        self.color_button.setStyleSheet(f"background:{color.name()}; color:{'#000000' if color.lightness() > 150 else '#ffffff'};")

    def _choose_color(self) -> None:
        selected = QColorDialog.getColor(QColor(self._config.background_color), self, "Stage background color")
        if selected.isValid():
            self._config.background_color = selected.name()
            self._set_color_button(selected.name())

    def _choose_background(self) -> None:
        kind = str(self.background_combo.currentData())
        if kind == "color":
            self._choose_color()
            return
        filter_text = "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All files (*)" if kind == "image" else "Videos (*.mp4 *.mov *.m4v *.avi *.mkv *.webm);;All files (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Choose stage background", self._config.background_path or str(Path.home()), filter_text)
        if path:
            self._config.background_path = path
            self.background_path.setText(path)

    def _apply_layout_preset(self) -> None:
        key = str(self.layout_preset_combo.currentData())
        layouts = {
            "geometry": geometry_focus_export_layout,
            "balanced": balanced_export_layout,
            "analysis": analysis_focus_export_layout,
            "overlay": overlay_export_layout,
            "export": lambda: self._export_layout.clone(),
        }
        self._config.layout = layouts[key]().clamp()
        self.canvas.set_layout_spec(self._config.layout)
        for name, check in self.layer_checks.items():
            check.setChecked(self._config.layout.item(name).enabled)

    def _layout_changed(self, layout: ExportLayoutSpec) -> None:
        self._config.layout = layout.clone().clamp()
        for name, check in self.layer_checks.items():
            if check.isChecked() != self._config.layout.item(name).enabled:
                check.blockSignals(True)
                check.setChecked(self._config.layout.item(name).enabled)
                check.blockSignals(False)

    def _layer_visibility_changed(self) -> None:
        for name, check in self.layer_checks.items():
            self._config.layout.item(name).enabled = check.isChecked()
        self._config.layout.clamp()
        self.canvas.set_layout_spec(self._config.layout)

    def config(self) -> StageOutputConfig:
        self._config.screen_name = str(self.screen_combo.currentData() or "")
        self._config.fullscreen = self.fullscreen_check.isChecked()
        self._config.refresh_fps = self.refresh_spin.value()
        self._config.background_kind = str(self.background_combo.currentData())
        self._config.background_path = self.background_path.text().strip()
        return self._config.clone().clamp()


__all__ = ["BACKGROUND_KINDS", "STAGE_OUTPUT_SCHEMA", "StageOutputConfig", "StageOutputDialog", "StageOutputWindow"]
