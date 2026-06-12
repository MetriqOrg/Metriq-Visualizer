#!/usr/bin/env python3
"""Build Metriq Visualizer with PyInstaller.

This script intentionally keeps packaging logic in source control while keeping
compiled artifacts out of the repository. Release artifacts belong in GitHub
Releases, not committed into the repo.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "metriq_visualizer_app.py"
NAME = "Metriq Visualizer"


def data_arg(source: str, target: str) -> str:
    sep = ";" if platform.system() == "Windows" else ":"
    return f"{source}{sep}{target}"


def main() -> int:
    if not APP.exists():
        print(f"Missing app entrypoint: {APP}", file=sys.stderr)
        return 2

    pyinstaller = shutil.which("pyinstaller")
    if not pyinstaller:
        print("PyInstaller is not installed. Run: python -m pip install pyinstaller", file=sys.stderr)
        return 2

    cmd = [
        pyinstaller,
        "--clean",
        "--noconfirm",
        "--windowed",
        "--name",
        NAME,
        "--add-data",
        data_arg(str(ROOT / "assets"), "assets"),
        "--add-data",
        data_arg(str(ROOT / "presets"), "presets"),
        "--hidden-import",
        "PIL._tkinter_finder",
        "--hidden-import",
        "scipy.spatial.transform._rotation_groups",
        "--collect-submodules",
        "pyqtgraph",
        "--collect-submodules",
        "librosa",
        str(APP),
    ]

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
