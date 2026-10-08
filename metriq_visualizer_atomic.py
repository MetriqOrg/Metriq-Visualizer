# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Dependency-free atomic replacement helpers for local files and directories."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
import shutil


@contextmanager
def atomic_destination(destination: str | Path, *, suffix: str = ".tmp") -> Iterator[Path]:
    output = Path(destination).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=suffix, dir=output.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
        if not temporary.is_file():
            raise RuntimeError(f"Temporary output was not produced: {temporary}")
        temporary.replace(output)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


@contextmanager
def atomic_directory(destination: str | Path) -> Iterator[Path]:
    output = Path(destination).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent))
    backup = output.with_name(f".{output.name}.old")
    try:
        yield temporary
        if not temporary.is_dir():
            raise RuntimeError(f"Temporary directory was not produced: {temporary}")
        if output.exists():
            with suppress(OSError):
                shutil.rmtree(backup)
            output.replace(backup)
        try:
            temporary.replace(output)
        except Exception:
            if backup.exists() and not output.exists():
                backup.replace(output)
            raise
        with suppress(OSError):
            shutil.rmtree(backup)
    finally:
        with suppress(OSError):
            shutil.rmtree(temporary)


def atomic_write_text(destination: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    output = Path(destination).expanduser()
    with atomic_destination(output) as temporary, temporary.open("w", encoding=encoding, newline="") as handle:
        handle.write(text)
        handle.flush()
        with suppress(OSError):
            os.fsync(handle.fileno())
    return output


__all__ = ["atomic_destination", "atomic_directory", "atomic_write_text"]
