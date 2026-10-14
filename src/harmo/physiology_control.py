"""Traceable cross-study gait EMG physiological positive control."""

from __future__ import annotations

import hashlib
import json
import math
import random
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import numpy as np
import yaml
from scipy import signal
from scipy.io import loadmat

from harmo.ingest.plugins.mat_mcos_explicit import (
    _expand_template,
    _get_nested_mat_data,
    _is_mcos_table,
    _resolve_mcos_table,
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md5_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5()  # noqa: S324 - source-release identity, not security
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_physiology_protocol(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = {
        "schema_version",
        "protocol_id",
        "required_studies",
        "source_assets",
        "muscles",
        "aggregation",
        "decision_rules",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ValueError("physiology protocol missing keys: {}".format(missing))
    value["protocol_path"] = str(path)
    value["protocol_sha256"] = _sha256_file(path)
    return value


def _normalise_cycle(values: np.ndarray, points: int = 101) -> np.ndarray:
    values = np.asarray(values, dtype=float).reshape(-1)
    if values.size < 2 or not np.isfinite(values).all():
        raise ValueError("cycle must contain at least two finite values")
    values = np.maximum(values, 0.0)
    maximum = float(np.max(values))
    if maximum <= 0:
        raise ValueError("cycle has no positive EMG amplitude")
    values = values / maximum
    old_grid = np.linspace(0.0, 1.0, values.size)
    new_grid = np.linspace(0.0, 1.0, points)
    interpolated = np.interp(new_grid, old_grid, values)
    return interpolated / float(np.max(interpolated))


def _hampel(values: np.ndarray, window: int, sigma: float) -> np.ndarray:
    """Apply a deterministic centered Hampel replacement."""
    values = np.asarray(values, dtype=float).copy()
    half = max(1, int(window) // 2)
    source = values.copy()
    for index in range(source.size):
        left = max(0, index - half)
        right = min(source.size, index + half + 1)
        local = source[left:right]
        median = float(np.median(local))
        mad = float(np.median(np.abs(local - median)))
        scale = 1.4826 * mad
        if scale > 0 and abs(source[index] - median) > float(sigma) * scale:
            values[index] = median
    return values


def preprocess_study38_cycle(
    values: Sequence[float], sampling_rate_hz: float, settings: Mapping[str, Any]
) -> np.ndarray:
    """Create the frozen Study 38 envelope on a 101-point gait grid."""
    raw = np.asarray(values, dtype=float).reshape(-1)
    if raw.size < 16 or not np.isfinite(raw).all():
        raise ValueError("raw cycle is too short or contains non-finite values")
    fs = float(sampling_rate_hz)
    low, high = [float(value) for value in settings["bandpass_hz"]]
    if not (0 < low < high < fs / 2):
        raise ValueError("bandpass must lie strictly below Nyquist")
    centered = raw - float(np.mean(raw)) if settings["mean_center"] else raw
    sos = signal.butter(
        int(settings["butterworth_order"]),
        [low, high],
        btype="bandpass",
        fs=fs,
        output="sos",
    )
    filtered = signal.sosfiltfilt(sos, centered)
    rectified = np.abs(filtered)
    hampel_window = max(3, int(round(float(settings["hampel_window_seconds"]) * fs)))
    if hampel_window % 2 == 0:
        hampel_window += 1
    cleaned = _hampel(rectified, hampel_window, float(settings["hampel_sigma"]))
    rms_window = max(1, int(round(float(settings["rms_window_seconds"]) * fs)))
    kernel = np.ones(rms_window, dtype=float) / float(rms_window)
    envelope = np.sqrt(np.convolve(cleaned * cleaned, kernel, mode="same"))
    return _normalise_cycle(
        envelope, points=int(settings["normalized_grid_points"])
    )


def _mat_string(value: Any) -> str:
    if isinstance(value, np.ndarray) and value.size == 1:
        value = value.item()
    return str(value).strip()


def _asset_attestation(path: Path, expected: Mapping[str, Any]) -> Dict[str, Any]:
    observed_bytes = path.stat().st_size
    observed_md5 = _md5_file(path)
    result = {
        "path": str(path),
        "bytes": observed_bytes,
        "expected_bytes": int(expected["bytes"]),
        "md5": observed_md5,
        "expected_md5": str(expected["md5"]),
        "sha256": _sha256_file(path),
    }
    result["pass"] = (
        result["bytes"] == result["expected_bytes"]
        and result["md5"] == result["expected_md5"]
    )
    if not result["pass"]:
        raise ValueError("source asset identity mismatch: {}".format(path))
    return result


def extract_study38_rows(
    source_dir: Path, protocol: Mapping[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    attestations: List[Dict[str, Any]] = []
    study = protocol["required_studies"]["study_38"]
    muscles = protocol["muscles"]
    for subject_id in study["subjects"]:
        name = "Subject{}.mat".format(subject_id)
        path = source_dir / name
        attestation = _asset_attestation(
            path, protocol["source_assets"]["study_38"][name]
        )
        attestations.append({"study_id": "study_38", **attestation})
        source = loadmat(path, squeeze_me=True, struct_as_record=False)["s"]
        fs = float(source.EMGFreq)
        labels = [_mat_string(value) for value in np.atleast_1d(source.EmgVarName)]
        side = _mat_string(source.EMGSide)
        walking = [
            item
            for item in np.atleast_1d(source.Data).ravel()
            if _mat_string(item.Task) == study["activity"]
            and _mat_string(item.Foot) == side
        ]
        selected = walking[: int(study["expected_cycles_per_subject"])]
        if len(selected) != int(study["expected_cycles_per_subject"]):
            raise ValueError("{} lacks five same-side Walking strides".format(name))
        for canonical_muscle, muscle_spec in muscles.items():
            label = str(muscle_spec["study_38_label"])
            if label not in labels:
                raise ValueError("{} missing channel {}".format(name, label))
            channel_index = labels.index(label)
            cycles = []
            for item in selected:
                emg = np.asarray(item.EMG, dtype=float)
                if emg.ndim != 2 or channel_index >= emg.shape[0]:
                    raise ValueError("{} has unexpected EMG shape".format(name))
                cycles.append(
                    preprocess_study38_cycle(
                        emg[channel_index, :],
                        fs,
                        protocol["study_38_envelope"],
                    )
                )
            subject_curve = np.median(np.vstack(cycles), axis=0)
            rows.append(
                {
                    "study_id": "study_38",
                    "subject_id": str(subject_id),
                    "muscle": canonical_muscle,
                    "side": side,
                    "normalization_method": "cycle-max-for-timing-only",
                    "representation": study["representation"],
                    "cycle_count": len(cycles),
                    "values": subject_curve.tolist(),
                    "source_asset": name,
                    "source_asset_md5": attestation["md5"],
                    "source_asset_sha256": attestation["sha256"],
                }
            )
    return rows, attestations


_GAIT120_LABELS = {
    "vastuslateralis": "vastus lateralis",
    "rectusfemoris": "rectus femoris",
    "vastusmedialis": "vastus medialis",
    "tibialisanterior": "tibialis anterior",
    "bicepsfemoris": "biceps femoris",
    "semitendinosus": "semitendinosus",
    # GastrocnemuisMedialis is the spelling retained in the source table.
    "gastrocnemuismedialis": "medial gastrocnemius",
    "gastrocnemiusmedialis": "medial gastrocnemius",
    "gastrocnemiuslateralis": "lateral gastrocnemius",
    "soleusmedialis": "medial soleus",
    "soleuslateralis": "lateral soleus",
    "peroneuslongus": "peroneus longus",
    "peroneusbrevis": "peroneus brevis",
}


def canonical_gait120_channel(label: str) -> str:
    normalized = "".join(
        character for character in str(label).lower() if character.isalnum()
    )
    if normalized not in _GAIT120_LABELS:
        raise ValueError("unknown Gait120 source channel label: {}".format(label))
    return _GAIT120_LABELS[normalized]


def _numeric_mat_value(
    mat_data: Mapping[str, Any], key: str
) -> Tuple[np.ndarray, List[str] | None]:
    value = _get_nested_mat_data(dict(mat_data), key)
    columns = None
    if _is_mcos_table(value):
        value, columns = _resolve_mcos_table(dict(mat_data), value)
    value = np.asarray(value, dtype=float).squeeze()
    if value.ndim != 2:
        raise ValueError("{} did not resolve to a two-dimensional array".format(key))
    return value, columns


def _gait120_cycles(
    path: Path,
) -> Tuple[Dict[str, List[np.ndarray]], Dict[str, str]]:
    mat_data = loadmat(path)
    templates = [
        "LevelWalking.{trial}.{step}.EMGs_interpolated",
        "LevelWalking.{trial}.{step}.EMGs_norm",
        "LevelWalking.{trial}.{step}.EMGs_env",
    ]
    matches: List[Tuple[str, Dict[str, str]]] = []
    selected_template = None
    patterns = {"trial": r"Trial\d+", "step": r"Step\d+"}
    for template in templates:
        matches = _expand_template(mat_data, template, patterns)
        if matches:
            selected_template = template
            break
    if not matches or selected_template is None:
        raise ValueError("{} contains no LevelWalking step envelopes".format(path))
    cycles: Dict[str, List[np.ndarray]] = defaultdict(list)
    source_labels: Dict[str, str] = {}
    for key, _ in matches:
        array, columns = _numeric_mat_value(mat_data, key)
        if columns is None or len(columns) != 12 or len(set(columns)) != 12:
            raise ValueError(
                "{} lacks 12 unique source-declared channel labels".format(key)
            )
        if 12 not in array.shape:
            raise ValueError("{} has no 12-channel dimension".format(key))
        if array.shape[1] == 12:
            sample_by_channel = array
        elif array.shape[0] == 12:
            sample_by_channel = array.T
        else:
            raise ValueError("{} has ambiguous channel orientation".format(key))
        for index, source_label in enumerate(columns):
            muscle = canonical_gait120_channel(source_label)
            if muscle in source_labels and source_labels[muscle] != source_label:
                raise ValueError("inconsistent source label for {}".format(muscle))
            source_labels[muscle] = source_label
            cycles[muscle].append(_normalise_cycle(sample_by_channel[:, index]))
    return dict(cycles), source_labels


def _find_archive_member(names: Iterable[str], subject_id: str) -> str:
    suffix = "{}/EMG/ProcessedData.mat".format(subject_id)
    matches = sorted(name for name in names if name.endswith(suffix))
    if len(matches) != 1:
        raise ValueError(
            "archive must contain exactly one {}; found {}".format(suffix, len(matches))
        )
    return matches[0]


def extract_study120_rows(
    archive_path: Path, protocol: Mapping[str, Any]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    expected = protocol["source_assets"]["study_120"]["Gait120_011_to_020.zip"]
    attestation = _asset_attestation(archive_path, expected)
    source_attestations = [{"study_id": "study_120", **attestation}]
    study = protocol["required_studies"]["study_120"]
    rows: List[Dict[str, Any]] = []
    with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory(
        prefix="harmo-g3-"
    ) as temporary:
        names = archive.namelist()
        for subject_id in study["subjects"]:
            member = _find_archive_member(names, str(subject_id))
            destination = Path(temporary) / "{}-ProcessedData.mat".format(subject_id)
            with archive.open(member) as source, destination.open("wb") as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(chunk)
            cycles_by_muscle, source_labels = _gait120_cycles(destination)
            for muscle in protocol["muscles"]:
                cycles = cycles_by_muscle.get(muscle, [])
                if len(cycles) < int(study["minimum_cycles_per_subject"]):
                    raise ValueError(
                        "{} {} has only {} cycles".format(
                            subject_id, muscle, len(cycles)
                        )
                    )
                subject_curve = np.median(np.vstack(cycles), axis=0)
                rows.append(
                    {
                        "study_id": "study_120",
                        "subject_id": str(subject_id),
                        "muscle": muscle,
                        "side": "right",
                        "normalization_method": "source-processed-then-cycle-max",
                        "representation": study["representation"],
                        "cycle_count": len(cycles),
                        "values": subject_curve.tolist(),
                        "source_asset": "Gait120_011_to_020.zip",
                        "source_member": member,
                        "source_channel_label": source_labels[muscle],
                        "source_asset_md5": attestation["md5"],
                        "source_asset_sha256": attestation["sha256"],
                    }
                )
    return rows, source_attestations


def _in_windows(value: float, windows: Sequence[Sequence[float]]) -> bool:
    return any(float(lower) <= value <= float(upper) for lower, upper in windows)


def _window_mean(values: np.ndarray, window: Sequence[float]) -> float:
    phases = np.linspace(0.0, 100.0, values.size)
    lower, upper = [float(value) for value in window]
    selected = values[(phases >= lower) & (phases <= upper)]
    if selected.size == 0:
        raise ValueError("phase window selected no samples")
    return float(np.mean(selected))


def _subject_metric(
    row: Mapping[str, Any], muscle_spec: Mapping[str, Any]
) -> Dict[str, Any]:
    values = np.asarray(row.get("values", []), dtype=float)
    result = {
        key: row.get(key)
        for key in (
            "study_id",
            "subject_id",
            "muscle",
            "side",
            "normalization_method",
            "representation",
            "cycle_count",
            "source_asset",
            "source_member",
            "source_channel_label",
            "source_asset_md5",
            "source_asset_sha256",
        )
        if row.get(key) is not None
    }
    if (
        values.ndim != 1
        or values.size != 101
        or not np.isfinite(values).all()
        or float(np.max(values)) <= 0
    ):
        return {**result, "outcome": "unassessed", "reason": "invalid_subject_curve"}
    values = np.maximum(values, 0.0)
    phases = np.linspace(0.0, 100.0, values.size)
    peak = float(phases[int(np.argmax(values))])
    centroid = float(np.sum(phases * values) / np.sum(values))
    active_mean = max(
        _window_mean(values, window)
        for window in muscle_spec["active_windows_percent"]
    )
    reference_mean = _window_mean(
        values, muscle_spec["reference_window_percent"]
    )
    epsilon = np.finfo(float).eps
    contrast = math.log2((active_mean + epsilon) / (reference_mean + epsilon))
    peak_pass = _in_windows(peak, muscle_spec["expected_peak_windows_percent"])
    contrast_pass = contrast > 0.0
    return {
        **result,
        "peak_timing_percent_gait_cycle": peak,
        "activation_centroid_percent_gait_cycle": centroid,
        "active_phase_mean_within_subject_scale": active_mean,
        "reference_phase_mean_within_subject_scale": reference_mean,
        "log2_phase_contrast": contrast,
        "peak_window_pass": peak_pass,
        "phase_contrast_pass": contrast_pass,
        "pattern_pass": peak_pass and contrast_pass,
        "outcome": "assessed",
    }


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("percentile requires values")
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _bootstrap_fraction(
    values: Sequence[bool], iterations: int, seed: int
) -> Dict[str, Any]:
    if not values:
        raise ValueError("bootstrap requires values")
    numeric = [1.0 if value else 0.0 for value in values]
    rng = random.Random(seed)
    draws = []
    for _ in range(iterations):
        draws.append(
            sum(numeric[rng.randrange(len(numeric))] for _ in numeric) / len(numeric)
        )
    return {
        "estimate": sum(numeric) / len(numeric),
        "ci_lower": _percentile(draws, 0.025),
        "ci_upper": _percentile(draws, 0.975),
        "iterations": iterations,
        "seed": seed,
    }


def evaluate_physiology_control(
    rows: Sequence[Mapping[str, Any]],
    protocol: Mapping[str, Any],
    source_attestations: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Evaluate the frozen positive control and retain complete denominators."""
    metrics = [
        _subject_metric(row, protocol["muscles"][str(row.get("muscle"))])
        if str(row.get("muscle")) in protocol["muscles"]
        else {**dict(row), "outcome": "unassessed", "reason": "unexpected_muscle"}
        for row in rows
    ]
    rules = protocol["decision_rules"]
    expected_studies = protocol["required_studies"]
    expected_muscles = list(protocol["muscles"])
    study_eligibility = []
    study_muscle_results = []
    complete = True
    for study_id, study_spec in expected_studies.items():
        expected_subjects = {str(value) for value in study_spec["subjects"]}
        present_subjects = {
            str(row.get("subject_id"))
            for row in metrics
            if row.get("study_id") == study_id
        }
        exact_subjects = present_subjects == expected_subjects
        eligible_subjects = set()
        for subject_id in expected_subjects:
            subject_rows = [
                row
                for row in metrics
                if row.get("study_id") == study_id
                and str(row.get("subject_id")) == subject_id
                and row.get("outcome") == "assessed"
            ]
            if {str(row.get("muscle")) for row in subject_rows} == set(
                expected_muscles
            ):
                eligible_subjects.add(subject_id)
        eligible = (
            exact_subjects
            and eligible_subjects == expected_subjects
            and len(eligible_subjects) >= int(rules["minimum_subjects_per_study"])
        )
        study_eligibility.append(
            {
                "study_id": study_id,
                "expected_subject_count": len(expected_subjects),
                "present_subject_count": len(present_subjects),
                "eligible_subject_count": len(eligible_subjects),
                "exact_frozen_subjects": exact_subjects,
                "eligible": eligible,
                "amplitude_pooling_permitted": False,
            }
        )
        complete = complete and eligible
        for muscle in expected_muscles:
            assessed = [
                row
                for row in metrics
                if row.get("study_id") == study_id
                and row.get("muscle") == muscle
                and row.get("outcome") == "assessed"
            ]
            passes = [bool(row["pattern_pass"]) for row in assessed]
            contrasts = [float(row["log2_phase_contrast"]) for row in assessed]
            fraction = sum(passes) / len(expected_subjects) if passes else 0.0
            median_contrast = (
                float(np.median(contrasts)) if contrasts else float("nan")
            )
            endpoint_pass = (
                len(assessed) == len(expected_subjects)
                and fraction
                >= float(rules["minimum_subject_pattern_fraction_per_muscle"])
                and median_contrast > 0.0
            )
            uncertainty = (
                _bootstrap_fraction(
                    passes,
                    int(protocol["aggregation"]["uncertainty_iterations"]),
                    int(protocol["aggregation"]["uncertainty_seed"]),
                )
                if passes
                else None
            )
            study_muscle_results.append(
                {
                    "study_id": study_id,
                    "muscle": muscle,
                    "assessed_subjects": len(assessed),
                    "expected_subjects": len(expected_subjects),
                    "subject_pattern_fraction": fraction,
                    "median_log2_phase_contrast": median_contrast,
                    "pattern_fraction_uncertainty": uncertainty,
                    "pass": endpoint_pass,
                }
            )
    cross_study = []
    for muscle in expected_muscles:
        medians = {}
        for study_id in expected_studies:
            centroids = [
                float(row["activation_centroid_percent_gait_cycle"])
                for row in metrics
                if row.get("study_id") == study_id
                and row.get("muscle") == muscle
                and row.get("outcome") == "assessed"
            ]
            if centroids:
                medians[study_id] = float(np.median(centroids))
        difference = (
            abs(max(medians.values()) - min(medians.values()))
            if len(medians) == len(expected_studies)
            else None
        )
        endpoint_pass = (
            difference is not None
            and difference
            <= float(rules["maximum_cross_study_centroid_difference_percent"])
        )
        cross_study.append(
            {
                "muscle": muscle,
                "study_median_activation_centroids": medians,
                "absolute_cross_study_difference_percent": difference,
                "threshold_percent": float(
                    rules["maximum_cross_study_centroid_difference_percent"]
                ),
                "pass": endpoint_pass,
            }
        )
    attestations = [dict(value) for value in (source_attestations or [])]
    sources_pass = bool(attestations) and all(
        value.get("pass") is True for value in attestations
    )
    reporting_complete = (
        complete
        and len(metrics)
        == sum(
            len(value["subjects"]) * len(expected_muscles)
            for value in expected_studies.values()
        )
        and all(row.get("outcome") in {"assessed", "unassessed"} for row in metrics)
        and sources_pass
    )
    scientific_pass = (
        reporting_complete
        and all(value["pass"] for value in study_muscle_results)
        and all(value["pass"] for value in cross_study)
    )
    protocol_path = Path(str(protocol["protocol_path"]))
    result = {
        "schema_version": "1.0",
        "protocol_id": protocol["protocol_id"],
        "protocol_path": str(protocol_path),
        "protocol_sha256": protocol["protocol_sha256"],
        "input_rows_sha256": hashlib.sha256(_canonical_bytes(list(rows))).hexdigest(),
        "source_attestations": attestations,
        "study_eligibility": study_eligibility,
        "study_muscle_results": study_muscle_results,
        "cross_study_centroid_concordance": cross_study,
        "metrics": metrics,
        "primary_muscles": expected_muscles,
        "amplitude_pooled_across_studies": False,
        "timing_pooled_across_studies": False,
        "reporting_complete": reporting_complete,
        "scientific_endpoint_pass": scientific_pass,
        "positive_control_pass": scientific_pass,
        "claim_boundary": protocol["interpretation"]["pass"]
        if scientific_pass
        else protocol["interpretation"]["fail"],
    }
    result["result_sha256"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


def run_public_physiology_control(
    study38_dir: Path,
    gait120_archive: Path,
    protocol_path: Path,
) -> Dict[str, Any]:
    protocol = load_physiology_protocol(protocol_path)
    study38_rows, study38_sources = extract_study38_rows(study38_dir, protocol)
    study120_rows, study120_sources = extract_study120_rows(gait120_archive, protocol)
    return evaluate_physiology_control(
        study38_rows + study120_rows,
        protocol,
        study38_sources + study120_sources,
    )
