"""OpenSim analysis configuration loader.

Reads ``osim_analysis_config.yaml`` and optionally merges defaults from
``marker_metadata.yaml``.  Also provides auto-generation of the
``MeasurementSet`` (scaling marker-pairs) from model marker locations.

Python 3.8 compatible.
"""

from __future__ import annotations

import itertools
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from harmo.analysis.scaling_retry import resolve_scaling_iteration_config


# ---------------------------------------------------------------------------
# YAML helper
# ---------------------------------------------------------------------------

def _load_yaml(path: str) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required.  Install it with: pip install pyyaml"
        ) from exc
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError("Top-level YAML must be a mapping: {0}".format(path))
    return data


# ---------------------------------------------------------------------------
# Default values (fallback when YAML is absent)
# ---------------------------------------------------------------------------

_DEFAULTS: Dict[str, Any] = {
    "workflow": {
        "IK": True,
        "RRA": False,
        "ID": True,
        "MocoInverse_effort": True,
        "MocoInverse_emg": False,
        "AnalyzeTool": True,
    },
    "scaling": {
        "source_policy": {
            "require_static_calibration": False,
            "allow_dynamic_pseudo_static": True,
        },
        "model_scaler": {
            "measurements": {},
        },
        "marker_placer": {
            "marker_weights": {},
        },
        "max_marker_movement": -1,
        "iteration_control": {
            "mode": "once",
            "max_error_threshold": 0.04,
            "max_iterations": 20,
        },
    },
    "IK": {
        "IKTaskSet": {
            "marker_weights": {},
        },
    },
    "RRA": {
        "passes": 2,
        "lowpass_cutoff_frequency": 6.0,
        "adjust_com_to_reduce_residuals": True,
        "adjusted_com_body": "torso",
    },
    "ID": {
        "lowpass_cutoff_frequency_for_coordinate": 6,
    },
    "external_loads": {
        "lowpass_cutoff_frequency_for_load_kinematics": 6,
        "threshold": 20.0,
    },
    "MocoInverse": {
        "kinematics_filter_freq": 6,
        "mesh_interval": 0.02,
        "constraint_tolerance": 1e-3,
        "convergence_tolerance": 1e-3,
        "tracking_weight": 50,
    },
    "marker_relabel": {
        "enabled": False,
        "gt_trc_path": None,
        "rms_threshold": 0.02,
        "max_error_threshold": 0.04,
        "n_sample_frames": 20,
        "vote_threshold": 0.5,
        "segment_groups": None,
    },
    "model_relabel": {
        "enabled": False,
        "max_passes": 3,
        "max_accept_dist": 0.15,
        "vote_threshold": 0.5,
    },
    "grf_side_check": {
        "enabled": False,
        "threshold": 20.0,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


# ---------------------------------------------------------------------------
# Auto-pair: generate MeasurementSet from marker_metadata.yaml
# ---------------------------------------------------------------------------

def _parse_location(loc_str: str) -> Tuple[float, float, float]:
    parts = [float(x.strip()) for x in loc_str.split(",")]
    if len(parts) != 3:
        raise ValueError("location must have 3 components: {0}".format(loc_str))
    return (parts[0], parts[1], parts[2])


def _euclidean(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def generate_measurements_from_marker_metadata(
    marker_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a ``scaling.model_scaler.measurements`` dict auto-generated
    from ``marker_config`` entries.

    For each body that has >= 2 markers the pair with the greatest
    Euclidean distance (in model space) is selected.  This gives a
    reasonable default that the user can later edit.

    Bodies with fewer than 2 usable markers are still included in the
    output with ``marker_pair_set: null``.  This makes it immediately
    visible in the generated YAML which bodies require a manual marker
    pair to be added.

    Returns a dict like::

        {
          "pelvis": {
            "marker_pair_set": [["RASIS", "LASIS"]],
            "body_scale_set": {"pelvis": ["X", "Y", "Z"]}
          },
          "radius_r": {
            "marker_pair_set": null,          # no pair available
            "body_scale_set": {"radius_r": ["X", "Y", "Z"]}
          },
          ...
        }
    """
    marker_config = marker_metadata.get("marker_config", {})
    measurements: Dict[str, Any] = {}

    for body_name, markers in marker_config.items():
        # Bodies with fewer than 2 markers cannot form a pair.
        # They are still recorded with marker_pair_set: null so that the user
        # can clearly see which bodies require manual attention.
        if not isinstance(markers, list) or len(markers) < 2:
            measurements[body_name] = {
                "marker_pair_set": None,
                "body_scale_set": {body_name: ["X", "Y", "Z"]},
            }
            continue

        # Parse locations
        locs: List[Tuple[str, Tuple[float, float, float]]] = []
        for m in markers:
            name = m.get("marker", "")
            loc_str = m.get("location", "")
            if not name or not loc_str:
                continue
            try:
                locs.append((name, _parse_location(loc_str)))
            except ValueError:
                continue

        if len(locs) < 2:
            # Markers exist but locations could not be parsed for >= 2 of them.
            measurements[body_name] = {
                "marker_pair_set": None,
                "body_scale_set": {body_name: ["X", "Y", "Z"]},
            }
            continue

        # Best (max distance) pair.
        # When all markers share the same placeholder location, distances are
        # all zero. In that case we still keep the first valid pair so the
        # body is represented in the generated MeasurementSet.
        best_pair: Optional[Tuple[str, str]] = None
        best_dist = -1.0
        for (n1, c1), (n2, c2) in itertools.combinations(locs, 2):
            d = _euclidean(c1, c2)
            if best_pair is None or d > best_dist:
                best_dist = d
                best_pair = (n1, n2)

        if best_pair is None:
            measurements[body_name] = {
                "marker_pair_set": None,
                "body_scale_set": {body_name: ["X", "Y", "Z"]},
            }
            continue

        measurements[body_name] = {
            "marker_pair_set": [list(best_pair)],
            "body_scale_set": {body_name: ["X", "Y", "Z"]},
        }

    return measurements


# ---------------------------------------------------------------------------
# Merge marker_metadata weights into config
# ---------------------------------------------------------------------------

def _weights_from_marker_metadata(
    marker_metadata: Dict[str, Any],
    weight_key: str,
) -> Dict[str, float]:
    """Extract ``{marker_name: weight}`` from marker_metadata for *weight_key*
    (e.g. ``"scale_weight"`` or ``"ik_weight"``).
    """
    config = marker_metadata.get("marker_config", {})
    weights: Dict[str, float] = {}
    for markers in config.values():
        if not isinstance(markers, list):
            continue
        for m in markers:
            name = m.get("marker", "")
            w = m.get(weight_key)
            if name and w is not None:
                weights[name] = float(w)
    return weights


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_osim_analysis_config(
    config_path: Optional[str] = None,
    marker_metadata_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Load and merge the OpenSim analysis configuration.

    Priority (highest → lowest):
      1. User edits in *config_path* (``osim_analysis_config.yaml``)
      2. Values derived from *marker_metadata_path* (``marker_metadata.yaml``)
      3. Hard-coded ``_DEFAULTS``

    Returns a fully-populated configuration dict.
    """
    # --- 1. Start from defaults ---
    cfg = dict(_DEFAULTS)

    # --- 2. Merge marker_metadata (auto-pair, weights) ---
    if marker_metadata_path and os.path.isfile(marker_metadata_path):
        mm = _load_yaml(marker_metadata_path)

        # Auto-generate measurements
        auto_measurements = generate_measurements_from_marker_metadata(mm)

        # Extract weights
        scale_weights = _weights_from_marker_metadata(mm, "scale_weight")
        ik_weights = _weights_from_marker_metadata(mm, "ik_weight")

        mm_overlay: Dict[str, Any] = {
            "scaling": {
                "model_scaler": {
                    "measurements": auto_measurements,
                },
                "marker_placer": {
                    "marker_weights": scale_weights,
                },
            },
            "IK": {
                "IKTaskSet": {
                    "marker_weights": ik_weights,
                },
            },
        }
        cfg = _deep_merge(cfg, mm_overlay)

    # --- 3. Merge user YAML (highest priority) ---
    if config_path and os.path.isfile(config_path):
        user = _load_yaml(config_path)
        cfg = _deep_merge(cfg, user)

    return cfg


# ---------------------------------------------------------------------------
# Template generation (called after ``harmo ingest``)
# ---------------------------------------------------------------------------

def generate_template_yaml(
    marker_metadata_path: str,
    output_path: str,
) -> str:
    """Create an ``osim_analysis_config.yaml`` pre-populated with values
    derived from *marker_metadata_path*.

    Returns the written file path.
    """
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML is required.") from exc

    mm = _load_yaml(marker_metadata_path)

    measurements = generate_measurements_from_marker_metadata(mm)
    scale_weights = _weights_from_marker_metadata(mm, "scale_weight")
    ik_weights = _weights_from_marker_metadata(mm, "ik_weight")

    template: Dict[str, Any] = {
        "workflow": {
            "IK": True,
            "RRA": False,
            "ID": True,
            "MocoInverse_effort": True,
            "MocoInverse_emg": False,
            "AnalyzeTool": True,
        },
        "scaling": {
            "source_policy": {
                "require_static_calibration": False,
                "allow_dynamic_pseudo_static": True,
            },
            "model_scaler": {
                "measurements": measurements,
            },
            "marker_placer": {
                "marker_weights": scale_weights,
            },
            "max_marker_movement": -1,
            "iteration_control": {
                "mode": "once",
                "max_error_threshold": 0.04,
                "max_iterations": 20,
            },
        },
        "IK": {
            "IKTaskSet": {
                "marker_weights": ik_weights,
            },
        },
        "RRA": {
            "passes": 2,
            "lowpass_cutoff_frequency": 6.0,
            "adjust_com_to_reduce_residuals": True,
            "adjusted_com_body": "torso",
        },
        "ID": {
            "lowpass_cutoff_frequency_for_coordinate": 6,
        },
        "external_loads": {
            "lowpass_cutoff_frequency_for_load_kinematics": 6,
            "threshold": 20.0,
        },
        "MocoInverse": {
            "kinematics_filter_freq": 6,
            "mesh_interval": 0.02,
            "constraint_tolerance": 1e-3,
            "convergence_tolerance": 1e-3,
            "tracking_weight": 50,
        },
        "marker_relabel": {
            "enabled": False,
            "gt_trc_path": None,
            "rms_threshold": 0.02,
            "max_error_threshold": 0.04,
            "n_sample_frames": 20,
            "vote_threshold": 0.5,
        },
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        yaml.dump(template, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return output_path


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

def get_scaling_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("scaling", {})


def get_scaling_iteration_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return normalized scaling iteration policy from config."""
    scaling_cfg = get_scaling_config(cfg)
    return resolve_scaling_iteration_config(scaling_cfg)


def get_ik_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("IK", {})


def get_id_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("ID", {})


def get_external_loads_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("external_loads", {})


def get_moco_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("MocoInverse", {})


def get_relabel_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Return the marker_relabel configuration section."""
    return cfg.get("marker_relabel", _DEFAULTS.get("marker_relabel", {}))


def get_workflow_config(cfg: Dict[str, Any]) -> Dict[str, bool]:
    return cfg.get("workflow", _DEFAULTS["workflow"])


# ---------------------------------------------------------------------------
# Workflow dependency resolution
# ---------------------------------------------------------------------------

# Dependency map: step -> list of steps it depends on.
_WORKFLOW_DEPS: Dict[str, List[str]] = {
    "IK": [],
    "RRA": ["IK"],
    "ID": ["IK"],
    "MocoInverse_effort": ["IK"],
    "MocoInverse_emg": ["MocoInverse_effort"],
    "AnalyzeTool": ["MocoInverse_effort"],
}


def resolve_workflow(cfg: Dict[str, Any]) -> Dict[str, bool]:
    """Return resolved workflow flags with dependency enforcement.

    If a step is disabled, all steps that depend on it are also disabled.
    A warning is printed when a step is implicitly disabled.
    """
    raw = dict(get_workflow_config(cfg))

    # Backward compatibility: migrate old "MocoInverse" key
    if "MocoInverse" in raw and "MocoInverse_effort" not in raw:
        raw["MocoInverse_effort"] = raw.pop("MocoInverse")

    resolved: Dict[str, bool] = {}
    for step in (
        "IK",
        "RRA",
        "ID",
        "MocoInverse_effort",
        "MocoInverse_emg",
        "AnalyzeTool",
    ):
        enabled = raw.get(step, _DEFAULTS["workflow"].get(step, True))
        if enabled:
            # Check that all dependencies are enabled
            for dep in _WORKFLOW_DEPS.get(step, []):
                if not resolved.get(dep, True):
                    print(
                        "[Warning] workflow.{step} は {dep} に依存しますが "
                        "{dep} が無効のため自動的にスキップされます。".format(
                            step=step, dep=dep
                        )
                    )
                    enabled = False
                    break
        resolved[step] = enabled

    return resolved
