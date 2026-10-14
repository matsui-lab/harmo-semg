"""Force plate left/right assignment verification and estimation.

Compare vertical GRF contact timing against foot marker vertical positions
from the corresponding marker data to detect and correct L/R assignment.

This module provides both:
1. Verification: Checking if an existing .mot file has correct L/R labels.
2. Estimation: Determining the side for raw force plate channels before .mot creation.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MOT file I/O
# ---------------------------------------------------------------------------

def _read_mot(mot_path: str) -> Tuple[pd.DataFrame, List[str]]:
    """Read an OpenSim ``.mot`` file, returning the DataFrame and header lines."""
    header_lines = []  # type: List[str]
    header_rows = 0
    with open(mot_path, "r") as f:
        for i, line in enumerate(f):
            header_lines.append(line)
            if "endheader" in line.lower():
                header_rows = i + 1
                break
    df = pd.read_csv(mot_path, sep="\t", skiprows=header_rows)
    df.columns = [c.strip() for c in df.columns]
    return df, header_lines


def _write_mot(df: pd.DataFrame, header_lines: List[str], out_path: str) -> None:
    """Write an OpenSim ``.mot`` file preserving the original header."""
    # Update nRows / nColumns in header
    new_header = []  # type: List[str]
    for line in header_lines:
        if line.strip().startswith("nRows"):
            new_header.append(f"nRows={len(df)}\n")
        elif line.strip().startswith("nColumns"):
            new_header.append(f"nColumns={len(df.columns)}\n")
        else:
            new_header.append(line if line.endswith("\n") else line + "\n")
    with open(out_path, "w") as f:
        f.writelines(new_header)
        f.write("\t".join(df.columns) + "\n")
        np.savetxt(f, df.to_numpy(), fmt="%.6f", delimiter="\t")


# ---------------------------------------------------------------------------
# TRC / Marker reading helpers
# ---------------------------------------------------------------------------

def _read_trc_marker_positions(trc_path: str) -> Tuple[np.ndarray, List[str], np.ndarray]:
    """Read TRC and return ``(times, marker_names, coords[n_frames, n_markers, 3])``."""
    with open(trc_path, "r") as fh:
        lines = fh.readlines()

    raw_markers = lines[3].strip().split("\t")[2:]
    marker_names = [m.strip() for m in raw_markers if m.strip()]

    df = pd.read_csv(trc_path, sep="\t", skiprows=6, header=None)
    times = df.iloc[:, 1].values.astype(float)
    data = df.iloc[:, 2:].values.astype(float)

    n_frames = data.shape[0]
    n_markers = len(marker_names)
    coords = np.full((n_frames, n_markers, 3), np.nan)
    for i in range(n_markers):
        c0 = i * 3
        c1 = c0 + 3
        if c1 <= data.shape[1]:
            coords[:, i, :] = data[:, c0:c1]
    return times, marker_names, coords


def _get_coords_from_df(marker_df: pd.DataFrame) -> Tuple[np.ndarray, List[str], np.ndarray]:
    """Convert a pivoted marker DataFrame to (times, names, coords)."""
    times = marker_df.index.values.astype(float)
    
    # Extract marker names from columns like 'MARKER_x', 'MARKER_y', 'MARKER_z'
    axis_pattern = re.compile(r"(.+?)(_?)([xyz])$", re.IGNORECASE)
    marker_axes: Dict[str, Dict[str, str]] = {}
    for col in marker_df.columns:
        m = axis_pattern.match(str(col))
        if m:
            name, sep, axis = m.groups()
            marker_axes.setdefault(name, {})[axis.lower()] = col
            
    marker_names = sorted(marker_axes.keys())
    n_frames = len(marker_df)
    n_markers = len(marker_names)
    coords = np.full((n_frames, n_markers, 3), np.nan)
    
    for i, name in enumerate(marker_names):
        axes = marker_axes[name]
        for j, ax in enumerate(['x', 'y', 'z']):
            if ax in axes:
                coords[:, i, j] = marker_df[axes[ax]].values
                
    return times, marker_names, coords


# ---------------------------------------------------------------------------
# Model-based foot marker extraction
# ---------------------------------------------------------------------------

_FOOT_BODIES = {"calcn_r": "right", "calcn_l": "left"}


def _get_foot_markers_from_model(model_path: Optional[str]) -> Dict[str, str]:
    """Return ``{marker_name: 'right'|'left'}`` for markers on foot bodies.

    Falls back to hard-coded names if no model is supplied.
    """
    if model_path and os.path.isfile(model_path):
        try:
            import xml.etree.ElementTree as ET

            tree = ET.parse(model_path)
            root = tree.getroot()
            marker_side = {}  # type: Dict[str, str]
            for marker_el in root.iter("Marker"):
                name = marker_el.get("name", "")
                socket_el = marker_el.find("socket_parent_frame")
                body_el = marker_el.find("body")
                body_name = None  # type: Optional[str]
                if socket_el is not None and socket_el.text:
                    body_name = socket_el.text.strip().split("/")[-1]
                elif body_el is not None and body_el.text:
                    body_name = body_el.text.strip()
                if body_name and body_name in _FOOT_BODIES:
                    marker_side[name] = _FOOT_BODIES[body_name]
            if marker_side:
                return marker_side
        except Exception as exc:
            logger.warning("Failed to parse model for foot markers: %s", exc)

    # Fallback: conventional heel/toe names
    return {
        "RHEE": "right", "RTOE": "right", "RASI": "right", "RPSI": "right",
        "LHEE": "left", "LTOE": "left", "LASI": "left", "LPSI": "left",
        # study 28 variations
        "RHE": "right", "RTO": "right",
        "LHE": "left", "LTO": "left",
    }


# ---------------------------------------------------------------------------
# Core verification and estimation logic
# ---------------------------------------------------------------------------

def _find_contact_intervals(
    fy: np.ndarray,
    times: np.ndarray,
    threshold: float = 20.0,
) -> List[Tuple[float, float]]:
    """Return list of ``(t_start, t_end)`` for contiguous Fy > threshold."""
    above = np.abs(fy) > threshold
    intervals = []  # type: List[Tuple[float, float]]
    in_contact = False
    start_idx = 0
    for i, val in enumerate(above):
        if val and not in_contact:
            in_contact = True
            start_idx = i
        elif not val and in_contact:
            in_contact = False
            intervals.append((times[start_idx], times[i - 1]))
    if in_contact:
        intervals.append((times[start_idx], times[-1]))
    return intervals


def _mean_marker_height(
    marker_coords: np.ndarray,
    marker_times: np.ndarray,
    t_start: float,
    t_end: float,
    y_idx: int = 1,
) -> float:
    """Mean vertical position of a marker during ``[t_start, t_end]``."""
    mask = (marker_times >= t_start) & (marker_times <= t_end)
    if not np.any(mask):
        return np.nan
    vals = marker_coords[mask, y_idx]
    valid = vals[~np.isnan(vals)]
    return float(np.mean(valid)) if len(valid) > 0 else np.nan


def _avg_foot_height(
    idxs: List[int], 
    coords: np.ndarray, 
    trc_times: np.ndarray, 
    t_start: float, 
    t_end: float, 
    y_idx: int
) -> float:
    heights: List[float] = []
    for idx in idxs:
        h = _mean_marker_height(coords[:, idx, :], trc_times, t_start, t_end, y_idx)
        if not np.isnan(h):
            heights.append(h)
    return float(np.mean(heights)) if heights else np.nan


def detect_side_for_fz_series(
    fz: np.ndarray,
    fz_times: np.ndarray,
    marker_times: np.ndarray,
    marker_names: List[str],
    marker_coords: np.ndarray,
    foot_markers: Dict[str, str],
    threshold: float = 20.0,
    y_idx: int = 1,
) -> Dict[str, Any]:
    """Detect the stance side for a single vertical force time series."""
    intervals = _find_contact_intervals(fz, fz_times, threshold)
    
    right_marker_idxs = [i for i, m in enumerate(marker_names) if foot_markers.get(m) == "right"]
    left_marker_idxs = [i for i, m in enumerate(marker_names) if foot_markers.get(m) == "left"]
    
    if not right_marker_idxs or not left_marker_idxs:
        return {"side": "unknown", "reason": "Missing foot markers in TRC"}

    r_votes = 0
    l_votes = 0
    details = []
    
    for t_start, t_end in intervals:
        r_height = _avg_foot_height(right_marker_idxs, marker_coords, marker_times, t_start, t_end, y_idx)
        l_height = _avg_foot_height(left_marker_idxs, marker_coords, marker_times, t_start, t_end, y_idx)
        
        if np.isnan(r_height) or np.isnan(l_height):
            continue
            
        # Lower height = on the ground = stance side
        if r_height < l_height:
            r_votes += 1
            side = "right"
        else:
            l_votes += 1
            side = "left"
            
        details.append({
            "interval": (t_start, t_end),
            "r_foot_height": r_height,
            "l_foot_height": l_height,
            "detected_side": side
        })
        
    if r_votes > l_votes:
        return {"side": "right", "r_votes": r_votes, "l_votes": l_votes, "details": details}
    elif l_votes > r_votes:
        return {"side": "left", "r_votes": r_votes, "l_votes": l_votes, "details": details}
    else:
        return {"side": "unknown", "r_votes": r_votes, "l_votes": l_votes, "details": details}


# ---------------------------------------------------------------------------
# High-level API for verification (existing .mot)
# ---------------------------------------------------------------------------

def detect_grf_side(
    mot_path: str,
    trc_path: str,
    *,
    model_path: Optional[str] = None,
    threshold: float = 20.0,
    y_idx: int = 1,
) -> Dict:
    """Detect the actual L/R assignment of force plate data in an existing MOT."""
    mot_df, _ = _read_mot(mot_path)
    time_col = next((c for c in mot_df.columns if c.lower() == "time"), mot_df.columns[0])
    mot_times = mot_df[time_col].values

    col_r_vy = next((c for c in mot_df.columns if "force_r_vy" in c), None)
    col_l_vy = next((c for c in mot_df.columns if "force_l_vy" in c), None)
    if col_r_vy is None or col_l_vy is None:
        raise ValueError(f"Cannot find vertical force columns in {mot_path}")

    trc_times, marker_names, coords = _read_trc_marker_positions(trc_path)
    foot_markers = _get_foot_markers_from_model(model_path)

    # Check 'right' columns
    r_check = detect_side_for_fz_series(
        mot_df[col_r_vy].values, mot_times,
        trc_times, marker_names, coords,
        foot_markers, threshold, y_idx
    )
    
    # Check 'left' columns
    l_check = detect_side_for_fz_series(
        mot_df[col_l_vy].values, mot_times,
        trc_times, marker_names, coords,
        foot_markers, threshold, y_idx
    )

    swap_votes = 0
    keep_votes = 0
    
    if r_check["side"] == "left": swap_votes += 1
    elif r_check["side"] == "right": keep_votes += 1
    
    if l_check["side"] == "right": swap_votes += 1
    elif l_check["side"] == "left": keep_votes += 1
    
    needs_swap = swap_votes > keep_votes
    
    return {
        "needs_swap": needs_swap,
        "swap_votes": swap_votes,
        "keep_votes": keep_votes,
        "details": r_check.get("details", []) + l_check.get("details", []),
        "right_intervals": _find_contact_intervals(mot_df[col_r_vy].values, mot_times, threshold),
        "left_intervals": _find_contact_intervals(mot_df[col_l_vy].values, mot_times, threshold),
    }


# ---------------------------------------------------------------------------
# High-level API for Estimation (pre-MOT)
# ---------------------------------------------------------------------------

def estimate_side_for_all_plates(
    force_data: pd.DataFrame,
    marker_data: pd.DataFrame,
    metadata: pd.DataFrame,
    *,
    model_path: Optional[str] = None,
    threshold: float = 20.0,
    vertical_axis: str = 'z',
    marker_y_idx: int = 1,
) -> pd.DataFrame:
    """Estimate 'side' for each plate in metadata based on marker motion.

    Parameters
    ----------
    force_data : pd.DataFrame
        Pivoted force plate data (index=time, columns=channel).
    marker_data : pd.DataFrame
        Pivoted marker data (index=time, columns=marker_axis).
    metadata : pd.DataFrame
        Dimension table rows for Force-plate sensor.
        Must have ``column_name``, ``physical_quantity``, and optionally ``side``.
    vertical_axis : str
        The axis character ('x', 'y', 'z') that represents vertical force in raw channels.
    marker_y_idx : int
        The index of the vertical axis in marker coords (usually 1 for Y).
    """
    updated_meta = metadata.copy()
    if 'side' not in updated_meta.columns:
        updated_meta['side'] = np.nan
        
    # Standardize 'side' values
    def _clean_side(s):
        if pd.isna(s): return None
        s = str(s).lower().strip()
        if s in ('left', 'right'): return s
        return None
    
    updated_meta['side'] = updated_meta['side'].apply(_clean_side)

    # Pre-process marker data
    m_times, m_names, m_coords = _get_coords_from_df(marker_data)
    foot_markers = _get_foot_markers_from_model(model_path)
    
    f_times = force_data.index.values.astype(float)
    
    # Identify unique plate IDs from column names (e.g. Force.Fz1 -> '1')
    plate_digit_re = re.compile(r"(\d+)")
    
    def _get_plate_id(col):
        digits = plate_digit_re.findall(str(col))
        return digits[-1] if digits else "0"

    # Identify vertical force channels for each plate
    fz_channels = {} # plate_id -> channel_name
    PQ_FORCE = {"grf", "force", "forces", "ground reaction force"}
    
    # Matches 'Fx', 'Fx1', 'Force.Fx1', etc. 
    # Capture the axis char and optional trailing digits
    axis_digit_pattern = re.compile(r"([xyz])(\d*)$", re.IGNORECASE)
    
    target_axis = vertical_axis.lower()
    for _, row in updated_meta.iterrows():
        col = str(row['column_name'])
        pq = str(row.get('physical_quantity', '')).lower().strip()
        if pq in PQ_FORCE:
            match = axis_digit_pattern.search(col)
            if match:
                axis_char = match.group(1).lower()
                if axis_char == target_axis:
                    pid = _get_plate_id(col)
                    fz_channels[pid] = col

    # Estimate side for each identified plate
    plate_sides = {} # plate_id -> 'Left' | 'Right'
    for pid, col in fz_channels.items():
        if col not in force_data.columns:
            continue
            
        fz_series = force_data[col].values
        result = detect_side_for_fz_series(
            fz_series, f_times,
            m_times, m_names, m_coords,
            foot_markers, threshold, marker_y_idx
        )
        
        if result['side'] != 'unknown':
            plate_sides[pid] = result['side'].capitalize()
            logger.info(f"Estimated side for Plate {pid} ({col}): {plate_sides[pid]}")
        else:
            logger.warning(f"Could not estimate side for Plate {pid} ({col})")

    # Update metadata with estimated sides
    for idx, row in updated_meta.iterrows():
        pid = _get_plate_id(row['column_name'])
        if pid in plate_sides:
            # Only update if current side is unknown/NaN
            if pd.isna(row['side']) or row['side'] is None:
                updated_meta.at[idx, 'side'] = plate_sides[pid]

    return updated_meta


# ---------------------------------------------------------------------------
# MOT column swap (Existing logic)
# ---------------------------------------------------------------------------

_SIDE_PATTERN = re.compile(r"(ground_(?:force|torque))_([rl])_(.+)")


def swap_mot_lr(mot_path: str, output_path: str) -> str:
    """Swap all ``_r_``/``_l_`` columns in a MOT file and write to *output_path*.

    Returns the output path.
    """
    df, header = _read_mot(mot_path)
    new_cols = {}  # type: Dict[str, str]
    for col in df.columns:
        m = _SIDE_PATTERN.match(col)
        if m:
            prefix, side, suffix = m.groups()
            swapped_side = "l" if side == "r" else "r"
            new_cols[col] = f"{prefix}_{swapped_side}_{suffix}"

    # Rename by swapping through temporary names to avoid collisions
    tmp_map = {old: f"__tmp_{i}__" for i, old in enumerate(new_cols)}
    final_map = {f"__tmp_{i}__": new_cols[old] for i, old in enumerate(new_cols)}
    df = df.rename(columns=tmp_map).rename(columns=final_map)

    # Reorder columns to standard order
    time_col = next((c for c in df.columns if c.lower() == "time"), df.columns[0])
    ordered = [
        "ground_force_r_vx", "ground_force_r_vy", "ground_force_r_vz",
        "ground_force_r_px", "ground_force_r_py", "ground_force_r_pz",
        "ground_torque_r_x", "ground_torque_r_y", "ground_torque_r_z",
        "ground_force_l_vx", "ground_force_l_vy", "ground_force_l_vz",
        "ground_force_l_px", "ground_force_l_py", "ground_force_l_pz",
        "ground_torque_l_x", "ground_torque_l_y", "ground_torque_l_z",
    ]
    final_cols = [time_col] + [c for c in ordered if c in df.columns]
    # add any remaining columns not in ordered list
    remaining = [c for c in df.columns if c not in final_cols]
    final_cols.extend(remaining)
    df = df[final_cols]

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    _write_mot(df, header, output_path)
    return output_path


# ---------------------------------------------------------------------------
# High-level pipeline entry point (Verification)
# ---------------------------------------------------------------------------

def verify_and_fix_grf(
    mot_path: str,
    trc_path: str,
    output_mot_path: Optional[str] = None,
    *,
    model_path: Optional[str] = None,
    threshold: float = 20.0,
    y_idx: int = 1,
) -> Dict:
    """Verify GRF L/R assignment and fix if necessary."""
    result = detect_grf_side(
        mot_path, trc_path,
        model_path=model_path,
        threshold=threshold,
        y_idx=y_idx,
    )

    if output_mot_path is None:
        output_mot_path = mot_path

    if result["needs_swap"]:
        swap_mot_lr(mot_path, output_mot_path)
        logger.warning(
            "GRF L/R swap detected (swap_votes=%d, keep_votes=%d). "
            "Corrected MOT written to %s",
            result["swap_votes"], result["keep_votes"], output_mot_path,
        )
        status = "swapped"
    else:
        # If output differs from input, copy as-is
        if os.path.abspath(output_mot_path) != os.path.abspath(mot_path):
            import shutil
            shutil.copy2(mot_path, output_mot_path)
        status = "correct"
        logger.info(
            "GRF L/R assignment correct (keep_votes=%d, swap_votes=%d).",
            result["keep_votes"], result["swap_votes"],
        )

    return {
        "status": status,
        "needs_swap": result["needs_swap"],
        "output_mot": output_mot_path,
        "swap_votes": result["swap_votes"],
        "keep_votes": result["keep_votes"],
        "details": result["details"],
    }
