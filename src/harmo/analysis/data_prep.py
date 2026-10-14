from __future__ import annotations

import logging
import os
import hashlib
import duckdb
import pandas as pd
from harmo.analysis import xml_builder as make_xml
from harmo.analysis import db_to_osim as db2osim
from harmo.warehouse.connection import get_warehouse_path
import shutil

from harmo.ingest.readers.c3d import read_c3d_file
from harmo.ingest.readers.trc import read_trc_file

logger = logging.getLogger(__name__)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _external_static_markers(source):
    """Load a validated external static trial and recheck its timebase/hash."""
    if not isinstance(source, dict):
        raise ValueError("external static source must be a mapping")
    path = os.path.abspath(str(source.get("absolute_path", "")))
    if not os.path.isfile(path):
        raise FileNotFoundError("external static source is missing: {}".format(path))
    expected_hash = str(source.get("observed_sha256", ""))
    if not expected_hash or _sha256(path) != expected_hash:
        raise ValueError("external static source checksum changed after intake")
    suffix = os.path.splitext(path)[1].lower()
    header = None
    if suffix == ".c3d":
        data = read_c3d_file(path, data_source="point")
    elif suffix == ".trc":
        header, data = read_trc_file(path)
    else:
        raise ValueError("external static source must be C3D or TRC")
    if "time" not in data.columns:
        raise ValueError("external static source has no time column")
    time = pd.to_numeric(data["time"], errors="coerce")
    if time.isna().any():
        raise ValueError("external static source time column must be finite")
    differences = time.diff().dropna()
    if differences.empty or (differences <= 0).any():
        raise ValueError("external static source timebase must be strictly increasing")
    median_interval = float(differences.median())
    if (differences - median_interval).abs().max() > max(
        1e-9, median_interval * 0.001
    ):
        raise ValueError("external static source timebase must be regular")
    observed_rate = 1.0 / median_interval
    declared_rate = float(source["sampling_rate_hz"])
    if abs(observed_rate - declared_rate) > max(0.01, declared_rate * 0.001):
        raise ValueError(
            "external static sampling rate mismatch: declared={} observed={}".format(
                declared_rate, observed_rate
            )
        )
    unit = str(source["coordinate_unit"]).strip().lower()
    if header is not None and not header.empty:
        header_unit = str(header.iloc[0].get("Units", "")).strip().lower()
        header_rate = header.iloc[0].get("DataRate")
        if header_unit and header_unit != unit:
            raise ValueError("external TRC unit conflicts with intake manifest")
        if header_rate is not None and abs(float(header_rate) - declared_rate) > 0.01:
            raise ValueError("external TRC rate conflicts with intake manifest")
    marker_data = data.drop(columns=["frame_num"], errors="ignore")
    return marker_data, unit, declared_rate, str(source["coordinate_system_id"])


def _scaling_source_policy(osim_cfg=None):
    scaling = (osim_cfg or {}).get("scaling", {}) or {}
    policy = scaling.get("source_policy", {}) or {}
    return {
        "require_static_calibration": bool(
            policy.get("require_static_calibration", False)
        ),
        "allow_dynamic_pseudo_static": bool(
            policy.get("allow_dynamic_pseudo_static", True)
        ),
    }


def select_scaling_trial(trials, osim_cfg=None):
    """Select explicit static input and fail closed when the policy requires it."""
    standing = next(
        (
            trial
            for trial in trials
            if str(trial.get("hed_ontology")).strip() == "(Standing)"
        ),
        None,
    )
    if standing is not None:
        return standing
    policy = _scaling_source_policy(osim_cfg)
    if (
        policy["require_static_calibration"]
        or not policy["allow_dynamic_pseudo_static"]
    ):
        raise ValueError(
            "No explicit (Standing) static calibration trial is available; "
            "dynamic pseudo-static scaling is forbidden by source policy."
        )
    return trials[0]


def _extract_allowed_markers(marker_df, apply_col):
    """dim_marker から TRC 出力対象マーカー名を抽出する。"""
    if marker_df is None or marker_df.empty or "marker" not in marker_df.columns:
        return []

    filtered = marker_df.copy()
    if apply_col in filtered.columns:
        # bool/文字列/数値の混在に対応
        truthy = filtered[apply_col].fillna(True).astype(str).str.lower().isin({"true", "1", "yes", "y", "t"})
        filtered = filtered[truthy]

    markers = filtered["marker"].dropna().astype(str).str.strip()
    markers = markers[markers != ""]
    return sorted(set(markers.tolist()))


def _get_db_connection():
    """Workerプロセス内でDuckDB接続を作成するヘルパー関数"""
    con = duckdb.connect()
    con.sql("INSTALL iceberg; LOAD iceberg;")
    return con

def _fill_default_weights(df, prefix):
    """マーカーの重み・適用フラグがなければデフォルト値で埋める"""
    apply_col = f"{prefix}_apply"
    weight_col = f"{prefix}_weight"
    
    if apply_col not in df.columns:
        df[apply_col] = "true"
    if weight_col not in df.columns:
        df[weight_col] = 1.0
    return df


def _apply_osim_marker_weights(df, prefix, osim_cfg=None):
    """osim_analysis_config の marker_weights を DataFrame に反映する。"""
    if osim_cfg is None:
        return df
    if prefix == "scale":
        marker_weights = ((osim_cfg.get("scaling", {}) or {}).get("marker_placer", {}).get("marker_weights", {}))
    elif prefix == "ik":
        marker_weights = ((osim_cfg.get("IK", {}) or {}).get("IKTaskSet", {}).get("marker_weights", {}))
    else:
        marker_weights = {}
    if not isinstance(marker_weights, dict) or not marker_weights:
        return df
    marker_to_weight = {}
    for marker, weight in marker_weights.items():
        try:
            marker_to_weight[str(marker)] = float(weight)
        except (TypeError, ValueError):
            continue
    if not marker_to_weight or "marker" not in df.columns:
        return df

    weight_col = f"{prefix}_weight"
    mapped = df["marker"].map(marker_to_weight)
    df[weight_col] = mapped.where(mapped.notna(), df[weight_col])
    return df


def _column_coord(study_id):
    """
    カラム名から座標情報を取得できない場合、definitionテーブルから取得する
    """
    _warehouse_path = get_warehouse_path()
    con = _get_db_connection()
    try:
        query = f"""
        SELECT DISTINCT column_name, axis
        FROM iceberg_scan('{_warehouse_path}/warehouse/dim_channels', allow_moved_paths = true)
        WHERE study_id = '{study_id}'
          AND axis IS NOT NULL 
        """
        col_coord_df = con.sql(query).fetchdf()
        return col_coord_df
    finally:
        con.close()

def export_emg_mapping(study_id, output_dir):
    """
    EMGマッピング定義(Input Map)をDBから取得し、CSVとして保存する
    """
    _warehouse_path = get_warehouse_path()
    con = _get_db_connection()
    try:
        query = f"""
        SELECT DISTINCT column_name as original_name, muscle as seniam_name, side
        FROM iceberg_scan('{_warehouse_path}/warehouse/dim_channels', allow_moved_paths = true)
        WHERE study_id = '{study_id}'
          AND muscle IS NOT NULL 
          AND muscle != '' 
          AND muscle != 'NaN'
        """
        df = con.sql(query).fetchdf()
        
        # ファイル保存
        csv_path = os.path.join(output_dir, f"study{study_id}_input_map.csv")
        df.to_csv(csv_path, index=False)
        return csv_path
    finally:
        con.close()

# =============================================================================
# Phase 1: Scaling用データ準備
# =============================================================================
def prepare_scaling_data(
    trial_meta, temp_dir, template_dir, osim_cfg=None, external_static_source=None
):
    """
    スケーリング(モデル作成)に必要なファイルを一時ディレクトリに生成し、
    使用したパラメータを返す。
    osim_cfg: osim_analysis_config.yaml の読み込み結果 (dict)。None なら従来動作。
    """
    _warehouse_path = get_warehouse_path()
    con = _get_db_connection()
    study_id = trial_meta['study_id']
    subject_id = trial_meta['subject_id']
    session_id = trial_meta['session_id']
    # task_id = trial_meta.get('task_id', None)  # legacy: 静的試行判定に使っていた
    hed_ontology = trial_meta.get('hed_ontology', None)

    # Store parameters for provenance
    params_list = []

    try:
        # --- 1. マーカーデータの取得 ---
        if external_static_source is not None:
            marker_pivot, mocap_unit, mocap_fs, coordinate_system_id = (
                _external_static_markers(external_static_source)
            )
            params_list.append(
                {
                    "setup_name": "external_scaling_source",
                    "source_kind": "static-calibration",
                    "static_trial_path": external_static_source["path"],
                    "static_trial_sha256": external_static_source[
                        "observed_sha256"
                    ],
                    "coordinate_unit": mocap_unit,
                    "coordinate_system_id": coordinate_system_id,
                    "sampling_rate_hz": mocap_fs,
                }
            )
        else:
            query_marker = f"""
            SELECT time, channel, value
            FROM iceberg_scan('{_warehouse_path}/warehouse/raw_data', allow_moved_paths = true)
            WHERE lower(sensor_type) = 'motion-capture'
              AND study_id = '{study_id}'
              AND subject_id = '{subject_id}'
              AND session_id = '{session_id}'
            """
            df_marker = con.sql(query_marker).fetchdf()
            if df_marker.empty:
                print(f"エラー: Scaling用のマーカーデータがありません (Session: {session_id})")
                return None
            marker_pivot = df_marker.pivot_table(
                values='value', index='time', columns='channel', dropna=False
            )
            query_coord = f"""
            SELECT DISTINCT unit
            FROM iceberg_scan('{_warehouse_path}/warehouse/raw_data', allow_moved_paths = true)
            WHERE study_id = '{study_id}' 
            AND lower(sensor_type) = 'motion-capture'
            """
            mocap_unit = str(con.sql(query_coord).fetchone()[0])
            query = f"""
            WITH target_session AS (
                SELECT mocap_sampling
                FROM iceberg_scan('/workspace/warehouse/warehouse/session_info', allow_moved_paths = true)
                WHERE study_id = '{study_id}'
                AND subject_id = '{subject_id}'
                AND session_id = '{session_id}'
            ),
            target_device AS (
                SELECT samplingrate_Hz
                FROM iceberg_scan('/workspace/warehouse/warehouse/device_table', allow_moved_paths = true)
                WHERE study_id = '{study_id}' AND type = 'Motion-capture'
            )
            SELECT COALESCE(
                (SELECT mocap_sampling FROM target_session),
                (SELECT samplingrate_Hz FROM target_device)
            )
            """
            result = con.sql(query).fetchone()
            mocap_fs = int(result[0]) if result and result[0] is not None else None
            coordinate_system_id = None
        
        # 座標変換用テーブル
        query_coord = f"""
        SELECT * FROM iceberg_scan('{_warehouse_path}/warehouse/dim_coordinate', allow_moved_paths = true)
        WHERE lower(sensor_type) = 'motion-capture' AND study_id = '{study_id}'
        """
        coor_table = con.sql(query_coord).fetchdf()
        if coordinate_system_id == "opensim":
            unify = marker_pivot
        else:
            if coordinate_system_id and coordinate_system_id not in set(
                coor_table.get("coord_sys_id", pd.Series(dtype=str)).astype(str)
            ):
                raise ValueError(
                    "external static coordinate system does not match study definition"
                )
            unify = db2osim.unify_coordinate_system(marker_pivot, coor_table)

        query_meta = f"""
        SELECT * FROM iceberg_scan('{_warehouse_path}/warehouse/dim_marker', allow_moved_paths = true)
        WHERE study_id = '{study_id}'
        """
        marker_df = con.sql(query_meta).fetchdf()
        marker_df = _fill_default_weights(marker_df, "scale")
        marker_df = _apply_osim_marker_weights(marker_df, "scale", osim_cfg)
        allowed_scale_markers = _extract_allowed_markers(marker_df, "scale_apply")
        
        scale_trc_filename = "scale.trc"
        
        _marker_cutoff = (
            (osim_cfg.get("IK", {}) or {}).get("marker_lowpass_cutoff")
            if osim_cfg else None
        )

        # if str(task_id) == '0':
        #     db2osim.scale_trc_static(
        #         unify,
        #         unit=mocap_unit,
        #         fs=mocap_fs,
        #         dir=temp_dir,
        #         output_file_name=scale_trc_filename,
        #         allowed_markers=allowed_scale_markers,
        #         marker_cutoff=_marker_cutoff,
        #     )
        # else:
        #     static_slice = unify.head(2)
        #     db2osim.scale_trc_kinetic(
        #         static_slice,
        #         unit=mocap_unit,
        #         fs=mocap_fs,
        #         dir=temp_dir,
        #         output_file_name=scale_trc_filename,
        #         allowed_markers=allowed_scale_markers,
        #         marker_cutoff=_marker_cutoff,
        #     )

        if external_static_source is not None or str(hed_ontology).strip() == '(Standing)':
            db2osim.scale_trc_static(
                unify,
                unit=mocap_unit,
                fs=mocap_fs,
                dir=temp_dir,
                output_file_name=scale_trc_filename,
                allowed_markers=allowed_scale_markers,
                marker_cutoff=_marker_cutoff,
            )
        else:
            source_policy = _scaling_source_policy(osim_cfg)
            if (
                source_policy["require_static_calibration"]
                or not source_policy["allow_dynamic_pseudo_static"]
            ):
                raise ValueError(
                    "Scaling rejected: session {!r} is not an explicit "
                    "(Standing) static calibration trial and the configured "
                    "source policy forbids dynamic pseudo-static frames.".format(
                        session_id
                    )
                )
            static_slice = unify.head(2)
            db2osim.scale_trc_kinetic(
                static_slice,
                unit=mocap_unit,
                fs=mocap_fs,
                dir=temp_dir,
                output_file_name=scale_trc_filename,
                allowed_markers=allowed_scale_markers,
                marker_cutoff=_marker_cutoff,
            )

        # --- 3. Scaling XML作成 ---

        markerset_filename = "markerset.xml"
        
        markerset_updates = {"marker_df": marker_df, "setup_name": "markerset.xml"}
        make_xml.update_markerset_xml(
            os.path.join(template_dir, "markerset.xml"),
            os.path.join(temp_dir, markerset_filename),
            markerset_updates
        )
        params_list.append(markerset_updates)


        query_subj = f"""
        SELECT height_m, weight_kg, age_year 
        FROM iceberg_scan('{_warehouse_path}/warehouse/subject_table', allow_moved_paths = true)
        WHERE study_id = '{study_id}' 
            AND subject_id = '{subject_id}'
        """
        subj_df = con.sql(query_subj).fetchdf()
        
        model_filename = "scaled_model.osim" 
        output_model_fullpath = os.path.join(temp_dir, model_filename)
        marker_df_scale = marker_df[["marker", "scale_apply", "scale_weight"]]
        
        scale_values = {
            "name": f"{study_id}_{subject_id}",
            "mass": subj_df["weight_kg"].iloc[0],
            "height": subj_df["height_m"].iloc[0] * 1000,
            "age": subj_df["age_year"].iloc[0],
            "model_file": os.path.join(os.environ.get('HARMO_OSIM_DIR', '/workspace/src/harmo/osim'), "model", f"study{study_id}_post_GUI.osim"),
            "marker_file": scale_trc_filename,
            "output_model_file": model_filename,
            "output_scale_file": "scale_result.xml",
            "output_motion_file": "scale_output_walk.mot",
            "marker_placer_tasks_df": marker_df_scale,
            "time_range_start": 0.0, 
            "time_range_end": 1000,
            "setup_name": "setup_scale.xml" # for provenance
        }
        
        setup_scale_path = os.path.join(temp_dir, "setup_scale.xml")
        returned_params = make_xml.update_scaletool(
            os.path.join(template_dir, "setup_scale.xml"),
            setup_scale_path,
            scale_values
        )
        params_list.append(returned_params)
        
        return {
            "scale_setup": setup_scale_path,
            "output_model": output_model_fullpath,
            "scale_result": os.path.join(temp_dir, "scale_result.xml"),
            "params_list": params_list # Return the collected parameters
        }

    finally:
        con.close()

# =============================================================================
# Phase 2: 解析(IK/Moco)用データ準備
# =============================================================================
def prepare_analysis_data(trial_meta, temp_dir, template_dir, shared_model_path, run_id=None, osim_cfg=None):
    """
    IK, Mocoに必要なファイルを生成し、使用したパラメータを返す。
    モデルは共有フォルダからコピーする。
    osim_cfg: osim_analysis_config.yaml の読み込み結果 (dict)。None なら従来動作。
    """
    _warehouse_path = get_warehouse_path()
    con = _get_db_connection()
    study_id = trial_meta['study_id']
    subject_id = trial_meta['subject_id']
    session_id = trial_meta['session_id']

    # Store parameters for provenance
    params_list = []

    ik_trc_fullpath = None
    grf_mot_fullpath = None
    emg_sto_path = None
    setup_ik_path = None
    setup_id_path = None
    grf_xml_path = None
    ik_result_fullpath = None   # 解析がスキップされた場合の保険
    id_result_fullpath = None   # ID結果ファイル
    
    model_filename = "scaled_model.osim"
    local_model_fullpath = os.path.join(temp_dir, model_filename)
    
    try:
        # --- 1. 共有モデルのコピー ---
        if os.path.abspath(shared_model_path) != os.path.abspath(local_model_fullpath):
            shutil.copy(shared_model_path, local_model_fullpath)
   
        
        # --- 2. Kinetic TRC作成 ---
        query_marker = f"""
        SELECT time, channel, value
        FROM iceberg_scan('{_warehouse_path}/warehouse/raw_data', allow_moved_paths = true)
        WHERE lower(sensor_type) = 'motion-capture'
          AND study_id = '{study_id}' 
          AND subject_id = '{subject_id}' 
          AND session_id = '{session_id}'
        """
        df_marker = con.sql(query_marker).fetchdf()
        if not df_marker.empty:
            # Keep all marker channels, even if a channel is all-NaN in this session.
            # This prevents TRC marker columns from shrinking and shifting across sessions.
            marker_pivot = df_marker.pivot_table(values='value', index='time', columns='channel', dropna=False)
        
            query_coord = f"""
            SELECT DISTINCT unit
            FROM iceberg_scan('{_warehouse_path}/warehouse/raw_data', allow_moved_paths = true)
            WHERE study_id = '{study_id}' 
            AND lower(sensor_type) = 'motion-capture'
            """
            mocap_unit = str(con.sql(query_coord).fetchone()[0])
            
            #第一優先はsession_infoのサンプリングレート、もし中身がNoneならdevice_tableの情報をとりにいく
            query = f"""
            WITH target_session AS (
                SELECT mocap_sampling
                FROM iceberg_scan('/workspace/warehouse/warehouse/session_info', allow_moved_paths = true)
                WHERE study_id = '{study_id}' 
                AND subject_id = '{subject_id}' 
                AND session_id = '{session_id}' 
            ),
            target_device AS (
                SELECT samplingrate_Hz
                FROM iceberg_scan('/workspace/warehouse/warehouse/device_table', allow_moved_paths = true)
                WHERE study_id = '{study_id}' 
                AND type = 'Motion-capture'
            )
            SELECT COALESCE(
                (SELECT mocap_sampling FROM target_session),
                (SELECT samplingrate_Hz FROM target_device)
            )
            """
            result = con.sql(query).fetchone()
            mocap_fs = int(result[0]) if result and result[0] is not None else None
            
            query_coord = f"""
            SELECT * FROM iceberg_scan('{_warehouse_path}/warehouse/dim_coordinate', allow_moved_paths = true) 
            WHERE sensor_type='Motion-capture' 
                AND study_id='{study_id}'
            """
            coor_table = con.sql(query_coord).fetchdf()
            unify = db2osim.unify_coordinate_system(marker_pivot, coor_table)

            query_meta = f"""
            SELECT * 
            FROM iceberg_scan('{_warehouse_path}/warehouse/dim_marker', allow_moved_paths = true) 
            WHERE study_id = '{study_id}'
            """
            marker_df = con.sql(query_meta).fetchdf()
            marker_df = _fill_default_weights(marker_df, "ik") 
            marker_df = _apply_osim_marker_weights(marker_df, "ik", osim_cfg)
            allowed_ik_markers = _extract_allowed_markers(marker_df, "ik_apply")

            ik_trc_filename = "ik.trc"
            _marker_cutoff_ik = (
                (osim_cfg.get("IK", {}) or {}).get("marker_lowpass_cutoff")
                if osim_cfg else None
            )
            _ref_trc_path = (
                (osim_cfg.get("IK", {}) or {}).get("marker_swap_reference")
                if osim_cfg else None
            )
            excluded_markers = db2osim.ik_trc(
                unify,
                unit=mocap_unit,
                fs=mocap_fs,
                dir=temp_dir,
                output_file_name=ik_trc_filename,
                allowed_markers=allowed_ik_markers,
                marker_cutoff=_marker_cutoff_ik,
                ref_trc_path=_ref_trc_path,
            )
            ik_trc_fullpath = os.path.join(temp_dir, ik_trc_filename)

            # 全行NaNだったマーカーを IK 対象から除外
            if excluded_markers:
                logger.warning(
                    f"[{study_id}_{subject_id}_{session_id}] "
                    f"全行NaNのため除外されたマーカー: {excluded_markers}"
                )
                excluded_upper = {m.upper() for m in excluded_markers}
                marker_df.loc[
                    marker_df["marker"].str.upper().isin(excluded_upper), "ik_apply"
                ] = False

            # --- 3. IK XML作成 ---
            
            ik_result_filename = "ik_result.mot"
            ik_result_fullpath = os.path.join(temp_dir, ik_result_filename)
            
            ik_values = {
                "name": f"{study_id}_{subject_id}_{session_id}",
                "model_file": local_model_fullpath,      
                "marker_file": ik_trc_fullpath,    
                "output_motion_file": ik_result_fullpath, 
                "results_directory": temp_dir,
                "marker_tasks_df": marker_df[["marker", "ik_apply", "ik_weight"]],
                "time_range_start": 0.0, 
                "time_range_end": 1000.0,
                "setup_name": "ik_setup.xml" # for provenance
            }
            
            setup_ik_path = os.path.join(temp_dir, "setup_ik.xml")
            returned_params_ik = make_xml.update_ik(
                os.path.join(template_dir, "ik_setup.xml"),
                setup_ik_path,
                ik_values
            )
            
            # IDセットアップは後で生成（GRFセットアップ後）
            # ここではパスだけ初期化しておく
            id_result_filename = "inverse_dynamics.sto"
            id_result_fullpath = os.path.join(temp_dir, id_result_filename)
            setup_id_path = None
            returned_params_id = None
            
            # IK Provenance用にパス情報を更新
            # 1. model_file を一時パスから共有パスへ書き換え
            returned_params_ik['model_file'] = shared_model_path
            
            # 2. Mocoモデルの永続化先パスを定義 ({run_id}/{unique_subj_id}_moco.osim)
            shared_model_dir = os.path.dirname(shared_model_path)
            moco_model_name = f"{study_id}_{subject_id}_moco.osim"
            returned_params_ik['moco_model_file'] = os.path.join(shared_model_dir, moco_model_name)
            
            params_list.append(returned_params_ik)
        else:
            pass
        
        # --- 4. GRF & EMG データ準備 ---
        # GRF
        query = f"""
        SELECT unit, time, channel, value
        FROM iceberg_scan('{_warehouse_path}/warehouse/raw_data', allow_moved_paths = true)
        WHERE lower(sensor_type) = 'force-plate' 
          AND study_id = '{study_id}' 
          AND subject_id = '{subject_id}' 
          AND session_id = '{session_id}'
        """
        df = con.sql(query).fetchdf()
        time_start = df["time"].min()
        time_end = df["time"].max()
        grf_xml_path = os.path.join(temp_dir, "grf.xml")
        if not df.empty:
            # Some datasets store analog units in channel metadata instead of raw_data.unit.
            # When unit is entirely NULL, pivot only by channel to avoid an empty table.
            if "unit" in df.columns and df["unit"].notna().any():
                exloads_pivot = df.pivot_table(values='value', index='time', columns=['channel', 'unit'])
            else:
                exloads_pivot = df.pivot_table(values='value', index='time', columns='channel')
            
            query = f"""
            SELECT physical_quantity, x_positive, y_positive, z_positive
            FROM iceberg_scan('{_warehouse_path}/warehouse/dim_coordinate', allow_moved_paths = true)
            WHERE study_id = '{study_id}' AND lower(sensor_type) = 'force-plate' 
            """
            coord_def = con.sql(query).fetchdf()
            
            query_meta_grf = f"""
            SELECT column_name, side, physical_quantity, unit
            FROM iceberg_scan('{_warehouse_path}/warehouse/dim_channels', allow_moved_paths = true)
            WHERE study_id = '{study_id}' AND lower(sensor_type) = 'force-plate' 
            """
            grf_meta_df = con.sql(query_meta_grf).fetchdf()
            
            
            #第一優先はsession_infoのサンプリングレート、もし中身がNoneならdevice_tableの情報をとりにいく
            query = f"""
            WITH target_session AS (
                SELECT force_sampling
                FROM iceberg_scan('/workspace/warehouse/warehouse/session_info', allow_moved_paths = true)
                WHERE study_id = '{study_id}' 
                AND subject_id = '{subject_id}' 
                AND session_id = '{session_id}' 
            ),
            target_device AS (
                SELECT samplingrate_Hz
                FROM iceberg_scan('/workspace/warehouse/warehouse/device_table', allow_moved_paths = true)
                WHERE study_id = '{study_id}' 
                AND type = 'Force-plate'
                LIMIT 1
            )
            SELECT COALESCE(
                (SELECT force_sampling FROM target_session),
                (SELECT samplingrate_Hz FROM target_device)
            )
            """
            result = con.sql(query).fetchone()
            force_fs = int(result[0]) if result and result[0] is not None else None
                        
            # --- Estimate GRF sides if missing ---
            _grf_cfg = (osim_cfg or {}).get("grf_side_check", {})
            if "side" in grf_meta_df.columns:
                normalized_side = grf_meta_df["side"].apply(
                    lambda v: None if pd.isna(v) else str(v).strip().lower()
                )
                side_missing = normalized_side.isna().any() or normalized_side.isin(["", "nan", "none"]).any()
            else:
                side_missing = True
            if _grf_cfg.get("enabled", False) or side_missing:
                try:
                    from harmo.analysis.grf_side_check import estimate_side_for_all_plates

                    v_axis = 'z' # 今はハードコートしているから、修正の必要あり
                    force_coord = coord_def[coord_def['physical_quantity'].str.lower().str.contains('force|grf')]
                    if not force_coord.empty:
                        for ax in ['x', 'y', 'z']:
                            val = str(force_coord.iloc[0][f'{ax}_positive']).lower()
                            if 'upward' in val or 'upright' in val or 'downward' in val or 'inferior' in val:
                                v_axis = ax
                                break

                    grf_meta_df = estimate_side_for_all_plates(
                        exloads_pivot,
                        unify,
                        grf_meta_df,
                        model_path=local_model_fullpath,
                        threshold=_grf_cfg.get("threshold", 20.0),
                        vertical_axis=v_axis,
                        marker_y_idx=1,
                    )
                except Exception as exc:
                    logger.warning(f"[{study_id}_{subject_id}_{session_id}] GRF side estimation failed: {exc}")

            output_grf_filename = "externalloads.mot"
            col_axis = _column_coord(study_id)
            mot_columns = db2osim.ex_mot(data=exloads_pivot,  metadata=grf_meta_df,  sampling_rate=force_fs,  coord_sys_definition=coord_def,  dir=temp_dir,  output_file_name=output_grf_filename, cutoff_freq=6, col_axis = col_axis)
            has_right = ("ground_force_r_vx" in mot_columns) if mot_columns is not None else False
            has_left  = ("ground_force_l_vx" in mot_columns) if mot_columns is not None else False
            grf_mot_fullpath = os.path.join(temp_dir, output_grf_filename)
            
            # --- GRF L/R side verification ---
            _grf_cfg = (osim_cfg or {}).get("grf_side_check", {})
            if _grf_cfg.get("enabled", False) and ik_trc_fullpath and os.path.isfile(ik_trc_fullpath):
                try:
                    from harmo.analysis.grf_side_check import verify_and_fix_grf
                    grf_check = verify_and_fix_grf(grf_mot_fullpath, ik_trc_fullpath, model_path=local_model_fullpath, threshold=_grf_cfg.get("threshold", 20.0),)
                    if grf_check["status"] == "swapped":
                        logger.warning(
                            f"[{study_id}_{subject_id}_{session_id}] GRF L/R swap detected and corrected (swap_votes={grf_check['swap_votes']}, keep_votes={grf_check['keep_votes']})")
                except Exception as exc:
                    logger.warning(f"[{study_id}_{subject_id}_{session_id}] GRF side check failed: {exc}")
            
            grf_updates = {"datafile": grf_mot_fullpath, "kinematics_lowpass_cutoff": 6.0, "setup_name": "grf.xml"}
            # 片脚ずつデータ欠損の確認
            if has_right:
                grf_updates["ground_force_r_v"] = "ground_force_r_v"
            else:
                grf_updates["right_leg"] = "delete"  # データがないのでXMLから削除
            if has_left:
                grf_updates["ground_force_l_v"] = "ground_force_l_v"
            else:
                grf_updates["left_leg"] = "delete"   # データがないのでXMLから削除
            returned_params_grf = make_xml.update_grf_xml(os.path.join(template_dir, "grf.xml"), grf_xml_path, grf_updates)
            params_list.append(returned_params_grf)
            
            # GRFセットアップが完了したら、IDセットアップを生成
            id_values = {
                "name": f"{study_id}_{subject_id}_{session_id}",
                "results_directory": temp_dir,
                "model_file": local_model_fullpath,      
                "output_gen_force_file": id_result_fullpath,
                "coordinates_file": ik_result_fullpath,
                "external_loads_file": grf_xml_path,  # GRFファイルを指定
                "time_range_start": time_start, 
                "time_range_end": time_end,
                "setup_name": "id_setup.xml"  # for provenance
            }
            
            setup_id_path = os.path.join(temp_dir, "setup_id.xml")
            returned_params_id = make_xml.update_id(os.path.join(template_dir, "id_setup.xml"), setup_id_path, id_values)
            
            # ID Provenance用にパス情報を更新
            returned_params_id['model_file'] = shared_model_path
            params_list.append(returned_params_id)
        else:
            pass 

        # EMG
        query_emg = f"""
        SELECT time, channel, value
        FROM iceberg_scan('{_warehouse_path}/warehouse/raw_data', allow_moved_paths = true)
        WHERE sensor_type = 'Surface-EMG' AND study_id='{study_id}' AND subject_id='{subject_id}' AND session_id='{session_id}'
        """
        df_emg = con.sql(query_emg).fetchdf()
        if not df_emg.empty:
            emg_pivot = df_emg.pivot_table(values='value', index='time', columns='channel')
            #第一優先はsession_infoのサンプリングレート、もし中身がNoneならdevice_tableの情報をとりにいく
            query = f"""
            WITH target_session AS (
                SELECT emg_sampling
                FROM iceberg_scan('/workspace/warehouse/warehouse/session_info', allow_moved_paths = true)
                WHERE study_id = '{study_id}' 
                AND subject_id = '{subject_id}' 
                AND session_id = '{session_id}' 
            ),
            target_device AS (
                SELECT samplingrate_Hz
                FROM iceberg_scan('/workspace/warehouse/warehouse/device_table', allow_moved_paths = true)
                WHERE study_id = '{study_id}' 
                AND type = 'Surface-EMG'
            )
            SELECT COALESCE(
                (SELECT emg_sampling FROM target_session),
                (SELECT samplingrate_Hz FROM target_device)
            )
            """
            result = con.sql(query).fetchone()
            emg_fs = int(result[0]) if result and result[0] is not None else None
                
            emg_sto_path = os.path.join(temp_dir, "processed_emg.sto")
            db2osim.emg(emg_pivot, sampling_rate=emg_fs, dir=temp_dir, output_file_name="processed_emg.sto")
        else:
            pass
        
        # パス返却
        return {
            "model": local_model_fullpath,
            "trc": ik_trc_fullpath,
            "mot": grf_mot_fullpath,
            "emg": emg_sto_path,
            "ik_setup": setup_ik_path,
            "id_setup": setup_id_path,
            "id_result": id_result_fullpath,
            "grf_xml": grf_xml_path,
            "ik_result": ik_result_fullpath,
            "rra_setup_template": os.path.join(template_dir, "rra_setup_1.xml"),
            "rra_actuators_template": os.path.join(template_dir, "rra_actuators.xml"),
            "rra_task_template": os.path.join(template_dir, "rra_task.xml"),
            "params_list": params_list # Return the collected parameters
        }
        
    finally:
        con.close()
