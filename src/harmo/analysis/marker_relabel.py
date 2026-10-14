"""Marker relabeling via topology (inter-marker distance matrix) matching.

This module provides the core algorithm for automatically correcting
mislabelled markers in an OpenSim ``.trc`` file by comparing its
inter-marker distance *topology* against a known-good Ground Truth (GT)
session.

Algorithm overview
------------------
1. Parse both GT and target ``.trc`` files into 3-D point clouds.
2. Compute per-segment intra-marker distance matrices (topology).
3. Use SVD-based Procrustes alignment to normalise body scale.
4. Solve the optimal label permutation via the Hungarian algorithm
   applied to a distance-matrix similarity cost.
5. Vote across multiple frames for robustness against dynamic poses.
6. Return the relabeling map ``{current_label: correct_label}``.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.spatial import procrustes as scipy_procrustes


# ---------------------------------------------------------------------------
# Segment definitions – markers grouped by rigid body
# ---------------------------------------------------------------------------
# These groups constrain the search space: labels are only reassigned
# *within the same segment* so that anatomically impossible relabeling
# (e.g. left knee → right ankle) is excluded by construction.
#
# This can be overridden via config.
DEFAULT_SEGMENT_GROUPS: Dict[str, List[str]] = {
    "pelvis": ["RASIS", "LASIS", "RPSIS", "LPSIS"],
    "L_thigh": ["LTROC", "LTH1", "LTH2", "LTH3"],
    "R_thigh": ["RTROC", "RTH1", "RTH2", "RTH3"],
    "L_shank": ["LLK", "LMK", "LSK1", "LSK2", "LSK3"],
    "R_shank": ["RLK", "RMK", "RSK1", "RSK2", "RSK3"],
    "L_foot": ["LLA", "LMA", "LFM1", "LFM2", "LFM5"],
    "R_foot": ["RLA", "RMA", "RFM1", "RFM2", "RFM5"],
    "torso": ["C7", "T10", "CLAV", "STRN", "RBAK"],
    "L_upper": ["LSHO", "LELB", "LWRA", "LWRB"],
    "R_upper": ["RSHO", "RELB", "RWRA", "RWRB"],
}

# ---------------------------------------------------------------------------
# TRC I/O helpers
# ---------------------------------------------------------------------------

def read_trc_to_frames(trc_path: str) -> Tuple[List[str], np.ndarray]:
    """Read a ``.trc`` file and return marker names and coordinate array.

    Returns
    -------
    marker_names : list[str]
        Ordered marker label list.
    coords : np.ndarray, shape (n_frames, n_markers, 3)
        Marker coordinates.  NaN where the marker is not visible.
    """
    with open(trc_path, "r") as fh:
        lines = fh.readlines()

    # Line 4 (index 3) contains marker names (tab separated)
    raw_markers = lines[3].strip().split("\t")[2:]
    marker_names = [m.strip() for m in raw_markers if m.strip()]

    # Data starts at line 7 (index 6)
    df = pd.read_csv(trc_path, sep="\t", skiprows=6, header=None)
    data = df.iloc[:, 2:].values.astype(float)

    n_frames = data.shape[0]
    n_markers = len(marker_names)
    coords = np.full((n_frames, n_markers, 3), np.nan)
    for i in range(n_markers):
        col_start = i * 3
        col_end = col_start + 3
        if col_end <= data.shape[1]:
            coords[:, i, :] = data[:, col_start:col_end]

    return marker_names, coords


def write_relabelled_trc(
    source_trc_path: str,
    output_trc_path: str,
    rename_map: Dict[str, str],
) -> str:
    """Rewrite *source_trc_path* with marker labels replaced per *rename_map*.

    Only header line 4 (marker names) is modified; coordinate data is
    unchanged.

    Returns the output path.
    """
    with open(source_trc_path, "r") as fh:
        lines = fh.readlines()

    # Parse and replace marker names in header line 4 (index 3)
    parts = lines[3].split("\t")
    new_parts = []
    for p in parts:
        stripped = p.strip()
        if stripped in rename_map:
            new_parts.append(rename_map[stripped])
        else:
            new_parts.append(p)
    lines[3] = "\t".join(new_parts)
    if not lines[3].endswith("\n"):
        lines[3] += "\n"

    os.makedirs(os.path.dirname(output_trc_path) or ".", exist_ok=True)
    with open(output_trc_path, "w") as fh:
        fh.writelines(lines)

    return output_trc_path


def write_trc_without_markers(
    source_trc_path: str,
    output_trc_path: str,
    markers_to_remove: List[str],
) -> str:
    """Rewrite *source_trc_path* with specified markers removed entirely.

    Handles the TRC format where each marker occupies 3 tab-separated
    columns in both the marker-name header (line 4) and coordinate-name
    header (line 5), plus 3 data columns per marker.

    Returns the output path.
    """
    with open(source_trc_path, "r") as fh:
        lines = fh.readlines()

    # Parse marker names from header line 4 (index 3).
    # Format: Frame#\tTime\tM1\t\t\tM2\t\t\t...
    parts4 = lines[3].rstrip("\n").split("\t")
    # Every marker occupies 3 tab-cells starting at col 2
    marker_col_starts = []
    raw_markers = []
    col = 2
    while col < len(parts4):
        name = parts4[col].strip()
        if name:
            raw_markers.append(name)
            marker_col_starts.append(col)
        col += 3  # each marker spans 3 columns

    remove_set = set(markers_to_remove)
    keep_indices = [i for i, m in enumerate(raw_markers) if m not in remove_set]

    if len(keep_indices) == len(raw_markers):
        os.makedirs(os.path.dirname(output_trc_path) or ".", exist_ok=True)
        import shutil
        shutil.copy2(source_trc_path, output_trc_path)
        return output_trc_path

    kept_names = [raw_markers[i] for i in keep_indices]

    # Rebuild header line 4: Frame#\tTime\tM1\t\t\tM2\t\t\t...
    new_parts4 = [parts4[0], parts4[1]]
    for name in kept_names:
        new_parts4.extend([name, "", ""])
    new_line4 = "\t".join(new_parts4) + "\n"

    # Rebuild header line 5: \t\tX1\tY1\tZ1\tX2\tY2\tZ2\t...
    parts5_prefix = lines[4].rstrip("\n").split("\t")[:2]
    coord_cols = []
    for seq_i in range(len(kept_names)):
        num = seq_i + 1
        coord_cols.extend([f"X{num}", f"Y{num}", f"Z{num}"])
    new_line5 = "\t".join(parts5_prefix + coord_cols) + "\n"

    # Update NumMarkers in line 3
    line3_parts = lines[2].rstrip("\n").split("\t")
    for ci, cell in enumerate(line3_parts):
        try:
            val = int(cell.strip())
            if val == len(raw_markers):
                line3_parts[ci] = str(len(kept_names))
                break
        except ValueError:
            continue
    new_line3 = "\t".join(line3_parts) + "\n"

    # Compute which data columns to keep (0-indexed)
    # Columns 0=Frame#, 1=Time, then each marker is 3 consecutive columns
    keep_data_cols: List[int] = [0, 1]
    for i in keep_indices:
        base = 2 + i * 3
        keep_data_cols.extend([base, base + 1, base + 2])
    keep_data_set = set(keep_data_cols)

    # Determine where data rows start.
    # Standard TRC has a blank separator at line 6 (index 5); some TRC files
    # produced by OpenSim skip it and start data immediately at index 5.
    data_start = 5
    if len(lines) > 5 and lines[5].strip() == "":
        data_start = 6  # blank separator present

    new_data_lines = []
    sorted_keep = sorted(keep_data_set)
    for line in lines[data_start:]:
        parts = line.rstrip("\n").split("\t")
        kept = [parts[j] for j in sorted_keep if j < len(parts)]
        new_data_lines.append("\t".join(kept) + "\n")

    # Assemble output
    out_lines = list(lines[:2]) + [new_line3, new_line4, new_line5]
    if data_start == 6:
        out_lines.append(lines[5])  # preserve blank separator
    out_lines.extend(new_data_lines)

    os.makedirs(os.path.dirname(output_trc_path) or ".", exist_ok=True)
    with open(output_trc_path, "w") as fh:
        fh.writelines(out_lines)

    return output_trc_path


# ---------------------------------------------------------------------------
# Distance matrix (Topology) computation
# ---------------------------------------------------------------------------

def _pairwise_distances(points: np.ndarray) -> np.ndarray:
    """Euclidean distance matrix for an (N, 3) array.  NaN-safe."""
    n = points.shape[0]
    D = np.full((n, n), np.nan)
    for i in range(n):
        for j in range(i, n):
            diff = points[i] - points[j]
            if np.any(np.isnan(diff)):
                continue
            d = np.sqrt(np.sum(diff ** 2))
            D[i, j] = d
            D[j, i] = d
    return D


def compute_topology(
    coords_frame: np.ndarray,
    marker_names: List[str],
    segment_markers: List[str],
) -> Tuple[List[str], np.ndarray]:
    """Compute intra-segment distance matrix for one frame.

    Parameters
    ----------
    coords_frame : (n_markers, 3)
    marker_names  : full marker list
    segment_markers : markers belonging to one rigid segment

    Returns
    -------
    present_names : markers actually present (non-NaN) in this frame
    D : distance matrix among *present_names*
    """
    idxs = []
    present = []
    for m in segment_markers:
        if m in marker_names:
            idx = marker_names.index(m)
            pt = coords_frame[idx]
            if not np.any(np.isnan(pt)):
                idxs.append(idx)
                present.append(m)

    if len(present) < 2:
        return present, np.array([])

    pts = coords_frame[idxs]
    return present, _pairwise_distances(pts)


# ---------------------------------------------------------------------------
# Procrustes (SVD) alignment for scale normalisation
# ---------------------------------------------------------------------------

def procrustes_align(
    source: np.ndarray,
    target: np.ndarray,
) -> Tuple[np.ndarray, float]:
    """Align *source* point cloud to *target* using SVD-based Procrustes.

    Both arrays are (N, 3).  Points with NaN in either are excluded.

    Returns
    -------
    aligned : (N, 3) – transformed source
    scale   : float  – scale factor applied
    """
    mask = ~(np.any(np.isnan(source), axis=1) | np.any(np.isnan(target), axis=1))
    n_valid = int(mask.sum())
    if n_valid < 3:
        return source.copy(), 1.0

    src = source[mask].copy()
    tgt = target[mask].copy()

    # Centre
    src_mean = src.mean(axis=0)
    tgt_mean = tgt.mean(axis=0)
    src_c = src - src_mean
    tgt_c = tgt - tgt_mean

    # Scale
    src_norm = np.sqrt(np.sum(src_c ** 2))
    tgt_norm = np.sqrt(np.sum(tgt_c ** 2))
    if src_norm < 1e-12 or tgt_norm < 1e-12:
        return source.copy(), 1.0
    scale = tgt_norm / src_norm
    src_c *= scale

    # Rotation (SVD)
    H = src_c.T @ tgt_c
    U, _, Vt = np.linalg.svd(H)
    d = np.linalg.det(Vt.T @ U.T)
    S = np.eye(3)
    if d < 0:
        S[2, 2] = -1
    R = Vt.T @ S @ U.T

    # Apply to full source
    aligned = (source - src_mean) * scale @ R.T + tgt_mean
    return aligned, scale


# ---------------------------------------------------------------------------
# Topology-based label matching (core algorithm)
# ---------------------------------------------------------------------------

def _topology_cost_matrix(
    gt_D: np.ndarray,
    tgt_D: np.ndarray,
) -> np.ndarray:
    """Build assignment cost from two distance matrices.

    For each GT marker *i* and target marker *j*, the cost is the
    L1 distance between their **sorted** distance profiles (excluding
    self-distance).  Sorting makes the comparison invariant to label
    permutation within the distance matrix itself.

    Both D matrices should be the same size (n, n).
    """
    n = gt_D.shape[0]
    cost = np.zeros((n, n))

    for i in range(n):
        # Sorted profile for GT marker i (exclude self = 0 diagonal)
        row_gt = np.sort(gt_D[i][np.arange(n) != i])
        mask_gt = ~np.isnan(row_gt)

        for j in range(n):
            row_tgt = np.sort(tgt_D[j][np.arange(n) != j])
            mask_tgt = ~np.isnan(row_tgt)

            # Use the intersection of valid entries
            valid = mask_gt & mask_tgt
            if valid.sum() == 0:
                cost[i, j] = 1e6
            else:
                cost[i, j] = np.mean(np.abs(row_gt[valid] - row_tgt[valid]))
    return cost


def match_segment_labels_single_frame(
    gt_names: List[str],
    gt_coords: np.ndarray,
    tgt_names: List[str],
    tgt_coords: np.ndarray,
    segment_markers: List[str],
    gt_marker_list: List[str],
    tgt_marker_list: List[str],
) -> Dict[str, str]:
    """Match target labels to GT labels for one segment in one frame.

    Uses Procrustes-aligned position matching as the primary cost,
    with topology (distance matrix) similarity as a secondary penalty.
    This hybrid approach handles symmetric configurations (e.g. left/right
    pelvis markers) where pure topology matching would be ambiguous.

    Returns ``{current_target_label: correct_gt_label}`` only for
    markers that are reassigned (identity mappings are excluded).
    """
    # Find markers present in both GT and target for this segment
    gt_present = []
    gt_pts = []
    for m in segment_markers:
        if m in gt_marker_list:
            idx = gt_marker_list.index(m)
            pt = gt_coords[idx]
            if not np.any(np.isnan(pt)):
                gt_present.append(m)
                gt_pts.append(pt)

    tgt_present = []
    tgt_pts = []
    for m in segment_markers:
        if m in tgt_marker_list:
            idx = tgt_marker_list.index(m)
            pt = tgt_coords[idx]
            if not np.any(np.isnan(pt)):
                tgt_present.append(m)
                tgt_pts.append(pt)

    if len(gt_present) < 2 or len(tgt_present) < 2:
        return {}

    # Work with common markers only
    common = sorted(set(gt_present) & set(tgt_present))
    if len(common) < 2:
        return {}

    gt_idx = [gt_present.index(m) for m in common]
    tgt_idx = [tgt_present.index(m) for m in common]

    gt_common_pts = np.array(gt_pts)[gt_idx]
    tgt_common_pts = np.array(tgt_pts)[tgt_idx]

    # Procrustes alignment: align target point cloud to GT
    aligned_tgt, _ = procrustes_align(tgt_common_pts, gt_common_pts)

    n = len(common)

    # Primary cost: Euclidean distance between aligned positions
    pos_cost = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            pos_cost[i, j] = np.linalg.norm(gt_common_pts[i] - aligned_tgt[j])

    # Secondary cost: topology similarity (sorted distance profiles)
    gt_D = _pairwise_distances(gt_common_pts)
    tgt_D = _pairwise_distances(aligned_tgt)
    topo_cost = _topology_cost_matrix(gt_D, tgt_D)

    # Normalise both costs to [0, 1] range before combining
    pos_max = pos_cost.max() if pos_cost.max() > 0 else 1.0
    topo_max = topo_cost.max() if topo_cost.max() > 0 else 1.0

    # Position is primary (weight 0.7), topology is secondary (weight 0.3)
    cost = 0.7 * (pos_cost / pos_max) + 0.3 * (topo_cost / topo_max)

    row_ind, col_ind = linear_sum_assignment(cost)

    rename = {}
    for r, c in zip(row_ind, col_ind):
        gt_label = common[r]
        tgt_label = common[c]
        if gt_label != tgt_label:
            rename[tgt_label] = gt_label
    return rename


# ---------------------------------------------------------------------------
# Multi-frame voting
# ---------------------------------------------------------------------------

def compute_relabel_map(
    gt_trc_path: str,
    target_trc_path: str,
    segment_groups: Optional[Dict[str, List[str]]] = None,
    n_sample_frames: int = 20,
    vote_threshold: float = 0.5,
) -> Dict[str, str]:
    """Compute a robust relabeling map using multi-frame majority voting.

    Parameters
    ----------
    gt_trc_path : str
        Path to the ground truth ``.trc`` file.
    target_trc_path : str
        Path to the target (mislabelled) ``.trc`` file.
    segment_groups : dict, optional
        ``{segment_name: [marker_names]}``.  Defaults to
        :data:`DEFAULT_SEGMENT_GROUPS`.
    n_sample_frames : int
        Number of frames to sample for voting.
    vote_threshold : float
        Minimum fraction of voted frames that must agree for a relabeling
        to be accepted.

    Returns
    -------
    rename_map : dict
        ``{current_target_label: correct_gt_label}``.
        Only contains entries where the label actually changes.
    """
    if segment_groups is None:
        segment_groups = DEFAULT_SEGMENT_GROUPS

    gt_names, gt_coords = read_trc_to_frames(gt_trc_path)
    tgt_names, tgt_coords = read_trc_to_frames(target_trc_path)

    n_frames = min(gt_coords.shape[0], tgt_coords.shape[0])
    if n_frames == 0:
        return {}

    # Sample frames evenly
    if n_frames <= n_sample_frames:
        frame_indices = list(range(n_frames))
    else:
        frame_indices = np.linspace(0, n_frames - 1, n_sample_frames, dtype=int).tolist()

    # Collect votes: key = (current_label, proposed_label), value = count
    votes: Dict[Tuple[str, str], int] = Counter()
    n_voted_frames = 0

    for fi in frame_indices:
        frame_rename: Dict[str, str] = {}
        for seg_name, seg_markers in segment_groups.items():
            seg_rename = match_segment_labels_single_frame(
                gt_names=gt_names,
                gt_coords=gt_coords[fi],
                tgt_names=tgt_names,
                tgt_coords=tgt_coords[fi],
                segment_markers=seg_markers,
                gt_marker_list=gt_names,
                tgt_marker_list=tgt_names,
            )
            frame_rename.update(seg_rename)

        if frame_rename:
            for pair in frame_rename.items():
                votes[pair] += 1
        n_voted_frames += 1

    if n_voted_frames == 0:
        return {}

    # Accept relabeling only if it wins the majority
    min_count = max(1, int(n_voted_frames * vote_threshold))
    rename_map: Dict[str, str] = {}
    for (current, proposed), count in votes.items():
        if count >= min_count:
            # Check for conflicts (same target assigned to multiple sources)
            existing = rename_map.get(current)
            if existing is None:
                rename_map[current] = proposed
            elif count > votes.get((current, existing), 0):
                rename_map[current] = proposed

    # Remove transitive conflicts (A→B and B→A should both be applied)
    # Ensure bijective mapping
    final_map: Dict[str, str] = {}
    used_targets: set = set()
    for current, proposed in sorted(rename_map.items(),
                                     key=lambda x: votes.get(x, 0),
                                     reverse=True):
        if proposed not in used_targets:
            final_map[current] = proposed
            used_targets.add(proposed)

    return final_map


# ---------------------------------------------------------------------------
# Full-cloud Procrustes matching (fallback for badly corrupted labels)
# ---------------------------------------------------------------------------

def match_by_procrustes_nearest(
    gt_trc_path: str,
    target_trc_path: str,
    n_sample_frames: int = 10,
    orig_dist_thresh: float = 1.5,
    ratio_thresh: float = 0.30,
) -> Dict[str, str]:
    """Global cross-segment matcher using pelvis-frame alignment.

    Aligns target and GT point clouds using reliable pelvis landmarks
    (RASIS, LASIS) as anchors.  For each target marker, compares its
    distance to the GT marker of the same name (``orig_d``) with its
    distance to the nearest GT marker (``nearest_d``).  A relabeling
    is proposed only when:

    * ``orig_d > orig_dist_thresh`` (in pelvis-width units)
    * ``nearest_d / orig_d < ratio_thresh``

    This ensures only markers that are **grossly** displaced from their
    labelled position are relabelled, avoiding false positives from
    normal body-proportion differences between subjects.
    """
    from scipy.spatial.distance import cdist as _cdist

    gt_names, gt_coords = read_trc_to_frames(gt_trc_path)
    tgt_names, tgt_coords = read_trc_to_frames(target_trc_path)

    n_frames = min(gt_coords.shape[0], tgt_coords.shape[0])
    if n_frames == 0:
        return {}

    frame_indices = np.linspace(
        0, n_frames - 1, min(n_sample_frames, n_frames), dtype=int,
    ).tolist()

    # Pelvis anchor markers (must be correctly labelled in both files)
    _ANCHOR_NAMES = ["RASIS", "LASIS"]

    def _anchor_indices(names):
        idxs = []
        for a in _ANCHOR_NAMES:
            if a in names:
                idxs.append(names.index(a))
        return idxs

    gt_anc_idx = _anchor_indices(gt_names)
    tgt_anc_idx = _anchor_indices(tgt_names)
    if len(gt_anc_idx) < 2 or len(tgt_anc_idx) < 2:
        return {}

    gt_name_set = set(gt_names)
    votes: Counter = Counter()

    for fi in frame_indices:
        gt_frame = gt_coords[fi]
        tgt_frame = tgt_coords[fi]

        # Anchors
        gt_anc_pts = gt_frame[gt_anc_idx]
        tgt_anc_pts = tgt_frame[tgt_anc_idx]
        if np.any(np.isnan(gt_anc_pts)) or np.any(np.isnan(tgt_anc_pts)):
            continue

        # Pelvis centre and width
        gt_centre = gt_anc_pts.mean(axis=0)
        tgt_centre = tgt_anc_pts.mean(axis=0)
        gt_pelvis_w = np.linalg.norm(gt_anc_pts[0] - gt_anc_pts[1])
        tgt_pelvis_w = np.linalg.norm(tgt_anc_pts[0] - tgt_anc_pts[1])
        if gt_pelvis_w < 1e-6 or tgt_pelvis_w < 1e-6:
            continue

        # Valid markers
        gt_valid = [
            (gt_names[i], (gt_frame[i] - gt_centre) / gt_pelvis_w)
            for i in range(len(gt_names))
            if not np.any(np.isnan(gt_frame[i]))
        ]
        tgt_valid = [
            (tgt_names[i], (tgt_frame[i] - tgt_centre) / tgt_pelvis_w)
            for i in range(len(tgt_names))
            if not np.any(np.isnan(tgt_frame[i]))
        ]

        if len(gt_valid) < 4 or len(tgt_valid) < 4:
            continue

        gt_pts = np.array([v[1] for v in gt_valid])
        tgt_pts = np.array([v[1] for v in tgt_valid])
        gt_label_list = [v[0] for v in gt_valid]

        # Distance from each target marker to every GT marker
        D = _cdist(tgt_pts, gt_pts)

        for ti in range(len(tgt_valid)):
            tgt_label = tgt_valid[ti][0]
            drow = D[ti]

            nearest_gi = int(np.argmin(drow))
            nearest_name = gt_label_list[nearest_gi]
            nearest_d = drow[nearest_gi]

            if tgt_label == nearest_name:
                continue  # already correct

            # Distance to same-name GT marker
            if tgt_label in gt_label_list:
                orig_gi = gt_label_list.index(tgt_label)
                orig_d = drow[orig_gi]
            else:
                orig_d = float("inf")

            ratio = nearest_d / orig_d if orig_d > 1e-6 else 0.0

            if orig_d > orig_dist_thresh and ratio < ratio_thresh:
                votes[(tgt_label, nearest_name)] += 1

    # Only keep relabelings that appear in >= 50% of sampled frames
    min_votes = max(1, len(frame_indices) // 2)
    rename_map: Dict[str, str] = {}
    used: set = set()
    for (current, proposed), cnt in votes.most_common():
        if cnt < min_votes:
            continue
        if current not in rename_map and proposed not in used:
            rename_map[current] = proposed
            used.add(proposed)

    return rename_map
    for (current, proposed), cnt in votes.most_common():
        if cnt < min_votes:
            continue
        if current not in rename_map and proposed not in used:
            rename_map[current] = proposed
            used.add(proposed)

    return rename_map
