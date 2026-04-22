# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass
class CameraKeyframe:
    time: float
    elevation: float
    azimuth: float
    zoom: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CameraKeyframe":
        data = data or {}
        return cls(
            time=float(data.get("time", 0.0)),
            elevation=float(data.get("elevation", 24.0)),
            azimuth=float(data.get("azimuth", 35.0)),
            zoom=float(data.get("zoom", 1.0)),
        )


@dataclass
class Bookmark:
    time: float
    label: str = "Bookmark"
    point_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Bookmark":
        data = data or {}
        point_index = data.get("point_index")
        return cls(
            time=float(data.get("time", 0.0)),
            label=str(data.get("label", "Bookmark") or "Bookmark"),
            point_index=None if point_index is None else int(point_index),
        )


@dataclass
class TimeRegion:
    start: float
    end: float
    label: str = "Region"

    def normalized(self) -> "TimeRegion":
        start = float(self.start)
        end = float(self.end)
        if end < start:
            start, end = end, start
        return TimeRegion(start=start, end=end, label=self.label)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        start = float(data["start"])
        end = float(data["end"])
        if end < start:
            data["start"], data["end"] = end, start
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TimeRegion":
        data = data or {}
        region = cls(
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            label=str(data.get("label", "Region") or "Region"),
        )
        return region.normalized()


def clamp_time(value: float, duration: float | None = None) -> float:
    value = max(0.0, float(value))
    if duration is not None and duration > 0.0:
        value = min(value, float(duration))
    return value


def sort_keyframes(keyframes: Iterable[CameraKeyframe]) -> list[CameraKeyframe]:
    return sorted((CameraKeyframe(float(k.time), float(k.elevation), float(k.azimuth), float(k.zoom)) for k in keyframes), key=lambda k: (float(k.time), float(k.azimuth), float(k.elevation), float(k.zoom)))


def serialize_keyframes(keyframes: Iterable[CameraKeyframe]) -> list[dict[str, Any]]:
    return [kf.to_dict() for kf in sort_keyframes(keyframes)]


def deserialize_keyframes(payload: Iterable[dict[str, Any]] | None) -> list[CameraKeyframe]:
    return sort_keyframes(CameraKeyframe.from_dict(item) for item in (payload or []))


def serialize_bookmarks(bookmarks: Iterable[Bookmark]) -> list[dict[str, Any]]:
    ordered = sorted((Bookmark(float(b.time), str(b.label), b.point_index) for b in bookmarks), key=lambda b: (float(b.time), str(b.label).casefold()))
    return [bookmark.to_dict() for bookmark in ordered]


def deserialize_bookmarks(payload: Iterable[dict[str, Any]] | None) -> list[Bookmark]:
    return [Bookmark.from_dict(item) for item in (payload or [])]


def serialize_regions(regions: Iterable[TimeRegion]) -> list[dict[str, Any]]:
    ordered = sorted((region.normalized() for region in regions), key=lambda r: (float(r.start), float(r.end), str(r.label).casefold()))
    return [region.to_dict() for region in ordered]


def deserialize_regions(payload: Iterable[dict[str, Any]] | None) -> list[TimeRegion]:
    return [TimeRegion.from_dict(item) for item in (payload or [])]


def _ease(frac: float, mode: str) -> float:
    frac = max(0.0, min(1.0, float(frac)))
    mode = str(mode or "ease_in_out")
    if mode == "linear":
        return frac
    if mode == "ease_in":
        return frac * frac
    if mode == "ease_out":
        return 1.0 - (1.0 - frac) * (1.0 - frac)
    # default ease_in_out
    return frac * frac * (3.0 - 2.0 * frac)


def camera_params_for_time(
    current_time: float,
    keyframes: Iterable[CameraKeyframe] | None,
    *,
    default_elevation: float,
    default_azimuth: float,
    default_zoom: float,
    autorotate: bool = False,
    rotation_speed: float = 0.0,
    easing: str = "ease_in_out",
    start_time: float = 0.0,
) -> tuple[float, float, float]:
    ordered = sort_keyframes(keyframes or [])
    if not ordered:
        azimuth = float(default_azimuth)
        if autorotate:
            azimuth += float(rotation_speed) * max(0.0, float(current_time) - float(start_time))
        return float(default_elevation), float(azimuth), float(default_zoom)

    now = float(current_time)
    if now <= ordered[0].time:
        first = ordered[0]
        return float(first.elevation), float(first.azimuth), float(first.zoom)
    if now >= ordered[-1].time:
        last = ordered[-1]
        return float(last.elevation), float(last.azimuth), float(last.zoom)

    prev = ordered[0]
    for nxt in ordered[1:]:
        if now <= nxt.time:
            span = max(1e-9, float(nxt.time) - float(prev.time))
            frac = _ease((now - float(prev.time)) / span, easing)
            elevation = float(prev.elevation) + (float(nxt.elevation) - float(prev.elevation)) * frac
            azimuth = float(prev.azimuth) + (float(nxt.azimuth) - float(prev.azimuth)) * frac
            zoom = float(prev.zoom) + (float(nxt.zoom) - float(prev.zoom)) * frac
            return elevation, azimuth, zoom
        prev = nxt
    last = ordered[-1]
    return float(last.elevation), float(last.azimuth), float(last.zoom)
