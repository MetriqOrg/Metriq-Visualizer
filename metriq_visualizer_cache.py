# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Disk-backed analysis cache for responsive repeat workflows.

The cache stores derived arrays only.  It never copies a user's source media,
and a source fingerprint invalidates stale entries automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from metriq_visualizer_core import (
    ANALYSIS_ENGINE_VERSION,
    AnalysisResult,
    AnalysisSettings,
    analysis_from_table_file,
    analyze_media,
    is_table_file,
)

CACHE_SCHEMA = "metriq.analysis-cache"
CACHE_VERSION = 5
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    path: str
    size: int
    modified_ns: int
    sample_hash: str

    def key(self) -> str:
        payload = f"{self.path}\0{self.size}\0{self.modified_ns}\0{self.sample_hash}".encode("utf-8", errors="surrogatepass")
        return hashlib.sha256(payload).hexdigest()


def cache_directory() -> Path:
    configured = os.environ.get("METRIQ_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    system = platform.system()
    if system == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Metriq" / "Visualizer" / "Cache" / "analysis"
    if system == "Darwin":
        return Path.home() / "Library" / "Caches" / "Metriq Visualizer" / "analysis"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "metriq-visualizer" / "analysis"


def fingerprint_source(path: str | Path) -> SourceFingerprint:
    source = Path(path).expanduser().resolve()
    stat = source.stat()
    digest = hashlib.sha256()
    chunk = 256 * 1024
    with source.open("rb") as handle:
        digest.update(handle.read(chunk))
        if stat.st_size > chunk * 2:
            handle.seek(max(0, (stat.st_size // 2) - (chunk // 2)))
            digest.update(handle.read(chunk))
        if stat.st_size > chunk:
            handle.seek(max(0, stat.st_size - chunk))
            digest.update(handle.read(chunk))
    return SourceFingerprint(str(source), int(stat.st_size), int(stat.st_mtime_ns), digest.hexdigest())


def _cache_path(
    fingerprint: SourceFingerprint,
    root: Path | None = None,
    settings: AnalysisSettings | None = None,
) -> Path:
    settings_key = settings.normalized().signature() if settings is not None else "source-default"
    digest = hashlib.sha256(f"{fingerprint.key()}\0{settings_key}".encode()).hexdigest()
    return (root or cache_directory()) / f"{digest}.npz"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return str(value)


def save_cached_analysis(
    result: AnalysisResult,
    fingerprint: SourceFingerprint | None = None,
    *,
    root: Path | None = None,
    settings: AnalysisSettings | None = None,
) -> Path:
    source_fingerprint = fingerprint or fingerprint_source(result.source_path)
    configured = settings or AnalysisSettings.from_mapping(result.metadata.get("analysis_settings"))
    output = _cache_path(source_fingerprint, root, configured)
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema": CACHE_SCHEMA,
        "schema_version": CACHE_VERSION,
        "fingerprint": {
            "path": source_fingerprint.path,
            "size": source_fingerprint.size,
            "modified_ns": source_fingerprint.modified_ns,
            "sample_hash": source_fingerprint.sample_hash,
        },
        "analysis_engine_version": ANALYSIS_ENGINE_VERSION,
        "analysis_settings": configured.normalized().to_dict(),
        "source_kind": result.source_kind,
        "duration": result.duration,
        "sample_rate": result.sample_rate,
        "audio_path": str(result.audio_path) if result.audio_path else "",
        "has_video": bool(result.has_video),
        "metadata": _json_safe({key: value for key, value in result.metadata.items() if key != "cache_hit"}),
        "feature_descriptions": _json_safe(result.feature_descriptions),
        "feature_names": list(result.features),
        "created_at": time.time(),
    }
    arrays: dict[str, Any] = {
        "__metadata__": np.asarray(json.dumps(metadata, separators=(",", ":"))),
        "times": result.times,
        "spectrogram": result.spectrogram,
        "spectrogram_frequencies": result.spectrogram_frequencies,
        "chromagram": result.chromagram,
        "mfcc": result.mfcc,
        "waveform": result.waveform,
    }
    for index, (_name, values) in enumerate(result.features.items()):
        arrays[f"feature_{index:04d}"] = values
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.stem}.",
        suffix=".tmp.npz",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        np.savez_compressed(temporary, **arrays)
        temporary.replace(output)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return output


def load_cached_analysis(
    path: str | Path,
    fingerprint: SourceFingerprint | None = None,
    *,
    root: Path | None = None,
    settings: AnalysisSettings | None = None,
) -> AnalysisResult | None:
    source = Path(path).expanduser().resolve()
    source_fingerprint = fingerprint or fingerprint_source(source)
    configured = settings or AnalysisSettings()
    cache_path = _cache_path(source_fingerprint, root, configured)
    if not cache_path.is_file():
        return None
    try:
        with np.load(cache_path, allow_pickle=False) as archive:
            metadata = json.loads(str(archive["__metadata__"].item()))
            if (
                metadata.get("schema") != CACHE_SCHEMA
                or int(metadata.get("schema_version", 0)) != CACHE_VERSION
                or str(metadata.get("analysis_engine_version", "")) != ANALYSIS_ENGINE_VERSION
            ):
                return None
            saved_settings = AnalysisSettings.from_mapping(metadata.get("analysis_settings"))
            if saved_settings.signature() != configured.normalized().signature():
                return None
            saved = metadata.get("fingerprint", {})
            if (
                str(saved.get("path")) != source_fingerprint.path
                or int(saved.get("size", -1)) != source_fingerprint.size
                or int(saved.get("modified_ns", -1)) != source_fingerprint.modified_ns
                or str(saved.get("sample_hash", "")) != source_fingerprint.sample_hash
            ):
                return None
            names = list(metadata.get("feature_names", []))
            features = {str(name): np.asarray(archive[f"feature_{index:04d}"], dtype=np.float32) for index, name in enumerate(names)}
            extra_metadata = dict(metadata.get("metadata", {}))
            extra_metadata["cache_hit"] = True
            result = AnalysisResult(
                source_path=source,
                source_kind=str(metadata.get("source_kind", "media")),
                times=np.asarray(archive["times"], dtype=np.float32),
                duration=float(metadata.get("duration", 0.0)),
                features=features,
                sample_rate=int(metadata.get("sample_rate", 0)),
                audio_path=Path(str(metadata["audio_path"])) if str(metadata.get("audio_path", "")) else None,
                has_video=bool(metadata.get("has_video", False)),
                spectrogram=np.asarray(archive["spectrogram"], dtype=np.float32),
                spectrogram_frequencies=np.asarray(archive["spectrogram_frequencies"], dtype=np.float32),
                chromagram=np.asarray(archive["chromagram"], dtype=np.float32),
                mfcc=np.asarray(archive["mfcc"], dtype=np.float32),
                waveform=np.asarray(archive["waveform"], dtype=np.float32),
                metadata=extra_metadata,
                feature_descriptions=dict(metadata.get("feature_descriptions", {})),
            )
        with suppress(OSError):
            os.utime(cache_path, None)
        return result
    except Exception:
        with suppress(OSError):
            cache_path.unlink(missing_ok=True)
        return None


def analyze_source_cached(
    path: str | Path,
    *,
    use_cache: bool = True,
    settings: AnalysisSettings | None = None,
) -> AnalysisResult:
    source = Path(path).expanduser().resolve()
    fingerprint = fingerprint_source(source)
    table_source = is_table_file(source)
    configured = None if table_source else (settings or AnalysisSettings()).normalized()
    if use_cache:
        cached = load_cached_analysis(source, fingerprint, settings=configured)
        if cached is not None:
            return cached
    result = analysis_from_table_file(source) if table_source else analyze_media(source, configured)
    result.metadata["cache_hit"] = False
    if use_cache:
        try:
            save_cached_analysis(result, fingerprint, settings=configured)
            prune_cache()
        except Exception:
            # A cache write is an optional optimization and must never invalidate
            # an otherwise successful local analysis.
            pass
    return result


def prune_cache(*, root: Path | None = None, max_bytes: int = DEFAULT_MAX_BYTES) -> int:
    directory = root or cache_directory()
    if not directory.exists():
        return 0
    files = []
    total = 0
    for path in directory.glob("*.npz"):
        try:
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        files.append((stat.st_atime_ns, stat.st_mtime_ns, stat.st_size, path))
    removed = 0
    for _access, _modified, size, path in sorted(files):
        if total <= max(0, int(max_bytes)):
            break
        try:
            path.unlink()
            total -= size
            removed += 1
        except OSError:
            pass
    return removed


def clear_cache(*, root: Path | None = None) -> int:
    directory = root or cache_directory()
    removed = 0
    if directory.exists():
        for path in directory.glob("*.npz"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


__all__ = [
    "CACHE_SCHEMA",
    "CACHE_VERSION",
    "DEFAULT_MAX_BYTES",
    "SourceFingerprint",
    "analyze_source_cached",
    "cache_directory",
    "clear_cache",
    "fingerprint_source",
    "load_cached_analysis",
    "prune_cache",
    "save_cached_analysis",
]
