# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
# If a copy of the MPL was not distributed with this file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class ExportQueueJob:
    job_id: int
    label: str
    source_path: str
    output_path: str
    state: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = dict(self.state or {})
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExportQueueJob":
        data = data or {}
        return cls(
            job_id=int(data.get("job_id", 0)),
            label=str(data.get("label", "Queued export") or "Queued export"),
            source_path=str(data.get("source_path", "") or ""),
            output_path=str(data.get("output_path", "") or ""),
            state=dict(data.get("state") or {}),
        )

    def display_label(self) -> str:
        stem = Path(self.output_path).name if self.output_path else "queued.mp4"
        source = Path(self.source_path).name if self.source_path else "unknown"
        return f"#{self.job_id} • {self.label} • {source} → {stem}"


def serialize_queue(jobs: list[ExportQueueJob]) -> list[dict[str, Any]]:
    return [job.to_dict() for job in jobs]


def deserialize_queue(payload: list[dict[str, Any]] | None) -> list[ExportQueueJob]:
    return [ExportQueueJob.from_dict(item) for item in (payload or [])]
