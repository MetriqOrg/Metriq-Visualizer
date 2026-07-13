# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Portable mapping/appearance preset files with legacy compatibility."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from metriq_visualizer_atomic import atomic_write_text
from metriq_visualizer_performance import DEFAULT_PERFORMANCE_PROFILE, normalize_profile_name

PRESET_EXTENSION = ".mvpreset"
PRESET_SCHEMA = "metriq.visualizer-preset"
PRESET_SCHEMA_VERSION = 4

_METADATA_KEYS = {
    "app",
    "app_version",
    "created_at",
    "format",
    "name",
    "preset_name",
    "preset_schema_version",
    "saved_at_utc",
    "schema",
    "schema_version",
}


def _mapping(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def normalize_preset_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Translate v1.10-era and current preset state into the v1.12 schema.

    Historical presets separated extraction, mapping, motion, performance,
    visual, export and UI sections.  v1.12.0 accidentally retained only a
    smaller subset.  Normalization is additive and keeps unknown sections so a
    newer application can still round-trip creator-authored data.
    """

    result = deepcopy(dict(state))
    result.pop("session", None)

    mapping = _mapping(result.get("mapping"))
    if "preset" not in mapping and "preset_name" in mapping:
        mapping["preset"] = mapping.get("preset_name")
    result["mapping"] = mapping

    extraction = _mapping(result.get("extraction"))
    extraction_aliases = {
        "sample_rate_hz": "sample_rate",
        "fft_size": "n_fft",
        "hop": "hop_length",
        "fmin": "min_frequency",
        "minimum_frequency": "min_frequency",
        "fmax": "max_frequency",
        "maximum_frequency": "max_frequency",
        "max_analysis_frames": "max_frames",
        "mel_bands": "n_mels",
        "n_mfcc": "mfcc_count",
    }
    for old_key, current_key in extraction_aliases.items():
        if current_key not in extraction and old_key in extraction:
            extraction[current_key] = extraction.get(old_key)
    frequency_range = extraction.get("frequency_range")
    if isinstance(frequency_range, (list, tuple)) and len(frequency_range) >= 2:
        extraction.setdefault("min_frequency", frequency_range[0])
        extraction.setdefault("max_frequency", frequency_range[1])
    result["extraction"] = extraction

    geometry = _mapping(result.get("geometry"))
    if "normalize_mode" not in geometry and "normalize" in mapping:
        geometry["normalize_mode"] = mapping.get("normalize")
    if "colormap" not in geometry and "colormap" in mapping:
        geometry["colormap"] = mapping.get("colormap")
    if "max_points" not in geometry and "max_points" in extraction:
        geometry["max_points"] = extraction.get("max_points")
    if "low_volume_cutoff_db" not in geometry and "low_volume_cutoff_db" in extraction:
        geometry["low_volume_cutoff_db"] = extraction.get("low_volume_cutoff_db")
    result["geometry"] = geometry

    visual = _mapping(result.get("visual"))
    if "base_alpha" not in visual and "alpha" in visual:
        visual["base_alpha"] = visual.get("alpha")
    motion = _mapping(result.get("motion"))
    motion_aliases = {
        "elevation": "elev",
        "base_azimuth": "azim",
        "zoom": "zoom",
        "autorotate": "autorotate",
        "rotation_speed": "rotation_speed",
    }
    for old_key, current_key in motion_aliases.items():
        if current_key not in visual and old_key in motion:
            visual[current_key] = motion.get(old_key)
    result["visual"] = visual

    performance = _mapping(result.get("performance"))
    performance["mode"] = normalize_profile_name(performance.get("mode", DEFAULT_PERFORMANCE_PROFILE))
    result["performance"] = performance

    export = _mapping(result.get("export"))
    if "layout" not in result and isinstance(export.get("layout"), Mapping):
        result["layout"] = deepcopy(dict(export["layout"]))
    layout = _mapping(result.get("layout"))
    # v1.10 called the edge-to-edge image mode ``fill``.  The current layout
    # model calls the equivalent mode ``stretch``.  Translate it recursively
    # so old creator profiles remain valid when loaded directly.
    for layer in layout.values():
        if isinstance(layer, Mapping) and str(layer.get("fit_mode", "")).casefold() == "fill":
            layer["fit_mode"] = "stretch"
    if layout:
        result["layout"] = layout

    return result


def build_preset_payload(name: str, state: Mapping[str, Any]) -> dict[str, Any]:
    clean = normalize_preset_state(state)
    return {
        "schema": PRESET_SCHEMA,
        "schema_version": PRESET_SCHEMA_VERSION,
        "name": str(name or "Metriq Visualizer Preset").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": clean,
    }


def save_preset(path: str | Path, payload: Mapping[str, Any]) -> Path:
    output = Path(path).expanduser()
    if output.suffix.lower() != PRESET_EXTENSION:
        output = output.with_suffix(PRESET_EXTENSION)
    atomic_write_text(
        output,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return output.resolve()


def load_preset(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("Preset file must contain a JSON object.")
    schema = str(payload.get("schema", ""))
    if schema and schema != PRESET_SCHEMA:
        raise ValueError("This JSON file is not a Metriq Visualizer preset.")
    version_value = payload.get("schema_version", payload.get("preset_schema_version", 1))
    version = int(version_value or 1)
    if schema == PRESET_SCHEMA and version > PRESET_SCHEMA_VERSION:
        raise ValueError(f"Preset schema {version} is newer than this application supports.")
    state = payload.get("state")
    if not isinstance(state, Mapping):
        # Compatibility with early files that stored state at the root.
        state = {key: value for key, value in payload.items() if key not in _METADATA_KEYS}
    result = deepcopy(dict(payload))
    result["state"] = normalize_preset_state(state)
    result["source_path"] = str(source)
    return result


def preset_display_name(payload: Mapping[str, Any], fallback: str = "Preset") -> str:
    for key in ("name", "preset_name"):
        value = str(payload.get(key, "")).strip()
        if value:
            return value.replace("_", " ")
    return str(fallback or "Preset").replace("_", " ")


def default_preset_directories() -> tuple[Path, ...]:
    directories: list[Path] = []
    environment = os.environ.get("METRIQ_PRESET_PATH", "")
    for entry in environment.split(os.pathsep):
        if entry.strip():
            directories.append(Path(entry).expanduser())
    # User presets take precedence over bundled presets with the same display
    # name.  This allows repository upgrades without erasing creator choices.
    directories.append(Path.home() / ".metriq_visualizer" / "presets")
    directories.append(Path(__file__).resolve().parent / "presets")
    return tuple(directories)


def discover_presets(directories: Iterable[str | Path] | None = None) -> dict[str, Path]:
    """Return readable preset files keyed by their user-facing name."""

    found: dict[str, Path] = {}
    sources = directories if directories is not None else default_preset_directories()
    for directory_value in sources:
        directory = Path(directory_value).expanduser()
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob(f"*{PRESET_EXTENSION}"), key=lambda item: item.name.casefold()):
            try:
                payload = load_preset(path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            name = preset_display_name(payload, path.stem)
            found.setdefault(name, path.resolve())
    return found


__all__ = [
    "PRESET_EXTENSION",
    "PRESET_SCHEMA",
    "PRESET_SCHEMA_VERSION",
    "build_preset_payload",
    "default_preset_directories",
    "discover_presets",
    "load_preset",
    "normalize_preset_state",
    "preset_display_name",
    "save_preset",
]
