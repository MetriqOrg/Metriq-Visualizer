# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Interactive quality tiers and load protection for Metriq Visualizer.

The live viewport and final export have different jobs.  The viewport must stay
responsive on ordinary computers while export should retain the creator's full
scene settings.  This module therefore applies *temporary, live-only* limits;
it never rewrites the saved visual configuration or the export configuration.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PerformanceProfile:
    """A predictable live-preview workload envelope."""

    name: str
    description: str
    target_fps: int
    point_budget: int
    curve_detail_cap: int
    tube_sides_cap: int
    allow_labels: bool
    allow_colorbar: bool
    allow_ghost_path: bool
    allow_tubes: bool
    allow_smoothing: bool
    allow_comet: bool
    pixel_ratio_cap: float | None

    @property
    def interval_ms(self) -> int:
        return max(16, round(1000 / max(1, self.target_fps)))


@dataclass(slots=True)
class AdaptivePreviewController:
    """Adjust only the live point budget when a host misses its frame target.

    The controller uses an exponentially weighted draw-time estimate.  It
    reduces density quickly when the renderer is over budget and restores it
    conservatively after sustained headroom.  Saved creator settings and final
    export density are never changed.
    """

    configured_budget: int = 1200
    target_fps: int = 12
    effective_budget: int = 1200
    ewma_draw_ms: float = 0.0
    _headroom_samples: int = 0

    def reset(self, configured_budget: int, target_fps: int) -> int:
        self.configured_budget = max(100, int(configured_budget))
        self.target_fps = max(5, min(60, int(target_fps)))
        self.effective_budget = self.configured_budget
        self.ewma_draw_ms = 0.0
        self._headroom_samples = 0
        return self.effective_budget

    @property
    def target_frame_ms(self) -> float:
        return 1000.0 / max(1, self.target_fps)

    def observe(self, draw_ms: float, *, enabled: bool = True) -> int:
        """Record one completed live draw and return the next point budget.

        Invalid samples are ignored.  Disabling adaptation immediately restores
        the configured budget.  Changes are rounded to 25 points to avoid
        constantly rebuilding nearly identical point sets.
        """

        if not enabled:
            self.effective_budget = self.configured_budget
            self.ewma_draw_ms = 0.0
            self._headroom_samples = 0
            return self.effective_budget

        sample = float(draw_ms)
        if not (sample > 0.0 and sample < 60_000.0):
            return self.effective_budget
        self.ewma_draw_ms = sample if self.ewma_draw_ms <= 0.0 else (0.78 * self.ewma_draw_ms + 0.22 * sample)
        target = self.target_frame_ms
        minimum = min(self.configured_budget, max(150, int(round(self.configured_budget * 0.20))))

        if self.ewma_draw_ms > target * 1.10 and self.effective_budget > minimum:
            pressure = target / max(self.ewma_draw_ms, 1e-6)
            factor = max(0.62, min(0.90, pressure * 0.94))
            proposed = max(minimum, int(self.effective_budget * factor))
            self.effective_budget = max(minimum, (proposed // 25) * 25)
            self._headroom_samples = 0
        elif self.ewma_draw_ms < target * 0.62 and self.effective_budget < self.configured_budget:
            self._headroom_samples += 1
            if self._headroom_samples >= 6:
                proposed = max(self.effective_budget + 25, int(round(self.effective_budget * 1.12)))
                self.effective_budget = min(self.configured_budget, ((proposed + 24) // 25) * 25)
                self._headroom_samples = 0
        else:
            self._headroom_samples = 0
        return self.effective_budget


PERFORMANCE_PROFILES: dict[str, PerformanceProfile] = {
    "Fast preview": PerformanceProfile(
        name="Fast preview",
        description="Lowest latency: bounded spline detail and motion accents, without tubes, labels, colorbar, or ghost path.",
        target_fps=15,
        point_budget=650,
        curve_detail_cap=2,
        tube_sides_cap=5,
        allow_labels=False,
        allow_colorbar=False,
        allow_ghost_path=False,
        allow_tubes=False,
        allow_smoothing=True,
        allow_comet=True,
        pixel_ratio_cap=1.0,
    ),
    "Balanced": PerformanceProfile(
        name="Balanced",
        description="Recommended playback proxy: bounded smoothing, density, and temporal accents.",
        target_fps=12,
        point_budget=1200,
        curve_detail_cap=4,
        tube_sides_cap=6,
        allow_labels=False,
        allow_colorbar=False,
        allow_ghost_path=False,
        allow_tubes=False,
        allow_smoothing=True,
        allow_comet=True,
        pixel_ratio_cap=1.25,
    ),
    "High quality": PerformanceProfile(
        name="High quality",
        description="Detailed playback proxy with higher spline detail, density, point labels, and colorbar support.",
        target_fps=8,
        point_budget=1800,
        curve_detail_cap=8,
        tube_sides_cap=8,
        allow_labels=True,
        allow_colorbar=True,
        allow_ghost_path=False,
        allow_tubes=False,
        allow_smoothing=True,
        allow_comet=True,
        pixel_ratio_cap=1.5,
    ),
    "Full live simulation": PerformanceProfile(
        name="Full live simulation",
        description="Highest-density moving scene; exact polygonal tube surfaces remain a paused and export operation.",
        target_fps=5,
        point_budget=6000,
        curve_detail_cap=24,
        tube_sides_cap=24,
        allow_labels=True,
        allow_colorbar=True,
        allow_ghost_path=True,
        allow_tubes=True,
        allow_smoothing=True,
        allow_comet=True,
        pixel_ratio_cap=None,
    ),
}

DEFAULT_PERFORMANCE_PROFILE = "Balanced"


def normalize_profile_name(value: Any) -> str:
    """Map current and historical profile labels to a supported tier."""

    text = str(value or "").strip().casefold()
    aliases = {
        "draft": "Fast preview",
        "fast": "Fast preview",
        "preview": "Fast preview",
        "fast preview": "Fast preview",
        "balanced": "Balanced",
        "normal": "Balanced",
        "recommended": "Balanced",
        "quality": "High quality",
        "high": "High quality",
        "high quality": "High quality",
        "ultra": "Full live simulation",
        "render": "Full live simulation",
        "full": "Full live simulation",
        "full live": "Full live simulation",
        "full live simulation": "Full live simulation",
    }
    return aliases.get(text, DEFAULT_PERFORMANCE_PROFILE)


def profile_for(value: Any) -> PerformanceProfile:
    return PERFORMANCE_PROFILES[normalize_profile_name(value)]


def apply_live_limits(
    options: Any,
    profile: PerformanceProfile,
    *,
    point_budget: int | None = None,
    target_fps: int | None = None,
) -> tuple[Any, int, int]:
    """Return a shallow live-only options copy and effective workload limits.

    Export receives the original options object.  The copy only suppresses
    expensive optional artists and caps interpolation/tube complexity while the
    user is interacting with the desktop viewport.
    """

    live = copy(options)
    live.curve_detail = min(max(1, int(getattr(live, "curve_detail", 1))), profile.curve_detail_cap)
    live.tube_sides = min(max(3, int(getattr(live, "tube_sides", 3))), profile.tube_sides_cap)
    if not profile.allow_labels:
        live.point_label_mode = "Off"
    if not profile.allow_colorbar:
        live.show_colorbar = False
    if not profile.allow_ghost_path:
        live.ghost_path = False
    if not profile.allow_tubes and "tube" in str(getattr(live, "render_mode", "")).casefold():
        live.render_mode = "Points + line"
    if not profile.allow_smoothing:
        live.path_curve_mode = "Straight"
        live.curve_detail = 1
    if not profile.allow_comet:
        live.comet_duration = 0.0
        live.flash_duration = 0.0
    effective_points = max(100, int(point_budget if point_budget is not None else profile.point_budget))
    effective_fps = max(5, min(60, int(target_fps if target_fps is not None else profile.target_fps)))
    return live, effective_points, effective_fps


__all__ = [
    "AdaptivePreviewController",
    "DEFAULT_PERFORMANCE_PROFILE",
    "PERFORMANCE_PROFILES",
    "PerformanceProfile",
    "apply_live_limits",
    "normalize_profile_name",
    "profile_for",
]
