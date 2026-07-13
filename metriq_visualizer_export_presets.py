# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Built-in creator export presets.

Presets expose the long-standing public module name while using the v1.12
resolution-independent layout model.  They are data, not feature gates; users
can change every value in Export Studio and save their own ``.mvexport`` file.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from metriq_visualizer_layout import (
    ExportLayoutSpec,
    analysis_focus_export_layout,
    balanced_export_layout,
    geometry_focus_export_layout,
    overlay_export_layout,
    social_vertical_export_layout,
)


@dataclass(frozen=True, slots=True)
class ExportPreset:
    key: str
    title: str
    width: int
    height: int
    fps: float
    layout_factory: Callable[[], ExportLayoutSpec]
    description: str = ""


EXPORT_PRESETS: tuple[ExportPreset, ...] = (
    ExportPreset(
        "hd_balanced",
        "HD · Balanced",
        1280,
        720,
        30.0,
        balanced_export_layout,
        "Fast 16:9 classroom, web, and presentation output.",
    ),
    ExportPreset(
        "full_hd_balanced",
        "Full HD · Balanced",
        1920,
        1080,
        30.0,
        balanced_export_layout,
        "General creator output with all analysis layers visible.",
    ),
    ExportPreset(
        "full_hd_geometry",
        "Full HD · Geometry focus",
        1920,
        1080,
        60.0,
        geometry_focus_export_layout,
        "Full-frame geometry suitable for editing into another video.",
    ),
    ExportPreset(
        "uhd_analysis",
        "4K UHD · Analysis",
        3840,
        2160,
        30.0,
        analysis_focus_export_layout,
        "High-resolution educational and scientific comparison layout.",
    ),
    ExportPreset(
        "vertical_social",
        "Vertical 1080 · Creator",
        1080,
        1920,
        30.0,
        social_vertical_export_layout,
        "9:16 layout for short-form platforms and mobile viewing.",
    ),
    ExportPreset(
        "square_overlay",
        "Square 1080 · Overlay",
        1080,
        1080,
        30.0,
        overlay_export_layout,
        "Square composition with source and spectrogram overlays.",
    ),
)

EXPORT_PRESET_MAP: dict[str, ExportPreset] = {preset.key: preset for preset in EXPORT_PRESETS}
EXPORT_PRESET_TITLE_TO_KEY: dict[str, str] = {preset.title: preset.key for preset in EXPORT_PRESETS}


def export_preset(key_or_title: str) -> ExportPreset:
    """Resolve a preset by stable key or human-readable title."""

    value = str(key_or_title)
    key = EXPORT_PRESET_TITLE_TO_KEY.get(value, value)
    try:
        return EXPORT_PRESET_MAP[key]
    except KeyError as exc:
        raise KeyError(f"Unknown export preset: {key_or_title}") from exc


__all__ = [
    "EXPORT_PRESETS",
    "EXPORT_PRESET_MAP",
    "EXPORT_PRESET_TITLE_TO_KEY",
    "ExportPreset",
    "export_preset",
]
