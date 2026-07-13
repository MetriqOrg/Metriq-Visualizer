from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from metriq_visualizer_updates import (
    APP_BUNDLE_IDENTIFIER,
    UpdateInfo,
    available_update,
    extract_verified_app,
    is_newer_version,
    schedule_macos_install,
)


class UpdateTests(unittest.TestCase):
    @staticmethod
    def _release(*, version: str = "v1.12.7", prerelease: bool = False, name: str = "Metriq-Visualizer-macos-arm64.zip") -> dict:
        return {
            "tag_name": version,
            "prerelease": prerelease,
            "draft": False,
            "html_url": "https://github.com/MetriqOrg/Metriq-Visualizer/releases/tag/v1.12.7",
            "assets": [
                {
                    "name": name,
                    "browser_download_url": "https://github.com/MetriqOrg/Metriq-Visualizer/releases/download/v1.12.7/Metriq-Visualizer-macos-arm64.zip",
                    "digest": "sha256:" + "a" * 64,
                    "size": 123,
                }
            ],
        }

    def test_update_selection_requires_new_stable_verified_macos_asset(self) -> None:
        self.assertTrue(is_newer_version("1.12.7", "1.12.5"))
        self.assertFalse(is_newer_version("1.12.5-beta", "1.12.5"))
        self.assertIsNone(available_update("1.12.5", [self._release(prerelease=True)]))
        self.assertIsNone(available_update("1.12.5", [self._release(name="Metriq-Visualizer-Complete-Source.zip")]))
        update = available_update(
            "1.12.5",
            [self._release(), self._release(version="v1.12.8")],
            machine="arm64",
        )
        self.assertIsNotNone(update)
        assert update is not None
        self.assertEqual(update.version, "1.12.8")
        self.assertEqual(update.asset_name, "Metriq-Visualizer-macos-arm64.zip")

    def test_update_selection_matches_the_macos_cpu_architecture(self) -> None:
        release = self._release()
        release["assets"] = [
            {
                "name": "Metriq-Visualizer-v1.12.7-macOS-arm64.zip",
                "browser_download_url": "https://example.test/arm64.zip",
                "digest": "sha256:" + "a" * 64,
                "size": 123,
            },
            {
                "name": "Metriq-Visualizer-v1.12.7-macOS-x86_64.zip",
                "browser_download_url": "https://example.test/x86_64.zip",
                "digest": "sha256:" + "b" * 64,
                "size": 456,
            },
        ]

        arm = available_update("1.12.5", [release], machine="arm64")
        intel = available_update("1.12.5", [release], machine="x86_64")

        self.assertIsNotNone(arm)
        self.assertIsNotNone(intel)
        assert arm is not None and intel is not None
        self.assertEqual(arm.asset_name, "Metriq-Visualizer-v1.12.7-macOS-arm64.zip")
        self.assertEqual(intel.asset_name, "Metriq-Visualizer-v1.12.7-macOS-x86_64.zip")

    def test_extracted_bundle_must_match_metriq_identity_and_release_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "Metriq-Visualizer-macos.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr(
                    "Metriq Visualizer.app/Contents/Info.plist",
                    plistlib.dumps(
                        {
                            "CFBundleIdentifier": APP_BUNDLE_IDENTIFIER,
                            "CFBundleShortVersionString": "1.12.7",
                        }
                    ),
                )
            update = UpdateInfo("1.12.7", archive.name, "https://example.test/app.zip", "a" * 64, 0, "https://example.test")
            bundle = extract_verified_app(archive, root / "stage", update)
            self.assertEqual(bundle.name, "Metriq Visualizer.app")

    @unittest.skipUnless(sys.platform == "darwin", "macOS-only install script")
    def test_install_is_deferred_to_a_script_after_the_app_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            staged = root / "stage" / "Metriq Visualizer.app"
            staged.mkdir(parents=True)
            target = root / "Applications" / "Metriq Visualizer.app"
            target.parent.mkdir()
            launched: list[tuple[list[str], dict]] = []

            script = schedule_macos_install(
                staged,
                target,
                process_id=424242,
                launcher=lambda command, **kwargs: launched.append((command, kwargs)),
            )

            self.assertTrue(script.is_file())
            self.assertEqual(launched[0][0][0], "/bin/sh")
            self.assertIn("while kill -0 424242", script.read_text(encoding="utf-8"))
            self.assertIn(str(staged), script.read_text(encoding="utf-8"))
