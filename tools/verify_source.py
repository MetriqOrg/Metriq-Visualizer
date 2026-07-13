#!/usr/bin/env python3
"""Verify a Metriq Visualizer source tree without modifying it."""
from __future__ import annotations

import argparse
import importlib
import json
import py_compile
import sys
from pathlib import Path

REQUIRED_FILES = (
    "VERSION.txt",
    "README.md",
    "LICENSE",
    "requirements.txt",
    "metriq_visualizer_app.py",
    "metriq_visualizer_atomic.py",
    "metriq_visualizer_3d.py",
    "metriq_visualizer_core.py",
    "metriq_visualizer_layout.py",
    "metriq_visualizer_render.py",
    "metriq_visualizer_export_pipeline.py",
    "metriq_visualizer_export_studio.py",
    "metriq_visualizer_live.py",
    "metriq_visualizer_panels.py",
    "metriq_visualizer_performance.py",
    "metriq_visualizer_realtime.py",
    "metriq_visualizer_preset_files.py",
    "metriq_visualizer_theme.py",
)
IMPORTS = (
    "metriq_visualizer_atomic",
    "metriq_visualizer_3d",
    "metriq_visualizer_core",
    "metriq_visualizer_layout",
    "metriq_visualizer_render",
    "metriq_visualizer_export_pipeline",
    "metriq_visualizer_export_studio",
    "metriq_visualizer_live",
    "metriq_visualizer_panels",
    "metriq_visualizer_performance",
    "metriq_visualizer_realtime",
    "metriq_visualizer_preset_files",
    "metriq_visualizer_theme",
    "metriq_visualizer_app",
)


def verify(root: Path, imports: bool) -> dict:
    root = root.resolve()
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    compile_errors: list[str] = []
    for path in sorted(root.glob("*.py")):
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            compile_errors.append(str(exc))
    import_errors: list[str] = []
    if imports:
        sys.path.insert(0, str(root))
        for name in IMPORTS:
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001
                import_errors.append(f"{name}: {type(exc).__name__}: {exc}")
    version = (root / "VERSION.txt").read_text(encoding="utf-8").strip() if (root / "VERSION.txt").is_file() else ""
    passed = not missing and not compile_errors and not import_errors and version == "1.12.7"
    return {
        "schema": "metriq.source-verification",
        "schema_version": 1,
        "root": str(root),
        "version": version,
        "passed": passed,
        "missing": missing,
        "compile_errors": compile_errors,
        "import_errors": import_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--imports", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = verify(args.target, args.imports)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else ("verification: passed" if result["passed"] else "verification: failed"))
    if not result["passed"] and not args.json:
        for category in ("missing", "compile_errors", "import_errors"):
            for item in result[category]:
                print(f"- {category}: {item}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
