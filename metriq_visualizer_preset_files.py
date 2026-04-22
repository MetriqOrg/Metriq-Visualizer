# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        backup_path = path.with_suffix(path.suffix + BACKUP_SUFFIX)
        try:
            shutil.copy2(path, backup_path)
        except Exception:
            pass

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    tmp_path.replace(path)
    return path


def load_preset(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Preset file is not a JSON object.")
    state = payload.get("state") or {}
    if not isinstance(state, dict):
        raise ValueError("Preset state is missing or invalid.")
    payload["preset_path"] = str(path.resolve())
    payload["state"] = dict(state)
    return payload
