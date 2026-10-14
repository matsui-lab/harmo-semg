import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "readiness", Path(__file__).parents[1] / "scripts/paper/check_scientific_reuse_readiness_v001.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def test_numeric_check_has_no_broadcast_or_nan_acceptance():
    assert M.close([1, 2], [1, 2 + 1e-12])
    assert not M.close([1], [1, 1])
    assert not M.close([float("nan")], [float("nan")])
    assert not M.close([1], [1.0001])


def test_local_repository_definitions_are_not_public_file_baseline():
    assert M.source_category({"source_type": "repository-config"}) == "analyst_configuration_origin_to_trace"
    assert M.source_category({"source_type": "unknown"}) == "unclassified_requires_review"


def fixture():
    return {
        "sources": [{"source_id": "s", "source_type": "repository-config", "uri": "studies/x.yaml"}],
        "claims": [{"claim_id": "c", "source_id": "s", "study_id": "x", "property_path": "muscle"}],
    }


def test_ledger_does_not_grant_permission_or_confirm_current_documents():
    row, = M.claim_ledger(fixture())
    assert row["P0_eligibility"] == "not_established"
    assert not row["reuse_permission_established"]
    assert not row["current_document_revalidated"]


def test_duplicate_and_missing_sources_fail_instead_of_silent_drop():
    data = fixture()
    data["sources"] *= 2
    with pytest.raises(ValueError):
        M.claim_ledger(data)
    data = fixture()
    data["claims"] *= 2
    with pytest.raises(ValueError):
        M.claim_ledger(data)
    data = fixture()
    data["sources"] = []
    with pytest.raises(KeyError):
        M.claim_ledger(data)
