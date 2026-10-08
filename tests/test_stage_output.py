from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from metriq_visualizer_stage_output import StageOutputConfig, StageOutputWindow


def test_stage_output_config_round_trip_and_clamp():
    config = StageOutputConfig("Projector", False, 60, True, True, True, "#123456", "/tmp/bg.png")
    restored = StageOutputConfig.from_dict(config.to_dict())
    assert restored.screen_name == "Projector"
    assert restored.refresh_fps == 30
    assert restored.analysis and restored.logo and restored.viewport
    assert restored.background_color == "#123456"
    assert restored.background_path == "/tmp/bg.png"
    assert StageOutputConfig.from_dict({}).fullscreen is True


def test_stage_output_composites_widget_snapshots_over_background():
    app = QApplication.instance() or QApplication([])
    pixmap = QPixmap(40, 40)
    pixmap.fill(QColor("#00ff00"))
    config = StageOutputConfig(fullscreen=False, viewport=True, background_color="#ff0000")
    window = StageOutputWindow(lambda: {"viewport": pixmap}, config)
    window.resize(200, 120)
    window._capture()
    window.show()
    app.processEvents()
    output = window.grab().toImage()
    assert output.pixelColor(5, 5) == QColor("#ff0000")
    assert output.pixelColor(100, 50).green() > 200
    window.close()
    app.processEvents()
