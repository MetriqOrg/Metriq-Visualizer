#!/usr/bin/env python3
"""Measure duplicate captures, panel cursor updates and deferred plot preparation.

Run this same script with --source-tree pointing at each checkout. Measurements
are CPU/offscreen timings and Python-tracked allocations, not physical GPU,
display FPS, executable size, or total resident-memory measurements.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import sys
import tracemalloc
from pathlib import Path
from statistics import median
from time import perf_counter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 5 <= args.frames <= 200:
        parser.error("frames must be between 5 and 200")
    sys.path.insert(0, str(args.source_tree.resolve()))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import numpy as np
    import PySide6
    from PySide6.QtWidgets import QApplication

    from metriq_visualizer_core import AnalysisResult, GeometryResult
    from metriq_visualizer_panels import AnalysisCanvas, AnalysisDockWidget
    from metriq_visualizer_realtime import Realtime3DCanvas
    from metriq_visualizer_render import ExportOptions

    app = QApplication.instance() or QApplication([])
    t = np.linspace(0, 100, 100_000)
    arrays = dict(x=np.sin(t), y=np.cos(t*.7), z=np.sin(t*.33), color=t,
                  size=.5+.5*np.sin(t)**2, times=t,
                  rgba=np.column_stack((t/100, 1-t/100, t*0+.5, t*0+1)),
                  source_indices=np.arange(t.size))
    geometry = GeometryResult(**{f"{k}_{suffix}": v for suffix in ("full", "plot") for k, v in arrays.items()})
    rng = np.random.default_rng(2026)
    analysis = AnalysisResult(source_path=Path("synthetic.csv"), source_kind="table", times=t,
                              duration=100, features={"time": t}, waveform=np.sin(t*4),
                              spectrogram=rng.normal(size=(256, 2048)),
                              spectrogram_frequencies=np.linspace(0, 8000, 256),
                              chromagram=rng.random((12, 2048)), mfcc=rng.normal(size=(13, 2048)))
    options = ExportOptions(history_mode="Full static", autorotate=False, curve_detail=4)
    canvas = Realtime3DCanvas()
    canvas.resize(960, 600)
    canvas.set_scene(analysis, geometry, options, maximum_points=1200)
    samples = []
    for i in range(args.frames + 3):
        canvas.set_time(10 + i*.2)
        start = perf_counter()
        canvas.grab()
        viewport = perf_counter()
        for _ in range(3):
            getattr(canvas, "snapshot", canvas.grab)()
        done = perf_counter()
        if i >= 3:
            samples.append(((viewport-start)*1000, (done-viewport)*1000, (done-start)*1000))
    dpr = canvas.devicePixelRatioF()
    canvas.close()
    app.processEvents()
    panel_results = {}
    for mode in ("spectrogram", "traces"):
        panel = AnalysisCanvas(mode)
        panel.resize(960, 240)
        panel.set_data(analysis, geometry)
        panel.show()
        app.processEvents()
        durations = []
        for i in range(args.frames + 3):
            start = perf_counter()
            panel.set_time(12 + i*.21)
            app.processEvents()
            panel.grab()
            elapsed = (perf_counter()-start)*1000
            if i >= 3:
                durations.append(elapsed)
        panel_results[mode] = {"median_ms": median(durations), "samples_ms": durations}
        panel.close()
        app.processEvents()

    # Allocate fixtures before starting tracing: only plot preparation is counted.
    gc.collect()
    tracemalloc.start()
    start = perf_counter()
    dock = AnalysisDockWidget()
    dock.set_data(analysis, geometry)
    app.processEvents()
    elapsed = (perf_counter()-start)*1000
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    dock.close()
    app.processEvents()
    result = {
        "source_tree": str(args.source_tree.resolve()), "python": platform.python_version(),
        "qt_binding": PySide6.__version__, "numpy": np.__version__,
        "platform": platform.platform(), "qt_platform": app.platformName(), "dpr": dpr,
        "measured_frames": args.frames, "warmup_frames": 3,
        "workload": "100,000 rows; 1,200 live points; 960x600 viewport; three same-state stage requests; 256x2048 spectrogram",
        "viewport": {
            "new_frame_median_ms": median(row[0] for row in samples),
            "three_same_state_captures_median_ms": median(row[1] for row in samples),
            "frame_plus_three_captures_median_ms": median(row[2] for row in samples),
            "samples_ms": samples,
        },
        "cursor_updates": panel_results,
        "source_tab_plot_preparation": {"elapsed_ms": elapsed, "traced_current_bytes": current,
                                         "traced_peak_bytes": peak},
        "limitations": "CPU/offscreen synthetic workload; traced allocations exclude native Qt allocations and are not RSS; no physical GPU/audio/display validation",
    }
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
