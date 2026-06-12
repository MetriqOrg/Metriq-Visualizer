# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


EXPORT_ENGINE_AUTO_LABEL = "Auto (GPU encoder → CPU)"
EXPORT_ENGINE_GPU_LABEL = "GPU encoder only"
EXPORT_ENGINE_CPU_LABEL = "CPU FFmpeg (libx264)"
EXPORT_ENGINE_LEGACY_LABEL = "Legacy OpenCV writer"
EXPORT_ENGINE_CHOICES = (
    EXPORT_ENGINE_AUTO_LABEL,
    EXPORT_ENGINE_GPU_LABEL,
    EXPORT_ENGINE_CPU_LABEL,
    EXPORT_ENGINE_LEGACY_LABEL,
)

EXPORT_QUALITY_FAST_LABEL = "Fast"
EXPORT_QUALITY_BALANCED_LABEL = "Balanced"
EXPORT_QUALITY_QUALITY_LABEL = "Quality"
EXPORT_QUALITY_CHOICES = (
    EXPORT_QUALITY_FAST_LABEL,
    EXPORT_QUALITY_BALANCED_LABEL,
    EXPORT_QUALITY_QUALITY_LABEL,
)


@dataclass(frozen=True)
class ExportEncoderCandidate:
    name: str
    label: str
    hardware: bool


def normalize_export_engine(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text or text.startswith("auto"):
        return "auto"
    if "legacy" in text or "opencv" in text:
        return "legacy"
    if "cpu" in text or "libx264" in text or "x264" in text:
        return "cpu"
    if "gpu" in text or "hardware" in text:
        return "gpu"
    return "auto"


def normalize_export_quality(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("fast") or text in {"speed", "draft"}:
        return "fast"
    if text.startswith("quality") or text in {"high", "slow"}:
        return "quality"
    return "balanced"


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


@lru_cache(maxsize=8)
def available_h264_encoders(ffmpeg_path: str) -> frozenset[str]:
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
    except Exception:
        return frozenset()
    if proc.returncode != 0:
        return frozenset()
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("h264"):
            names.add(parts[1])
        elif len(parts) >= 2 and parts[1] in {"libx264", "libx264rgb"}:
            names.add(parts[1])
    return frozenset(names)


def _hardware_encoder_order() -> tuple[ExportEncoderCandidate, ...]:
    candidates = [
        ExportEncoderCandidate("h264_nvenc", "NVIDIA NVENC", True),
        ExportEncoderCandidate("h264_qsv", "Intel Quick Sync", True),
        ExportEncoderCandidate("h264_vaapi", "Linux VAAPI", True),
        ExportEncoderCandidate("h264_amf", "AMD AMF", True),
        ExportEncoderCandidate("h264_videotoolbox", "Apple VideoToolbox", True),
    ]
    if sys.platform == "darwin":
        priority = {"h264_videotoolbox": 0, "h264_nvenc": 1, "h264_qsv": 2, "h264_vaapi": 3, "h264_amf": 4}
    elif os.name == "nt":
        priority = {"h264_nvenc": 0, "h264_qsv": 1, "h264_amf": 2, "h264_vaapi": 3, "h264_videotoolbox": 4}
    else:
        priority = {"h264_nvenc": 0, "h264_qsv": 1, "h264_vaapi": 2, "h264_amf": 3, "h264_videotoolbox": 4}
    return tuple(sorted(candidates, key=lambda candidate: priority.get(candidate.name, 99)))


def encoder_candidates_for_engine(engine: str | None, ffmpeg_path: str | None = None) -> list[ExportEncoderCandidate]:
    ffmpeg = ffmpeg_path or find_ffmpeg()
    if not ffmpeg:
        return []
    available = available_h264_encoders(ffmpeg)
    normalized = normalize_export_engine(engine)
    hardware = [candidate for candidate in _hardware_encoder_order() if candidate.name in available]
    cpu = [ExportEncoderCandidate("libx264", "CPU libx264", False)] if "libx264" in available else []
    if normalized == "gpu":
        return hardware
    if normalized == "cpu":
        return cpu
    if normalized == "legacy":
        return []
    return hardware + cpu


def sanitize_video_size(width: int | float, height: int | float) -> tuple[int, int]:
    # H.264 + yuv420p requires even dimensions. Keep the requested size unless it is odd.
    w = max(2, int(round(float(width))))
    h = max(2, int(round(float(height))))
    if w % 2:
        w -= 1
    if h % 2:
        h -= 1
    return max(2, w), max(2, h)


def bitrate_for_export(width: int, height: int, fps: int, quality: str | None, override_mbps: float | None = None) -> float:
    try:
        override = float(override_mbps or 0.0)
    except Exception:
        override = 0.0
    if override > 0.0:
        return max(1.0, min(240.0, override))
    q = normalize_export_quality(quality)
    base = {"fast": 8.0, "balanced": 12.0, "quality": 20.0}.get(q, 12.0)
    scale = (max(1, int(width)) * max(1, int(height)) * max(1, int(fps))) / float(1920 * 1080 * 30)
    return max(3.0, min(240.0, base * scale))


def find_vaapi_device() -> str | None:
    for index in range(128, 136):
        candidate = Path(f"/dev/dri/renderD{index}")
        if candidate.exists():
            return str(candidate)
    return None


def encoder_args(encoder_name: str, quality: str | None, width: int, height: int, fps: int, bitrate_mbps: float | None = None) -> list[str]:
    q = normalize_export_quality(quality)
    if encoder_name == "libx264":
        preset = {"fast": "veryfast", "balanced": "medium", "quality": "slow"}.get(q, "medium")
        crf = {"fast": "23", "balanced": "21", "quality": "18"}.get(q, "21")
        return ["-preset", preset, "-crf", crf]
    bitrate = bitrate_for_export(width, height, fps, q, bitrate_mbps)
    return ["-b:v", f"{bitrate:.1f}M"]


@lru_cache(maxsize=64)
def probe_encoder(ffmpeg_path: str, encoder_name: str, quality: str = "balanced") -> bool:
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="metriq_encoder_probe_", suffix=".mp4", delete=False) as handle:
            tmp_path = handle.name
        cmd = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if encoder_name == "h264_vaapi":
            device = find_vaapi_device()
            if not device:
                return False
            cmd.extend(["-vaapi_device", device])
        cmd.extend([
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=1:d=0.1",
            "-frames:v",
            "1",
        ])
        if encoder_name == "h264_vaapi":
            cmd.extend(["-vf", "format=nv12,hwupload"])
        cmd.extend([
            "-c:v",
            encoder_name,
            *encoder_args(encoder_name, quality, 64, 64, 1, 2.0),
        ])
        if encoder_name != "h264_vaapi":
            cmd.extend(["-pix_fmt", "yuv420p"])
        cmd.extend(["-an", tmp_path])
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12)
        return proc.returncode == 0 and tmp_path is not None and Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 0
    except Exception:
        return False
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


def build_ffmpeg_rawvideo_command(
    *,
    ffmpeg_path: str,
    encoder: ExportEncoderCandidate,
    output_path: str,
    width: int,
    height: int,
    fps: int,
    quality: str | None,
    bitrate_mbps: float | None = None,
    audio_path: str | None = None,
    audio_start_time: float = 0.0,
    audio_duration: float | None = None,
) -> list[str]:
    cmd = [
        ffmpeg_path,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if encoder.name == "h264_vaapi":
        device = find_vaapi_device()
        if not device:
            raise RuntimeError("Linux VAAPI encoder was selected, but no /dev/dri/renderD* device was found.")
        cmd.extend(["-vaapi_device", device])
    cmd.extend([
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{int(width)}x{int(height)}",
        "-r",
        str(max(1, int(fps))),
        "-i",
        "pipe:0",
    ])

    has_audio = bool(audio_path and Path(audio_path).exists())
    if has_audio:
        if float(audio_start_time or 0.0) > 1e-6:
            cmd.extend(["-ss", f"{float(audio_start_time):0.6f}"])
        if audio_duration is not None and float(audio_duration) > 1e-6:
            cmd.extend(["-t", f"{float(audio_duration):0.6f}"])
        cmd.extend(["-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0?"])
    else:
        cmd.extend(["-map", "0:v:0"])

    if encoder.name == "h264_vaapi":
        cmd.extend(["-vf", "format=nv12,hwupload"])
    cmd.extend([
        "-c:v",
        encoder.name,
        *encoder_args(encoder.name, quality, width, height, fps, bitrate_mbps),
    ])
    if encoder.name != "h264_vaapi":
        cmd.extend(["-pix_fmt", "yuv420p"])
    if has_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])
    else:
        cmd.extend(["-an"])
    cmd.extend(["-movflags", "+faststart", str(output_path)])
    return cmd
