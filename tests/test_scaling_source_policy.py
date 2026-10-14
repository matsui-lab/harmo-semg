import pytest

from harmo.analysis.data_prep import _scaling_source_policy, select_scaling_trial


def _policy(require_static, allow_dynamic):
    return {
        "scaling": {
            "source_policy": {
                "require_static_calibration": require_static,
                "allow_dynamic_pseudo_static": allow_dynamic,
            }
        }
    }


def test_confirmatory_policy_rejects_dynamic_pseudo_static_trial():
    trials = [{"session_id": "V25-T1", "hed_ontology": "(Walking)"}]

    with pytest.raises(ValueError, match="dynamic pseudo-static"):
        select_scaling_trial(trials, _policy(True, False))


def test_explicit_standing_trial_is_selected_under_confirmatory_policy():
    walking = {"session_id": "V25-T1", "hed_ontology": "(Walking)"}
    standing = {"session_id": "Static", "hed_ontology": "(Standing)"}

    assert select_scaling_trial([walking, standing], _policy(True, False)) is standing


def test_legacy_default_remains_explicit_and_discoverable():
    assert _scaling_source_policy(None) == {
        "require_static_calibration": False,
        "allow_dynamic_pseudo_static": True,
    }
