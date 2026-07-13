# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Metriq Visualizer desktop studio.

Version 1.12.5 preserves the original visualizer-first window and genuine
interactive 3D scene while repairing camera interaction, restoring configurable
audio extraction, integrating microphone trajectories into the main viewport,
and retaining the creator-oriented Export Studio around that core.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QElapsedTimer, QObject, QSettings, QSignalBlocker, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from metriq_visualizer_3d import Interactive3DViewport, ViewportCommandStrip
from metriq_visualizer_cache import analyze_source_cached, cache_directory, clear_cache
from metriq_visualizer_core import (
    DEFAULT_PRESETS,
    AnalysisResult,
    AnalysisSettings,
    GeometryResult,
    build_geometry,
    format_feature_reference,
)
from metriq_visualizer_data_export import export_analysis_csv, export_analysis_npz
from metriq_visualizer_export_studio import ExportStudioDialog
from metriq_visualizer_layout import ExportLayoutSpec, balanced_export_layout
from metriq_visualizer_live import LiveInputPanel
from metriq_visualizer_panels import AnalysisDockWidget
from metriq_visualizer_performance import (
    DEFAULT_PERFORMANCE_PROFILE,
    PERFORMANCE_PROFILES,
    AdaptivePreviewController,
    apply_live_limits,
    normalize_profile_name,
    profile_for,
)
from metriq_visualizer_preset_files import (
    build_preset_payload,
    discover_presets,
    load_preset,
    preset_display_name,
    save_preset,
)
from metriq_visualizer_projects import LEGACY_PROJECT_EXTENSIONS, build_project_payload, load_project, save_project
from metriq_visualizer_render import ExportOptions
from metriq_visualizer_theme import BootOverlay, CutCornerFrame, TechHeader, apply_theme

APP_NAME = "Metriq Visualizer"
APP_VERSION = "1.12.5"
APP_WINDOW_TITLE = f"{APP_NAME} v{APP_VERSION}"

SUPPORTED_FILES = (
    "Supported data (*.mp3 *.wav *.flac *.ogg *.opus *.m4a *.aac *.aiff *.aif *.mp4 *.mov *.avi *.mkv *.webm "
    "*.m4v *.mpg *.mpeg *.wmv *.flv *.csv *.tsv *.txt *.xlsx);;All files (*)"
)

ANALYSIS_PROFILES: dict[str, AnalysisSettings | None] = {
    "Balanced / native": AnalysisSettings(),
    "Legacy v1.10": AnalysisSettings(
        sample_rate=22_050,
        n_fft=2048,
        hop_length=256,
        min_frequency=0.0,
        max_frequency=0.0,
        max_frames=4096,
        n_mels=128,
        mfcc_count=13,
    ),
    "Fast analysis": AnalysisSettings(
        sample_rate=22_050,
        n_fft=1024,
        hop_length=512,
        min_frequency=0.0,
        max_frequency=11_025.0,
        max_frames=2048,
        n_mels=64,
        mfcc_count=13,
    ),
    "Birdsong detail": AnalysisSettings(
        sample_rate=44_100,
        n_fft=4096,
        hop_length=256,
        min_frequency=150.0,
        max_frequency=18_000.0,
        max_frames=8192,
        n_mels=128,
        mfcc_count=20,
    ),
    "Custom": None,
}


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _application_settings() -> QSettings:
    """Return the normal app settings, or an explicit isolated settings file.

    ``METRIQ_SETTINGS_PATH`` is intentionally opt-in.  It gives automated
    checks a reliable settings location on macOS, where ``QSettings`` ignores
    the XDG test directories and would otherwise overwrite a real user's
    recoverable session.
    """

    configured_path = os.environ.get("METRIQ_SETTINGS_PATH", "").strip()
    if not configured_path:
        return QSettings("Metriq", "Metriq Visualizer")
    settings_path = Path(configured_path).expanduser()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    return QSettings(str(settings_path), QSettings.Format.IniFormat)


class AnalysisWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, source_path: Path, settings: AnalysisSettings) -> None:
        super().__init__()
        self.source_path = source_path
        self.settings = settings.normalized()

    @Slot()
    def run(self) -> None:
        try:
            result = analyze_source_cached(self.source_path, settings=self.settings)
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.failed.emit(details[-8000:])


class MainWindow(QMainWindow):
    """Responsive desktop interface around Metriq's existing local renderer."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_WINDOW_TITLE)
        self.resize(1540, 940)
        self.setMinimumSize(1080, 700)
        self.setAcceptDrops(True)

        self.settings = _application_settings()
        self.theme_name = str(self.settings.value("theme", "dark"))
        self.recent_paths = self._load_recent_paths()
        self.visual_preset_paths: dict[str, Path] = {}
        self.performance_mode = normalize_profile_name(
            self.settings.value("performance_mode", DEFAULT_PERFORMANCE_PROFILE)
        )
        self.adaptive_preview = AdaptivePreviewController()
        self._last_preview_draw_ms = 0.0
        self._analysis_cursor_clock = QElapsedTimer()
        self._analysis_cursor_clock.start()
        self.analysis_settings = AnalysisSettings()
        self._applying_analysis_profile = False
        self.live_input_dock: QDockWidget | None = None
        self.live_input_panel: LiveInputPanel | None = None

        self.analysis: AnalysisResult | None = None
        self.geometry: GeometryResult | None = None
        self.source_path: Path | None = None
        self.project_path: Path | None = None
        self.current_layout: ExportLayoutSpec = balanced_export_layout()
        self.current_time = 0.0

        self.analysis_thread: QThread | None = None
        self.analysis_worker: AnalysisWorker | None = None
        self.pending_state: dict[str, Any] | None = None
        self.pending_seek: float | None = None

        # The main workspace owns a genuine interactive 3D scene. Export
        # composition is intentionally separate and reuses the same scene
        # renderer instead of replacing the live visualizer with a raster card.
        self.preview_session: Any = None  # compatibility alias for older integrations
        self.preview_session_dirty = True
        self._building_geometry = False
        self._applying_state = False

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(55)
        self.preview_timer.timeout.connect(self._render_preview)

        self.geometry_timer = QTimer(self)
        self.geometry_timer.setSingleShot(True)
        self.geometry_timer.setInterval(240)
        self.geometry_timer.timeout.connect(self.rebuild_geometry)

        self.play_timer = QTimer(self)
        self.play_timer.setInterval(25)
        self.play_timer.timeout.connect(self._play_tick)
        self.playback_render_timer = QTimer(self)
        self.playback_render_timer.setInterval(33)
        self.playback_render_timer.timeout.connect(self._render_playback_frame)
        self.play_clock = QElapsedTimer()
        self._playing = False
        self._using_media_clock = False
        self._media_failed = False
        self._media_source_path: Path | None = None
        self._media_has_audio = False
        self._media_last_position_ms = 0
        self._media_stall_clock = QElapsedTimer()

        self.audio_output = QAudioOutput(self)
        saved_volume = min(100, max(0, _safe_int(self.settings.value("playback_volume", 82), 82)))
        self.audio_output.setVolume(saved_volume / 100.0)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.errorOccurred.connect(self._media_error)
        self.media_player.mediaStatusChanged.connect(self._media_status_changed)

        self._build_ui()
        self._connect_shortcuts()
        self._apply_preset(self.preset_combo.currentText(), rebuild=False)
        self._apply_performance_profile(self.performance_mode, persist=False, mark_dirty=False)
        saved_visual_preset = str(self.settings.value("visual_preset", "")).strip()
        if saved_visual_preset and self.visual_preset_combo.findText(saved_visual_preset) >= 0:
            self.visual_preset_combo.setCurrentText(saved_visual_preset)
        self._set_theme(self.theme_name, persist=False)
        self._set_ready_state(False)
        self._set_status("Ready · drop a local media or table file to begin.")

        self.boot_overlay = BootOverlay(self.centralWidget())
        self.boot_overlay.finished.connect(lambda: self.open_button.setFocus())
        QTimer.singleShot(80, self._start_boot_overlay)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 10)
        root_layout.setSpacing(10)

        self.tech_header = TechHeader(
            "Metriq Visualizer",
            "Local analysis, educational visualization, creator export, and light scientific inspection",
        )
        root_layout.addWidget(self.tech_header)

        root_layout.addWidget(self._build_command_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_controls())
        splitter.addWidget(self._build_workspace())
        splitter.setSizes([450, 1080])
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, 1)

        self.status_bar = QStatusBar()
        self.status_progress = QProgressBar()
        self.status_progress.setRange(0, 0)
        self.status_progress.setMaximumWidth(210)
        self.status_progress.setVisible(False)
        self.status_bar.addPermanentWidget(self.status_progress)
        self.setStatusBar(self.status_bar)

    def _build_command_bar(self) -> QWidget:
        frame = CutCornerFrame(cut=10)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(7)

        self.open_button = QPushButton("Open data")
        self.open_button.setProperty("accent", True)
        self.open_button.clicked.connect(self.open_file)
        self.live_button = QPushButton("Live input")
        self.live_button.clicked.connect(self.open_live_input)
        self.rebuild_button = QPushButton("Rebuild geometry")
        self.rebuild_button.clicked.connect(self.rebuild_geometry)
        self.load_project_button = QPushButton("Load project")
        self.load_project_button.clicked.connect(self.load_project_dialog)
        self.save_project_button = QPushButton("Save project")
        self.save_project_button.clicked.connect(self.save_project_dialog)
        self.export_button = QPushButton("Export Studio")
        self.export_button.setProperty("accent", True)
        self.export_button.clicked.connect(self.open_export_studio)
        self.data_export_button = QPushButton("Export data")
        self.data_export_button.clicked.connect(self.export_data_dialog)
        self.restore_button = QPushButton("Restore session")
        self.restore_button.clicked.connect(self.restore_session)
        self.restore_button.setEnabled(self._recoverable_session() is not None)
        self.theme_button = QPushButton("Light mode")
        self.theme_button.clicked.connect(self.toggle_theme)

        for button in (
            self.open_button,
            self.live_button,
            self.rebuild_button,
            self.load_project_button,
            self.save_project_button,
            self.export_button,
            self.data_export_button,
            self.restore_button,
        ):
            layout.addWidget(button)
        self.recent_combo = QComboBox()
        self.recent_combo.setMinimumWidth(150)
        self.recent_combo.addItem("Recent sources…", "")
        for recent in self.recent_paths:
            self.recent_combo.addItem(Path(recent).name, recent)
        self.recent_combo.activated.connect(self._recent_selected)
        layout.addWidget(self.recent_combo)
        layout.addStretch(1)
        self.source_badge = QLabel("NO SOURCE")
        self.source_badge.setObjectName("Eyebrow")
        self.source_badge.setToolTip("Current local source")
        layout.addWidget(self.source_badge)
        layout.addWidget(self.theme_button)
        return frame

    def _build_controls(self) -> QWidget:
        tabs = QTabWidget()
        tabs.setMinimumWidth(390)
        tabs.addTab(self._build_mapping_tab(), "Mapping")
        tabs.addTab(self._build_appearance_tab(), "Appearance")
        tabs.addTab(self._build_data_tab(), "Data")
        return tabs

    @staticmethod
    def _scroll_panel() -> tuple[QScrollArea, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        scroll.setWidget(content)
        return scroll, layout

    def _build_mapping_tab(self) -> QWidget:
        scroll, layout = self._scroll_panel()

        preset_group = QGroupBox("Mapping preset")
        preset_form = QFormLayout(preset_group)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(DEFAULT_PRESETS.keys())
        self.preset_combo.currentTextChanged.connect(self._apply_preset)
        self.load_preset_button = QPushButton("Load preset file")
        self.load_preset_button.clicked.connect(self.load_preset_dialog)
        self.save_preset_button = QPushButton("Save preset file")
        self.save_preset_button.clicked.connect(self.save_preset_dialog)
        preset_actions = QWidget()
        preset_actions_layout = QHBoxLayout(preset_actions)
        preset_actions_layout.setContentsMargins(0, 0, 0, 0)
        preset_actions_layout.addWidget(self.load_preset_button)
        preset_actions_layout.addWidget(self.save_preset_button)
        preset_form.addRow("Preset", self.preset_combo)
        preset_form.addRow("Files", preset_actions)
        layout.addWidget(preset_group)

        analysis_group = QGroupBox("Audio analysis")
        analysis_form = QFormLayout(analysis_group)
        self.analysis_profile_combo = QComboBox()
        self.analysis_profile_combo.addItems(ANALYSIS_PROFILES.keys())
        self.analysis_profile_combo.currentTextChanged.connect(self._analysis_profile_selected)

        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItem("Native source rate", 0)
        for rate in (8_000, 16_000, 22_050, 32_000, 44_100, 48_000, 88_200, 96_000, 192_000):
            self.sample_rate_combo.addItem(f"{rate:,} Hz", rate)
        self.sample_rate_combo.currentIndexChanged.connect(self._analysis_setting_changed)

        self.fft_size_combo = QComboBox()
        for size in (256, 512, 1024, 2048, 4096, 8192, 16_384):
            self.fft_size_combo.addItem(f"{size:,}", size)
        self.fft_size_combo.currentIndexChanged.connect(self._analysis_setting_changed)

        self.hop_length_spin = QSpinBox()
        self.hop_length_spin.setRange(16, 16_384)
        self.hop_length_spin.setSingleStep(16)
        self.hop_length_spin.setValue(512)
        self.hop_length_spin.setSuffix(" samples")
        self.hop_length_spin.valueChanged.connect(self._analysis_setting_changed)

        self.min_frequency_spin = QDoubleSpinBox()
        self.min_frequency_spin.setRange(0.0, 96_000.0)
        self.min_frequency_spin.setDecimals(0)
        self.min_frequency_spin.setSingleStep(10.0)
        self.min_frequency_spin.setSuffix(" Hz")
        self.min_frequency_spin.valueChanged.connect(self._analysis_setting_changed)

        self.max_frequency_spin = QDoubleSpinBox()
        self.max_frequency_spin.setRange(0.0, 96_000.0)
        self.max_frequency_spin.setDecimals(0)
        self.max_frequency_spin.setSingleStep(10.0)
        self.max_frequency_spin.setSpecialValueText("Nyquist")
        self.max_frequency_spin.setSuffix(" Hz")
        self.max_frequency_spin.setToolTip("Zero uses the selected or native sample rate's Nyquist frequency.")
        self.max_frequency_spin.valueChanged.connect(self._analysis_setting_changed)

        self.mel_bands_spin = QSpinBox()
        self.mel_bands_spin.setRange(24, 256)
        self.mel_bands_spin.setSingleStep(4)
        self.mel_bands_spin.setValue(96)
        self.mel_bands_spin.valueChanged.connect(self._analysis_setting_changed)

        self.mfcc_count_spin = QSpinBox()
        self.mfcc_count_spin.setRange(4, 40)
        self.mfcc_count_spin.setSingleStep(1)
        self.mfcc_count_spin.setValue(20)
        self.mfcc_count_spin.valueChanged.connect(self._analysis_setting_changed)

        self.analysis_frames_spin = QSpinBox()
        self.analysis_frames_spin.setRange(128, 16_384)
        self.analysis_frames_spin.setSingleStep(64)
        self.analysis_frames_spin.setValue(4096)
        self.analysis_frames_spin.setSuffix(" frames")
        self.analysis_frames_spin.valueChanged.connect(self._analysis_setting_changed)

        self.reanalyze_button = QPushButton("Reanalyze source")
        self.reanalyze_button.setToolTip("Re-extract audio features with the current analysis settings.")
        self.reanalyze_button.clicked.connect(self.reanalyze_source)
        analysis_form.addRow("Profile", self.analysis_profile_combo)
        analysis_form.addRow("Sample rate", self.sample_rate_combo)
        analysis_form.addRow("FFT size", self.fft_size_combo)
        analysis_form.addRow("Hop length", self.hop_length_spin)
        analysis_form.addRow("Minimum frequency", self.min_frequency_spin)
        analysis_form.addRow("Maximum frequency", self.max_frequency_spin)
        analysis_form.addRow("Mel bands", self.mel_bands_spin)
        analysis_form.addRow("MFCC coefficients", self.mfcc_count_spin)
        analysis_form.addRow("Frame ceiling", self.analysis_frames_spin)
        analysis_form.addRow("Apply", self.reanalyze_button)
        layout.addWidget(analysis_group)
        self._apply_extraction_settings(self.analysis_settings, profile_name="Balanced / native")

        formula_group = QGroupBox("Feature formulas")
        formula_form = QFormLayout(formula_group)
        self.x_edit = QLineEdit()
        self.y_edit = QLineEdit()
        self.z_edit = QLineEdit()
        self.color_edit = QLineEdit()
        self.size_edit = QLineEdit()
        for label, widget in (
            ("X", self.x_edit),
            ("Y", self.y_edit),
            ("Z", self.z_edit),
            ("Color", self.color_edit),
            ("Size", self.size_edit),
        ):
            widget.setClearButtonEnabled(True)
            widget.editingFinished.connect(self._queue_geometry_rebuild)
            formula_form.addRow(label, widget)
        layout.addWidget(formula_group)

        sampling_group = QGroupBox("Sampling and normalization")
        sampling_form = QFormLayout(sampling_group)
        self.normalize_combo = QComboBox()
        self.normalize_combo.addItem("Z-score", "zscore")
        self.normalize_combo.addItem("Min–max", "minmax")
        self.normalize_combo.addItem("Raw values", "raw")
        self.normalize_combo.currentIndexChanged.connect(self._queue_geometry_rebuild)
        self.max_points_spin = QSpinBox()
        self.max_points_spin.setRange(100, 50_000)
        self.max_points_spin.setSingleStep(50)
        self.max_points_spin.setValue(3000)
        self.max_points_spin.setSuffix(" points")
        self.max_points_spin.setToolTip(
            "Maximum geometry density retained for high-quality preview and export. Live profiles apply a separate temporary cap."
        )
        self.max_points_spin.valueChanged.connect(self._queue_geometry_rebuild)
        self.cutoff_spin = QDoubleSpinBox()
        self.cutoff_spin.setRange(0.0, 120.0)
        self.cutoff_spin.setDecimals(2)
        self.cutoff_spin.setSingleStep(0.25)
        self.cutoff_spin.setValue(0.0)
        self.cutoff_spin.setSuffix(" dB")
        self.cutoff_spin.setToolTip("Suppress frames this far below the source peak. Zero disables the filter.")
        self.cutoff_spin.valueChanged.connect(self._queue_geometry_rebuild)
        sampling_form.addRow("Normalize", self.normalize_combo)
        sampling_form.addRow("Full geometry density", self.max_points_spin)
        sampling_form.addRow("Low-volume cutoff", self.cutoff_spin)
        layout.addWidget(sampling_group)

        formula_note = QLabel(
            "Formulas are evaluated locally against extracted features. Supported arithmetic and helper functions "
            "include abs, sqrt, log, exp, clip, smooth, mean, sum, min, and max."
        )
        formula_note.setObjectName("Subtle")
        formula_note.setWordWrap(True)
        layout.addWidget(formula_note)
        layout.addStretch(1)
        return scroll

    def _build_appearance_tab(self) -> QWidget:
        scroll, layout = self._scroll_panel()

        style_group = QGroupBox("Visual style")
        style_form = QFormLayout(style_group)
        self.visual_preset_combo = QComboBox()
        self.visual_preset_combo.setToolTip(
            "Applies appearance, camera, and geometry sampling without replacing mapping formulas or the selected live-performance profile."
        )
        self.reload_visual_presets_button = QPushButton("Reload preset folder")
        self.reload_visual_presets_button.clicked.connect(self._refresh_visual_presets)
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(["plasma", "viridis", "magma", "inferno", "cividis", "turbo", "coolwarm"])
        self.colormap_combo.currentTextChanged.connect(self._queue_geometry_rebuild)
        style_form.addRow("Style", self.visual_preset_combo)
        style_form.addRow("Color map", self.colormap_combo)
        style_form.addRow("Library", self.reload_visual_presets_button)
        layout.addWidget(style_group)
        self._refresh_visual_presets(connect_signal=True)

        performance_group = QGroupBox("Live performance")
        performance_form = QFormLayout(performance_group)
        self.performance_combo = QComboBox()
        self.performance_combo.addItems(PERFORMANCE_PROFILES.keys())
        self.performance_combo.setCurrentText(self.performance_mode)
        self.performance_combo.currentTextChanged.connect(self._performance_profile_selected)
        self.live_points_spin = QSpinBox()
        self.live_points_spin.setRange(100, 12_000)
        self.live_points_spin.setSingleStep(50)
        self.live_points_spin.setSuffix(" points")
        self.live_points_spin.valueChanged.connect(self._performance_limits_changed)
        self.live_fps_spin = QSpinBox()
        self.live_fps_spin.setRange(5, 60)
        self.live_fps_spin.setSuffix(" fps")
        self.live_fps_spin.valueChanged.connect(self._performance_limits_changed)
        self.adaptive_preview_check = QCheckBox("Adapt live density under load")
        saved_adaptive = str(self.settings.value("adaptive_preview", "true")).strip().casefold()
        self.adaptive_preview_check.setChecked(saved_adaptive not in {"0", "false", "no", "off"})
        self.adaptive_preview_check.setToolTip(
            "Measures completed live draws, lowers only the playback point budget when the target rate is missed, "
            "and restores density gradually when headroom returns. Busy draws are always coalesced. "
            "Saved settings and export remain unchanged."
        )
        self.adaptive_preview_check.toggled.connect(self._performance_limits_changed)
        self.refine_idle_check = QCheckBox("Refine to full scene when playback stops")
        saved_refine = str(self.settings.value("refine_idle", "false")).strip().casefold()
        self.refine_idle_check.setChecked(saved_refine not in {"0", "false", "no", "off"})
        self.refine_idle_check.setToolTip(
            "Uses the selected live profile during playback, then restores the complete scene while paused. Export is always complete."
        )
        self.refine_idle_check.toggled.connect(self._performance_limits_changed)
        self.performance_description = QLabel()
        self.performance_description.setObjectName("Subtle")
        self.performance_description.setWordWrap(True)
        performance_form.addRow("Profile", self.performance_combo)
        performance_form.addRow("Live density", self.live_points_spin)
        performance_form.addRow("Target rate", self.live_fps_spin)
        performance_form.addRow("Load protection", self.adaptive_preview_check)
        performance_form.addRow("Idle quality", self.refine_idle_check)
        performance_form.addRow(self.performance_description)
        layout.addWidget(performance_group)

        scene_group = QGroupBox("Geometry scene")
        form = QFormLayout(scene_group)
        self.render_mode_combo = QComboBox()
        self.render_mode_combo.addItems(["Points + line", "Points only", "Tube", "Tube + points"])
        self.history_combo = QComboBox()
        self.history_combo.addItems(["Trail fade", "Cumulative reveal", "Full static"])
        self.lifespan_spin = QDoubleSpinBox()
        self.lifespan_spin.setRange(0.05, 120.0)
        self.lifespan_spin.setDecimals(2)
        self.lifespan_spin.setSingleStep(0.05)
        self.lifespan_spin.setValue(3.0)
        self.lifespan_spin.setSuffix(" s")
        self.fade_curve_spin = QDoubleSpinBox()
        self.fade_curve_spin.setRange(0.1, 8.0)
        self.fade_curve_spin.setDecimals(2)
        self.fade_curve_spin.setSingleStep(0.05)
        self.fade_curve_spin.setValue(1.35)
        self.base_alpha_spin = QDoubleSpinBox()
        self.base_alpha_spin.setRange(0.05, 1.0)
        self.base_alpha_spin.setSingleStep(0.05)
        self.base_alpha_spin.setValue(0.82)
        self.line_width_spin = QDoubleSpinBox()
        self.line_width_spin.setRange(0.1, 12.0)
        self.line_width_spin.setDecimals(2)
        self.line_width_spin.setSingleStep(0.05)
        self.line_width_spin.setValue(1.35)
        self.point_scale_spin = QDoubleSpinBox()
        self.point_scale_spin.setRange(0.05, 5.0)
        self.point_scale_spin.setDecimals(2)
        self.point_scale_spin.setSingleStep(0.02)
        self.point_scale_spin.setValue(0.4)
        self.curve_combo = QComboBox()
        self.curve_combo.addItems(["Smooth spline", "Straight"])
        self.curve_detail_spin = QSpinBox()
        self.curve_detail_spin.setRange(1, 24)
        self.curve_detail_spin.setValue(4)
        form.addRow("Render mode", self.render_mode_combo)
        form.addRow("History", self.history_combo)
        form.addRow("Trail lifespan", self.lifespan_spin)
        form.addRow("Fade curve", self.fade_curve_spin)
        form.addRow("Base opacity", self.base_alpha_spin)
        form.addRow("Line width", self.line_width_spin)
        form.addRow("Point scale", self.point_scale_spin)
        form.addRow("Path", self.curve_combo)
        form.addRow("Curve detail", self.curve_detail_spin)
        layout.addWidget(scene_group)

        toggles_group = QGroupBox("Visibility")
        toggles_layout = QVBoxLayout(toggles_group)
        self.connect_lines_check = QCheckBox("Connect points")
        self.connect_lines_check.setChecked(True)
        self.ghost_path_check = QCheckBox("Show ghost path")
        self.head_marker_check = QCheckBox("Show current head marker")
        self.head_marker_check.setChecked(True)
        self.axes_check = QCheckBox("Show axes")
        self.axes_check.setChecked(True)
        self.grid_check = QCheckBox("Show grid planes")
        self.grid_check.setChecked(True)
        self.axis_labels_check = QCheckBox("Show axis labels")
        self.axis_labels_check.setChecked(True)
        self.colorbar_check = QCheckBox("Show color scale")
        for widget in (
            self.connect_lines_check,
            self.ghost_path_check,
            self.head_marker_check,
            self.axes_check,
            self.grid_check,
            self.axis_labels_check,
            self.colorbar_check,
        ):
            toggles_layout.addWidget(widget)
        layout.addWidget(toggles_group)

        motion_group = QGroupBox("Motion accents")
        motion_form = QFormLayout(motion_group)
        self.comet_duration_spin = QDoubleSpinBox()
        self.comet_duration_spin.setRange(0.0, 5.0)
        self.comet_duration_spin.setDecimals(2)
        self.comet_duration_spin.setSingleStep(0.05)
        self.comet_duration_spin.setValue(0.45)
        self.comet_duration_spin.setSuffix(" s")
        self.comet_duration_spin.setToolTip(
            "Length of the bright directional trail behind the playback head. Zero disables it."
        )
        self.flash_duration_spin = QDoubleSpinBox()
        self.flash_duration_spin.setRange(0.0, 2.0)
        self.flash_duration_spin.setDecimals(3)
        self.flash_duration_spin.setSingleStep(0.01)
        self.flash_duration_spin.setValue(0.18)
        self.flash_duration_spin.setSuffix(" s")
        self.flash_duration_spin.setToolTip("Decay time for the head impact flash. Zero disables it.")
        self.head_size_spin = QDoubleSpinBox()
        self.head_size_spin.setRange(0.0, 2.0)
        self.head_size_spin.setDecimals(2)
        self.head_size_spin.setSingleStep(0.02)
        self.head_size_spin.setValue(0.24)
        self.halo_size_spin = QDoubleSpinBox()
        self.halo_size_spin.setRange(0.0, 3.0)
        self.halo_size_spin.setDecimals(2)
        self.halo_size_spin.setSingleStep(0.05)
        self.halo_size_spin.setValue(0.45)
        self.flash_size_spin = QDoubleSpinBox()
        self.flash_size_spin.setRange(0.0, 1.0)
        self.flash_size_spin.setDecimals(2)
        self.flash_size_spin.setSingleStep(0.01)
        self.flash_size_spin.setValue(0.05)
        motion_form.addRow("Comet duration", self.comet_duration_spin)
        motion_form.addRow("Flash duration", self.flash_duration_spin)
        motion_form.addRow("Head scale", self.head_size_spin)
        motion_form.addRow("Halo scale", self.halo_size_spin)
        motion_form.addRow("Flash scale", self.flash_size_spin)
        layout.addWidget(motion_group)

        camera_group = QGroupBox("Camera")
        camera_form = QFormLayout(camera_group)
        self.elev_spin = QDoubleSpinBox()
        self.elev_spin.setRange(-90.0, 90.0)
        self.elev_spin.setDecimals(1)
        self.elev_spin.setSingleStep(0.5)
        self.elev_spin.setValue(24.0)
        self.elev_spin.setSuffix("°")
        self.azim_spin = QDoubleSpinBox()
        self.azim_spin.setRange(-360.0, 360.0)
        self.azim_spin.setDecimals(1)
        self.azim_spin.setSingleStep(0.5)
        self.azim_spin.setValue(35.0)
        self.azim_spin.setSuffix("°")
        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(0.2, 5.0)
        self.zoom_spin.setDecimals(2)
        self.zoom_spin.setSingleStep(0.02)
        self.zoom_spin.setValue(1.0)
        self.autorotate_check = QCheckBox("Autorotate")
        self.autorotate_check.setChecked(True)
        self.rotation_speed_spin = QDoubleSpinBox()
        self.rotation_speed_spin.setRange(-180.0, 180.0)
        self.rotation_speed_spin.setDecimals(2)
        self.rotation_speed_spin.setSingleStep(0.25)
        self.rotation_speed_spin.setValue(16.0)
        self.rotation_speed_spin.setSuffix("°/s")
        camera_form.addRow("Elevation", self.elev_spin)
        camera_form.addRow("Azimuth", self.azim_spin)
        camera_form.addRow("Zoom", self.zoom_spin)
        camera_form.addRow("Motion", self.autorotate_check)
        camera_form.addRow("Rotation speed", self.rotation_speed_spin)
        layout.addWidget(camera_group)

        tube_group = QGroupBox("Tube rendering")
        tube_form = QFormLayout(tube_group)
        self.tube_radius_spin = QDoubleSpinBox()
        self.tube_radius_spin.setRange(0.05, 5.0)
        self.tube_radius_spin.setDecimals(2)
        self.tube_radius_spin.setSingleStep(0.02)
        self.tube_radius_spin.setValue(1.0)
        self.tube_sides_spin = QSpinBox()
        self.tube_sides_spin.setRange(3, 48)
        self.tube_sides_spin.setValue(12)
        self.tube_sides_spin.setToolTip("Controls spline and edge-rounding quality for the rendered tube.")
        self.tube_follow_check = QCheckBox("Follow point size")
        self.tube_follow_check.setChecked(True)
        self.tube_taper_spin = QDoubleSpinBox()
        self.tube_taper_spin.setRange(0.0, 1.0)
        self.tube_taper_spin.setSingleStep(0.02)
        self.tube_taper_spin.setValue(0.2)
        tube_form.addRow("Radius scale", self.tube_radius_spin)
        tube_form.addRow("Roundness", self.tube_sides_spin)
        tube_form.addRow("Sizing", self.tube_follow_check)
        tube_form.addRow("Taper", self.tube_taper_spin)
        layout.addWidget(tube_group)

        labels_group = QGroupBox("Point labels")
        labels_form = QFormLayout(labels_group)
        self.point_label_mode_combo = QComboBox()
        self.point_label_mode_combo.addItems(["Off", "Current point", "Visible points"])
        self.point_label_content_combo = QComboBox()
        self.point_label_content_combo.addItems(["Time + Hz", "Time", "Dominant Hz", "Index"])
        self.max_labels_spin = QSpinBox()
        self.max_labels_spin.setRange(1, 100)
        self.max_labels_spin.setValue(8)
        labels_form.addRow("Mode", self.point_label_mode_combo)
        labels_form.addRow("Content", self.point_label_content_combo)
        labels_form.addRow("Maximum", self.max_labels_spin)
        layout.addWidget(labels_group)

        for widget in self._visual_widgets():
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self._visual_setting_changed)
            elif hasattr(widget, "currentIndexChanged"):
                widget.currentIndexChanged.connect(self._visual_setting_changed)
            elif hasattr(widget, "toggled"):
                widget.toggled.connect(self._visual_setting_changed)
        layout.addStretch(1)
        return scroll

    def _build_data_tab(self) -> QWidget:
        scroll, layout = self._scroll_panel()
        summary_group = QGroupBox("Current source")
        summary_layout = QFormLayout(summary_group)
        self.summary_source = QLabel("Not loaded")
        self.summary_source.setWordWrap(True)
        self.summary_kind = QLabel("—")
        self.summary_duration = QLabel("—")
        self.summary_frames = QLabel("—")
        self.summary_points = QLabel("—")
        summary_layout.addRow("File", self.summary_source)
        summary_layout.addRow("Kind", self.summary_kind)
        summary_layout.addRow("Duration", self.summary_duration)
        summary_layout.addRow("Analysis frames", self.summary_frames)
        summary_layout.addRow("Visible points", self.summary_points)
        layout.addWidget(summary_group)

        tools_group = QGroupBox("Analysis portability")
        tools_layout = QVBoxLayout(tools_group)
        export_note = QLabel("Export every analyzed feature plus the current mapped geometry to CSV or compressed NPZ.")
        export_note.setObjectName("Subtle")
        export_note.setWordWrap(True)
        self.data_tab_export_button = QPushButton("Export analyzed and mapped data")
        self.data_tab_export_button.clicked.connect(self.export_data_dialog)
        self.clear_cache_button = QPushButton("Clear local analysis cache")
        self.clear_cache_button.clicked.connect(self.clear_analysis_cache)
        self.cache_path_label = QLabel(str(cache_directory()))
        self.cache_path_label.setObjectName("Subtle")
        self.cache_path_label.setWordWrap(True)
        tools_layout.addWidget(export_note)
        tools_layout.addWidget(self.data_tab_export_button)
        tools_layout.addWidget(self.clear_cache_button)
        tools_layout.addWidget(self.cache_path_label)
        layout.addWidget(tools_group)

        reference_group = QGroupBox("Feature reference")
        reference_layout = QVBoxLayout(reference_group)
        self.info_box = QTextEdit()
        self.info_box.setReadOnly(True)
        self.info_box.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.info_box.setPlaceholderText("Feature descriptions appear after analysis.")
        reference_layout.addWidget(self.info_box)
        layout.addWidget(reference_group, 1)

        note = QLabel(
            "Analysis is local. Source files, microphone audio, projects, presets, and rendered frames are not sent "
            "to a remote service by this application."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        layout.addWidget(note)
        return scroll

    def _build_workspace(self) -> QWidget:
        frame = CutCornerFrame(cut=14)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.viewport_commands = ViewportCommandStrip(frame)
        self.viewport_commands.resetRequested.connect(self._reset_viewport_camera)
        top.addWidget(self.viewport_commands, 1)
        self.preview_status = QLabel("IDLE")
        self.preview_status.setObjectName("Subtle")
        self.preview_status.setFixedWidth(690)
        self.preview_status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.preview_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.preview_status.setStyleSheet("font-family: monospace;")
        top.addWidget(self.preview_status)
        layout.addLayout(top)

        # Preserve the visualizer as the dominant product surface. Scientific
        # panels are a subordinate bottom dock, never a replacement for 3D.
        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical, frame)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.viewport = Interactive3DViewport(self.workspace_splitter)
        self.viewport.cameraChanged.connect(self._viewport_camera_changed)
        self.viewport.interactionStarted.connect(self._viewport_interaction_started)
        self.viewport.frameRendered.connect(self._preview_frame_rendered)
        # Compatibility name retained for external scripts that used the old
        # QLabel-based preview. It points only to the empty-state label.
        self.preview_label = self.viewport.placeholder
        self.analysis_dock = AnalysisDockWidget(self.workspace_splitter)
        self.analysis_dock.set_media_player(self.media_player)
        self.analysis_dock.collapsedChanged.connect(self._analysis_dock_collapsed_changed)
        self.workspace_splitter.addWidget(self.viewport)
        self.workspace_splitter.addWidget(self.analysis_dock)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        self.workspace_splitter.setSizes([760, 190])
        # Default to the classic, visualizer-first workspace. The dock remains
        # visible as a bottom command strip and expands in place when needed.
        self.analysis_dock.toggle_collapsed()
        layout.addWidget(self.workspace_splitter, 1)

        timeline = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)
        self.time_label = QLabel("00:00.000 / 00:00.000")
        self.time_label.setFixedWidth(188)
        self.time_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time_label.setStyleSheet("font-family: monospace;")
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 10_000)
        self.time_slider.valueChanged.connect(self._timeline_changed)
        self.loop_check = QCheckBox("Loop")
        self.loop_check.setChecked(True)
        self.loop_check.toggled.connect(self._loop_toggled)
        self.volume_label = QLabel("VOL")
        self.volume_label.setObjectName("Subtle")
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(round(self.audio_output.volume() * 100))
        self.volume_slider.setMaximumWidth(110)
        self.volume_slider.setToolTip("Source-audio playback volume")
        self.volume_slider.valueChanged.connect(self._volume_changed)
        self.mute_check = QCheckBox("Mute")
        self.mute_check.toggled.connect(self._mute_toggled)
        timeline.addWidget(self.play_button)
        timeline.addWidget(self.time_label)
        timeline.addWidget(self.time_slider, 1)
        timeline.addWidget(self.loop_check)
        timeline.addWidget(self.volume_label)
        timeline.addWidget(self.volume_slider)
        timeline.addWidget(self.mute_check)
        layout.addLayout(timeline)

        hint = QLabel(
            "Drag in the 3D field to orbit · scroll to zoom · Space play/pause · Ctrl+E Export Studio · "
            "analysis panels remain docked below the original visualizer workflow."
        )
        hint.setObjectName("Subtle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return frame

    def _connect_shortcuts(self) -> None:
        shortcuts = (
            ("Ctrl+O", self.open_file),
            ("Ctrl+Shift+O", self.load_project_dialog),
            ("Ctrl+S", self.save_project_dialog),
            ("Ctrl+E", self.open_export_studio),
            ("Ctrl+Shift+E", self.export_data_dialog),
            ("Ctrl+L", self.open_live_input),
            ("F5", self.rebuild_geometry),
            ("Space", self.toggle_playback),
        )
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    # ---------------------------------------------------------- Data loading
    def open_file(self) -> None:
        path_text, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open local media or table",
            str(self.source_path.parent if self.source_path else Path.home()),
            SUPPORTED_FILES,
        )
        if path_text:
            self._route_path(Path(path_text))

    def _route_path(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix == ".mvproj" or suffix in LEGACY_PROJECT_EXTENSIONS:
            self._load_project_path(path)
        elif suffix == ".mvpreset":
            self._load_preset_path(path)
        else:
            self._start_analysis(path)

    def _start_analysis(self, path: Path, *, state: Mapping[str, Any] | None = None) -> None:
        if self.analysis_thread is not None:
            QMessageBox.information(
                self, "Analysis in progress", "Finish the current analysis before opening another source."
            )
            return
        if not path.exists():
            QMessageBox.warning(self, "Source not found", f"The source file does not exist:\n{path}")
            return
        self.stop_playback()
        extraction_state = state.get("extraction") if isinstance(state, Mapping) else None
        if isinstance(extraction_state, Mapping):
            profile_name = str(extraction_state.get("profile", "")).strip() or None
            self._apply_extraction_settings(
                AnalysisSettings.from_mapping(extraction_state),
                profile_name=profile_name,
            )
        self.analysis_settings = self._current_analysis_settings()
        self._clear_media_source()
        self._close_preview_session()
        self.source_path = path.resolve()
        if state is None:
            self.project_path = None
        self.analysis = None
        self.geometry = None
        self.current_time = 0.0
        self.pending_seek = None
        self.pending_state = dict(state) if isinstance(state, Mapping) else None
        with QSignalBlocker(self.time_slider):
            self.time_slider.setValue(0)
        self._update_time_label()
        self._set_ready_state(False)
        self.open_button.setEnabled(False)
        self.live_button.setEnabled(False)
        self.source_badge.setText(f"ANALYZING / {path.name.upper()}")
        self.viewport.clear_scene(f"ANALYZING LOCAL SOURCE\n\n{path.name}")
        self.analysis_dock.set_data(None)
        self.preview_status.setText("ANALYSIS ACTIVE")
        self.status_progress.setVisible(True)
        self.status_progress.setRange(0, 0)
        self._set_status(f"Analyzing {path.name}…")

        self.analysis_worker = AnalysisWorker(self.source_path, self.analysis_settings)
        self.analysis_thread = QThread(self)
        self.analysis_worker.moveToThread(self.analysis_thread)
        self.analysis_thread.started.connect(self.analysis_worker.run)
        self.analysis_worker.finished.connect(self._analysis_finished)
        self.analysis_worker.failed.connect(self._analysis_failed)
        self.analysis_worker.finished.connect(self.analysis_thread.quit)
        self.analysis_worker.failed.connect(self.analysis_thread.quit)
        self.analysis_thread.finished.connect(self._analysis_thread_finished)
        self.analysis_thread.start()

    @Slot(object)
    def _analysis_finished(self, result: AnalysisResult) -> None:
        self.analysis = result
        metadata_settings = result.metadata.get("analysis_settings") if isinstance(result.metadata, Mapping) else None
        if isinstance(metadata_settings, Mapping):
            self.analysis_settings = AnalysisSettings.from_mapping(metadata_settings)
        self.source_path = Path(str(result.source_path)).expanduser()
        self._configure_media_source()
        self.analysis_dock.set_data(result, None)
        state = self.pending_state
        self.pending_state = None
        if state:
            self._apply_state(state, rebuild=False)
        elif str(getattr(result, "source_kind", "media")) == "table" and not self._applying_state:
            index = self.preset_combo.findText("Table / PCA explorer")
            if index >= 0:
                with QSignalBlocker(self.preset_combo):
                    self.preset_combo.setCurrentIndex(index)
                self._apply_preset("Table / PCA explorer", rebuild=False)
        self.info_box.setPlainText(format_feature_reference(result))
        self._update_data_summary()
        self.rebuild_geometry()
        if self.pending_seek is not None:
            self._seek_seconds(self.pending_seek)
            self.pending_seek = None
        self._remember_recent(self.source_path)
        cache_note = " from analysis cache" if bool(result.metadata.get("cache_hit")) else ""
        self._set_status(f"Loaded {self.source_path.name}{cache_note}.")
        self._update_reanalyze_state()

    @Slot(str)
    def _analysis_failed(self, details: str) -> None:
        self.analysis = None
        self.geometry = None
        self.pending_state = None
        self.pending_seek = None
        self._clear_media_source()
        self.viewport.clear_scene("ANALYSIS FAILED\n\nSee the error dialog for details.")
        self.analysis_dock.set_data(None)
        self.preview_status.setText("ERROR")
        QMessageBox.critical(self, "Could not analyze source", details)
        self._set_status("Analysis failed.")

    @Slot()
    def _analysis_thread_finished(self) -> None:
        thread = self.analysis_thread
        worker = self.analysis_worker
        self.analysis_thread = None
        self.analysis_worker = None
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()
        self.open_button.setEnabled(True)
        self.live_button.setEnabled(True)
        self._update_reanalyze_state()
        self.status_progress.setVisible(False)
        self.status_progress.setRange(0, 100)
        ready = self.analysis is not None and self.geometry is not None
        self._set_ready_state(ready)
        if ready:
            self._schedule_preview()

    # ----------------------------------------------- Audio extraction settings
    def _analysis_setting_widgets(self) -> tuple[QWidget, ...]:
        return (
            self.sample_rate_combo,
            self.fft_size_combo,
            self.hop_length_spin,
            self.min_frequency_spin,
            self.max_frequency_spin,
            self.mel_bands_spin,
            self.mfcc_count_spin,
            self.analysis_frames_spin,
        )

    def _current_analysis_settings(self) -> AnalysisSettings:
        return AnalysisSettings(
            sample_rate=int(self.sample_rate_combo.currentData() or 0),
            n_fft=int(self.fft_size_combo.currentData() or 2048),
            hop_length=int(self.hop_length_spin.value()),
            min_frequency=float(self.min_frequency_spin.value()),
            max_frequency=float(self.max_frequency_spin.value()),
            max_frames=int(self.analysis_frames_spin.value()),
            n_mels=int(self.mel_bands_spin.value()),
            mfcc_count=int(self.mfcc_count_spin.value()),
        ).normalized()

    @staticmethod
    def _matching_analysis_profile(settings: AnalysisSettings) -> str:
        signature = settings.normalized().signature()
        for name, candidate in ANALYSIS_PROFILES.items():
            if candidate is not None and candidate.normalized().signature() == signature:
                return name
        return "Custom"

    def _apply_extraction_settings(
        self,
        settings: AnalysisSettings | Mapping[str, Any],
        *,
        profile_name: str | None = None,
    ) -> None:
        configured = (
            settings.normalized() if isinstance(settings, AnalysisSettings) else AnalysisSettings.from_mapping(settings)
        )
        self._applying_analysis_profile = True
        try:
            blockers = [QSignalBlocker(widget) for widget in self._analysis_setting_widgets()]
            sample_index = self.sample_rate_combo.findData(configured.sample_rate)
            self.sample_rate_combo.setCurrentIndex(max(0, sample_index))
            fft_index = self.fft_size_combo.findData(configured.n_fft)
            self.fft_size_combo.setCurrentIndex(max(0, fft_index))
            self.hop_length_spin.setMaximum(configured.n_fft)
            self.hop_length_spin.setValue(configured.hop_length)
            self.min_frequency_spin.setValue(configured.min_frequency)
            self.max_frequency_spin.setValue(configured.max_frequency)
            self.mel_bands_spin.setValue(configured.n_mels)
            self.mfcc_count_spin.setValue(configured.mfcc_count)
            self.analysis_frames_spin.setValue(configured.max_frames)
            del blockers
            selected_profile = profile_name or self._matching_analysis_profile(configured)
            if selected_profile not in ANALYSIS_PROFILES:
                selected_profile = self._matching_analysis_profile(configured)
            with QSignalBlocker(self.analysis_profile_combo):
                self.analysis_profile_combo.setCurrentText(selected_profile)
        finally:
            self._applying_analysis_profile = False
        self.analysis_settings = configured
        self._update_reanalyze_state()

    def _analysis_profile_selected(self, name: str) -> None:
        if self._applying_analysis_profile or self._applying_state:
            return
        selected = ANALYSIS_PROFILES.get(str(name))
        if selected is None:
            return
        self._apply_extraction_settings(selected, profile_name=str(name))
        self._analysis_setting_changed()

    def _analysis_setting_changed(self, *_args) -> None:
        if self._applying_analysis_profile or self._applying_state:
            return
        # Keep hop length legal as FFT size changes. This is functional DSP
        # state, not inert preset metadata.
        n_fft = int(self.fft_size_combo.currentData() or 2048)
        self.hop_length_spin.setMaximum(n_fft)
        if self.hop_length_spin.value() > n_fft:
            with QSignalBlocker(self.hop_length_spin):
                self.hop_length_spin.setValue(n_fft)
        # Keep an explicit upper frequency above the selected lower bound.
        # Zero remains the intentional “use Nyquist” sentinel.
        minimum_frequency = float(self.min_frequency_spin.value())
        maximum_frequency = float(self.max_frequency_spin.value())
        if maximum_frequency > 0.0 and maximum_frequency <= minimum_frequency:
            with QSignalBlocker(self.max_frequency_spin):
                self.max_frequency_spin.setValue(min(96_000.0, minimum_frequency + 10.0))
        configured = self._current_analysis_settings()
        self.analysis_settings = configured
        profile_name = self._matching_analysis_profile(configured)
        with QSignalBlocker(self.analysis_profile_combo):
            self.analysis_profile_combo.setCurrentText(profile_name)
        self._update_reanalyze_state()
        if self.analysis is not None and str(self.analysis.source_kind) != "table":
            self._set_status("Audio analysis settings changed · select Reanalyze source to apply them.")

    def _update_reanalyze_state(self) -> None:
        if not hasattr(self, "reanalyze_button"):
            return
        media_ready = (
            self.source_path is not None
            and self.analysis_thread is None
            and (self.analysis is None or str(getattr(self.analysis, "source_kind", "media")) != "table")
        )
        self.reanalyze_button.setEnabled(media_ready)

    def reanalyze_source(self) -> None:
        if self.source_path is None or self.analysis_thread is not None:
            return
        if self.analysis is not None and str(self.analysis.source_kind) == "table":
            self._set_status("Table sources do not use audio extraction settings.")
            return
        state = self._capture_state(include_session=False)
        self._start_analysis(self.source_path, state=state)

    # ------------------------------------------------ Preset/performance library
    def _refresh_visual_presets(self, *_args, connect_signal: bool = False) -> None:
        paths = discover_presets()
        selected = self.visual_preset_combo.currentText() if hasattr(self, "visual_preset_combo") else "Custom"
        self.visual_preset_paths = paths
        if not hasattr(self, "visual_preset_combo"):
            return
        with QSignalBlocker(self.visual_preset_combo):
            self.visual_preset_combo.clear()
            self.visual_preset_combo.addItem("Custom")
            for name in paths:
                self.visual_preset_combo.addItem(name)
            self._set_combo_text(self.visual_preset_combo, selected)
        if connect_signal and not getattr(self, "_visual_preset_signal_connected", False):
            self.visual_preset_combo.currentTextChanged.connect(self._visual_preset_selected)
            self._visual_preset_signal_connected = True

    def _visual_preset_selected(self, name: str) -> None:
        if self._applying_state or not name or name == "Custom":
            return
        path = self.visual_preset_paths.get(name)
        if path is None:
            return
        try:
            payload = load_preset(path)
            state = payload.get("state") or {}
            if not isinstance(state, Mapping):
                raise ValueError("Preset state is missing or invalid.")
            # The historical behavior-preset dropdown intentionally leaves the
            # current mapping alone.  A full preset can still be loaded through
            # the adjacent file command in the Mapping tab.
            appearance_state = {key: state[key] for key in ("geometry", "visual") if key in state}
            self._apply_state(appearance_state, rebuild=self.analysis is not None)
            self.settings.setValue("visual_preset", name)
            self._set_status(f"Applied visual style {name}.")
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not apply visual style", exc)

    def _performance_profile_selected(self, name: str) -> None:
        if not self._applying_state:
            self._apply_performance_profile(name)

    def _apply_performance_profile(
        self,
        name: str,
        *,
        persist: bool = True,
        mark_dirty: bool = True,
    ) -> None:
        normalized = normalize_profile_name(name)
        profile = profile_for(normalized)
        self.performance_mode = normalized
        if hasattr(self, "performance_combo"):
            with QSignalBlocker(self.performance_combo):
                self.performance_combo.setCurrentText(normalized)
            with QSignalBlocker(self.live_points_spin):
                self.live_points_spin.setValue(profile.point_budget)
            with QSignalBlocker(self.live_fps_spin):
                self.live_fps_spin.setValue(profile.target_fps)
            self.performance_description.setText(
                f"{profile.description} Live-only limits; Export Studio retains the full scene settings."
            )
        self._update_performance_runtime(mark_dirty=mark_dirty)
        if persist:
            self.settings.setValue("performance_mode", normalized)

    def _performance_limits_changed(self, *_args) -> None:
        if not self._applying_state:
            if hasattr(self, "adaptive_preview_check"):
                self.settings.setValue("adaptive_preview", self.adaptive_preview_check.isChecked())
            if hasattr(self, "refine_idle_check"):
                self.settings.setValue("refine_idle", self.refine_idle_check.isChecked())
            self._update_performance_runtime(mark_dirty=True)

    def _update_performance_runtime(self, *, mark_dirty: bool) -> None:
        if not hasattr(self, "live_fps_spin"):
            return
        fps = max(5, int(self.live_fps_spin.value()))
        self.playback_render_timer.setInterval(max(16, round(1000 / fps)))
        configured_budget = int(self.live_points_spin.value())
        self.adaptive_preview.reset(configured_budget, fps)
        self._last_preview_draw_ms = 0.0
        if hasattr(self, "viewport"):
            self.viewport.set_live_point_budget(configured_budget)
            self.viewport.set_motion_frame_interval(max(16, round(1000 / fps)))
        if mark_dirty:
            self._mark_preview_dirty()

    # -------------------------------------------------------------- Geometry
    def _apply_preset(self, name: str, *_args, rebuild: bool = True) -> None:
        preset = DEFAULT_PRESETS.get(name) or DEFAULT_PRESETS.get("Audio PCA") or next(iter(DEFAULT_PRESETS.values()))
        blockers = [
            QSignalBlocker(widget)
            for widget in (self.x_edit, self.y_edit, self.z_edit, self.color_edit, self.size_edit)
        ]
        self.x_edit.setText(str(preset.get("x", "pc1")))
        self.y_edit.setText(str(preset.get("y", "pc2")))
        self.z_edit.setText(str(preset.get("z", "pc3")))
        self.color_edit.setText(str(preset.get("color", "time")))
        self.size_edit.setText(str(preset.get("size", "rms")))
        del blockers
        if rebuild:
            self._queue_geometry_rebuild()

    def _queue_geometry_rebuild(self, *_args) -> None:
        if self._applying_state or self.analysis is None:
            return
        self.geometry_timer.start()

    @Slot()
    def rebuild_geometry(self) -> None:
        if self.analysis is None or self._building_geometry:
            return
        self.geometry_timer.stop()
        self._building_geometry = True
        self.rebuild_button.setEnabled(False)
        self._set_status("Building geometry…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.geometry = build_geometry(
                self.analysis,
                self.x_edit.text().strip() or "pc1",
                self.y_edit.text().strip() or "pc2",
                self.z_edit.text().strip() or "pc3",
                self.color_edit.text().strip() or "time",
                self.size_edit.text().strip() or "rms",
                normalize_mode=str(self.normalize_combo.currentData() or "zscore"),
                max_points=int(self.max_points_spin.value()),
                low_volume_cutoff_db=float(self.cutoff_spin.value()),
                colormap=self.colormap_combo.currentText(),
            )
            self._update_data_summary()
            self.analysis_dock.update_geometry(self.analysis, self.geometry)
            self._set_ready_state(True)
            self._mark_preview_dirty()
            self._set_status(
                f"Geometry ready · {int(self.geometry.x_plot.size):,} preview points from "
                f"{int(self.geometry.x_full.size):,} analyzed frames."
            )
        except Exception as exc:  # noqa: BLE001
            self.geometry = None
            self._set_ready_state(False)
            self._show_error("Could not build geometry", exc)
        finally:
            QApplication.restoreOverrideCursor()
            self._building_geometry = False
            self.rebuild_button.setEnabled(self.analysis is not None)

    # --------------------------------------------------------------- Preview
    def _visual_widgets(self) -> tuple[QWidget, ...]:
        return (
            self.render_mode_combo,
            self.history_combo,
            self.lifespan_spin,
            self.fade_curve_spin,
            self.base_alpha_spin,
            self.line_width_spin,
            self.point_scale_spin,
            self.curve_combo,
            self.curve_detail_spin,
            self.connect_lines_check,
            self.ghost_path_check,
            self.head_marker_check,
            self.axes_check,
            self.grid_check,
            self.axis_labels_check,
            self.colorbar_check,
            self.comet_duration_spin,
            self.flash_duration_spin,
            self.head_size_spin,
            self.halo_size_spin,
            self.flash_size_spin,
            self.elev_spin,
            self.azim_spin,
            self.zoom_spin,
            self.autorotate_check,
            self.rotation_speed_spin,
            self.tube_radius_spin,
            self.tube_sides_spin,
            self.tube_follow_check,
            self.tube_taper_spin,
            self.point_label_mode_combo,
            self.point_label_content_combo,
            self.max_labels_spin,
        )

    def _visual_setting_changed(self, *_args) -> None:
        if not self._applying_state:
            if hasattr(self, "visual_preset_combo") and self.visual_preset_combo.currentText() != "Custom":
                with QSignalBlocker(self.visual_preset_combo):
                    self.visual_preset_combo.setCurrentText("Custom")
                self.settings.setValue("visual_preset", "Custom")
            self._mark_preview_dirty()

    @Slot(bool)
    def _analysis_dock_collapsed_changed(self, collapsed: bool) -> None:
        """Keep a collapsed analysis dock from reserving an invisible panel."""

        if not hasattr(self, "workspace_splitter"):
            return

        def apply_sizes() -> None:
            total = max(1, self.workspace_splitter.height())
            if collapsed:
                dock_height = 42
            else:
                dock_height = min(max(190, self.analysis_dock.preferred_expanded_height), max(190, total // 2))
            self.workspace_splitter.setSizes([max(1, total - dock_height), dock_height])

        # Splitters may not have final geometry while the window is being
        # assembled. Apply after the current event turn as well as immediately.
        apply_sizes()
        QTimer.singleShot(0, apply_sizes)

    def _mark_preview_dirty(self) -> None:
        self.preview_session_dirty = True
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        if (
            self.analysis is not None
            and self.geometry is not None
            and self.analysis_thread is None
            and not self._playing
        ):
            self.preview_timer.start()

    def _close_preview_session(self) -> None:
        # v1.12.5 no longer uses the main-window composition raster. Keep the
        # compatibility attribute empty and release the true 3D scene.
        self.preview_session = None
        self.preview_session_dirty = True
        if hasattr(self, "viewport"):
            self.viewport.clear_scene()
        if hasattr(self, "analysis_dock"):
            self.analysis_dock.set_data(None)

    @Slot()
    def _reset_viewport_camera(self) -> None:
        if not hasattr(self, "viewport"):
            return
        self.viewport.reset_camera()

    @Slot()
    def _viewport_interaction_started(self) -> None:
        # The viewport pauses its timer while a drag is in progress, then
        # resumes automatically.  Keep the user's autorotate selection intact
        # instead of silently turning the control off on first interaction.
        return

    @Slot(float, float, float)
    def _viewport_camera_changed(self, elev: float, azim: float, zoom: float) -> None:
        blockers = [QSignalBlocker(widget) for widget in (self.elev_spin, self.azim_spin, self.zoom_spin)]
        self.elev_spin.setValue(float(np.clip(elev, self.elev_spin.minimum(), self.elev_spin.maximum())))
        normalized_azim = ((float(azim) + 360.0) % 720.0) - 360.0
        self.azim_spin.setValue(float(np.clip(normalized_azim, self.azim_spin.minimum(), self.azim_spin.maximum())))
        self.zoom_spin.setValue(float(np.clip(zoom, self.zoom_spin.minimum(), self.zoom_spin.maximum())))
        del blockers
        self.preview_session_dirty = False

    def _live_proxy_active(self) -> bool:
        live_capture = bool(self.live_input_panel is not None and self.live_input_panel.engine.active)
        return bool(
            self._playing
            or live_capture
            or (hasattr(self, "autorotate_check") and self.autorotate_check.isChecked())
            or not hasattr(self, "refine_idle_check")
            or not self.refine_idle_check.isChecked()
        )

    def _effective_live_point_budget(self) -> int:
        if self._live_proxy_active() or self.geometry is None:
            configured = int(self.live_points_spin.value())
            if hasattr(self, "adaptive_preview_check") and self.adaptive_preview_check.isChecked():
                return min(configured, int(self.adaptive_preview.effective_budget))
            return configured
        return min(int(self.max_points_spin.value()), int(self.geometry.x_full.size))

    @Slot(float)
    def _preview_frame_rendered(self, draw_ms: float) -> None:
        self._last_preview_draw_ms = max(0.0, float(draw_ms))
        if not self._playing or not hasattr(self, "adaptive_preview_check"):
            return
        budget = self.adaptive_preview.observe(
            self._last_preview_draw_ms,
            enabled=self.adaptive_preview_check.isChecked(),
        )
        self.viewport.set_live_point_budget(budget)

    def _make_render_options(
        self,
        *,
        width: int = 960,
        height: int = 540,
        live_preview: bool = False,
    ) -> ExportOptions:
        layout = self.current_layout.clone().clamp()
        options = ExportOptions(
            output_path="",
            width=int(width),
            height=int(height),
            fps=30,
            layout=layout,
            include_preview=bool(layout.preview.enabled),
            include_panels=bool(
                layout.spectrogram.enabled or layout.chromagram.enabled or layout.mfcc.enabled or layout.traces.enabled
            ),
            base_alpha=float(self.base_alpha_spin.value()),
            history_mode=self.history_combo.currentText(),
            point_lifespan=float(self.lifespan_spin.value()),
            fade_curve=float(self.fade_curve_spin.value()),
            line_width=float(self.line_width_spin.value()),
            path_curve_mode=self.curve_combo.currentText(),
            curve_detail=int(self.curve_detail_spin.value()),
            connect_lines=self.connect_lines_check.isChecked(),
            ghost_path=self.ghost_path_check.isChecked(),
            elev=float(self.elev_spin.value()),
            azim=float(self.azim_spin.value()),
            autorotate=self.autorotate_check.isChecked(),
            rotation_speed=float(self.rotation_speed_spin.value()),
            zoom=float(self.zoom_spin.value()),
            point_size_scale=float(self.point_scale_spin.value()),
            render_mode=self.render_mode_combo.currentText(),
            tube_radius_scale=float(self.tube_radius_spin.value()),
            tube_sides=int(self.tube_sides_spin.value()),
            tube_follow_size=self.tube_follow_check.isChecked(),
            tube_taper=float(self.tube_taper_spin.value()),
            show_head_marker=self.head_marker_check.isChecked(),
            comet_duration=float(self.comet_duration_spin.value()),
            flash_duration=float(self.flash_duration_spin.value()),
            head_size_scale=float(self.head_size_spin.value()),
            halo_size_scale=float(self.halo_size_spin.value()),
            flash_size_scale=float(self.flash_size_spin.value()),
            show_axes=self.axes_check.isChecked(),
            show_grid=self.grid_check.isChecked(),
            show_axis_labels=self.axis_labels_check.isChecked(),
            point_label_mode=self.point_label_mode_combo.currentText(),
            point_label_content=self.point_label_content_combo.currentText(),
            max_point_labels=int(self.max_labels_spin.value()),
            show_colorbar=self.colorbar_check.isChecked(),
            show_project_title=True,
            project_title=self.source_path.stem if self.source_path else APP_NAME,
            project_subtitle="Metriq Visualizer",
            show_watermark=False,
            watermark_text="",
            title=APP_NAME,
            start_time=0.0,
            end_time=float(getattr(self.analysis, "duration", 0.0)) if self.analysis is not None else None,
        )
        if not live_preview or not self._live_proxy_active():
            return options
        profile = profile_for(self.performance_combo.currentText())
        live_options, _points, _fps = apply_live_limits(
            options,
            profile,
            point_budget=int(self.live_points_spin.value()),
            target_fps=int(self.live_fps_spin.value()),
        )
        return live_options

    @Slot()
    def _render_preview(self) -> None:
        if self.analysis is None or self.geometry is None:
            return
        try:
            profile = profile_for(self.performance_combo.currentText())
            live_proxy = self._live_proxy_active()
            ratio_cap = profile.pixel_ratio_cap if live_proxy else None
            self.viewport.set_render_pixel_ratio_cap(ratio_cap)
            self.viewport.set_motion_mode(live_proxy)
            self.viewport.set_motion_frame_interval(max(16, round(1000 / max(1, int(self.live_fps_spin.value())))))
            effective_budget = self._effective_live_point_budget()
            self.viewport.set_live_point_budget(effective_budget)
            options = self._make_render_options(
                width=max(640, self.viewport.width()),
                height=max(360, self.viewport.height()),
                live_preview=True,
            )
            scene_missing = self.viewport.scene is None
            geometry_changed = (
                self.viewport.geometry is not self.geometry or self.viewport.analysis is not self.analysis
            )
            needs_exact_draw = scene_missing or geometry_changed or self.preview_session_dirty
            if scene_missing or geometry_changed:
                self.viewport.set_scene(self.analysis, self.geometry, options)
                self.analysis_dock.set_data(self.analysis, self.geometry)
            elif self.preview_session_dirty:
                # During playback only time changes. Reapplying axes, labels,
                # camera limits, and colorbar options every frame is costly on
                # high-DPI canvases and can nearly double Matplotlib work.
                self.viewport.update_options(options, draw=False)
            self.preview_session = self.viewport.scene
            self.preview_session_dirty = False
            # A paused exact scene is static until the user changes an option,
            # camera, or timeline position. Forcing Matplotlib to redraw it
            # from every queued idle callback caused a runaway CPU loop on
            # high-DPI displays. Motion keeps using the realtime proxy; exact
            # rendering is now requested only for a new or changed scene.
            rendered = (
                self.viewport.update_time(self.current_time, draw=True)
                if live_proxy or needs_exact_draw
                else False
            )
            # The scientific panels are Matplotlib canvases.  Updating their
            # cursor on every 3D playback frame can starve the realtime
            # renderer, especially with a visible spectrogram.  Keep the
            # cursor state current but request its expensive canvas paint at a
            # human-smooth 8 Hz while the live proxy is active.
            draw_analysis_cursor = (
                not live_proxy or self._analysis_cursor_clock.elapsed() >= 125
            )
            self.analysis_dock.set_time(self.current_time, draw=draw_analysis_cursor)
            if draw_analysis_cursor:
                self._analysis_cursor_clock.restart()
            profile_name = self.performance_combo.currentText() if live_proxy else "Refined idle"
            draw_state = "DRAW" if rendered else "COALESCED"
            pixel_ratio = self.viewport.render_pixel_ratio()
            configured_budget = int(self.live_points_spin.value())
            density_text = (
                f"{effective_budget:,}/{configured_budget:,} PTS"
                if live_proxy and effective_budget != configured_budget
                else f"{effective_budget:,} PTS"
            )
            latency_text = f"{self._last_preview_draw_ms:0.0f} MS" if self._last_preview_draw_ms > 0.0 else "WARMUP"
            self.preview_status.setText(
                f"3D SCENE / {profile_name.upper()} / {density_text} / "
                f"{int(self.live_fps_spin.value())} FPS CEILING / {pixel_ratio:0.2f}× DPI / "
                f"{latency_text} / {draw_state} / {self.current_time:0.2f}s"
            )
        except Exception as exc:  # noqa: BLE001
            self.preview_status.setText("3D VIEW ERROR")
            if self.viewport.scene is None:
                self.viewport.clear_scene(f"3D VIEW UNAVAILABLE\n\n{exc}")
            self._set_status(str(exc))

    # -------------------------------------------------------------- Playback
    def _timeline_changed(self, value: int) -> None:
        if self.analysis is None:
            return
        duration = float(max(0.0, self.analysis.duration))
        self.current_time = duration * value / max(1, self.time_slider.maximum())
        if self._media_source_path is not None and not self._media_failed:
            position_ms = round(self.current_time * 1000.0)
            self.media_player.setPosition(position_ms)
            self._media_last_position_ms = position_ms
            if self._playing:
                self._media_stall_clock.restart()
                self.play_clock.restart()
        self._update_time_label()
        if hasattr(self, "analysis_dock"):
            self.analysis_dock.set_time(self.current_time)
        self._schedule_preview()

    def _seek_seconds(self, seconds: float, *, sync_media: bool = True, schedule_preview: bool = True) -> None:
        if self.analysis is None:
            self.pending_seek = seconds
            return
        duration = float(max(0.0, self.analysis.duration))
        self.current_time = min(duration, max(0.0, seconds))
        value = round(self.current_time / duration * self.time_slider.maximum()) if duration > 0 else 0
        with QSignalBlocker(self.time_slider):
            self.time_slider.setValue(value)
        if sync_media and self._media_source_path is not None and not self._media_failed:
            position_ms = round(self.current_time * 1000.0)
            self.media_player.setPosition(position_ms)
            self._media_last_position_ms = position_ms
            self._media_stall_clock.restart()
            self.play_clock.restart()
        self._update_time_label()
        if hasattr(self, "analysis_dock"):
            self.analysis_dock.set_time(self.current_time)
        if schedule_preview:
            self._schedule_preview()

    def toggle_playback(self) -> None:
        if self.analysis is None or self.geometry is None:
            return
        duration = float(max(0.0, self.analysis.duration))
        if duration <= 0.0:
            self._render_preview()
            return
        if self._playing:
            self.stop_playback()
            return

        if self.current_time >= duration:
            self._seek_seconds(0.0)
        self.preview_timer.stop()
        self.adaptive_preview.reset(int(self.live_points_spin.value()), int(self.live_fps_spin.value()))
        self._last_preview_draw_ms = 0.0
        self._playing = True
        self.preview_session_dirty = True
        self._using_media_clock = bool(self._media_source_path is not None and not self._media_failed)
        self.play_clock.restart()
        if self._using_media_clock:
            self._loop_toggled(self.loop_check.isChecked())
            position_ms = round(self.current_time * 1000.0)
            self.media_player.setPosition(position_ms)
            self._media_last_position_ms = position_ms
            self._media_stall_clock.restart()
            self.media_player.play()
        self.play_timer.start()
        self.playback_render_timer.start()
        self.play_button.setText("Pause")
        self._render_playback_frame()

    def stop_playback(self) -> None:
        was_playing = self._playing
        self._playing = False
        self.preview_session_dirty = True
        self.play_timer.stop()
        self.playback_render_timer.stop()
        if (
            hasattr(self, "media_player")
            and self.media_player.playbackState() != QMediaPlayer.PlaybackState.StoppedState
        ):
            self.media_player.pause()
        self._using_media_clock = False
        if hasattr(self, "play_button"):
            self.play_button.setText("Play")
        if was_playing and self.analysis is not None and self.geometry is not None:
            self.preview_timer.start(0)

    def _play_tick(self) -> None:
        if self.analysis is None:
            self.stop_playback()
            return
        duration = float(max(0.0, self.analysis.duration))
        target: float
        if self._using_media_clock:
            position_ms = max(0, int(self.media_player.position()))
            if abs(position_ms - self._media_last_position_ms) >= 2:
                self._media_last_position_ms = position_ms
                self._media_stall_clock.restart()
            elif self._media_stall_clock.isValid() and self._media_stall_clock.elapsed() >= 1500:
                # Some host multimedia stacks neither advance nor emit an
                # immediate error. Keep the visualization usable and allow a
                # late-starting player to rejoin when its clock moves.
                self._using_media_clock = False
                self.play_clock.restart()
                self._set_status("Media is still starting; visual playback is using its local clock.")
            target = position_ms / 1000.0 if self._using_media_clock else self.current_time
        else:
            position_ms = max(0, int(self.media_player.position())) if self._media_source_path is not None else 0
            media_advancing = (
                not self._media_failed
                and self._media_source_path is not None
                and self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and position_ms > self._media_last_position_ms + 8
            )
            media_seconds = position_ms / 1000.0
            media_near_visual = abs(media_seconds - self.current_time) <= 0.45
            if media_advancing and media_near_visual:
                self._using_media_clock = True
                self._media_last_position_ms = position_ms
                self._media_stall_clock.restart()
                target = media_seconds
                self._set_status("Media playback synchronized.")
            else:
                # A backend that starts late can be seconds behind the local
                # visual clock. Seek it to the current visual position instead
                # of making the visualization jump backward when audio wakes.
                if media_advancing and not media_near_visual:
                    desired_ms = round(self.current_time * 1000.0)
                    self.media_player.setPosition(desired_ms)
                    self._media_last_position_ms = desired_ms
                    self._media_stall_clock.restart()
                else:
                    self._media_last_position_ms = max(self._media_last_position_ms, position_ms)
                elapsed = self.play_clock.restart() / 1000.0
                target = self.current_time + max(0.0, elapsed)
        if target >= duration:
            if self.loop_check.isChecked() and duration > 0:
                target %= duration
                if self._media_source_path is not None and not self._media_failed:
                    position_ms = round(target * 1000.0)
                    self.media_player.setPosition(position_ms)
                    self._media_last_position_ms = position_ms
                    self._media_stall_clock.restart()
                    self.media_player.play()
            else:
                target = duration
                self._seek_seconds(target, sync_media=False, schedule_preview=False)
                self.stop_playback()
                return
        self._seek_seconds(target, sync_media=False, schedule_preview=False)

    @Slot()
    def _render_playback_frame(self) -> None:
        if not (self._playing and self.analysis is not None and self.geometry is not None):
            return
        if self.adaptive_preview_check.isChecked() and self.viewport.draw_pending():
            return
        self._render_preview()

    def _configure_media_source(self) -> None:
        self._clear_media_source()
        if self.analysis is None:
            return
        audio_source = Path(self.analysis.audio_path).expanduser().resolve() if self.analysis.audio_path else None
        has_audio = bool(audio_source is not None and audio_source.is_file())
        if has_audio:
            source = audio_source
        elif bool(self.analysis.has_video):
            source = Path(self.analysis.source_path).expanduser().resolve()
        else:
            source = None
        if source is None or not source.is_file():
            self._set_audio_controls_enabled(False)
            return
        self._media_source_path = source
        self._media_has_audio = has_audio
        self._media_failed = False
        self._media_last_position_ms = 0
        self.media_player.setSource(QUrl.fromLocalFile(str(source)))
        self._loop_toggled(self.loop_check.isChecked())
        self._set_audio_controls_enabled(has_audio)

    def _clear_media_source(self) -> None:
        if not hasattr(self, "media_player"):
            return
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self._media_source_path = None
        self._media_has_audio = False
        self._media_failed = False
        self._using_media_clock = False
        self._media_last_position_ms = 0
        self._media_stall_clock.invalidate()
        self._set_audio_controls_enabled(False)

    def _set_audio_controls_enabled(self, enabled: bool) -> None:
        for name in ("volume_label", "volume_slider", "mute_check"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(bool(enabled))

    @Slot(bool)
    def _loop_toggled(self, enabled: bool) -> None:
        if hasattr(self, "media_player"):
            self.media_player.setLoops(QMediaPlayer.Loops.Infinite if enabled else QMediaPlayer.Loops.Once)

    @Slot(int)
    def _volume_changed(self, value: int) -> None:
        volume = min(100, max(0, int(value)))
        self.audio_output.setVolume(volume / 100.0)
        self.settings.setValue("playback_volume", volume)

    @Slot(bool)
    def _mute_toggled(self, muted: bool) -> None:
        self.audio_output.setMuted(bool(muted))

    @Slot(QMediaPlayer.Error, str)
    def _media_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self._media_failed = True
        self._media_stall_clock.invalidate()
        if self._playing and self._using_media_clock:
            self._using_media_clock = False
            self.play_clock.restart()
        details = str(message or self.media_player.errorString() or "unknown playback error")
        self._set_status(f"Media playback unavailable ({details}); visual playback continues.")

    @Slot(QMediaPlayer.MediaStatus)
    def _media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._playing:
            if self.loop_check.isChecked() and self.analysis is not None and self.analysis.duration > 0:
                self.media_player.setPosition(0)
                self.media_player.play()
                self._seek_seconds(0.0, sync_media=False, schedule_preview=False)
            else:
                self._seek_seconds(
                    float(getattr(self.analysis, "duration", 0.0)),
                    sync_media=False,
                    schedule_preview=False,
                )
                self.stop_playback()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._media_error(self.media_player.error(), self.media_player.errorString())

    def _update_time_label(self) -> None:
        duration = float(getattr(self.analysis, "duration", 0.0)) if self.analysis is not None else 0.0
        self.time_label.setText(f"{self._format_time(self.current_time)} / {self._format_time(duration)}")

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        return f"{minutes:02d}:{remainder:06.3f}"

    # ----------------------------------------------------------- Export/live
    def open_export_studio(self) -> None:
        if self.analysis is None or self.geometry is None:
            return
        self.stop_playback()
        try:
            dialog = ExportStudioDialog(
                self.analysis,
                self.geometry,
                self._make_render_options(width=1920, height=1080),
                self,
            )
            dialog.exec()
            self.current_layout = dialog.layout_spec.clone().clamp()
            self._mark_preview_dirty()
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not open Export Studio", exc)

    def open_live_input(self) -> None:
        try:
            if self.live_input_dock is None or self.live_input_panel is None:
                panel = LiveInputPanel(self)
                dock = QDockWidget("Live Input", self)
                dock.setObjectName("MetriqLiveInputDock")
                dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
                dock.setFeatures(
                    QDockWidget.DockWidgetFeature.DockWidgetClosable
                    | QDockWidget.DockWidgetFeature.DockWidgetMovable
                    | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                )
                dock.setWidget(panel)
                panel.trajectoryUpdated.connect(self._live_trajectory_updated)
                panel.activeChanged.connect(self._live_active_changed)
                panel.captureReady.connect(self._live_capture_ready)
                panel.errorOccurred.connect(self._live_input_error)
                dock.visibilityChanged.connect(self._live_dock_visibility_changed)
                self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
                self.live_input_panel = panel
                self.live_input_dock = dock
            self.stop_playback()
            self.live_input_dock.show()
            self.live_input_dock.raise_()
            if not self.live_input_panel.engine.active:
                QTimer.singleShot(0, self.live_input_panel.start_input)
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not open live input", exc)

    @Slot(object, object, object)
    def _live_trajectory_updated(self, points: object, colors: object, sizes: object) -> None:
        if self.live_input_panel is None:
            return
        point_array = np.asarray(points, dtype=np.float64)
        color_array = np.asarray(colors, dtype=np.float64)
        size_array = np.asarray(sizes, dtype=np.float64)
        options = self._make_render_options(
            width=max(640, self.viewport.width()),
            height=max(360, self.viewport.height()),
            live_preview=True,
        )
        self.viewport.set_live_point_budget(max(100, int(point_array.shape[0])))
        self.viewport.set_live_trajectory(point_array, color_array, size_array, options=options)
        self.preview_status.setText(
            f"LIVE 3D / {self.live_input_panel.mapping_combo.currentText().upper():<24.24} / "
            f"{point_array.shape[0]:>5,} PTS / MICROPHONE"
        )

    @Slot(bool)
    def _live_active_changed(self, active: bool) -> None:
        self.viewport.set_live_mode(bool(active))
        self.viewport.set_motion_mode(bool(active) or self._live_proxy_active())
        if active:
            self.source_badge.setText("LIVE / LOCAL MICROPHONE")
            self.preview_status.setText("LIVE 3D / STARTING / MICROPHONE")
            self._set_status("Microphone input active · incoming features are mapped into the main 3D viewport.")
        else:
            if self.analysis is not None and self.source_path is not None:
                self.source_badge.setText(f"LOCAL / {self.source_path.name.upper()}")
                self._mark_preview_dirty()
            else:
                self.source_badge.setText("NO SOURCE")
                self.preview_status.setText("IDLE")

    @Slot(str)
    def _live_capture_ready(self, path: str) -> None:
        self._start_analysis(Path(path))

    @Slot(str)
    def _live_input_error(self, message: str) -> None:
        self._set_status(f"Microphone input failed: {message}")
        QMessageBox.warning(
            self,
            "Could not start microphone",
            f"{message}\n\nOn macOS, confirm microphone permission for Python or the Metriq Visualizer app "
            "in System Settings → Privacy & Security → Microphone, then select Start input again.",
        )

    @Slot(bool)
    def _live_dock_visibility_changed(self, visible: bool) -> None:
        if not visible and self.live_input_panel is not None and self.live_input_panel.engine.active:
            self.live_input_panel.stop_input()

    def export_data_dialog(self) -> None:
        if self.analysis is None:
            return
        source_stem = self.source_path.stem if self.source_path else "metriq_analysis"
        default = (self.source_path.parent if self.source_path else Path.home()) / f"{source_stem}_analysis.csv"
        path_text, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export analyzed and mapped data",
            str(default),
            "CSV table (*.csv);;Compressed NumPy archive (*.npz)",
        )
        if not path_text:
            return
        try:
            path = Path(path_text)
            if path.suffix.lower() == ".npz" or "NumPy" in selected_filter:
                output = export_analysis_npz(path, self.analysis, self.geometry)
            else:
                output = export_analysis_csv(path, self.analysis, self.geometry)
            self._set_status(f"Exported analysis data to {output.name}.")
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not export analysis data", exc)

    def clear_analysis_cache(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear analysis cache",
            "Delete locally cached derived analysis arrays? Source media, projects, presets, and exports are not affected.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = clear_cache()
        self._set_status(f"Cleared {removed} cached analysis file{'s' if removed != 1 else ''}.")

    def _load_recent_paths(self) -> list[str]:
        try:
            payload = json.loads(str(self.settings.value("recent_sources", "[]")))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = []
        if not isinstance(payload, list):
            return []
        result: list[str] = []
        for value in payload:
            path = Path(str(value)).expanduser()
            if path.exists() and str(path.resolve()) not in result:
                result.append(str(path.resolve()))
        return result[:8]

    def _remember_recent(self, path: Path) -> None:
        resolved = str(Path(path).expanduser().resolve())
        self.recent_paths = [resolved] + [value for value in self.recent_paths if value != resolved]
        self.recent_paths = self.recent_paths[:8]
        self.settings.setValue("recent_sources", json.dumps(self.recent_paths))
        if hasattr(self, "recent_combo"):
            with QSignalBlocker(self.recent_combo):
                self.recent_combo.clear()
                self.recent_combo.addItem("Recent sources…", "")
                for recent in self.recent_paths:
                    self.recent_combo.addItem(Path(recent).name, recent)

    def _recent_selected(self, index: int) -> None:
        path_text = str(self.recent_combo.itemData(index) or "")
        with QSignalBlocker(self.recent_combo):
            self.recent_combo.setCurrentIndex(0)
        if path_text:
            self._route_path(Path(path_text))

    def _recoverable_session(self) -> dict[str, Any] | None:
        raw = str(self.settings.value("recoverable_session", "")).strip()
        if not raw:
            return None
        try:
            state = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(state, dict):
            return None
        session = state.get("session")
        if not isinstance(session, dict):
            return None
        source_text = str(session.get("file_path", "")).strip()
        return state if source_text and Path(source_text).expanduser().exists() else None

    def restore_session(self) -> None:
        state = self._recoverable_session()
        if state is None:
            QMessageBox.information(self, "No session to restore", "No recoverable local session is available.")
            self.restore_button.setEnabled(False)
            return
        source = Path(str(state["session"]["file_path"])).expanduser()
        self.project_path = None
        self._start_analysis(source, state=state)

    def _save_recoverable_session(self) -> None:
        if self.source_path is None:
            return
        with suppress(TypeError, ValueError):
            self.settings.setValue("recoverable_session", json.dumps(self._capture_state(include_session=True)))

    # -------------------------------------------------------- Projects/presets
    def _capture_state(self, *, include_session: bool) -> dict[str, Any]:
        state: dict[str, Any] = {
            "app_version": APP_VERSION,
            "mapping": {
                "preset": self.preset_combo.currentText(),
                "x": self.x_edit.text(),
                "y": self.y_edit.text(),
                "z": self.z_edit.text(),
                "color": self.color_edit.text(),
                "size": self.size_edit.text(),
            },
            "extraction": {
                "profile": self.analysis_profile_combo.currentText(),
                **self._current_analysis_settings().to_dict(),
            },
            "geometry": {
                "normalize_mode": self.normalize_combo.currentData(),
                "max_points": self.max_points_spin.value(),
                "low_volume_cutoff_db": self.cutoff_spin.value(),
                "colormap": self.colormap_combo.currentText(),
            },
            "visual": {
                "render_mode": self.render_mode_combo.currentText(),
                "history_mode": self.history_combo.currentText(),
                "point_lifespan": self.lifespan_spin.value(),
                "fade_curve": self.fade_curve_spin.value(),
                "base_alpha": self.base_alpha_spin.value(),
                "line_width": self.line_width_spin.value(),
                "point_size_scale": self.point_scale_spin.value(),
                "path_curve_mode": self.curve_combo.currentText(),
                "curve_detail": self.curve_detail_spin.value(),
                "connect_lines": self.connect_lines_check.isChecked(),
                "ghost_path": self.ghost_path_check.isChecked(),
                "show_head_marker": self.head_marker_check.isChecked(),
                "show_axes": self.axes_check.isChecked(),
                "show_grid": self.grid_check.isChecked(),
                "show_axis_labels": self.axis_labels_check.isChecked(),
                "show_colorbar": self.colorbar_check.isChecked(),
                "comet_duration": self.comet_duration_spin.value(),
                "flash_duration": self.flash_duration_spin.value(),
                "head_size_scale": self.head_size_spin.value(),
                "halo_size_scale": self.halo_size_spin.value(),
                "flash_size_scale": self.flash_size_spin.value(),
                "elev": self.elev_spin.value(),
                "azim": self.azim_spin.value(),
                "zoom": self.zoom_spin.value(),
                "autorotate": self.autorotate_check.isChecked(),
                "rotation_speed": self.rotation_speed_spin.value(),
                "tube_radius_scale": self.tube_radius_spin.value(),
                "tube_sides": self.tube_sides_spin.value(),
                "tube_follow_size": self.tube_follow_check.isChecked(),
                "tube_taper": self.tube_taper_spin.value(),
                "point_label_mode": self.point_label_mode_combo.currentText(),
                "point_label_content": self.point_label_content_combo.currentText(),
                "max_point_labels": self.max_labels_spin.value(),
            },
            "performance": {
                "mode": self.performance_combo.currentText(),
                "live_point_budget": self.live_points_spin.value(),
                "live_redraw_fps": self.live_fps_spin.value(),
                "adaptive": self.adaptive_preview_check.isChecked(),
                "refine_idle": self.refine_idle_check.isChecked(),
            },
            "layout": self.current_layout.to_dict(),
        }
        if include_session:
            state["session"] = {
                "file_path": str(self.source_path) if self.source_path else "",
                "current_time": self.current_time,
                "theme": self.theme_name,
            }
        return state

    def _apply_state(self, state: Mapping[str, Any], *, rebuild: bool = True) -> None:
        self._applying_state = True
        try:
            mapping = state.get("mapping") if isinstance(state.get("mapping"), Mapping) else {}
            preset_name = str(mapping.get("preset", self.preset_combo.currentText()))
            self._set_combo_text(self.preset_combo, preset_name)
            for key, widget in (
                ("x", self.x_edit),
                ("y", self.y_edit),
                ("z", self.z_edit),
                ("color", self.color_edit),
                ("size", self.size_edit),
            ):
                if key in mapping:
                    widget.setText(str(mapping[key]))

            extraction = state.get("extraction") if isinstance(state.get("extraction"), Mapping) else {}
            if extraction:
                self._apply_extraction_settings(
                    AnalysisSettings.from_mapping(extraction),
                    profile_name=str(extraction.get("profile", "")).strip() or None,
                )

            geometry = state.get("geometry") if isinstance(state.get("geometry"), Mapping) else {}
            normalize = str(geometry.get("normalize_mode", "zscore"))
            index = self.normalize_combo.findData(normalize)
            if index >= 0:
                self.normalize_combo.setCurrentIndex(index)
            self.max_points_spin.setValue(_safe_int(geometry.get("max_points"), self.max_points_spin.value()))
            self.cutoff_spin.setValue(_safe_float(geometry.get("low_volume_cutoff_db"), self.cutoff_spin.value()))
            self._set_combo_text(self.colormap_combo, str(geometry.get("colormap", self.colormap_combo.currentText())))

            visual = state.get("visual") if isinstance(state.get("visual"), Mapping) else {}
            combo_values = (
                ("render_mode", self.render_mode_combo),
                ("history_mode", self.history_combo),
                ("path_curve_mode", self.curve_combo),
                ("point_label_mode", self.point_label_mode_combo),
                ("point_label_content", self.point_label_content_combo),
            )
            for key, widget in combo_values:
                if key in visual:
                    self._set_combo_text(widget, str(visual[key]))
            numeric_values = (
                ("point_lifespan", self.lifespan_spin),
                ("fade_curve", self.fade_curve_spin),
                ("base_alpha", self.base_alpha_spin),
                ("line_width", self.line_width_spin),
                ("point_size_scale", self.point_scale_spin),
                ("curve_detail", self.curve_detail_spin),
                ("elev", self.elev_spin),
                ("azim", self.azim_spin),
                ("zoom", self.zoom_spin),
                ("rotation_speed", self.rotation_speed_spin),
                ("tube_radius_scale", self.tube_radius_spin),
                ("tube_sides", self.tube_sides_spin),
                ("tube_taper", self.tube_taper_spin),
                ("max_point_labels", self.max_labels_spin),
                ("comet_duration", self.comet_duration_spin),
                ("flash_duration", self.flash_duration_spin),
                ("head_size_scale", self.head_size_spin),
                ("halo_size_scale", self.halo_size_spin),
                ("flash_size_scale", self.flash_size_spin),
            )
            for key, widget in numeric_values:
                if key in visual:
                    value = visual[key]
                    widget.setValue(int(round(float(value))) if isinstance(widget, QSpinBox) else float(value))
            bool_values = (
                ("connect_lines", self.connect_lines_check),
                ("ghost_path", self.ghost_path_check),
                ("show_head_marker", self.head_marker_check),
                ("show_axes", self.axes_check),
                ("show_grid", self.grid_check),
                ("show_axis_labels", self.axis_labels_check),
                ("show_colorbar", self.colorbar_check),
                ("autorotate", self.autorotate_check),
                ("tube_follow_size", self.tube_follow_check),
            )
            for key, widget in bool_values:
                if key in visual:
                    widget.setChecked(bool(visual[key]))

            performance = state.get("performance") if isinstance(state.get("performance"), Mapping) else {}
            if performance:
                mode = normalize_profile_name(performance.get("mode", self.performance_combo.currentText()))
                with QSignalBlocker(self.performance_combo):
                    self.performance_combo.setCurrentText(mode)
                profile = profile_for(mode)
                with QSignalBlocker(self.live_points_spin):
                    self.live_points_spin.setValue(
                        _safe_int(performance.get("live_point_budget"), profile.point_budget)
                    )
                with QSignalBlocker(self.live_fps_spin):
                    self.live_fps_spin.setValue(_safe_int(performance.get("live_redraw_fps"), profile.target_fps))
                with QSignalBlocker(self.adaptive_preview_check):
                    self.adaptive_preview_check.setChecked(bool(performance.get("adaptive", True)))
                with QSignalBlocker(self.refine_idle_check):
                    self.refine_idle_check.setChecked(bool(performance.get("refine_idle", False)))
                self.settings.setValue("adaptive_preview", self.adaptive_preview_check.isChecked())
                self.settings.setValue("refine_idle", self.refine_idle_check.isChecked())
                self.performance_mode = mode
                self.performance_description.setText(
                    f"{profile.description} Live-only limits; Export Studio retains the full scene settings."
                )
                self._update_performance_runtime(mark_dirty=False)

            layout_payload = state.get("layout")
            if isinstance(layout_payload, Mapping):
                self.current_layout = ExportLayoutSpec.from_dict(layout_payload).clamp()

            session = state.get("session") if isinstance(state.get("session"), Mapping) else {}
            if "current_time" in session:
                self.pending_seek = _safe_float(session.get("current_time"), 0.0)
            theme = str(session.get("theme", "")).strip().lower()
            if theme in {"dark", "light"}:
                self._set_theme(theme)
        finally:
            self._applying_state = False
        if rebuild and self.analysis is not None:
            self.rebuild_geometry()
        else:
            self._mark_preview_dirty()

    @staticmethod
    def _set_combo_text(combo: QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save_project_dialog(self) -> None:
        if self.source_path is None:
            return
        default = self.project_path or self.source_path.with_suffix(".mvproj")
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Save Metriq project",
            str(default),
            "Metriq project (*.mvproj)",
        )
        if not path_text:
            return
        try:
            payload = build_project_payload(
                self.source_path.stem,
                self._capture_state(include_session=True),
                project_path=path_text,
            )
            self.project_path = save_project(path_text, payload)
            self._set_status(f"Saved project {self.project_path.name}.")
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not save project", exc)

    def load_project_dialog(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "Load Metriq project",
            str(self.project_path.parent if self.project_path else Path.home()),
            "Metriq project (*.mvproj);;Legacy project (*.bgl);;All files (*)",
        )
        if path_text:
            self._load_project_path(Path(path_text))

    def _load_project_path(self, path: Path) -> None:
        try:
            payload = load_project(path)
            state = payload.get("state") or {}
            if not isinstance(state, Mapping):
                raise ValueError("Project state is missing or invalid.")
            self.project_path = path.resolve()
            session = state.get("session") if isinstance(state.get("session"), Mapping) else {}
            source_text = str(session.get("file_path", "")).strip()
            if source_text and Path(source_text).expanduser().exists():
                self._start_analysis(Path(source_text), state=state)
            else:
                self._apply_state(state, rebuild=self.analysis is not None)
                QMessageBox.warning(
                    self,
                    "Project source unavailable",
                    "The project settings were loaded, but its source file could not be found. Open the source "
                    "manually; the loaded settings will remain in place.",
                )
            self._set_status(f"Loaded project {path.name}.")
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not load project", exc)

    def save_preset_dialog(self) -> None:
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Save visual preset",
            str(Path.home() / "metriq_visualizer.mvpreset"),
            "Metriq preset (*.mvpreset)",
        )
        if not path_text:
            return
        try:
            payload = build_preset_payload(
                self.preset_combo.currentText() or "Custom visual preset",
                self._capture_state(include_session=False),
            )
            output = save_preset(path_text, payload)
            self._set_status(f"Saved preset {output.name}.")
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not save preset", exc)

    def load_preset_dialog(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "Load visual preset",
            str(Path.home()),
            "Metriq preset (*.mvpreset)",
        )
        if path_text:
            self._load_preset_path(Path(path_text))

    def _load_preset_path(self, path: Path) -> None:
        try:
            payload = load_preset(path)
            state = payload.get("state") or {}
            if not isinstance(state, Mapping):
                raise ValueError("Preset state is missing or invalid.")
            self._apply_state(state, rebuild=self.analysis is not None)
            display_name = preset_display_name(payload, path.stem)
            self.visual_preset_paths.setdefault(display_name, path.resolve())
            if self.visual_preset_combo.findText(display_name) < 0:
                self.visual_preset_combo.addItem(display_name)
            self._set_status(f"Loaded preset {path.name}.")
        except Exception as exc:  # noqa: BLE001
            self._show_error("Could not load preset", exc)

    # --------------------------------------------------------------- Theme/UI
    def toggle_theme(self) -> None:
        self._set_theme("light" if self.theme_name == "dark" else "dark")

    def _set_theme(self, name: str, *, persist: bool = True) -> None:
        theme = "light" if str(name).lower() == "light" else "dark"
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, theme)
        self.theme_name = theme
        if hasattr(self, "viewport"):
            self.viewport.set_theme(theme)
        self.theme_button.setText("Dark mode" if theme == "light" else "Light mode")
        if persist:
            self.settings.setValue("theme", theme)
        if hasattr(self, "boot_overlay"):
            self.boot_overlay.update()

    def _set_ready_state(self, ready: bool) -> None:
        self.rebuild_button.setEnabled(self.analysis is not None)
        self.save_project_button.setEnabled(self.analysis is not None and self.source_path is not None)
        self.export_button.setEnabled(ready)
        self.data_export_button.setEnabled(self.analysis is not None)
        if hasattr(self, "data_tab_export_button"):
            self.data_tab_export_button.setEnabled(self.analysis is not None)
        duration = float(getattr(self.analysis, "duration", 0.0)) if self.analysis is not None else 0.0
        self.play_button.setEnabled(bool(ready and duration > 0.0))
        self._set_audio_controls_enabled(self._media_source_path is not None and self._media_has_audio)
        self.save_preset_button.setEnabled(True)
        self._update_reanalyze_state()
        if ready and self.source_path is not None:
            self.source_badge.setText(f"LOCAL / {self.source_path.name.upper()}")
        elif self.analysis_thread is None:
            self.source_badge.setText("NO SOURCE")

    def _update_data_summary(self) -> None:
        if self.analysis is None:
            return
        self.summary_source.setText(str(self.source_path or self.analysis.source_path))
        self.summary_kind.setText(str(getattr(self.analysis, "source_kind", "media")).title())
        self.summary_duration.setText(f"{float(self.analysis.duration):.3f} seconds")
        self.summary_frames.setText(f"{int(self.analysis.times.size):,}")
        points = int(self.geometry.x_plot.size) if self.geometry is not None else 0
        full = int(self.geometry.x_full.size) if self.geometry is not None else int(self.analysis.times.size)
        self.summary_points.setText(f"{points:,} displayed / {full:,} total")
        self._update_time_label()

    def _set_status(self, message: str) -> None:
        self.status_bar.showMessage(message)

    def _show_error(self, title: str, exc: BaseException) -> None:
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        QMessageBox.critical(self, title, details[-7000:])
        self._set_status(str(exc))

    def _start_boot_overlay(self) -> None:
        self.boot_overlay.setGeometry(self.centralWidget().rect())
        self.boot_overlay.start()

    # ----------------------------------------------------------- Qt events
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self._route_path(Path(url.toLocalFile()))
                event.acceptProposedAction()
                return
        event.ignore()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "boot_overlay"):
            self.boot_overlay.setGeometry(self.centralWidget().rect())
        if self.analysis is not None and self.geometry is not None:
            self._schedule_preview()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self.analysis_thread is not None:
            QMessageBox.information(
                self,
                "Analysis in progress",
                "The local analysis task is still active. Close the application after it completes.",
            )
            event.ignore()
            return
        if self.live_input_panel is not None:
            self.live_input_panel.shutdown()
        self.stop_playback()
        self.preview_timer.stop()
        self.playback_render_timer.stop()
        self.geometry_timer.stop()
        self._save_recoverable_session()
        self._close_preview_session()
        self._clear_media_source()
        super().closeEvent(event)


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(APP_VERSION)
        return 0
    launch_path = next(
        (
            Path(argument).expanduser()
            for argument in sys.argv[1:]
            if not argument.startswith("-") and Path(argument).expanduser().exists()
        ),
        None,
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("Metriq")
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    if launch_path is not None:
        QTimer.singleShot(200, lambda path=launch_path: window._route_path(path))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
