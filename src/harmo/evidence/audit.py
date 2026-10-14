"""Lineage audit, reproducibility comparison, and Atlas exchange export."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from .io import read_json, sha256_contract, write_json_atomic
from .models import (
    Comparability,
    ComparabilityAssertion,
    EvidenceManifest,
    HarmoRecordingBundle,
    ReleaseDecision,
    ReleaseStatus,
    TrustReport,
    TrustStatus,
)


def audit_manifest(manifest: EvidenceManifest, artifact_id: Optional[str] = None) -> Dict[str, Any]:
    artifacts = {a.artifact_id: a for a in manifest.inputs + manifest.outputs}
    if artifact_id and artifact_id not in artifacts:
        raise KeyError("artifact not found: {}".format(artifact_id))
    selected = [artifact_id] if artifact_id else sorted(artifacts)
    lineage = []
    for current in selected:
        producing = [s.step_id for s in manifest.steps if current in s.outputs]
        consuming = [s.step_id for s in manifest.steps if current in s.inputs]
        lineage.append({
            "artifact": artifacts[current].to_dict(),
            "produced_by": producing,
            "consumed_by": consuming,
        })
    return {
        "run_id": manifest.run_id,
        "code_commit": manifest.code_commit,
        "config_sha256": manifest.config_sha256,
        "manifest_digest": sha256_contract(manifest),
        "lineage": lineage,
    }


def _compare_values(expected: Any, actual: Any, path: str, atol: float, rtol: float, diffs: List[Dict[str, Any]]) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected or key not in actual:
                diffs.append({"path": path + "/" + key, "expected": expected.get(key), "actual": actual.get(key)})
            else:
                _compare_values(expected[key], actual[key], path + "/" + key, atol, rtol, diffs)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            diffs.append({"path": path, "expected_length": len(expected), "actual_length": len(actual)})
        for index, (left, right) in enumerate(zip(expected, actual)):
            _compare_values(left, right, path + "/" + str(index), atol, rtol, diffs)
    elif isinstance(expected, bool) or isinstance(actual, bool):
        if type(expected) is not type(actual) or expected is not actual:
            diffs.append({"path": path, "expected": expected, "actual": actual})
    elif (
        isinstance(expected, (int, float))
        and isinstance(actual, (int, float))
        and not isinstance(expected, bool)
        and not isinstance(actual, bool)
    ):
        left = float(expected)
        right = float(actual)
        if not math.isfinite(left) or not math.isfinite(right):
            diffs.append(
                {
                    "path": path,
                    "expected": expected,
                    "actual": actual,
                    "reason": "non-finite numeric value",
                }
            )
        elif not math.isclose(left, right, rel_tol=rtol, abs_tol=atol):
            diffs.append({"path": path, "expected": expected, "actual": actual})
    elif expected != actual:
        diffs.append({"path": path, "expected": expected, "actual": actual})


def compare_contracts(expected: Dict[str, Any], actual: Dict[str, Any], atol: float = 1e-9, rtol: float = 1e-7) -> Dict[str, Any]:
    diffs = []  # type: List[Dict[str, Any]]
    _compare_values(expected, actual, "", atol, rtol, diffs)
    return {"matches": not diffs, "difference_count": len(diffs), "differences": diffs}


def export_atlas_exchange(
    output_dir: str,
    bundle: HarmoRecordingBundle,
    manifest: EvidenceManifest,
    trust: TrustReport,
    comparison: ComparabilityAssertion,
    release_decision: ReleaseDecision,
) -> Dict[str, str]:
    """Write an evidence-qualified, non-destructive EMG ATLAS exchange package."""
    if bundle.recording_id != trust.subject_id:
        raise ValueError(
            "trust report subject does not match recording: {} != {}".format(
                trust.subject_id, bundle.recording_id
            )
        )
    if bundle.recording_id != release_decision.subject_id:
        raise ValueError("release decision does not match recording")
    if bundle.recording_id not in (comparison.left_id, comparison.right_id):
        raise ValueError("comparability assertion does not include recording")
    if comparison.intended_use != release_decision.intended_use:
        raise ValueError("release intended use does not match comparability assertion")
    bundle_digest = sha256_contract(bundle)
    linked_outputs = [
        artifact
        for artifact in manifest.outputs
        if artifact.digest_kind == "sha256-canonical-json"
        and artifact.sha256 == bundle_digest
    ]
    if not linked_outputs:
        raise ValueError(
            "manifest does not contain the canonical recording bundle digest"
        )
    contract_digests = {
        "recording": bundle_digest,
        "manifest": sha256_contract(manifest),
        "trust": sha256_contract(trust),
        "comparability": sha256_contract(comparison),
    }
    declared_digests = {
        "recording": release_decision.recording_sha256,
        "manifest": release_decision.manifest_sha256,
        "trust": release_decision.trust_report_sha256,
        "comparability": release_decision.comparability_assertion_sha256,
    }
    mismatches = [
        name
        for name in contract_digests
        if contract_digests[name] != declared_digests[name]
    ]
    if mismatches:
        raise ValueError(
            "release decision digest mismatch: {}".format(", ".join(mismatches))
        )
    if not release_decision.export_permitted:
        raise ValueError(
            "release decision {} forbids exchange export".format(
                release_decision.status.value
            )
        )
    blocking_trust = {
        dimension: trust.dimension_status(dimension)
        for dimension in release_decision.required_dimensions
        if trust.dimension_status(dimension)
        in (TrustStatus.FAIL, TrustStatus.UNASSESSED)
    }
    if blocking_trust:
        raise ValueError(
            "required trust evidence blocks export: {}".format(
                ", ".join(
                    "{}={}".format(key, value.value)
                    for key, value in sorted(blocking_trust.items())
                )
            )
        )
    if comparison.status in (
        Comparability.NOT_COMPARABLE,
        Comparability.UNASSESSED,
    ):
        raise ValueError(
            "comparability status {} blocks export".format(
                comparison.status.value
            )
        )
    if release_decision.status == ReleaseStatus.ALLOW:
        nonpassing = {
            dimension: trust.dimension_status(dimension)
            for dimension in release_decision.required_dimensions
            if trust.dimension_status(dimension) != TrustStatus.PASS
        }
        if nonpassing or comparison.status != Comparability.COMPARABLE:
            raise ValueError("unconditional release requires passing trust and comparability")
    if (
        release_decision.status == ReleaseStatus.CONDITIONAL
        and not release_decision.conditions
    ):
        raise ValueError("conditional release requires explicit conditions")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "recording": write_json_atomic(str(output / "recording.bundle.json"), bundle),
        "manifest": write_json_atomic(str(output / "evidence.manifest.json"), manifest),
        "trust": write_json_atomic(str(output / "trust.report.json"), trust),
        "comparability": write_json_atomic(
            str(output / "comparability.assertion.json"), comparison
        ),
        "release_decision": write_json_atomic(
            str(output / "release.decision.json"), release_decision
        ),
    }
    index = {
        "schema": "https://matsui-lab.github.io/emg-atlas/schemas/exchange-2.0.0.json",
        "recording_id": bundle.recording_id,
        "recording_digest": bundle_digest,
        "run_id": manifest.run_id,
        "intended_use": release_decision.intended_use,
        "release_status": release_decision.status.value,
        "release_conditions": release_decision.conditions,
        "files": {name: Path(path).name for name, path in files.items()},
        "digests": {name: sha256_contract(read_json(path)) for name, path in files.items()},
        "trust_summary": {
            dimension: trust.dimension_status(dimension).value
            for dimension in (
                "semantic_fidelity",
                "scientific_validity",
                "computational_reproducibility",
                "traceability",
            )
        },
    }
    files["index"] = write_json_atomic(str(output / "atlas.exchange.json"), index)
    return files
