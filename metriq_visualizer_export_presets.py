# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from metriq_visualizer_layout import ExportLayoutSpec, geometry_focus_export_layout


@dataclass(frozen=True)
class ExportPreset:
    key: str
    title: str
    width: int
    height: int
    fps: int
    layout_factory: Callable[[], ExportLayoutSpec]
    description: str


EXPORT_PRESETS: tuple[ExportPreset, ...] = (
    ExportPreset(
        key="core_landscape_720p",
        title="Landscape 720p",
        width=1280,
        height=720,
        fps=30,
        layout_factory=geometry_focus_export_layout,
        description="Geometry-only 720p landscape MP4 export.",
    ),
    ExportPreset(
        key="core_landscape_1080p",
        title="Landscape 1080p",
        width=1920,
        height=1080,
        fps=30,
        layout_factory=geometry_focus_export_layout,
        description="Geometry-only 1080p landscape MP4 export.",
    ),
    ExportPreset(
        key="core_vertical_1080x1920",
        title="Vertical 1080×1920",
        width=1080,
        height=1920,
        fps=30,
        layout_factory=geometry_focus_export_layout,
        description="Geometry-only vertical MP4 export for social posts.",
    ),
)

EXPORT_PRESET_MAP = {preset.key: preset for preset in EXPORT_PRESETS}
EXPORT_PRESET_TITLE_TO_KEY = {preset.title: preset.key for preset in EXPORT_PRESETS}
