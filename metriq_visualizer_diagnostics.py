# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np


@dataclass
class BaselineModel:
    feature_names: list[str]
    mean: np.ndarray
    std: np.ndarray
    created_at: float
    window_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "mean": np.asarray(self.mean, dtype=np.float64).tolist(),
            "std": np.asarray(self.std, dtype=np.float64).tolist(),
            "created_at": float(self.created_at),
            "window_seconds": float(self.window_seconds),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "BaselineModel | None":
        payload = payload or {}
        names = payload.get("feature_names") or []
        mean = np.asarray(payload.get("mean") or [], dtype=np.float64)
        std = np.asarray(payload.get("std") or [], dtype=np.float64)
        if not names or mean.size == 0 or std.size == 0 or mean.size != std.size or len(names) != mean.size:
            return None
        return cls(
            feature_names=[str(name) for name in names],
            mean=mean,
            std=np.maximum(std, 1e-4),
            created_at=float(payload.get("created_at", time.time())),
            window_seconds=float(payload.get("window_seconds", 0.0)),
        )


class LiveDiagnosticsEngine:
    """Stubbed diagnostics engine for this release."""

    def __init__(
        self,
        *,
        sample_rate: int = 22050,
        frame_size: int = 2048,
        hop_length: int = 1024,
        history_seconds: float = 30.0,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.frame_size = int(frame_size)
        self.hop_length = int(hop_length)
        self.history_seconds = float(history_seconds)
        self.gate_db = 15.0
        self.warn_threshold = 2.0
        self.alarm_threshold = 4.0
        self.min_event_seconds = 0.55
        self.is_running = False
        self._baseline: BaselineModel | None = None

    def configure(
        self,
        *,
        history_seconds: float | None = None,
        gate_db: float | None = None,
        warn_threshold: float | None = None,
        alarm_threshold: float | None = None,
        min_event_seconds: float | None = None,
    ) -> None:
        if history_seconds is not None:
            self.history_seconds = float(history_seconds)
        if gate_db is not None:
            self.gate_db = float(gate_db)
        if warn_threshold is not None:
            self.warn_threshold = float(warn_threshold)
        if alarm_threshold is not None:
            self.alarm_threshold = float(alarm_threshold)
        if min_event_seconds is not None:
            self.min_event_seconds = float(min_event_seconds)

    def start(self, *_args, **_kwargs) -> None:
        self.is_running = False

    def stop(self) -> None:
        self.is_running = False

    def set_baseline(self, baseline: BaselineModel | None) -> None:
        self._baseline = baseline

    def capture_baseline(self, window_seconds: float) -> BaselineModel | None:
        _ = float(window_seconds)
        return self._baseline

    def clear_baseline(self) -> None:
        self._baseline = None

    def clear_events(self) -> None:
        return None

    def snapshot(self, max_points: int = 1200) -> dict[str, Any]:
        _ = int(max_points)
        return {
            "times": [],
            "scores": [],
            "events": [],
            "features": {},
            "current_score": 0.0,
            "current_status": "Unavailable",
            "status": "Unavailable",
            "baseline": self._baseline is not None,
            "overflow_count": 0,
            "current_time": 0.0,
            "active_mask": [],
        }


def available_input_devices() -> list[dict[str, Any]]:
    return []


def serialize_baseline_model(model: BaselineModel | None) -> dict[str, Any] | None:
    return None if model is None else model.to_dict()


def deserialize_baseline_model(payload: dict[str, Any] | None) -> BaselineModel | None:
    return BaselineModel.from_dict(payload)


def snapshot_to_analysis_geometry(
    snapshot: dict[str, Any],
    *,
    sample_rate: int,
    max_points: int,
    colormap: str = "plasma",
):
    _ = (snapshot, sample_rate, max_points, colormap)
    return None
