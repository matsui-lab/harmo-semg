#!/usr/bin/env python3
"""Compare separately executed Experiment F or G result directories.

The source-reading commands deliberately write one self-contained result
directory per process.  This comparator turns two such directories into the
machine-readable cross-process reproducibility artifacts used by the paper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


ADAPTER_FILES = {
    "trial_manifest": "ultra_mocap_trial_manifest.json",
    "source_manifest": "ultra_mocap_source_manifest.json",
    "within_process_reproducibility_file": (
        "ultra_mocap_adapter_reproducibility.json"
    ),
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError("required reproduction artifact is missing: {}".format(path))
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("{} must contain a JSON object".format(path))
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _artifact_comparison(run_dirs: Sequence[Path], filename: str) -> Dict[str, Any]:
    runs = []
    for index, directory in enumerate(run_dirs, 1):
        path = directory / filename
        if not path.is_file():
            raise FileNotFoundError(
                "required reproduction artifact is missing: {}".format(path)
            )
        runs.append(
            {
                "run": index,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    identical = len({(row["bytes"], row["sha256"]) for row in runs}) == 1
    return {
        "bytes": runs[0]["bytes"],
        "sha256": runs[0]["sha256"],
        "byte_identical": identical,
        "runs": runs,
    }


def _optional_run_record(directory: Path, filename: str) -> Optional[Dict[str, Any]]:
    path = directory / filename
    if not path.is_file():
        return None
    return read_json(path)


def compare_adapter_runs(run_dirs: Sequence[Path]) -> Dict[str, Any]:
    if len(run_dirs) != 2:
        raise ValueError("Experiment F comparison requires exactly two run directories")
    metrics = [read_json(path / "adapter_metrics.json") for path in run_dirs]
    scientific_metrics = []
    for value in metrics:
        retained = dict(value)
        retained.pop("active_work_seconds", None)
        scientific_metrics.append(retained)

    metric_digests = [canonical_sha256(value) for value in scientific_metrics]
    artifacts = {
        role: _artifact_comparison(run_dirs, filename)
        for role, filename in ADAPTER_FILES.items()
    }
    repeat_counts = [int(value.get("repeat_count", 0)) for value in metrics]
    normalized_digests = [
        value.get("normalized_manifest_sha256") for value in metrics
    ]
    source_runs_pass = all(value.get("pass") is True for value in metrics)
    scientific_identical = len(set(metric_digests)) == 1
    files_identical = all(value["byte_identical"] for value in artifacts.values())
    complete_scans = repeat_counts[0] if len(set(repeat_counts)) == 1 else repeat_counts
    run_records = [
        _optional_run_record(path, "ultra_mocap_adapter_run.json")
        for path in run_dirs
    ]

    result = {
        "schema_version": "1.1",
        "experiment": "F held-out ULTRA-MoCap adapter reproducibility",
        "dataset": metrics[0].get("dataset"),
        "independent_process_count": 2,
        "complete_scans_per_process": complete_scans,
        "total_complete_scans": sum(repeat_counts),
        "run_active_work_seconds": [
            value.get("active_work_seconds") for value in metrics
        ],
        "normalized_manifest_sha256": normalized_digests,
        "trial_manifest": artifacts["trial_manifest"],
        "source_manifest": artifacts["source_manifest"],
        "within_process_reproducibility_file": artifacts[
            "within_process_reproducibility_file"
        ],
        "scientific_metrics_sha256_excluding_execution_time": metric_digests[0],
        "scientific_metrics_run_sha256": metric_digests,
        "scientific_metrics_identical_excluding_execution_time": (
            scientific_identical
        ),
        "core_comparison_baseline_commit": metrics[0].get(
            "core_comparison_baseline_commit"
        ),
        "core_comparison_implementation_commit": metrics[0].get(
            "core_comparison_implementation_commit"
        ),
        "run_records": run_records,
        "source_runs_pass": source_runs_pass,
        "pass": bool(source_runs_pass and scientific_identical and files_identical),
    }
    return result


def compare_physiology_runs(
    run_dirs: Sequence[Path],
    correction_record_path: Path,
    reference_code_commit: str,
) -> Dict[str, Any]:
    if len(run_dirs) != 2:
        raise ValueError("Experiment G comparison requires exactly two run directories")
    filename = "physiology_positive_control.json"
    artifact = _artifact_comparison(run_dirs, filename)
    payloads = [read_json(path / filename) for path in run_dirs]
    run_records = [
        _optional_run_record(path, "physiology_positive_control_run.json")
        for path in run_dirs
    ]
    correction = read_json(correction_record_path)
    internal_digests = [value.get("result_sha256") for value in payloads]
    protocol_digests = [value.get("protocol_sha256") for value in payloads]
    execution_commits = []
    for record in run_records:
        environment = (record or {}).get("environment") or {}
        execution_commits.append(environment.get("code_commit"))
    known_commits = [value for value in execution_commits if value]
    scientific_pass = all(
        value.get("reporting_complete") is True
        and value.get("scientific_endpoint_pass") is True
        and value.get("amplitude_pooled_across_studies") is False
        for value in payloads
    )
    internal_identical = len(set(internal_digests)) == 1 and bool(
        internal_digests[0]
    )
    protocol_identical = len(set(protocol_digests)) == 1 and bool(
        protocol_digests[0]
    )
    byte_identical = bool(artifact["byte_identical"])
    pass_value = bool(
        scientific_pass and internal_identical and protocol_identical and byte_identical
    )

    runs = []
    for index, path in enumerate(run_dirs, 1):
        runs.append(
            {
                "run": index,
                "output": str(path / filename),
                "file_sha256": artifact["runs"][index - 1]["sha256"],
                "execution_record": run_records[index - 1],
            }
        )

    code_commit = known_commits[0] if len(set(known_commits)) == 1 else None
    return {
        "schema_version": "1.1",
        "experiment": "G cross-study gait physiological positive control",
        "reference_code_commit": reference_code_commit,
        "code_commit": code_commit,
        "execution_commits": execution_commits,
        "execution_differs_from_frozen_commit": bool(
            known_commits
            and any(value != reference_code_commit for value in known_commits)
        ),
        "bound_code_and_protocol_differs_from_commit": bool(
            known_commits
            and any(value != reference_code_commit for value in known_commits)
        ),
        "protocol_sha256": protocol_digests[0],
        "channel_mapping_correction_sha256": file_sha256(correction_record_path),
        "invalid_pre_correction_result_sha256": correction.get(
            "invalid_result_sha256"
        ),
        "corrected_internal_result_sha256": internal_digests[0],
        "corrected_result_file_sha256": artifact["sha256"],
        "corrected_result_bytes": artifact["bytes"],
        "runs": runs,
        "byte_identical": byte_identical,
        "reporting_complete": all(
            value.get("reporting_complete") is True for value in payloads
        ),
        "scientific_endpoint_pass": scientific_pass,
        "amplitude_pooled_across_studies": any(
            value.get("amplitude_pooled_across_studies") is True
            for value in payloads
        ),
        "pass": pass_value,
    }


def _parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Compare separate Experiment F or G reproduction runs"
    )
    parser.add_argument("--root", default=str(root))
    commands = parser.add_subparsers(dest="command", required=True)

    adapter = commands.add_parser("adapter", help="compare two Experiment F runs")
    adapter.add_argument("run_dirs", nargs=2)
    adapter.add_argument("--output", required=True)

    physiology = commands.add_parser(
        "physiology", help="compare two Experiment G runs"
    )
    physiology.add_argument("run_dirs", nargs=2)
    physiology.add_argument("--output", required=True)
    physiology.add_argument(
        "--correction-record",
        default="paper/results/validation/g3_channel_mapping_correction.json",
    )
    physiology.add_argument(
        "--reference-code-commit",
        default="2cf1496b2281d32af442e3643f0617dc21f61e3c",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    run_dirs = [Path(value).resolve() for value in args.run_dirs]
    if args.command == "adapter":
        result = compare_adapter_runs(run_dirs)
    elif args.command == "physiology":
        correction = Path(args.correction_record)
        if not correction.is_absolute():
            correction = root / correction
        result = compare_physiology_runs(
            run_dirs,
            correction,
            args.reference_code_commit,
        )
    else:
        raise AssertionError(args.command)

    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    write_json(output, result)
    print(
        json.dumps(
            {"output": str(output), "pass": result["pass"]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
