from __future__ import annotations

import unittest

import numpy as np

from metriq_visualizer_visuals import (
    compute_tube_radii,
    geometry_limits,
    normalize_values,
    prepare_color_mapping,
    smooth_curve,
    zoom_limits,
)


class VisualHelperTests(unittest.TestCase):
    def test_normalization_and_color_mapping(self) -> None:
        values = np.asarray([1.0, 2.0, 3.0])
        normalized = normalize_values(values)
        np.testing.assert_allclose(normalized, [0.0, 0.5, 1.0])
        scalar, rgba, _name = prepare_color_mapping(values, "viridis")
        self.assertEqual(scalar.shape, (3,))
        self.assertEqual(rgba.shape, (3, 4))

    def test_smoothing_radii_and_zoom(self) -> None:
        values = np.asarray([0.0, 10.0, 0.0, 10.0, 0.0])
        smoothed = smooth_curve(values, 3)
        self.assertEqual(smoothed.shape, values.shape)
        radii = compute_tube_radii(values, taper=0.5)
        self.assertTrue(np.all(radii > 0))
        limits = geometry_limits(np.column_stack((values, values * 2, values * 3)))
        zoomed = zoom_limits(limits, 2.0)
        self.assertLess(zoomed["x"][1] - zoomed["x"][0], limits["x"][1] - limits["x"][0])


if __name__ == "__main__":
    unittest.main()
