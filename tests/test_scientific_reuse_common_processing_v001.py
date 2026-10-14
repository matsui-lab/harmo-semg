import importlib.util
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location("common", Path(__file__).parents[1] / "scripts/paper/scientific_reuse_common_processing_v001.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


@pytest.mark.parametrize("fs", [800, 960, 1000, 2000])
def test_delayed_normalization_matches_frozen_cycle_function(fs):
    p = M.load_physiology_protocol(M.ROOT / "paper/physiology_positive_control_protocol.yaml")
    x = np.random.default_rng(12).normal(size=fs)
    env = M.common_envelope(x, fs, p["study_38_envelope"])
    assert M.close(M.cut_normalize(env, 0, len(env)), M.preprocess_study38_cycle(x, fs, p["study_38_envelope"]))


def test_cut_normalizes_cycle_not_whole_trial():
    x = np.array([1000, 1000, 1, 2, 3, 4, 5, 1000], float)
    actual = M.cut_normalize(x, 2, 7)
    assert M.close(actual, M._normalise_cycle([1, 2, 3, 4, 5]))
    assert actual.max() == 1
    with pytest.raises(ValueError):
        M.cut_normalize(x, -1, 7)


def test_bootstrap_keeps_all_conditions_and_muscles_paired():
    left = np.arange(20).reshape(10, 2)
    right = np.stack([left + 2, left + 5, left - 1], axis=1)
    r = M.summarize(left, right, iterations=500)
    assert np.allclose(r["study_differences"], [[2, 2], [5, 5], [-1, -1]])
    for name, expected in [("cycle_minus_existing", -3), ("trial_minus_existing", 3), ("cycle_minus_trial", -6)]:
        assert np.allclose(r["contrasts"][name]["change_ci"], expected)
        assert np.allclose(r["contrasts"][name]["median_participant_change_ci"], expected)
    assert r == M.summarize(left, right, iterations=500)


def test_missing_values_are_not_silently_removed():
    with pytest.raises(ValueError):
        M.summarize([[1, 2], [np.nan, 3]], np.ones((2, 3, 2)), iterations=5)
