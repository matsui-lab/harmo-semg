import importlib.util
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location(
    "rms_sensitivity", Path(__file__).parents[1] / "scripts/paper/scientific_reuse_rms_sensitivity_v001.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


@pytest.mark.parametrize("window", [4, 5, 100, 125])
def test_constant_signal_has_expected_endpoint_effect_only(window):
    fixed, observed = M.rms_pair(np.ones(500), window)
    assert np.allclose(observed, 1, rtol=0, atol=1e-14)
    assert fixed[0] < 1 and fixed[-1] < 1
    assert np.allclose(fixed[window:-window], observed[window:-window], rtol=0, atol=1e-14)


@pytest.mark.parametrize("fs", [800, 960, 1000])
def test_baseline_matches_original_function(fs):
    p = M.load_physiology_protocol(M.ROOT / "paper/physiology_positive_control_protocol.yaml")
    raw = np.random.default_rng(9).normal(size=fs)
    pair = M.cycle_pair(raw, fs, p["study_38_envelope"])
    assert M.close(pair[0], M.preprocess_study38_cycle(raw, fs, p["study_38_envelope"]))
    assert pair[0].shape == pair[1].shape == (101,)


def test_invalid_input_fails_closed():
    for values, window in [([1, np.nan], 2), ([1, 2], 3), ([[1, 2]], 1), ([1, 2], 0)]:
        with pytest.raises(ValueError):
            M.rms_pair(values, window)


def test_bootstrap_keeps_conditions_paired_and_sign_correct():
    baseline = np.arange(20).reshape(10, 2)
    left = np.stack([baseline, baseline + 3], axis=1)
    right = baseline + 2
    result = M.bootstrap(left, right, iterations=1000)
    assert np.allclose(result["study_difference"], [[2, 2], [-1, -1]])
    assert np.allclose(result["change_in_study_difference"], [-3, -3])
    assert np.allclose(result["change_in_study_difference_ci"], [[-3, -3], [-3, -3]])
    assert np.allclose(result["median_within_participant_change_ci"], [[3, 3], [3, 3]])
    assert result == M.bootstrap(left, right, iterations=1000)


def test_identical_conditions_have_zero_change():
    a = np.arange(20).reshape(10, 2)
    result = M.bootstrap(np.stack([a, a], axis=1), a, iterations=100)
    assert np.allclose(result["change_in_study_difference_ci"], 0)
