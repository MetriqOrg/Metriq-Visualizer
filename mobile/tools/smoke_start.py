#!/usr/bin/env python3
"""Start the mobile host preview and fail on immediate QML/runtime exit."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


def candidates(build_dir: Path) -> list[Path]:
    names = {"MetriqVisualizerMobile", "MetriqVisualizerMobile.exe"}
    found: list[Path] = []
    for path in build_dir.rglob("*"):
        if path.is_file() and path.name in names:
            found.append(path)
    return sorted(found, key=lambda path: ("Contents/MacOS" not in path.as_posix(), len(path.parts)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", type=Path)
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()

    executables = candidates(args.build_dir.resolve())
    if not executables:
        print("mobile smoke: executable not found", file=sys.stderr)
        return 2

    environment = os.environ.copy()
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    environment.setdefault("QSG_RHI_BACKEND", "software")
    environment["METRIQ_FORCE_SOFTWARE"] = "1"
    process = subprocess.Popen(
        [str(executables[0])],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )
    try:
        time.sleep(max(0.5, args.seconds))
        result = process.poll()
        if result is not None:
            output = process.stdout.read() if process.stdout else ""
            print(output, file=sys.stderr)
            print(f"mobile smoke: application exited early with {result}", file=sys.stderr)
            return 3
        print(f"mobile smoke: startup remained healthy for {args.seconds:.1f}s")
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()


if __name__ == "__main__":
    raise SystemExit(main())
