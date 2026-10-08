from pathlib import Path

import pytest

from metriq_visualizer_atomic import atomic_destination, atomic_directory
from metriq_visualizer_projects import load_project, save_project
from metriq_visualizer_preset_files import load_preset, save_preset


def test_atomic_destination_preserves_existing_file_on_failure(tmp_path: Path) -> None:
    destination = tmp_path / "data.json"
    destination.write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError):
        with atomic_destination(destination) as temporary:
            temporary.write_text("incomplete", encoding="utf-8")
            raise RuntimeError("write failed")
    assert destination.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".data.json.*")) == []


def test_atomic_directory_replaces_complete_tree(tmp_path: Path) -> None:
    destination = tmp_path / "bundle"
    destination.mkdir()
    (destination / "old").write_text("old", encoding="utf-8")
    with atomic_directory(destination) as temporary:
        (temporary / "new").write_text("new", encoding="utf-8")
    assert not (destination / "old").exists()
    assert (destination / "new").read_text(encoding="utf-8") == "new"


def test_project_and_preset_round_trip_through_atomic_writer(tmp_path: Path) -> None:
    project_path = save_project(tmp_path / "sample", {"format": "mvproj", "state": {"session": {}}})
    assert load_project(project_path)["state"]["session"] == {}
    preset_path = save_preset(tmp_path / "sample", {"format": "mvpreset", "state": {"mapping": {"x": "time"}}})
    assert load_preset(preset_path)["state"]["mapping"]["x"] == "time"
