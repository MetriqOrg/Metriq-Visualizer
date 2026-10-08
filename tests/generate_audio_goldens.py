"""Regenerate with: python tests/generate_audio_goldens.py (requires FFmpeg)."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import warnings

import librosa
import numpy as np
import scipy

from audio_fixtures import FIXTURE_NAMES, write_fixture

ROOT = Path(__file__).resolve().parents[1]
TAG = "baseline-v1.10.18"
PROFILES = {"default": (22050, 2048, 256), "native_large": (44100, 4096, 512),
            "odd_fft": (22050, 2049, 257)}


def main():
    # Never obtain expected values from the working core.
    source = subprocess.check_output(["git", "show", f"{TAG}:metriq_visualizer_core.py"], cwd=ROOT)
    commit = subprocess.check_output(["git", "rev-parse", TAG], cwd=ROOT, text=True).strip()
    output = ROOT / "tests" / "goldens" / "audio_v1_10_18"
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        module_path = directory / "baseline_core.py"
        module_path.write_bytes(source)
        spec = importlib.util.spec_from_file_location("baseline_core", module_path)
        baseline = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = baseline
        spec.loader.exec_module(baseline)
        for name in FIXTURE_NAMES:
            wav = directory / f"{name}.wav"
            write_fixture(wav, name)
            hashes[name] = hashlib.sha256(wav.read_bytes()).hexdigest()
            for profile, settings in PROFILES.items():
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    result = baseline.analyze_media(wav, *settings, temp_dir=directory / name)
                if profile == "default":
                    np.savez_compressed(output / f"{name}.npz", **result.features,
                                        panel_spectrogram=result.spectrogram_db,
                                        panel_frequencies=result.spectrogram_freqs_hz,
                                        panel_chromagram=result.chromagram, panel_mfcc=result.mfcc)
                else:
                    np.savez_compressed(output / f"{name}_{profile}.npz", **result.features)
                print(f"{name}/{profile}: {len(result.features)} features, {result.times.size} frames")
    metadata = {"tag": TAG, "commit": commit, "core_sha256": hashlib.sha256(source).hexdigest(),
                "numpy": np.__version__, "scipy": scipy.__version__, "librosa": librosa.__version__,
                "settings": {"sample_rate": 22050, "n_fft": 2048, "hop_length": 256},
                "additional_profiles": {key: dict(zip(("sample_rate", "n_fft", "hop_length"), value))
                                        for key, value in PROFILES.items() if key != "default"},
                "fixture_sha256": hashes}
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
