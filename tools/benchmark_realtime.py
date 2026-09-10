#!/usr/bin/env python3
"""Reproducible CPU preview benchmark; not a GPU or display-FPS measurement."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from statistics import median
from time import perf_counter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-rows", type=int, default=100_000)
    parser.add_argument("--points", type=int, default=1200)
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.source_rows, args.points, args.frames) < 4:
        parser.error("source-rows, points and frames must be at least four")
    if args.source_rows > 2_000_000 or args.points > 6000 or args.frames > 1000:
        parser.error("bounded benchmark limits: 2,000,000 rows, 6,000 points and 1,000 frames")
    sys.path.insert(0, str(args.source_tree.resolve()))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    import PySide6
    from PySide6.QtWidgets import QApplication

    from metriq_visualizer_3d import interpolate_spline
    from metriq_visualizer_core import GeometryResult
    from metriq_visualizer_realtime import Realtime3DCanvas
    from metriq_visualizer_render import ExportOptions

    app = QApplication.instance() or QApplication([])
    t = np.linspace(0.0, 100.0, args.source_rows)
    arrays = dict(x=np.sin(t), y=np.cos(t * 0.7), z=np.sin(t * 0.33), color=t, size=0.5 + 0.5*np.sin(t)**2,
                  times=t, rgba=np.column_stack((t/100, 1-t/100, np.full(t.size, 0.5), np.ones(t.size))),
                  source_indices=np.arange(t.size))
    geometry = GeometryResult(
        **{f"{key}_{suffix}": value for suffix in ("full", "plot") for key, value in arrays.items()},
        formulas={}, normalize_mode="None", colormap="viridis",
    )
    options = ExportOptions(history_mode="Full static", path_curve_mode="Smooth spline", curve_detail=4,
                            autorotate=False, show_scene_hud=True)
    canvas = Realtime3DCanvas()
    canvas.resize(960, 600)
    canvas.set_scene(None, geometry, options, maximum_points=args.points)
    canvas.show()
    app.processEvents()
    path_t = np.linspace(0, 10, args.points)
    points = np.column_stack((np.sin(path_t), np.cos(path_t), path_t))
    colors, widths = np.ones((args.points, 4)), np.ones(args.points)
    spline_ms, frame_ms = [], []
    try:
        for index in range(args.frames + 3):
            started = perf_counter()
            interpolate_spline(points, colors, widths, 4)
            spline_elapsed = (perf_counter() - started) * 1000
            canvas.set_time(40.0 + index * 0.03)
            started = perf_counter()
            image = canvas.grab()
            frame_elapsed = (perf_counter() - started) * 1000
            if image.isNull():
                raise RuntimeError("The Qt canvas did not produce a frame")
            if index >= 3:
                spline_ms.append(spline_elapsed)
                frame_ms.append(frame_elapsed)
        result = {
            "schema": "metriq.cpu-preview-benchmark.v1",
            "source_tree": str(args.source_tree.resolve()),
            "python": platform.python_version(), "platform": platform.platform(),
            "numpy": np.__version__, "qt_binding": PySide6.__version__, "qt_platform": app.platformName(),
            "source_rows": args.source_rows, "live_points": args.points, "curve_detail": 4,
            "logical_size": [960, 600], "device_pixel_ratio": canvas.devicePixelRatioF(),
            "measured_frames": args.frames, "warmup_frames": 3,
            "spline_median_ms": median(spline_ms), "spline_samples_ms": spline_ms,
            "raster_frame_median_ms": median(frame_ms), "raster_frame_samples_ms": frame_ms,
            "limitations": "Synthetic CPU raster frames including QWidget.grab; excludes audio IO, window composition, display refresh and GPU hardware. Not an FPS promise.",
        }
        text = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.write_text(text, encoding="utf-8")
        print(text)
    finally:
        canvas.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
