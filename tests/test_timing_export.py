import json
import numpy as np
import pytest
from test_comparability_resolution import _run_resolution
from harmo.timing_export import export_pair


def fixture(tmp_path, monkeypatch):
    # Synthetic clean-code fixture tests the gate, not a real release approval.
    monkeypatch.setattr("harmo.evidence.resolution.current_repository_state",
        lambda: {"commit": "a"*40, "dirty": False, "working_tree_sha256": None})
    return _run_resolution(tmp_path)[1]


def test_released_arrays_match_selected_channel(tmp_path, monkeypatch):
    resolved = fixture(tmp_path, monkeypatch)
    result = export_pair(resolved, tmp_path / "export")
    assert result["amplitude_pooling"] is False
    with np.load(tmp_path / "export/timing.npz", allow_pickle=False) as arrays:
        assert set(arrays.files) == {"left_phase", "right_phase", "left_values", "right_values"}
        assert arrays["left_values"].shape == (101,)
        assert np.array_equal(arrays["left_phase"], np.linspace(0, 100, 101))
    with pytest.raises(ValueError, match="output_exists"):
        export_pair(resolved, tmp_path / "export")


def test_changed_phase_never_exports_signal(tmp_path, monkeypatch):
    resolved = fixture(tmp_path, monkeypatch)
    (resolved / "left/resolved.phase.csv").write_text("gait_cycle_percent\n0\n99\n")
    with pytest.raises(ValueError, match="phase_changed"):
        export_pair(resolved, tmp_path / "export")
    assert not (tmp_path / "export/timing.npz").exists()


def test_held_release_never_exports_signal(tmp_path, monkeypatch):
    resolved = fixture(tmp_path, monkeypatch)
    path = resolved / "left/release.decision.json"
    data = json.loads(path.read_text())
    data["status"] = "quarantine"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unconditional_release"):
        export_pair(resolved, tmp_path / "export")
    assert not (tmp_path / "export").exists()
