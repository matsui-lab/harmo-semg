"""Fail-closed mechanics auditing for the G6c OpenSim validation gate.

The module deliberately keeps force-plate semantics separate from downstream
quality thresholds.  A force-plate wrench is transformed by rigid-body wrench
algebra, while an independently generated inverse-dynamics result is compared
with the public reference only after the input convention has been identified.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import yaml

from harmo.dynamic_calibration import sha256_file, validate_public_source_bundle
from harmo.ingest.readers.sto import read_opensim_sto_trc


MECHANICS_AUDIT_SCHEMA_VERSION = "1.0.0"
_MASS_PATTERNS = (
    re.compile(
        r"(?:recommended|recommend(?:ed)?)[^\n]{0,80}?mass(?:\s+change)?"
        r"[^+\-\d]{0,20}([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)\s*kg",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:total\s+mass\s+change|mass\s+change)\s*[:=]\s*"
        r"([+\-]?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)(?:\s*kg)?",
        re.IGNORECASE,
    ),
)


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("G6c YAML root must be a mapping")
    return value


def canonical_sha256(value: Any) -> str:
    """Hash JSON-compatible content independent of key insertion order."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonicalize_opensim_log(text: str) -> str:
    """Remove only runtime-dependent clock fields from an OpenSim log."""
    rows = []
    for raw in str(text).splitlines():
        line = re.sub(
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?\]\s*",
            "[runtime-timestamp] ",
            raw,
        )
        line = re.sub(
            r"(-- (?:Start|End|Finish) time = ).*$",
            r"\1<runtime-clock>",
            line,
        )
        line = re.sub(
            r"(\b(?:in|took) )\d+(?:\.\d+)? second\(s\)",
            r"\1<runtime-seconds> second(s)",
            line,
        )
        line = re.sub(
            r"(-- Elapsed time = )\d+(?:\.\d+)? seconds?\.",
            r"\1<runtime-seconds> seconds.",
            line,
        )
        rows.append(line)
    return "\n".join(rows) + ("\n" if rows else "")


def pelvis_mass_center(model_path: Path) -> List[float]:
    """Read the pelvis COM in the pelvis frame from an OpenSim model."""
    try:
        root = ET.parse(str(model_path)).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError("OpenSim model XML is unreadable: {}".format(model_path)) from exc
    matches = [body for body in root.iter("Body") if body.get("name") == "pelvis"]
    if len(matches) != 1:
        raise ValueError("OpenSim model must contain exactly one pelvis Body")
    raw = matches[0].findtext("mass_center")
    if raw is None:
        raise ValueError("pelvis Body has no mass_center")
    values = [float(value) for value in raw.split()]
    if len(values) != 3 or not np.all(np.isfinite(values)):
        raise ValueError("pelvis mass_center must contain three finite values")
    return values


def transform_wrench_to_point(
    force: Sequence[Sequence[float]],
    moment_at_origin: Sequence[Sequence[float]],
    point_from_origin: Sequence[Sequence[float]],
) -> np.ndarray:
    """Return moment at ``point`` using ``M_point = M_origin - r x F``.

    Inputs use SI units (N, N m, m) and a common expressed-in frame.  The
    function intentionally performs no axis guessing or unit conversion.
    """
    forces = np.asarray(force, dtype=float)
    moments = np.asarray(moment_at_origin, dtype=float)
    points = np.asarray(point_from_origin, dtype=float)
    if forces.shape != moments.shape or forces.shape != points.shape:
        raise ValueError("force, moment, and point arrays must have identical shapes")
    if forces.ndim != 2 or forces.shape[1] != 3:
        raise ValueError("wrench arrays must have shape (n, 3)")
    if not np.all(np.isfinite(forces)) or not np.all(np.isfinite(moments)):
        raise ValueError("force and moment arrays must be finite")
    return moments - np.cross(points, forces)


def reconstruct_cop_and_free_moment(
    force: Sequence[Sequence[float]],
    moment_at_origin: Sequence[Sequence[float]],
    *,
    vertical_axis: int,
    surface_coordinate_m: float = 0.0,
    force_threshold_n: float = 20.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Reconstruct COP and the free-moment vector from an origin wrench.

    COP is constrained to the declared force-plate surface.  The two
    horizontal coordinates are solved from ``M = r x F + tau e_vertical``.
    Samples below the declared vertical-force threshold are returned as NaN
    and marked false in the contact mask.
    """
    forces = np.asarray(force, dtype=float)
    moments = np.asarray(moment_at_origin, dtype=float)
    if forces.shape != moments.shape or forces.ndim != 2 or forces.shape[1] != 3:
        raise ValueError("force and moment arrays must both have shape (n, 3)")
    if vertical_axis not in (0, 1, 2):
        raise ValueError("vertical_axis must be 0, 1, or 2")
    if not math.isfinite(float(surface_coordinate_m)):
        raise ValueError("surface coordinate must be finite")
    threshold = float(force_threshold_n)
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("force threshold must be finite and positive")
    contact = np.abs(forces[:, vertical_axis]) >= threshold
    cop = np.full_like(forces, np.nan, dtype=float)
    free = np.full_like(forces, np.nan, dtype=float)
    horizontal = [axis for axis in range(3) if axis != vertical_axis]
    unit = np.zeros(3, dtype=float)
    unit[vertical_axis] = 1.0
    for index in np.flatnonzero(contact):
        # Unknowns are the two horizontal COP coordinates and vertical free
        # moment.  Solve the overdetermined three-equation wrench relation.
        basis = []
        for axis in horizontal:
            vector = np.zeros(3, dtype=float)
            vector[axis] = 1.0
            basis.append(np.cross(vector, forces[index]))
        basis.append(unit)
        matrix = np.column_stack(basis)
        fixed = np.zeros(3, dtype=float)
        fixed[vertical_axis] = float(surface_coordinate_m)
        rhs = moments[index] - np.cross(fixed, forces[index])
        solution, _, rank, _ = np.linalg.lstsq(matrix, rhs, rcond=None)
        if rank < 3:
            contact[index] = False
            continue
        cop[index] = fixed
        cop[index, horizontal] = solution[:2]
        free[index] = transform_wrench_to_point(
            forces[index : index + 1],
            moments[index : index + 1],
            cop[index : index + 1],
        )[0]
        # Numerical noise in horizontal components is preserved for audit,
        # not silently projected away.
    return cop, free, contact


def classify_ground_torque_semantics(
    force: Sequence[Sequence[float]],
    origin_moment: Sequence[Sequence[float]],
    cop: Sequence[Sequence[float]],
    published_torque: Sequence[Sequence[float]],
    *,
    atol_nm: float = 1e-6,
) -> Dict[str, Any]:
    """Classify a published torque by direct wrench equivalence.

    The choice is based solely on the source transformation.  It never uses
    RRA residuals, IK error, or any other downstream acceptance metric.
    """
    forces = np.asarray(force, dtype=float)
    origins = np.asarray(origin_moment, dtype=float)
    cops = np.asarray(cop, dtype=float)
    published = np.asarray(published_torque, dtype=float)
    if not (forces.shape == origins.shape == cops.shape == published.shape):
        raise ValueError("all torque-classification arrays must have identical shapes")
    free = transform_wrench_to_point(forces, origins, cops)
    finite = np.all(np.isfinite(published), axis=1) & np.all(np.isfinite(free), axis=1)
    if not finite.any():
        raise ValueError("torque classification has no finite common samples")
    origin_rmse = float(np.sqrt(np.mean(np.square(published[finite] - origins[finite]))))
    free_rmse = float(np.sqrt(np.mean(np.square(published[finite] - free[finite]))))
    origin_match = origin_rmse <= float(atol_nm)
    free_match = free_rmse <= float(atol_nm)
    if origin_match == free_match:
        semantics = "ambiguous" if origin_match else "unresolved"
    else:
        semantics = "force_plate_origin_moment" if origin_match else "cop_free_moment"
    return {
        "semantics": semantics,
        "origin_moment_rmse_nm": origin_rmse,
        "cop_free_moment_rmse_nm": free_rmse,
        "sample_count": int(finite.sum()),
        "tolerance_nm": float(atol_nm),
        "pass": semantics in {"force_plate_origin_moment", "cop_free_moment"},
        "selection_basis": "source-wrench-equivalence-only",
    }


def _unit_scale(unit: str, quantity: str) -> float:
    normalized = str(unit).strip().lower().replace(" ", "")
    lookup = {
        "force": {"n": 1.0, "kn": 1000.0},
        "moment": {"nm": 1.0, "n*m": 1.0, "nmm": 0.001, "n*mm": 0.001},
        "position": {"m": 1.0, "mm": 0.001, "cm": 0.01},
    }
    if normalized not in lookup[quantity]:
        raise ValueError("unsupported C3D {} unit: {}".format(quantity, unit))
    return lookup[quantity][normalized]


def _mot_vector(frame: pd.DataFrame, prefix: str, stem: str) -> np.ndarray:
    columns = ["{}_{}{}".format(prefix, stem, axis) for axis in "xyz"]
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ValueError("MOT lacks external-load columns: {}".format(missing))
    return frame[columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)


def audit_c3d_mot_external_loads(
    c3d_path: Path,
    mot_path: Path,
    plate_to_prefix: Mapping[int, str],
    *,
    c3d_to_ground_rotation: Sequence[Sequence[float]],
    c3d_to_ground_translation_m: Sequence[float] = (0.0, 0.0, 0.0),
    force_rmse_tolerance_n: float = 1e-5,
    point_rmse_tolerance_m: float = 1e-6,
    torque_rmse_tolerance_nm: float = 1e-6,
) -> Dict[str, Any]:
    """Trace published MOT loads to raw C3D force-platform records.

    Plate-to-side assignment and the coordinate transform are protocol inputs;
    this function never searches over signs, axes, or assignments to improve a
    downstream OpenSim score.
    """
    try:
        import ezc3d  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("G6c raw C3D audit requires ezc3d") from exc
    rotation = np.asarray(c3d_to_ground_rotation, dtype=float)
    translation = np.asarray(c3d_to_ground_translation_m, dtype=float)
    if rotation.shape != (3, 3) or not np.all(np.isfinite(rotation)):
        raise ValueError("C3D-to-ground rotation must be a finite 3x3 matrix")
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0.0):
        raise ValueError("C3D-to-ground rotation must be orthonormal")
    if translation.shape != (3,) or not np.all(np.isfinite(translation)):
        raise ValueError("C3D-to-ground translation must be a finite three-vector")
    c3d = ezc3d.c3d(str(c3d_path), extract_forceplat_data=True)
    platforms = c3d["data"].get("platform", [])
    if not platforms:
        raise ValueError("C3D contains no extractable force platforms")
    analog_rate = float(c3d["parameters"]["ANALOG"]["RATE"]["value"][0])
    first_frame = float(c3d["header"]["analogs"]["first_frame"])
    _, mot = read_opensim_sto_trc(str(mot_path))
    mot_time = pd.to_numeric(mot["time"], errors="coerce").to_numpy(float)
    rows = []
    semantics = []
    for plate_index, prefix in sorted(plate_to_prefix.items()):
        if int(plate_index) < 0 or int(plate_index) >= len(platforms):
            raise ValueError("declared force-platform index is out of range")
        platform = platforms[int(plate_index)]
        force_scale = _unit_scale(platform["unit_force"], "force")
        moment_scale = _unit_scale(platform["unit_moment"], "moment")
        position_scale = _unit_scale(platform["unit_position"], "position")
        force = np.asarray(platform["force"], dtype=float).T * force_scale
        origin_moment = np.asarray(platform["moment"], dtype=float).T * moment_scale
        cop = np.asarray(platform["center_of_pressure"], dtype=float).T * position_scale
        tz = np.asarray(platform["Tz"], dtype=float).T * moment_scale
        c3d_time = first_frame / analog_rate + np.arange(force.shape[0]) / analog_rate
        force_ground = force @ rotation.T
        moment_ground = origin_moment @ rotation.T
        cop_ground = cop @ rotation.T + translation
        tz_ground = tz @ rotation.T
        # A change of origin by translation changes the origin moment.  COP and
        # free moment are already transformed at their respective points.
        moment_ground = moment_ground + np.cross(
            np.broadcast_to(translation, force_ground.shape), force_ground
        )

        def aligned(values: np.ndarray) -> np.ndarray:
            return np.column_stack(
                [np.interp(mot_time, c3d_time, values[:, axis]) for axis in range(3)]
            )

        aligned_force = aligned(force_ground)
        aligned_moment = aligned(moment_ground)
        aligned_cop = aligned(cop_ground)
        aligned_tz = aligned(tz_ground)
        published_force = _mot_vector(mot, str(prefix), "ground_force_v")
        published_cop = _mot_vector(mot, str(prefix), "ground_force_p")
        published_torque = _mot_vector(mot, str(prefix), "ground_torque_")
        contact = np.linalg.norm(published_force, axis=1) >= 20.0
        if not contact.any():
            raise ValueError("declared plate has no published contact samples")
        force_rmse = float(
            np.sqrt(np.mean(np.square(published_force[contact] - aligned_force[contact])))
        )
        point_rmse = float(
            np.sqrt(np.mean(np.square(published_cop[contact] - aligned_cop[contact])))
        )
        origin_rmse = float(
            np.sqrt(np.mean(np.square(published_torque[contact] - aligned_moment[contact])))
        )
        free_rmse = float(
            np.sqrt(np.mean(np.square(published_torque[contact] - aligned_tz[contact])))
        )
        origin_match = origin_rmse <= float(torque_rmse_tolerance_nm)
        free_match = free_rmse <= float(torque_rmse_tolerance_nm)
        torque_semantics = (
            "force_plate_origin_moment"
            if origin_match and not free_match
            else "cop_free_moment"
            if free_match and not origin_match
            else "ambiguous"
            if free_match and origin_match
            else "unresolved"
        )
        semantics.append(torque_semantics)
        rows.append(
            {
                "plate_index_zero_based": int(plate_index),
                "mot_prefix": str(prefix),
                "force_rmse_n": force_rmse,
                "cop_rmse_m": point_rmse,
                "origin_moment_rmse_nm": origin_rmse,
                "free_moment_rmse_nm": free_rmse,
                "torque_semantics": torque_semantics,
                "contact_sample_count": int(contact.sum()),
                "units": {"force": "N", "moment": "N m", "position": "m"},
                "pass": (
                    force_rmse <= float(force_rmse_tolerance_n)
                    and point_rmse <= float(point_rmse_tolerance_m)
                    and torque_semantics
                    in {"force_plate_origin_moment", "cop_free_moment"}
                ),
            }
        )
    return {
        "source_c3d_sha256": sha256_file(c3d_path),
        "published_mot_sha256": sha256_file(mot_path),
        "coordinate_transform": {
            "rotation": rotation.tolist(),
            "translation_m": translation.tolist(),
            "declared_not_fitted": True,
        },
        "plates": rows,
        "torque_semantics": semantics[0] if len(set(semantics)) == 1 else "mixed",
        "pass": bool(rows) and all(row["pass"] for row in rows),
        "selection_basis": "raw-c3d-provenance-not-downstream-threshold",
    }


def audit_c3d_mot_combined_platform(
    c3d_path: Path,
    mot_path: Path,
    *,
    platform_index: int,
    published_prefixes: Sequence[str],
    c3d_to_ground_rotation: Sequence[Sequence[float]],
    c3d_time_offset_s: float,
    vertical_axis: int,
    force_nrmse_tolerance: float,
    cop_rmse_tolerance_m: float,
    minimum_component_correlation: float,
    minimum_free_moment_correlation: float,
) -> Dict[str, Any]:
    """Audit a combined treadmill plate split into published foot loads.

    The declared side loads are recombined before comparison with the raw
    platform.  This preserves a traceable raw-source audit even when the public
    processing pipeline partitioned a single treadmill wrench across feet.
    """
    try:
        import ezc3d  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("G6c raw C3D audit requires ezc3d") from exc
    rotation = np.asarray(c3d_to_ground_rotation, dtype=float)
    if rotation.shape != (3, 3) or not np.allclose(
        rotation.T @ rotation, np.eye(3), atol=1e-8, rtol=0.0
    ):
        raise ValueError("C3D-to-ground rotation must be orthonormal")
    if vertical_axis not in (0, 1, 2):
        raise ValueError("vertical_axis must be 0, 1, or 2")
    c3d = ezc3d.c3d(str(c3d_path), extract_forceplat_data=True)
    platforms = c3d["data"].get("platform", [])
    if int(platform_index) < 0 or int(platform_index) >= len(platforms):
        raise ValueError("combined force-platform index is out of range")
    platform = platforms[int(platform_index)]
    analog_rate = float(c3d["parameters"]["ANALOG"]["RATE"]["value"][0])
    source_time = (
        np.arange(np.asarray(platform["force"]).shape[1], dtype=float) / analog_rate
        + float(c3d_time_offset_s)
    )
    force = (
        np.asarray(platform["force"], dtype=float).T
        * _unit_scale(platform["unit_force"], "force")
    ) @ rotation.T
    cop = (
        np.asarray(platform["center_of_pressure"], dtype=float).T
        * _unit_scale(platform["unit_position"], "position")
    ) @ rotation.T
    free = (
        np.asarray(platform["Tz"], dtype=float).T
        * _unit_scale(platform["unit_moment"], "moment")
    ) @ rotation.T
    for values in (cop, free):
        for axis in range(3):
            finite = np.isfinite(values[:, axis])
            if finite.sum() < 2:
                raise ValueError("C3D platform output has insufficient finite samples")
            values[:, axis] = np.interp(
                source_time, source_time[finite], values[finite, axis]
            )
    _, mot = read_opensim_sto_trc(str(mot_path))
    target_time = pd.to_numeric(mot["time"], errors="coerce").to_numpy(float)

    def align(values: np.ndarray) -> np.ndarray:
        return np.column_stack(
            [np.interp(target_time, source_time, values[:, axis]) for axis in range(3)]
        )

    raw_force = align(force)
    raw_cop = align(cop)
    raw_free = align(free)
    side_force = [_mot_vector(mot, str(prefix), "ground_force_v") for prefix in published_prefixes]
    side_cop = [_mot_vector(mot, str(prefix), "ground_force_p") for prefix in published_prefixes]
    side_torque = [_mot_vector(mot, str(prefix), "ground_torque_") for prefix in published_prefixes]
    published_force = np.sum(side_force, axis=0)
    vertical = np.sum([values[:, vertical_axis] for values in side_force], axis=0)
    weighted_cop = np.sum(
        [point * values[:, vertical_axis, None] for point, values in zip(side_cop, side_force)],
        axis=0,
    )
    published_cop = weighted_cop / np.where(
        np.abs(vertical[:, None]) < 1e-12, 1.0, vertical[:, None]
    )
    published_free = np.sum(side_torque, axis=0)
    contact = (np.abs(published_force[:, vertical_axis]) >= 20.0) & (
        np.abs(raw_force[:, vertical_axis]) >= 20.0
    )
    if int(contact.sum()) < 10:
        raise ValueError("combined-platform audit has insufficient contact samples")
    force_difference = published_force[contact] - raw_force[contact]
    force_rmse = float(np.sqrt(np.mean(np.square(force_difference))))
    force_denominator = float(
        np.sqrt(np.mean(np.square(published_force[contact])))
    )
    force_nrmse = force_rmse / force_denominator
    force_correlations = []
    for axis in range(3):
        force_correlations.append(
            float(
                np.corrcoef(
                    published_force[contact, axis], raw_force[contact, axis]
                )[0, 1]
            )
        )
    cop_rmse = float(
        np.sqrt(np.mean(np.square(published_cop[contact] - raw_cop[contact])))
    )
    free_corr = float(
        np.corrcoef(
            published_free[contact, vertical_axis], raw_free[contact, vertical_axis]
        )[0, 1]
    )
    horizontal = [axis for axis in range(3) if axis != vertical_axis]
    published_horizontal_torque_max = float(
        np.max(np.abs(published_free[contact][:, horizontal]))
    )
    semantics = (
        "cop_free_moment"
        if published_horizontal_torque_max <= 1e-12
        and free_corr >= float(minimum_free_moment_correlation)
        else "unresolved"
    )
    checks = {
        "force_nrmse": force_nrmse <= float(force_nrmse_tolerance),
        "force_component_correlation": min(force_correlations)
        >= float(minimum_component_correlation),
        "cop_rmse": cop_rmse <= float(cop_rmse_tolerance_m),
        "free_moment_semantics": semantics == "cop_free_moment",
    }
    return {
        "source_c3d_sha256": sha256_file(c3d_path),
        "published_mot_sha256": sha256_file(mot_path),
        "aggregation": "recombine-published-foot-loads-to-single-raw-platform",
        "platform_index_zero_based": int(platform_index),
        "published_prefixes": [str(value) for value in published_prefixes],
        "coordinate_transform": {
            "rotation": rotation.tolist(),
            "translation_m": [0.0, 0.0, 0.0],
            "time_offset_s": float(c3d_time_offset_s),
            "declared_not_fitted_to_opensim_quality": True,
        },
        "force_rmse_n": force_rmse,
        "force_nrmse": force_nrmse,
        "force_component_correlations": force_correlations,
        "cop_rmse_m": cop_rmse,
        "vertical_free_moment_correlation": free_corr,
        "published_horizontal_torque_max_nm": published_horizontal_torque_max,
        "torque_semantics": semantics,
        "contact_sample_count": int(contact.sum()),
        "checks": checks,
        "pass": all(checks.values()),
        "selection_basis": "raw-c3d-source-equivalence-not-rra-residual",
    }


def storage_time_grid(path: Path) -> np.ndarray:
    _, data = read_opensim_sto_trc(str(path))
    if "time" not in data:
        raise ValueError("OpenSim storage has no time column")
    times = pd.to_numeric(data["time"], errors="coerce").to_numpy(float)
    if times.size < 2 or not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0):
        raise ValueError("OpenSim storage time must be finite and strictly increasing")
    return times


def trc_time_grid(path: Path, start_s: float, end_s: float) -> np.ndarray:
    _, data = read_opensim_sto_trc(str(path))
    if "time" not in data:
        raise ValueError("TRC has no time column")
    times = pd.to_numeric(data["time"], errors="coerce").to_numpy(float)
    mask = (times >= float(start_s) - 1e-10) & (times <= float(end_s) + 1e-10)
    selected = times[mask]
    if selected.size < 2 or np.any(np.diff(selected) <= 0):
        raise ValueError("source marker grid has fewer than two ordered samples")
    return selected


def _storage_header(path: Path) -> Tuple[List[str], int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    end = next((index for index, line in enumerate(lines) if line.strip() == "endheader"), None)
    if end is None:
        raise ValueError("OpenSim storage has no endheader")
    return lines[: end + 1], end


def resample_storage_to_grid(
    source: Path,
    target_times: Sequence[float],
    output: Path,
    *,
    time_atol_s: float = 1e-10,
) -> Dict[str, Any]:
    """Linearly resample OpenSim coordinates to an explicit source grid."""
    header, data = read_opensim_sto_trc(str(source))
    del header
    source_times = pd.to_numeric(data["time"], errors="coerce").to_numpy(float)
    target = np.asarray(target_times, dtype=float)
    if target.size < 2 or not np.all(np.isfinite(target)) or np.any(np.diff(target) <= 0):
        raise ValueError("target time grid must be finite and strictly increasing")
    if target[0] < source_times[0] - time_atol_s or target[-1] > source_times[-1] + time_atol_s:
        raise ValueError("target grid would extrapolate beyond source kinematics")
    columns = [str(column) for column in data.columns if str(column) != "time"]
    values: Dict[str, np.ndarray] = {}
    for column in columns:
        numeric = pd.to_numeric(data[column], errors="coerce").to_numpy(float)
        finite = np.isfinite(source_times) & np.isfinite(numeric)
        if finite.sum() < 2:
            raise ValueError("storage column {} has insufficient finite data".format(column))
        values[column] = np.interp(target, source_times[finite], numeric[finite])

    original_header, _ = _storage_header(source)
    filtered = [
        line
        for line in original_header[:-1]
        if not re.match(r"^(nRows|nColumns|datarows|datacolumns)\s*=", line, re.I)
    ]
    filtered.extend(
        [
            "nRows={}".format(target.size),
            "nColumns={}".format(len(columns) + 1),
            "endheader",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(filtered) + "\n")
        stream.write("\t".join(["time"] + columns) + "\n")
        for row, time in enumerate(target):
            stream.write(
                "\t".join(
                    ["{:.12g}".format(time)]
                    + ["{:.12g}".format(values[column][row]) for column in columns]
                )
                + "\n"
            )
    observed = storage_time_grid(output)
    exact = observed.shape == target.shape and np.allclose(
        observed, target, atol=time_atol_s, rtol=0.0
    )
    if not exact:
        raise RuntimeError("resampled storage did not preserve the requested time grid")
    return {
        "source_file_sha256": sha256_file(source),
        "output_file_sha256": sha256_file(output),
        "source_row_count": int(source_times.size),
        "output_row_count": int(target.size),
        "start_time_s": float(target[0]),
        "end_time_s": float(target[-1]),
        "target_grid_sha256": canonical_sha256([float(value) for value in target]),
        "exact_source_grid": True,
    }


def parse_rra_mass_recommendation(text: str) -> Dict[str, Any]:
    """Parse, but never apply, an RRA-recommended total mass change."""
    matches: List[float] = []
    for line in text.splitlines():
        for pattern in _MASS_PATTERNS:
            match = pattern.search(line)
            if match is not None:
                matches.append(float(match.group(1)))
                break
    if not matches:
        return {
            "found": False,
            "recommended_mass_change_kg": None,
            "recommendation_sequence_kg": [],
            "auto_applied": False,
        }
    # A two-pass RRA legitimately emits a distinct recommendation after each
    # pass.  Preserve the complete sequence and expose the final value; do not
    # mistake convergence across passes for a conflicting log.
    return {
        "found": True,
        "recommended_mass_change_kg": matches[-1],
        "recommendation_sequence_kg": matches,
        "auto_applied": False,
        "policy": "record-only-unless-independently-consistent",
    }


def assess_mass_recommendation(
    recommendation_kg: Optional[float],
    recorded_mass_kg: float,
    mean_vertical_grf_n: float,
    *,
    gravity_m_s2: float = 9.80665,
    relative_tolerance: float = 0.02,
) -> Dict[str, Any]:
    force_equivalent = float(mean_vertical_grf_n) / float(gravity_m_s2)
    consistency = abs(force_equivalent - float(recorded_mass_kg)) / float(recorded_mass_kg)
    recommendation = None if recommendation_kg is None else float(recommendation_kg)
    # A mass change is independently justified only when the measured mean
    # vertical force indicates the same change within tolerance.
    implied = force_equivalent - float(recorded_mass_kg)
    recommended_consistent = (
        recommendation is not None
        and abs(recommendation - implied)
        <= float(relative_tolerance) * float(recorded_mass_kg)
    )
    return {
        "recorded_mass_kg": float(recorded_mass_kg),
        "mean_vertical_grf_n": float(mean_vertical_grf_n),
        "force_equivalent_mass_kg": force_equivalent,
        "force_mass_relative_difference": consistency,
        "recommended_mass_change_kg": recommendation,
        "force_implied_mass_change_kg": implied,
        "independently_consistent": bool(recommended_consistent),
        "auto_apply": False,
    }


def waveform_metrics(reference: Sequence[float], candidate: Sequence[float]) -> Dict[str, float]:
    ref = np.asarray(reference, dtype=float)
    cand = np.asarray(candidate, dtype=float)
    finite = np.isfinite(ref) & np.isfinite(cand)
    if finite.sum() < 3:
        raise ValueError("waveform comparison requires at least three finite pairs")
    ref = ref[finite]
    cand = cand[finite]
    rmse = float(np.sqrt(np.mean(np.square(cand - ref))))
    denominator = float(np.sqrt(np.mean(np.square(ref))))
    nrmse = 0.0 if denominator == 0 and rmse == 0 else rmse / denominator
    correlation = float(np.corrcoef(ref, cand)[0, 1])
    return {"rmse": rmse, "nrmse": nrmse, "correlation": correlation}


def compare_inverse_dynamics(
    reference_path: Path,
    candidate_path: Path,
    coordinates: Sequence[str],
    *,
    comparison_window_s: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """Compare selected ID moments after interpolation to reference times.

    ``comparison_window_s`` is an explicitly declared diagnostic window, not a
    data-dependent crop.  It is used to remove the fixed boundary padding from
    a marker-rederived IK diagnostic while the primary public-IK closure test
    continues to use the complete released grid.
    """
    _, reference = read_opensim_sto_trc(str(reference_path))
    _, candidate = read_opensim_sto_trc(str(candidate_path))
    ref_time = pd.to_numeric(reference["time"], errors="coerce").to_numpy(float)
    cand_time = pd.to_numeric(candidate["time"], errors="coerce").to_numpy(float)
    common_start = max(float(np.nanmin(ref_time)), float(np.nanmin(cand_time)))
    common_end = min(float(np.nanmax(ref_time)), float(np.nanmax(cand_time)))
    common = (
        np.isfinite(ref_time)
        & (ref_time >= common_start - 1e-10)
        & (ref_time <= common_end + 1e-10)
    )
    declared_window = None
    if comparison_window_s is not None:
        if len(comparison_window_s) != 2:
            raise ValueError("comparison window must contain start and end")
        declared_start, declared_end = [float(value) for value in comparison_window_s]
        if not (
            math.isfinite(declared_start)
            and math.isfinite(declared_end)
            and declared_end > declared_start
        ):
            raise ValueError("comparison window must be finite and ordered")
        common &= (ref_time >= declared_start - 1e-10) & (
            ref_time <= declared_end + 1e-10
        )
        declared_window = [declared_start, declared_end]
    if common.sum() < 3:
        raise ValueError("ID artifacts have fewer than three common time samples")
    comparison_time = ref_time[common]
    rows = []
    for coordinate in coordinates:
        key = str(coordinate).lower()
        if key not in reference or key not in candidate:
            raise ValueError("ID moment is missing from one artifact: {}".format(key))
        cand_values = pd.to_numeric(candidate[key], errors="coerce").to_numpy(float)
        aligned = np.interp(comparison_time, cand_time, cand_values)
        ref_values = pd.to_numeric(reference[key], errors="coerce").to_numpy(float)[common]
        rows.append({"coordinate": key, **waveform_metrics(ref_values, aligned)})
    nrmse = [row["nrmse"] for row in rows]
    correlations = [row["correlation"] for row in rows]
    return {
        "coordinates": rows,
        "median_nrmse": float(np.median(nrmse)),
        "maximum_nrmse": float(np.max(nrmse)),
        "minimum_correlation": float(np.min(correlations)),
        "comparison_row_count": int(common.sum()),
        "comparison_start_time_s": float(comparison_time[0]),
        "comparison_end_time_s": float(comparison_time[-1]),
        "declared_comparison_window_s": declared_window,
    }


def diagnose_released_kinematics_closure(
    public_ik_comparison: Mapping[str, Any],
    marker_rederived_comparison: Mapping[str, Any],
    *,
    distal_coordinates: Sequence[str],
    proximal_coordinates: Sequence[str],
    thresholds: Mapping[str, Any],
    minimum_proximal_improvement_factor: float,
) -> Dict[str, Any]:
    """Classify a released IK→ID dependency edge without replacing it.

    A public bundle is closed only when the released model, IK, loads, and ID
    pass the frozen ID waveform thresholds directly.  Marker-rederived IK is a
    localization control: improvement can implicate the released kinematics
    dependency, but it can never be promoted to the missing original upstream
    artifact or used to turn G6c into a pass.
    """

    maximum_nrmse = float(thresholds.get("maximum_nrmse", 0.05))
    minimum_correlation = float(thresholds.get("minimum_correlation", 0.99))
    improvement_threshold = float(minimum_proximal_improvement_factor)
    if not math.isfinite(improvement_threshold) or improvement_threshold <= 1.0:
        raise ValueError("proximal improvement factor must be finite and greater than one")

    def rows_by_name(comparison: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
        rows = comparison.get("coordinates") or []
        return {str(row.get("coordinate", "")).lower(): row for row in rows}

    released = rows_by_name(public_ik_comparison)
    rederived = rows_by_name(marker_rederived_comparison)
    distal = [str(value).lower() for value in distal_coordinates]
    proximal = [str(value).lower() for value in proximal_coordinates]
    requested = distal + proximal
    missing = sorted(
        coordinate
        for coordinate in requested
        if coordinate not in released or coordinate not in rederived
    )
    if missing:
        raise ValueError(
            "kinematics closure diagnostic lacks coordinates: {}".format(missing)
        )

    def row_pass(row: Mapping[str, Any]) -> bool:
        return float(row.get("nrmse", math.inf)) <= maximum_nrmse and float(
            row.get("correlation", -math.inf)
        ) >= minimum_correlation

    distal_released_pass = all(row_pass(released[coordinate]) for coordinate in distal)
    proximal_released_pass = all(
        row_pass(released[coordinate]) for coordinate in proximal
    )
    public_bundle_closed = (
        float(public_ik_comparison.get("median_nrmse", math.inf))
        <= float(thresholds.get("median_nrmse", 0.02))
        and float(public_ik_comparison.get("maximum_nrmse", math.inf))
        <= maximum_nrmse
        and float(public_ik_comparison.get("minimum_correlation", -math.inf))
        >= minimum_correlation
    )
    improvements = {}
    for coordinate in proximal:
        released_nrmse = float(released[coordinate]["nrmse"])
        rederived_nrmse = float(rederived[coordinate]["nrmse"])
        improvements[coordinate] = (
            math.inf if rederived_nrmse == 0.0 else released_nrmse / rederived_nrmse
        )
    minimum_observed_improvement = min(improvements.values()) if improvements else 0.0
    localized = (
        not public_bundle_closed
        and distal_released_pass
        and not proximal_released_pass
        and minimum_observed_improvement >= improvement_threshold
    )
    classification = (
        "released-kinematics-to-id-nonclosure"
        if localized
        else "unresolved-source-nonclosure"
        if not public_bundle_closed
        else "released-source-bundle-closed"
    )
    return {
        "classification": classification,
        "public_bundle_closed": bool(public_bundle_closed),
        "released_distal_coordinates_pass": bool(distal_released_pass),
        "released_proximal_coordinates_pass": bool(proximal_released_pass),
        "distal_coordinates": distal,
        "proximal_coordinates": proximal,
        "proximal_nrmse_improvement_factor": improvements,
        "minimum_observed_proximal_improvement_factor": float(
            minimum_observed_improvement
        ),
        "minimum_required_proximal_improvement_factor": improvement_threshold,
        "marker_rederived_reference_replacement_allowed": False,
        "marker_rederived_comparison_passes_frozen_thresholds": bool(
            float(marker_rederived_comparison.get("median_nrmse", math.inf))
            <= float(thresholds.get("median_nrmse", 0.02))
            and float(marker_rederived_comparison.get("maximum_nrmse", math.inf))
            <= maximum_nrmse
            and float(marker_rederived_comparison.get("minimum_correlation", -math.inf))
            >= minimum_correlation
        ),
        "interpretation": (
            "The released model/IK/loads/setup do not reproduce the released ID. "
            "Marker-rederived IK localizes the dominant discrepancy but is a "
            "diagnostic control, not the unreleased source trajectory."
        ),
    }


def mean_total_vertical_grf(
    storage_path: Path,
    *,
    start_time_s: float,
    end_time_s: float,
    vertical_axis: int,
    prefixes: Sequence[str],
) -> Dict[str, Any]:
    """Compute mean total vertical GRF on an explicitly bounded source window."""
    if vertical_axis not in (0, 1, 2):
        raise ValueError("vertical_axis must be 0, 1, or 2")
    _, table = read_opensim_sto_trc(str(storage_path))
    times = pd.to_numeric(table["time"], errors="coerce").to_numpy(float)
    window = (
        np.isfinite(times)
        & (times >= float(start_time_s) - 1e-10)
        & (times <= float(end_time_s) + 1e-10)
    )
    if not window.any():
        raise ValueError("GRF storage has no rows in the requested window")
    axis = "xyz"[vertical_axis]
    requested = ["{}_ground_force_v{}".format(str(prefix).lower(), axis) for prefix in prefixes]
    column_lookup = {str(column).lower(): str(column) for column in table.columns}
    missing = [column for column in requested if column not in column_lookup]
    if missing:
        raise ValueError("GRF storage lacks vertical-force columns: {}".format(missing))
    columns = [column_lookup[column] for column in requested]
    values = np.zeros(int(window.sum()), dtype=float)
    for column in columns:
        values += pd.to_numeric(table[column], errors="coerce").to_numpy(float)[window]
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("vertical GRF has no finite samples in the requested window")
    return {
        "mean_total_vertical_grf_n": float(np.mean(finite)),
        "sample_count": int(finite.size),
        "columns": columns,
        "column_matching": "case-insensitive-exact-name",
        "start_time_s": float(times[window][0]),
        "end_time_s": float(times[window][-1]),
    }


def verify_exact_grid(path: Path, expected: Sequence[float], atol_s: float = 1e-10) -> bool:
    observed = storage_time_grid(path)
    target = np.asarray(expected, dtype=float)
    return bool(
        observed.shape == target.shape
        and np.allclose(observed, target, atol=float(atol_s), rtol=0.0)
    )


def build_mechanics_audit_plan(
    protocol_path: Path, manifest_path: Path, data_root: Path
) -> Dict[str, Any]:
    """Bind a G6c worker to checksum-verified source-faithfulness inputs."""
    protocol_path = protocol_path.resolve()
    manifest_path = manifest_path.resolve()
    protocol = _load_yaml(protocol_path)
    if str(protocol.get("schema_version")) != MECHANICS_AUDIT_SCHEMA_VERSION:
        raise ValueError("unsupported G6c protocol version")
    audit = validate_public_source_bundle(manifest_path, data_root)
    if not audit["pass"]:
        raise ValueError("G6c source bundle failed checksum verification")
    design = protocol.get("design") or {}
    role_map = design.get("roles") or {}
    required_keys = (
        "model",
        "markers",
        "ground_reaction",
        "public_ik",
        "public_id",
        "ik_setup",
        "id_setup",
    )
    if any(not str(role_map.get(key, "")).strip() for key in required_keys):
        raise ValueError("G6c protocol must declare every required source role")
    roles = audit["assets_by_role"]
    missing = sorted(set(str(role_map[key]) for key in required_keys) - set(roles))
    if missing:
        raise ValueError("G6c source manifest lacks roles: {}".format(missing))
    paths = {
        key: str(Path(str(roles[str(role_map[key])]["absolute_path"])).resolve())
        for key in required_keys
    }
    raw_role = str(role_map.get("raw_c3d", "")).strip()
    if raw_role:
        if raw_role not in roles:
            raise ValueError("G6c raw C3D role is absent")
        paths["raw_c3d"] = str(Path(str(roles[raw_role]["absolute_path"])).resolve())
    start, end = [float(value) for value in design.get("window_s", [])]
    grid = trc_time_grid(Path(paths["markers"]), start, end)
    public_times = storage_time_grid(Path(paths["public_ik"]))
    public_grid = public_times[np.isfinite(public_times)]
    if public_grid.size < 3:
        raise ValueError("G6c public IK has insufficient source-faithfulness samples")
    closure = design.get("closure_diagnostic") or {}
    closure_required = (
        "marker_rederived_window_s",
        "comparison_window_s",
        "distal_moments",
        "proximal_moments",
        "minimum_proximal_improvement_factor",
    )
    closure_missing = [key for key in closure_required if key not in closure]
    if closure_missing:
        raise ValueError(
            "G6c closure diagnostic lacks fields: {}".format(closure_missing)
        )
    diagnostic_window = [float(value) for value in closure["marker_rederived_window_s"]]
    comparison_window = [float(value) for value in closure["comparison_window_s"]]
    if (
        len(diagnostic_window) != 2
        or len(comparison_window) != 2
        or diagnostic_window[1] <= diagnostic_window[0]
        or comparison_window[1] <= comparison_window[0]
        or comparison_window[0] <= diagnostic_window[0]
        or comparison_window[1] >= diagnostic_window[1]
    ):
        raise ValueError(
            "G6c closure comparison window must be ordered and strictly inside "
            "the marker-rederived window"
        )
    if closure.get("reference_replacement_allowed") is not False:
        raise ValueError("G6c marker-rederived reference replacement must be false")
    major = [str(value).lower() for value in design.get("major_moments", [])]
    closure_coordinates = [
        str(value).lower()
        for key in ("distal_moments", "proximal_moments")
        for value in closure[key]
    ]
    if not closure_coordinates or not set(closure_coordinates).issubset(set(major)):
        raise ValueError("G6c closure coordinates must be declared major moments")
    return {
        "schema_version": MECHANICS_AUDIT_SCHEMA_VERSION,
        "scope": "g6c-mechanics-audit-plan",
        "protocol_sha256": sha256_file(protocol_path),
        "source_manifest_sha256": audit["manifest_sha256"],
        "input_paths": paths,
        "input_sha256": {key: sha256_file(Path(path)) for key, path in paths.items()},
        "start_time_s": start,
        "end_time_s": end,
        "source_time_grid_s": [float(value) for value in grid],
        "source_time_grid_sha256": canonical_sha256([float(value) for value in grid]),
        "source_faithfulness_domain": str(
            design.get("source_faithfulness_domain", "full-public-ik-grid")
        ),
        "public_id_time_grid_s": [float(value) for value in public_grid],
        "public_id_time_grid_sha256": canonical_sha256(
            [float(value) for value in public_grid]
        ),
        "vertical_axis": int(design.get("vertical_axis", 1)),
        "moment_semantics": str(design.get("moment_semantics", "must-be-audited")),
        "external_loads": design.get("external_loads") or {},
        "closure_diagnostic": closure,
        "major_moments": major,
        "opensim": protocol.get("opensim") or {},
        "thresholds": protocol.get("thresholds") or {},
    }


def evaluate_mechanics_audit(
    plan: Mapping[str, Any], worker: Mapping[str, Any]
) -> Dict[str, Any]:
    """Evaluate G6c without rewriting or weakening any threshold."""
    thresholds = plan.get("thresholds") or {}
    comparison = worker.get("public_id_reproduction") or {}
    checks = {
        "worker_scope": worker.get("scope") == "g6c-mechanics-audit-worker",
        "worker_input_hashes": worker.get("input_sha256") == plan.get("input_sha256"),
        "source_grid_exact": worker.get("source_grid_exact") is True,
        "public_id_grid_exact": worker.get("public_id_grid_exact") is True,
        "residual_point_is_pelvis_com": worker.get("residual_point_is_pelvis_com") is True,
        "mass_recommendation_not_auto_applied": (
            (worker.get("mass_recommendation") or {}).get("auto_applied") is False
        ),
        "external_load_semantics_traceable": (
            (worker.get("external_load_audit") or {}).get("pass") is True
        ),
        "released_source_bundle_closed": (
            (worker.get("kinematics_id_closure") or {}).get("public_bundle_closed")
            is True
        ),
        "public_id_median_nrmse": float(comparison.get("median_nrmse", math.inf))
        <= float(thresholds.get("median_nrmse", 0.02)),
        "public_id_maximum_nrmse": float(comparison.get("maximum_nrmse", math.inf))
        <= float(thresholds.get("maximum_nrmse", 0.05)),
        "public_id_minimum_correlation": float(
            comparison.get("minimum_correlation", -math.inf)
        )
        >= float(thresholds.get("minimum_correlation", 0.99)),
        "common_residual_explained": worker.get("common_residual_explained") is True,
    }
    passed = all(checks.values())
    return {
        "schema_version": MECHANICS_AUDIT_SCHEMA_VERSION,
        "scope": "g6c-mechanics-audit",
        "pass": passed,
        "checks": checks,
        "thresholds": dict(thresholds),
        "worker": dict(worker),
        "scientific_claim_boundary": (
            "G6c source-faithfulness and mechanics evaluation integrity only; "
            "dynamic-only personalization is not validated"
        ),
        "next_gate": "g6d" if passed else "blocked-at-g6c",
    }


def write_mechanics_audit(result: Mapping[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
