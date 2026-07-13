# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Portable Metriq Visualizer project persistence."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from metriq_visualizer_atomic import atomic_write_text

PROJECT_EXTENSION = ".mvproj"
LEGACY_PROJECT_EXTENSIONS = (".bgl",)
PROJECT_SCHEMA = "metriq.visualizer-project"
PROJECT_SCHEMA_VERSION = 2


def _json_write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def build_project_payload(name: str, state: Mapping[str, Any], *, project_path: str | Path | None = None) -> dict[str, Any]:
    clean_state = deepcopy(dict(state))
    relative_source = ""
    if project_path:
        project = Path(project_path).expanduser().resolve()
        session = clean_state.get("session")
        if isinstance(session, dict):
            source_text = str(session.get("file_path", "")).strip()
            if source_text:
                source = Path(source_text).expanduser().resolve()
                try:
                    # ``relative_to`` only works for descendants. ``relpath``
                    # also preserves sibling layouts such as ../media/source.wav.
                    relative_source = os.path.relpath(source, project.parent)
                except ValueError:
                    # Different Windows drives cannot be represented as one
                    # relative path; retain the absolute session path instead.
                    relative_source = ""
    return {
        "schema": PROJECT_SCHEMA,
        "schema_version": PROJECT_SCHEMA_VERSION,
        "name": str(name or "Metriq Visualizer Project").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "relative_source": relative_source,
        "state": clean_state,
    }


def save_project(path: str | Path, payload: Mapping[str, Any]) -> Path:
    output = Path(path).expanduser()
    if output.suffix.lower() != PROJECT_EXTENSION:
        output = output.with_suffix(PROJECT_EXTENSION)
    _json_write_atomic(output, payload)
    return output.resolve()


def load_project(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("Project file must contain a JSON object.")
    schema = str(payload.get("schema", ""))
    if schema and schema != PROJECT_SCHEMA:
        raise ValueError("This JSON file is not a Metriq Visualizer project.")
    version = int(payload.get("schema_version", 1))
    if version > PROJECT_SCHEMA_VERSION:
        raise ValueError(f"Project schema {version} is newer than this application supports.")
    state = payload.get("state", payload if source.suffix.lower() in LEGACY_PROJECT_EXTENSIONS else None)
    if not isinstance(state, Mapping):
        raise ValueError("Project state is missing or invalid.")
    result = dict(payload)
    result["state"] = deepcopy(dict(state))

    # Resolve a portable source reference before falling back to an absolute path.
    relative = str(payload.get("relative_source", "")).strip()
    session = result["state"].get("session")
    if isinstance(session, dict) and relative:
        candidate = Path(relative).expanduser() if relative.startswith("~") else (source.parent / relative)
        if candidate.exists():
            session["file_path"] = str(candidate.resolve())
    return result


__all__ = [
    "LEGACY_PROJECT_EXTENSIONS",
    "PROJECT_EXTENSION",
    "PROJECT_SCHEMA",
    "PROJECT_SCHEMA_VERSION",
    "build_project_payload",
    "load_project",
    "save_project",
]
