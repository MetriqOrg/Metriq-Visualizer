# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Disk cache for analysis results, enabled by the public media analyzer."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from metriq_visualizer_core import AnalysisResult, _analyze_media_uncached, analysis_from_table_file, is_table_file

CACHE_SCHEMA = "metriq.analysis-cache"
CACHE_VERSION = 2
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
ANALYSIS_ENGINE_VERSION = "1.10.18-compatible-dsp-v1"


@dataclass(frozen=True)
class AnalysisSettings:
    """All configurable inputs that alter feature extraction in v1.10.18."""
    sample_rate: int = 22050
    n_fft: int = 2048
    hop_length: int = 256

    @classmethod
    def from_mapping(cls, value: dict | None) -> "AnalysisSettings":
        value = value or {}
        return cls(sample_rate=int(value.get("sample_rate", 22050)), n_fft=int(value.get("n_fft", 2048)), hop_length=int(value.get("hop_length", 256)))

    def normalized(self) -> "AnalysisSettings":
        return AnalysisSettings(max(1, self.sample_rate), max(2, self.n_fft), max(1, self.hop_length))

    def signature(self) -> str:
        return json.dumps(asdict(self.normalized()), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    size: int
    modified_ns: int
    sample_hash: str

    def key(self) -> str:
        raw = f"{self.path}\0{self.size}\0{self.modified_ns}\0{self.sample_hash}".encode("utf-8", errors="surrogatepass")
        return hashlib.sha256(raw).hexdigest()


def cache_directory() -> Path:
    configured = os.environ.get("METRIQ_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if platform.system() == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Metriq" / "Visualizer" / "Cache" / "analysis"
    if platform.system() == "Darwin":
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
            handle.seek(max(0, stat.st_size // 2 - chunk // 2)); digest.update(handle.read(chunk))
        if stat.st_size > chunk:
            handle.seek(max(0, stat.st_size - chunk)); digest.update(handle.read(chunk))
    return SourceFingerprint(str(source), stat.st_size, stat.st_mtime_ns, digest.hexdigest())


def _cache_path(fingerprint: SourceFingerprint, root: Path | None, settings: AnalysisSettings) -> Path:
    key = hashlib.sha256(f"{fingerprint.key()}\0{settings.signature()}\0{ANALYSIS_ENGINE_VERSION}".encode()).hexdigest()
    return (root or cache_directory()) / f"{key}.npz"


def save_cached_analysis(result: AnalysisResult, fingerprint: SourceFingerprint | None = None, *, root: Path | None = None, settings: AnalysisSettings | None = None) -> Path:
    source_fp = fingerprint or fingerprint_source(result.source_path)
    configured = (settings or AnalysisSettings()).normalized()
    output = _cache_path(source_fp, root, configured)
    output.parent.mkdir(parents=True, exist_ok=True)
    audio_path = Path(result.audio_path)
    # The renderer muxes this PCM stream. A temporary decoder path cannot be
    # reused after restart, so persist it alongside the feature archive.
    if result.source_kind == "media" and audio_path.resolve() != Path(source_fp.path):
        cached_audio = output.with_suffix(".wav")
        if audio_path.resolve() != cached_audio.resolve():
            fd, name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".tmp.wav", dir=output.parent)
            os.close(fd)
            temporary_audio = Path(name)
            try:
                shutil.copyfile(audio_path, temporary_audio)
                temporary_audio.replace(cached_audio)
            finally:
                with suppress(OSError): temporary_audio.unlink(missing_ok=True)
        audio_path = cached_audio.resolve()
    metadata = {"schema": CACHE_SCHEMA, "version": CACHE_VERSION, "engine": ANALYSIS_ENGINE_VERSION,
                "fingerprint": asdict(source_fp), "settings": asdict(configured), "source_kind": result.source_kind,
                "audio_path": str(audio_path), "sample_rate": result.sample_rate, "duration": result.duration,
                "hop_length": result.hop_length, "n_fft": result.n_fft, "feature_names": list(result.features),
                "feature_descriptions": result.feature_descriptions}
    arrays = {"__metadata__": np.asarray(json.dumps(metadata, separators=(",", ":"))), "times": result.times,
              "spectrogram_db": result.spectrogram_db, "spectrogram_freqs_hz": result.spectrogram_freqs_hz,
              "chromagram": result.chromagram, "mfcc": result.mfcc}
    arrays.update({f"feature_{i:04d}": values for i, values in enumerate(result.features.values())})
    fd, name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".tmp.npz", dir=output.parent)
    os.close(fd); temporary = Path(name)
    try:
        np.savez_compressed(temporary, **arrays)
        temporary.replace(output)
    finally:
        with suppress(OSError): temporary.unlink(missing_ok=True)
    return output


def load_cached_analysis(path: str | Path, fingerprint: SourceFingerprint | None = None, *, root: Path | None = None, settings: AnalysisSettings | None = None) -> AnalysisResult | None:
    source = Path(path).expanduser().resolve(); source_fp = fingerprint or fingerprint_source(source)
    configured = (settings or AnalysisSettings()).normalized(); cache_path = _cache_path(source_fp, root, configured)
    if not cache_path.is_file(): return None
    try:
        with np.load(cache_path, allow_pickle=False) as data:
            meta = json.loads(str(data["__metadata__"].item()))
            if meta.get("schema") != CACHE_SCHEMA or meta.get("version") != CACHE_VERSION or meta.get("engine") != ANALYSIS_ENGINE_VERSION: return None
            if meta.get("fingerprint") != asdict(source_fp) or AnalysisSettings.from_mapping(meta.get("settings")).signature() != configured.signature(): return None
            if meta.get("source_kind") == "media" and not Path(meta["audio_path"]).is_file(): return None
            names = meta["feature_names"]
            features = {name: np.asarray(data[f"feature_{i:04d}"], dtype=np.float64) for i, name in enumerate(names)}
            result = AnalysisResult(source_path=str(source), audio_path=str(meta["audio_path"]), sample_rate=int(meta["sample_rate"]), duration=float(meta["duration"]),
                hop_length=int(meta["hop_length"]), n_fft=int(meta["n_fft"]), times=np.asarray(data["times"]), features=features,
                spectrogram_db=np.asarray(data["spectrogram_db"]), spectrogram_freqs_hz=np.asarray(data["spectrogram_freqs_hz"]),
                chromagram=np.asarray(data["chromagram"]), mfcc=np.asarray(data["mfcc"]), feature_descriptions=dict(meta.get("feature_descriptions", {})), source_kind=meta.get("source_kind", "media"))
        with suppress(OSError): os.utime(cache_path, None)
        return result
    except Exception:
        with suppress(OSError): cache_path.unlink(missing_ok=True)
        with suppress(OSError): cache_path.with_suffix(".wav").unlink(missing_ok=True)
        return None


def analyze_source_cached(path: str | Path, *, use_cache: bool = True, settings: AnalysisSettings | None = None, cache_root: Path | None = None, temp_dir: str | Path | None = None) -> AnalysisResult:
    source = Path(path).expanduser().resolve(); configured = (settings or AnalysisSettings()).normalized()
    fingerprint = fingerprint_source(source)
    if use_cache:
        try:
            cached = load_cached_analysis(source, fingerprint, root=cache_root, settings=configured)
        except OSError:
            cached = None
        if cached is not None: return cached
    result = analysis_from_table_file(source) if is_table_file(source) else _analyze_media_uncached(source, configured.sample_rate, configured.n_fft, configured.hop_length, temp_dir)
    if use_cache:
        try: save_cached_analysis(result, fingerprint, root=cache_root, settings=configured); prune_cache(root=cache_root)
        except Exception: pass
    return result


def prune_cache(*, root: Path | None = None, max_bytes: int = DEFAULT_MAX_BYTES) -> int:
    directory = root or cache_directory()
    if not directory.exists(): return 0
    files = []
    for path in directory.glob("*.npz"):
        with suppress(OSError):
            stat = path.stat()
            size = stat.st_size
            audio = path.with_suffix(".wav")
            if audio.is_file(): size += audio.stat().st_size
            files.append((stat.st_atime_ns, size, path))
    total = sum(size for _, size, _ in files); removed = 0
    for _, size, path in sorted(files):
        if total <= max(0, int(max_bytes)): break
        try:
            path.unlink()
            with suppress(OSError): path.with_suffix(".wav").unlink(missing_ok=True)
            total -= size; removed += 1
        except OSError: pass
    return removed


def clear_cache(*, root: Path | None = None) -> int:
    directory = root or cache_directory(); removed = 0
    if directory.exists():
        for path in directory.glob("*.npz"):
            try:
                path.unlink()
                with suppress(OSError): path.with_suffix(".wav").unlink(missing_ok=True)
                removed += 1
            except OSError: pass
    return removed


__all__ = ["AnalysisSettings", "SourceFingerprint", "analyze_source_cached", "cache_directory", "clear_cache", "fingerprint_source", "load_cached_analysis", "prune_cache", "save_cached_analysis"]
