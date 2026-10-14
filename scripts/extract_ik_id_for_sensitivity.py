#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
extract_ik_id_for_sensitivity.py
================================
Python 3.8 環境用データ抽出スクリプト

Iceberg warehouse から IK（関節角度, method_id=1）と ID（関節トルク, method_id=7）の
歩行周期正規化波形を抽出し、parquet 形式で保存する。

出力:
  - notebooks/sensitivity_ik_id_data.parquet
    列: study_id, subject_id, session_id, run_id, cycle_duration, to_percent,
        hip_flexion_r_0 ... hip_flexion_r_100,
        knee_angle_r_0  ... knee_angle_r_100,
        ankle_angle_r_0 ... ankle_angle_r_100,
        hip_flexion_r_moment_0 ... hip_flexion_r_moment_100,
        knee_angle_r_moment_0  ... knee_angle_r_moment_100,
        ankle_angle_r_moment_0 ... ankle_angle_r_moment_100

使い方:
  python scripts/extract_ik_id_for_sensitivity.py
"""
import gc
import time as time_module
import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, find_peaks

import duckdb
duckdb.sql("INSTALL iceberg"); duckdb.sql("LOAD iceberg")
duckdb.sql("INSTALL avro"); duckdb.sql("LOAD avro")
duckdb.sql("SET unsafe_enable_version_guessing = true;")

# ═══════════════════════════════════════════════════════════════
#  設定
# ═══════════════════════════════════════════════════════════════
WAREHOUSE = "/workspace/warehouse/warehouse"
OUTPUT_PATH = "/workspace/notebooks/IKandID_all_filtered_data.parquet"

def scan(t):
    return f"iceberg_scan('{WAREHOUSE}/{t}', allow_moved_paths = true)"

# --- 品質フィルタ ---
IK_RMS_THRESHOLD = 99.99
IK_MAX_THRESHOLD = 99.99
PELVIS_MARKER_ERROR_THRESHOLD = 99.99
PELVIS_MARKER_NAMES = [
    'RASI', 'LASI', 'RPSI', 'LPSI', 'LTHI', 'RTHI',
    'LKNE', 'RKNE', 'LANK', 'RANK', 'LTIB', 'RTIB',
    'LHEE', 'RHEE', 'LTOE', 'RTOE',
]
GRF_THRESHOLD = 20.0

# --- 関節名 ---
JOINTS_IK = ['hip_flexion_r', 'knee_angle_r', 'ankle_angle_r']
JOINTS_ID = ['hip_flexion_r_moment', 'knee_angle_r_moment', 'ankle_angle_r_moment']

# --- 歩行周期検出パラメータ ---
MIN_CYCLE = 0.6
MAX_CYCLE = 13.5
LOWPASS_CUTOFF = 6.0
GRF_MIN_GAP = 1.0
PELVIS_ALIASES = ['RASIS', 'RASI']
ANKLE_ALIASES  = ['RLK', 'RANK']
GRF_STUDY_IDS = {None}
MARKER_STUDY_IDS = {'120', '1'}

N_RESAMPLE = 101

# --- 解析対象 ---
RUN_CONFIGS = [
    {"run_id": "20260331_0523_keisanki", "task_id": 2.1},
    {"run_id": "20260425_0505_IKandID", "task_id": 2.1},
]

# ═══════════════════════════════════════════════════════════════
#  信号処理関数
# ═══════════════════════════════════════════════════════════════

def apply_butterworth_lowpass(waveform, cutoff_hz=6.0, fs=100.0, order=4):
    """
    101点の波形に対して Butterworth ローパスフィルタを適用
    
    Args:
        waveform: 1D array of waveform values
        cutoff_hz: カットオフ周波数 (Hz), デフォルト 6.0 Hz
        fs: サンプリングレート (Hz), デフォルト 100.0 Hz (101点/周期)
        order: フィルタ次数, デフォルト 4
    
    Returns:
        フィルタ適用後の波形
    """
    if len(waveform) < order + 1:
        return waveform.copy()
    
    nyquist_freq = fs / 2.0
    normalized_cutoff = cutoff_hz / nyquist_freq
    
    if normalized_cutoff >= 1.0:
        normalized_cutoff = 0.99
    
    b, a = butter(order, normalized_cutoff, btype='low')
    
    # 有限値のみをフィルタ処理
    valid_mask = np.isfinite(waveform)
    if valid_mask.sum() < order + 1:
        return waveform.copy()
    
    filtered = waveform.copy()
    valid_indices = np.where(valid_mask)[0]
    if len(valid_indices) > 0:
        valid_vals = waveform[valid_mask]
        try:
            filtered_valid = filtfilt(b, a, valid_vals)
            filtered[valid_indices] = filtered_valid
        except Exception:
            pass
    
    return filtered


# ═══════════════════════════════════════════════════════════════
#  歩行周期検出関数（参考ノートブックと同一）
# ═══════════════════════════════════════════════════════════════

def detect_gait_cycle_grf(t_grf, vy_grf, threshold=20.0, min_gap=1.0):
    if len(t_grf) == 0:
        return None
    contact = vy_grf > threshold
    onsets = []
    if contact[0]:
        onsets.append(0)
    for i in range(1, len(contact)):
        if contact[i] and not contact[i - 1]:
            onsets.append(i)
    if len(onsets) < 2:
        return None
    for i in range(len(onsets) - 1):
        t1 = t_grf[onsets[i]]
        t2 = t_grf[onsets[i + 1]]
        dur = t2 - t1
        if dur < min_gap or dur > MAX_CYCLE:
            continue
        to_time = None
        for j in range(onsets[i], onsets[i + 1]):
            if contact[j] and (j + 1 >= len(contact) or not contact[j + 1]):
                to_time = t_grf[j]
                break
        return {
            't_start': t1, 't_end': t2,
            'to_time': to_time, 'duration': dur, 'type': 'gait_cycle',
        }
    return None


def _find_extrema(signal, min_dist, mode='max', height=None):
    N = len(signal)
    if N == 0:
        return np.array([], dtype=int)
    if N == 1:
        return np.array([0])
    s = signal if mode == 'max' else -signal
    internal, _ = find_peaks(s, distance=min_dist, height=height)
    candidates = list(internal)
    if s[0] >= s[1] and (height is None or s[0] >= height):
        if len(candidates) == 0 or candidates[0] >= min_dist:
            candidates.insert(0, 0)
    if s[-1] >= s[-2] and (height is None or s[-1] >= height):
        if len(candidates) == 0 or (N - 1 - candidates[-1]) >= min_dist:
            candidates.append(N - 1)
    candidates = sorted(set(candidates))
    if len(candidates) > 1:
        filtered = [candidates[0]]
        for c in candidates[1:]:
            if c - filtered[-1] >= min_dist:
                filtered.append(c)
        candidates = filtered
    if len(candidates) == 0:
        candidates = [int(np.argmax(s))]
    return np.array(candidates, dtype=int)


def _resolve_marker_event_modes(pelvis_x):
    if pelvis_x is None or len(pelvis_x) < 2:
        return 'max', 'min'
    if pelvis_x[-1] > pelvis_x[0]:
        return 'max', 'min'
    if pelvis_x[0] > pelvis_x[-1]:
        return 'min', 'max'
    return 'max', 'min'


def detect_gait_events_marker(t, diff_signal, pelvis_x=None, min_distance_sec=0.3):
    dt = np.median(np.diff(t)) if len(t) > 1 else 0.01
    fs = 1.0 / dt if dt > 0 else 100.0
    if LOWPASS_CUTOFF > 0 and LOWPASS_CUTOFF < 0.5 * fs and len(t) > 12:
        b, a = butter(4, LOWPASS_CUTOFF / (0.5 * fs), btype='low')
        diff_filtered = filtfilt(b, a, diff_signal)
    else:
        diff_filtered = diff_signal.copy()
    min_dist = max(1, int(min_distance_sec * fs))
    height_thresh = 0.15
    hs_mode, to_mode = _resolve_marker_event_modes(pelvis_x)
    hs_idx = _find_extrema(diff_filtered, min_dist, mode=hs_mode, height=height_thresh)
    to_idx = _find_extrema(diff_filtered, min_dist, mode=to_mode, height=height_thresh)
    return hs_idx, to_idx, diff_filtered


def select_marker_cycle_with_grf(t_marker, hs_indices, to_indices,
                                  t_grf, vy_grf, threshold):
    for i in range(len(hs_indices) - 1):
        hs1, hs2 = hs_indices[i], hs_indices[i + 1]
        dur = t_marker[hs2] - t_marker[hs1]
        if not (MIN_CYCLE <= dur <= MAX_CYCLE):
            continue
        to_between = to_indices[(to_indices > hs1) & (to_indices < hs2)]
        to_i = int(to_between[0]) if len(to_between) > 0 else None
        if len(t_grf) == 0:
            continue
        mask = (t_grf >= t_marker[hs1]) & (t_grf <= t_marker[hs2])
        vy_in = vy_grf[mask]
        if len(vy_in) > 0 and np.sum(vy_in > threshold) / len(vy_in) > 0.01:
            return {
                'hs1': hs1, 'hs2': hs2,
                't_start': t_marker[hs1], 't_end': t_marker[hs2],
                'to_time': t_marker[to_i] if to_i is not None else None,
                'duration': dur, 'type': 'gait_cycle',
            }
    return None


def _pick_column(available_cols, aliases, axis='X'):
    for alias in aliases:
        cand = f"{alias}_{axis}"
        if cand in available_cols:
            return cand
    return None


# ═══════════════════════════════════════════════════════════════
#  メイン処理
# ═══════════════════════════════════════════════════════════════

def main():
    t_start_all = time_module.time()

    # ─── 1. 有効トライアルの取得 ───
    print("=" * 60)
    print("Step 1: 有効トライアルの特定")
    print("=" * 60)
    frames = []
    pelvis_marker_sql = ", ".join(f"'{name}'" for name in PELVIS_MARKER_NAMES)

    for cfg in RUN_CONFIGS:
        rid, tid = cfg['run_id'], cfg['task_id']

        vt = duckdb.sql(f"""
            SELECT DISTINCT
                v.study_id, v.subject_id, v.session_id
            FROM {scan('osim_validation')} v
            INNER JOIN {scan('session_info')} s
                ON v.study_id = s.study_id AND v.subject_id = s.subject_id AND v.session_id = s.session_id
            WHERE v.run_id = '{rid}'
              AND s.task_id = {tid}
              AND v.ik_rms_error <= {IK_RMS_THRESHOLD}
              AND v.ik_max_error < {IK_MAX_THRESHOLD}
              AND NOT EXISTS (
                  SELECT 1
                  FROM {scan('ik_marker_errors')} ime
                  WHERE ime.run_id = v.run_id
                    AND ime.study_id = v.study_id
                    AND ime.subject_id = v.subject_id
                    AND ime.session_id = v.session_id
                    AND upper(ime.marker_name) IN ({pelvis_marker_sql})
                    AND ime.max_error > {PELVIS_MARKER_ERROR_THRESHOLD}
              )
            ORDER BY v.subject_id, v.session_id
        """).fetchdf()

        gt = duckdb.sql(f"""
            SELECT DISTINCT study_id, subject_id, session_id
            FROM {scan('osim_input_value')}
            WHERE run_id = '{rid}' AND type = 2
        """).fetchdf()

        for col in ['study_id', 'subject_id', 'session_id']:
            vt[col] = vt[col].astype(str)
            gt[col] = gt[col].astype(str)

        vt = vt.merge(gt, on=['study_id', 'subject_id', 'session_id'])
        vt['run_id'] = rid
        print(f"  {rid}: {len(vt)} trials (study {vt['study_id'].unique()})")
        frames.append(vt)

    valid_trials = pd.concat(frames, ignore_index=True)
    print(f"\n統合有効トライアル数: {len(valid_trials)}")
    print(f"Study IDs: {sorted(valid_trials['study_id'].unique())}")

    # ─── 2. 歩行周期検出 ───
    print("\n" + "=" * 60)
    print("Step 2: 歩行周期検出")
    print("=" * 60)

    trial_cols = ['run_id', 'study_id', 'subject_id', 'session_id']
    valid_trials_local = valid_trials[trial_cols].copy()
    for c in trial_cols:
        valid_trials_local[c] = valid_trials_local[c].astype(str)

    cycle_records = []
    fail_counts = {
        'grf_no_data': 0, 'grf_no_cycle': 0,
        'marker_no_marker': 0, 'marker_no_hs_pair': 0,
        'marker_no_valid': 0, 'too_short': 0,
    }
    method_counts = {'grf': 0, 'marker': 0}

    for cfg in RUN_CONFIGS:
        rid = str(cfg['run_id'])
        trial_df = valid_trials_local[valid_trials_local['run_id'] == rid].copy()
        if trial_df.empty:
            continue
        trial_df = trial_df.drop_duplicates(subset=['study_id', 'subject_id', 'session_id'])
        trial_keys = trial_df[['study_id', 'subject_id', 'session_id']].copy()
        for c in ['study_id', 'subject_id', 'session_id']:
            trial_keys[c] = trial_keys[c].astype(str)

        study_ids_in_run = set(trial_df['study_id'].unique())
        need_grf = bool(study_ids_in_run & (GRF_STUDY_IDS | MARKER_STUDY_IDS))
        need_marker = bool(study_ids_in_run & MARKER_STUDY_IDS)

        duckdb.register('trial_keys_tmp', trial_keys)
        grf_lookup = {}
        marker_lookup = {}

        try:
            if need_grf:
                grf_df = duckdb.sql(f"""
                    SELECT
                        v.study_id::VARCHAR AS study_id,
                        v.subject_id::VARCHAR AS subject_id,
                        v.session_id::VARCHAR AS session_id,
                        v.time, v.value
                    FROM {scan('osim_input_value')} AS v
                    INNER JOIN trial_keys_tmp AS t
                        ON v.study_id::VARCHAR = t.study_id
                       AND v.subject_id::VARCHAR = t.subject_id
                       AND v.session_id::VARCHAR = t.session_id
                    WHERE v.run_id = '{rid}'
                      AND v.type = 2
                      AND v."column" = 'ground_force_r_vy'
                    ORDER BY v.study_id, v.subject_id, v.session_id, v.time
                """).fetchdf()
                if not grf_df.empty:
                    for c in ['study_id', 'subject_id', 'session_id']:
                        grf_df[c] = grf_df[c].astype(str)
                    grf_lookup = {
                        key: g[['time', 'value']].to_numpy(copy=False)
                        for key, g in grf_df.groupby(
                            ['study_id', 'subject_id', 'session_id'], sort=False
                        )
                    }

            if need_marker:
                avail_df = duckdb.sql(f"""
                    SELECT DISTINCT "column"
                    FROM {scan('osim_input_value')}
                    WHERE run_id = '{rid}' AND type = 1
                """).fetchdf()
                avail = set(avail_df['column']) if not avail_df.empty else set()
                pelvis_col = _pick_column(avail, PELVIS_ALIASES, 'X')
                ankle_col = _pick_column(avail, ANKLE_ALIASES, 'X')

                if pelvis_col is not None and ankle_col is not None:
                    marker_df = duckdb.sql(f"""
                        SELECT
                            v.study_id::VARCHAR AS study_id,
                            v.subject_id::VARCHAR AS subject_id,
                            v.session_id::VARCHAR AS session_id,
                            v.time, v."column", v.value
                        FROM {scan('osim_input_value')} AS v
                        INNER JOIN trial_keys_tmp AS t
                            ON v.study_id::VARCHAR = t.study_id
                           AND v.subject_id::VARCHAR = t.subject_id
                           AND v.session_id::VARCHAR = t.session_id
                        WHERE v.run_id = '{rid}'
                          AND v.type = 1
                          AND v."column" IN ('{pelvis_col}', '{ankle_col}')
                        ORDER BY v.study_id, v.subject_id, v.session_id, v.time
                    """).fetchdf()
                    if not marker_df.empty:
                        for c in ['study_id', 'subject_id', 'session_id']:
                            marker_df[c] = marker_df[c].astype(str)
                        marker_lookup = {
                            key: g
                            for key, g in marker_df.groupby(
                                ['study_id', 'subject_id', 'session_id'], sort=False
                            )
                        }
        finally:
            duckdb.unregister('trial_keys_tmp')

        run_success = 0
        for trial in trial_df.itertuples(index=False):
            sid = str(trial.study_id)
            subj = str(trial.subject_id)
            ses = str(trial.session_id)
            key = (sid, subj, ses)
            grf_arr = grf_lookup.get(key)
            if grf_arr is None or len(grf_arr) == 0:
                fail_counts['grf_no_data'] += 1
                continue
            t_grf = grf_arr[:, 0]
            vy_grf = grf_arr[:, 1]
            result = None

            if sid in GRF_STUDY_IDS:
                result = detect_gait_cycle_grf(t_grf, vy_grf,
                                               threshold=GRF_THRESHOLD, min_gap=GRF_MIN_GAP)
                if result is None:
                    fail_counts['grf_no_cycle'] += 1
                    continue
                method_counts['grf'] += 1

            elif sid in MARKER_STUDY_IDS:
                msub = marker_lookup.get(key)
                if msub is None or msub.empty:
                    fail_counts['marker_no_marker'] += 1
                    continue
                piv = msub.pivot_table(index='time', columns='column', values='value')
                pc = next((c for c in PELVIS_ALIASES if f"{c}_X" in piv.columns), None)
                ac = next((c for c in ANKLE_ALIASES if f"{c}_X" in piv.columns), None)
                pname = f"{pc}_X" if pc else None
                aname = f"{ac}_X" if ac else None
                if pname is None or aname is None:
                    fail_counts['marker_no_marker'] += 1
                    continue
                t_mk = piv.index.values
                if len(t_mk) < 10:
                    fail_counts['too_short'] += 1
                    continue
                pelvis_x = piv[pname].interpolate(limit_direction='both').values
                ankle_x = piv[aname].interpolate(limit_direction='both').values
                diff_raw = ankle_x - pelvis_x
                hs_idx, to_idx, _ = detect_gait_events_marker(t_mk, diff_raw, pelvis_x=pelvis_x)
                if len(hs_idx) < 2:
                    fail_counts['marker_no_hs_pair'] += 1
                    continue
                result = select_marker_cycle_with_grf(
                    t_mk, hs_idx, to_idx, t_grf, vy_grf, GRF_THRESHOLD)
                if result is None:
                    fail_counts['marker_no_valid'] += 1
                    continue
                method_counts['marker'] += 1
            else:
                continue

            to_pct = None
            if result['to_time'] is not None:
                to_pct = (result['to_time'] - result['t_start']) / result['duration'] * 100.0
            cycle_records.append({
                'run_id': rid, 'study_id': sid, 'subject_id': subj, 'session_id': ses,
                'cycle_type': result['type'],
                'start_time': result['t_start'], 'end_time': result['t_end'],
                'to_time': result['to_time'],
                'cycle_duration': result['duration'], 'to_percent': to_pct,
                'method': 'grf' if sid in GRF_STUDY_IDS else 'marker',
            })
            run_success += 1

        print(f"  {rid}: {run_success}/{len(trial_df)} trials 完了")
        del grf_lookup, marker_lookup
        gc.collect()

    gait_cycles = pd.DataFrame(cycle_records)
    del cycle_records
    gc.collect()
    print(f"\n歩行周期検出完了: {len(gait_cycles)} trials")
    print(f"検出方法: {method_counts}")
    print(f"失敗内訳: {fail_counts}")

    if len(gait_cycles) == 0:
        print("ERROR: 歩行周期が1つも検出されませんでした。終了します。")
        return

    # ─── 2b. 被験者体重の取得 ───
    print("\n被験者体重を取得中...")
    weight_df = duckdb.sql(f"""
        SELECT DISTINCT
            study_id::VARCHAR AS study_id,
            subject_id::VARCHAR AS subject_id,
            weight_kg
        FROM {scan('subject_table')}
        WHERE weight_kg IS NOT NULL
    """).fetchdf()
    weight_map = {}
    for _, row in weight_df.iterrows():
        weight_map[(str(row['study_id']), str(row['subject_id']))] = float(row['weight_kg'])
    print(f"  体重データ: {len(weight_map)} subjects")

    # ─── 3. IK + ID 波形の切り出し ───
    print("\n" + "=" * 60)
    print("Step 3: IK + ID 波形の切り出し（101点リサンプル）")
    print("=" * 60)

    all_joints = JOINTS_IK + JOINTS_ID
    joint_sql = ", ".join([f"'{j}'" for j in all_joints])
    cycle_cols = [
        'run_id', 'study_id', 'subject_id', 'session_id',
        'start_time', 'end_time', 'cycle_duration', 'cycle_type', 'to_percent',
    ]

    extracted = []

    for rid, cycle_run in gait_cycles.groupby('run_id', sort=False):
        cycle_run = cycle_run[cycle_cols].copy()
        if cycle_run.empty:
            continue
        for c in ['run_id', 'study_id', 'subject_id', 'session_id']:
            cycle_run[c] = cycle_run[c].astype(str)

        cycle_keys = cycle_run.drop_duplicates(
            subset=['study_id', 'subject_id', 'session_id', 'start_time', 'end_time']
        ).copy()

        # IK (method_id=1) を取得
        duckdb.register('cycle_keys_tmp', cycle_keys)
        try:
            ik_chunk = duckdb.sql(f"""
                SELECT
                    v.study_id::VARCHAR AS study_id,
                    v.subject_id::VARCHAR AS subject_id,
                    v.session_id::VARCHAR AS session_id,
                    v.time, v.variable, v.value,
                    c.start_time, c.end_time, c.cycle_duration, c.cycle_type, c.to_percent
                FROM {scan('osim_value')} AS v
                INNER JOIN cycle_keys_tmp AS c
                    ON v.study_id::VARCHAR = c.study_id
                   AND v.subject_id::VARCHAR = c.subject_id
                   AND v.session_id::VARCHAR = c.session_id
                   AND v.time >= c.start_time AND v.time <= c.end_time
                WHERE v.run_id = '{rid}'
                  AND v.method_id = 1
                  AND v.variable IN ({", ".join(f"'{j}'" for j in JOINTS_IK)})
                ORDER BY v.study_id, v.subject_id, v.session_id, c.start_time, v.variable, v.time
            """).fetchdf()
        finally:
            duckdb.unregister('cycle_keys_tmp')

        # ID (method_id=7) を取得
        duckdb.register('cycle_keys_tmp', cycle_keys)
        try:
            id_chunk = duckdb.sql(f"""
                SELECT
                    v.study_id::VARCHAR AS study_id,
                    v.subject_id::VARCHAR AS subject_id,
                    v.session_id::VARCHAR AS session_id,
                    v.time, v.variable, v.value,
                    c.start_time, c.end_time
                FROM {scan('osim_value')} AS v
                INNER JOIN cycle_keys_tmp AS c
                    ON v.study_id::VARCHAR = c.study_id
                   AND v.subject_id::VARCHAR = c.subject_id
                   AND v.session_id::VARCHAR = c.session_id
                   AND v.time >= c.start_time AND v.time <= c.end_time
                WHERE v.run_id = '{rid}'
                  AND v.method_id = 7
                  AND v.variable IN ({", ".join(f"'{j}'" for j in JOINTS_ID)})
                ORDER BY v.study_id, v.subject_id, v.session_id, c.start_time, v.variable, v.time
            """).fetchdf()
        finally:
            duckdb.unregister('cycle_keys_tmp')

        if ik_chunk.empty:
            print(f"  {rid}: IK データなし")
            continue

        for c in ['study_id', 'subject_id', 'session_id']:
            ik_chunk[c] = ik_chunk[c].astype(str)
            if not id_chunk.empty:
                id_chunk[c] = id_chunk[c].astype(str)

        ik_chunk['run_id'] = str(rid)
        group_cols = ['study_id', 'subject_id', 'session_id', 'start_time', 'end_time']

        # ID を辞書に変換 (高速アクセス用)
        id_groups = {}
        if not id_chunk.empty:
            for gkey, gdf in id_chunk.groupby(group_cols, sort=False):
                id_groups[gkey] = gdf

        for gkey, g in ik_chunk.groupby(group_cols, sort=False):
            pivot_ik = g.pivot_table(index='time', columns='variable', values='value')
            if not all(j in pivot_ik.columns for j in JOINTS_IK):
                continue
            if len(pivot_ik) < 5:
                continue

            t_orig = pivot_ik.index.to_numpy()
            t_norm = np.linspace(t_orig[0], t_orig[-1], N_RESAMPLE)

            # 元のサンプリングレート推定
            dt_orig = np.median(np.diff(t_orig)) if len(t_orig) > 1 else 0.01
            fs_orig = 1.0 / dt_orig if dt_orig > 0 else 100.0

            wkg = weight_map.get((gkey[0], gkey[1]), np.nan)
            row_data = {
                'run_id': str(rid),
                'study_id': gkey[0],
                'subject_id': gkey[1],
                'session_id': gkey[2],
                'weight_kg': wkg,
                'cycle_duration': g.iloc[0]['cycle_duration'],
                'to_percent': g.iloc[0]['to_percent'],
            }

            # IK 波形（元のサンプリングレートで LPF 適用 → 101点にリサンプリング）
            for j in JOINTS_IK:
                raw_vals = pivot_ik[j].to_numpy()
                # 元のサンプリングレートで LPF 適用
                vals_filtered = apply_butterworth_lowpass(raw_vals, cutoff_hz=6.0, fs=fs_orig)
                # その後、101点にリサンプリング
                vals_resampled = np.interp(t_norm, t_orig, vals_filtered)
                for ti in range(N_RESAMPLE):
                    row_data[f"{j}_{ti}"] = vals_resampled[ti]

            # ID 波形（元のサンプリングレートで LPF 適用 → 101点にリサンプリング）
            id_g = id_groups.get(gkey)
            has_id = False
            if id_g is not None and not id_g.empty:
                pivot_id = id_g.pivot_table(index='time', columns='variable', values='value')
                if all(j in pivot_id.columns for j in JOINTS_ID) and len(pivot_id) >= 5:
                    t_id = pivot_id.index.to_numpy()
                    t_norm_id = np.linspace(t_id[0], t_id[-1], N_RESAMPLE)
                    # 元のサンプリングレート推定
                    dt_id = np.median(np.diff(t_id)) if len(t_id) > 1 else 0.01
                    fs_id = 1.0 / dt_id if dt_id > 0 else 100.0
                    for j in JOINTS_ID:
                        raw_vals = pivot_id[j].to_numpy()
                        # 元のサンプリングレートで LPF 適用
                        vals_filtered = apply_butterworth_lowpass(raw_vals, cutoff_hz=6.0, fs=fs_id)
                        # その後、101点にリサンプリング
                        vals_resampled = np.interp(t_norm_id, t_id, vals_filtered)
                        for ti in range(N_RESAMPLE):
                            row_data[f"{j}_{ti}"] = vals_resampled[ti]
                    has_id = True

            if not has_id:
                for j in JOINTS_ID:
                    for ti in range(N_RESAMPLE):
                        row_data[f"{j}_{ti}"] = np.nan

            extracted.append(row_data)

        del ik_chunk, id_chunk
        gc.collect()

    print(f"\n切り出し完了: {len(extracted)} トライアル")

    # ─── 4. parquet 保存 ───
    print("\n" + "=" * 60)
    print("Step 4: parquet 保存")
    print("=" * 60)

    df_out = pd.DataFrame(extracted)
    del extracted
    gc.collect()

    # IK/ID が両方存在するトライアルのみ残す
    id_first_col = f"{JOINTS_ID[0]}_0"
    n_before = len(df_out)
    df_out = df_out.dropna(subset=[id_first_col])
    print(f"ID データあり: {len(df_out)} / {n_before} トライアル")

    # Study別の集計
    for sid in sorted(df_out['study_id'].unique()):
        sub = df_out[df_out['study_id'] == sid]
        print(f"  Study {sid}: {len(sub)} trials ({sub['subject_id'].nunique()} subjects)")

    df_out.to_parquet(OUTPUT_PATH, index=False, engine='pyarrow')
    print(f"\n保存完了: {OUTPUT_PATH}")
    print(f"行列サイズ: {df_out.shape}")
    print(f"IK列: {len(JOINTS_IK)} joints × {N_RESAMPLE} steps = {len(JOINTS_IK)*N_RESAMPLE}")
    print(f"ID列: {len(JOINTS_ID)} joints × {N_RESAMPLE} steps = {len(JOINTS_ID)*N_RESAMPLE}")

    elapsed = time_module.time() - t_start_all
    print(f"\n全処理完了 ({elapsed:.1f}秒)")


if __name__ == '__main__':
    main()
