# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Metriq Dynamics-inspired application chrome and boot sequence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class ThemePalette:
    name: str
    background: str
    surface: str
    surface_raised: str
    surface_soft: str
    border: str
    border_strong: str
    text: str
    muted: str
    accent: str
    accent_soft: str
    cyan: str
    blue: str
    danger: str
    warning: str
    grid: str


DARK: Final[ThemePalette] = ThemePalette(
    name="dark",
    background="#070b11",
    surface="#0b1118",
    surface_raised="#0e1721",
    surface_soft="#101c28",
    border="#263447",
    border_strong="#376078",
    text="#e8edf5",
    muted="#9aabba",
    accent="#06a269",
    accent_soft="#123f35",
    cyan="#0a91a7",
    blue="#5fa6f7",
    danger="#ed6a78",
    warning="#e6bd62",
    grid="#152331",
)

LIGHT: Final[ThemePalette] = ThemePalette(
    name="light",
    background="#edf2f4",
    surface="#f8fafb",
    surface_raised="#ffffff",
    surface_soft="#e8f0f2",
    border="#b6c7ce",
    border_strong="#678995",
    text="#10212a",
    muted="#516872",
    accent="#087d55",
    accent_soft="#cce8dd",
    cyan="#087d94",
    blue="#286cae",
    danger="#b73b4b",
    warning="#8a651c",
    grid="#d7e1e5",
)


_CURRENT = DARK


def palette_for(name: str) -> ThemePalette:
    return LIGHT if str(name).lower() == "light" else DARK


def current_palette() -> ThemePalette:
    return _CURRENT


def _build_stylesheet(p: ThemePalette) -> str:
    return f"""
    * {{
        font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
        font-size: 10pt;
        outline: none;
    }}
    QMainWindow, QDialog, QWidget#AppRoot {{
        background: {p.background};
        color: {p.text};
    }}
    QWidget {{ color: {p.text}; background: {p.background}; }}
    QToolTip {{
        color: {p.text};
        background: {p.surface_raised};
        border: 1px solid {p.border_strong};
        padding: 5px;
    }}
    QLabel#Eyebrow {{
        color: {p.accent};
        font-family: "JetBrains Mono", "Cascadia Mono", monospace;
        font-size: 8pt;
        font-weight: 700;
        letter-spacing: 2px;
    }}
    QLabel#Title {{
        color: {p.text};
        font-size: 19pt;
        font-weight: 650;
    }}
    QLabel#Subtle, QLabel[muted="true"] {{ color: {p.muted}; }}
    QLabel#StatusOnline {{
        color: {p.accent};
        font-family: "JetBrains Mono", "Cascadia Mono", monospace;
        font-size: 8pt;
        font-weight: 700;
    }}
    QFrame#Toolbar, QFrame#Panel, QFrame#Inspector {{
        background: {p.surface};
        border: 1px solid {p.border};
    }}
    QPushButton, QToolButton {{
        min-height: 30px;
        padding: 4px 12px;
        color: {p.text};
        background: {p.surface_raised};
        border: 1px solid {p.border};
        border-radius: 2px;
        font-weight: 600;
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {p.surface_soft};
        border-color: {p.border_strong};
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background: {p.accent_soft};
        border-color: {p.accent};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {p.muted};
        background: {p.surface};
        border-color: {p.border};
    }}
    QPushButton[accent="true"], QToolButton[accent="true"] {{
        color: #f6fffb;
        background: {p.accent};
        border-color: {p.accent};
    }}
    QPushButton[accent="true"]:hover, QToolButton[accent="true"]:hover {{
        background: {p.cyan};
        border-color: {p.cyan};
    }}
    QPushButton[danger="true"] {{ color: {p.danger}; }}
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        min-height: 27px;
        padding: 2px 7px;
        color: {p.text};
        selection-color: #ffffff;
        selection-background-color: {p.accent};
        background: {p.surface_raised};
        border: 1px solid {p.border};
        border-radius: 2px;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {p.accent};
    }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {p.surface_raised};
        color: {p.text};
        selection-background-color: {p.accent_soft};
        selection-color: {p.text};
        border: 1px solid {p.border_strong};
    }}
    QCheckBox, QRadioButton {{ spacing: 7px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 15px; height: 15px;
        background: {p.surface_raised};
        border: 1px solid {p.border_strong};
    }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {p.accent};
        border-color: {p.accent};
    }}
    QTabWidget::pane {{
        background: {p.surface};
        border: 1px solid {p.border};
        top: -1px;
    }}
    QTabBar::tab {{
        color: {p.muted};
        background: {p.surface};
        border: 1px solid transparent;
        border-bottom-color: {p.border};
        padding: 8px 12px;
        min-width: 62px;
    }}
    QTabBar::tab:hover {{ color: {p.text}; }}
    QTabBar::tab:selected {{
        color: {p.accent};
        border-color: {p.border};
        border-bottom-color: {p.surface};
        font-weight: 700;
    }}
    QGroupBox {{
        color: {p.text};
        background: transparent;
        border: 1px solid {p.border};
        margin-top: 10px;
        padding-top: 10px;
        font-weight: 700;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 9px;
        padding: 0 5px;
        color: {p.muted};
    }}
    QScrollArea {{ background: transparent; border: 0; }}
    QScrollBar:vertical {{ background: {p.background}; width: 11px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {p.border_strong}; min-height: 28px; border-radius: 4px; }}
    QScrollBar:horizontal {{ background: {p.background}; height: 11px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: {p.border_strong}; min-width: 28px; border-radius: 4px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QSplitter::handle {{ background: {p.background}; }}
    QSplitter::handle:hover {{ background: {p.border}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 14px; margin: -6px 0;
        background: {p.text}; border: 2px solid {p.accent}; border-radius: 7px;
    }}
    QProgressBar {{
        color: {p.text};
        background: {p.surface_raised};
        border: 1px solid {p.border};
        border-radius: 2px;
        text-align: center;
        min-height: 18px;
    }}
    QProgressBar::chunk {{ background: {p.accent}; }}
    QStatusBar {{
        color: {p.muted};
        background: {p.surface};
        border-top: 1px solid {p.border};
    }}
    QMenuBar {{ background: {p.surface}; color: {p.text}; border-bottom: 1px solid {p.border}; }}
    QMenuBar::item:selected {{ background: {p.surface_soft}; }}
    QMenu {{ background: {p.surface_raised}; color: {p.text}; border: 1px solid {p.border}; }}
    QMenu::item:selected {{ background: {p.accent_soft}; }}
    """


def apply_theme(app: QApplication, name: str) -> ThemePalette:
    global _CURRENT
    _CURRENT = palette_for(name)
    app.setStyle("Fusion")
    app.setStyleSheet(_build_stylesheet(_CURRENT))
    return _CURRENT


def cut_corner_path(rect: QRectF, cut: float = 12.0) -> QPainterPath:
    c = min(cut, max(0.0, rect.width() / 4), max(0.0, rect.height() / 4))
    path = QPainterPath()
    path.moveTo(rect.left() + c, rect.top())
    path.lineTo(rect.right(), rect.top())
    path.lineTo(rect.right(), rect.bottom() - c)
    path.lineTo(rect.right() - c, rect.bottom())
    path.lineTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + c)
    path.closeSubpath()
    return path


class CutCornerFrame(QFrame):
    """A lightweight panel whose border mirrors the Dynamics cut-corner motif."""

    def __init__(self, parent: QWidget | None = None, *, cut: int = 12) -> None:
        super().__init__(parent)
        self._cut = cut
        self.setObjectName("CutCornerFrame")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        path = cut_corner_path(rect, float(self._cut))
        painter.fillPath(path, QColor(p.surface))
        painter.setPen(QPen(QColor(p.border), 1.2))
        painter.drawPath(path)
        # Small active traces make the panel read as an interface component.
        painter.setPen(QPen(QColor(p.accent), 1.6))
        painter.drawLine(int(rect.left() + self._cut), int(rect.top()), int(rect.left() + self._cut + 30), int(rect.top()))
        painter.drawLine(int(rect.right()), int(rect.bottom() - self._cut - 22), int(rect.right()), int(rect.bottom() - self._cut))
        painter.end()
        super().paintEvent(event)


class TechHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 4)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        eyebrow = QLabel("METRIQ / VISUAL SYSTEM")
        eyebrow.setObjectName("Eyebrow")
        title_label = QLabel(title)
        title_label.setObjectName("Title")
        text_layout.addWidget(eyebrow)
        text_layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("Subtle")
            text_layout.addWidget(subtitle_label)
        layout.addLayout(text_layout)
        layout.addStretch(1)
        self.status_label = QLabel("● ONLINE")
        self.status_label.setObjectName("StatusOnline")
        layout.addWidget(self.status_label, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)


class BootOverlay(QWidget):
    """Local-only startup sequence; click or press Escape to skip."""

    finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BootOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._progress = 0.0
        self._step_index = 0
        self._steps = (
            "VERIFYING LOCAL RUNTIME",
            "INDEXING ANALYSIS MODULES",
            "CALIBRATING VISUAL PIPELINE",
            "MOUNTING EXPORT SYSTEM",
            "INTERFACE READY",
        )
        self._timer = QTimer(self)
        self._timer.setInterval(170)
        self._timer.timeout.connect(self._advance)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(260)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fade.finished.connect(self._complete)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 48, 48, 48)
        outer.addStretch(2)
        card = CutCornerFrame(self, cut=20)
        card.setMaximumWidth(680)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(38, 34, 38, 34)
        card_layout.setSpacing(13)
        system = QLabel("MD-VIS / ENTRY LOCAL")
        system.setObjectName("Eyebrow")
        title = QLabel("METRIQ VISUALIZER")
        title.setObjectName("Title")
        self._step_label = QLabel("00 / STANDBY")
        self._step_label.setObjectName("Subtle")
        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setTextVisible(False)
        hint = QLabel("CLICK OR ESC TO SKIP")
        hint.setObjectName("Subtle")
        card_layout.addWidget(system)
        card_layout.addWidget(title)
        card_layout.addSpacing(10)
        card_layout.addWidget(self._step_label)
        card_layout.addWidget(self._bar)
        card_layout.addWidget(hint, 0, Qt.AlignmentFlag.AlignRight)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)

    def get_progress(self) -> float:
        return self._progress

    def set_progress(self, value: float) -> None:
        self._progress = float(min(1.0, max(0.0, value)))
        self._bar.setValue(round(self._progress * 1000))
        self.update()

    progress = Property(float, get_progress, set_progress)

    def start(self) -> None:
        self._step_index = 0
        self.set_progress(0.0)
        self._opacity.setOpacity(1.0)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._timer.start()

    def _advance(self) -> None:
        if self._step_index >= len(self._steps):
            self._timer.stop()
            self._fade.start()
            return
        label = self._steps[self._step_index]
        self._step_label.setText(f"{self._step_index + 1:02d} / {label}")
        self._step_index += 1
        self.set_progress(self._step_index / len(self._steps))

    def skip(self) -> None:
        self._timer.stop()
        if self._fade.state() != QPropertyAnimation.State.Running:
            self._fade.start()

    def _complete(self) -> None:
        self.hide()
        self.finished.emit()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self.skip()
        event.accept()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.skip()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        p = current_palette()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(p.background))
        painter.setPen(QPen(QColor(p.grid), 1))
        spacing = 42
        for x in range(0, self.width(), spacing):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), spacing):
            painter.drawLine(0, y, self.width(), y)
        painter.setPen(QPen(QColor(p.accent), 1))
        painter.drawLine(0, 1, int(self.width() * self._progress), 1)
        painter.end()
        super().paintEvent(event)


__all__ = [
    "BootOverlay",
    "CutCornerFrame",
    "DARK",
    "LIGHT",
    "TechHeader",
    "ThemePalette",
    "apply_theme",
    "current_palette",
    "cut_corner_path",
    "palette_for",
]
