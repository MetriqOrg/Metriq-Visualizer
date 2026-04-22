# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

FIT_MODES = ("contain", "fill", "stretch")
LAYOUT_ITEM_ORDER = ("geometry", "preview", "spectrogram", "chromagram", "mfcc", "traces")
LAYOUT_ITEM_TITLES = {
    "geometry": "Geometry",
    "preview": "Source preview",
    "spectrogram": "Spectrogram",
    "chromagram": "Chromagram",
    "mfcc": "MFCC / Cepstrogram",
    "traces": "Mapped traces",
}


@dataclass
class LayoutRect:
    enabled: bool = True
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0
    content_scale: float = 1.0
    fit_mode: str = "contain"
    background_alpha: float = 0.08
    show_title: bool = True

    def clamp(self) -> "LayoutRect":
        self.x = float(min(1.0, max(0.0, self.x)))
        self.y = float(min(1.0, max(0.0, self.y)))
        self.w = float(min(1.0, max(0.05, self.w)))
        self.h = float(min(1.0, max(0.05, self.h)))
        self.content_scale = float(min(5.0, max(0.35, self.content_scale)))
        self.fit_mode = str(self.fit_mode) if str(self.fit_mode) in FIT_MODES else "contain"
        self.background_alpha = float(min(1.0, max(0.0, self.background_alpha)))
        self.show_title = bool(self.show_title)
        if self.x + self.w > 1.0:
            self.x = max(0.0, 1.0 - self.w)
        if self.y + self.h > 1.0:
            self.y = max(0.0, 1.0 - self.h)
        return self

    def copy(self) -> "LayoutRect":
        return deepcopy(self)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LayoutRect":
        data = data or {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            w=float(data.get("w", 1.0)),
            h=float(data.get("h", 1.0)),
            content_scale=float(data.get("content_scale", 1.0)),
            fit_mode=str(data.get("fit_mode", "contain")),
            background_alpha=float(data.get("background_alpha", 0.08)),
            show_title=bool(data.get("show_title", True)),
        ).clamp()


@dataclass
class ExportLayoutSpec:
    geometry: LayoutRect = field(default_factory=LayoutRect)
    preview: LayoutRect = field(default_factory=LayoutRect)
    spectrogram: LayoutRect = field(default_factory=LayoutRect)
    chromagram: LayoutRect = field(default_factory=LayoutRect)
    mfcc: LayoutRect = field(default_factory=LayoutRect)
    traces: LayoutRect = field(default_factory=LayoutRect)

    def clone(self) -> "ExportLayoutSpec":
        return deepcopy(self)

    def item(self, name: str) -> LayoutRect:
        return getattr(self, name)

    def clamp(self) -> "ExportLayoutSpec":
        for name in LAYOUT_ITEM_ORDER:
            getattr(self, name).clamp()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {name: self.item(name).to_dict() for name in LAYOUT_ITEM_ORDER}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExportLayoutSpec":
        data = data or {}
        return cls(**{name: LayoutRect.from_dict(data.get(name)) for name in LAYOUT_ITEM_ORDER}).clamp()


DEFAULT_BLOCK_ALPHA = {
    "geometry": 0.02,
    "preview": 0.04,
    "spectrogram": 0.06,
    "chromagram": 0.06,
    "mfcc": 0.06,
    "traces": 0.06,
}


def _rect(
    enabled: bool,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    scale: float = 1.0,
    fit: str = "contain",
    bg: float = 0.08,
    show_title: bool = True,
) -> LayoutRect:
    return LayoutRect(enabled, x, y, w, h, content_scale=scale, fit_mode=fit, background_alpha=bg, show_title=show_title)


def balanced_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_rect(True, 0.03, 0.04, 0.68, 0.60, fit="contain", bg=DEFAULT_BLOCK_ALPHA["geometry"]),
        preview=_rect(True, 0.74, 0.06, 0.23, 0.22, fit="contain", bg=DEFAULT_BLOCK_ALPHA["preview"]),
        spectrogram=_rect(True, 0.03, 0.69, 0.47, 0.12, fit="fill", bg=DEFAULT_BLOCK_ALPHA["spectrogram"]),
        chromagram=_rect(True, 0.51, 0.69, 0.46, 0.10, fit="fill", bg=DEFAULT_BLOCK_ALPHA["chromagram"]),
        mfcc=_rect(True, 0.03, 0.83, 0.47, 0.13, fit="fill", bg=DEFAULT_BLOCK_ALPHA["mfcc"]),
        traces=_rect(True, 0.51, 0.81, 0.46, 0.15, fit="contain", bg=DEFAULT_BLOCK_ALPHA["traces"]),
    )


def geometry_focus_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_rect(True, 0.03, 0.04, 0.75, 0.72, scale=1.15, fit="contain", bg=DEFAULT_BLOCK_ALPHA["geometry"]),
        preview=_rect(True, 0.80, 0.05, 0.17, 0.18, fit="contain", bg=DEFAULT_BLOCK_ALPHA["preview"]),
        spectrogram=_rect(True, 0.03, 0.80, 0.31, 0.15, fit="fill", bg=DEFAULT_BLOCK_ALPHA["spectrogram"]),
        chromagram=_rect(True, 0.35, 0.80, 0.20, 0.15, fit="fill", bg=DEFAULT_BLOCK_ALPHA["chromagram"]),
        mfcc=_rect(True, 0.56, 0.80, 0.20, 0.15, fit="fill", bg=DEFAULT_BLOCK_ALPHA["mfcc"]),
        traces=_rect(True, 0.77, 0.33, 0.20, 0.29, fit="contain", bg=DEFAULT_BLOCK_ALPHA["traces"]),
    )


def analysis_focus_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_rect(True, 0.03, 0.04, 0.54, 0.54, fit="contain", bg=DEFAULT_BLOCK_ALPHA["geometry"]),
        preview=_rect(True, 0.60, 0.05, 0.18, 0.17, fit="contain", bg=DEFAULT_BLOCK_ALPHA["preview"]),
        spectrogram=_rect(True, 0.03, 0.62, 0.45, 0.15, fit="fill", bg=DEFAULT_BLOCK_ALPHA["spectrogram"]),
        chromagram=_rect(True, 0.50, 0.62, 0.47, 0.13, fit="fill", bg=DEFAULT_BLOCK_ALPHA["chromagram"]),
        mfcc=_rect(True, 0.03, 0.80, 0.45, 0.16, fit="fill", bg=DEFAULT_BLOCK_ALPHA["mfcc"]),
        traces=_rect(True, 0.50, 0.77, 0.47, 0.19, fit="contain", bg=DEFAULT_BLOCK_ALPHA["traces"]),
    )


def overlay_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_rect(True, 0.02, 0.03, 0.96, 0.92, scale=1.22, fit="contain", bg=0.0, show_title=False),
        preview=_rect(True, 0.76, 0.05, 0.20, 0.18, fit="contain", bg=0.04),
        spectrogram=_rect(True, 0.03, 0.75, 0.55, 0.12, fit="fill", bg=0.03),
        chromagram=_rect(True, 0.03, 0.88, 0.33, 0.08, fit="fill", bg=0.03),
        mfcc=_rect(False, 0.38, 0.88, 0.28, 0.08, fit="fill", bg=0.03),
        traces=_rect(True, 0.68, 0.68, 0.29, 0.16, fit="contain", bg=0.04),
    )


def social_vertical_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_rect(True, 0.04, 0.05, 0.92, 0.48, scale=1.18, fit="contain", bg=DEFAULT_BLOCK_ALPHA["geometry"]),
        preview=_rect(True, 0.70, 0.05, 0.22, 0.12, fit="contain", bg=DEFAULT_BLOCK_ALPHA["preview"]),
        spectrogram=_rect(True, 0.06, 0.56, 0.88, 0.11, fit="fill", bg=DEFAULT_BLOCK_ALPHA["spectrogram"]),
        chromagram=_rect(True, 0.06, 0.69, 0.88, 0.09, fit="fill", bg=DEFAULT_BLOCK_ALPHA["chromagram"]),
        mfcc=_rect(True, 0.06, 0.80, 0.88, 0.10, fit="fill", bg=DEFAULT_BLOCK_ALPHA["mfcc"]),
        traces=_rect(True, 0.06, 0.91, 0.88, 0.06, fit="contain", bg=DEFAULT_BLOCK_ALPHA["traces"]),
    )


def default_export_layout() -> ExportLayoutSpec:
    return balanced_export_layout()
