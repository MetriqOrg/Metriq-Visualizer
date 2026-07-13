#!/usr/bin/env python3
"""Create a self-contained desktop directory with PyInstaller.

FFmpeg is intentionally not embedded: licensing, hardware support, and update
cadence differ by platform. The built app detects a system FFmpeg and retains
PNG/JPEG sequence export when FFmpeg is absent.
"""
from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "metriq_visualizer_app.py"
APP_NAME = "Metriq Visualizer"
BUNDLE_IDENTIFIER = "org.metriq.visualizer"
MICROPHONE_USAGE = (
    "Metriq Visualizer uses microphone input only when you start Live Input, "
    "so it can visualize and optionally record sound locally in real time."
)


def patch_macos_bundle_metadata(app_path: Path, version: str) -> Path:
    """Add the macOS privacy declaration required before CoreAudio capture."""

    plist_path = Path(app_path) / "Contents" / "Info.plist"
    if not plist_path.is_file():
        raise FileNotFoundError(f"PyInstaller app bundle is missing {plist_path}")
    with plist_path.open("rb") as handle:
        payload = plistlib.load(handle)
    payload["CFBundleIdentifier"] = BUNDLE_IDENTIFIER
    payload["CFBundleShortVersionString"] = str(version)
    payload["CFBundleVersion"] = str(version)
    payload["NSMicrophoneUsageDescription"] = MICROPHONE_USAGE
    temporary = plist_path.with_name("Info.plist.metriq-tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle, fmt=plistlib.FMT_XML, sort_keys=True)
    temporary.replace(plist_path)
    return plist_path


def sign_macos_bundle(app_path: Path, *, runner=subprocess.run) -> None:
    """Ad-hoc sign a local release bundle after changing its Info.plist."""

    result = runner(
        ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if int(result.returncode) != 0:
        message = str(result.stderr).strip() or "codesign failed."
        raise RuntimeError(f"Could not sign {app_path.name}: {message}")



def data_arg(source: Path, target: str) -> str:
    separator = ";" if platform.system() == "Windows" else ":"
    return f"{source}{separator}{target}"


def main() -> int:
    executable = shutil.which("pyinstaller")
    if not executable:
        print("PyInstaller is not installed. Run: python -m pip install '.[dev]'", file=sys.stderr)
        return 2
    command = [
        executable,
        "--clean",
        "--noconfirm",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", str(ROOT / "build-out" / "dist"),
        "--workpath", str(ROOT / "build-out" / "work"),
        "--specpath", str(ROOT / "build-out"),
        "--add-data", data_arg(ROOT / "assets", "assets"),
        "--add-data", data_arg(ROOT / "presets", "presets"),
        "--add-data", data_arg(ROOT / "export_profiles", "export_profiles"),
        "--collect-all", "soundfile",
        "--hidden-import", "openpyxl",
        "--hidden-import", "sounddevice",
        "--hidden-import", "PySide6.QtMultimedia",
        "--hidden-import", "metriq_visualizer_realtime",
        str(ENTRY),
    ]
    system = platform.system()
    if system == "Darwin":
        command[1:1] = ["--osx-bundle-identifier", BUNDLE_IDENTIFIER]
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    print("Running:", " ".join(command))
    result = subprocess.call(command, cwd=ROOT, env=environment)
    if result == 0 and system == "Darwin":
        version = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
        app_path = ROOT / "build-out" / "dist" / f"{APP_NAME}.app"
        patch_macos_bundle_metadata(app_path, version)
        sign_macos_bundle(app_path)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
