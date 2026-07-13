from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.merge_into_repo import REPORT_NAME, merge, source_files


class MergeToolTests(unittest.TestCase):
    def test_merge_preserves_git_metadata_and_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "checkout"
            (target / ".git" / "info").mkdir(parents=True)
            head = target / ".git" / "HEAD"
            head.write_text("ref: refs/heads/main\n", encoding="utf-8")
            readme = target / "README.md"
            readme.write_text("old visualizer readme\n", encoding="utf-8")
            unrelated = target / "UNRELATED_KEEP.txt"
            unrelated.write_text("preserve me\n", encoding="utf-8")

            dry = merge(target, dry_run=True)
            self.assertGreater(dry["counts"]["added"], 0)
            self.assertGreater(dry["counts"]["updated"], 0)
            self.assertEqual(readme.read_text(encoding="utf-8"), "old visualizer readme\n")
            self.assertFalse((target / REPORT_NAME).exists())

            applied = merge(target, dry_run=False)
            self.assertEqual(head.read_text(encoding="utf-8"), "ref: refs/heads/main\n")
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve me\n")
            self.assertTrue((target / REPORT_NAME).is_file())
            self.assertIn("Metriq Visualizer v1.12.7", readme.read_text(encoding="utf-8"))

            readme_action = next(action for action in applied["actions"] if action["path"] == "README.md")
            backup = target / readme_action["backup"]
            self.assertEqual(backup.read_text(encoding="utf-8"), "old visualizer readme\n")

            repeated = merge(target, dry_run=True)
            self.assertEqual(repeated["counts"]["added"], 0)
            self.assertEqual(repeated["counts"]["updated"], 0)
            self.assertGreater(repeated["counts"]["unchanged"], 0)


    def test_merge_preserves_existing_creator_preset_with_same_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "checkout"
            (target / ".git" / "info").mkdir(parents=True)
            preset = target / "presets" / "Glowstick.mvpreset"
            preset.parent.mkdir(parents=True)
            original = '{"name":"My Glowstick","state":{"visual":{"line_width":99}}}\n'
            preset.write_text(original, encoding="utf-8")

            dry = merge(target, dry_run=True)
            action = next(item for item in dry["actions"] if item["path"] == "presets/Glowstick.mvpreset")
            self.assertEqual(action["action"], "preserved")
            self.assertEqual(dry["counts"]["preserved"], 1)

            merge(target, dry_run=False)
            self.assertEqual(preset.read_text(encoding="utf-8"), original)

    def test_manifest_limits_source_files_and_rejects_tampering(self) -> None:
        import hashlib
        import json

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            listed = root / "listed.txt"
            listed.write_text("release payload\n", encoding="utf-8")
            generated = root / "build" / "lib" / "generated.py"
            generated.parent.mkdir(parents=True)
            generated.write_text("not part of the release\n", encoding="utf-8")
            digest = hashlib.sha256(listed.read_bytes()).hexdigest()
            (root / "PACKAGE_MANIFEST.json").write_text(
                json.dumps(
                    {
                        "schema": "metriq.complete-source-manifest",
                        "schema_version": 1,
                        "files": [{"path": "listed.txt", "sha256": digest}],
                    }
                ),
                encoding="utf-8",
            )
            files = [path.relative_to(root).as_posix() for path in source_files(root)]
            self.assertEqual(files, ["listed.txt", "PACKAGE_MANIFEST.json"])

            listed.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                list(source_files(root))

    def test_merge_refuses_a_final_component_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "checkout"
            (target / ".git" / "info").mkdir(parents=True)
            outside = root / "outside-readme.md"
            outside.write_text("do not replace\n", encoding="utf-8")
            try:
                (target / "README.md").symlink_to(outside)
            except OSError:
                self.skipTest("Symlinks are not available on this host")
            with self.assertRaisesRegex(ValueError, "symlink"):
                merge(target, dry_run=True)
            self.assertEqual(outside.read_text(encoding="utf-8"), "do not replace\n")


if __name__ == "__main__":
    unittest.main()
