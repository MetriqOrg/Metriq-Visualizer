# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from copy import deepcopy
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from metriq_visualizer_atomic import atomic_write_text

PRESET_SCHEMA_VERSION = 1
PRESET_FORMAT = "mvpreset"
PRESET_EXTENSION = ".mvpreset"
PRESET_EXTENSIONS = (PRESET_EXTENSION,)
BACKUP_SUFFIX = ".bak"


def build_preset_payload(preset_name: str, state_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": PRESET_FORMAT,
        "preset_name": str(preset_name or "Untitled preset").strip() or "Untitled preset",
        "preset_schema_version": PRESET_SCHEMA_VERSION,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "state": dict(state_payload or {}),
    }


def save_preset(path: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(path).expanduser()
    if path.suffix.lower() != PRESET_EXTENSION:
        path = path.with_suffix(PRESET_EXTENSION)

    if path.exists():
        backup_path = path.with_suffix(path.suffix + BACKUP_SUFFIX)
        try:
            shutil.copy2(path, backup_path)
        except Exception:
            pass

    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True))
    return path


def load_preset(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Preset file is not a JSON object.")
    file_format = payload.get("format")
    if file_format not in (None, PRESET_FORMAT):
        raise ValueError("This JSON file is not a Metriq Visualizer preset.")
    try:
        version = int(payload.get("preset_schema_version", PRESET_SCHEMA_VERSION) or PRESET_SCHEMA_VERSION)
    except (TypeError, ValueError) as exc:
        raise ValueError("Preset schema version is invalid.") from exc
    if version > PRESET_SCHEMA_VERSION:
        raise ValueError(f"Preset schema {version} is newer than this application supports.")
    state = payload.get("state")
    if state is None:
        metadata = {"app", "app_version", "created_at", "format", "preset_name", "preset_schema_version", "saved_at_utc"}
        state = {key: value for key, value in payload.items() if key not in metadata}
    if not isinstance(state, dict):
        raise ValueError("Preset state is missing or invalid.")
    # Deep-copy without translating or dropping creator-authored sections.
    payload = deepcopy(payload)
    payload["preset_path"] = str(path.resolve())
    payload["state"] = deepcopy(state)
    return payload


def default_preset_directories() -> tuple[Path, ...]:
    directories = [Path(item).expanduser() for item in os.environ.get("METRIQ_PRESET_PATH", "").split(os.pathsep) if item.strip()]
    directories.extend((Path.home() / ".metriq_visualizer" / "presets", Path(__file__).resolve().parent / "presets"))
    return tuple(directories)


def preset_display_name(payload: Mapping[str, Any], fallback: str = "Preset") -> str:
    for key in ("preset_name", "name"):
        value = str(payload.get(key, "")).strip()
        if value:
            return value.replace("_", " ")
    return str(fallback or "Preset").replace("_", " ")


def discover_presets(directories: Iterable[str | Path] | None = None) -> dict[str, Path]:
    """Discover readable presets; earlier (user) directories win name collisions."""
    found: dict[str, Path] = {}
    for value in directories if directories is not None else default_preset_directories():
        directory = Path(value).expanduser()
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob(f"*{PRESET_EXTENSION}"), key=lambda item: item.name.casefold()):
            try:
                payload = load_preset(path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            found.setdefault(preset_display_name(payload, path.stem), path.resolve())
    return found
