# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from metriq_visualizer_core import (
    DEFAULT_PRESETS,
    AnalysisResult,
    GeometryResult,
    analysis_from_table_file,
    analyze_media,
    build_geometry,
    format_feature_reference,
    is_table_file,
)
from metriq_visualizer_layout import default_export_layout
from metriq_visualizer_render import ExportOptions, ExportPreviewSession, render_export_video


APP_NAME = "Metriq Visualizer"
APP_VERSION = "1.10.16"
APP_WINDOW_TITLE = f"{APP_NAME} v{APP_VERSION}"


def _image_from_rgba(frame: np.ndarray) -> QImage:
    array = np.ascontiguousarray(frame, dtype=np.uint8)
    if array.ndim != 3 or array.shape[2] != 4:
        raise ValueError("Render frame must be RGBA.")
    height, width, _channels = array.shape
    return QImage(array.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_WINDOW_TITLE)
        self.resize(1320, 860)

        self.analysis: AnalysisResult | None = None
        self.geometry: GeometryResult | None = None
        self.source_path: Path | None = None

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        header = QHBoxLayout()
        self.open_button = QPushButton("Open media or table")
        self.open_button.clicked.connect(self.open_file)
        self.rebuild_button = QPushButton("Rebuild")
        self.rebuild_button.clicked.connect(self.rebuild_geometry)
        self.render_button = QPushButton("Render view")
        self.render_button.clicked.connect(self.render_view)
        self.export_button = QPushButton("Export MP4")
        self.export_button.clicked.connect(self.export_mp4)
        self.export_button.setEnabled(False)
        header.addWidget(self.open_button)
        header.addWidget(self.rebuild_button)
        header.addWidget(self.render_button)
        header.addWidget(self.export_button)
        header.addStretch(1)
        root_layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter, 1)

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 10, 0)
        controls_layout.setSpacing(10)

        self.preset_combo = QComboBox()
        self.preset_combo.addItems(DEFAULT_PRESETS.keys())
        self.preset_combo.currentTextChanged.connect(self.apply_preset)

        self.x_edit = QLineEdit()
        self.y_edit = QLineEdit()
        self.z_edit = QLineEdit()
        self.color_edit = QLineEdit()
        self.size_edit = QLineEdit()

        form = QFormLayout()
        form.addRow("Preset", self.preset_combo)
        form.addRow("X", self.x_edit)
        form.addRow("Y", self.y_edit)
        form.addRow("Z", self.z_edit)
        form.addRow("Color", self.color_edit)
        form.addRow("Size", self.size_edit)
        controls_layout.addLayout(form)

        render_form = QFormLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(320, 7680)
        self.width_spin.setSingleStep(160)
        self.width_spin.setValue(1280)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(240, 4320)
        self.height_spin.setSingleStep(90)
        self.height_spin.setValue(720)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(12, 120)
        self.fps_spin.setValue(30)
        render_form.addRow("Width", self.width_spin)
        render_form.addRow("Height", self.height_spin)
        render_form.addRow("FPS", self.fps_spin)
        controls_layout.addLayout(render_form)

        self.info_box = QTextEdit()
        self.info_box.setReadOnly(True)
        controls_layout.addWidget(self.info_box, 1)
        splitter.addWidget(controls)

        output = QWidget()
        output_layout = QVBoxLayout(output)
        output_layout.setContentsMargins(10, 0, 0, 0)
        self.render_label = QLabel("Open a local audio, video, CSV, TSV, TXT, or XLSX file.")
        self.render_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.render_label.setMinimumSize(720, 420)
        self.render_label.setStyleSheet("background:#070b11;color:#dbe6f4;border:1px solid #263447;")
        output_layout.addWidget(self.render_label, 1)
        splitter.addWidget(output)
        splitter.setSizes([420, 900])

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.apply_preset(self.preset_combo.currentText())
        self.set_status("Ready.")

    def set_status(self, message: str) -> None:
        self.status.showMessage(message)
        QApplication.processEvents()

    def apply_preset(self, name: str) -> None:
        preset = DEFAULT_PRESETS.get(name, DEFAULT_PRESETS["Audio PCA"])
        self.x_edit.setText(preset["x"])
        self.y_edit.setText(preset["y"])
        self.z_edit.setText(preset["z"])
        self.color_edit.setText(preset["color"])
        self.size_edit.setText(preset["size"])

    def open_file(self) -> None:
        path_text, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open local data",
            str(Path.home()),
            "Supported files (*.mp3 *.wav *.flac *.ogg *.m4a *.mp4 *.mov *.avi *.mkv *.webm *.csv *.tsv *.txt *.xlsx);;All files (*)",
        )
        if not path_text:
            return
        try:
            self.source_path = Path(path_text)
            self.set_status(f"Analyzing {self.source_path.name}...")
            if is_table_file(self.source_path):
                self.analysis = analysis_from_table_file(self.source_path)
            else:
                self.analysis = analyze_media(self.source_path)
            self.rebuild_geometry()
            self.info_box.setPlainText(format_feature_reference(self.analysis))
            self.export_button.setEnabled(True)
            self.set_status(f"Loaded {self.source_path.name}.")
        except Exception as exc:  # noqa: BLE001
            self.show_error("Could not open file", exc)

    def rebuild_geometry(self) -> None:
        if self.analysis is None:
            return
        try:
            self.geometry = build_geometry(
                self.analysis,
                self.x_edit.text().strip() or "pc1",
                self.y_edit.text().strip() or "pc2",
                self.z_edit.text().strip() or "pc3",
                self.color_edit.text().strip() or "time",
                self.size_edit.text().strip() or "rms",
                max_points=3000,
            )
            self.render_view()
        except Exception as exc:  # noqa: BLE001
            self.show_error("Could not build geometry", exc)

    def export_options(self, output_path: str) -> ExportOptions:
        return ExportOptions(
            output_path=output_path,
            width=int(self.width_spin.value()),
            height=int(self.height_spin.value()),
            fps=int(self.fps_spin.value()),
            layout=default_export_layout(),
            title=APP_NAME,
            project_title=APP_NAME,
            show_project_title=True,
            show_watermark=False,
        )

    def render_view(self) -> None:
        if self.analysis is None or self.geometry is None:
            return
        session: ExportPreviewSession | None = None
        try:
            width = max(640, int(self.width_spin.value()))
            height = max(360, int(self.height_spin.value()))
            options = self.export_options(str(Path.home() / "metriq_visualizer_render_check.mp4"))
            options.width = width
            options.height = height
            session = ExportPreviewSession(self.analysis, self.geometry, options)
            midpoint = float(self.analysis.times[len(self.analysis.times) // 2]) if self.analysis.times.size else 0.0
            frame = session.render_frame(current_time=midpoint, output_size=(width, height))
            pixmap = QPixmap.fromImage(_image_from_rgba(frame))
            scaled = pixmap.scaled(
                self.render_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.render_label.setPixmap(scaled)
            self.set_status("Rendered current view.")
        except Exception as exc:  # noqa: BLE001
            self.show_error("Could not render view", exc)
        finally:
            if session is not None:
                session.close()

    def export_mp4(self) -> None:
        if self.analysis is None or self.geometry is None:
            return
        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save MP4",
            str(Path.home() / "metriq_visualizer.mp4"),
            "MP4 video (*.mp4)",
        )
        if not output_path:
            return
        try:
            def report(progress: float, message: str) -> None:
                self.set_status(f"{int(progress * 100):3d}% - {message}")

            render_export_video(self.analysis, self.geometry, self.export_options(output_path), progress_callback=report)
            self.set_status(f"Saved {output_path}")
        except Exception as exc:  # noqa: BLE001
            self.show_error("Could not export MP4", exc)

    def show_error(self, title: str, exc: BaseException) -> None:
        details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        QMessageBox.critical(self, title, details[-4000:])
        self.set_status(str(exc))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
