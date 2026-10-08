"""Regenerate decoded_<name>_22050.wav: the 22.05 kHz mono PCM16 audio that the v1.10.18
code (via FFmpeg) produces from each 44.1 kHz fixture. Storing it removes FFmpeg's
resampler version from the audio-equivalence tests. Run on the golden-generating machine:
    python tests/generate_decoded_fixtures.py   (requires FFmpeg)"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from audio_fixtures import FIXTURE_NAMES, write_fixture

ROOT = Path(__file__).resolve().parents[1]
TAG = "baseline-v1.10.18"
GOLDENS = ROOT / "tests" / "goldens" / "audio_v1_10_18"


def main():
    source = subprocess.check_output(["git", "show", f"{TAG}:metriq_visualizer_core.py"], cwd=ROOT)
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
            decoded = Path(baseline.ensure_wav_audio(wav, sample_rate=22050, temp_dir=directory / f"{name}_decode"))
            target = GOLDENS / f"decoded_{name}_22050.wav"
            shutil.copyfile(decoded, target)
            hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
            print(name, target.name, target.stat().st_size, "bytes")
    manifest_path = GOLDENS / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["decoded_22050_sha256"] = hashes
    manifest["decoded_with"] = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
