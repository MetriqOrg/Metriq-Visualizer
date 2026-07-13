from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TEST_SETTINGS_ROOT = Path(tempfile.mkdtemp(prefix="metriq-stage-output-settings-"))
os.environ.setdefault("METRIQ_SETTINGS_PATH", str(_TEST_SETTINGS_ROOT / "settings.ini"))

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from metriq_visualizer_layout import balanced_export_layout, geometry_focus_export_layout  # noqa: E402
from metriq_visualizer_stage_output import StageOutputConfig, StageOutputWindow  # noqa: E402


class StageOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_stage_configuration_round_trips_with_composition(self) -> None:
        config = StageOutputConfig(
            layout=balanced_export_layout(),
            screen_name="Projector",
            fullscreen=False,
            refresh_fps=19,
            background_kind="image",
            background_color="#123456",
            background_path="/tmp/background.png",
        )
        restored = StageOutputConfig.from_dict(config.to_dict())
        self.assertEqual(restored.screen_name, "Projector")
        self.assertEqual(restored.refresh_fps, 19)
        self.assertTrue(restored.layout.spectrogram.enabled)
        self.assertEqual(restored.background_kind, "image")

    def test_stage_window_composites_existing_layer_snapshots(self) -> None:
        pixmap = QPixmap(80, 60)
        pixmap.fill()
        config = StageOutputConfig(layout=geometry_focus_export_layout(), fullscreen=False, refresh_fps=15)
        window = StageOutputWindow(lambda: {"geometry": pixmap}, config)
        window.resize(640, 360)
        window.show()
        self.app.processEvents()
        output = window.grab()
        self.assertFalse(output.isNull())
        window.close()
        self.app.processEvents()
