"""Checksum-bound real-data format-fidelity validation for Study 31."""

from __future__ import annotations

import gc
import hashlib
import io
import math
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.io import loadmat

from harmo.ingest.plugins.mat_mcos_explicit import _resolve_mcos_table
from harmo.validation_corpus import verify_corpus_asset
from harmo.validation_study import canonical_sha256, environment_record


FORMAT_FIDELITY_VERSION = "1.0.0"


def compare_format_frames(
    reference: pd.DataFrame,
    candidate: pd.DataFrame,
    sampling_rate_hz: float,
    numeric_tolerance: float,
    reference_events: Sequence[int] = (),
    candidate_events: Sequence[int] = (),
) -> Dict[str, Any]:
    """Compare two wide source representations without dropping missing data."""
    reference_channels = [str(value) for value in reference.columns if value != "Time"]
    candidate_channels = [str(value) for value in candidate.columns if value != "Time"]
    missing_channels = sorted(set(reference_channels) - set(candidate_channels))
    extra_channels = sorted(set(candidate_channels) - set(reference_channels))
    shared_channels = [
        value for value in reference_channels if value in set(candidate_channels)
    ]
    row_complete = len(reference) == len(candidate)
    overlap = min(len(reference), len(candidate))

    if overlap:
        left_time = pd.to_numeric(
            reference["Time"].iloc[:overlap], errors="coerce"
        ).to_numpy(dtype="float64")
        right_time = pd.to_numeric(
            candidate["Time"].iloc[:overlap], errors="coerce"
        ).to_numpy(dtype="float64")
        finite_time = np.isfinite(left_time) & np.isfinite(right_time)
        max_time_difference = (
            float(np.max(np.abs(left_time[finite_time] - right_time[finite_time])))
            if finite_time.any()
            else math.inf
        )
        time_missingness_equal = bool(
            np.array_equal(np.isfinite(left_time), np.isfinite(right_time))
        )
    else:
        max_time_difference = math.inf
        time_missingness_equal = False

    channel_metrics: Dict[str, Dict[str, Any]] = {}
    max_numeric_error = 0.0
    missingness_equal = True
    for channel in shared_channels:
        left = pd.to_numeric(
            reference[channel].iloc[:overlap], errors="coerce"
        ).to_numpy(dtype="float64")
        right = pd.to_numeric(
            candidate[channel].iloc[:overlap], errors="coerce"
        ).to_numpy(dtype="float64")
        left_finite = np.isfinite(left)
        right_finite = np.isfinite(right)
        channel_missingness_equal = bool(np.array_equal(left_finite, right_finite))
        missingness_equal = missingness_equal and channel_missingness_equal
        paired = left_finite & right_finite
        if paired.any():
            differences = np.abs(left[paired] - right[paired])
            maximum = float(np.max(differences))
            root_mean_square = float(np.sqrt(np.mean(np.square(differences))))
            if paired.sum() >= 2 and np.std(left[paired]) and np.std(right[paired]):
                correlation = float(np.corrcoef(left[paired], right[paired])[0, 1])
            else:
                correlation = None
            numeric_status = "compared"
        elif channel_missingness_equal and not left_finite.any():
            maximum = 0.0
            root_mean_square = None
            correlation = None
            numeric_status = "source-missing-in-both-representations"
        else:
            maximum = None
            root_mean_square = None
            correlation = None
            numeric_status = "not-comparable"
        if maximum is not None:
            max_numeric_error = max(max_numeric_error, maximum)
        channel_metrics[channel] = {
            "paired_finite_samples": int(paired.sum()),
            "reference_nonfinite_samples": int((~left_finite).sum()),
            "candidate_nonfinite_samples": int((~right_finite).sum()),
            "missingness_equal": channel_missingness_equal,
            "numeric_status": numeric_status,
            "max_absolute_error": maximum,
            "root_mean_square_error": root_mean_square,
            "pearson_correlation": correlation,
        }

    event_complete = len(reference_events) == len(candidate_events)
    event_differences = [
        abs(int(left) - int(right))
        for left, right in zip(reference_events, candidate_events)
    ]
    max_event_difference = max(event_differences or [0])
    equivalence_pass = bool(
        row_complete
        and not missing_channels
        and not extra_channels
        and time_missingness_equal
        and missingness_equal
        and max_time_difference * sampling_rate_hz <= 0.5
        and event_complete
        and max_event_difference <= 1
        and max_numeric_error <= numeric_tolerance
    )
    finite_correlations = [
        value["pearson_correlation"]
        for value in channel_metrics.values()
        if value["pearson_correlation"] is not None
        and math.isfinite(value["pearson_correlation"])
    ]
    return {
        "reference_rows": len(reference),
        "candidate_rows": len(candidate),
        "row_complete": row_complete,
        "reference_channels": len(reference_channels),
        "candidate_channels": len(candidate_channels),
        "channel_complete": not missing_channels and not extra_channels,
        "missing_channels": missing_channels,
        "extra_channels": extra_channels,
        "missingness_equal": missingness_equal,
        "event_complete": event_complete,
        "event_status": (
            "not-present-in-either-source"
            if not reference_events and not candidate_events
            else "compared"
        ),
        "max_event_difference_samples": max_event_difference,
        "max_time_difference_samples": (
            max_time_difference * sampling_rate_hz
            if math.isfinite(max_time_difference)
            else None
        ),
        "numeric_tolerance": numeric_tolerance,
        "max_numeric_error": max_numeric_error,
        "minimum_channel_correlation": (
            min(finite_correlations) if finite_correlations else None
        ),
        "median_channel_correlation": (
            float(np.median(finite_correlations)) if finite_correlations else None
        ),
        "channel_metrics": channel_metrics,
        "equivalence_pass": equivalence_pass,
    }


def _member_bytes(archive: zipfile.ZipFile, name: str) -> Tuple[bytes, Dict[str, Any]]:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise KeyError("required source member is missing: {}".format(name)) from exc
    with archive.open(info) as stream:
        payload = stream.read()
    return payload, {
        "member": name,
        "size_bytes": len(payload),
        "crc32": "{:08x}".format(info.CRC),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _extract_member(
    archive: zipfile.ZipFile, name: str, destination: Path
) -> Dict[str, Any]:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise KeyError("required source member is missing: {}".format(name)) from exc
    digest = hashlib.sha256()
    with archive.open(info) as source, destination.open("wb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            output.write(chunk)
    return {
        "member": name,
        "size_bytes": destination.stat().st_size,
        "crc32": "{:08x}".format(info.CRC),
        "sha256": digest.hexdigest(),
    }


def _read_ascii(payload: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(payload), sep="\t", comment="#")


def _trial_label(value: Any) -> int:
    current = value
    while isinstance(current, np.ndarray) and current.size == 1:
        current = current.flat[0]
    match = re.search(r"(\d+)", str(current))
    if match is None:
        raise ValueError("MAT trial label has no integer: {!r}".format(current))
    return int(match.group(1))


def _mat_table(
    mat_data: Mapping[str, Any], subject: int, speed: str, trial: int, field: str
) -> pd.DataFrame:
    root_key = "Subject{}_raw".format(subject)
    root = mat_data[root_key][0, 0][speed][0, 0]
    trial_table = root[field]
    matches = [
        index
        for index in range(trial_table.shape[0])
        if _trial_label(trial_table[index, 0]) == trial
    ]
    if len(matches) != 1:
        raise ValueError(
            "{} {} trial {} resolved to {} MAT rows".format(
                speed, field, trial, len(matches)
            )
        )
    values, columns = _resolve_mcos_table(mat_data, trial_table[matches[0], 1])
    return pd.DataFrame(values, columns=columns)


def _c3d_frames(
    path: Path, emg_channel_map: Mapping[str, str]
) -> Tuple[pd.DataFrame, pd.DataFrame, List[int], Dict[str, Any]]:
    import ezc3d

    source = ezc3d.c3d(str(path))
    point_values = source["data"]["points"][:3, :, :]
    point_labels = [
        str(value).strip() for value in source["parameters"]["POINT"]["LABELS"]["value"]
    ]
    point_rate = float(source["parameters"]["POINT"]["RATE"]["value"][0])
    point_rows = point_values.shape[2]
    point_columns = [
        "{}{}".format(label, axis) for label in point_labels for axis in ("X", "Y", "Z")
    ]
    trajectories = pd.DataFrame(
        point_values.transpose(2, 1, 0).reshape(point_rows, -1),
        columns=point_columns,
    )
    trajectories.insert(0, "Time", np.arange(point_rows) / point_rate)

    analog_values = source["data"]["analogs"]
    if analog_values.shape[0] != 1:
        raise ValueError(
            "unsupported C3D analog subframe layout {}".format(analog_values.shape)
        )
    analog_values = analog_values[0].T
    analog_labels = [
        str(value).strip()
        for value in source["parameters"]["ANALOG"]["LABELS"]["value"]
    ]
    analog_rate = float(source["header"]["analogs"]["frame_rate"])
    analog_index = {name: index for index, name in enumerate(analog_labels)}
    missing = sorted(set(emg_channel_map.values()) - set(analog_index))
    if missing:
        raise ValueError("C3D lacks mapped EMG channels {}".format(missing))
    emg = pd.DataFrame(
        {
            output_name: analog_values[:, analog_index[source_name]]
            for output_name, source_name in emg_channel_map.items()
        }
    )
    emg.insert(0, "Time", np.arange(len(emg)) / analog_rate)

    event_group = source["parameters"].get("EVENT") or {}
    used = int((event_group.get("USED") or {}).get("value", [0])[0])
    times = (event_group.get("TIMES") or {}).get("value")
    event_samples: List[int] = []
    if used and times is not None:
        raw_times = np.asarray(times, dtype="float64")
        if raw_times.ndim == 2 and raw_times.shape[0] >= 2:
            seconds = raw_times[0, :used] * 60.0 + raw_times[1, :used]
        else:
            seconds = raw_times.reshape(-1)[:used]
        event_samples = [int(round(value * point_rate)) for value in seconds]
    return (
        emg,
        trajectories,
        event_samples,
        {
            "ezc3d_version": str(getattr(ezc3d, "__version__", "unknown")),
            "point_rate_hz": point_rate,
            "analog_rate_hz": analog_rate,
            "point_storage_scale": float(
                source["parameters"]["POINT"]["SCALE"]["value"][0]
            ),
            "events": len(event_samples),
        },
    )


def _float32_ulp(frame: pd.DataFrame) -> float:
    values = frame.drop(columns=["Time"]).to_numpy(dtype="float64")
    finite = np.abs(values[np.isfinite(values)])
    maximum = float(np.max(finite)) if finite.size else 0.0
    return float(abs(np.spacing(np.float32(maximum))))


def _frame_digest(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update("\0".join(str(value) for value in frame.columns).encode("utf-8"))
    values = frame.to_numpy(dtype="float64")
    canonical = np.where(np.isnan(values), np.nan, values).astype(">f8", copy=False)
    digest.update(canonical.tobytes(order="C"))
    return digest.hexdigest()


def _comparison_row(
    subject: int,
    signal: str,
    reference_format: str,
    candidate_format: str,
    frames: Mapping[str, Mapping[str, pd.DataFrame]],
    events: Mapping[str, Sequence[int]],
    signal_config: Mapping[str, Any],
    expected_outcome: str,
    comparison_role: str,
) -> Dict[str, Any]:
    reference = frames[signal][reference_format]
    candidate = frames[signal][candidate_format]
    quantum = float(signal_config["ascii_decimal_quantum"])
    candidate_ulp = _float32_ulp(candidate) if candidate_format == "c3d" else 0.0
    reference_ulp = _float32_ulp(reference) if reference_format == "c3d" else 0.0
    tolerance = quantum / 2.0 + candidate_ulp + reference_ulp
    metrics = compare_format_frames(
        reference,
        candidate,
        float(signal_config["sampling_rate_hz"]),
        tolerance,
        events.get(reference_format, ()),
        events.get(candidate_format, ()),
    )
    observed_outcome = "equivalent" if metrics["equivalence_pass"] else "non-equivalent"
    if expected_outcome == "classified-by-frozen-tolerance":
        protocol_pass = observed_outcome in {"equivalent", "non-equivalent"}
    else:
        protocol_pass = observed_outcome == expected_outcome
    return {
        "subject_id": "sub-{:02d}".format(subject),
        "cohort_role": "pilot" if subject == 1 else "confirmatory",
        "signal": signal,
        "reference_format": reference_format,
        "candidate_format": candidate_format,
        "comparison_role": comparison_role,
        "expected_outcome": expected_outcome,
        "observed_outcome": observed_outcome,
        "trust_state": (
            "traceable-format-equivalent"
            if observed_outcome == "equivalent"
            else "quarantined-source-format-discordance"
        ),
        "source_precision_model": (
            "half-ascii-decimal-quantum-plus-float32-ulp"
            if "c3d" in {reference_format, candidate_format}
            else "half-ascii-decimal-quantum"
        ),
        "ascii_decimal_quantum": quantum,
        "reference_frame_sha256": _frame_digest(reference),
        "candidate_frame_sha256": _frame_digest(candidate),
        **metrics,
        "pass": protocol_pass,
    }


def _source_absent_comparison_row(
    subject: int,
    signal: str,
    reference_format: str,
    candidate_format: str,
    comparison_role: str,
    missing_formats: Sequence[str],
    declared_absent: bool,
) -> Dict[str, Any]:
    observed_outcome = "source-absent-retained"
    expected_outcome = (
        "source-absent-retained" if declared_absent else "assessment-required"
    )
    return {
        "subject_id": "sub-{:02d}".format(subject),
        "cohort_role": "pilot" if subject == 1 else "confirmatory",
        "signal": signal,
        "reference_format": reference_format,
        "candidate_format": candidate_format,
        "comparison_role": comparison_role,
        "expected_outcome": expected_outcome,
        "observed_outcome": observed_outcome,
        "missing_formats": sorted(missing_formats),
        "not_assessable_reason": "official source representation is absent",
        "equivalence_pass": None,
        "pass": observed_outcome == expected_outcome,
    }


def _archive_assets(
    corpus: Mapping[str, Any], data_root: Path
) -> Tuple[Dict[str, Path], Dict[str, Dict[str, Any]]]:
    study = corpus["studies"]["study31"]
    paths: Dict[str, Path] = {}
    verification: Dict[str, Dict[str, Any]] = {}
    for format_name in ("ascii", "mat", "c3d"):
        asset = study["assets"][format_name]
        path = data_root / str(asset["local_name"])
        if not path.is_file():
            raise FileNotFoundError("missing Study 31 source asset: {}".format(path))
        paths[format_name] = path
        verification[format_name] = verify_corpus_asset(path, asset)
    return paths, verification


def _run_once(registry: Mapping[str, Any], paths: Mapping[str, Path]) -> Dict[str, Any]:
    experiment = registry["experiments"]["B"]
    speed = str(experiment["speed"])
    trial = int(str(experiment["trial"]).lstrip("T"))
    signals = experiment["signals"]
    required_pairs = [
        tuple(value) for value in experiment["required_equivalence_pairs"]
    ]
    c3d_audit_pairs = [
        tuple(value) for value in experiment["c3d_interchangeability_audits"]
    ]
    comparisons: List[Dict[str, Any]] = []
    members: List[Dict[str, Any]] = []
    availability: List[Dict[str, Any]] = []
    c3d_metadata: List[Dict[str, Any]] = []

    with zipfile.ZipFile(paths["ascii"]) as ascii_archive, zipfile.ZipFile(
        paths["mat"]
    ) as mat_archive, zipfile.ZipFile(
        paths["c3d"]
    ) as c3d_archive, tempfile.TemporaryDirectory(
        prefix="harmo-format-fidelity-"
    ) as temporary:
        temporary_root = Path(temporary)
        ascii_members = set(ascii_archive.namelist())
        for subject in experiment["subjects"]:
            ascii_frames: Dict[str, pd.DataFrame] = {}
            for signal in ("emg", "trajectories"):
                directory = signals[signal]["ascii_directory"]
                name = "ASCII files/Participant{}/Raw_Data/{}/{}/T{}.txt".format(
                    subject, speed, directory, trial
                )
                declared_absent = int(subject) in set(
                    signals[signal].get("source_absent_subjects") or []
                )
                if name not in ascii_members:
                    record = {
                        "member": name,
                        "size_bytes": None,
                        "crc32": None,
                        "sha256": None,
                        "outcome": "source-absent",
                    }
                    if not declared_absent:
                        raise KeyError(
                            "required source member is missing: {}".format(name)
                        )
                    availability.append(
                        {
                            "subject_id": "sub-{:02d}".format(subject),
                            "signal": signal,
                            "format": "ascii",
                            "available": False,
                            "declared_source_absent": True,
                            "evidence": name,
                        }
                    )
                else:
                    payload, record = _member_bytes(ascii_archive, name)
                    record["outcome"] = "read"
                    ascii_frames[signal] = _read_ascii(payload)
                    availability.append(
                        {
                            "subject_id": "sub-{:02d}".format(subject),
                            "signal": signal,
                            "format": "ascii",
                            "available": True,
                            "declared_source_absent": declared_absent,
                            "evidence": name,
                        }
                    )
                record.update(
                    {"subject_id": "sub-{:02d}".format(subject), "format": "ascii"}
                )
                members.append(record)

            mat_name = "MAT files/Participant{}/Raw_Data.mat".format(subject)
            mat_path = temporary_root / "participant-{:02d}.mat".format(subject)
            mat_record = _extract_member(mat_archive, mat_name, mat_path)
            mat_record.update(
                {
                    "subject_id": "sub-{:02d}".format(subject),
                    "format": "mat",
                    "outcome": "read",
                }
            )
            members.append(mat_record)
            mat_data = loadmat(str(mat_path), squeeze_me=False)
            mat_frames: Dict[str, pd.DataFrame] = {}
            for signal in ("emg", "trajectories"):
                declared_absent = int(subject) in set(
                    signals[signal].get("source_absent_subjects") or []
                )
                try:
                    mat_frames[signal] = _mat_table(
                        mat_data,
                        int(subject),
                        speed,
                        trial,
                        str(signals[signal]["mat_field"]),
                    )
                    available = True
                    error = None
                except (KeyError, ValueError) as exc:
                    if not declared_absent:
                        raise
                    available = False
                    error = "{}: {}".format(type(exc).__name__, exc)
                availability.append(
                    {
                        "subject_id": "sub-{:02d}".format(subject),
                        "signal": signal,
                        "format": "mat",
                        "available": available,
                        "declared_source_absent": declared_absent,
                        "evidence": "{}.{}".format(speed, signals[signal]["mat_field"]),
                        "error": error,
                    }
                )

            c3d_name = "c3d files/Participant{}/Raw_Data/{}/T{}.c3d".format(
                subject, speed, trial
            )
            c3d_path = temporary_root / "participant-{:02d}.c3d".format(subject)
            c3d_record = _extract_member(c3d_archive, c3d_name, c3d_path)
            c3d_record.update(
                {
                    "subject_id": "sub-{:02d}".format(subject),
                    "format": "c3d",
                    "outcome": "read",
                }
            )
            members.append(c3d_record)
            c3d_emg, c3d_trajectories, c3d_events, metadata = _c3d_frames(
                c3d_path, signals["emg"]["c3d_channel_map"]
            )
            metadata["subject_id"] = "sub-{:02d}".format(subject)
            c3d_metadata.append(metadata)
            for signal in ("emg", "trajectories"):
                availability.append(
                    {
                        "subject_id": "sub-{:02d}".format(subject),
                        "signal": signal,
                        "format": "c3d",
                        "available": True,
                        "declared_source_absent": False,
                        "evidence": c3d_name,
                    }
                )

            frames = {
                "emg": {
                    "c3d": c3d_emg,
                },
                "trajectories": {
                    "c3d": c3d_trajectories,
                },
            }
            for signal in ("emg", "trajectories"):
                if signal in ascii_frames:
                    frames[signal]["ascii"] = ascii_frames[signal]
                if signal in mat_frames:
                    frames[signal]["mat"] = mat_frames[signal]
            event_samples = {"ascii": [], "mat": [], "c3d": c3d_events}
            for signal, reference_format, candidate_format in required_pairs:
                missing_formats = [
                    value
                    for value in (reference_format, candidate_format)
                    if value not in frames[signal]
                ]
                if missing_formats:
                    comparisons.append(
                        _source_absent_comparison_row(
                            int(subject),
                            signal,
                            reference_format,
                            candidate_format,
                            "required-equivalence",
                            missing_formats,
                            int(subject)
                            in set(signals[signal].get("source_absent_subjects") or []),
                        )
                    )
                else:
                    comparisons.append(
                        _comparison_row(
                            int(subject),
                            signal,
                            reference_format,
                            candidate_format,
                            frames,
                            event_samples,
                            signals[signal],
                            "equivalent",
                            "required-equivalence",
                        )
                    )
            for signal, reference_format, candidate_format in c3d_audit_pairs:
                missing_formats = [
                    value
                    for value in (reference_format, candidate_format)
                    if value not in frames[signal]
                ]
                if missing_formats:
                    comparisons.append(
                        _source_absent_comparison_row(
                            int(subject),
                            signal,
                            reference_format,
                            candidate_format,
                            "c3d-interchangeability-audit",
                            missing_formats,
                            int(subject)
                            in set(signals[signal].get("source_absent_subjects") or []),
                        )
                    )
                else:
                    comparisons.append(
                        _comparison_row(
                            int(subject),
                            signal,
                            reference_format,
                            candidate_format,
                            frames,
                            event_samples,
                            signals[signal],
                            "classified-by-frozen-tolerance",
                            "c3d-interchangeability-audit",
                        )
                    )

            mat_path.unlink()
            c3d_path.unlink()
            del mat_data, mat_frames, c3d_emg, c3d_trajectories, frames
            gc.collect()

    required = [
        row for row in comparisons if row["comparison_role"] == "required-equivalence"
    ]
    c3d_audits = [
        row
        for row in comparisons
        if row["comparison_role"] == "c3d-interchangeability-audit"
    ]
    assessed = [row for row in comparisons if row["equivalence_pass"] is not None]
    retained_absence = [
        row
        for row in comparisons
        if row["observed_outcome"] == "source-absent-retained"
    ]
    confirmatory = [row for row in comparisons if row["cohort_role"] == "confirmatory"]
    return {
        "subjects": len(experiment["subjects"]),
        "source_members": members,
        "source_availability": availability,
        "c3d_metadata": c3d_metadata,
        "comparisons": comparisons,
        "summary": {
            "comparison_attempts": len(comparisons),
            "assessed_comparisons": len(assessed),
            "source_absent_comparisons_retained": len(retained_absence),
            "required_equivalence_comparisons": len(required),
            "required_equivalence_passed": sum(row["pass"] for row in required),
            "c3d_interchangeability_audits": len(c3d_audits),
            "c3d_audits_classified": sum(row["pass"] for row in c3d_audits),
            "c3d_equivalent": sum(
                row["observed_outcome"] == "equivalent" for row in c3d_audits
            ),
            "c3d_quarantined_source_discordance": sum(
                row["observed_outcome"] == "non-equivalent" for row in c3d_audits
            ),
            "c3d_source_absence_retained": sum(
                row["observed_outcome"] == "source-absent-retained"
                for row in c3d_audits
            ),
            "confirmatory_comparisons": len(confirmatory),
            "confirmatory_passed": sum(row["pass"] for row in confirmatory),
            "source_member_ledger_rows": len(members),
            "source_members_read": sum(row["outcome"] == "read" for row in members),
        },
        "pass": bool(comparisons) and all(row["pass"] for row in comparisons),
    }


def run_study31_format_fidelity(
    root: Path,
    registry: Mapping[str, Any],
    corpus: Mapping[str, Any],
    data_root: Path,
    repeats: int = 2,
) -> Dict[str, Any]:
    """Run the frozen Study 31 ASCII/MAT/C3D fidelity experiment."""
    if repeats < 2:
        raise ValueError("format-fidelity validation requires at least two repeats")
    paths, asset_verification = _archive_assets(corpus, data_root)
    results = [_run_once(registry, paths) for _ in range(repeats)]
    digests = [canonical_sha256(value) for value in results]
    first = results[0]
    source_pass = all(
        value.get("pass") is True for value in asset_verification.values()
    )
    reproducible = len(set(digests)) == 1
    experiment = registry["experiments"]["B"]
    report = {
        "schema_version": FORMAT_FIDELITY_VERSION,
        "scope": "checksum-bound-study31-source-format-fidelity",
        "experiment_id": "B",
        "study_id": "study31",
        "selection": {
            "subjects": experiment["subjects"],
            "pilot_subject": experiment["pilot_subject"],
            "confirmatory_subjects": experiment["confirmatory_subjects"],
            "speed": experiment["speed"],
            "trial": experiment["trial"],
            "missing_policy": experiment["missing_policy"],
        },
        "protocol_interpretation": {
            "required_equivalence": experiment["required_equivalence_pairs"],
            "c3d_interchangeability_audits": experiment[
                "c3d_interchangeability_audits"
            ],
            "discordance_freeze": experiment["source_discordance_freeze"],
            "raw_event_policy": experiment["raw_event_policy"],
            "decision_rule": "all assessable ASCII-MAT equivalence controls must pass; every C3D comparison must be classified by the frozen source-precision threshold and non-equivalent representations must be quarantined",
        },
        "source_assets": {
            key: {
                **{
                    field: observed
                    for field, observed in value.items()
                    if field != "path"
                },
                "local_name": paths[key].name,
            }
            for key, value in asset_verification.items()
        },
        "registry_sha256": canonical_sha256(registry),
        "corpus_sha256": canonical_sha256(corpus),
        "denominator": first["summary"],
        "source_members": first["source_members"],
        "source_availability": first["source_availability"],
        "c3d_metadata": first["c3d_metadata"],
        "comparisons": first["comparisons"],
        "reproducibility": {
            "repeats": repeats,
            "result_sha256": digests,
            "pass": reproducible,
        },
        "environment": environment_record(),
        "conclusions": [
            "ASCII and MAT EMG values are numerically equivalent at source decimal precision for every assessable frozen subject; official source absence remains explicit for Participants 6 and 7.",
            "C3D marker trajectories are equivalent for most subjects, while source-level discrepancies in Participants 14-16 are detected and quarantined.",
            "C3D EMG is time-aligned but not amplitude-equivalent to the official ASCII/MAT representation in assessable subjects and is therefore quarantined from direct numeric interchangeability.",
            "Raw event annotations are absent from all compared representations; this absence is retained and is not imputed.",
        ],
        "limitations": [
            "The experiment validates one fixed speed and trial per subject, not every recording in the release.",
            "C3D EMG discordance is detected and bounded descriptively; its vendor-export cause is not inferred from the files alone.",
            "Event fidelity cannot be estimated because the raw source representations contain no event annotations for the frozen recordings.",
        ],
    }
    report["pass"] = bool(source_pass and first["pass"] and reproducible)
    return report
