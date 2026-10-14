"""G6c-S6 structural-adequacy and missing-angular-momentum audit."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import numpy as np
import yaml

from harmo.dynamic_calibration import sha256_file, validate_public_source_bundle
from harmo.whole_body_balance import zero_phase_lowpass


STRUCTURAL_ADEQUACY_SCHEMA_VERSION = "1.0.0"
AXES = ("x", "y", "z")


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c-S6 YAML root must be a mapping")
    return value


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("G6c-S6 JSON root must be a mapping")
    return value


def _time_weighted_mean(time_s: np.ndarray, values: np.ndarray) -> np.ndarray:
    time_s = np.asarray(time_s, dtype=float)
    values = np.asarray(values, dtype=float)
    duration = float(time_s[-1] - time_s[0])
    if duration <= 0:
        raise ValueError("G6c-S6 duration must be positive")
    return np.trapz(values, time_s, axis=0) / duration


def _cumulative_trapezoid(time_s: np.ndarray, values: np.ndarray) -> np.ndarray:
    output = np.zeros_like(values, dtype=float)
    output[1:] = np.cumsum(
        0.5 * (values[1:] + values[:-1]) * np.diff(time_s)[:, None],
        axis=0,
    )
    return output


def detect_sustained_contacts(
    time_s: Sequence[float],
    vertical_force_n: Sequence[float],
    *,
    threshold_n: float,
    minimum_duration_s: float,
    cutoff_hz: float,
    filter_order: int,
    window_s: Sequence[float],
) -> Dict[str, Any]:
    """Detect sustained positive vertical-force contacts without residual input."""
    time = np.asarray(time_s, dtype=float)
    force = np.asarray(vertical_force_n, dtype=float)
    if (
        time.ndim != 1
        or force.shape != time.shape
        or time.size < 16
        or np.any(np.diff(time) <= 0)
        or not np.all(np.isfinite(time))
        or not np.all(np.isfinite(force))
    ):
        raise ValueError("G6c-S6 contact input must be finite, ordered vectors")
    if len(window_s) != 2:
        raise ValueError("G6c-S6 contact window must have two values")
    start_window, end_window = (float(value) for value in window_s)
    if end_window <= start_window:
        raise ValueError("G6c-S6 contact window is invalid")

    filtered = zero_phase_lowpass(
        time,
        force[:, None],
        cutoff_hz=float(cutoff_hz),
        order=int(filter_order),
    )[:, 0]
    contact = filtered >= float(threshold_n)
    starts = np.flatnonzero(contact & ~np.r_[False, contact[:-1]])
    ends = np.flatnonzero(~contact & np.r_[False, contact[:-1]])
    if contact[0]:
        starts = np.r_[0, starts]
    if contact[-1]:
        ends = np.r_[ends, len(contact) - 1]

    retained = []
    rejected = []
    for start_index in starts:
        following = ends[ends > start_index]
        if following.size == 0:
            continue
        end_index = int(following[0])
        duration = float(time[end_index] - time[start_index])
        record = {
            "start_s": float(time[start_index]),
            "end_s": float(time[end_index]),
            "duration_s": duration,
            "peak_force_n": float(np.max(filtered[start_index : end_index + 1])),
        }
        if not (start_window <= record["start_s"] <= end_window):
            continue
        if duration < float(minimum_duration_s):
            record["reason"] = "shorter-than-minimum-duration"
            rejected.append(record)
        else:
            retained.append(record)
    return {
        "selection_input": "vertical-ground-reaction-force-only",
        "residual_blind": True,
        "threshold_n": float(threshold_n),
        "minimum_duration_s": float(minimum_duration_s),
        "lowpass_cutoff_hz": float(cutoff_hz),
        "lowpass_order": int(filter_order),
        "window_s": [start_window, end_window],
        "contacts": retained,
        "rejected_contacts": rejected,
    }


def build_stride_intervals(
    contacts: Sequence[Mapping[str, Any]],
    *,
    minimum_stride_s: float,
    maximum_stride_s: float,
) -> list[Dict[str, Any]]:
    """Create same-side consecutive-contact stride intervals."""
    starts = [float(contact["start_s"]) for contact in contacts]
    strides = []
    for index, (start, end) in enumerate(zip(starts[:-1], starts[1:]), start=1):
        duration = end - start
        if float(minimum_stride_s) <= duration <= float(maximum_stride_s):
            strides.append(
                {
                    "stride_id": "stride-{:02d}".format(index),
                    "start_s": start,
                    "end_s": end,
                    "duration_s": duration,
                }
            )
    return strides


def required_periodic_angular_momentum(
    time_s: Sequence[float], residual_moment_nm: np.ndarray
) -> Dict[str, Any]:
    """Integrate residual torque after separating its stride-mean component."""
    time = np.asarray(time_s, dtype=float)
    moment = np.asarray(residual_moment_nm, dtype=float)
    if (
        time.ndim != 1
        or moment.shape != (time.size, 3)
        or time.size < 8
        or np.any(np.diff(time) <= 0)
        or not np.all(np.isfinite(moment))
    ):
        raise ValueError("G6c-S6 residual moment must be finite N x 3 data")
    mean = _time_weighted_mean(time, moment)
    periodic_torque = moment - mean
    angular_momentum = _cumulative_trapezoid(time, periodic_torque)
    angular_momentum -= _time_weighted_mean(time, angular_momentum)
    closure = np.trapz(periodic_torque, time, axis=0)
    raw_impulse = np.trapz(moment, time, axis=0)
    return {
        "mean_moment_nm": mean,
        "raw_angular_impulse_nms": raw_impulse,
        "periodic_moment_nm": periodic_torque,
        "required_periodic_angular_momentum_nms": angular_momentum,
        "periodic_closure_error_nms": closure,
    }


def _phase_normalize(
    time_s: np.ndarray, values: np.ndarray, points: int
) -> tuple[np.ndarray, np.ndarray]:
    phase = np.linspace(0.0, 100.0, int(points))
    source = 100.0 * (time_s - time_s[0]) / (time_s[-1] - time_s[0])
    output = np.column_stack(
        [np.interp(phase, source, values[:, axis]) for axis in range(values.shape[1])]
    )
    return phase, output


def _waveform_correlation(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left, dtype=float)
    right = np.asarray(right, dtype=float)
    left_scale = float(np.std(left))
    right_scale = float(np.std(right))
    if left_scale <= np.finfo(float).eps or right_scale <= np.finfo(float).eps:
        return 1.0 if np.allclose(left, right, atol=1e-12, rtol=0.0) else 0.0
    return float(np.corrcoef(left, right)[0, 1])


def summarize_study31_strides(
    actuation_time_s: Sequence[float],
    residual_moment_nm: np.ndarray,
    strides: Sequence[Mapping[str, Any]],
    *,
    phase_points: int,
    sample_interval_s: float,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    """Summarize stride-wise corrective torque and required angular momentum."""
    source_time = np.asarray(actuation_time_s, dtype=float)
    source_moment = np.asarray(residual_moment_nm, dtype=float)
    if (
        source_moment.shape != (source_time.size, 3)
        or np.any(np.diff(source_time) <= 0)
    ):
        raise ValueError("G6c-S6 actuation input is invalid")
    records = []
    tidy_rows = []
    phase_moment = []
    phase_momentum = []
    for stride in strides:
        start = float(stride["start_s"])
        end = float(stride["end_s"])
        first = int(round(start / float(sample_interval_s)))
        last = int(round(end / float(sample_interval_s)))
        grid = (
            np.arange(first, last + 1, dtype=float) * float(sample_interval_s)
        )
        if grid[0] < source_time[0] or grid[-1] > source_time[-1]:
            raise ValueError("G6c-S6 stride exceeds the RRA actuation range")
        moment = np.column_stack(
            [
                np.interp(grid, source_time, source_moment[:, axis])
                for axis in range(3)
            ]
        )
        requirement = required_periodic_angular_momentum(grid, moment)
        periodic = requirement["periodic_moment_nm"]
        angular_momentum = requirement[
            "required_periodic_angular_momentum_nms"
        ]
        phase, phase_torque = _phase_normalize(grid, periodic, phase_points)
        _, phase_h = _phase_normalize(grid, angular_momentum, phase_points)
        phase_moment.append(phase_torque)
        phase_momentum.append(phase_h)
        record = {
            "stride_id": str(stride["stride_id"]),
            "start_s": start,
            "end_s": end,
            "duration_s": float(end - start),
            "sample_count": int(grid.size),
            "mean_moment_nm": requirement["mean_moment_nm"].tolist(),
            "raw_angular_impulse_nms": requirement[
                "raw_angular_impulse_nms"
            ].tolist(),
            "periodic_moment_axis_rms_nm": np.sqrt(
                np.mean(periodic * periodic, axis=0)
            ).tolist(),
            "periodic_moment_vector_rms_nm": float(
                np.sqrt(np.mean(np.sum(periodic * periodic, axis=1)))
            ),
            "required_angular_momentum_axis_rms_nms": np.sqrt(
                np.mean(angular_momentum * angular_momentum, axis=0)
            ).tolist(),
            "required_angular_momentum_peak_to_peak_nms": np.ptp(
                angular_momentum, axis=0
            ).tolist(),
            "periodic_closure_error_nms": requirement[
                "periodic_closure_error_nms"
            ].tolist(),
        }
        records.append(record)
        for row_index, phase_value in enumerate(phase):
            row = {
                "stride_id": record["stride_id"],
                "phase_percent": float(phase_value),
            }
            for axis, label in enumerate(AXES):
                row["periodic_moment_nm_{}".format(label)] = float(
                    phase_torque[row_index, axis]
                )
                row["required_angular_momentum_nms_{}".format(label)] = float(
                    phase_h[row_index, axis]
                )
            tidy_rows.append(row)

    correlations = {"periodic_moment": {}, "required_angular_momentum": {}}
    for axis, label in enumerate(AXES):
        moment_values = []
        momentum_values = []
        for left in range(len(records)):
            for right in range(left + 1, len(records)):
                moment_values.append(
                    _waveform_correlation(
                        phase_moment[left][:, axis],
                        phase_moment[right][:, axis],
                    )
                )
                momentum_values.append(
                    _waveform_correlation(
                        phase_momentum[left][:, axis],
                        phase_momentum[right][:, axis],
                    )
                )
        correlations["periodic_moment"][label] = moment_values
        correlations["required_angular_momentum"][label] = momentum_values

    vertical_peak_to_peak = np.asarray(
        [record["required_angular_momentum_peak_to_peak_nms"][1] for record in records],
        dtype=float,
    )
    vertical_periodic_rms = np.asarray(
        [record["periodic_moment_axis_rms_nm"][1] for record in records],
        dtype=float,
    )
    return {
        "stride_count": len(records),
        "strides": records,
        "pairwise_phase_correlations": correlations,
        "vertical_axis": "y",
        "vertical_required_angular_momentum_peak_to_peak_mean_nms": float(
            np.mean(vertical_peak_to_peak)
        ),
        "vertical_required_angular_momentum_peak_to_peak_cv": float(
            np.std(vertical_peak_to_peak, ddof=1) / np.mean(vertical_peak_to_peak)
        )
        if len(vertical_peak_to_peak) > 1
        else math.nan,
        "vertical_periodic_moment_rms_mean_nm": float(
            np.mean(vertical_periodic_rms)
        ),
        "interpretation": (
            "integral-of-RRA-corrective-torque-after-removing-each-stride-mean;"
            "not-observed-segment-angular-momentum"
        ),
    }, tidy_rows


def compute_upper_limb_orbital_proxy(
    time_s: Sequence[float],
    markers_m: Mapping[str, np.ndarray],
    *,
    subject_mass_kg: float,
    reference_total_mass_kg: float,
    reference_segment_masses_kg: Mapping[str, float],
    cutoff_hz: float,
    filter_order: int,
    edge_exclusion_s: float,
) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
    """Compute a marker-based upper-limb orbital angular-momentum proxy."""
    time = np.asarray(time_s, dtype=float)
    required = {
        "CentreOfMass",
        "LSHO",
        "LELB",
        "LWRA",
        "LWRB",
        "LFIN",
        "RSHO",
        "RELB",
        "RWRA",
        "RWRB",
        "RFIN",
    }
    if not required.issubset(markers_m):
        raise ValueError(
            "G6c-S6 upper-limb proxy lacks markers: {}".format(
                sorted(required - set(markers_m))
            )
        )
    if time.size < 16 or np.any(np.diff(time) <= 0):
        raise ValueError("G6c-S6 upper-limb time is invalid")
    for marker in required:
        values = np.asarray(markers_m[marker], dtype=float)
        if values.shape != (time.size, 3) or not np.all(np.isfinite(values)):
            raise ValueError("G6c-S6 marker {} is invalid".format(marker))

    mass_scale = float(subject_mass_kg) / float(reference_total_mass_kg)
    segment_masses = {
        name: float(value) * mass_scale
        for name, value in reference_segment_masses_kg.items()
    }
    for name in ("humerus", "forearm", "hand"):
        if name not in segment_masses or segment_masses[name] <= 0:
            raise ValueError("G6c-S6 reference arm masses are incomplete")

    com = zero_phase_lowpass(
        time,
        np.asarray(markers_m["CentreOfMass"], dtype=float),
        cutoff_hz=float(cutoff_hz),
        order=int(filter_order),
    )
    com_velocity = np.gradient(com, time, axis=0)
    orbital = np.zeros((time.size, 3), dtype=float)
    definitions = []
    for side in ("L", "R"):
        shoulder = np.asarray(markers_m[side + "SHO"], dtype=float)
        elbow = np.asarray(markers_m[side + "ELB"], dtype=float)
        wrist = 0.5 * (
            np.asarray(markers_m[side + "WRA"], dtype=float)
            + np.asarray(markers_m[side + "WRB"], dtype=float)
        )
        finger = np.asarray(markers_m[side + "FIN"], dtype=float)
        definitions.extend(
            [
                (side + "-humerus", "humerus", 0.5 * (shoulder + elbow)),
                (side + "-forearm", "forearm", 0.5 * (elbow + wrist)),
                (side + "-hand", "hand", 0.5 * (wrist + finger)),
            ]
        )
    for _, mass_name, raw_position in definitions:
        position = zero_phase_lowpass(
            time,
            raw_position,
            cutoff_hz=float(cutoff_hz),
            order=int(filter_order),
        )
        velocity = np.gradient(position, time, axis=0)
        orbital += segment_masses[mass_name] * np.cross(
            position - com, velocity - com_velocity
        )
    orbital = zero_phase_lowpass(
        time,
        orbital,
        cutoff_hz=float(cutoff_hz),
        order=int(filter_order),
    )
    torque = np.gradient(orbital, time, axis=0)
    retained = (time >= time[0] + float(edge_exclusion_s)) & (
        time <= time[-1] - float(edge_exclusion_s)
    )
    if int(np.sum(retained)) < 16:
        raise ValueError("G6c-S6 edge exclusion retained too few Study 1 samples")

    rows = []
    for index in np.flatnonzero(retained):
        row = {"time_s": float(time[index])}
        for axis, label in enumerate(AXES):
            row["upper_limb_orbital_angular_momentum_{}".format(label)] = float(
                orbital[index, axis]
            )
            row["upper_limb_orbital_torque_{}".format(label)] = float(
                torque[index, axis]
            )
        rows.append(row)
    observed_h = orbital[retained]
    observed_torque = torque[retained]
    return {
        "method": "bilateral-segment-midpoint-orbital-angular-momentum-proxy",
        "spin_angular_momentum_included": False,
        "subject_mass_kg": float(subject_mass_kg),
        "reference_total_mass_kg": float(reference_total_mass_kg),
        "mass_scale": mass_scale,
        "scaled_one_side_segment_masses_kg": segment_masses,
        "scaled_bilateral_upper_limb_mass_kg": float(
            2.0 * sum(segment_masses.values())
        ),
        "lowpass_cutoff_hz": float(cutoff_hz),
        "lowpass_order": int(filter_order),
        "edge_exclusion_s": float(edge_exclusion_s),
        "retained_sample_count": int(np.sum(retained)),
        "angular_momentum_axis_rms_nms": np.sqrt(
            np.mean(observed_h * observed_h, axis=0)
        ).tolist(),
        "angular_momentum_peak_to_peak_nms": np.ptp(
            observed_h, axis=0
        ).tolist(),
        "orbital_torque_axis_rms_nm": np.sqrt(
            np.mean(observed_torque * observed_torque, axis=0)
        ).tolist(),
        "orbital_torque_axis_mean_nm": np.mean(
            observed_torque, axis=0
        ).tolist(),
        "coordinate_system": "source-C3D",
        "vertical_axis": "z",
        "interpretation": (
            "descriptive-observational-proxy;segment-midpoints-and-reference-"
            "masses;not-a-subject-specific-inverse-dynamics-result"
        ),
    }, rows


def inspect_full_body_c3d(
    path: Path,
    *,
    marker_groups: Mapping[str, Sequence[str]],
    calculated_markers: Sequence[str],
    minimum_valid_fraction: float,
    expected_analog_channels: Sequence[str],
    maximum_platform_roundtrip_error_nmm: float,
) -> tuple[Dict[str, Any], Dict[str, np.ndarray], np.ndarray]:
    """Inspect full-body observability and force-platform metadata in one C3D."""
    try:
        import ezc3d
    except ImportError as exc:
        raise RuntimeError("G6c-S6 requires ezc3d") from exc

    c3d = ezc3d.c3d(str(path), extract_forceplat_data=True)
    labels = [
        str(value).strip()
        for value in c3d["parameters"]["POINT"]["LABELS"]["value"]
    ]
    points = np.asarray(c3d["data"]["points"], dtype=float)[:3].transpose(2, 1, 0)
    residuals = np.asarray(
        c3d["data"]["meta_points"]["residuals"], dtype=float
    ).reshape(-1, points.shape[0]).T
    rate_hz = float(c3d["parameters"]["POINT"]["RATE"]["value"][0])
    unit_values = c3d["parameters"]["POINT"].get("UNITS", {}).get("value", [])
    point_unit = str(unit_values[0]).strip().lower() if unit_values else ""
    unit_scale = {"mm": 0.001, "m": 1.0}.get(point_unit)
    if unit_scale is None:
        raise ValueError("G6c-S6 C3D point unit is unsupported")
    time = np.arange(points.shape[0], dtype=float) / rate_hz

    markers = {}
    marker_validity = {}
    all_required = sorted(
        {
            marker
            for values in marker_groups.values()
            for marker in values
        }
        | {str(marker) for marker in calculated_markers}
    )
    for marker in all_required:
        if marker not in labels:
            marker_validity[marker] = {
                "present": False,
                "valid_fraction": 0.0,
                "pass": False,
            }
            continue
        index = labels.index(marker)
        xyz = points[:, index, :] * unit_scale
        valid = (
            np.all(np.isfinite(xyz), axis=1)
            & (np.linalg.norm(xyz, axis=1) > 0)
            & (residuals[:, index] >= 0)
        )
        marker_validity[marker] = {
            "present": True,
            "valid_fraction": float(np.mean(valid)),
            "pass": bool(np.mean(valid) >= float(minimum_valid_fraction)),
        }
        if np.all(valid):
            markers[marker] = xyz

    group_reports = {}
    for group, group_markers in marker_groups.items():
        group_reports[str(group)] = {
            "markers": [str(value) for value in group_markers],
            "pass": all(
                marker_validity.get(str(value), {}).get("pass") is True
                for value in group_markers
            ),
        }
    analog_labels = [
        str(value).strip()
        for value in c3d["parameters"]["ANALOG"]["LABELS"]["value"]
    ]
    missing_analog = sorted(set(expected_analog_channels) - set(analog_labels))
    force_platform_parameters = c3d["parameters"].get("FORCE_PLATFORM") or {}
    used_values = force_platform_parameters.get("USED", {}).get("value", [0])
    platform_metadata_count = int(used_values[0]) if used_values else 0
    platforms = c3d["data"].get("platform", [])
    extracted_platform_count = len(platforms)
    platform_reports = []
    for index, platform in enumerate(platforms):
        force = np.asarray(platform.get("force"), dtype=float).T
        moment = np.asarray(platform.get("moment"), dtype=float).T
        cop = np.asarray(platform.get("center_of_pressure"), dtype=float).T
        free = np.asarray(platform.get("Tz"), dtype=float).T
        corners = np.asarray(platform.get("corners"), dtype=float)
        shapes_ready = bool(
            force.ndim == 2
            and force.shape[1] == 3
            and moment.shape == force.shape
            and cop.shape == force.shape
            and free.shape == force.shape
            and corners.shape == (3, 4)
        )
        contact = (
            np.linalg.norm(force, axis=1) >= 20.0
            if shapes_ready
            else np.zeros(0, dtype=bool)
        )
        finite = bool(
            shapes_ready
            and np.all(np.isfinite(corners))
            and np.all(np.isfinite(force))
            and np.all(np.isfinite(moment))
            and np.any(contact)
            and np.all(np.isfinite(cop[contact]))
            and np.all(np.isfinite(free[contact]))
        )
        plate_center = (
            np.mean(corners, axis=1) if corners.shape == (3, 4) else np.zeros(3)
        )
        reconstructed = (
            np.cross(cop - plate_center, force) + free
            if shapes_ready
            else np.zeros_like(moment)
        )
        roundtrip_error = (
            float(np.max(np.abs(reconstructed[contact] - moment[contact])))
            if finite and np.any(contact)
            else math.inf
        )
        unit_force = str(platform.get("unit_force", "")).lower()
        unit_moment = str(platform.get("unit_moment", "")).lower()
        unit_position = str(platform.get("unit_position", "")).lower()
        units_ready = (
            unit_force == "n"
            and unit_moment in ("nmm", "n*mm")
            and unit_position == "mm"
        )
        platform_reports.append(
            {
                "platform_index_zero_based": index,
                "force_unit": unit_force,
                "moment_unit": unit_moment,
                "position_unit": unit_position,
                "contact_sample_count": int(np.sum(contact)),
                "plate_center_mm": plate_center.tolist(),
                "origin_moment_roundtrip_maximum_error_nmm": roundtrip_error,
                "pass": bool(
                    finite
                    and units_ready
                    and np.any(contact)
                    and roundtrip_error
                    <= float(maximum_platform_roundtrip_error_nmm)
                ),
            }
        )
    full_body_markers_ready = all(
        report["pass"] for report in group_reports.values()
    ) and all(
        marker_validity.get(str(marker), {}).get("pass") is True
        for marker in calculated_markers
    )
    report = {
        "source_sha256": sha256_file(path),
        "point_rate_hz": rate_hz,
        "point_unit": point_unit,
        "frame_count": int(points.shape[0]),
        "marker_groups": group_reports,
        "marker_validity": marker_validity,
        "full_body_markers_ready": full_body_markers_ready,
        "expected_analog_channels": [str(value) for value in expected_analog_channels],
        "missing_analog_channels": missing_analog,
        "raw_force_moment_analog_ready": not missing_analog,
        "force_platform_metadata_count": platform_metadata_count,
        "extracted_force_platform_count": extracted_platform_count,
        "force_platform_reports": platform_reports,
        "force_platform_geometry_ready": bool(
            platform_metadata_count > 0
            and extracted_platform_count == platform_metadata_count
            and all(value["pass"] for value in platform_reports)
        ),
    }
    report["external_load_roundtrip_ready"] = bool(
        report["raw_force_moment_analog_ready"]
        and report["force_platform_geometry_ready"]
    )
    return report, markers, time


def _read_subject_mass(path: Path, subject_id: str) -> float:
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    matched = [
        row for row in rows if str(row.get("subject_id", "")).strip() == subject_id
    ]
    if len(matched) != 1:
        raise ValueError("G6c-S6 Study 1 subject lookup is not unique")
    mass = float(matched[0]["weight_kg"])
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError("G6c-S6 Study 1 subject mass is invalid")
    return mass


def build_structural_adequacy_plan(
    protocol_path: Path,
    source_manifest_path: Path,
    data_root: Path,
    s2_result_path: Path,
    s5_result_path: Path,
    s2_work_dir: Path,
    study1_c3d_path: Path,
    study1_subject_info_path: Path,
) -> Dict[str, Any]:
    """Build a checksum-bound G6c-S6 structural-adequacy plan."""
    protocol_path = protocol_path.resolve()
    source_manifest_path = source_manifest_path.resolve()
    s2_result_path = s2_result_path.resolve()
    s5_result_path = s5_result_path.resolve()
    s2_work_dir = s2_work_dir.resolve()
    study1_c3d_path = study1_c3d_path.resolve()
    study1_subject_info_path = study1_subject_info_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != STRUCTURAL_ADEQUACY_SCHEMA_VERSION:
        raise ValueError("unsupported G6c-S6 protocol version")
    s2 = _load_json(s2_result_path)
    s5 = _load_json(s5_result_path)
    if s2.get("scope") != "g6c-s2-mechanics-localization-audit":
        raise ValueError("G6c-S6 S2 source has the wrong scope")
    if s5.get("scope") != "g6c-s5-rra-sensitivity-audit":
        raise ValueError("G6c-S6 S5 source has the wrong scope")
    if (
        s2.get("diagnostic_complete") is not True
        or s5.get("diagnostic_complete") is not True
        or s5.get("pass") is not False
    ):
        raise ValueError("G6c-S6 requires complete retained S2/S5 failures")

    design = protocol.get("design") or {}
    declared = design.get("source_work_artifacts") or {}
    paths = {
        "rra_actuation": s2_work_dir
        / str(declared.get("rra_actuation", "")),
        "s2_worker_report": s2_work_dir
        / str(declared.get("s2_worker_report", "")),
        "s2_plan": s2_work_dir / str(declared.get("s2_plan", "")),
        "s2_result": s2_result_path,
        "s5_result": s5_result_path,
        "study1_c3d": study1_c3d_path,
        "study1_subject_info": study1_subject_info_path,
    }
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError("G6c-S6 input is missing: {}".format(name))
        paths[name] = path.resolve()
    local_s2_worker = _load_json(paths["s2_worker_report"])
    if local_s2_worker != (s2.get("worker") or {}):
        raise ValueError("G6c-S6 local S2 worker differs from frozen S2")
    s2_artifacts = local_s2_worker.get("artifacts") or {}
    if sha256_file(paths["rra_actuation"]) != str(
        s2_artifacts.get("rra_actuation_sha256", "")
    ):
        raise ValueError("G6c-S6 RRA actuation differs from S2")
    if sha256_file(paths["s2_plan"]) != str(s2_artifacts.get("plan_sha256", "")):
        raise ValueError("G6c-S6 S2 plan hash mismatch")
    baseline = (
        ((s5.get("worker") or {}).get("cells") or {}).get(
            str(design.get("baseline_cell", ""))
        )
        or {}
    )
    if sha256_file(paths["rra_actuation"]) != str(
        (baseline.get("artifacts") or {}).get("final_actuation_sha256", "")
    ):
        raise ValueError("G6c-S6 actuation differs from the S5 source baseline")

    source = validate_public_source_bundle(source_manifest_path, data_root)
    if not source["pass"]:
        raise ValueError("G6c-S6 public source bundle failed checksum verification")
    ground_role = str(design.get("ground_reaction_role", ""))
    ground_entry = source["assets_by_role"].get(ground_role)
    if not ground_entry:
        raise ValueError("G6c-S6 ground-reaction role is missing")
    paths["ground_reaction"] = Path(str(ground_entry["absolute_path"])).resolve()

    study1 = design.get("study1_observational_control") or {}
    expected_c3d_hash = str(study1.get("expected_c3d_sha256", ""))
    if expected_c3d_hash and sha256_file(paths["study1_c3d"]) != expected_c3d_hash:
        raise ValueError("G6c-S6 Study 1 C3D checksum mismatch")
    subject_id = str(study1.get("subject_id", ""))
    subject_mass = _read_subject_mass(paths["study1_subject_info"], subject_id)

    thresholds = dict(protocol.get("thresholds") or {})
    if float(thresholds.get("residual_moment_fraction", math.nan)) != float(
        (s5.get("thresholds") or {}).get("residual_moment_fraction", math.nan)
    ):
        raise ValueError("G6c-S6 changed the inherited residual-moment gate")
    return {
        "schema_version": STRUCTURAL_ADEQUACY_SCHEMA_VERSION,
        "scope": "g6c-s6-structural-adequacy-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": source["manifest_sha256"],
        "prior_result_sha256": {
            "s2": sha256_file(s2_result_path),
            "s5": sha256_file(s5_result_path),
        },
        "input_paths": {name: str(path) for name, path in paths.items()},
        "input_sha256": {name: sha256_file(path) for name, path in paths.items()},
        "study31": dict(design.get("study31") or {}),
        "study1_observational_control": study1,
        "study1_subject_mass_kg": subject_mass,
        "thresholds": thresholds,
        "claim_boundary": str(protocol.get("claim_boundary", "")),
    }


def classify_structural_adequacy(
    study31: Mapping[str, Any],
    study1: Mapping[str, Any],
    *,
    thresholds: Mapping[str, Any],
) -> Dict[str, Any]:
    """Classify evidence without converting plausibility into causal validation."""
    correlations = study31.get("pairwise_phase_correlations") or {}
    moment_y = (correlations.get("periodic_moment") or {}).get("y") or []
    momentum_y = (
        (correlations.get("required_angular_momentum") or {}).get("y") or []
    )
    stride_count_pass = int(study31.get("stride_count", 0)) >= int(
        thresholds.get("minimum_complete_strides", 0)
    )
    moment_repeatability_pass = bool(moment_y) and min(moment_y) >= float(
        thresholds.get("minimum_vertical_phase_correlation", math.inf)
    )
    momentum_repeatability_pass = bool(momentum_y) and min(momentum_y) >= float(
        thresholds.get("minimum_vertical_phase_correlation", math.inf)
    )
    variability_pass = float(
        study31.get(
            "vertical_required_angular_momentum_peak_to_peak_cv", math.inf
        )
    ) <= float(thresholds.get("maximum_vertical_peak_to_peak_cv", -math.inf))
    closure = [
        abs(float(value))
        for stride in study31.get("strides", [])
        for value in stride.get("periodic_closure_error_nms", [])
    ]
    closure_pass = bool(closure) and max(closure) <= float(
        thresholds.get("maximum_periodic_closure_error_nms", -math.inf)
    )
    repeatable = all(
        (
            stride_count_pass,
            moment_repeatability_pass,
            momentum_repeatability_pass,
            variability_pass,
            closure_pass,
        )
    )
    marker_ready = study1.get("full_body_markers_ready") is True
    proxy = study1.get("upper_limb_proxy") or {}
    proxy_ready = bool(proxy.get("retained_sample_count", 0))
    external_ready = study1.get("external_load_roundtrip_ready") is True
    paired_ready = study1.get("paired_full_body_rra_ready") is True

    required_h = float(
        study31.get(
            "vertical_required_angular_momentum_peak_to_peak_mean_nms", math.nan
        )
    )
    observed_h = float(
        (proxy.get("angular_momentum_peak_to_peak_nms") or [math.nan] * 3)[2]
    )
    required_torque = float(
        study31.get("vertical_periodic_moment_rms_mean_nm", math.nan)
    )
    observed_torque = float(
        (proxy.get("orbital_torque_axis_rms_nm") or [math.nan] * 3)[2]
    )
    h_ratio = observed_h / required_h
    torque_ratio = observed_torque / required_torque
    low = float(thresholds.get("descriptive_same_order_ratio_minimum", math.nan))
    high = float(thresholds.get("descriptive_same_order_ratio_maximum", math.nan))
    same_order = bool(
        all(math.isfinite(value) and low <= value <= high for value in (h_ratio, torque_ratio))
    )
    if repeatable and marker_ready and proxy_ready and same_order:
        classification = (
            "repeatable-corrective-angular-momentum-with-observational-"
            "upper-limb-plausibility"
        )
    elif repeatable:
        classification = (
            "repeatable-corrective-angular-momentum-structural-cause-unidentified"
        )
    else:
        classification = "corrective-angular-momentum-not-repeatably-localized"
    return {
        "classification": classification,
        "study31_repeatability_pass": repeatable,
        "checks": {
            "minimum_complete_strides": stride_count_pass,
            "vertical_periodic_moment_repeatability": moment_repeatability_pass,
            "vertical_required_momentum_repeatability": momentum_repeatability_pass,
            "vertical_peak_to_peak_variability": variability_pass,
            "periodic_integration_closure": closure_pass,
            "study1_full_body_markers": marker_ready,
            "study1_upper_limb_proxy": proxy_ready,
            "study1_force_platform_geometry": external_ready,
            "study1_paired_model_control": paired_ready,
        },
        "descriptive_cross_dataset_comparison": {
            "study1_to_study31_angular_momentum_peak_to_peak_ratio": h_ratio,
            "study1_to_study31_torque_rms_ratio": torque_ratio,
            "same_order_descriptive_range": [low, high],
            "within_descriptive_range": same_order,
            "causal_test": False,
            "activity_matched": False,
            "subject_matched": False,
        },
        "structural_explanation_status": (
            "plausible-not-identified" if repeatable and same_order else "unidentified"
        ),
        "paired_full_body_rra_ready": paired_ready,
        "mechanics_closed": False,
    }


def evaluate_structural_adequacy(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    diagnosis = worker.get("diagnosis") or {}
    checks = {
        "worker_scope": worker.get("scope")
        == "g6c-s6-structural-adequacy-worker",
        "worker_inputs": worker.get("input_sha256") == plan.get("input_sha256"),
        "prior_results": worker.get("prior_result_sha256")
        == plan.get("prior_result_sha256"),
        "baseline_actuation": worker.get("baseline_actuation_verified") is True,
        "contact_selection_residual_blind": worker.get(
            "contact_selection_residual_blind"
        )
        is True,
        "study31_analysis_complete": worker.get("study31_analysis_complete")
        is True,
        "study1_analysis_complete": worker.get("study1_analysis_complete") is True,
        "artifacts": worker.get("artifacts_complete") is True,
    }
    diagnostic_complete = all(checks.values())
    return {
        "schema_version": STRUCTURAL_ADEQUACY_SCHEMA_VERSION,
        "scope": "g6c-s6-structural-adequacy-audit",
        "pass": False,
        "diagnostic_complete": diagnostic_complete,
        "mechanics_closed": False,
        "checks": checks,
        "thresholds": dict(plan.get("thresholds") or {}),
        "worker": dict(worker),
        "scientific_claim_boundary": str(plan.get("claim_boundary", "")),
        "next_gate": (
            "g6c-s6b-paired-full-body-structural-contrast"
            if diagnostic_complete
            else "blocked-at-g6c-s6-analysis"
        ),
        "structural_explanation_status": diagnosis.get(
            "structural_explanation_status", "unidentified"
        ),
    }


def write_structural_adequacy(
    result: Mapping[str, Any], output: Path
) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
