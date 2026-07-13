# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Resolution-independent six-layer export layout model."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

LAYOUT_SCHEMA = "metriq.export-layout"
LAYOUT_SCHEMA_VERSION = 2
LAYOUT_ITEM_ORDER: tuple[str, ...] = ("geometry", "preview", "spectrogram", "chromagram", "mfcc", "traces")
LAYOUT_ITEM_TITLES: dict[str, str] = {
    "geometry": "Geometry",
    "preview": "Source",
    "spectrogram": "Spectrogram",
    "chromagram": "Chromagram",
    "mfcc": "MFCC",
    "traces": "Mapped traces",
}
FIT_MODES: tuple[str, ...] = ("contain", "cover", "stretch")


@dataclass(slots=True)
class LayoutItemSpec:
    enabled: bool = True
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0
    content_scale: float = 1.0
    fit_mode: str = "contain"
    background_alpha: float = 0.72
    show_title: bool = True
    content_alpha: float = 1.0

    def clamp(self) -> LayoutItemSpec:
        self.enabled = bool(self.enabled)
        self.w = min(1.0, max(0.03, float(self.w)))
        self.h = min(1.0, max(0.03, float(self.h)))
        self.x = min(1.0 - self.w, max(0.0, float(self.x)))
        self.y = min(1.0 - self.h, max(0.0, float(self.y)))
        self.content_scale = min(4.0, max(0.1, float(self.content_scale)))
        self.fit_mode = str(self.fit_mode).lower() if str(self.fit_mode).lower() in FIT_MODES else "contain"
        self.background_alpha = min(1.0, max(0.0, float(self.background_alpha)))
        self.content_alpha = min(1.0, max(0.0, float(self.content_alpha)))
        self.show_title = bool(self.show_title)
        return self

    def clone(self) -> LayoutItemSpec:
        return deepcopy(self)

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> LayoutItemSpec:
        if not isinstance(payload, Mapping):
            return cls()
        known = cls.__dataclass_fields__.keys()  # type: ignore[attr-defined]
        return cls(**{key: payload[key] for key in known if key in payload}).clamp()


@dataclass(slots=True)
class ExportLayoutSpec:
    geometry: LayoutItemSpec = field(default_factory=LayoutItemSpec)
    preview: LayoutItemSpec = field(default_factory=lambda: LayoutItemSpec(enabled=False))
    spectrogram: LayoutItemSpec = field(default_factory=lambda: LayoutItemSpec(enabled=False))
    chromagram: LayoutItemSpec = field(default_factory=lambda: LayoutItemSpec(enabled=False))
    mfcc: LayoutItemSpec = field(default_factory=lambda: LayoutItemSpec(enabled=False))
    traces: LayoutItemSpec = field(default_factory=lambda: LayoutItemSpec(enabled=False))
    order: list[str] = field(default_factory=lambda: list(LAYOUT_ITEM_ORDER))
    background: str = "#070b11"
    safe_area_percent: float = 5.0

    def item(self, name: str) -> LayoutItemSpec:
        if name not in LAYOUT_ITEM_ORDER:
            raise KeyError(f"Unknown layout item: {name}")
        return getattr(self, name)

    def clone(self) -> ExportLayoutSpec:
        return deepcopy(self)

    def clamp(self) -> ExportLayoutSpec:
        for name in LAYOUT_ITEM_ORDER:
            self.item(name).clamp()
        seen: set[str] = set()
        normalized: list[str] = []
        for name in self.order:
            if name in LAYOUT_ITEM_ORDER and name not in seen:
                seen.add(name)
                normalized.append(name)
        normalized.extend(name for name in LAYOUT_ITEM_ORDER if name not in seen)
        self.order = normalized
        self.background = str(self.background or "#070b11")
        self.safe_area_percent = min(20.0, max(0.0, float(self.safe_area_percent)))
        return self

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return {
            "schema": LAYOUT_SCHEMA,
            "schema_version": LAYOUT_SCHEMA_VERSION,
            "items": {name: self.item(name).to_dict() for name in LAYOUT_ITEM_ORDER},
            "order": list(self.order),
            "background": self.background,
            "safe_area_percent": self.safe_area_percent,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ExportLayoutSpec:
        if not isinstance(payload, Mapping):
            return default_export_layout()
        items_payload = payload.get("items") if isinstance(payload.get("items"), Mapping) else payload
        kwargs: dict[str, Any] = {}
        for name in LAYOUT_ITEM_ORDER:
            item_payload = items_payload.get(name) if isinstance(items_payload, Mapping) else None
            kwargs[name] = LayoutItemSpec.from_dict(item_payload if isinstance(item_payload, Mapping) else None)
        order = payload.get("order", list(LAYOUT_ITEM_ORDER))
        kwargs["order"] = list(order) if isinstance(order, list | tuple) else list(LAYOUT_ITEM_ORDER)
        kwargs["background"] = str(payload.get("background", "#070b11"))
        kwargs["safe_area_percent"] = float(payload.get("safe_area_percent", 5.0))
        return cls(**kwargs).clamp()

    def move_layer(self, name: str, delta: int) -> None:
        self.clamp()
        if name not in self.order:
            return
        current = self.order.index(name)
        target = max(0, min(len(self.order) - 1, current + int(delta)))
        if target != current:
            self.order.insert(target, self.order.pop(current))


def _item(enabled: bool, x: float, y: float, w: float, h: float, *,
          scale: float = 1.0, fit: str = "contain", alpha: float = 0.74,
          title: bool = True, content_alpha: float = 1.0) -> LayoutItemSpec:
    return LayoutItemSpec(enabled, x, y, w, h, scale, fit, alpha, title, content_alpha).clamp()


def balanced_export_layout() -> ExportLayoutSpec:
    """Default landscape layout with a full-width visual field and bottom analysis dock."""

    return ExportLayoutSpec(
        geometry=_item(True, 0.015, 0.025, 0.970, 0.625, alpha=0.24),
        preview=_item(True, 0.015, 0.675, 0.235, 0.300, fit="cover", alpha=0.82),
        spectrogram=_item(True, 0.265, 0.675, 0.355, 0.140, alpha=0.82),
        chromagram=_item(True, 0.265, 0.835, 0.170, 0.140, alpha=0.82),
        mfcc=_item(True, 0.450, 0.835, 0.170, 0.140, alpha=0.82),
        traces=_item(True, 0.635, 0.675, 0.350, 0.300, alpha=0.82),
    ).clamp()


def default_export_layout() -> ExportLayoutSpec:
    return balanced_export_layout()


def geometry_focus_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_item(True, 0.015, 0.025, 0.970, 0.950, alpha=0.18),
        preview=_item(False, 0.73, 0.05, 0.24, 0.25, fit="cover", alpha=0.80),
        spectrogram=_item(False, 0.68, 0.72, 0.29, 0.20),
        chromagram=_item(False, 0.68, 0.58, 0.29, 0.11),
        mfcc=_item(False, 0.68, 0.44, 0.29, 0.11),
        traces=_item(False, 0.68, 0.31, 0.29, 0.10),
    ).clamp()


def analysis_focus_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_item(True, 0.015, 0.025, 0.970, 0.440, alpha=0.25),
        preview=_item(True, 0.015, 0.490, 0.300, 0.485, fit="cover", alpha=0.84),
        spectrogram=_item(True, 0.330, 0.490, 0.655, 0.220, alpha=0.84),
        chromagram=_item(True, 0.330, 0.735, 0.205, 0.240, alpha=0.84),
        mfcc=_item(True, 0.550, 0.735, 0.205, 0.240, alpha=0.84),
        traces=_item(True, 0.770, 0.735, 0.215, 0.240, alpha=0.84),
    ).clamp()


def overlay_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_item(True, 0.0, 0.0, 1.0, 1.0, alpha=0.0, title=False),
        preview=_item(True, 0.70, 0.055, 0.265, 0.265, fit="cover", alpha=0.70),
        spectrogram=_item(True, 0.05, 0.78, 0.90, 0.16, alpha=0.64),
        chromagram=_item(False, 0.05, 0.64, 0.43, 0.11, alpha=0.64),
        mfcc=_item(False, 0.52, 0.64, 0.43, 0.11, alpha=0.64),
        traces=_item(False, 0.05, 0.50, 0.90, 0.11, alpha=0.64),
        order=["geometry", "spectrogram", "chromagram", "mfcc", "traces", "preview"],
    ).clamp()


def social_vertical_export_layout() -> ExportLayoutSpec:
    return ExportLayoutSpec(
        geometry=_item(True, 0.035, 0.025, 0.930, 0.555, alpha=0.24),
        preview=_item(True, 0.035, 0.595, 0.445, 0.190, fit="cover", alpha=0.82),
        spectrogram=_item(True, 0.500, 0.595, 0.465, 0.190, alpha=0.82),
        chromagram=_item(True, 0.035, 0.800, 0.290, 0.175, alpha=0.82),
        mfcc=_item(True, 0.345, 0.800, 0.290, 0.175, alpha=0.82),
        traces=_item(True, 0.655, 0.800, 0.310, 0.175, alpha=0.82),
    ).clamp()


__all__ = [
    "ExportLayoutSpec",
    "FIT_MODES",
    "LAYOUT_ITEM_ORDER",
    "LAYOUT_ITEM_TITLES",
    "LayoutItemSpec",
    "analysis_focus_export_layout",
    "balanced_export_layout",
    "default_export_layout",
    "geometry_focus_export_layout",
    "overlay_export_layout",
    "social_vertical_export_layout",
]
