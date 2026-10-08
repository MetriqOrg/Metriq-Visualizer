import json
from pathlib import Path

import pytest

from metriq_visualizer_preset_files import discover_presets, load_preset, save_preset


def test_legacy_root_schema_and_creator_values_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "legacy.mvpreset"
    original = {"app_version": "1.9", "preset_name": "Creator_Profile", "preset_schema_version": 1,
                "mapping": {"x": "custom_feature", "creator_note": "keep me"}, "custom_section": {"value": 42}}
    path.write_text("\ufeff" + json.dumps(original), encoding="utf-8")
    loaded = load_preset(path)
    assert loaded["state"]["mapping"] == original["mapping"]
    assert loaded["state"]["custom_section"] == original["custom_section"]
    saved = save_preset(tmp_path / "roundtrip", loaded)
    assert json.loads(saved.read_text(encoding="utf-8"))["state"]["custom_section"] == original["custom_section"]


def test_user_preset_directory_takes_precedence(tmp_path: Path) -> None:
    user, bundled = tmp_path / "user", tmp_path / "bundled"
    user.mkdir(); bundled.mkdir()
    for directory, value in ((user, "user"), (bundled, "bundled")):
        (directory / "entry.mvpreset").write_text(json.dumps({"format": "mvpreset", "preset_name": "Shared", "preset_schema_version": 1,
            "state": {"visual": {"creator_value": value}}}), encoding="utf-8")
    found = discover_presets((user, bundled))
    assert found["Shared"] == (user / "entry.mvpreset").resolve()


def test_future_or_wrong_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "invalid.mvpreset"
    path.write_text('{"format":"wrong","state":{}}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_preset(path)
