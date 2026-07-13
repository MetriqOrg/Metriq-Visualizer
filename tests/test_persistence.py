from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from metriq_visualizer_preset_files import (
    build_preset_payload,
    discover_presets,
    load_preset,
    save_preset,
)
from metriq_visualizer_projects import build_project_payload, load_project, save_project


class PersistenceTests(unittest.TestCase):
    def test_project_uses_relative_source_when_possible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "media" / "tone.wav"
            source.parent.mkdir()
            source.write_bytes(b"RIFF")
            project_path = root / "projects" / "session.mvproj"
            project_path.parent.mkdir()
            state = {"session": {"file_path": str(source), "current_time": 2.5}, "mapping": {"x": "pc1"}}
            payload = build_project_payload("Session", state, project_path=project_path)
            self.assertEqual(Path(payload["relative_source"]), Path("../media/tone.wav"))
            saved = save_project(project_path, payload)
            loaded = load_project(saved)
            self.assertEqual(Path(loaded["state"]["session"]["file_path"]), source.resolve())
            self.assertEqual(loaded["state"]["mapping"]["x"], "pc1")

            moved_root = root / "moved"
            moved_source = moved_root / "media" / "tone.wav"
            moved_source.parent.mkdir(parents=True)
            moved_source.write_bytes(source.read_bytes())
            moved_project = moved_root / "projects" / "session.mvproj"
            moved_project.parent.mkdir(parents=True)
            moved_project.write_bytes(saved.read_bytes())
            source.unlink()
            moved_loaded = load_project(moved_project)
            self.assertEqual(
                Path(moved_loaded["state"]["session"]["file_path"]),
                moved_source.resolve(),
            )

    def test_preset_removes_session_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {"session": {"file_path": "/private/source.wav"}, "mapping": {"x": "pc1"}}
            payload = build_preset_payload("Safe preset", state)
            path = save_preset(Path(temp_dir) / "safe", payload)
            loaded = load_preset(path)
            self.assertNotIn("session", loaded["state"])
            self.assertEqual(loaded["state"]["mapping"]["x"], "pc1")

    def test_legacy_visual_preset_schema_is_translated_without_losing_creator_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Glowstick.mvpreset"
            path.write_text(
                """{
  "format": "Metriq Visualizer Preset",
  "preset_schema_version": 1,
  "preset_name": "Glowstick Legacy",
  "mapping": {"preset_name": "Pitch/Timbre/Motion", "normalize": "minmax", "colormap": "plasma"},
  "extraction": {"max_points": 777, "low_volume_cutoff_db": 12.5},
  "visual": {"alpha": 0.73, "history_mode": "Trail fade"},
  "motion": {"elevation": 19, "base_azimuth": 44, "autorotate": true, "rotation_speed": 8},
  "performance": {"mode": "draft", "live_point_budget": 420, "live_redraw_fps": 12},
  "export": {"layout": {"preview": {"fit_mode": "fill"}}}
}
""",
                encoding="utf-8",
            )
            loaded = load_preset(path)
            state = loaded["state"]
            self.assertEqual(state["mapping"]["preset"], "Pitch/Timbre/Motion")
            self.assertEqual(state["geometry"]["normalize_mode"], "minmax")
            self.assertEqual(state["geometry"]["colormap"], "plasma")
            self.assertEqual(state["geometry"]["max_points"], 777)
            self.assertEqual(state["geometry"]["low_volume_cutoff_db"], 12.5)
            self.assertEqual(state["visual"]["base_alpha"], 0.73)
            self.assertEqual(state["visual"]["elev"], 19)
            self.assertEqual(state["visual"]["azim"], 44)
            self.assertEqual(state["performance"]["mode"], "Fast preview")
            self.assertEqual(state["performance"]["live_point_budget"], 420)
            self.assertEqual(state["layout"]["preview"]["fit_mode"], "stretch")

    def test_user_preset_directory_takes_precedence_over_bundled_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            user = root / "user"
            bundled = root / "bundled"
            user.mkdir()
            bundled.mkdir()
            user_path = save_preset(
                user / "Glowstick.mvpreset",
                build_preset_payload("Glowstick", {"visual": {"line_width": 9.0}}),
            )
            save_preset(
                bundled / "Glowstick.mvpreset",
                build_preset_payload("Glowstick", {"visual": {"line_width": 1.0}}),
            )
            discovered = discover_presets((user, bundled))
            self.assertEqual(discovered["Glowstick"], user_path)


if __name__ == "__main__":
    unittest.main()
