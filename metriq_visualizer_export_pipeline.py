# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Streaming export pipeline for Metriq Visualizer.

The existing renderer remains the source of truth for visual composition. This
module adds container/codec profiles, image-sequence export, direct frame
streaming to FFmpeg, cancellation, progress reporting, and portable profile
serialization. It deliberately does not introduce cloud services or paid
features.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from metriq_visualizer_atomic import atomic_destination, atomic_write_text

EXPORT_PROFILE_SCHEMA = "metriq.export-profile"
EXPORT_PROFILE_VERSION = 4

ProgressCallback = Callable[[float, str], None]

ENCODER_MODES: tuple[tuple[str, str], ...] = (
    ("auto", "Automatic hardware → software"),
    ("software", "Software / maximum compatibility"),
    ("nvidia", "NVIDIA NVENC"),
    ("intel", "Intel Quick Sync"),
    ("apple", "Apple VideoToolbox"),
    ("amd", "AMD AMF"),
)
ENCODER_MODE_KEYS = {key for key, _label in ENCODER_MODES}



class ExportCancelled(RuntimeError):
    """Raised when a running export is cancelled by the user."""


class ExportConfigurationError(ValueError):
    """Raised when an export profile cannot produce a valid output."""


@dataclass(frozen=True)
class ResolutionPreset:
    name: str
    width: int
    height: int
    description: str


RESOLUTION_PRESETS: tuple[ResolutionPreset, ...] = (
    ResolutionPreset("HD 720p", 1280, 720, "16:9 landscape"),
    ResolutionPreset("Full HD 1080p", 1920, 1080, "16:9 landscape"),
    ResolutionPreset("QHD 1440p", 2560, 1440, "16:9 landscape"),
    ResolutionPreset("Ultra HD 4K", 3840, 2160, "16:9 landscape"),
    ResolutionPreset("Square 1080", 1080, 1080, "1:1 social"),
    ResolutionPreset("Portrait 4:5", 1080, 1350, "Feed portrait"),
    ResolutionPreset("Vertical 1080", 1080, 1920, "9:16 short-form"),
    ResolutionPreset("Vertical 4K", 2160, 3840, "9:16 high-resolution"),
)


@dataclass(frozen=True)
class FormatDefinition:
    key: str
    label: str
    extension: str
    kind: str
    supports_audio: bool
    description: str


FORMAT_DEFINITIONS: tuple[FormatDefinition, ...] = (
    FormatDefinition("mp4_h264", "MP4 · H.264", ".mp4", "video", True, "Broadest playback compatibility"),
    FormatDefinition("mp4_h265", "MP4 · H.265/HEVC", ".mp4", "video", True, "Smaller files; newer playback stack"),
    FormatDefinition("webm_vp9", "WebM · VP9", ".webm", "video", True, "Open web delivery"),
    FormatDefinition("mov_prores422", "MOV · ProRes 422 HQ", ".mov", "video", True, "High-quality editing master"),
    FormatDefinition("mov_prores4444", "MOV · ProRes 4444", ".mov", "video", True, "High-fidelity 4:4:4 editing master"),
    FormatDefinition("gif", "Animated GIF", ".gif", "video", False, "Compact loop; no audio"),
    FormatDefinition("png_sequence", "PNG image sequence", "", "sequence", False, "Lossless frames in a folder"),
    FormatDefinition("jpeg_sequence", "JPEG image sequence", "", "sequence", False, "Smaller frames in a folder"),
)
FORMAT_BY_KEY = {item.key: item for item in FORMAT_DEFINITIONS}


@dataclass
class ExportProfile:
    """Output-specific settings, independent of the renderer's visual options."""

    name: str = "Full HD H.264"
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    format_key: str = "mp4_h264"
    quality: int = 18
    include_audio: bool = True
    audio_bitrate_kbps: int = 192
    jpeg_quality: int = 92
    start_time: float = 0.0
    end_time: float | None = None
    title: str = "Metriq Visualizer"
    layout: dict[str, Any] | None = None
    encoder_mode: str = "auto"
    show_project_title: bool = True
    project_subtitle: str = ""
    show_watermark: bool = False
    watermark_text: str = ""
    show_axes: bool = True
    show_axis_labels: bool = True
    show_colorbar: bool = False
    show_scene_hud: bool = True
    show_timecode: bool = False

    def validate(self) -> ExportProfile:
        self.width = int(min(7680, max(160, self.width)))
        self.height = int(min(7680, max(160, self.height)))
        self.fps = float(min(240.0, max(1.0, self.fps)))
        if self.format_key not in FORMAT_BY_KEY:
            raise ExportConfigurationError(f"Unknown export format: {self.format_key}")
        # Chroma-subsampled delivery codecs require even dimensions. Image
        # sequences and GIF retain the exact custom dimensions requested.
        if self.format_key in {"mp4_h264", "mp4_h265", "webm_vp9", "mov_prores422"}:
            if self.width % 2:
                self.width += 1
            if self.height % 2:
                self.height += 1
        self.quality = int(min(51, max(0, self.quality)))
        self.audio_bitrate_kbps = int(min(512, max(64, self.audio_bitrate_kbps)))
        self.jpeg_quality = int(min(100, max(20, self.jpeg_quality)))
        self.start_time = float(max(0.0, self.start_time))
        if self.end_time is not None:
            self.end_time = float(max(self.start_time, self.end_time))
        definition = FORMAT_BY_KEY[self.format_key]
        self.include_audio = bool(self.include_audio and definition.supports_audio)
        self.name = str(self.name or definition.label).strip()
        self.title = str(self.title or "Metriq Visualizer").strip()
        self.project_subtitle = str(self.project_subtitle or "").strip()
        self.watermark_text = str(self.watermark_text or "").strip()
        self.show_project_title = bool(self.show_project_title)
        self.show_watermark = bool(self.show_watermark)
        self.show_axes = bool(self.show_axes)
        self.show_axis_labels = bool(self.show_axis_labels)
        self.show_colorbar = bool(self.show_colorbar)
        self.show_scene_hud = bool(self.show_scene_hud)
        self.show_timecode = bool(self.show_timecode)
        self.encoder_mode = str(self.encoder_mode or "auto").lower()
        if self.encoder_mode not in ENCODER_MODE_KEYS:
            self.encoder_mode = "auto"
        return self

    @property
    def definition(self) -> FormatDefinition:
        return FORMAT_BY_KEY[self.format_key]

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": EXPORT_PROFILE_SCHEMA,
            "schema_version": EXPORT_PROFILE_VERSION,
            "profile": asdict(self),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ExportProfile:
        if str(payload.get("schema", "")) != EXPORT_PROFILE_SCHEMA:
            raise ExportConfigurationError("This is not a Metriq export profile.")
        version = int(payload.get("schema_version", 0))
        if version > EXPORT_PROFILE_VERSION:
            raise ExportConfigurationError(
                f"Export profile version {version} is newer than this application supports."
            )
        data = payload.get("profile")
        if not isinstance(data, Mapping):
            raise ExportConfigurationError("Export profile is missing its profile object.")
        known = {field.name for field in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        profile = cls(**{key: value for key, value in data.items() if key in known})
        return profile.validate()


def save_export_profile(path: str | Path, profile: ExportProfile) -> Path:
    output = Path(path)
    if output.suffix.lower() != ".mvexport":
        output = output.with_suffix(".mvexport")
    atomic_write_text(
        output,
        json.dumps(profile.to_payload(), indent=2, sort_keys=True) + "\n",
    )
    return output


def load_export_profile(path: str | Path) -> ExportProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ExportConfigurationError("Export profile must contain a JSON object.")
    return ExportProfile.from_payload(payload)


def ffmpeg_executable() -> str | None:
    configured = os.environ.get("METRIQ_FFMPEG", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def output_path_for_profile(path: str | Path, profile: ExportProfile) -> Path:
    output = Path(path).expanduser()
    definition = profile.definition
    if definition.kind == "sequence":
        return output
    if output.suffix.lower() != definition.extension:
        output = output.with_suffix(definition.extension)
    return output


def _software_encoder(profile: ExportProfile) -> str | None:
    if profile.format_key == "mp4_h264":
        return "libx264"
    if profile.format_key == "mp4_h265":
        return "libx265"
    return None


def _video_codec_arguments(profile: ExportProfile, encoder: str | None = None) -> list[str]:
    quality = str(profile.quality)
    chosen = encoder or _software_encoder(profile)
    if profile.format_key in {"mp4_h264", "mp4_h265"}:
        if not chosen:
            raise ExportConfigurationError(f"No encoder is available for {profile.format_key}.")
        if chosen in {"libx264", "libx265"}:
            arguments = ["-c:v", chosen, "-preset", "medium", "-crf", quality]
        elif chosen.endswith("_nvenc"):
            arguments = ["-c:v", chosen, "-preset", "p5", "-cq", quality, "-b:v", "0"]
        elif chosen.endswith("_qsv"):
            arguments = ["-c:v", chosen, "-preset", "medium", "-global_quality", quality]
        elif chosen.endswith("_videotoolbox"):
            # VideoToolbox uses a quality scale rather than CRF.  Keep a bounded inverse mapping.
            arguments = ["-c:v", chosen, "-q:v", str(max(1, min(100, 100 - profile.quality)))]
        elif chosen.endswith("_amf"):
            arguments = ["-c:v", chosen, "-quality", "balanced", "-qp_i", quality, "-qp_p", quality]
        else:
            arguments = ["-c:v", chosen]
        if profile.format_key == "mp4_h265":
            arguments.extend(["-tag:v", "hvc1"])
        arguments.extend(["-pix_fmt", "yuv420p", "-movflags", "+faststart"])
        return arguments
    if profile.format_key == "webm_vp9":
        return [
            "-c:v", "libvpx-vp9", "-crf", quality, "-b:v", "0",
            "-row-mt", "1", "-pix_fmt", "yuv420p",
        ]
    if profile.format_key == "mov_prores422":
        return ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    if profile.format_key == "mov_prores4444":
        return ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le"]
    if profile.format_key == "gif":
        return [
            "-filter_complex",
            "[0:v]split[s0][s1];[s0]palettegen=max_colors=256[p];"
            "[s1][p]paletteuse=dither=sierra2_4a[outv]",
            "-map", "[outv]",
            "-loop", "0",
        ]
    raise ExportConfigurationError(f"No video encoder is defined for {profile.format_key}.")


@lru_cache(maxsize=8)
def available_ffmpeg_encoders(executable: str) -> frozenset[str]:
    try:
        process = subprocess.run(
            [executable, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=12,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    encoders: set[str] = set()
    for line in process.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) >= 1 and parts[0][0] in {"V", "."}:
            encoders.add(parts[1])
    return frozenset(encoders)


@lru_cache(maxsize=64)
def _probe_encoder(executable: str, encoder: str) -> bool:
    command = [
        executable, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=64x64:r=1:d=1",
        "-frames:v", "1", "-an", "-c:v", encoder, "-pix_fmt", "yuv420p",
        "-f", "null", "-",
    ]
    try:
        process = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return process.returncode == 0


def _encoder_candidates(profile: ExportProfile) -> list[str]:
    if profile.format_key == "mp4_h264":
        hardware = {
            "nvidia": "h264_nvenc", "intel": "h264_qsv", "apple": "h264_videotoolbox", "amd": "h264_amf"
        }
        software = "libx264"
    elif profile.format_key == "mp4_h265":
        hardware = {
            "nvidia": "hevc_nvenc", "intel": "hevc_qsv", "apple": "hevc_videotoolbox", "amd": "hevc_amf"
        }
        software = "libx265"
    else:
        return []
    mode = profile.encoder_mode
    if mode == "software":
        return [software]
    if mode in hardware:
        return [hardware[mode], software]
    # Prefer platform-native hardware, then broadly available devices, then software.
    if os.name == "nt":
        ordered = [hardware["nvidia"], hardware["intel"], hardware["amd"], hardware["apple"]]
    elif sys.platform == "darwin":
        ordered = [hardware["apple"], hardware["nvidia"], hardware["intel"], hardware["amd"]]
    else:
        ordered = [hardware["nvidia"], hardware["intel"], hardware["amd"], hardware["apple"]]
    return ordered + [software]


def select_video_encoder(profile: ExportProfile, executable: str) -> str | None:
    if profile.format_key not in {"mp4_h264", "mp4_h265"}:
        return None
    advertised = available_ffmpeg_encoders(executable)
    candidates = _encoder_candidates(profile)
    for encoder in candidates:
        if advertised and encoder not in advertised:
            continue
        if encoder in {"libx264", "libx265"} or _probe_encoder(executable, encoder):
            return encoder
    software = _software_encoder(profile)
    if software and (not advertised or software in advertised):
        return software
    raise RuntimeError(f"FFmpeg does not provide a usable encoder for {profile.definition.label}.")


def _audio_codec_arguments(profile: ExportProfile) -> list[str]:
    if profile.format_key in {"mov_prores422", "mov_prores4444"}:
        return ["-c:a", "pcm_s16le"]
    if profile.format_key == "webm_vp9":
        return ["-c:a", "libopus", "-b:a", f"{profile.audio_bitrate_kbps}k"]
    return ["-c:a", "aac", "-b:a", f"{profile.audio_bitrate_kbps}k"]


def build_ffmpeg_command(
    executable: str,
    output_path: str | Path,
    profile: ExportProfile,
    *,
    audio_path: str | Path | None,
    duration: float,
    video_encoder: str | None = None,
) -> list[str]:
    """Build the exact streaming command. Kept pure for tests and inspection."""

    profile = deepcopy(profile).validate()
    if profile.definition.kind != "video":
        raise ExportConfigurationError("FFmpeg command requested for an image-sequence profile.")
    output = output_path_for_profile(output_path, profile)
    command = [
        executable,
        "-hide_banner",
        "-loglevel", "warning",
        "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgba",
        "-video_size", f"{profile.width}x{profile.height}",
        "-framerate", f"{profile.fps:g}",
        "-i", "pipe:0",
    ]
    use_audio = bool(profile.include_audio and audio_path and profile.definition.supports_audio)
    if use_audio:
        command.extend(["-ss", f"{profile.start_time:.6f}"])
        if duration > 0:
            command.extend(["-t", f"{duration:.6f}"])
        command.extend(["-i", str(audio_path)])
    # GIF's filter graph maps its own named output; other formats map raw video.
    if profile.format_key != "gif":
        command.extend(["-map", "0:v:0"])
    if use_audio:
        command.extend(["-map", "1:a:0?"])
    command.extend(_video_codec_arguments(profile, video_encoder))
    if use_audio:
        command.extend(_audio_codec_arguments(profile))
        command.append("-shortest")
    command.extend(["-metadata", f"title={profile.title}", str(output)])
    return command


def _resolve_time_range(profile: ExportProfile, analysis: Any) -> tuple[float, float, int]:
    duration = float(max(0.0, getattr(analysis, "duration", 0.0)))
    start = min(duration, max(0.0, float(profile.start_time))) if duration > 0 else 0.0
    requested_end = duration if profile.end_time is None else float(profile.end_time)
    end = min(duration, max(start, requested_end)) if duration > 0 else max(start, requested_end)
    # A table may have a zero duration but still deserves a single frame.
    frame_count = 1 if end <= start else max(1, int(math.ceil((end - start) * profile.fps)))
    return start, end, frame_count


def _iter_frames(
    session: Any,
    profile: ExportProfile,
    layout: Any,
    frame_count: int,
    start: float,
    cancel_event: threading.Event,
    progress_callback: ProgressCallback | None,
) -> Iterable[np.ndarray]:
    for frame_index in range(frame_count):
        if cancel_event.is_set():
            raise ExportCancelled("Export cancelled.")
        current_time = start + (frame_index / profile.fps)
        frame = session.render_frame(
            current_time=current_time,
            layout=layout,
            output_size=(profile.width, profile.height),
        )
        rgba = np.ascontiguousarray(frame, dtype=np.uint8)
        if rgba.shape != (profile.height, profile.width, 4):
            raise RuntimeError(
                f"Renderer returned {rgba.shape}; expected "
                f"({profile.height}, {profile.width}, 4)."
            )
        if progress_callback:
            progress_callback(frame_index / max(1, frame_count), f"Rendering frame {frame_index + 1:,}/{frame_count:,}")
        yield rgba


def _write_sequence(
    output_dir: Path,
    profile: ExportProfile,
    frames: Iterable[np.ndarray],
    frame_count: int,
    progress_callback: ProgressCallback | None,
) -> Path:
    output_dir, created_directory = _prepare_sequence_directory(output_dir)
    extension = ".png" if profile.format_key == "png_sequence" else ".jpg"
    written: list[Path] = []
    manifest_path = output_dir / "sequence.json"
    written_count = 0
    try:
        for index, frame in enumerate(frames, start=1):
            if index > frame_count:
                raise RuntimeError(f"Renderer produced more than the expected {frame_count:,} frames.")
            image = Image.fromarray(frame, mode="RGBA")
            target = output_dir / f"frame_{index:06d}{extension}"
            # Exclusive creation guarantees that an existing creator frame is
            # never overwritten, including a late race after directory setup.
            with target.open("xb") as handle:
                # Track the file immediately after exclusive creation so a
                # partially written frame is removed if Pillow fails midway.
                written.append(target)
                if extension == ".png":
                    image.save(handle, format="PNG", optimize=False, compress_level=3)
                else:
                    image.convert("RGB").save(
                        handle,
                        format="JPEG",
                        quality=profile.jpeg_quality,
                        subsampling=0,
                        optimize=False,
                    )
            written_count = index
            if progress_callback:
                progress_callback(index / max(1, frame_count), f"Saved frame {index:,}/{frame_count:,}")
        if written_count != frame_count:
            raise RuntimeError(f"Renderer produced {written_count:,} frames; expected {frame_count:,}.")
        manifest = {
            "schema": "metriq.image-sequence",
            "schema_version": 1,
            "width": profile.width,
            "height": profile.height,
            "fps": profile.fps,
            "frame_count": written_count,
            "start_time": profile.start_time,
            "format": profile.format_key,
            "pattern": f"frame_%06d{extension}",
        }
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
            handle.write("\n")
        return output_dir
    except BaseException:
        with suppress(OSError):
            manifest_path.unlink(missing_ok=True)
        for target in written:
            with suppress(OSError):
                target.unlink(missing_ok=True)
        if created_directory:
            with suppress(OSError):
                output_dir.rmdir()
        raise


def _prepare_sequence_directory(requested: Path) -> tuple[Path, bool]:
    """Return an empty output directory without deleting or merging user files."""

    output = Path(requested).expanduser()
    if output.exists() and not output.is_dir():
        raise ExportConfigurationError(f"Image-sequence output is not a directory: {output}")
    if not output.exists():
        output.mkdir(parents=True, exist_ok=False)
        return output, True
    try:
        next(output.iterdir())
    except StopIteration:
        return output, False

    base = output / "metriq_frames"
    candidate = base
    suffix = 2
    while True:
        try:
            candidate.mkdir(parents=False, exist_ok=False)
            return candidate, True
        except FileExistsError:
            candidate = output / f"{base.name}_{suffix}"
            suffix += 1


def _drain_stderr(pipe: Any, tail: deque[str]) -> None:
    try:
        for raw in iter(pipe.readline, b""):
            tail.append(raw.decode("utf-8", errors="replace").rstrip())
    finally:
        with suppress(Exception):
            pipe.close()


def _write_video(
    output_path: Path,
    profile: ExportProfile,
    frames: Iterable[np.ndarray],
    frame_count: int,
    *,
    audio_path: str | Path | None,
    duration: float,
    cancel_event: threading.Event,
    progress_callback: ProgressCallback | None,
) -> Path:
    executable = ffmpeg_executable()
    if not executable:
        raise RuntimeError(
            "FFmpeg was not found. Install FFmpeg or set METRIQ_FFMPEG to its executable. "
            "PNG and JPEG sequence export remain available without FFmpeg."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_encoder = select_video_encoder(profile, executable)
    # Encode beside the final file and replace it only after FFmpeg exits
    # successfully. A failed or cancelled export can therefore never delete
    # or truncate a creator's existing output.
    with atomic_destination(output_path, suffix=output_path.suffix) as temporary_output:
        command = build_ffmpeg_command(
            executable,
            temporary_output,
            profile,
            audio_path=audio_path,
            duration=duration,
            video_encoder=video_encoder,
        )
        stderr_tail: deque[str] = deque(maxlen=80)
        process = subprocess.Popen(  # noqa: S603 - command is constructed internally
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stderr is not None
        reader = threading.Thread(target=_drain_stderr, args=(process.stderr, stderr_tail), daemon=True)
        reader.start()
        written_count = 0
        try:
            for index, frame in enumerate(frames, start=1):
                if index > frame_count:
                    raise RuntimeError(f"Renderer produced more than the expected {frame_count:,} frames.")
                if cancel_event.is_set():
                    raise ExportCancelled("Export cancelled.")
                rgba = np.ascontiguousarray(frame, dtype=np.uint8)
                expected_shape = (profile.height, profile.width, 4)
                if rgba.shape != expected_shape:
                    raise RuntimeError(f"Renderer returned {rgba.shape}; expected {expected_shape}.")
                try:
                    process.stdin.write(rgba.tobytes(order="C"))
                except BrokenPipeError as exc:
                    raise RuntimeError("FFmpeg stopped while receiving frames.") from exc
                written_count = index
                if progress_callback:
                    progress_callback(index / max(1, frame_count), f"Encoding frame {index:,}/{frame_count:,}")
            if written_count != frame_count:
                raise RuntimeError(f"Renderer produced {written_count:,} frames; expected {frame_count:,}.")
            process.stdin.close()
            return_code = process.wait()
            reader.join(timeout=2.0)
            if return_code != 0:
                details = "\n".join(stderr_tail).strip()
                raise RuntimeError(f"FFmpeg exited with code {return_code}.\n{details}".strip())
            if not temporary_output.is_file() or temporary_output.stat().st_size <= 0:
                raise RuntimeError("FFmpeg reported success but did not produce a valid output file.")
        except BaseException:
            with suppress(Exception):
                process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=2.0)
            reader.join(timeout=1.0)
            raise
    return output_path


def export_visualization(
    analysis: Any,
    geometry: Any,
    render_options: Any,
    profile: ExportProfile,
    output_path: str | Path,
    *,
    cancel_event: threading.Event | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """Render and export without staging thousands of temporary frame files."""

    profile = deepcopy(profile).validate()
    cancel = cancel_event or threading.Event()
    output = output_path_for_profile(output_path, profile)
    start, end, frame_count = _resolve_time_range(profile, analysis)
    profile.start_time = start
    profile.end_time = end

    from metriq_visualizer_layout import ExportLayoutSpec
    from metriq_visualizer_render import ExportPreviewSession

    layout = (
        ExportLayoutSpec.from_dict(profile.layout)
        if isinstance(profile.layout, Mapping)
        else deepcopy(render_options.layout)
    )
    layout.clamp()
    options = deepcopy(render_options)
    options.output_path = str(output)
    options.width = profile.width
    options.height = profile.height
    options.fps = int(round(profile.fps))
    options.start_time = start
    options.end_time = end
    options.layout = layout
    options.include_preview = bool(layout.preview.enabled)
    options.include_panels = bool(
        layout.spectrogram.enabled
        or layout.chromagram.enabled
        or layout.mfcc.enabled
        or layout.traces.enabled
    )
    options.title = profile.title
    options.show_project_title = profile.show_project_title
    options.project_title = profile.title
    options.project_subtitle = profile.project_subtitle
    options.show_watermark = profile.show_watermark
    options.watermark_text = profile.watermark_text
    options.show_axes = profile.show_axes
    options.show_axis_labels = profile.show_axis_labels
    options.show_colorbar = profile.show_colorbar

    if progress_callback:
        progress_callback(0.0, "Preparing renderer")
    session = ExportPreviewSession(analysis, geometry, options)
    try:
        frames = _iter_frames(session, profile, layout, frame_count, start, cancel, progress_callback)
        if profile.definition.kind == "sequence":
            result = _write_sequence(output, profile, frames, frame_count, progress_callback)
        else:
            audio_path = getattr(analysis, "audio_path", None)
            result = _write_video(
                output,
                profile,
                frames,
                frame_count,
                audio_path=audio_path,
                duration=max(0.0, end - start),
                cancel_event=cancel,
                progress_callback=progress_callback,
            )
        if progress_callback:
            progress_callback(1.0, f"Saved {result.name}")
        return result
    finally:
        session.close()


__all__ = [
    "EXPORT_PROFILE_SCHEMA",
    "EXPORT_PROFILE_VERSION",
    "ExportCancelled",
    "ExportConfigurationError",
    "ENCODER_MODES",
    "ExportProfile",
    "FORMAT_BY_KEY",
    "FORMAT_DEFINITIONS",
    "RESOLUTION_PRESETS",
    "ResolutionPreset",
    "build_ffmpeg_command",
    "export_visualization",
    "ffmpeg_executable",
    "load_export_profile",
    "output_path_for_profile",
    "save_export_profile",
    "available_ffmpeg_encoders",
    "select_video_encoder",
]
