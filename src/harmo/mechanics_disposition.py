"""Evidence-bound final disposition for the paper's OpenSim experiment.

The disposition separates success of the frozen scientific endpoint from
completion of a fully reported negative experiment. It never changes an
upstream threshold or result.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import yaml


SCHEMA_VERSION = "1.0.0"


def _file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_policy(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("mechanics disposition policy must be a mapping")
    if value.get("policy_version") != SCHEMA_VERSION:
        raise ValueError("unsupported mechanics disposition policy version")
    return value


def _lookup(value: Mapping[str, Any], dotted_key: str) -> Any:
    current: Any = value
    for token in dotted_key.split("."):
        if not isinstance(current, Mapping) or token not in current:
            return None
        current = current[token]
    return current


def _git_identity(root: Path) -> Dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(root),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return {"code_commit": None, "code_dirty": None}
    return {"code_commit": commit, "code_dirty": dirty}


def _all_reproducibility_flags_pass(value: Mapping[str, Any]) -> bool:
    results = value.get("results")
    if not isinstance(results, Mapping):
        return False
    flags = [
        flag
        for name, flag in results.items()
        if name.endswith("_byte_identical") and isinstance(flag, bool)
    ]
    return bool(flags) and all(flags)


def _evaluate_object(
    identifier: str,
    value: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> Sequence[Dict[str, Any]]:
    checks = []
    for field, expected_value in expected.items():
        observed = _lookup(value, str(field))
        checks.append(
            {
                "check_id": "{}:{}".format(identifier, field),
                "pass": observed == expected_value,
                "expected": expected_value,
                "observed": observed,
            }
        )
    return checks


def _evaluate_ledger(
    identifier: str,
    rows: Any,
    specification: Mapping[str, Any],
) -> Sequence[Dict[str, Any]]:
    if not isinstance(rows, list):
        return [
            {
                "check_id": "{}:list".format(identifier),
                "pass": False,
                "expected": "JSON list",
                "observed": type(rows).__name__,
            }
        ]
    count = int(specification["expected_count"])
    identity_fields = [str(value) for value in specification["identity_fields"]]
    allowed_outcomes = set(str(value) for value in specification["allowed_outcomes"])
    identities = {
        tuple(row.get(field) for field in identity_fields)
        for row in rows
        if isinstance(row, Mapping)
    }
    observed_outcomes = sorted(
        {
            str(row.get("outcome"))
            for row in rows
            if isinstance(row, Mapping)
        }
    )
    return [
        {
            "check_id": "{}:row_count".format(identifier),
            "pass": len(rows) == count,
            "expected": count,
            "observed": len(rows),
        },
        {
            "check_id": "{}:unique_identities".format(identifier),
            "pass": len(identities) == count,
            "expected": count,
            "observed": len(identities),
        },
        {
            "check_id": "{}:explicit_outcomes".format(identifier),
            "pass": all(
                isinstance(row, Mapping) and str(row.get("outcome")) in allowed_outcomes
                for row in rows
            ),
            "expected": sorted(allowed_outcomes),
            "observed": observed_outcomes,
        },
    ]


def build_mechanics_disposition(
    policy_path: Path,
    results_dir: Path,
    repository_root: Path,
) -> Dict[str, Any]:
    """Build the final G6 disposition without changing upstream results."""

    policy = _load_policy(policy_path.resolve())
    checks = []
    artifacts = []

    policy_path = policy_path.resolve()
    results_dir = results_dir.resolve()
    repository_root = repository_root.resolve()
    for specification in policy.get("required_evidence", []):
        identifier = str(specification["id"])
        path = results_dir / str(specification["file"])
        if not path.is_file():
            checks.append(
                {
                    "check_id": "{}:present".format(identifier),
                    "pass": False,
                    "expected": True,
                    "observed": False,
                }
            )
            continue
        value = _load_json(path)
        artifacts.append(
            {
                "evidence_id": identifier,
                "path": path.relative_to(repository_root).as_posix(),
                "sha256": _file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
        checks.append(
            {
                "check_id": "{}:present".format(identifier),
                "pass": True,
                "expected": True,
                "observed": True,
            }
        )
        kind = str(specification.get("kind", "result"))
        if kind == "ledger":
            checks.extend(_evaluate_ledger(identifier, value, specification))
            continue
        if kind == "reproducibility":
            expected_scope = specification.get("scope")
            observed_scope = (
                value.get("scope") if isinstance(value, Mapping) else None
            )
            identical = bool(
                isinstance(value, Mapping)
                and _all_reproducibility_flags_pass(value)
            )
            checks.extend(
                [
                    {
                        "check_id": "{}:scope".format(identifier),
                        "pass": observed_scope == expected_scope,
                        "expected": expected_scope,
                        "observed": observed_scope,
                    },
                    {
                        "check_id": "{}:byte_identical".format(identifier),
                        "pass": identical,
                        "expected": True,
                        "observed": identical,
                    },
                ]
            )
            continue
        if isinstance(value, Mapping):
            checks.extend(
                _evaluate_object(
                    identifier,
                    value,
                    specification.get("expect") or {},
                )
            )
        else:
            checks.append(
                {
                    "check_id": "{}:mapping".format(identifier),
                    "pass": False,
                    "expected": "JSON object",
                    "observed": type(value).__name__,
                }
            )

    evidence_complete = bool(checks) and all(check["pass"] for check in checks)
    disposition = policy["disposition"]
    reporting_complete = bool(
        evidence_complete
        and policy.get("post_outcome_reporting_policy") is True
        and disposition.get("frozen_endpoint_status") == "failed"
        and disposition.get("mechanics_readiness") == "unvalidated"
        and disposition.get("dynamic_personalization_readiness") == "unvalidated"
        and disposition.get("default_mechanics_export") == "deny"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": "g6-final-mechanics-evidence-disposition",
        "decision_id": policy["decision_id"],
        "policy_sha256": _file_sha256(policy_path),
        "policy_path": policy_path.relative_to(repository_root).as_posix(),
        "post_outcome_reporting_policy": bool(
            policy.get("post_outcome_reporting_policy")
        ),
        "thresholds_or_upstream_results_changed": False,
        "evidence_artifacts": artifacts,
        "evidence_checks": checks,
        "evidence_complete": evidence_complete,
        "reporting_complete": reporting_complete,
        "scientific_endpoint_pass": False,
        "frozen_endpoint_status": disposition["frozen_endpoint_status"],
        "mechanics_readiness": disposition["mechanics_readiness"],
        "dynamic_personalization_readiness": disposition[
            "dynamic_personalization_readiness"
        ],
        "default_mechanics_export": disposition["default_mechanics_export"],
        "artifact_release_states": disposition["artifact_release_states"],
        "allowed_claims": disposition["allowed_claims"],
        "prohibited_claims": disposition["prohibited_claims"],
        "continuation_trigger": disposition["continuation_trigger"],
        "publication_role": disposition["publication_role"],
        "environment": _git_identity(repository_root),
        "pass": False,
    }


def validate_mechanics_disposition(
    value: Mapping[str, Any],
    results_dir: Path,
    repository_root: Path,
    policy_path: Path,
) -> Dict[str, bool]:
    """Validate a stored disposition and its checksum-bound dependencies."""

    policy_path = policy_path.resolve()
    results_dir = results_dir.resolve()
    repository_root = repository_root.resolve()
    artifacts = value.get("evidence_artifacts") or []
    hashes_match = bool(artifacts)
    for artifact in artifacts:
        path = repository_root / str(artifact.get("path", ""))
        hashes_match = bool(
            hashes_match
            and path.is_file()
            and _file_sha256(path) == artifact.get("sha256")
            and path.stat().st_size == artifact.get("size_bytes")
        )
    required_paths = {
        (results_dir / str(specification["file"]))
        .relative_to(repository_root)
        .as_posix()
        for specification in _load_policy(policy_path).get("required_evidence", [])
    }
    bound_paths = {
        str(artifact.get("path", "")) for artifact in artifacts
    }
    return {
        "scope": value.get("scope") == "g6-final-mechanics-evidence-disposition",
        "policy_hash": value.get("policy_sha256") == _file_sha256(policy_path),
        "all_required_evidence_bound": required_paths == bound_paths,
        "evidence_hashes": hashes_match,
        "evidence_complete": value.get("evidence_complete") is True,
        "reporting_complete": value.get("reporting_complete") is True,
        "negative_endpoint_retained": (
            value.get("scientific_endpoint_pass") is False
            and value.get("pass") is False
            and value.get("frozen_endpoint_status") == "failed"
        ),
        "mechanics_not_promoted": (
            value.get("mechanics_readiness") == "unvalidated"
            and value.get("dynamic_personalization_readiness") == "unvalidated"
            and value.get("default_mechanics_export") == "deny"
        ),
        "upstream_results_unchanged": (
            value.get("thresholds_or_upstream_results_changed") is False
        ),
        "post_outcome_label": value.get("post_outcome_reporting_policy") is True,
    }


def write_mechanics_disposition(value: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
