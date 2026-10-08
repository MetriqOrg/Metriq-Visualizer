"""Fresh-process timing/RSS comparison; not part of pytest.

Run: python tests/benchmark_audio_analysis.py --runs 3
"""

import argparse
import importlib.util
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def worker(engine, wav, directory, reference):
    directory = Path(directory)
    start_import = time.perf_counter()
    if engine == "baseline":
        path = directory / "baseline_core.py"
        path.write_bytes(subprocess.check_output(
            ["git", "show", "baseline-v1.10.18:metriq_visualizer_core.py"], cwd=ROOT))
    elif engine == "reference":
        path = Path(reference) / "metriq_visualizer_core.py"
    else:
        path = ROOT / "metriq_visualizer_core.py"
    spec = importlib.util.spec_from_file_location("bench_core", path)
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core
    spec.loader.exec_module(core)
    import_seconds = time.perf_counter() - start_import
    if engine == "reference":
        settings = core.AnalysisSettings(sample_rate=22050, n_fft=2048, hop_length=256)
        analyze = lambda: core.analyze_media(wav, settings)
    elif engine == "baseline":
        analyze = lambda: core.analyze_media(wav, temp_dir=directory / "decode")
    else:
        # Use the real public module so the cache and core share AnalysisResult.
        from metriq_visualizer_core import analyze_media
        analyze = lambda: analyze_media(wav, temp_dir=directory / "decode",
                                         use_cache=engine == "cached", cache_root=directory / "cache")
    timings = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for _ in range(2):
            start = time.perf_counter()
            result = analyze()
            timings.append(time.perf_counter() - start)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes; Linux reports KiB.
    rss_mb = rss / (1024 ** 2 if sys.platform == "darwin" else 1024)
    print(json.dumps(dict(engine=engine, import_seconds=import_seconds,
                          first_seconds=timings[0], second_seconds=timings[1], peak_rss_mib=rss_mb,
                          frames=int(result.times.size), features=len(result.features),
                          rms_mean=float(result.features["rms"].mean()))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--reference", default=str(ROOT.parent / "ref-v1.12.8"))
    parser.add_argument("--worker", choices=("baseline", "ported", "cached", "reference"))
    parser.add_argument("--wav")
    parser.add_argument("--directory")
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker, args.wav, args.directory, args.reference)
    from audio_fixtures import write_fixture
    results = {}
    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        wav = scratch / "tones_noise.wav"
        write_fixture(wav, "tones_noise")
        for engine in ("baseline", "reference", "ported", "cached"):
            measurements = []
            for run in range(args.runs):
                directory = scratch / f"{engine}-{run}"
                directory.mkdir()
                raw = subprocess.check_output([sys.executable, str(Path(__file__).resolve()),
                                               "--worker", engine, "--wav", str(wav),
                                               "--directory", str(directory), "--reference", args.reference], text=True)
                measurements.append(json.loads(raw))
            summary = {key: statistics.median(item[key] for item in measurements)
                       for key in measurements[0] if key != "engine"}
            results[engine] = summary
            print(json.dumps({engine: summary}), flush=True)
    return results


if __name__ == "__main__":
    main()
