# Copyright (c) Metriq Foundation, Inc.
# This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0.
"""Small, dependency-free helpers for crash-safe local file replacement."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


@contextmanager
def atomic_destination(
    destination: str | Path,
    *,
    suffix: str = ".tmp",
) -> Iterator[Path]:
    """Yield a unique temporary path and atomically replace *destination*.

    The temporary file is created in the destination directory so ``replace``
    stays on the same filesystem. Existing destination data is left untouched
    unless the complete write succeeds.
    """

    output = Path(destination).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=suffix,
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        yield temporary
        if not temporary.is_file():
            raise RuntimeError(f"Temporary output was not produced: {temporary}")
        temporary.replace(output)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def atomic_write_text(
    destination: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Write text through a unique same-directory temporary file."""

    output = Path(destination).expanduser()
    with atomic_destination(output) as temporary, temporary.open(
        "w", encoding=encoding, newline=""
    ) as handle:
        handle.write(text)
        handle.flush()
        with suppress(OSError):
            os.fsync(handle.fileno())
    return output


__all__ = ["atomic_destination", "atomic_write_text"]
