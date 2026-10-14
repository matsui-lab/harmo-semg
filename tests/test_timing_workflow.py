import json
from pathlib import Path
import pytest

from harmo.timing_workflow import checked_profile, verify_catalog_curves
from harmo.reuse_workflow import Stages, acquire_sources, run

ROOT = Path(__file__).resolve().parents[1]


def test_changed_protocol_is_not_registered(tmp_path):
    path = tmp_path / "protocol.yaml"
    path.write_text((ROOT / "paper/physiology_positive_control_protocol.yaml").read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="unregistered_timing_protocol"):
        checked_profile(path)


def test_catalog_curve_identity_and_denominator():
    curves = [dict(subject_id=str(i), muscle="TA", values=[0., 1.]) for i in range(20)]
    verify_catalog_curves({"curves": curves}, curves)
    changed = json.loads(json.dumps(curves))
    changed[0]["values"][1] = 1.01
    with pytest.raises(ValueError, match="processing_differ"):
        verify_catalog_curves({"curves": changed}, curves)
    with pytest.raises(ValueError, match="denominator"):
        verify_catalog_curves({"curves": curves[:1]}, curves)


def test_stage_resume_checks_inputs_outputs_and_failed_attempts(tmp_path):
    state = Stages(tmp_path / "workflow")
    calls = []
    def action(path):
        calls.append(path)
        path.mkdir()
        (path / "result.json").write_text("{}")
        return {"ok": True}
    assert state.run("one", {"input": "a"}, action) == {"ok": True}
    again = Stages(state.output, resume=True)
    assert again.run("one", {"input": "a"}, action) == {"ok": True}
    assert len(calls) == 1
    with pytest.raises(ValueError, match="inputs_changed"):
        again.run("one", {"input": "b"}, action)
    (calls[0] / "result.json").write_text("changed")
    with pytest.raises(ValueError, match="output_changed"):
        again.run("one", {"input": "a"}, action)
    def fail(path):
        path.mkdir()
        (path / "partial").write_text("preserve")
        raise ValueError("interrupted")
    with pytest.raises(ValueError, match="interrupted"):
        state.run("two", {}, fail)
    assert not (state.output / "two.receipt.json").exists()
    state.run("two", {}, action)
    assert (state.output / "two-001/partial").read_text() == "preserve"
    assert (state.output / "two-002/result.json").exists()


def test_unknown_urls_rejected_before_writes(tmp_path):
    with pytest.raises(ValueError, match="exact_two_registered"):
        run(["https://example.org"], tmp_path, "absent", "absent", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_acquisition_budget_and_existing_corruption(tmp_path):
    protocol = {"source_assets": {"study": {"a.mat": {"bytes": 3, "md5": "0"*32, "file_id": 1}}}}
    with pytest.raises(ValueError, match="budget"):
        acquire_sources(tmp_path, protocol, 2, True)
    with pytest.raises(ValueError, match="missing_cache"):
        acquire_sources(tmp_path, protocol, 3, False)
    (tmp_path / "a.mat").write_bytes(b"bad")
    with pytest.raises(ValueError, match="integrity_failure"):
        acquire_sources(tmp_path, protocol, 3, True)
    assert (tmp_path / "a.mat").read_bytes() == b"bad"
