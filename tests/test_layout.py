from __future__ import annotations

import unittest

from metriq_visualizer_layout import (
    LAYOUT_ITEM_ORDER,
    ExportLayoutSpec,
    LayoutItemSpec,
    analysis_focus_export_layout,
    balanced_export_layout,
    social_vertical_export_layout,
)


class LayoutTests(unittest.TestCase):
    def test_item_clamp_keeps_layer_inside_frame(self) -> None:
        item = LayoutItemSpec(x=-2, y=4, w=2, h=-1, content_scale=20, fit_mode="invalid", content_alpha=4).clamp()
        self.assertGreaterEqual(item.x, 0)
        self.assertGreaterEqual(item.y, 0)
        self.assertLessEqual(item.x + item.w, 1)
        self.assertLessEqual(item.y + item.h, 1)
        self.assertEqual(item.fit_mode, "contain")
        self.assertEqual(item.content_scale, 4.0)
        self.assertEqual(item.content_alpha, 1.0)

    def test_round_trip_preserves_order_and_safe_area(self) -> None:
        source = social_vertical_export_layout()
        source.safe_area_percent = 7.5
        source.move_layer("preview", 3)
        loaded = ExportLayoutSpec.from_dict(source.to_dict())
        self.assertEqual(loaded.order, source.order)
        self.assertEqual(loaded.safe_area_percent, 7.5)
        self.assertEqual(loaded.preview.fit_mode, "cover")


    def test_default_and_analysis_layouts_dock_supporting_panels_below_geometry(self) -> None:
        for layout in (balanced_export_layout(), analysis_focus_export_layout()):
            geometry_bottom = layout.geometry.y + layout.geometry.h
            for name in ("preview", "spectrogram", "chromagram", "mfcc", "traces"):
                item = layout.item(name)
                if item.enabled:
                    self.assertGreaterEqual(
                        item.y + 1e-9,
                        geometry_bottom,
                        f"{name} must be docked below the geometry field",
                    )

    def test_malformed_order_is_normalized(self) -> None:
        layout = balanced_export_layout()
        layout.order = ["preview", "preview", "unknown", "geometry"]
        layout.clamp()
        self.assertEqual(len(layout.order), len(LAYOUT_ITEM_ORDER))
        self.assertEqual(set(layout.order), set(LAYOUT_ITEM_ORDER))
        self.assertEqual(layout.order[:2], ["preview", "geometry"])


if __name__ == "__main__":
    unittest.main()
