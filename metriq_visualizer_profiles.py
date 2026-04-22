# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

APP_DIR_NAME = "metriq_visualizer"
CONFIG_ROOT = Path.home() / ".config" / APP_DIR_NAME
RECENT_FILES_PATH = CONFIG_ROOT / "recent_files.json"
RECENT_PROJECTS_PATH = CONFIG_ROOT / "recent_projects.json"
RECENT_PRESETS_PATH = CONFIG_ROOT / "recent_presets.json"
CRASH_LOG_PATH = CONFIG_ROOT / "crash.log"
FREEZE_LOG_PATH = CONFIG_ROOT / "freeze.log"
DIAGNOSTIC_LOG_PATH = CONFIG_ROOT / "diagnostics.log"
PERF_LOG_PATH = CONFIG_ROOT / "perf.log"


def _safe_slug(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", " ") else "_" for ch in name).strip()
    cleaned = cleaned.replace(" ", "_")
    return cleaned or "profile"


class ProfileStore:
    """Minimal local state for the application.

    This store keeps lightweight runtime files such as recents and logs.
    """

    def __init__(self) -> None:
        CONFIG_ROOT.mkdir(parents=True, exist_ok=True)

    def list_profiles(self) -> list[str]:
        return []

    def save_profile(self, name: str, payload: dict[str, Any]) -> Path:
        raise RuntimeError("Profiles are not part of this application.")

    def load_profile(self, name: str) -> dict[str, Any] | None:
        return None

    def delete_profile(self, name: str) -> bool:
        return False

    def save_last_session(self, payload: dict[str, Any]) -> None:
        return None

    def load_last_session(self) -> dict[str, Any] | None:
        return None

    def save_recovery_session(self, payload: dict[str, Any]) -> None:
        return None

    def load_recovery_session(self) -> dict[str, Any] | None:
        return None

    def clear_recovery_session(self) -> None:
        return None

    def save_runtime_state(self, payload: dict[str, Any]) -> None:
        return None

    def load_runtime_state(self) -> dict[str, Any]:
        return {}

    def mark_runtime_dirty(self, dirty: bool, **extra: Any) -> None:
        return None

    def save_recent_files(self, files: list[str]) -> None:
        self._write_json(RECENT_FILES_PATH, {"files": files[:12]})

    def load_recent_files(self) -> list[str]:
        if not RECENT_FILES_PATH.exists():
            return []
        try:
            data = self._read_json(RECENT_FILES_PATH)
        except Exception:
            return []
        files = data.get("files", [])
        return [str(p) for p in files if isinstance(p, str)]

    def append_recent_file(self, path: str) -> list[str]:
        resolved = str(Path(path).expanduser())
        files = [resolved]
        for existing in self.load_recent_files():
            if existing != resolved:
                files.append(existing)
        files = [p for p in files if Path(p).exists()][:12]
        self.save_recent_files(files)
        return files

    def save_recent_projects(self, files: list[str]) -> None:
        self._write_json(RECENT_PROJECTS_PATH, {"files": files[:12]})

    def load_recent_projects(self) -> list[str]:
        if not RECENT_PROJECTS_PATH.exists():
            return []
        try:
            data = self._read_json(RECENT_PROJECTS_PATH)
        except Exception:
            return []
        files = data.get("files", [])
        return [str(p) for p in files if isinstance(p, str)]

    def append_recent_project(self, path: str) -> list[str]:
        resolved = str(Path(path).expanduser())
        files = [resolved]
        for existing in self.load_recent_projects():
            if existing != resolved:
                files.append(existing)
        files = [p for p in files if Path(p).exists()][:12]
        self.save_recent_projects(files)
        return files

    def save_recent_presets(self, files: list[str]) -> None:
        self._write_json(RECENT_PRESETS_PATH, {"files": files[:12]})

    def load_recent_presets(self) -> list[str]:
        if not RECENT_PRESETS_PATH.exists():
            return []
        try:
            data = self._read_json(RECENT_PRESETS_PATH)
        except Exception:
            return []
        files = data.get("files", [])
        return [str(p) for p in files if isinstance(p, str)]

    def append_recent_preset(self, path: str) -> list[str]:
        resolved = str(Path(path).expanduser())
        files = [resolved]
        for existing in self.load_recent_presets():
            if existing != resolved:
                files.append(existing)
        files = [p for p in files if Path(p).exists()][:12]
        self.save_recent_presets(files)
        return files

    def append_crash_log(self, text: str) -> None:
        self._append_text(CRASH_LOG_PATH, text)

    def append_freeze_log(self, text: str) -> None:
        self._append_text(FREEZE_LOG_PATH, text)

    def append_diagnostic_log(self, text: str) -> None:
        self._append_text(DIAGNOSTIC_LOG_PATH, text)

    def append_perf_log(self, text: str) -> None:
        self._append_text(PERF_LOG_PATH, text)

    def _append_text(self, path: Path, text: str) -> None:
        CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
            handle.write("\n")

    def _read_json(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        tmp.replace(path)
