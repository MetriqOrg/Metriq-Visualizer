# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Low-latency microphone scope for education, content, and field observation."""

from __future__ import annotations

import colorsys
import math
import os
import platform
import tempfile
import threading
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from metriq_visualizer_atomic import atomic_destination
from metriq_visualizer_theme import CutCornerFrame, current_palette, cut_corner_path

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - depends on host audio stack
    sd = None

try:
    import soundfile as sf
except Exception:  # pragma: no cover - core requirements normally include it
    sf = None


DEFAULT_INPUT_TOKEN = "__system_default_input__"


class LiveAudioEngine:
    """PortAudio capture with a bounded callback and retryable host fallback."""

    def __init__(self, *, sample_rate: int = 48_000, block_size: int = 0, seconds: float = 8.0) -> None:
        self.sample_rate = int(sample_rate)
        self.block_size = max(0, int(block_size))
        self.history_seconds = float(max(1.0, seconds))
        self.max_samples = int(self.history_seconds * self.sample_rate)
        self._chunks: deque[np.ndarray] = deque()
        self._sample_count = 0
        self._recorded: list[np.ndarray] = []
        self._recorded_count = 0
        self._recording = False
        self._lock = threading.Lock()
        self._stream: Any | None = None
        self.last_status = "Stopped"

    @property
    def active(self) -> bool:
        stream = self._stream
        if stream is None:
            return False
        state = getattr(stream, "active", None)
        return True if state is None else bool(state)

    @property
    def recording(self) -> bool:
        return self._recording

    def _candidate_rates(self, device: int | None) -> list[int]:
        rates: list[int] = [int(self.sample_rate)]
        if sd is not None:
            with suppress(Exception):
                info = sd.query_devices(device, "input")
                native = int(round(float(info.get("default_samplerate", 0))))
                if native > 0:
                    rates.append(native)
        rates.extend((48_000, 44_100))
        return list(dict.fromkeys(rate for rate in rates if rate > 0))

    def start(self, device: int | None = None) -> None:
        if sd is None:
            raise RuntimeError("Live input requires the optional sounddevice package.")
        if self.active:
            return
        if self._stream is not None:
            # A host-device disconnect can leave a closed PortAudio stream
            # object behind. Close and replace it so Start input is retryable.
            stale = self._stream
            self._stream = None
            with suppress(Exception):
                stale.stop()
            with suppress(Exception):
                stale.close()

        # Device enumeration may fail before macOS presents its TCC permission
        # prompt. The operating-system default must still be attempted.
        if device is not None:
            try:
                info = sd.query_devices(device, "input")
                if int(info.get("max_input_channels", 0)) < 1:
                    raise RuntimeError("The selected device has no input channels.")
            except Exception as exc:
                raise RuntimeError(f"The selected input device is unavailable: {exc}") from exc

        self.last_status = "Starting"
        errors: list[str] = []
        for rate in self._candidate_rates(device):
            for latency in ("low", None):
                kwargs: dict[str, Any] = {
                    "device": device,
                    "channels": 1,
                    "samplerate": rate,
                    "blocksize": self.block_size,
                    "dtype": "float32",
                    "callback": self._callback,
                }
                if latency is not None:
                    kwargs["latency"] = latency
                with suppress(Exception):
                    sd.check_input_settings(device=device, channels=1, dtype="float32", samplerate=rate)
                stream: Any | None = None
                try:
                    stream = sd.InputStream(**kwargs)
                    stream.start()
                    actual_rate = int(round(float(getattr(stream, "samplerate", rate))))
                    self.sample_rate = actual_rate if actual_rate > 0 else rate
                    self.max_samples = int(self.history_seconds * self.sample_rate)
                    with self._lock:
                        self._chunks.clear()
                        self._sample_count = 0
                        if self._recording:
                            self._recorded = []
                            self._recorded_count = 0
                    self._stream = stream
                    self.last_status = "Listening"
                    return
                except Exception as exc:  # noqa: BLE001 - host APIs raise varied exceptions
                    errors.append(str(exc))
                    if stream is not None:
                        with suppress(Exception):
                            stream.close()

        self._stream = None
        self.last_status = "Start failed"
        detail = next((message for message in reversed(errors) if message), "No compatible input stream was available.")
        if platform.system() == "Darwin":
            detail += (
                "\n\nOn macOS, allow microphone access for Metriq Visualizer (or the terminal/Python host) "
                "under System Settings → Privacy & Security → Microphone, then retry."
            )
        raise RuntimeError(detail)

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            with suppress(Exception):
                stream.stop()
            with suppress(Exception):
                stream.close()
        self.last_status = "Stopped"

    def set_recording(self, enabled: bool) -> None:
        with self._lock:
            if enabled and not self._recording:
                self._recorded = []
                self._recorded_count = 0
            self._recording = bool(enabled)

    def _callback(self, indata: np.ndarray, _frames: int, _time: Any, status: Any) -> None:
        array = np.asarray(indata, dtype=np.float32)
        chunk = (array[:, 0] if array.ndim > 1 else array).reshape(-1).copy()
        with self._lock:
            self._chunks.append(chunk)
            self._sample_count += int(chunk.size)
            while self._sample_count > self.max_samples and self._chunks:
                self._sample_count -= int(self._chunks.popleft().size)
            if self._recording:
                self._recorded.append(chunk.copy())
                self._recorded_count += int(chunk.size)
            self.last_status = str(status) if status else "Listening"

    def snapshot(self, seconds: float = 2.0) -> np.ndarray:
        required = int(max(0.05, seconds) * self.sample_rate)
        with self._lock:
            if not self._chunks:
                return np.zeros(required, dtype=np.float32)
            chunks = list(self._chunks)
        values = np.concatenate(chunks)
        if values.size >= required:
            return values[-required:]
        output = np.zeros(required, dtype=np.float32)
        output[-values.size :] = values
        return output

    @property
    def recorded_sample_count(self) -> int:
        with self._lock:
            return int(self._recorded_count)

    def recorded_audio(self) -> np.ndarray:
        with self._lock:
            if not self._recorded:
                return np.empty(0, dtype=np.float32)
            return np.concatenate(list(self._recorded)).astype(np.float32, copy=False)

    def clear_recording(self) -> None:
        with self._lock:
            self._recorded = []
            self._recorded_count = 0


def extract_live_features(samples: np.ndarray, sample_rate: int) -> dict[str, float]:
    """Extract bounded scalar features for one live trajectory update."""

    values = np.asarray(samples, dtype=np.float32).reshape(-1)
    if values.size < 16 or sample_rate <= 0:
        return {
            "rms": 0.0,
            "peak": 0.0,
            "rms_db": -120.0,
            "dominant_frequency": 0.0,
            "spectral_centroid": 0.0,
            "spectral_bandwidth": 0.0,
            "spectral_rolloff": 0.0,
            "spectral_flatness": 0.0,
            "zero_crossing_rate": 0.0,
            "onset_strength": 0.0,
        }
    window_count = min(values.size, 8_192)
    frame = values[-window_count:]
    rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float32)) + 1e-20))
    peak = float(np.max(np.abs(frame), initial=0.0))
    signs = np.signbit(frame)
    zcr = float(np.mean(signs[1:] != signs[:-1])) if frame.size > 1 else 0.0
    spectrum = np.abs(np.fft.rfft(frame * np.hanning(frame.size))).astype(np.float64)
    frequencies = np.fft.rfftfreq(frame.size, d=1.0 / float(sample_rate))
    total = float(np.sum(spectrum))
    spectral_peak = float(np.max(spectrum, initial=0.0))
    if total <= 1e-10 or spectral_peak <= 1e-10:
        dominant = centroid = bandwidth = rolloff = flatness = 0.0
    else:
        dominant_index = int(np.argmax(spectrum[1:]) + 1) if spectrum.size > 1 else 0
        dominant = float(frequencies[dominant_index])
        centroid = float(np.sum(frequencies * spectrum) / total)
        bandwidth = float(np.sqrt(np.sum(np.square(frequencies - centroid) * spectrum) / total))
        cumulative = np.cumsum(spectrum)
        rolloff = float(frequencies[min(int(np.searchsorted(cumulative, total * 0.85)), frequencies.size - 1)])
        flatness = float(np.exp(np.mean(np.log(spectrum + 1e-20))) / max(float(np.mean(spectrum)), 1e-20))
    crest = max(0.0, peak - rms)
    return {
        "rms": rms,
        "peak": peak,
        "rms_db": 20.0 * math.log10(max(rms, 1e-6)),
        "dominant_frequency": dominant,
        "spectral_centroid": centroid,
        "spectral_bandwidth": bandwidth,
        "spectral_rolloff": rolloff,
        "spectral_flatness": flatness,
        "zero_crossing_rate": zcr,
        "onset_strength": crest,
    }


class LiveScopeWidget(QWidget):
    """Paints waveform, spectrum, rolling spectrogram, and plain-language metrics."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 180)
        self.waveform = np.zeros(1_024, dtype=np.float32)
        self.frequencies = np.linspace(0.0, 24_000.0, 256)
        self.spectrum_db = np.full(256, -100.0, dtype=np.float32)
        self.spectrogram: deque[np.ndarray] = deque(maxlen=180)
        self.metrics = {"RMS": "—", "Peak": "—", "Dominant": "—", "Centroid": "—", "ZCR": "—"}

    def set_audio(self, samples: np.ndarray, sample_rate: int) -> None:
        if samples.size < 16:
            return
        samples = np.asarray(samples, dtype=np.float32)
        display_count = min(samples.size, 2_048)
        self.waveform = samples[-display_count:].copy()
        fft_count = min(samples.size, 8_192)
        windowed = samples[-fft_count:] * np.hanning(fft_count).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(windowed))
        frequencies = np.fft.rfftfreq(fft_count, d=1.0 / sample_rate)
        spectral_peak = float(np.max(spectrum)) if spectrum.size else 0.0
        silent = spectral_peak <= 1e-10
        if silent:
            db = np.full(spectrum.shape, -100.0, dtype=np.float64)
        else:
            db = 20.0 * np.log10(np.maximum(spectrum / spectral_peak, 1e-5))
        keep = frequencies <= min(24_000.0, sample_rate / 2)
        frequencies = frequencies[keep]
        db = np.clip(db[keep], -100.0, 0.0)
        if frequencies.size:
            target_freqs = np.geomspace(30.0, max(31.0, frequencies[-1]), 256)
            target_freqs[0] = 0.0
            self.frequencies = target_freqs
            self.spectrum_db = np.interp(target_freqs, frequencies, db).astype(np.float32)
            spec_column = np.interp(
                np.geomspace(30.0, max(31.0, frequencies[-1]), 128),
                frequencies,
                db,
            ).astype(np.float32)
            self.spectrogram.append(spec_column)
        rms = float(np.sqrt(np.mean(np.square(samples))))
        peak = float(np.max(np.abs(samples)))
        rms_db = 20.0 * math.log10(max(rms, 1e-8))
        peak_db = 20.0 * math.log10(max(peak, 1e-8))
        if spectrum.size > 1 and not silent:
            dominant_index = int(np.argmax(spectrum[1:]) + 1)
            dominant = float(np.fft.rfftfreq(fft_count, d=1.0 / sample_rate)[dominant_index])
            bins = np.fft.rfftfreq(fft_count, d=1.0 / sample_rate)
            centroid = float(np.sum(bins * spectrum) / max(np.sum(spectrum), 1e-8))
        else:
            dominant = centroid = 0.0
        signs = np.signbit(samples)
        zcr = float(np.mean(signs[1:] != signs[:-1])) if samples.size > 1 else 0.0
        self.metrics = {
            "RMS": f"{rms_db:5.1f} dBFS",
            "Peak": f"{peak_db:5.1f} dBFS",
            "Dominant": self._format_frequency(dominant),
            "Centroid": self._format_frequency(centroid),
            "ZCR": f"{zcr * 100:4.1f}%",
        }
        self.update()

    @staticmethod
    def _format_frequency(value: float) -> str:
        return f"{value / 1000:.2f} kHz" if value >= 1_000 else f"{value:.0f} Hz"

    def _panel(self, painter: QPainter, rect: QRectF, title: str) -> QRectF:
        p = current_palette()
        path = cut_corner_path(rect, 10)
        painter.fillPath(path, QColor(p.surface))
        painter.setPen(QPen(QColor(p.border), 1))
        painter.drawPath(path)
        painter.setPen(QColor(p.accent))
        font = QFont("Cascadia Mono", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect.adjusted(12, 7, -8, -5), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, title)
        return rect.adjusted(10, 27, -10, -10)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(p.background))
        margin = 10.0
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin
        top_h = height * 0.33
        bottom_h = height - top_h - 10
        waveform_rect = QRectF(margin, margin, width * 0.62 - 5, top_h)
        metrics_rect = QRectF(margin + width * 0.62 + 5, margin, width * 0.38 - 5, top_h)
        spectrum_rect = QRectF(margin, margin + top_h + 10, width * 0.50 - 5, bottom_h)
        spectrogram_rect = QRectF(margin + width * 0.50 + 5, margin + top_h + 10, width * 0.50 - 5, bottom_h)
        wave_area = self._panel(painter, waveform_rect, "01 / WAVEFORM")
        metrics_area = self._panel(painter, metrics_rect, "02 / SIGNAL METRICS")
        spectrum_area = self._panel(painter, spectrum_rect, "03 / LOG SPECTRUM")
        spectrogram_area = self._panel(painter, spectrogram_rect, "04 / SPECTROGRAM")
        self._draw_grid(painter, wave_area)
        self._draw_waveform(painter, wave_area)
        self._draw_metrics(painter, metrics_area)
        self._draw_grid(painter, spectrum_area)
        self._draw_spectrum(painter, spectrum_area)
        self._draw_spectrogram(painter, spectrogram_area)
        painter.end()

    def _draw_grid(self, painter: QPainter, rect: QRectF) -> None:
        p = current_palette()
        painter.setPen(QPen(QColor(p.grid), 1))
        for index in range(1, 6):
            x = rect.left() + rect.width() * index / 6
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for index in range(1, 4):
            y = rect.top() + rect.height() * index / 4
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

    def _draw_waveform(self, painter: QPainter, rect: QRectF) -> None:
        p = current_palette()
        values = self.waveform
        if values.size < 2:
            return
        if values.size > int(rect.width() * 2):
            indices = np.linspace(0, values.size - 1, max(2, int(rect.width() * 2))).astype(int)
            values = values[indices]
        center = rect.center().y()
        scale = rect.height() * 0.45
        points = QPolygonF(
            [
                QPointF(rect.left() + rect.width() * i / max(1, values.size - 1), center - float(value) * scale)
                for i, value in enumerate(values)
            ]
        )
        painter.setPen(QPen(QColor(p.accent), 1.35))
        painter.drawPolyline(points)

    def _draw_metrics(self, painter: QPainter, rect: QRectF) -> None:
        p = current_palette()
        painter.setFont(QFont("Cascadia Mono", 9))
        row_height = rect.height() / max(1, len(self.metrics))
        for row, (name, value) in enumerate(self.metrics.items()):
            row_rect = QRectF(rect.left(), rect.top() + row * row_height, rect.width(), row_height)
            painter.setPen(QColor(p.muted))
            painter.drawText(row_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name.upper())
            painter.setPen(QColor(p.text))
            painter.drawText(row_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, value)

    def _draw_spectrum(self, painter: QPainter, rect: QRectF) -> None:
        p = current_palette()
        values = np.clip((self.spectrum_db + 100.0) / 100.0, 0.0, 1.0)
        if values.size < 2:
            return
        points = QPolygonF()
        for index, value in enumerate(values):
            x = rect.left() + rect.width() * index / max(1, values.size - 1)
            y = rect.bottom() - rect.height() * float(value)
            points.append(QPointF(x, y))
        painter.setPen(QPen(QColor(p.blue), 1.25))
        painter.drawPolyline(points)
        painter.setPen(QColor(p.muted))
        painter.setFont(QFont("Cascadia Mono", 7))
        painter.drawText(
            rect.adjusted(2, 0, -2, -1), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, "30 Hz"
        )
        painter.drawText(
            rect.adjusted(2, 0, -2, -1), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "24 kHz"
        )

    def _draw_spectrogram(self, painter: QPainter, rect: QRectF) -> None:
        p = current_palette()
        columns = list(self.spectrogram)
        painter.fillRect(rect, QColor(p.surface_raised))
        if not columns:
            painter.setPen(QColor(p.muted))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Waiting for signal")
            return
        column_width = rect.width() / max(1, self.spectrogram.maxlen or len(columns))
        bin_height = rect.height() / 128.0
        x_start = rect.right() - len(columns) * column_width
        for column_index, column in enumerate(columns):
            x = x_start + column_index * column_width
            for bin_index, db in enumerate(column):
                value = float(np.clip((db + 90.0) / 90.0, 0.0, 1.0))
                # Existing Metriq palette: navy -> cyan -> green -> pale blue.
                if value < 0.35:
                    color = QColor.fromRgbF(0.04, 0.11 + value * 0.25, 0.16 + value * 0.35)
                elif value < 0.70:
                    t = (value - 0.35) / 0.35
                    color = QColor.fromRgbF(0.03, 0.35 + 0.35 * t, 0.45 - 0.12 * t)
                else:
                    t = (value - 0.70) / 0.30
                    color = QColor.fromRgbF(0.12 + 0.65 * t, 0.65 + 0.25 * t, 0.52 + 0.45 * t)
                y = rect.bottom() - (bin_index + 1) * bin_height
                painter.fillRect(QRectF(x, y, column_width + 0.4, bin_height + 0.4), color)


class LiveInputPanel(QWidget):
    """Embedded microphone source that feeds the main three-dimensional viewport."""

    trajectoryUpdated = Signal(object, object, object)
    activeChanged = Signal(bool)
    captureReady = Signal(str)
    errorOccurred = Signal(str)

    QUALITY_LEVELS: dict[str, tuple[int, int]] = {
        "Efficient": (60, 420),
        "Balanced": (40, 900),
        "Full live": (25, 1_800),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LiveInputPanel")
        self.setMinimumHeight(250)
        self.engine = LiveAudioEngine(sample_rate=48_000, block_size=0, seconds=8.0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self._points: deque[np.ndarray] = deque(maxlen=900)
        self._colors: deque[np.ndarray] = deque(maxlen=900)
        self._sizes: deque[float] = deque(maxlen=900)
        self._sample_counter = 0
        self._phase = 0.0
        self._previous_features: dict[str, float] | None = None
        self._build_ui()
        self.reload_devices()
        self._quality_changed(self.quality_combo.currentText())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(7)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        eyebrow = QLabel("LIVE INPUT / MAIN 3D FIELD")
        eyebrow.setObjectName("Eyebrow")
        controls.addWidget(eyebrow)
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(230)
        controls.addWidget(self.device_combo, 2)
        self.rate_combo = QComboBox()
        for label, value in (
            ("Host native", 0),
            ("44.1 kHz", 44_100),
            ("48 kHz", 48_000),
            ("96 kHz", 96_000),
        ):
            self.rate_combo.addItem(label, value)
        self.rate_combo.setCurrentIndex(0)
        controls.addWidget(self.rate_combo)
        self.mapping_combo = QComboBox()
        self.mapping_combo.addItems(("Birdsong", "Spectral orbit", "Waveform trace"))
        controls.addWidget(self.mapping_combo)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(self.QUALITY_LEVELS)
        self.quality_combo.setCurrentText("Balanced")
        self.quality_combo.currentTextChanged.connect(self._quality_changed)
        controls.addWidget(self.quality_combo)
        self.record_check = QCheckBox("Record")
        self.record_check.toggled.connect(self.engine.set_recording)
        controls.addWidget(self.record_check)
        self.start_button = QPushButton("Start input")
        self.start_button.setProperty("accent", True)
        self.start_button.clicked.connect(self.toggle_input)
        controls.addWidget(self.start_button)
        self.freeze_button = QPushButton("Freeze")
        self.freeze_button.setCheckable(True)
        self.freeze_button.toggled.connect(lambda frozen: self.freeze_button.setText("Resume" if frozen else "Freeze"))
        controls.addWidget(self.freeze_button)
        self.clear_button = QPushButton("Clear trail")
        self.clear_button.clicked.connect(self.clear_trajectory)
        controls.addWidget(self.clear_button)
        self.save_button = QPushButton("Save WAV")
        self.save_button.clicked.connect(self.save_capture)
        self.save_button.setEnabled(False)
        controls.addWidget(self.save_button)
        self.analyze_button = QPushButton("Analyze capture")
        self.analyze_button.clicked.connect(self.analyze_capture)
        self.analyze_button.setEnabled(False)
        controls.addWidget(self.analyze_button)
        root.addLayout(controls)

        state_row = QHBoxLayout()
        self.status = QLabel("● OFFLINE")
        self.status.setObjectName("StatusOnline")
        self.status.setMinimumWidth(180)
        state_row.addWidget(self.status)
        self.metrics_label = QLabel("RMS —   ·   PEAK —   ·   DOM —   ·   CENTROID —")
        self.metrics_label.setObjectName("Subtle")
        self.metrics_label.setMinimumWidth(560)
        state_row.addWidget(self.metrics_label, 1)
        root.addLayout(state_row)

        self.scope = LiveScopeWidget()
        self.scope.setMinimumHeight(155)
        self.scope.setMaximumHeight(230)
        root.addWidget(self.scope, 1)

    def reload_devices(self) -> None:
        self.device_combo.clear()
        self.device_combo.addItem("System default input", DEFAULT_INPUT_TOKEN)
        self.start_button.setEnabled(sd is not None)
        if sd is None:
            self.status.setText("● SOUNDDEVICE NOT INSTALLED")
            return
        try:
            devices = sd.query_devices()
            default_input: int | None = None
            with suppress(Exception):
                default_input = int(sd.default.device[0])
            default_row = 0
            for index, info in enumerate(devices):
                if int(info.get("max_input_channels", 0)) <= 0:
                    continue
                self.device_combo.addItem(f"{index}: {info.get('name', 'Input device')}", index)
                if index == default_input:
                    default_row = self.device_combo.count() - 1
            # Keep the explicit system-default route as the initial choice; it
            # is the most reliable way to trigger a macOS permission prompt.
            self.device_combo.setCurrentIndex(0 if default_row >= 0 else 0)
            self.status.setText("● READY")
        except Exception as exc:  # noqa: BLE001
            # Enumeration failure is not fatal. InputStream(device=None) may
            # still succeed and may be required before the host grants access.
            self.status.setText("● DEFAULT INPUT AVAILABLE")
            self.device_combo.setToolTip(f"Device enumeration failed; system default will still be attempted: {exc}")

    def _quality_changed(self, name: str) -> None:
        interval, maximum = self.QUALITY_LEVELS.get(str(name), self.QUALITY_LEVELS["Balanced"])
        previous_points = list(self._points)[-maximum:]
        previous_colors = list(self._colors)[-maximum:]
        previous_sizes = list(self._sizes)[-maximum:]
        self._points = deque(previous_points, maxlen=maximum)
        self._colors = deque(previous_colors, maxlen=maximum)
        self._sizes = deque(previous_sizes, maxlen=maximum)
        self.timer.setInterval(interval)
        if self.timer.isActive():
            self.timer.start(interval)

    def toggle_input(self) -> None:
        if self.engine.active:
            self.stop_input()
        else:
            self.start_input()

    def start_input(self) -> bool:
        if self.engine.active:
            return True
        try:
            selected = self.device_combo.currentData()
            device = None if selected in (None, DEFAULT_INPUT_TOKEN) else int(selected)
            requested_rate = int(self.rate_combo.currentData() or 0)
            self.engine.sample_rate = requested_rate
            self.engine.block_size = 0
            self.engine.set_recording(self.record_check.isChecked())
            self.engine.start(device)
            self.timer.start()
            self.start_button.setText("Stop input")
            self.start_button.setProperty("accent", False)
            self.start_button.setProperty("danger", True)
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self.device_combo.setEnabled(False)
            self.rate_combo.setEnabled(False)
            self.status.setText(f"● LISTENING / {self.engine.sample_rate:,} HZ")
            self.activeChanged.emit(True)
            return True
        except Exception as exc:  # noqa: BLE001
            self.engine.stop()
            message = str(exc)
            self.status.setText("● INPUT FAILED — RETRY AVAILABLE")
            self.errorOccurred.emit(message)
            self.activeChanged.emit(False)
            return False

    def stop_input(self) -> None:
        self.timer.stop()
        self.engine.stop()
        self.start_button.setText("Start input")
        self.start_button.setProperty("danger", False)
        self.start_button.setProperty("accent", True)
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.device_combo.setEnabled(True)
        self.rate_combo.setEnabled(True)
        self.status.setText("● OFFLINE")
        has_capture = self.engine.recorded_sample_count > 0
        self.save_button.setEnabled(has_capture)
        self.analyze_button.setEnabled(has_capture)
        self.activeChanged.emit(False)

    @staticmethod
    def _normalized_frequency(value: float, nyquist: float) -> float:
        if value <= 0.0 or nyquist <= 1.0:
            return 0.0
        return float(np.clip(math.log1p(value) / math.log1p(nyquist), 0.0, 1.0))

    def _mapped_point(self, features: dict[str, float], samples: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
        nyquist = self.engine.sample_rate / 2.0
        dominant = self._normalized_frequency(features["dominant_frequency"], nyquist)
        centroid = self._normalized_frequency(features["spectral_centroid"], nyquist)
        bandwidth = float(np.clip(features["spectral_bandwidth"] / max(nyquist, 1.0), 0.0, 1.0))
        rms_level = float(np.clip((features["rms_db"] + 72.0) / 72.0, 0.0, 1.0))
        flatness = float(np.clip(features["spectral_flatness"], 0.0, 1.0))
        zcr = float(np.clip(features["zero_crossing_rate"] * 5.0, 0.0, 1.0))
        onset = float(np.clip(features["onset_strength"] * 10.0, 0.0, 1.0))
        previous = self._previous_features or features
        flux = float(
            np.clip(
                abs(features["spectral_centroid"] - previous.get("spectral_centroid", 0.0)) / max(nyquist, 1.0)
                + abs(features["rms"] - previous.get("rms", 0.0)) * 4.0,
                0.0,
                1.0,
            )
        )
        self._previous_features = dict(features)
        mapping = self.mapping_combo.currentText()
        if mapping == "Spectral orbit":
            self._phase += 0.045 + 0.25 * dominant
            radius = 0.25 + 0.75 * centroid
            point = np.asarray(
                (math.cos(self._phase) * radius, math.sin(self._phase) * radius, 2.0 * bandwidth - 1.0),
                dtype=np.float64,
            )
        elif mapping == "Waveform trace":
            maximum = max(2, self._points.maxlen or 900)
            x = 2.0 * ((self._sample_counter % maximum) / (maximum - 1)) - 1.0
            tail = samples[-min(512, samples.size) :] if samples.size else np.zeros(1)
            point = np.asarray((x, float(np.clip(np.mean(tail) * 5.0, -1.0, 1.0)), 2.0 * rms_level - 1.0))
        else:  # Birdsong-compatible live map
            point = np.asarray(
                (
                    2.0 * dominant - 1.0,
                    2.0 * np.clip(0.62 * centroid + 0.23 * bandwidth + 0.15 * flatness, 0.0, 1.0) - 1.0,
                    2.0 * np.clip(0.45 * flux + 0.30 * onset + 0.25 * zcr, 0.0, 1.0) - 1.0,
                ),
                dtype=np.float64,
            )
        hue = (0.52 + 0.42 * dominant + 0.06 * self._sample_counter / max(1, self._points.maxlen or 1)) % 1.0
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.68, 0.72 + 0.28 * rms_level)
        color = np.asarray((red, green, blue, 0.30 + 0.70 * rms_level), dtype=np.float64)
        size = 0.35 + 1.65 * rms_level
        return point, color, size

    def _refresh(self) -> None:
        if not self.engine.active:
            return
        samples = self.engine.snapshot(0.75)
        self.scope.set_audio(samples, self.engine.sample_rate)
        features = extract_live_features(samples, self.engine.sample_rate)
        if not self.freeze_button.isChecked():
            point, color, size = self._mapped_point(features, samples)
            self._points.append(point)
            self._colors.append(color)
            self._sizes.append(size)
            self._sample_counter += 1
            self.trajectoryUpdated.emit(
                np.asarray(self._points, dtype=np.float64),
                np.asarray(self._colors, dtype=np.float64),
                np.asarray(self._sizes, dtype=np.float64),
            )
        self.metrics_label.setText(
            f"RMS {features['rms_db']:6.1f} dBFS   ·   PEAK {20.0 * math.log10(max(features['peak'], 1e-6)):6.1f} dBFS   ·   "
            f"DOM {LiveScopeWidget._format_frequency(features['dominant_frequency'])}   ·   "
            f"CENTROID {LiveScopeWidget._format_frequency(features['spectral_centroid'])}"
        )
        self.status.setText(
            "● LISTENING" if self.engine.last_status == "Listening" else f"● {self.engine.last_status.upper()}"
        )
        has_capture = self.engine.recorded_sample_count > 0
        self.save_button.setEnabled(has_capture)
        self.analyze_button.setEnabled(has_capture)

    def clear_trajectory(self) -> None:
        self._points.clear()
        self._colors.clear()
        self._sizes.clear()
        self._previous_features = None
        self._phase = 0.0
        self._sample_counter = 0
        self.trajectoryUpdated.emit(
            np.empty((0, 3), dtype=np.float64),
            np.empty((0, 4), dtype=np.float64),
            np.empty(0, dtype=np.float64),
        )

    def _write_capture(self, path: Path) -> Path:
        if sf is None:
            raise RuntimeError("soundfile is required to write WAV captures.")
        samples = self.engine.recorded_audio()
        if samples.size == 0:
            raise RuntimeError("No recorded samples are available. Enable Record before or during capture.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_destination(path, suffix=".wav") as temporary_path:
            sf.write(str(temporary_path), samples, self.engine.sample_rate, subtype="PCM_16")
        return path

    def save_capture(self) -> None:
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Save microphone capture",
            str(Path.home() / "metriq_live_capture.wav"),
            "WAV audio (*.wav)",
        )
        if not path_text:
            return
        try:
            output = Path(path_text)
            if output.suffix.lower() != ".wav":
                output = output.with_suffix(".wav")
            self._write_capture(output)
            self.status.setText(f"● SAVED / {output.name.upper()}")
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(str(exc))

    def analyze_capture(self) -> None:
        try:
            if self.engine.active:
                self.stop_input()
            descriptor, temp_name = tempfile.mkstemp(prefix="metriq_live_", suffix=".wav")
            os.close(descriptor)
            Path(temp_name).unlink(missing_ok=True)
            output = self._write_capture(Path(temp_name))
            self.captureReady.emit(str(output))
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(str(exc))

    def shutdown(self) -> None:
        self.stop_input()

    def hideEvent(self, event: Any) -> None:  # type: ignore[override]
        if self.engine.active:
            self.stop_input()
        super().hideEvent(event)


class LiveInputDialog(QDialog):
    captureReady = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Metriq Live Input")
        self.resize(1080, 760)
        self.engine = LiveAudioEngine()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._refresh)
        self._build_ui()
        self._load_devices()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        header = CutCornerFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 10, 18, 10)
        title_box = QVBoxLayout()
        eyebrow = QLabel("LIVE / LOCAL MICROPHONE")
        eyebrow.setObjectName("Eyebrow")
        title = QLabel("Signal Monitor")
        title.setObjectName("Title")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)
        self.status = QLabel("● OFFLINE")
        self.status.setObjectName("StatusOnline")
        header_layout.addWidget(self.status)
        root.addWidget(header)

        control = CutCornerFrame()
        control_layout = QHBoxLayout(control)
        control_layout.setContentsMargins(14, 10, 14, 10)
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(300)
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(8_000, 192_000)
        self.rate_spin.setSingleStep(1_000)
        self.rate_spin.setValue(48_000)
        self.block_combo = QComboBox()
        self.block_combo.addItems(["256", "512", "1024", "2048", "4096"])
        self.block_combo.setCurrentText("1024")
        self.record_check = QCheckBox("Record capture")
        self.record_check.toggled.connect(self.engine.set_recording)
        self.start_button = QPushButton("Start input")
        self.start_button.setProperty("accent", True)
        self.start_button.clicked.connect(self._toggle_input)
        self.save_button = QPushButton("Save WAV")
        self.save_button.clicked.connect(self._save_capture)
        self.save_button.setEnabled(False)
        self.analyze_button = QPushButton("Analyze capture")
        self.analyze_button.clicked.connect(self._analyze_capture)
        self.analyze_button.setEnabled(False)
        control_layout.addWidget(QLabel("Device"))
        control_layout.addWidget(self.device_combo, 1)
        control_layout.addWidget(QLabel("Rate"))
        control_layout.addWidget(self.rate_spin)
        control_layout.addWidget(QLabel("Block"))
        control_layout.addWidget(self.block_combo)
        control_layout.addWidget(self.record_check)
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.save_button)
        control_layout.addWidget(self.analyze_button)
        root.addWidget(control)

        self.scope = LiveScopeWidget()
        root.addWidget(self.scope, 1)
        note = QLabel(
            "Local DSP only. The monitor reports waveform, spectrum, spectrogram, RMS, peak, dominant "
            "frequency, spectral centroid, and zero-crossing rate. Species or sound-event identification "
            "is intentionally left as a later plug-in layer."
        )
        note.setWordWrap(True)
        note.setObjectName("Subtle")
        root.addWidget(note)

    def _load_devices(self) -> None:
        self.device_combo.clear()
        if sd is None:
            self.device_combo.addItem("sounddevice is not installed", None)
            self.start_button.setEnabled(False)
            return
        try:
            devices = sd.query_devices()
            default_input = None
            with suppress(Exception):
                default_input = int(sd.default.device[0])
            selected = 0
            for index, info in enumerate(devices):
                if int(info.get("max_input_channels", 0)) <= 0:
                    continue
                self.device_combo.addItem(f"{index}: {info.get('name', 'Input device')}", index)
                if index == default_input:
                    selected = self.device_combo.count() - 1
            self.device_combo.setCurrentIndex(selected)
            if self.device_combo.count() == 0:
                self.device_combo.addItem("No input devices found", None)
                self.start_button.setEnabled(False)
        except Exception as exc:  # noqa: BLE001
            self.device_combo.addItem(f"Could not enumerate devices: {exc}", None)
            self.start_button.setEnabled(False)

    def _toggle_input(self) -> None:
        if self.engine.active:
            self._stop_input()
            return
        try:
            self.engine.sample_rate = int(self.rate_spin.value())
            self.engine.block_size = int(self.block_combo.currentText())
            device = self.device_combo.currentData()
            self.engine.start(None if device is None else int(device))
            self.timer.start()
            self.start_button.setText("Stop input")
            self.start_button.setProperty("accent", False)
            self.start_button.setProperty("danger", True)
            self.start_button.style().unpolish(self.start_button)
            self.start_button.style().polish(self.start_button)
            self.rate_spin.setEnabled(False)
            self.block_combo.setEnabled(False)
            self.device_combo.setEnabled(False)
            self.status.setText("● LISTENING")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not start microphone", str(exc))

    def _stop_input(self) -> None:
        self.timer.stop()
        self.engine.stop()
        self.start_button.setText("Start input")
        self.start_button.setProperty("danger", False)
        self.start_button.setProperty("accent", True)
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.rate_spin.setEnabled(True)
        self.block_combo.setEnabled(True)
        self.device_combo.setEnabled(True)
        self.status.setText("● OFFLINE")
        has_capture = self.engine.recorded_sample_count > 0
        self.save_button.setEnabled(has_capture)
        self.analyze_button.setEnabled(has_capture)

    def _refresh(self) -> None:
        samples = self.engine.snapshot(2.0)
        self.scope.set_audio(samples, self.engine.sample_rate)
        self.status.setText(
            "● LISTENING" if self.engine.last_status == "Listening" else f"● {self.engine.last_status.upper()}"
        )
        has_capture = self.engine.recorded_sample_count > 0
        self.save_button.setEnabled(has_capture)
        self.analyze_button.setEnabled(has_capture)

    def _write_capture(self, path: Path) -> Path:
        if sf is None:
            raise RuntimeError("soundfile is required to write WAV captures.")
        samples = self.engine.recorded_audio()
        if samples.size == 0:
            raise RuntimeError("No recorded samples are available. Enable Record capture first.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_destination(path, suffix=".wav") as temporary_path:
            sf.write(str(temporary_path), samples, self.engine.sample_rate, subtype="PCM_16")
        return path

    def _save_capture(self) -> None:
        path_text, _ = QFileDialog.getSaveFileName(
            self,
            "Save microphone capture",
            str(Path.home() / "metriq_live_capture.wav"),
            "WAV audio (*.wav)",
        )
        if not path_text:
            return
        try:
            output = Path(path_text)
            if output.suffix.lower() != ".wav":
                output = output.with_suffix(".wav")
            self._write_capture(output)
            QMessageBox.information(self, "Capture saved", str(output))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save capture", str(exc))

    def _analyze_capture(self) -> None:
        try:
            if self.engine.active:
                self._stop_input()
            fd, temp_name = tempfile.mkstemp(prefix="metriq_live_", suffix=".wav")
            try:
                import os

                os.close(fd)
            except OSError:
                pass
            Path(temp_name).unlink(missing_ok=True)
            output = self._write_capture(Path(temp_name))
            self.captureReady.emit(str(output))
            self.accept()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not analyze capture", str(exc))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_input()
        super().closeEvent(event)


__all__ = [
    "DEFAULT_INPUT_TOKEN",
    "LiveAudioEngine",
    "LiveInputDialog",
    "LiveInputPanel",
    "LiveScopeWidget",
    "extract_live_features",
]
