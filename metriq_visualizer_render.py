# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Local RGBA composition renderer used by Export Studio and streaming output.

The six-layer compositor uses Pillow, NumPy, optional OpenCV, and the shared
headless Matplotlib 3D scene. It does not require a visible Qt widget or OpenGL
context, so the same true-3D geometry path is available in CI, image sequences,
and FFmpeg streaming exports.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from metriq_visualizer_3d import Matplotlib3DFrameRenderer
from metriq_visualizer_core import AnalysisResult, GeometryResult
from metriq_visualizer_layout import LAYOUT_ITEM_ORDER, LAYOUT_ITEM_TITLES, ExportLayoutSpec, balanced_export_layout

try:  # Pillow 9 compatibility
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
    RESAMPLE_BILINEAR = Image.Resampling.BILINEAR
except AttributeError:  # pragma: no cover
    RESAMPLE_LANCZOS = Image.LANCZOS
    RESAMPLE_BILINEAR = Image.BILINEAR

DARK_BACKGROUND = (7, 11, 17, 255)
SURFACE = (11, 18, 27, 232)
SURFACE_RAISED = (16, 27, 39, 242)
BORDER = (54, 82, 98, 230)
GRID = (42, 65, 79, 100)
TEXT = (225, 238, 242, 255)
SUBTLE = (145, 169, 179, 255)
ACCENT = (28, 213, 145, 255)
ACCENT_BLUE = (81, 163, 242, 255)
WARNING = (238, 193, 79, 255)


@dataclass
class ExportOptions:
    output_path: str = ""
    width: int = 1920
    height: int = 1080
    fps: int = 30
    layout: ExportLayoutSpec = field(default_factory=balanced_export_layout)
    include_preview: bool = True
    include_panels: bool = True
    base_alpha: float = 0.82
    history_mode: str = "Trail fade"
    point_lifespan: float = 3.0
    fade_curve: float = 1.35
    line_width: float = 1.35
    path_curve_mode: str = "Smooth spline"
    curve_detail: int = 4
    connect_lines: bool = True
    ghost_path: bool = False
    elev: float = 24.0
    azim: float = 35.0
    autorotate: bool = True
    rotation_speed: float = 16.0
    zoom: float = 1.0
    point_size_scale: float = 0.4
    render_mode: str = "Points + line"
    tube_radius_scale: float = 1.0
    tube_sides: int = 12
    tube_follow_size: bool = True
    tube_taper: float = 0.2
    show_head_marker: bool = True
    comet_duration: float = 0.45
    flash_duration: float = 0.18
    head_size_scale: float = 0.24
    halo_size_scale: float = 0.45
    flash_size_scale: float = 0.05
    show_axes: bool = True
    show_grid: bool = True
    show_axis_labels: bool = True
    point_label_mode: str = "Off"
    point_label_content: str = "Time + Hz"
    max_point_labels: int = 8
    show_colorbar: bool = False
    show_scene_hud: bool = True
    show_timecode: bool = False
    show_project_title: bool = True
    project_title: str = "Metriq Visualizer"
    project_subtitle: str = ""
    show_watermark: bool = False
    watermark_text: str = ""
    title: str = "Metriq Visualizer"
    start_time: float = 0.0
    end_time: float | None = None


class ExportPreviewSession:
    """Reusable frame renderer with cached source readers and panel rasters."""

    def __init__(self, analysis: AnalysisResult, geometry: GeometryResult, options: ExportOptions) -> None:
        self.analysis = analysis
        self.geometry = geometry
        self.options = options
        self._capture: Any = None
        self._capture_attempted = False
        self._panel_cache: dict[tuple[str, int, int], Image.Image] = {}
        self._font_cache: dict[tuple[int, bool], ImageFont.ImageFont] = {}
        self._closed = False
        self._geometry_renderer: Matplotlib3DFrameRenderer | None = None

    # ------------------------------------------------------------------ public
    def render_frame(
        self,
        *,
        current_time: float,
        layout: ExportLayoutSpec | None = None,
        output_size: tuple[int, int] | None = None,
    ) -> np.ndarray:
        if self._closed:
            raise RuntimeError("The preview session is closed.")
        width, height = output_size or (self.options.width, self.options.height)
        width, height = max(2, int(width)), max(2, int(height))
        spec = (layout or self.options.layout).clone().clamp()
        background = _parse_color(spec.background, DARK_BACKGROUND)
        canvas = Image.new("RGBA", (width, height), background)

        render_order = spec.order if getattr(spec, "order", None) else list(LAYOUT_ITEM_ORDER)
        for name in render_order:
            item = spec.item(name)
            if not item.enabled:
                continue
            x = int(round(item.x * width))
            y = int(round(item.y * height))
            w = max(4, int(round(item.w * width)))
            h = max(4, int(round(item.h * height)))
            if x >= width or y >= height:
                continue
            w = min(w, width - x)
            h = min(h, height - y)
            panel = self._render_named_panel(name, w, h, float(current_time), item.show_title)
            panel = _apply_content_scale(panel, item.content_scale)
            panel = _fit_image(panel, (w, h), item.fit_mode, background=(0, 0, 0, 0))
            if item.content_alpha < 0.999:
                content_alpha = float(item.content_alpha)
                alpha = panel.getchannel("A").point(lambda value, scale=content_alpha: int(value * scale))
                panel.putalpha(alpha)
            framed = _frame_panel(
                panel,
                title=LAYOUT_ITEM_TITLES.get(name, name.title()) if item.show_title else "",
                background_alpha=item.background_alpha,
                accent=_panel_accent(name),
            )
            canvas.alpha_composite(framed, (x, y))

        self._draw_global_overlays(canvas, float(current_time))
        return np.asarray(canvas, dtype=np.uint8)

    def close(self) -> None:
        if self._capture is not None:
            with suppress(Exception):
                self._capture.release()
        self._capture = None
        if self._geometry_renderer is not None:
            with suppress(Exception):
                self._geometry_renderer.close()
        self._geometry_renderer = None
        self._panel_cache.clear()
        self._closed = True

    def __enter__(self) -> ExportPreviewSession:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    # --------------------------------------------------------------- composition
    def _render_named_panel(self, name: str, width: int, height: int, current_time: float, show_title: bool) -> Image.Image:
        title_height = _title_height(height) if show_title else 0
        content_height = max(4, height - title_height)
        if name == "geometry":
            image = self._render_geometry(width, content_height, current_time)
        elif name == "preview":
            image = self._render_source(width, content_height, current_time)
        elif name == "spectrogram":
            image = self._render_analysis_panel("spectrogram", width, content_height, current_time)
        elif name == "chromagram":
            image = self._render_analysis_panel("chromagram", width, content_height, current_time)
        elif name == "mfcc":
            image = self._render_analysis_panel("mfcc", width, content_height, current_time)
        elif name == "traces":
            image = self._render_traces(width, content_height, current_time)
        else:  # pragma: no cover - guarded by layout model
            image = Image.new("RGBA", (width, content_height), SURFACE)
        if title_height:
            padded = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            padded.alpha_composite(image, (0, title_height))
            return padded
        return image

    def _draw_global_overlays(self, canvas: Image.Image, current_time: float) -> None:
        draw = ImageDraw.Draw(canvas, "RGBA")
        width, height = canvas.size
        if self.options.show_project_title:
            margin = max(14, int(min(width, height) * 0.022))
            title_size = max(16, int(min(width, height) * 0.035))
            sub_size = max(10, int(title_size * 0.46))
            title_font = self._font(title_size, bold=True)
            sub_font = self._font(sub_size)
            title = str(self.options.project_title or self.options.title).strip()
            subtitle = str(self.options.project_subtitle).strip()
            if title:
                bbox = draw.textbbox((0, 0), title, font=title_font)
                box_w = bbox[2] - bbox[0] + margin * 1.4
                box_h = bbox[3] - bbox[1] + margin * (1.25 if subtitle else 0.8)
                if subtitle:
                    sub_bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
                    box_w = max(box_w, sub_bbox[2] - sub_bbox[0] + margin * 1.4)
                    box_h += sub_bbox[3] - sub_bbox[1] + margin * 0.25
                polygon = _cut_corner_polygon((margin, margin, int(box_w), int(box_h)), max(5, margin // 3))
                draw.polygon(polygon, fill=(5, 10, 15, 188), outline=(43, 91, 85, 225), width=max(1, margin // 12))
                draw.text((margin * 1.7, margin * 1.35), title, font=title_font, fill=TEXT)
                if subtitle:
                    draw.text((margin * 1.7, margin * 1.35 + title_size * 1.1), subtitle.upper(), font=sub_font, fill=SUBTLE)

        if self.options.show_watermark and str(self.options.watermark_text).strip():
            text = str(self.options.watermark_text).strip()
            font = self._font(max(10, int(min(width, height) * 0.019)), bold=True)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            pad = max(8, int(th * 0.55))
            x, y = width - tw - pad * 2, height - th - pad * 2
            draw.rounded_rectangle((x, y, width - pad // 2, height - pad // 2), radius=pad // 2, fill=(4, 8, 12, 165), outline=(65, 103, 112, 180))
            draw.text((x + pad, y + pad // 2), text, font=font, fill=(220, 238, 239, 210))

        if self.options.show_timecode:
            status_font = self._font(max(8, int(min(width, height) * 0.011)))
            timestamp = _format_time(current_time)
            label = f"LOCAL / {timestamp}"
            bbox = draw.textbbox((0, 0), label, font=status_font)
            draw.text((width - (bbox[2] - bbox[0]) - 10, 8), label, font=status_font, fill=(115, 144, 151, 160))

    # ---------------------------------------------------------------- geometry
    def _render_geometry(self, width: int, height: int, current_time: float) -> Image.Image:
        """Render geometry through the shared genuine 3D scene."""

        if self._geometry_renderer is None:
            self._geometry_renderer = Matplotlib3DFrameRenderer(
                self.analysis,
                self.geometry,
                self.options,
                width=width,
                height=height,
            )
        else:
            self._geometry_renderer.update_options(self.options)
        rgba = self._geometry_renderer.render_frame(current_time, width=width, height=height)
        return Image.fromarray(rgba, mode="RGBA")

    def _draw_technical_grid(self, draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
        step = max(28, int(min(width, height) / 11))
        for x in range(step, width, step):
            draw.line((x, 0, x, height), fill=GRID, width=1)
        for y in range(step, height, step):
            draw.line((0, y, width, y), fill=GRID, width=1)
        cut = max(10, int(min(width, height) * 0.03))
        draw.line((0, cut, cut, 0), fill=(36, 91, 78, 130), width=2)
        draw.line((width - cut, height, width, height - cut), fill=(36, 91, 78, 130), width=2)

    # --------------------------------------------------------------- source view
    def _open_capture(self) -> None:
        if self._capture_attempted:
            return
        self._capture_attempted = True
        try:
            import cv2
        except ImportError:
            return
        path = Path(self.analysis.source_path)
        capture = cv2.VideoCapture(str(path))
        if capture.isOpened():
            self._capture = capture
        else:
            capture.release()

    def _render_source(self, width: int, height: int, current_time: float) -> Image.Image:
        if self._capture is None and bool(getattr(self.analysis, "has_video", False)):
            self._open_capture()
        if self._capture is not None:
            try:
                import cv2

                self._capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, current_time) * 1000.0)
                ok, frame = self._capture.read()
                if ok and frame is not None:
                    rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
                    return Image.fromarray(rgba, mode="RGBA")
            except Exception:
                pass
        suffix = self.analysis.source_path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            try:
                return Image.open(self.analysis.source_path).convert("RGBA")
            except Exception:
                pass
        return self._render_waveform(width, height, current_time)

    def _render_waveform(self, width: int, height: int, current_time: float) -> Image.Image:
        image = Image.new("RGBA", (width, height), (7, 13, 19, 255))
        draw = ImageDraw.Draw(image, "RGBA")
        self._draw_technical_grid(draw, width, height)
        waveform = np.asarray(self.analysis.waveform, dtype=np.float32)
        if waveform.size == 0:
            values = self.analysis.features.get("column_mean", self.analysis.features.get("rms", np.empty(0)))
            waveform = np.asarray(values, dtype=np.float32)
        if waveform.size:
            columns = max(2, width)
            if waveform.size > columns:
                edges = np.linspace(0, waveform.size, columns + 1, dtype=np.int64)
                minimum = np.empty(columns, dtype=np.float32)
                maximum = np.empty(columns, dtype=np.float32)
                for index in range(columns):
                    segment = waveform[edges[index] : max(edges[index] + 1, edges[index + 1])]
                    minimum[index] = float(np.min(segment))
                    maximum[index] = float(np.max(segment))
            else:
                x_old = np.linspace(0, 1, waveform.size)
                x_new = np.linspace(0, 1, columns)
                resampled = np.interp(x_new, x_old, waveform)
                minimum = maximum = resampled.astype(np.float32)
            amplitude = float(max(np.percentile(np.abs(np.concatenate((minimum, maximum))), 98), 1e-6))
            center = height / 2
            scale = height * 0.40 / amplitude
            for x in range(columns):
                y1 = center - float(maximum[x]) * scale
                y2 = center - float(minimum[x]) * scale
                draw.line((x, y1, x, y2), fill=(43, 211, 148, 210), width=1)
            ratio = current_time / max(self.analysis.duration, 1e-9)
            cursor_x = int(np.clip(ratio, 0.0, 1.0) * (width - 1))
            draw.line((cursor_x, 0, cursor_x, height), fill=(104, 188, 247, 235), width=max(1, width // 450))
        else:
            self._draw_empty(draw, width, height, "NO SOURCE PREVIEW")
        return image

    # ------------------------------------------------------------ analysis views
    def _render_analysis_panel(self, name: str, width: int, height: int, current_time: float) -> Image.Image:
        key = (name, width, height)
        base = self._panel_cache.get(key)
        if base is None:
            if name == "spectrogram":
                matrix = self.analysis.spectrogram
                cmap = "magma"
                flip = True
            elif name == "chromagram":
                matrix = self.analysis.chromagram
                cmap = "viridis"
                flip = True
            else:
                matrix = self.analysis.mfcc
                cmap = "cividis"
                flip = False
            base = _heatmap_image(matrix, width, height, cmap=cmap, flip_vertical=flip)
            base = _add_panel_grid(base)
            self._panel_cache[key] = base
        image = base.copy()
        draw = ImageDraw.Draw(image, "RGBA")
        ratio = current_time / max(self.analysis.duration, 1e-9)
        x = int(np.clip(ratio, 0.0, 1.0) * max(0, width - 1))
        draw.line((x, 0, x, height), fill=(238, 244, 246, 225), width=max(1, width // 500))
        draw.line((x + 1, 0, x + 1, height), fill=(28, 213, 145, 190), width=1)
        return image

    def _render_traces(self, width: int, height: int, current_time: float) -> Image.Image:
        key = ("traces", width, height)
        base = self._panel_cache.get(key)
        if base is None:
            image = Image.new("RGBA", (width, height), (7, 13, 19, 255))
            draw = ImageDraw.Draw(image, "RGBA")
            self._draw_technical_grid(draw, width, height)
            series = (
                (self.geometry.x_full, (43, 213, 148, 220)),
                (self.geometry.y_full, (88, 171, 245, 220)),
                (self.geometry.z_full, (231, 188, 79, 220)),
                (self.geometry.color_full, (192, 119, 239, 190)),
            )
            for values, color in series:
                points = _trace_points(values, width, height)
                if len(points) > 1:
                    draw.line(points, fill=color, width=max(1, int(height / 120)), joint="curve")
            base = image
            self._panel_cache[key] = base
        image = base.copy()
        draw = ImageDraw.Draw(image, "RGBA")
        ratio = current_time / max(self.analysis.duration, 1e-9)
        x = int(np.clip(ratio, 0.0, 1.0) * max(0, width - 1))
        draw.line((x, 0, x, height), fill=(239, 245, 247, 230), width=max(1, width // 500))
        return image

    # ---------------------------------------------------------------- utilities
    def _font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        key = (max(7, int(size)), bool(bold))
        cached = self._font_cache.get(key)
        if cached is not None:
            return cached
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        ]
        font: ImageFont.ImageFont
        for candidate in candidates:
            try:
                font = ImageFont.truetype(candidate, key[0])
                break
            except OSError:
                continue
        else:
            font = ImageFont.load_default()
        self._font_cache[key] = font
        return font

    def _draw_empty(self, draw: ImageDraw.ImageDraw, width: int, height: int, label: str) -> None:
        font = self._font(max(10, int(min(width, height) * 0.055)), bold=True)
        bbox = draw.textbbox((0, 0), label, font=font)
        draw.text(((width - bbox[2] + bbox[0]) / 2, (height - bbox[3] + bbox[1]) / 2), label, font=font, fill=SUBTLE)


# ---------------------------------------------------------------------------
# Standalone helpers


def _parse_color(value: str, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    text = str(value).strip().lstrip("#")
    try:
        if len(text) == 6:
            return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), 255
        if len(text) == 8:
            return tuple(int(text[index : index + 2], 16) for index in range(0, 8, 2))  # type: ignore[return-value]
    except ValueError:
        pass
    return default


def _panel_accent(name: str) -> tuple[int, int, int, int]:
    return {
        "geometry": (29, 211, 144, 255),
        "preview": (89, 166, 244, 255),
        "spectrogram": (37, 184, 204, 255),
        "chromagram": (139, 207, 112, 255),
        "mfcc": (148, 135, 225, 255),
        "traces": (231, 190, 90, 255),
    }.get(name, ACCENT)


def _title_height(height: int) -> int:
    return max(18, min(42, int(height * 0.11)))


def _cut_corner_polygon(rect: tuple[int, int, int, int], cut: int) -> list[tuple[int, int]]:
    x, y, w, h = rect
    right, bottom = x + w, y + h
    c = max(0, min(cut, w // 3, h // 3))
    return [(x + c, y), (right, y), (right, bottom - c), (right - c, bottom), (x, bottom), (x, y + c)]


def _frame_panel(panel: Image.Image, *, title: str, background_alpha: float, accent: tuple[int, int, int, int]) -> Image.Image:
    width, height = panel.size
    cut = max(4, min(18, int(min(width, height) * 0.06)))
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.polygon(_cut_corner_polygon((0, 0, width - 1, height - 1), cut), fill=255)
    base = Image.new("RGBA", (width, height), (8, 14, 21, int(255 * background_alpha)))
    base.putalpha(mask)
    content = panel.copy()
    content_alpha = Image.composite(content.getchannel("A"), Image.new("L", (width, height), 0), mask)
    content.putalpha(content_alpha)
    base.alpha_composite(content)
    draw = ImageDraw.Draw(base, "RGBA")
    draw.line((cut, 0, width - 1, 0), fill=accent[:-1] + (185,), width=max(1, min(width, height) // 220 + 1))
    draw.line((0, cut, cut, 0), fill=accent[:-1] + (185,), width=max(1, min(width, height) // 220 + 1))
    draw.line((width - 1, 0, width - 1, height - cut), fill=BORDER, width=1)
    draw.line((width - cut, height - 1, width - 1, height - cut), fill=BORDER, width=1)
    draw.line((0, cut, 0, height - 1), fill=BORDER, width=1)
    draw.line((0, height - 1, width - cut, height - 1), fill=BORDER, width=1)
    if title:
        title_h = _title_height(height)
        draw.rectangle((0, 0, width, title_h), fill=(5, 10, 16, 204))
        draw.line((0, title_h, width, title_h), fill=accent[:-1] + (115,), width=1)
        font = _load_font(max(8, int(title_h * 0.38)), bold=True)
        draw.text((max(8, cut), max(2, title_h * 0.24)), title.upper(), font=font, fill=(207, 226, 229, 235))
    return base


def _load_font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit_image(image: Image.Image, target: tuple[int, int], mode: str, *, background: tuple[int, int, int, int]) -> Image.Image:
    width, height = max(1, int(target[0])), max(1, int(target[1]))
    source = image.convert("RGBA")
    if source.size == (width, height):
        return source
    if str(mode).lower() == "stretch":
        return source.resize((width, height), RESAMPLE_LANCZOS)
    scale_x = width / max(1, source.width)
    scale_y = height / max(1, source.height)
    scale = max(scale_x, scale_y) if str(mode).lower() == "cover" else min(scale_x, scale_y)
    resized = source.resize((max(1, round(source.width * scale)), max(1, round(source.height * scale))), RESAMPLE_LANCZOS)
    if str(mode).lower() == "cover":
        left = max(0, (resized.width - width) // 2)
        top = max(0, (resized.height - height) // 2)
        return resized.crop((left, top, left + width, top + height))
    result = Image.new("RGBA", (width, height), background)
    result.alpha_composite(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return result


def _apply_content_scale(image: Image.Image, scale: float) -> Image.Image:
    factor = max(0.1, min(4.0, float(scale)))
    if abs(factor - 1.0) < 1e-3:
        return image
    width, height = image.size
    resized = image.resize((max(1, round(width * factor)), max(1, round(height * factor))), RESAMPLE_LANCZOS)
    if factor > 1.0:
        left = max(0, (resized.width - width) // 2)
        top = max(0, (resized.height - height) // 2)
        return resized.crop((left, top, left + width, top + height))
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    result.alpha_composite(resized, ((width - resized.width) // 2, (height - resized.height) // 2))
    return result


def _heatmap_image(matrix: np.ndarray, width: int, height: int, *, cmap: str, flip_vertical: bool) -> Image.Image:
    values = np.asarray(matrix, dtype=np.float32)
    if values.ndim != 2 or values.size == 0:
        return Image.new("RGBA", (width, height), SURFACE)
    finite = np.isfinite(values)
    if finite.any():
        low, high = np.percentile(values[finite], [1.0, 99.0])
        normalized = np.zeros_like(values) if high <= low + 1e-12 else np.clip((values - low) / (high - low), 0.0, 1.0)
    else:
        normalized = np.zeros_like(values)
    if flip_vertical:
        normalized = normalized[::-1]
    try:
        from matplotlib import colormaps

        rgba = np.asarray(colormaps.get_cmap(cmap)(normalized), dtype=np.float32)
        rgba[..., 3] = 1.0
        pixels = np.clip(rgba * 255.0, 0, 255).astype(np.uint8)
    except Exception:
        pixels = np.empty((*normalized.shape, 4), dtype=np.uint8)
        pixels[..., 0] = (normalized * 255).astype(np.uint8)
        pixels[..., 1] = (np.sqrt(normalized) * 190).astype(np.uint8)
        pixels[..., 2] = ((1.0 - normalized) * 140).astype(np.uint8)
        pixels[..., 3] = 255
    return Image.fromarray(pixels, mode="RGBA").resize((width, height), RESAMPLE_BILINEAR)


def _add_panel_grid(image: Image.Image) -> Image.Image:
    result = image.copy()
    draw = ImageDraw.Draw(result, "RGBA")
    width, height = result.size
    for index in range(1, 8):
        x = int(width * index / 8)
        draw.line((x, 0, x, height), fill=(232, 244, 245, 28), width=1)
    for index in range(1, 4):
        y = int(height * index / 4)
        draw.line((0, y, width, y), fill=(232, 244, 245, 24), width=1)
    return result


def _trace_points(values: np.ndarray, width: int, height: int) -> list[tuple[float, float]]:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if array.size == 0:
        return []
    count = min(width, array.size)
    indices = np.linspace(0, array.size - 1, count, dtype=np.int64)
    sampled = array[indices]
    low, high = np.percentile(sampled, [2.0, 98.0])
    normalized = np.full_like(sampled, 0.5) if high <= low + 1e-12 else np.clip((sampled - low) / (high - low), 0.0, 1.0)
    x = np.linspace(0, width - 1, count)
    y = (height * 0.88) - normalized * (height * 0.76)
    return list(zip(x.tolist(), y.tolist(), strict=True))


def _smooth_path(path: np.ndarray, colors: np.ndarray, alphas: np.ndarray, detail: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    points = np.asarray(path, dtype=np.float64)
    if points.shape[0] < 4 or detail <= 1:
        return path, colors, alphas
    # Catmull-Rom interpolation with endpoint duplication.
    padded = np.vstack((points[0], points, points[-1]))
    color_pad = np.vstack((colors[0], colors, colors[-1]))
    alpha_pad = np.concatenate(([alphas[0]], alphas, [alphas[-1]]))
    output_points: list[np.ndarray] = []
    output_colors: list[np.ndarray] = []
    output_alphas: list[float] = []
    for index in range(1, padded.shape[0] - 2):
        p0, p1, p2, p3 = padded[index - 1 : index + 3]
        c1, c2 = color_pad[index], color_pad[index + 1]
        a1, a2 = alpha_pad[index], alpha_pad[index + 1]
        for step in range(detail):
            t = step / detail
            t2, t3 = t * t, t * t * t
            point = 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
            output_points.append(point)
            output_colors.append((1.0 - t) * c1 + t * c2)
            output_alphas.append(float((1.0 - t) * a1 + t * a2))
    output_points.append(points[-1])
    output_colors.append(colors[-1])
    output_alphas.append(float(alphas[-1]))
    return np.asarray(output_points, dtype=np.float32), np.asarray(output_colors, dtype=np.float32), np.asarray(output_alphas, dtype=np.float32)


def _format_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"


__all__ = ["ExportOptions", "ExportPreviewSession"]
