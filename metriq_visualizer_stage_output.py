# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Audience display built from snapshots of the live studio widgets."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)


@dataclass
class StageOutputConfig:
    screen_name: str = ""
    fullscreen: bool = True
    refresh_fps: int = 30
    viewport: bool = True
    analysis: bool = False
    logo: bool = False
    background_color: str = "#070b11"
    background_path: str = ""

    def clamp(self) -> "StageOutputConfig":
        self.screen_name = str(self.screen_name or "")
        self.fullscreen = bool(self.fullscreen)
        self.refresh_fps = min(30, max(5, int(self.refresh_fps)))
        self.viewport, self.analysis, self.logo = bool(self.viewport), bool(self.analysis), bool(self.logo)
        color = QColor(str(self.background_color or "#070b11"))
        self.background_color = color.name() if color.isValid() else "#070b11"
        self.background_path = str(self.background_path or "")
        return self

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return {"schema": "metriq.stage-output", "version": 1, **self.__dict__}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "StageOutputConfig":
        if not isinstance(payload, Mapping):
            return cls()
        keys = ("screen_name", "fullscreen", "refresh_fps", "viewport", "analysis", "logo", "background_color", "background_path")
        values = {key: payload[key] for key in keys if key in payload}
        try:
            return cls(**values).clamp()
        except (TypeError, ValueError):
            return cls().clamp()


class StageOutputWindow(QWidget):
    """Draws cached pixmaps supplied by the existing studio widgets."""
    closed = Signal()

    def __init__(self, layer_provider, config: StageOutputConfig):
        super().__init__(None, Qt.WindowType.Window)
        self.setObjectName("MetriqStageOutput")
        self.setWindowTitle("Metriq Visualizer · Stage Output")
        self.setMinimumSize(640, 360)
        self._layer_provider = layer_provider
        self.config = StageOutputConfig.from_dict(config.to_dict())
        self._background = QImage(self.config.background_path) if self.config.background_path else QImage()
        self._layers: Mapping[str, QPixmap] = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._capture)
        self._timer.start(max(33, round(1000 / self.config.refresh_fps)))

    def _capture(self):
        try:
            self._layers = self._layer_provider()
        except Exception:
            self._layers = {}
        self.update()

    def set_config(self, config: StageOutputConfig):
        self.config = StageOutputConfig.from_dict(config.to_dict())
        self._background = QImage(self.config.background_path) if self.config.background_path else QImage()
        self._timer.start(max(33, round(1000 / self.config.refresh_fps)))
        self.update()

    def show_on_selected_screen(self):
        from PySide6.QtGui import QGuiApplication
        screens = QGuiApplication.screens()
        screen = next((s for s in screens if s.name() == self.config.screen_name), None)
        if screen is None and screens:
            screen = screens[1] if len(screens) > 1 else screens[0]
        if screen is not None:
            self.setGeometry(screen.availableGeometry())
        self.showFullScreen() if self.config.fullscreen and len(screens) > 1 else self.show()
        self.raise_()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.config.background_color))
        if not self._background.isNull():
            painter.drawImage(self.rect(), self._background)
        rect = self.rect()
        if self.config.viewport and not self._layers.get("viewport", QPixmap()).isNull():
            vp = self._layers["viewport"]
            area = QRectF(rect).adjusted(rect.width() * .025, rect.height() * .025, -rect.width() * .025, -rect.height() * .20 if self.config.analysis else -rect.height() * .025)
            painter.drawPixmap(area, vp, QRectF(vp.rect()))
        if self.config.analysis and not self._layers.get("analysis", QPixmap()).isNull():
            panel = self._layers["analysis"]
            area = QRectF(rect.left() + rect.width() * .025, rect.top() + rect.height() * .79, rect.width() * .70, rect.height() * .18)
            painter.drawPixmap(area, panel, QRectF(panel.rect()))
        if self.config.logo and not self._layers.get("logo", QPixmap()).isNull():
            logo = self._layers["logo"]
            area = QRectF(rect.right() - rect.width() * .20, rect.top() + rect.height() * .81, rect.width() * .17, rect.height() * .14)
            painter.drawPixmap(area, logo, QRectF(logo.rect()))
        painter.end()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._timer.stop()
        self.closed.emit()
        super().closeEvent(event)


class StageOutputSettingsDialog(QDialog):
    def __init__(self, config: StageOutputConfig, screens, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stage Output Settings")
        self.setModal(True)
        self._config = StageOutputConfig.from_dict(config.to_dict())
        form = QFormLayout(self)
        self.screen = QComboBox()
        self.screen.addItem("Automatic", "")
        for item in screens:
            self.screen.addItem(item.name(), item.name())
        index = self.screen.findData(self._config.screen_name)
        self.screen.setCurrentIndex(max(0, index))
        self.fullscreen = QCheckBox("Fullscreen on selected display")
        self.fullscreen.setChecked(self._config.fullscreen)
        self.viewport = QCheckBox("Live viewport")
        self.viewport.setChecked(self._config.viewport)
        self.analysis = QCheckBox("Selected analysis panel")
        self.analysis.setChecked(self._config.analysis)
        self.logo = QCheckBox("Metriq logo")
        self.logo.setChecked(self._config.logo)
        self.fps = QSpinBox()
        self.fps.setRange(5, 30)
        self.fps.setValue(self._config.refresh_fps)
        self.color = QLineEdit(self._config.background_color)
        color_row = QHBoxLayout()
        color_button = QPushButton("Choose…")
        color_button.clicked.connect(self._choose_color)
        color_row.addWidget(self.color, 1)
        color_row.addWidget(color_button)
        path_row = QHBoxLayout()
        self.path = QLineEdit(self._config.background_path)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        form.addRow("Display", self.screen)
        form.addRow("", self.fullscreen)
        form.addRow("Layers", self.viewport)
        form.addRow("", self.analysis)
        form.addRow("", self.logo)
        form.addRow("Refresh (fps)", self.fps)
        form.addRow("Background colour", color_row)
        form.addRow("Background image", path_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose stage background image", self.path.text(), "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.path.setText(path)

    def _choose_color(self):
        selected = QColorDialog.getColor(QColor(self.color.text()), self, "Stage background colour")
        if selected.isValid():
            self.color.setText(selected.name())

    def config(self):
        return StageOutputConfig(screen_name=str(self.screen.currentData() or ""), fullscreen=self.fullscreen.isChecked(), refresh_fps=self.fps.value(), viewport=self.viewport.isChecked(), analysis=self.analysis.isChecked(), logo=self.logo.isChecked(), background_color=self.color.text(), background_path=self.path.text()).clamp()
