# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import ast
import csv
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable

import librosa
import numpy as np


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".wmv",
    ".flv",
}

TABLE_EXTENSIONS = {".csv", ".tsv", ".txt", ".xlsx"}

DEFAULT_PRESETS: dict[str, dict[str, str]] = {
    "Pitch/Timbre/Motion": {
        "x": "0.7*chroma_mean + 0.3*f0_hz",
        "y": "0.6*mfcc_1 + 0.4*mfcc_2",
        "z": "0.6*spectral_flux + 0.4*onset_strength",
        "color": "spectral_centroid_hz",
        "size": "rms",
    },
    "Audio PCA": {
        "x": "pc1",
        "y": "pc2",
        "z": "pc3",
        "color": "f0_hz",
        "size": "0.65*rms + 0.35*onset_strength",
    },
    "Rhythm / Brightness / Texture": {
        "x": "0.7*onset_strength + 0.3*spectral_flux",
        "y": "spectral_centroid_hz",
        "z": "0.6*spectral_flatness + 0.4*chroma_entropy",
        "color": "dominant_freq_hz",
        "size": "0.7*rms + 0.3*zcr",
    },
    "Table / PCA explorer": {
        "x": "pc1",
        "y": "pc2",
        "z": "pc3",
        "color": "time",
        "size": "magnitude",
    },
    "Table / Spread / Change": {
        "x": "column_mean",
        "y": "column_spread",
        "z": "delta_magnitude",
        "color": "time",
        "size": "magnitude",
    },
    "Manual starter": {
        "x": "pc1 + 0.2*mfcc_1",
        "y": "pc2 + 0.3*chroma_mean",
        "z": "pc3 + 0.2*spectral_flux",
        "color": "dominant_freq_hz",
        "size": "0.5*rms + 0.5*onset_strength",
    },
}


DEFAULT_PRESET_DISPLAY_LABELS: dict[str, dict[str, str]] = {
    "Audio PCA": {
        "x": "PCA axis 1",
        "y": "PCA axis 2",
        "z": "PCA axis 3",
        "color": "Pitch",
        "size": "Energy / attack",
    },
    "Pitch/Timbre/Motion": {
        "x": "Pitch",
        "y": "Timbre",
        "z": "Motion",
        "color": "Brightness",
        "size": "Loudness",
    },
    "Rhythm / Brightness / Texture": {
        "x": "Rhythm",
        "y": "Brightness",
        "z": "Texture",
        "color": "Frequency",
        "size": "Loudness / roughness",
    },
    "Table / PCA explorer": {
        "x": "PCA axis 1",
        "y": "PCA axis 2",
        "z": "PCA axis 3",
        "color": "Time",
        "size": "Magnitude",
    },
    "Table / Spread / Change": {
        "x": "Mean",
        "y": "Spread",
        "z": "Change",
        "color": "Time",
        "size": "Magnitude",
    },
    "Manual starter": {
        "x": "Geometry X",
        "y": "Geometry Y",
        "z": "Geometry Z",
        "color": "Frequency",
        "size": "Energy",
    },
}

FEATURE_FRIENDLY_LABELS: dict[str, str] = {
    "time": "Time",
    "t": "Time",
    "rms": "Loudness",
    "rms_db": "Loudness (dB)",
    "zcr": "Noisiness",
    "spectral_centroid_hz": "Brightness",
    "spectral_bandwidth_hz": "Spectral spread",
    "spectral_rolloff_hz": "Rolloff",
    "spectral_flatness": "Tone vs. noise",
    "spectral_flux": "Motion / change",
    "spectral_contrast_mean": "Spectral contrast",
    "dominant_freq_hz": "Dominant frequency",
    "onset_strength": "Attack",
    "chroma_mean": "Pitch class",
    "chroma_entropy": "Pitch entropy",
    "f0_hz": "Fundamental pitch",
    "pc1": "PCA axis 1",
    "pc2": "PCA axis 2",
    "pc3": "PCA axis 3",
    "row_index": "Row index",
    "magnitude": "Magnitude",
    "delta_magnitude": "Change",
    "column_mean": "Mean",
    "column_spread": "Spread",
}

FEATURE_DESCRIPTIONS = {
    "time": "Time in seconds.",
    "t": "Alias for time in seconds.",
    "rms": "Root-mean-square energy (loudness envelope).",
    "rms_db": "RMS energy in dB relative to the loudest frame in the file.",
    "zcr": "Zero crossing rate (noisiness / roughness proxy).",
    "spectral_centroid_hz": "Spectral centroid in Hz (brightness).",
    "spectral_bandwidth_hz": "Spectral bandwidth in Hz (spread).",
    "spectral_rolloff_hz": "Spectral rolloff in Hz.",
    "spectral_flatness": "Spectral flatness (tone vs. noise).",
    "spectral_flux": "Frame-to-frame spectral change.",
    "spectral_contrast_mean": "Mean spectral contrast across bands.",
    "dominant_freq_hz": "Frequency of the strongest spectral bin in Hz.",
    "onset_strength": "Onset strength / local attack energy.",
    "chroma_mean": "Mean chroma activity across pitch classes.",
    "chroma_entropy": "Pitch-class entropy.",
    "f0_hz": "Estimated fundamental frequency in Hz.",
    "row_index": "Row number from an imported table.",
    "magnitude": "Row-wise magnitude across imported numeric columns.",
    "delta_magnitude": "Row-to-row change magnitude across imported numeric columns.",
    "column_mean": "Mean value across imported numeric columns.",
    "column_spread": "Standard deviation across imported numeric columns.",
}

PITCH_CLASS_NAMES = [
    "C",
    "Cs",
    "D",
    "Ds",
    "E",
    "F",
    "Fs",
    "G",
    "Gs",
    "A",
    "As",
    "B",
]


@dataclass
class AnalysisResult:
    source_path: str
    audio_path: str
    sample_rate: int
    duration: float
    hop_length: int
    n_fft: int
    times: np.ndarray
    features: dict[str, np.ndarray]
    spectrogram_db: np.ndarray
    spectrogram_freqs_hz: np.ndarray
    chromagram: np.ndarray
    mfcc: np.ndarray
    feature_descriptions: dict[str, str] = field(default_factory=dict)
    source_kind: str = "media"


@dataclass
class GeometryResult:
    x_full: np.ndarray
    y_full: np.ndarray
    z_full: np.ndarray
    color_full: np.ndarray
    size_full: np.ndarray
    size_display_full: np.ndarray
    times_full: np.ndarray
    x_plot: np.ndarray
    y_plot: np.ndarray
    z_plot: np.ndarray
    color_plot: np.ndarray
    size_plot: np.ndarray
    times_plot: np.ndarray
    plot_indices: np.ndarray
    labels: dict[str, str]
    normalize_mode: str
    colormap: str
    active_mask_full: np.ndarray
    low_volume_cutoff_db: float


def is_video_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_table_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in TABLE_EXTENSIONS


def _safe_feature_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[^0-9a-zA-Z_]+", "_", str(name or "").strip().lower()).strip("_")
    if not base:
        base = "column"
    if base[0].isdigit():
        base = f"col_{base}"
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _parse_numeric(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        value = float(value)
        return value if math.isfinite(value) else None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    try:
        parsed = float(text)
    except Exception:
        return None
    return parsed if math.isfinite(parsed) else None


def _read_delimited_rows(path: str | Path) -> tuple[list[str], list[list[object]]]:
    with Path(path).expanduser().open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;	|")
        except Exception:
            dialect = csv.excel_tab if Path(path).suffix.lower() == ".tsv" else csv.excel
        reader = csv.reader(handle, dialect)
        rows = [list(row) for row in reader if any(str(cell).strip() for cell in row)]
    if not rows:
        raise ValueError("The selected table file is empty.")
    first = rows[0]
    header_like = any(_parse_numeric(cell) is None for cell in first)
    if header_like:
        headers = [str(cell).strip() or f"Column {idx + 1}" for idx, cell in enumerate(first)]
        data_rows = rows[1:]
    else:
        headers = [f"Column {idx + 1}" for idx in range(len(first))]
        data_rows = rows
    return headers, data_rows


def _read_xlsx_rows(path: str | Path) -> tuple[list[str], list[list[object]]]:
    try:
        from openpyxl import load_workbook
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("XLSX import requires openpyxl. Install it from requirements.txt and retry.") from exc

    workbook = load_workbook(filename=str(Path(path).expanduser()), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = [list(row) for row in sheet.iter_rows(values_only=True) if any(cell not in (None, "") for cell in row)]
    finally:
        workbook.close()
    if not rows:
        raise ValueError("The selected spreadsheet is empty.")
    first = rows[0]
    header_like = any(_parse_numeric(cell) is None for cell in first)
    if header_like:
        headers = [str(cell).strip() or f"Column {idx + 1}" for idx, cell in enumerate(first)]
        data_rows = rows[1:]
    else:
        headers = [f"Column {idx + 1}" for idx in range(len(first))]
        data_rows = rows
    return headers, data_rows


def _time_header_kind(name: str) -> str | None:
    cleaned = re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
    if cleaned in {"time", "t", "seconds", "second", "sec", "secs", "times", "timesec", "elapsed", "elapsedtime"} or cleaned.endswith("seconds") or cleaned.endswith("sec"):
        return "seconds"
    if cleaned in {"ms", "millisecond", "milliseconds", "timems", "elapsedms"} or cleaned.endswith("ms"):
        return "milliseconds"
    if cleaned in {"timestamp", "epoch", "epochtime", "unix", "unixseconds"} or cleaned.endswith("timestamp"):
        return "timestamp"
    return None


def analysis_from_table_file(path: str | Path) -> AnalysisResult:
    source = Path(path).expanduser()
    suffix = source.suffix.lower()
    if suffix == ".xlsx":
        headers, rows = _read_xlsx_rows(source)
    else:
        headers, rows = _read_delimited_rows(source)

    if not rows:
        raise ValueError("The selected table did not contain any data rows.")

    column_count = max(len(headers), max(len(row) for row in rows))
    headers = list(headers) + [f"Column {idx + 1}" for idx in range(len(headers), column_count)]
    used: set[str] = set()
    numeric_columns: list[tuple[str, str, np.ndarray, str | None]] = []

    for idx in range(column_count):
        original_name = headers[idx] if idx < len(headers) else f"Column {idx + 1}"
        raw_values = [row[idx] if idx < len(row) else None for row in rows]
        parsed = [_parse_numeric(value) for value in raw_values]
        finite = [value for value in parsed if value is not None]
        if len(finite) < 2:
            continue
        if len(finite) < max(2, int(round(0.6 * len(parsed)))):
            continue
        fill = float(np.median(np.asarray(finite, dtype=np.float64))) if finite else 0.0
        arr = np.asarray([fill if value is None else value for value in parsed], dtype=np.float64)
        safe_name = _safe_feature_name(original_name, used)
        numeric_columns.append((safe_name, str(original_name), arr, _time_header_kind(original_name)))

    if not numeric_columns:
        raise ValueError("No usable numeric columns were found. Provide a CSV or spreadsheet with at least one numeric field.")

    row_count = int(numeric_columns[0][2].size)
    time_name = None
    time_values = None
    for safe_name, original_name, arr, kind in numeric_columns:
        if kind is None:
            continue
        candidate = np.asarray(arr, dtype=np.float64).reshape(-1)
        if kind == "milliseconds":
            candidate = (candidate - candidate[0]) / 1000.0
        else:
            candidate = candidate - candidate[0]
        if candidate.size > 1 and np.all(np.diff(candidate) >= -1e-9) and float(candidate[-1] - candidate[0]) >= 0.0:
            time_name = safe_name
            time_values = candidate
            break

    if time_values is None:
        time_values = np.arange(row_count, dtype=np.float64)
    else:
        order = np.argsort(time_values, kind="stable")
        time_values = np.asarray(time_values[order], dtype=np.float64)
        numeric_columns = [(safe, original, arr[order], kind) for safe, original, arr, kind in numeric_columns]

    duration = float(time_values[-1]) if time_values.size > 0 else float(max(0, row_count - 1))
    if duration <= 0.0 and row_count > 1:
        time_values = np.arange(row_count, dtype=np.float64)
        duration = float(time_values[-1])

    features: dict[str, np.ndarray] = {"time": time_values, "t": time_values, "row_index": np.arange(row_count, dtype=np.float64)}
    feature_descriptions = dict(FEATURE_DESCRIPTIONS)
    feature_descriptions["time"] = "Timeline in seconds for the imported table."
    feature_descriptions["t"] = "Alias for the imported table timeline in seconds."

    imported_feature_names: list[str] = []
    for alias_idx, (safe_name, original_name, arr, _kind) in enumerate(numeric_columns, start=1):
        features[safe_name] = arr
        imported_feature_names.append(safe_name)
        feature_descriptions[safe_name] = f"Imported numeric column '{original_name}'."
        alias = f"input_{alias_idx}"
        if alias not in features:
            features[alias] = arr
            feature_descriptions[alias] = f"Alias for imported column '{original_name}'."

    matrix_names = [name for name in imported_feature_names if name != time_name]
    if not matrix_names:
        matrix_names = imported_feature_names[:]
    matrix = np.column_stack([_normalize_feature(features[name], "zscore") for name in matrix_names])
    if matrix.ndim != 2 or matrix.shape[0] != row_count:
        raise ValueError("The imported table could not be converted into a stable numeric matrix.")

    features["column_mean"] = np.mean(matrix, axis=1)
    features["column_spread"] = np.std(matrix, axis=1)
    features["magnitude"] = np.linalg.norm(matrix, axis=1)
    diffs = np.diff(matrix, axis=0, prepend=matrix[:1])
    features["delta_magnitude"] = np.linalg.norm(diffs, axis=1)
    feature_descriptions["column_mean"] = "Mean value across imported numeric columns."
    feature_descriptions["column_spread"] = "Standard deviation across imported numeric columns."
    feature_descriptions["magnitude"] = "Row-wise magnitude across imported numeric columns."
    feature_descriptions["delta_magnitude"] = "Row-to-row change magnitude across imported numeric columns."

    pca_components = _pca_components(matrix, n_components=6)
    for idx in range(pca_components.shape[1]):
        key = f"pc{idx + 1}"
        features[key] = pca_components[:, idx]
        feature_descriptions[key] = f"Principal component {idx + 1} across imported numeric columns."

    display_matrix = np.column_stack([_robust_minmax(features[name]) for name in matrix_names]).T.astype(np.float64, copy=False)
    if display_matrix.shape[0] < 2:
        display_matrix = np.vstack([display_matrix, display_matrix])
    spectrogram_db = display_matrix

    if display_matrix.shape[0] >= 12:
        chroma = display_matrix[:12]
    else:
        chroma = np.zeros((12, display_matrix.shape[1]), dtype=np.float64)
        chroma[: display_matrix.shape[0], :] = display_matrix

    if display_matrix.shape[0] >= 13:
        mfcc = display_matrix[:13]
    else:
        mfcc = np.zeros((13, display_matrix.shape[1]), dtype=np.float64)
        mfcc[: display_matrix.shape[0], :] = display_matrix

    spectrogram_freqs_hz = np.arange(spectrogram_db.shape[0], dtype=np.float64)

    return AnalysisResult(
        source_path=str(source),
        audio_path="",
        sample_rate=1,
        duration=duration,
        hop_length=1,
        n_fft=max(1, spectrogram_db.shape[0]),
        times=time_values,
        features=features,
        spectrogram_db=spectrogram_db,
        spectrogram_freqs_hz=spectrogram_freqs_hz,
        chromagram=chroma,
        mfcc=mfcc,
        feature_descriptions=feature_descriptions,
        source_kind="table",
    )


def ensure_wav_audio(
    source_path: str | Path,
    sample_rate: int = 22050,
    temp_dir: str | Path | None = None,
) -> str:
    """Convert audio or video to a mono WAV file. Uses ffmpeg when available.

    For audio-only inputs, a failed ffmpeg conversion falls back to the original file.
    For video inputs, ffmpeg is required.
    """
    source = str(source_path)
    temp_root = Path(temp_dir) if temp_dir else Path(tempfile.mkdtemp(prefix="metriq_visualizer_"))
    temp_root.mkdir(parents=True, exist_ok=True)
    output = temp_root / "analysis_audio.wav"

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            source,
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-acodec",
            "pcm_s16le",
            str(output),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0 and output.exists():
            return str(output)
        if is_video_file(source):
            raise RuntimeError(
                "ffmpeg could not extract audio from the video. "
                f"stderr:\n{proc.stderr.decode(errors='ignore')}"
            )

    if is_video_file(source):
        raise RuntimeError(
            "ffmpeg is required to analyze video files on Linux. "
            "Install it first, then retry."
        )
    return source


def _clean_feature(values: np.ndarray | Iterable[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return arr
    finite_mask = np.isfinite(arr)
    if not finite_mask.any():
        return np.zeros_like(arr)
    fill = float(np.nanmedian(arr[finite_mask]))
    arr = np.nan_to_num(arr, nan=fill, posinf=fill, neginf=fill)
    return arr


def _normalize_feature(arr: np.ndarray, mode: str) -> np.ndarray:
    arr = _clean_feature(arr)
    if mode == "raw":
        return arr.copy()
    if arr.size == 0:
        return arr.copy()
    if mode == "minmax":
        low = float(np.min(arr))
        high = float(np.max(arr))
        span = high - low
        if span < 1e-12:
            return np.zeros_like(arr)
        return (arr - low) / span
    # default: zscore
    mean = float(np.mean(arr))
    std = float(np.std(arr))
    if std < 1e-12:
        return np.zeros_like(arr)
    return (arr - mean) / std


def _robust_minmax(arr: np.ndarray, low_pct: float = 2.0, high_pct: float = 98.0) -> np.ndarray:
    arr = _clean_feature(arr)
    if arr.size == 0:
        return arr.copy()
    low = float(np.percentile(arr, low_pct))
    high = float(np.percentile(arr, high_pct))
    span = high - low
    if span < 1e-12:
        return np.zeros_like(arr)
    clipped = np.clip(arr, low, high)
    return (clipped - low) / span


def _moving_average(arr: np.ndarray, window: float | int) -> np.ndarray:
    arr = _clean_feature(arr)
    win = max(1, int(round(float(window))))
    if win <= 1 or arr.size == 0:
        return arr.copy()
    kernel = np.ones(win, dtype=np.float64) / win
    return np.convolve(arr, kernel, mode="same")


ALLOWED_FUNCTIONS: dict[str, Callable[..., np.ndarray | float]] = {
    "abs": np.abs,
    "sqrt": np.sqrt,
    "log1p": np.log1p,
    "log": lambda x: np.log(np.maximum(_clean_feature(x), 1e-12)),
    "exp": np.exp,
    "clip": lambda x, lo, hi: np.clip(_clean_feature(x), lo, hi),
    "smooth": _moving_average,
    "mean": lambda *args: np.mean(np.vstack([_clean_feature(a) for a in args]), axis=0),
    "avg": lambda *args: np.mean(np.vstack([_clean_feature(a) for a in args]), axis=0),
    "sum": lambda *args: np.sum(np.vstack([_clean_feature(a) for a in args]), axis=0),
    "max": lambda *args: np.max(np.vstack([_clean_feature(a) for a in args]), axis=0),
    "min": lambda *args: np.min(np.vstack([_clean_feature(a) for a in args]), axis=0),
}

ALLOWED_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
}


class FormulaEvaluator(ast.NodeVisitor):
    def __init__(self, feature_map: dict[str, np.ndarray], length: int):
        self.feature_map = feature_map
        self.length = length

    def visit_Expression(self, node: ast.Expression):
        return self.visit(node.body)

    def visit_Name(self, node: ast.Name):
        if node.id in self.feature_map:
            return self.feature_map[node.id]
        if node.id in ALLOWED_CONSTANTS:
            return float(ALLOWED_CONSTANTS[node.id])
        raise ValueError(f"Unknown feature or constant: {node.id}")

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError("Only numeric constants are allowed in formulas.")

    def visit_UnaryOp(self, node: ast.UnaryOp):
        value = self.visit(node.operand)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
        raise ValueError("Unsupported unary operator in formula.")

    def visit_BinOp(self, node: ast.BinOp):
        left = self.visit(node.left)
        right = self.visit(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise ValueError("Unsupported binary operator in formula.")

    def visit_Call(self, node: ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only direct function names are allowed in formulas.")
        func_name = node.func.id
        if func_name not in ALLOWED_FUNCTIONS:
            raise ValueError(f"Unsupported function: {func_name}")
        args = [self.visit(arg) for arg in node.args]
        return ALLOWED_FUNCTIONS[func_name](*args)

    def generic_visit(self, node: ast.AST):
        raise ValueError(f"Unsupported syntax in formula: {type(node).__name__}")


def evaluate_formula(expression: str, feature_map: dict[str, np.ndarray], length: int) -> np.ndarray:
    expr = (expression or "").strip()
    if not expr:
        return np.zeros(length, dtype=np.float64)
    try:
        tree = ast.parse(expr, mode="eval")
        evaluator = FormulaEvaluator(feature_map, length)
        result = evaluator.visit(tree)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not evaluate formula '{expression}': {exc}") from exc

    if np.isscalar(result):
        return np.full(length, float(result), dtype=np.float64)
    arr = _clean_feature(np.asarray(result, dtype=np.float64))
    if arr.size != length:
        raise ValueError(
            f"Formula '{expression}' returned {arr.size} values but expected {length}."
        )
    return arr


def _stack_feature_matrix(feature_map: dict[str, np.ndarray], names: list[str]) -> np.ndarray:
    cols = []
    for name in names:
        if name in feature_map:
            cols.append(_normalize_feature(feature_map[name], "zscore"))
    if not cols:
        raise ValueError("No valid features were available for PCA.")
    return np.column_stack(cols)


def _compute_low_volume_active_mask(rms: np.ndarray, cutoff_db: float) -> np.ndarray:
    values = np.asarray(rms, dtype=np.float64).reshape(-1)
    if values.size == 0:
        return np.zeros(0, dtype=bool)
    cutoff_db = max(0.0, float(cutoff_db))
    if cutoff_db <= 0.0:
        return np.ones(values.size, dtype=bool)
    peak = float(np.max(values))
    if peak <= 1e-12:
        return np.zeros(values.size, dtype=bool)

    rms_db = 20.0 * np.log10(np.maximum(values, 1e-12) / peak)
    active = rms_db >= -cutoff_db

    if active.size >= 3:
        expanded = active.copy()
        expanded[1:] |= active[:-1]
        expanded[:-1] |= active[1:]
        active = expanded

    return np.asarray(active, dtype=bool)


def _pca_components(matrix: np.ndarray, n_components: int = 6) -> np.ndarray:
    x = np.asarray(matrix, dtype=np.float64)
    if x.ndim != 2:
        raise ValueError("PCA input matrix must be 2D.")
    if x.shape[0] < 2 or x.shape[1] < 1:
        return np.zeros((x.shape[0], 1), dtype=np.float64)
    x_centered = x - x.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(x_centered, full_matrices=False)
    comps = x_centered @ vt.T[:, : max(1, min(n_components, vt.shape[0]))]
    return comps


def analyze_media(
    source_path: str | Path,
    sample_rate: int = 22050,
    n_fft: int = 2048,
    hop_length: int = 256,
    temp_dir: str | Path | None = None,
) -> AnalysisResult:
    """Extract a dense audio feature set from an audio or video file."""
    audio_path = ensure_wav_audio(source_path, sample_rate=sample_rate, temp_dir=temp_dir)
    y, sr = librosa.load(audio_path, sr=sample_rate, mono=True)
    if y.size == 0:
        raise ValueError("The selected file did not contain a readable audio stream.")

    duration = float(len(y) / sr)
    stft = librosa.stft(y=y, n_fft=n_fft, hop_length=hop_length)
    magnitude = np.abs(stft)
    spectrogram_db = librosa.amplitude_to_db(magnitude + 1e-12, ref=np.max)
    spectrogram_freqs_hz = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    rms = librosa.feature.rms(S=magnitude, frame_length=n_fft)[0]
    zcr = librosa.feature.zero_crossing_rate(y, frame_length=n_fft, hop_length=hop_length)[0]
    centroid = librosa.feature.spectral_centroid(S=magnitude, sr=sr)[0]
    bandwidth = librosa.feature.spectral_bandwidth(S=magnitude, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(S=magnitude, sr=sr)[0]
    flatness = librosa.feature.spectral_flatness(S=magnitude)[0]
    contrast = librosa.feature.spectral_contrast(S=magnitude, sr=sr)
    chroma = librosa.feature.chroma_stft(S=magnitude, sr=sr)
    mfcc = librosa.feature.mfcc(S=librosa.power_to_db(magnitude**2 + 1e-12), sr=sr, n_mfcc=13)
    onset_strength = librosa.onset.onset_strength(S=librosa.power_to_db(magnitude**2 + 1e-12), sr=sr)

    # Spectral flux: frame-to-frame change in a column-normalized magnitude spectrum.
    mag_norm = magnitude / np.maximum(np.linalg.norm(magnitude, axis=0, keepdims=True), 1e-12)
    flux = np.sqrt(np.sum(np.diff(mag_norm, axis=1, prepend=mag_norm[:, :1]) ** 2, axis=0))

    dominant_bins = np.argmax(magnitude, axis=0)
    dominant_freq_hz = spectrogram_freqs_hz[dominant_bins]

    chroma_prob = chroma / np.maximum(np.sum(chroma, axis=0, keepdims=True), 1e-12)
    chroma_entropy = -np.sum(chroma_prob * np.log2(np.maximum(chroma_prob, 1e-12)), axis=0)
    chroma_mean = np.mean(chroma, axis=0)
    contrast_mean = np.mean(contrast, axis=0)

    # Fundamental frequency estimate. Keep it permissive enough for broad audio content.
    try:
        fmax = float(min(sr / 2 - 50, 12000))
        f0_hz = librosa.yin(
            y,
            fmin=50,
            fmax=max(200.0, fmax),
            sr=sr,
            frame_length=n_fft,
            hop_length=hop_length,
        )
    except Exception:  # noqa: BLE001
        f0_hz = np.zeros_like(rms)

    # Align everything to a single frame count.
    frame_lengths = [
        rms.size,
        zcr.size,
        centroid.size,
        bandwidth.size,
        rolloff.size,
        flatness.size,
        contrast.shape[1],
        chroma.shape[1],
        mfcc.shape[1],
        onset_strength.size,
        flux.size,
        dominant_freq_hz.size,
        chroma_entropy.size,
        chroma_mean.size,
        contrast_mean.size,
        f0_hz.size,
    ]
    n_frames = int(min(frame_lengths))
    if n_frames <= 1:
        raise ValueError("The file is too short for stable analysis.")

    def cut(arr: np.ndarray) -> np.ndarray:
        arr = np.asarray(arr)
        if arr.ndim == 1:
            return _clean_feature(arr[:n_frames])
        return np.asarray(arr[:, :n_frames], dtype=np.float64)

    rms = cut(rms)
    peak_rms = float(np.max(rms)) if rms.size > 0 else 0.0
    if peak_rms > 1e-12:
        rms_db = 20.0 * np.log10(np.maximum(rms, 1e-12) / peak_rms)
    else:
        rms_db = np.full_like(rms, -120.0, dtype=np.float64)
    zcr = cut(zcr)
    centroid = cut(centroid)
    bandwidth = cut(bandwidth)
    rolloff = cut(rolloff)
    flatness = cut(flatness)
    contrast = cut(contrast)
    chroma = cut(chroma)
    mfcc = cut(mfcc)
    onset_strength = cut(onset_strength)
    flux = cut(flux)
    dominant_freq_hz = cut(dominant_freq_hz)
    chroma_entropy = cut(chroma_entropy)
    chroma_mean = cut(chroma_mean)
    contrast_mean = cut(contrast_mean)
    f0_hz = cut(f0_hz)

    times = librosa.frames_to_time(np.arange(n_frames), sr=sr, hop_length=hop_length)

    features: dict[str, np.ndarray] = {
        "time": times,
        "t": times,
        "rms": rms,
        "rms_db": rms_db,
        "zcr": zcr,
        "spectral_centroid_hz": centroid,
        "spectral_bandwidth_hz": bandwidth,
        "spectral_rolloff_hz": rolloff,
        "spectral_flatness": flatness,
        "spectral_flux": flux,
        "spectral_contrast_mean": contrast_mean,
        "dominant_freq_hz": dominant_freq_hz,
        "onset_strength": onset_strength,
        "chroma_mean": chroma_mean,
        "chroma_entropy": chroma_entropy,
        "f0_hz": f0_hz,
    }

    feature_descriptions = dict(FEATURE_DESCRIPTIONS)

    for idx, name in enumerate(PITCH_CLASS_NAMES):
        key = f"chroma_{name}"
        features[key] = chroma[idx]
        feature_descriptions[key] = f"Chroma activity for pitch class {name}."

    for idx in range(mfcc.shape[0]):
        key = f"mfcc_{idx + 1}"
        features[key] = mfcc[idx]
        feature_descriptions[key] = f"MFCC coefficient {idx + 1}."

    for idx in range(contrast.shape[0]):
        key = f"contrast_{idx + 1}"
        features[key] = contrast[idx]
        feature_descriptions[key] = f"Spectral contrast band {idx + 1}."

    pca_source_names = [
        "rms",
        "zcr",
        "spectral_centroid_hz",
        "spectral_bandwidth_hz",
        "spectral_rolloff_hz",
        "spectral_flatness",
        "spectral_flux",
        "spectral_contrast_mean",
        "dominant_freq_hz",
        "onset_strength",
        "chroma_mean",
        "chroma_entropy",
        "f0_hz",
        "rms_db",
        *[f"mfcc_{i}" for i in range(1, 14)],
        *[f"chroma_{name}" for name in PITCH_CLASS_NAMES],
    ]
    pca_matrix = _stack_feature_matrix(features, pca_source_names)
    pca_components = _pca_components(pca_matrix, n_components=6)
    for idx in range(pca_components.shape[1]):
        key = f"pc{idx + 1}"
        features[key] = pca_components[:, idx]
        feature_descriptions[key] = f"Principal component {idx + 1} across the core feature stack."

    return AnalysisResult(
        source_path=str(source_path),
        audio_path=str(audio_path),
        sample_rate=sr,
        duration=duration,
        hop_length=hop_length,
        n_fft=n_fft,
        times=times,
        features=features,
        spectrogram_db=spectrogram_db[:, :n_frames],
        spectrogram_freqs_hz=spectrogram_freqs_hz,
        chromagram=chroma,
        mfcc=mfcc,
        feature_descriptions=feature_descriptions,
        source_kind="media",
    )


def make_feature_map(result: AnalysisResult, normalize_mode: str = "zscore") -> dict[str, np.ndarray]:
    fmap: dict[str, np.ndarray] = {}
    for name, values in result.features.items():
        if name in {"time", "t"}:
            fmap[name] = _clean_feature(values)
        else:
            fmap[name] = _normalize_feature(values, normalize_mode)
    return fmap


def build_geometry(
    result: AnalysisResult,
    x_expression: str,
    y_expression: str,
    z_expression: str,
    color_expression: str,
    size_expression: str,
    normalize_mode: str = "zscore",
    max_points: int = 2500,
    low_volume_cutoff_db: float = 0.0,
    colormap: str = "plasma",
) -> GeometryResult:
    length = result.times.size
    feature_map = make_feature_map(result, normalize_mode=normalize_mode)

    x_full = evaluate_formula(x_expression, feature_map, length)
    y_full = evaluate_formula(y_expression, feature_map, length)
    z_full = evaluate_formula(z_expression, feature_map, length)
    color_full = evaluate_formula(color_expression, feature_map, length)
    size_full = evaluate_formula(size_expression, feature_map, length)

    active_mask_full = _compute_low_volume_active_mask(result.features.get("rms", np.ones(length, dtype=np.float64)), low_volume_cutoff_db)

    if np.any(active_mask_full):
        plot_source_indices = np.flatnonzero(active_mask_full)
    else:
        plot_source_indices = np.arange(length, dtype=int)

    if max_points > 0 and plot_source_indices.size > max_points:
        keep = np.linspace(0, plot_source_indices.size - 1, num=max_points, dtype=int)
        plot_indices = plot_source_indices[keep]
    else:
        plot_indices = plot_source_indices

    x_plot = x_full[plot_indices]
    y_plot = y_full[plot_indices]
    z_plot = z_full[plot_indices]
    color_plot = color_full[plot_indices]
    display_sizes_full = 10.0 + 50.0 * _robust_minmax(np.abs(size_full))
    size_plot = display_sizes_full[plot_indices]
    times_plot = result.times[plot_indices]

    labels = {
        "x": x_expression.strip() or "0",
        "y": y_expression.strip() or "0",
        "z": z_expression.strip() or "0",
        "color": color_expression.strip() or "0",
        "size": size_expression.strip() or "0",
    }

    return GeometryResult(
        x_full=x_full,
        y_full=y_full,
        z_full=z_full,
        color_full=color_full,
        size_full=size_full,
        size_display_full=display_sizes_full,
        times_full=result.times,
        x_plot=x_plot,
        y_plot=y_plot,
        z_plot=z_plot,
        color_plot=color_plot,
        size_plot=size_plot,
        times_plot=times_plot,
        plot_indices=plot_indices,
        labels=labels,
        normalize_mode=normalize_mode,
        colormap=colormap,
        active_mask_full=active_mask_full,
        low_volume_cutoff_db=float(low_volume_cutoff_db),
    )


def nearest_time_index(times: np.ndarray, target_time: float) -> int:
    if times.size == 0:
        return 0
    idx = int(np.searchsorted(times, target_time))
    if idx <= 0:
        return 0
    if idx >= times.size:
        return int(times.size - 1)
    prev_idx = idx - 1
    return idx if abs(times[idx] - target_time) < abs(times[prev_idx] - target_time) else prev_idx


def format_feature_reference(result: AnalysisResult) -> str:
    ordered_names = sorted(result.features.keys())
    lines = [
        "Available features (use them directly in formulas):",
        "",
    ]
    for name in ordered_names:
        desc = result.feature_descriptions.get(name, "")
        lines.append(f"- {name}: {desc}")

    if getattr(result, "source_kind", "media") == "table":
        lines.extend(
            [
                "",
                "Formula examples for imported tables:",
                "- pc1",
                "- input_1",
                "- mean(input_1, input_2)",
                "- smooth(column_mean, 5)",
                "- delta_magnitude",
                "- log1p(abs(magnitude))",
                "",
                "Supported functions: abs, sqrt, log, log1p, exp, clip, smooth, mean, avg, sum, max, min",
                "Normalization mode applies to every feature except time/t.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "",
            "Formula examples:",
            "- pc1",
            "- 0.7*mfcc_1 + 0.3*chroma_mean",
            "- mean(chroma_C, chroma_G, chroma_E)",
            "- smooth(spectral_flux, 5)",
            "- log1p(abs(f0_hz))",
            "- 0.5*rms + 0.5*onset_strength",
            "",
            "Supported functions: abs, sqrt, log, log1p, exp, clip, smooth, mean, avg, sum, max, min",
            "Normalization mode applies to every feature except time/t.",
        ]
    )
    return "\n".join(lines)
