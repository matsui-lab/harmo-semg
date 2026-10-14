"""Checksum-bound, static-hidden dynamic calibration benchmark.

The functions in this module do not treat a walking frame as a static trial.
They estimate segment measurement lengths independently within each dynamic
trial, combine trial estimates with equal weight, and reveal the held-out
static trial only when calculating benchmark error.

This is the first, model-independent gate of G6b.  Passing it establishes that
the marker measurements needed for scaling are recoverable; it does not by
itself establish IK, inverse-dynamics, RRA, or muscle-force validity.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd
import yaml

from harmo.ingest.readers.trc import read_trc_file


DYNAMIC_CALIBRATION_SCHEMA_VERSION = "1.0.0"
_SHA256_LENGTH = 64


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("YAML root must be a mapping: {}".format(path))
    return value


def _safe_asset_path(root: Path, name: str) -> Path:
    relative = Path(str(name))
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 1:
        raise ValueError("public source local_name must be one safe filename")
    path = root / relative
    if path.resolve().parent != root.resolve():
        raise ValueError("public source path escapes data root")
    return path


def validate_public_source_bundle(
    manifest_path: Path, data_root: Path
) -> Dict[str, Any]:
    """Verify every declared public artifact by byte size and SHA-256."""
    manifest_path = manifest_path.resolve()
    data_root = data_root.resolve()
    manifest = _load_yaml(manifest_path)
    if str(manifest.get("schema_version")) != "1.0.0":
        raise ValueError("unsupported G6b public source manifest version")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("G6b public source manifest has no assets")
    roles: Dict[str, Dict[str, Any]] = {}
    rows: List[Dict[str, Any]] = []
    for raw in assets:
        if not isinstance(raw, Mapping):
            raise ValueError("G6b public source asset must be a mapping")
        role = str(raw.get("role", "")).strip()
        if not role or role in roles:
            raise ValueError("G6b public source roles must be nonempty and unique")
        expected_hash = str(raw.get("sha256", "")).strip().lower()
        if len(expected_hash) != _SHA256_LENGTH or any(
            character not in "0123456789abcdef" for character in expected_hash
        ):
            raise ValueError("invalid SHA-256 for public source role {}".format(role))
        expected_size = int(raw.get("size_bytes", 0))
        if expected_size <= 0:
            raise ValueError("invalid byte size for public source role {}".format(role))
        path = _safe_asset_path(data_root, str(raw.get("local_name", "")))
        observed_size = path.stat().st_size if path.is_file() else None
        observed_hash = sha256_file(path) if path.is_file() else None
        passed = observed_size == expected_size and observed_hash == expected_hash
        row = dict(raw)
        row.update(
            {
                "absolute_path": str(path),
                "present": path.is_file(),
                "observed_size_bytes": observed_size,
                "observed_sha256": observed_hash,
                "pass": passed,
            }
        )
        rows.append(row)
        roles[role] = row
    checks = {
        "manifest_sha256_valid": True,
        "roles_unique": len(roles) == len(rows),
        "all_assets_verified": all(row["pass"] for row in rows),
    }
    return {
        "schema_version": DYNAMIC_CALIBRATION_SCHEMA_VERSION,
        "scope": "g6b-public-source-audit",
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "data_root": str(data_root),
        "source_record_doi": manifest.get("source_record_doi"),
        "license": manifest.get("license"),
        "checks": checks,
        "pass": all(checks.values()),
        "assets": rows,
        "assets_by_role": roles,
    }


def download_public_source_bundle(
    manifest_path: Path, data_root: Path
) -> Dict[str, Any]:
    """Download missing declared artifacts without overwriting mismatched data."""
    manifest = _load_yaml(manifest_path.resolve())
    data_root = data_root.resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    for raw in manifest.get("assets", []):
        role = str(raw.get("role", ""))
        path = _safe_asset_path(data_root, str(raw.get("local_name", "")))
        expected_size = int(raw.get("size_bytes", 0))
        expected_hash = str(raw.get("sha256", "")).lower()
        if path.exists():
            if (
                path.is_file()
                and path.stat().st_size == expected_size
                and sha256_file(path) == expected_hash
            ):
                continue
            raise ValueError(
                "refusing to overwrite mismatched public source role {}".format(role)
            )
        url = str(raw.get("download_url", "")).strip()
        if not url.startswith("https://"):
            raise ValueError("public source download URL must use HTTPS")
        partial = path.with_name(path.name + ".part")
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                with partial.open("wb") as output:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
            if partial.stat().st_size != expected_size:
                raise ValueError("downloaded byte size mismatch for {}".format(role))
            if sha256_file(partial) != expected_hash:
                raise ValueError("downloaded SHA-256 mismatch for {}".format(role))
            os.replace(str(partial), str(path))
        finally:
            if partial.exists():
                partial.unlink()
    audit = validate_public_source_bundle(manifest_path, data_root)
    if not audit["pass"]:
        raise ValueError("downloaded G6b source bundle failed verification")
    return audit


def _trc_units(header: pd.DataFrame) -> str:
    if header.empty:
        return ""
    lookup = {str(key).lower(): value for key, value in header.iloc[0].items()}
    return str(lookup.get("units", "")).strip().lower()


def _read_verified_trc(asset: Mapping[str, Any]) -> pd.DataFrame:
    if asset.get("pass") is not True:
        raise ValueError("cannot read an unverified G6b source artifact")
    path = Path(str(asset["absolute_path"]))
    header, data = read_trc_file(str(path))
    unit = _trc_units(header)
    if unit not in {"m", "mm"}:
        raise ValueError("G6b TRC must declare m or mm units")
    if "time" not in data:
        raise ValueError("G6b TRC has no time column")
    time = pd.to_numeric(data["time"], errors="coerce").to_numpy(float)
    if time.size < 2 or not np.all(np.isfinite(time)) or np.any(np.diff(time) <= 0):
        raise ValueError("G6b TRC timebase must be finite and strictly increasing")
    marker_columns = [column for column in data.columns if column not in {"time", "frame_num"}]
    numeric = data[marker_columns].apply(pd.to_numeric, errors="coerce")
    if unit == "mm":
        numeric = numeric / 1000.0
    numeric.insert(0, "time", time)
    return numeric


def _marker_xyz(data: pd.DataFrame, marker: str) -> np.ndarray:
    columns = ["{}_{}".format(marker, axis) for axis in "XYZ"]
    missing = [column for column in columns if column not in data]
    if missing:
        raise ValueError(
            "marker {} is missing coordinate columns {}".format(marker, missing)
        )
    return data[columns].to_numpy(float)


def _pair_distances(data: pd.DataFrame, marker_a: str, marker_b: str) -> np.ndarray:
    difference = _marker_xyz(data, marker_a) - _marker_xyz(data, marker_b)
    distances = np.linalg.norm(difference, axis=1)
    return distances[np.isfinite(distances) & (distances > 0)]


def _robust_estimate(values: np.ndarray, outlier_mad: float) -> Dict[str, Any]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values) & (values > 0)]
    if values.size == 0:
        raise ValueError("marker pair has no finite positive distances")
    median = float(np.median(values))
    absolute = np.abs(values - median)
    mad = float(np.median(absolute))
    if mad == 0:
        keep = np.isclose(values, median, rtol=0.0, atol=1e-12)
        if int(keep.sum()) < max(2, int(math.ceil(values.size * 0.5))):
            keep = np.ones(values.shape, dtype=bool)
    else:
        robust_z = 0.6744897501960817 * absolute / mad
        keep = robust_z <= float(outlier_mad)
    retained = values[keep]
    if retained.size == 0:
        raise ValueError("marker pair outlier rejection retained no samples")
    return {
        "estimate_m": float(np.median(retained)),
        "mad_m": mad,
        "sample_count": int(values.size),
        "inlier_count": int(retained.size),
        "inlier_fraction": float(retained.size / values.size),
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), percentile))


def _bootstrap_trial_median(
    estimates: Sequence[float], iterations: int, seed: int
) -> Tuple[float, float]:
    array = np.asarray(estimates, dtype=float)
    if array.size < 2:
        raise ValueError("dynamic calibration requires at least two trials")
    generator = np.random.default_rng(int(seed))
    sampled = generator.choice(array, size=(int(iterations), array.size), replace=True)
    medians = np.median(sampled, axis=1)
    return _percentile(medians, 2.5), _percentile(medians, 97.5)


def run_dynamic_calibration_benchmark(
    protocol_path: Path, manifest_path: Path, data_root: Path
) -> Dict[str, Any]:
    """Run the preregistered G6b marker-measurement recovery benchmark."""
    protocol_path = protocol_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != "1.0.0":
        raise ValueError("unsupported G6b protocol version")
    audit = validate_public_source_bundle(manifest_path, data_root)
    if not audit["pass"]:
        raise ValueError("G6b public source bundle did not pass checksum audit")
    roles = audit["assets_by_role"]
    pilot = protocol.get("pilot") or {}
    static_role = str(pilot.get("static_reference_role", ""))
    calibration_roles = [str(value) for value in pilot.get("calibration_roles", [])]
    heldout_role = str(pilot.get("heldout_dynamic_role", ""))
    if len(calibration_roles) < 2 or len(set(calibration_roles)) != len(calibration_roles):
        raise ValueError("G6b requires at least two unique calibration trials")
    if static_role in calibration_roles or heldout_role in calibration_roles:
        raise ValueError("G6b static and held-out trials cannot enter calibration")
    required_roles = [static_role, heldout_role] + calibration_roles
    missing_roles = sorted(set(required_roles) - set(roles))
    if missing_roles:
        raise ValueError("G6b manifest lacks roles: {}".format(missing_roles))
    role_hashes = [str(roles[role]["observed_sha256"]) for role in required_roles]
    if len(set(role_hashes)) != len(role_hashes):
        raise ValueError("G6b trial split contains duplicate source content")

    data = {role: _read_verified_trc(roles[role]) for role in required_roles}
    estimation = protocol.get("estimation") or {}
    outlier_mad = float(estimation.get("outlier_mad", 4.5))
    iterations = int(estimation.get("bootstrap_iterations", 10000))
    base_seed = int(estimation.get("bootstrap_seed", 20260721))
    measurements = protocol.get("measurements")
    if not isinstance(measurements, list) or not measurements:
        raise ValueError("G6b protocol has no marker-pair measurements")

    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    seen_ids = set()
    for index, raw in enumerate(measurements):
        if not isinstance(raw, Mapping):
            raise ValueError("G6b measurement must be a mapping")
        measurement_id = str(raw.get("id", "")).strip()
        marker_a = str(raw.get("marker_a", "")).strip()
        marker_b = str(raw.get("marker_b", "")).strip()
        if not measurement_id or measurement_id in seen_ids or not marker_a or not marker_b:
            raise ValueError("G6b measurement ids/markers must be nonempty and unique")
        seen_ids.add(measurement_id)
        try:
            static = _robust_estimate(
                _pair_distances(data[static_role], marker_a, marker_b), outlier_mad
            )
            trial_rows = []
            for role in calibration_roles:
                estimate = _robust_estimate(
                    _pair_distances(data[role], marker_a, marker_b), outlier_mad
                )
                trial_rows.append({"role": role, **estimate})
            trial_estimates = [row["estimate_m"] for row in trial_rows]
            dynamic_estimate = float(np.median(trial_estimates))
            ci_low, ci_high = _bootstrap_trial_median(
                trial_estimates, iterations, base_seed + index
            )
            heldout = _robust_estimate(
                _pair_distances(data[heldout_role], marker_a, marker_b), outlier_mad
            )
            signed_error = (dynamic_estimate - static["estimate_m"]) / static[
                "estimate_m"
            ]
            heldout_error = (heldout["estimate_m"] - dynamic_estimate) / dynamic_estimate
            rows.append(
                {
                    "measurement_id": measurement_id,
                    "body": str(raw.get("body", "")),
                    "measurement_class": str(raw.get("measurement_class", "")),
                    "marker_a": marker_a,
                    "marker_b": marker_b,
                    "static_reference_m": static["estimate_m"],
                    "dynamic_estimate_m": dynamic_estimate,
                    "dynamic_ci95_low_m": ci_low,
                    "dynamic_ci95_high_m": ci_high,
                    "signed_relative_error": float(signed_error),
                    "absolute_relative_error": float(abs(signed_error)),
                    "heldout_dynamic_m": heldout["estimate_m"],
                    "heldout_relative_error": float(heldout_error),
                    "heldout_absolute_relative_error": float(abs(heldout_error)),
                    "static_sample_count": static["sample_count"],
                    "heldout_sample_count": heldout["sample_count"],
                    "calibration_trial_count": len(trial_rows),
                    "calibration_trials": trial_rows,
                }
            )
        except ValueError as exc:
            failures.append({"measurement_id": measurement_id, "reason": str(exc)})

    thresholds = protocol.get("thresholds") or {}
    required_coverage = float(thresholds.get("measurement_coverage", 0.9))
    median_limit = float(thresholds.get("median_absolute_relative_error", 0.02))
    p95_limit = float(thresholds.get("p95_absolute_relative_error", 0.05))
    heldout_p95_limit = float(
        thresholds.get("heldout_p95_absolute_relative_error", 0.05)
    )
    coverage = len(rows) / len(measurements)
    absolute_errors = [row["absolute_relative_error"] for row in rows]
    heldout_errors = [row["heldout_absolute_relative_error"] for row in rows]
    summary = {
        "measurement_count_frozen": len(measurements),
        "measurement_count_assessed": len(rows),
        "measurement_count_failed": len(failures),
        "measurement_coverage": float(coverage),
        "median_absolute_relative_error": (
            float(np.median(absolute_errors)) if absolute_errors else None
        ),
        "p95_absolute_relative_error": (
            _percentile(absolute_errors, 95) if absolute_errors else None
        ),
        "max_absolute_relative_error": max(absolute_errors) if absolute_errors else None,
        "heldout_p95_absolute_relative_error": (
            _percentile(heldout_errors, 95) if heldout_errors else None
        ),
    }
    checks = {
        "public_sources_verified": audit["pass"] is True,
        "static_hidden_from_estimation": static_role not in calibration_roles,
        "heldout_hidden_from_estimation": heldout_role not in calibration_roles,
        "calibration_content_unique": len(set(role_hashes)) == len(role_hashes),
        "multiple_dynamic_trials": len(calibration_roles) >= 2,
        "measurement_coverage": coverage >= required_coverage,
        "median_error_within_threshold": bool(
            absolute_errors and summary["median_absolute_relative_error"] <= median_limit
        ),
        "p95_error_within_threshold": bool(
            absolute_errors and summary["p95_absolute_relative_error"] <= p95_limit
        ),
        "heldout_p95_within_threshold": bool(
            heldout_errors
            and summary["heldout_p95_absolute_relative_error"] <= heldout_p95_limit
        ),
    }
    return {
        "schema_version": DYNAMIC_CALIBRATION_SCHEMA_VERSION,
        "scope": "g6b-static-hidden-dynamic-calibration-pilot",
        "scientific_claim_boundary": (
            "marker-measurement recovery only; OpenSim IK/ID/RRA and clinical "
            "validity remain unassessed"
        ),
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": audit["manifest_sha256"],
        "source_record_doi": audit.get("source_record_doi"),
        "subject_id": str(pilot.get("subject_id", "")),
        "static_reference_role": static_role,
        "calibration_roles": calibration_roles,
        "heldout_dynamic_role": heldout_role,
        "estimator": {
            "within_trial": "median-after-MAD-outlier-rejection",
            "across_trials": "equal-trial-weighted-median",
            "outlier_mad": outlier_mad,
            "bootstrap_iterations": iterations,
            "bootstrap_seed": base_seed,
        },
        "thresholds": {
            "measurement_coverage": required_coverage,
            "median_absolute_relative_error": median_limit,
            "p95_absolute_relative_error": p95_limit,
            "heldout_p95_absolute_relative_error": heldout_p95_limit,
        },
        "source_audit": {
            "manifest_sha256": audit["manifest_sha256"],
            "checks": audit["checks"],
            # Absolute cache paths are useful while executing but are neither
            # scientific provenance nor portable publication evidence.
            "assets": [
                {
                    key: value
                    for key, value in asset.items()
                    if key != "absolute_path"
                }
                for asset in audit["assets"]
            ],
        },
        "summary": summary,
        "checks": checks,
        "pass": all(checks.values()),
        "measurement_failures": failures,
        "measurements": rows,
    }


def write_dynamic_calibration_results(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    rows = result.get("measurements", [])
    csv_path = output.with_suffix(".csv")
    flat_rows = []
    for raw in rows:
        row = {key: value for key, value in raw.items() if key != "calibration_trials"}
        flat_rows.append(row)
    pd.DataFrame(flat_rows).to_csv(csv_path, index=False)
