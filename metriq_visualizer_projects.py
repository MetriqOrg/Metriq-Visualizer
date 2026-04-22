# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_SCHEMA_VERSION = 3
PROJECT_FORMAT = "mvproj"
PROJECT_EXTENSION = ".mvproj"
LEGACY_PROJECT_EXTENSIONS = (".bgl",)
PROJECT_EXTENSIONS = (PROJECT_EXTENSION,) + LEGACY_PROJECT_EXTENSIONS
BACKUP_SUFFIX = ".bak"


def _resolve_source_path(project_path: Path, session: dict[str, Any]) -> dict[str, Any]:
    session = dict(session or {})
    rel = session.get("file_path_relative")
    abs_path = session.get("file_path")
    candidates: list[Path] = []
    if isinstance(rel, str) and rel.strip():
        candidates.append((project_path.parent / rel).expanduser())
    if isinstance(abs_path, str) and abs_path.strip():
        candidates.append(Path(abs_path).expanduser())
    for candidate in candidates:
        try:
            if candidate.exists():
                session["file_path"] = str(candidate.resolve())
                return session
        except Exception:
            continue
    return session


def build_project_payload(project_name: str, state_payload: dict[str, Any], project_path: str | Path | None = None) -> dict[str, Any]:
    payload = {
        "format": PROJECT_FORMAT,
        "project_name": str(project_name or "Untitled project").strip() or "Untitled project",
        "project_schema_version": PROJECT_SCHEMA_VERSION,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "state": dict(state_payload),
    }
    if project_path is not None:
        project_file = Path(project_path).expanduser()
        session = dict(payload["state"].get("session") or {})
        source_path = session.get("file_path")
        if isinstance(source_path, str) and source_path.strip():
            try:
                source = Path(source_path).expanduser().resolve()
                session["file_path"] = str(source)
                session["file_path_relative"] = str(source.relative_to(project_file.parent.resolve()))
            except Exception:
                try:
                    source = Path(source_path).expanduser().resolve()
                    session["file_path"] = str(source)
                except Exception:
                    pass
            payload["state"]["session"] = session
    return payload


def save_project(path: str | Path, payload: dict[str, Any]) -> Path:
    path = Path(path).expanduser()
    if path.suffix.lower() != PROJECT_EXTENSION:
        path = path.with_suffix(PROJECT_EXTENSION)
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


def load_project(path: str | Path) -> dict[str, Any]:
    path = Path(path).expanduser()
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    state = dict(payload.get("state") or {})
    session = _resolve_source_path(path, state.get("session") or {})
    state["session"] = session
    payload["state"] = state
    payload["project_path"] = str(path.resolve())
    return payload
