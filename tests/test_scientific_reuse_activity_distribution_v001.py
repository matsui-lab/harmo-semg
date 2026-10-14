import importlib.util
from pathlib import Path
import unittest
import numpy as np

spec = importlib.util.spec_from_file_location("activity", Path(__file__).resolve().parents[1] / "scripts/paper/scientific_reuse_activity_distribution_v001.py")
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


class ActivityDistributionTests(unittest.TestCase):
    def test_constant_curve(self):
        np.testing.assert_allclose(A.areas(np.ones((1, 101))), [[.2, .4, .4]])

    def test_scale_invariance(self):
        x = np.random.default_rng(1).random((7, 101))
        np.testing.assert_allclose(A.areas(x), A.areas(x * np.arange(1, 8)[:, None]))
        np.testing.assert_allclose(A.areas(x).sum(axis=1), 1)

    def test_boundary_area_partition(self):
        x = np.zeros((1, 101))
        x[0, 20] = 1
        np.testing.assert_allclose(A.areas(x), [[.5, .5, 0]])

    def test_invalid_inputs(self):
        for x in (np.zeros((1, 101)), np.ones((1, 100)), np.full((1, 101), np.nan),
                  -np.ones((1, 101)), np.empty((0, 101))):
            with self.assertRaises(ValueError):
                A.areas(x)

    def test_ties_choose_first(self):
        x = np.zeros((1, 101))
        x[0, [6, 75]] = 1
        self.assertEqual(A.features(x)[0, 1], 6)

    def test_paired_change_zero(self):
        base = np.arange(10)[:, None, None] * np.ones((10, 3, 3))
        draws = np.random.default_rng(2).integers(0, 10, (100, 10))
        point, ci = A.paired_change(base, 0, 2, draws)
        np.testing.assert_array_equal(point, 0)
        np.testing.assert_array_equal(ci, 0)


if __name__ == "__main__":
    unittest.main()
