import importlib.util
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location("aggregation", Path(__file__).parents[1] / "scripts/paper/scientific_reuse_aggregation_v001.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def test_identical_cycles_give_identical_estimands():
    x = np.exp(-((np.arange(101) - 30) / 9) ** 2)
    values, waveform, each = M.aggregate(np.tile(x, (5, 1)))
    np.testing.assert_allclose(values[0], values[1])
    np.testing.assert_allclose(waveform, x)
    np.testing.assert_allclose(each[:, 1], 30)


def test_aggregation_does_not_commute():
    x = np.ones((3, 101)) * .01
    x[0, 10] = 1
    x[1, 50] = 1
    x[2, 90] = 1
    values, _, _ = M.aggregate(x)
    assert values[0, 1] == 0  # pointwise median is flat: first maximum
    assert values[1, 1] == 50


def test_features_invariant_to_positive_cycle_scaling():
    x = np.random.default_rng(9).uniform(.1, 1, (4, 101))
    np.testing.assert_allclose(M.features(x), M.features(x * np.array([.1, 2, 3, 10])[:, None]))


def test_even_linear_peak_median_is_not_circular():
    x = np.ones((2, 101)) * .01
    x[0, 0] = 1
    x[1, 100] = 1
    values, _, each = M.aggregate(x)
    assert values[1, 1] == 50
    np.testing.assert_array_equal(each[:, 1], [0, 100])


@pytest.mark.parametrize("x", [np.zeros((3, 101)), np.ones((3, 100)), np.ones((0, 101)),
                                np.full((3, 101), np.nan), -np.ones((3, 101))])
def test_invalid_cycles_not_silently_dropped(x):
    with pytest.raises(ValueError):
        M.aggregate(x)


def test_bootstrap_keeps_aggregation_order_paired():
    left = np.broadcast_to(np.arange(10)[:, None, None, None, None], (10, 3, 2, 2, 2)).astype(float).copy()
    right = left + 2
    left[:, :, 1] += 3
    right[:, :, 1] += 8
    result = M.summarize(left, right, iterations=500)
    for row in result:
        assert row["study_difference_A"]["estimate"] == 2
        assert row["study_difference_B"]["estimate"] == 7
        assert row["change_B_minus_A"] == {"estimate": 5., "ci95": [5., 5.]}
        assert row["lencioni_participant_change"]["ci95"] == [3., 3.]
        assert row["gait120_participant_change"]["ci95"] == [8., 8.]
    assert result == M.summarize(left, right, iterations=500)


def test_bootstrap_rejects_nonfinite_participants():
    x = np.ones((10, 3, 2, 2, 2))
    x[0, 0, 0, 0, 0] = np.nan
    with pytest.raises(ValueError):
        M.summarize(x, x, iterations=10)
