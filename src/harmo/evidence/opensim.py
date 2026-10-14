"""Adapters from harmo OpenSim validation output to public evidence contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .io import read_json, sha256_file
from .models import ArtifactReference, OpenSimArtifact, TrustReport


def _ratio(data: Dict[str, Any], numerator: str, denominator: str) -> Optional[float]:
    top = data.get(numerator)
    bottom = data.get(denominator)
    if top is None or bottom is None or float(bottom) <= 0:
        return None
    return float(top) / float(bottom)


def _reference(
    artifact_id: str, path: str, media_type: Optional[str] = None
) -> ArtifactReference:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise ValueError("OpenSim artifact file does not exist: {}".format(resolved))
    return ArtifactReference(
        artifact_id=artifact_id,
        uri=resolved.as_uri(),
        sha256=sha256_file(str(resolved)),
        media_type=media_type,
    )


def opensim_evidence_from_validation(
    validation_path: str,
    artifact_id: str,
    recording_id: str,
    model_path: str,
    output_paths: Optional[List[str]] = None,
) -> Tuple[OpenSimArtifact, TrustReport]:
    """Translate an existing validation.json without inventing missing evidence."""
    data = read_json(validation_path)
    outputs = [
        _reference("opensim-validation", validation_path, "application/json")
    ] + [
        _reference("opensim-output-{}".format(index + 1), path)
        for index, path in enumerate(output_paths or [])
    ]
    artifact = OpenSimArtifact(
        artifact_id=artifact_id,
        recording_id=recording_id,
        model=_reference("opensim-model", model_path, "application/xml"),
        outputs=outputs,
        ik_rms_error_m=_optional_float(data.get("ik_rms_error")),
        ik_max_error_m=_optional_float(data.get("ik_max_error")),
        residual_force_fraction_peak=_ratio(
            data, "residual_force_peak", "ex_loads_peak"
        ),
        residual_force_fraction_rms=_ratio(data, "residual_force_rms", "ex_loads_rms"),
        residual_moment_fraction_peak=_ratio(
            data, "residual_moment_peak", "ex_moment_peak"
        ),
        residual_moment_fraction_rms=_ratio(
            data, "residual_moment_rms", "ex_moment_rms"
        ),
        rra_angle_change_rms_deg=_optional_float(data.get("rra_angle_change_rms_deg")),
        manifest_run_id=data.get("run_id"),
    )
    return artifact, TrustReport(
        subject_id=artifact_id, evidence=artifact.trust_evidence()
    )


def _optional_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)
