import importlib.util
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("release_build", Path(__file__).resolve().parents[1] / "build/build_pyinstaller.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def test_release_bundle_metadata_is_updatable_and_preserves_other_keys(tmp_path):
    app = tmp_path / "Metriq Visualizer.app"
    info = app / "Contents/Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({"CFBundleExecutable": "Metriq Visualizer", "custom": "keep"}))
    builder.patch_macos_bundle_metadata(app, "1.13.0")
    payload = plistlib.loads(info.read_bytes())
    assert payload["CFBundleIdentifier"] == "org.metriq.visualizer"
    assert payload["CFBundleShortVersionString"] == payload["CFBundleVersion"] == "1.13.0"
    assert payload["custom"] == "keep"
    assert payload["CFBundleExecutable"] == "Metriq Visualizer"


def test_sign_failure_stops_release_build(tmp_path):
    with pytest.raises(RuntimeError, match="sign"):
        builder.sign_macos_bundle(tmp_path, runner=lambda *a, **k: SimpleNamespace(returncode=1, stderr="sign failed"))
