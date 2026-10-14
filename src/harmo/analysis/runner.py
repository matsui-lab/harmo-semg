from __future__ import annotations

import os
import shutil
import pandas as pd
import tempfile
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import reduce
import traceback
import json
import duckdb
from datetime import datetime
from pathlib import Path
import argparse
from harmo.analysis import data_prep as data_loader
from harmo.analysis import osim_bridge as run_osim
from harmo.analysis import result_writer as osim2db
from harmo.provenance import tracker as provenance_writer
from harmo.analysis.bids_like_ingest import ingest_bids_like_data
from harmo.warehouse.connection import get_warehouse_path, get_spark_session
from harmo.g6_execution import (
    build_execution_plan,
    enrich_validation,
    sha256_file,
    source_map_from_audit,
)
from harmo.opensim_sources import validate_opensim_source_bundle
from harmo.validation_study import load_registry

# === 設定 ===
_OSIM_DIR = os.environ.get('HARMO_OSIM_DIR', '/workspace/src/harmo/osim')
TEMPLATE_DIR = os.path.join(_OSIM_DIR, "setup_sample")
SHARED_MODEL_DIR = os.path.join(_OSIM_DIR, "model")
MASTER_CSV = os.environ.get('HARMO_MASTER_CSV', '/workspace/src/harmo/osim/mapping/model_mapping_master.csv')  #モデルとSENIAM筋肉マッピング定義
AGGREGATED_RESULTS_DIR = os.path.join(_OSIM_DIR, "all_results")  # 全トライアル結果の集約ディレクトリ
LOG_DIR = os.path.join(_OSIM_DIR, "logs")  # ログファイルの格納ディレクトリ


def _is_static_task(task_id) -> bool:
    """task_id の型揺れ(0, 0.0, '0', '0.0')を吸収して静的試行を判定する。"""
    try:
        value = float(task_id)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and abs(value - 0.0) < 1e-9


def _is_standing_session(hed_ontology) -> bool:
    """session_hed の hed_ontology が (Standing) かどうかを判定する。"""
    return str(hed_ontology).strip() == '(Standing)'


def _retain_g6_scaled_model(model_path, subject_id):
    """Retain a content-addressed model so resume never aliases model versions."""
    digest = sha256_file(Path(model_path))
    filename = "study31_{}_scaled_model_{}.osim".format(
        subject_id, digest[:16]
    )
    retained = os.path.join(AGGREGATED_RESULTS_DIR, filename)
    os.makedirs(AGGREGATED_RESULTS_DIR, exist_ok=True)
    if not os.path.isfile(retained):
        shutil.copy2(model_path, retained)
    if sha256_file(Path(retained)) != digest:
        raise ValueError("retained G6 scaled model checksum mismatch")
    return retained


def process_with_run_id(trial_meta, run_id, osim_cfg=None, g6_context=None):
    # ここで本来の解析処理を呼び出す
    return process_single_analysis(
        trial_meta, run_id, osim_cfg=osim_cfg, g6_context=g6_context
    )


def save_params_to_csv(params_list, trial_meta, run_id, output_dir, file_suffix):
    """
    収集したパラメータをosim_handlerの形式でCSVに保存する
    """
    if not params_list:
        return

    flat_params = []
    for params_dict in params_list:
        setup_name = params_dict.get("setup_name", "unknown_setup")
        
        for key, value in params_dict.items():
            # setup_nameはキーではないのでスキップ
            if key == "setup_name":
                continue

            # DataFrameはJSON文字列に変換
            if isinstance(value, pd.DataFrame):
                value_str = value.to_json(orient='records')
            else:
                value_str = str(value)

            record = {
                "study_id": trial_meta.get('study_id'),
                "subject_id": trial_meta.get('subject_id'),
                "session_id": trial_meta.get('session_id'),
                "run_id": run_id,
                "setup_name": setup_name,
                "key": key,
                "value": value_str
            }
            flat_params.append(record)

    if not flat_params:
        return

    df = pd.DataFrame(flat_params)
    
    # ファイル名を一意にする
    unique_id = f"{trial_meta['study_id']}_{trial_meta['subject_id']}_{trial_meta.get('session_id', 'scaling')}"
    output_filename = f"{unique_id}_{run_id}_{file_suffix}_params.csv"
    output_path = os.path.join(output_dir, output_filename)
    
    df.to_csv(output_path, index=False)


def move_and_rename_moco_logs(study_id, subject_id, session_id, run_id, search_dir=None):
    """
    MocoWithEMG.log と MocoWithOutEMG.log をリネームして LOG_DIR/{run_id}/ に移動する。
    search_dir: ログファイルを探すディレクトリ。NoneならCWDを使う。
    """
    run_log_dir = os.path.join(LOG_DIR, run_id)
    os.makedirs(run_log_dir, exist_ok=True)
    base = search_dir or "."
    
    # MocoWithEMG.log (大文字) または mocowithemg.log (小文字) をチェック
    if os.path.exists(os.path.join(base, "MocoWithEMG.log")):
        src = os.path.join(base, "MocoWithEMG.log")
    elif os.path.exists(os.path.join(base, "mocowithemg.log")):
        src = os.path.join(base, "mocowithemg.log")
    else:
        src = None
    
    if src:
        new_name = f"{study_id}_{subject_id}_{session_id}_MocoWithEMG.log"
        shutil.move(src, os.path.join(run_log_dir, new_name))
    
    # MocoWithOutEMG.log (大文字) または mocowithoutemg.log (小文字) をチェック
    if os.path.exists(os.path.join(base, "MocoWithOutEMG.log")):
        src = os.path.join(base, "MocoWithOutEMG.log")
    elif os.path.exists(os.path.join(base, "mocowithoutemg.log")):
        src = os.path.join(base, "mocowithoutemg.log")
    else:
        src = None
    
    if src:
        new_name = f"{study_id}_{subject_id}_{session_id}_MocoWithOutEMG.log"
        shutil.move(src, os.path.join(run_log_dir, new_name))


def move_and_rename_opensim_log(run_id):
    """
    opensim.log をリネームして LOG_DIR/{run_id}/ に移動する
    """
    run_log_dir = os.path.join(LOG_DIR, run_id)
    os.makedirs(run_log_dir, exist_ok=True)
    
    if os.path.exists("opensim.log"):
        shutil.move("opensim.log", os.path.join(run_log_dir, "opensim.log"))



# =================================================
# Phase 1: Model Generation (Scaling)
# =================================================
def get_all_trials_from_db(study_id):
    """
    DBからトライアル情報を取得し、辞書のリストとして返す関数
    """
    con = duckdb.connect()
    con.sql("INSTALL iceberg; LOAD iceberg;")
    
    _warehouse_path = get_warehouse_path()
    try:
        #必要なカラムをSELECTする
        query = f"""
        SELECT DISTINCT study_id, subject_id, session_id, hed_ontology
        FROM iceberg_scan('{_warehouse_path}/warehouse/session_hed', allow_moved_paths = true)
        WHERE study_id = '{study_id}'
        """
        # 1. DataFrameとして取得
        df = con.sql(query).fetchdf()
        
        # 型変換しておく
        df['study_id'] = df['study_id'].astype(str)
        df['subject_id'] = df['subject_id'].astype(str)
        df['session_id'] = df['session_id'].astype(str)
        # 2. 辞書のリストに変換
        # 'records' を指定すると [{'col1': val1, 'col2': val2}, ...] の形になります
        trials_list = df.to_dict('records')
        
        return trials_list

    finally:
        con.close()
        
def process_model_generation(group_meta, run_id, osim_cfg=None, source_row=None):
    """
    1つの被験者グループに対し、代表モデルを作成して共有フォルダに保存する
    group_meta: (study_id, subject_id, trials_list_of_dicts)
    run_id: このバッチ実行のユニークID
    osim_cfg: osim_analysis_config.yaml の読み込み結果 (dict)
    """
    study_id, subject_id, trials = group_meta
    unique_subj_id = f"{study_id}_{subject_id}"
    
    print(f"=== Phase 1: Generating Model for {unique_subj_id} ===")
    
    if not trials:
        print(f"  [Warning] No trial metadata found for {unique_subj_id}; skipping model generation.")
        return False
    
    try:
        target_trial = (
            trials[0]
            if source_row is not None
            else data_loader.select_scaling_trial(trials, osim_cfg=osim_cfg)
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            dst_model = os.path.join(
                SHARED_MODEL_DIR, f"{run_id}/{unique_subj_id}_scaled.osim"
            )
            os.makedirs(os.path.dirname(dst_model), exist_ok=True)
            if source_row is not None and source_row["source_kind"] == (
                "traceable-subject-specific-model"
            ):
                source_model = source_row["artifacts_by_role"]["scaled_model"]
                source_model_path = Path(source_model["absolute_path"])
                if sha256_file(source_model_path) != source_model["observed_sha256"]:
                    raise ValueError(
                        "external scaled model checksum changed after intake"
                    )
                shutil.copy2(source_model["absolute_path"], dst_model)
                if sha256_file(Path(dst_model)) != source_model["observed_sha256"]:
                    raise ValueError("copied external scaled model checksum mismatch")
                source_params = {
                    "setup_name": "external_scaling_source",
                    "source_kind": source_row["source_kind"],
                    "static_trial_id": source_row["provenance"]["static_trial_id"],
                    "static_trial_sha256": source_row["artifacts_by_role"][
                        "static_trial"
                    ]["observed_sha256"],
                    "scaled_model_sha256": source_model["observed_sha256"],
                }
                save_params_to_csv(
                    [source_params],
                    target_trial,
                    run_id,
                    AGGREGATED_RESULTS_DIR,
                    "scaling",
                )
                retained_model = _retain_g6_scaled_model(dst_model, subject_id)
                data_loader.export_emg_mapping(study_id, SHARED_MODEL_DIR)
                return retained_model

            # Scaling用ファイルの準備
            external_static = (
                source_row["artifacts_by_role"]["static_trial"]
                if source_row is not None
                else None
            )
            scaling_data = data_loader.prepare_scaling_data(
                target_trial,
                temp_dir,
                TEMPLATE_DIR,
                osim_cfg=osim_cfg,
                external_static_source=external_static,
            )
            if not scaling_data:
                print(f"  [Warning] Scaling data preparation failed for {unique_subj_id}; skipping model generation.")
                return False

            paths = {k: v for k, v in scaling_data.items() if k != 'params_list'}
            params_list = scaling_data.get('params_list', [])

            # パラメータをCSVに保存
            save_params_to_csv(params_list, target_trial, run_id, AGGREGATED_RESULTS_DIR, "scaling")
            
            # Scaling実行
            run_osim.run_scaling_process(paths, osim_cfg=osim_cfg)
            
            # 結果モデルを共有フォルダにコピー (カタログ登録)
            # 保存名: {study_id}_{subject_id}_{run_id}_scaled.osim
            src_model = paths['output_model']
            shutil.copy2(src_model, dst_model)
            retained_model = (
                _retain_g6_scaled_model(dst_model, subject_id)
                if source_row is not None
                else None
            )
            
            # ついでにEMG Input Mapもこのタイミングで出力しておく (1回で良いので)
            data_loader.export_emg_mapping(study_id, SHARED_MODEL_DIR)
            return retained_model or True

    except Exception as e:
        print(f"  [Error] Model generation failed for {unique_subj_id}: {e}")
        traceback.print_exc()
        return False


# =================================================
# Phase 2: Analysis Execution (Parallel)
# =================================================
def process_single_analysis(trial_meta, run_id, osim_cfg=None, g6_context=None):
    """
    1トライアルの解析を実行する (Worker関数)
    結果ファイルは集約ディレクトリにコピーする（DB登録はしない）
    trial_meta: トライアルメタデータ
    run_id: このバッチ実行のユニークID
    osim_cfg: osim_analysis_config.yaml の読み込み結果 (dict)
    """
    study_label = "study31" if g6_context is not None else trial_meta['study_id']
    unique_id = f"{study_label}_{trial_meta['subject_id']}_{trial_meta['session_id']}"
    unique_subj_id = f"{trial_meta['study_id']}_{trial_meta['subject_id']}"
    
    print(f"=== Phase 2 START: {unique_id} ===")
    
    # 共有モデルの確認
    shared_model_path = os.path.join(SHARED_MODEL_DIR, f"{run_id}/{unique_subj_id}_scaled.osim")
    if not os.path.exists(shared_model_path):
        return f"{unique_id}: FAILED (No Scaled Model)"

    # マッピング情報の読み込み (共有フォルダにあるはず)
    input_map_path = os.path.join(SHARED_MODEL_DIR, f"study{trial_meta['study_id']}_input_map.csv")
    if os.path.exists(input_map_path):
        input_map_df = pd.read_csv(input_map_path)
    else:
        # なければデフォルトかエラー (ここでは空を仮定)
        input_map_df = pd.DataFrame(columns=['original_name', 'seniam_name', 'side'])
    
    master_model_df = pd.read_csv(MASTER_CSV, index_col='seniam_name')

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # 並列実行時にOpenSimのログ(opensim.log等)が競合しないよう、
            # ワーカーごとにCWDをtemp_dirに切り替える
            prev_cwd = os.getcwd()
            os.chdir(temp_dir)
            try:
                # 解析用ファイルの準備 (モデルはコピーされる)
                analysis_data = data_loader.prepare_analysis_data(trial_meta, temp_dir, TEMPLATE_DIR, shared_model_path, run_id, osim_cfg=osim_cfg)
                # パスとパラメータリストを分割
                paths = {k: v for k, v in analysis_data.items() if k != 'params_list'}
                paths["run_id"] = run_id
                params_list = analysis_data.get('params_list', [])
                
                # Mocoモデルの保存先パスを取得
                persistent_moco_path = None
                for p in params_list:
                    if 'moco_model_file' in p:
                        persistent_moco_path = p['moco_model_file']

                # パラメータをCSVに保存
                save_params_to_csv(params_list, trial_meta, run_id, AGGREGATED_RESULTS_DIR, "analysis")
                # 解析実行 (IK -> Analysis)
                run_osim.run_analysis_pipeline(paths, input_map_df, master_model_df, osim_cfg=osim_cfg)

                if g6_context is not None:
                    validation_path = os.path.join(temp_dir, "validation.json")
                    if not os.path.isfile(validation_path):
                        raise RuntimeError("G6 validation.json was not created")
                    enrich_validation(
                        Path(validation_path),
                        trial_meta,
                        run_id,
                        g6_context["source_audit"],
                        g6_context["source_row"],
                        Path(
                            g6_context["scaled_model_path"]
                        ),
                    )
                
                # Mocoモデルを共有フォルダ(persistent_moco_path)に保存
                if persistent_moco_path:
                    moco_temp_path = os.path.join(temp_dir, "model_with_moco_actuators.osim")
                    if os.path.exists(moco_temp_path):
                        os.makedirs(os.path.dirname(persistent_moco_path), exist_ok=True)
                        shutil.copy(moco_temp_path, persistent_moco_path)

                # tmp内でファイルをリネーム（manage用）
                # 1. Targets ファイルのリネーム
                targets = {
                    "trackingSolution.sto": f"{unique_id}_MocoInverse_trackingSolution.sto",
                    "effortSolution.sto": f"{unique_id}_MocoInverse_effortSolution.sto", 
                    "ik_result.mot": f"{unique_id}_InverseKinematics_InverseKinematics.mot",
                    "inverse_dynamics.sto": f"{unique_id}_InverseDynamics_InverseDynamics.sto",
                    "workflow.json": f"{unique_id}_workflow.json",
                    "validation.json": f"{unique_id}_validation.json",
                    "rra_kinematics.sto": f"{unique_id}_RRA_Kinematics_q.sto",
                    "rra_actuation_force.sto": f"{unique_id}_RRA_Actuation_force.sto",
                    "rra_adjusted_model.osim": f"{unique_id}_RRA_AdjustedModel.osim",
                    "rra_actuators.xml": f"{unique_id}_RRA_Actuators.xml",
                    "rra_task.xml": f"{unique_id}_RRA_Tasks.xml",
                    "setup_rra_pass1.xml": f"{unique_id}_RRA_Setup_pass1.xml",
                    "setup_rra_pass2.xml": f"{unique_id}_RRA_Setup_pass2.xml",
                    "setup_rra_pass3.xml": f"{unique_id}_RRA_Setup_pass3.xml",
                    "rra_adjusted_pass1.osim": f"{unique_id}_RRA_AdjustedModel_pass1.osim",
                    "rra_adjusted_pass2.osim": f"{unique_id}_RRA_AdjustedModel_pass2.osim",
                    "rra_adjusted_pass3.osim": f"{unique_id}_RRA_AdjustedModel_pass3.osim",
                    "moco_settings.json": f"{unique_id}_moco_settings.json",
                    "reserve_analysis.json": f"{unique_id}_reserve_analysis.json",
                    "coord_and_emgdriven.sto": f"{unique_id}_MocoInverse_coord_and_emgdriven.sto"
                }
                for src, dst in targets.items():
                    s_p = os.path.join(temp_dir, src)
                    if os.path.exists(s_p):
                        d_p = os.path.join(temp_dir, dst)
                        shutil.move(s_p, d_p)
                        
                # 2. AnalyzeToolの結果をリネーム (analyze_ で始まるファイルを一括処理)
                for filename in os.listdir(temp_dir):
                    if filename.startswith("analyze_"):
                        new_filename = filename.replace("analyze_", f"{unique_id}_", 1)
                        old_path = os.path.join(temp_dir, filename)
                        new_path = os.path.join(temp_dir, new_filename)
                        shutil.move(old_path, new_path)
                
                # 入力ファイルも後段の処理のためプリフィックスを付けておく
                input_files_to_rename = {
                    "ik.trc": f"{unique_id}_ik.trc",
                    "externalloads.mot": f"{unique_id}_externalloads.mot",
                    "processed_emg.sto": f"{unique_id}_processed_emg.sto",
                    "coord_and_emgdriven.sto": f"{unique_id}_coord_and_emgdriven.sto"
                }
                for src, dst in input_files_to_rename.items():
                    s_p = os.path.join(temp_dir, src)
                    if os.path.exists(s_p):
                        d_p = os.path.join(temp_dir, dst)
                        shutil.move(s_p, d_p)

                # 3. 集約ディレクトリにコピー（DB登録は後で一括実行）
                os.makedirs(AGGREGATED_RESULTS_DIR, exist_ok=True)
                _copy_count = 0
                for filename in os.listdir(temp_dir):
                    src_path = os.path.join(temp_dir, filename)
                    dst_path = os.path.join(AGGREGATED_RESULTS_DIR, filename)
                    if os.path.isfile(src_path):
                        shutil.copy(src_path, dst_path)
                        _copy_count += 1
                print(f"=== Phase 2 SUCCESS: {unique_id} ===")
                
                # Mocoログを移動・リネーム (temp_dirから探す)
                move_and_rename_moco_logs(trial_meta['study_id'], trial_meta['subject_id'], trial_meta['session_id'], run_id, search_dir=temp_dir)
                
                return f"{unique_id}: IK~AnalyzeTool成功。結果をディレクトリに集約"
            finally:
                os.chdir(prev_cwd)

    except Exception as e:
        tb_str = traceback.format_exc()
        print("\n" + "!"*60)
        print(f"【検証用】エラー発生箇所: {unique_id}")
        print(f"エラー内容: {e}")
        traceback.print_exc()
        print("!"*60 + "\n")
        # エラー情報をファイルに保存
        error_info = {
            "unique_id": unique_id,
            "study_id": trial_meta.get("study_id"),
            "subject_id": trial_meta.get("subject_id"),
            "session_id": trial_meta.get("session_id"),
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback": tb_str,
        }
        try:
            os.makedirs(AGGREGATED_RESULTS_DIR, exist_ok=True)
            error_path = os.path.join(AGGREGATED_RESULTS_DIR, f"{unique_id}_error.json")
            with open(error_path, 'w', encoding='utf-8') as f:
                json.dump(error_info, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # エラー保存自体の失敗は無視
        raise  # 呼び出し元 (executor) にエラーを伝播させる


def register_provenance_bulk(aggregated_dir, run_id, spark=None):
    """
    集約ディレクトリ内のパラメータファイルやログをスキャンし、
    provenance_writerを使ってIcebergテーブルに登録する
    """
    print("\n=== Phase 4: Registering Provenance Data ===")
    _created_spark = spark is None
    if _created_spark:
        spark = provenance_writer.get_spark_session()

    # テーブルごとのDataFrameを集約して、Icebergへの書き込み回数を減らす
    batched_dfs = {
        "analysis_runs": [],
        "marker_task_parameters": [],
        "muscle_parameters": [],
        "workflow_steps": [],
        "session_specific_parameters": [],
        "osim_validation": [],
        "emg_tracking_metrics": [],
        "reserve_actuators": [],
        "ik_marker_errors": [],
    }
    
    try:
        # --- ファイルのスキャンと代表ファイルの特定 ---
        prov_csv = os.path.join(aggregated_dir, f"{run_id}_provenance.csv")
        analysis_files = []
        scaling_files = []
        with os.scandir(aggregated_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                name = entry.name
                if run_id not in name:
                    continue
                if name.endswith("_analysis_params.csv"):
                    analysis_files.append(name)
                elif name.endswith("_scaling_params.csv"):
                    scaling_files.append(name)
        analysis_files.sort()
        scaling_files.sort()

        if not analysis_files or not scaling_files or not os.path.exists(prov_csv):
            print("  [Warning] Essential provenance files are missing. Skipping registration.")
            return

        # Subject ID -> Scaling File のマッピング作成
        scaling_map = {sf.split('_')[1]: os.path.join(aggregated_dir, sf) for sf in scaling_files}

        # --- run_id単位で不変のテーブルを登録 (1回だけ実行) ---
        # 代表として最初の分析ファイルを選択
        first_af = analysis_files[0]
        first_subj = first_af.split('_')[1]
        
        rep_analysis_csv = os.path.join(aggregated_dir, first_af)
        rep_scaling_csv = scaling_map.get(first_subj)

        if rep_scaling_csv:
            # 1. analysis_runs
            df_ar = provenance_writer.process_analysis_runs(spark, prov_csv, rep_scaling_csv)
            if df_ar is not None:
                batched_dfs["analysis_runs"].append(df_ar)
            
            # 2. marker_task_parameters
            df_mtp = provenance_writer.process_marker_task_parameters(spark, rep_scaling_csv, rep_analysis_csv)
            if df_mtp is not None:
                batched_dfs["marker_task_parameters"].append(df_mtp)

            # ファイル名のサフィックスを除去して representative unique_id を復元
            rep_unique_id = first_af.replace(f"_{run_id}_analysis_params.csv", "")
            rep_moco_json = os.path.join(aggregated_dir, f"{rep_unique_id}_moco_settings.json")
            rep_workflow_json = os.path.join(aggregated_dir, f"{rep_unique_id}_workflow.json")

            # 3. muscle_parameters
            if os.path.exists(rep_moco_json):
                df_mp = provenance_writer.process_muscle_parameters(spark, rep_moco_json, run_id)
                if df_mp is not None:
                    batched_dfs["muscle_parameters"].append(df_mp)
            
            # 4. workflow_steps
            if os.path.exists(rep_workflow_json):
                df_ws = provenance_writer.process_workflow_steps(spark, rep_workflow_json, run_id)
                if df_ws is not None:
                    batched_dfs["workflow_steps"].append(df_ws)
        else:
            print(f"  [Warning] Could not find scaling file for representative subject {first_subj}. Skipping run-invariant data.")

        # --- session_id単位で変動するテーブルを登録 (トライアルごとに実行) ---
        for af in analysis_files:
            try:
                unique_id = af.replace(f"_{run_id}_analysis_params.csv", "")
                analysis_csv_path = os.path.join(aggregated_dir, af)

                moco_json = os.path.join(aggregated_dir, f"{unique_id}_moco_settings.json")
                validation_json = os.path.join(aggregated_dir, f"{unique_id}_validation.json")
                reserve_analysis_json = os.path.join(aggregated_dir, f"{unique_id}_reserve_analysis.json")

                # 5. session_specific_parameters
                if os.path.exists(moco_json):
                    df_ssp = provenance_writer.process_session_specific_parameters(spark, analysis_csv_path, moco_json)
                    if df_ssp is not None:
                        batched_dfs["session_specific_parameters"].append(df_ssp)
                
                # 6. osim_validation
                if os.path.exists(validation_json):
                    df_ov = provenance_writer.process_osim_validation(spark, validation_json, reserve_analysis_json, analysis_csv_path, run_id)
                    if df_ov is not None:
                        batched_dfs["osim_validation"].append(df_ov)

                # 7. emg_tracking_metrics (DTW details)
                if os.path.exists(validation_json):
                    df_emg = provenance_writer.process_emg_tracking_metrics(spark, validation_json, analysis_csv_path, run_id)
                    if df_emg is not None:
                        batched_dfs["emg_tracking_metrics"].append(df_emg)

                # 7.5. ik_marker_errors (per-marker IK errors)
                if os.path.exists(validation_json):
                    df_ikm = provenance_writer.process_ik_marker_errors(spark, validation_json, analysis_csv_path, run_id)
                    if df_ikm is not None:
                        batched_dfs["ik_marker_errors"].append(df_ikm)

                # 8. reserve_actuators (新規テーブル)
                if os.path.exists(reserve_analysis_json):
                    df_ra = provenance_writer.process_reserve_actuators(spark, reserve_analysis_json, analysis_csv_path, run_id)
                    if df_ra is not None:
                        batched_dfs["reserve_actuators"].append(df_ra)
                    else:
                        print(f"  [Warning] reserve_actuators DataFrame is empty or None for {unique_id}")
                else:
                    print(f"  [Warning] reserve_analysis.json not found: {reserve_analysis_json}")

            except Exception as e:
                print(f"  [Error] Failed to register session-variant provenance for trial associated with {af}: {e}")
                traceback.print_exc()
                # Continue to the next trial in the loop
            
        partition_map = {
            "analysis_runs": None,
            "marker_task_parameters": "run_id",
            "muscle_parameters": "run_id",
            "workflow_steps": "run_id",
            "session_specific_parameters": "run_id",
            "osim_validation": "run_id",
            "emg_tracking_metrics": "run_id",
            "reserve_actuators": "run_id",
            "ik_marker_errors": "run_id",
        }

        for table_name, dfs in batched_dfs.items():
            if not dfs:
                continue
            merged_df = reduce(lambda d1, d2: d1.unionByName(d2, allowMissingColumns=True), dfs,)
            provenance_writer.write_df_to_iceberg( spark, merged_df, table_name, partition_map[table_name],)

        print("  [Provenance] Registration completed.")
        
    except Exception as e:
        print(f"  [Error] Provenance registration failed: {e}")
        traceback.print_exc()
    finally:
        if _created_spark:
            spark.stop()

# =================================================
# Main Entry Point
# =================================================
def main(
    config_path=None,
    label=None,
    g6_source_manifest=None,
    g6_source_bundle_root=None,
    g6_registry=None,
    g6_ledger=None,
    resume=True,
):
    # === CLI fallback (直接実行時) ===
    if config_path is None or label is None:
        parser = argparse.ArgumentParser(description="OpenSim batch analysis pipeline")
        parser.add_argument("--config", type=str, required=True, help="Path to config.yaml")
        parser.add_argument("--label", type=str, default="default", help="Label for this batch run (used in run_id)")
        parser.add_argument("--g6-source-manifest")
        parser.add_argument("--g6-source-bundle-root")
        parser.add_argument("--g6-registry", default="paper/validation_registry.yaml")
        parser.add_argument("--g6-ledger", default="paper/results/validation/opensim_quality.json")
        parser.add_argument("--no-resume", action="store_true")
        args = parser.parse_args()
        if config_path is None:
            config_path = args.config
        if label is None:
            label = args.label
        g6_source_manifest = g6_source_manifest or args.g6_source_manifest
        g6_source_bundle_root = g6_source_bundle_root or args.g6_source_bundle_root
        g6_registry = g6_registry or args.g6_registry
        g6_ledger = g6_ledger or args.g6_ledger
        resume = not args.no_resume

    # === run_id の生成 ===
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    run_id = f"{timestamp}_{label}"
    print(f"\n{'='*70}")
    print(f"Starting Analysis Pipeline with run_id: {run_id}")
    print(f"{'='*70}\n")

    # === 来歴情報の保存 ===
    # OpenSimバージョンの取得と保存
    opensim_version = None
    try:
        import opensim as osim
        opensim_version = osim.GetVersion()
        provenance_data = {"run_id": [run_id], "opensim_version": [opensim_version], "execution_timestamp": [timestamp]}
        provenance_df = pd.DataFrame(provenance_data)
        
        # 集約ディレクトリが存在しない場合は作成
        os.makedirs(AGGREGATED_RESULTS_DIR, exist_ok=True)
        provenance_file_path = os.path.join(AGGREGATED_RESULTS_DIR, f"{run_id}_provenance.csv")
        provenance_df.to_csv(provenance_file_path, index=False)
    
    except ImportError:
        print("  [Warning] OpenSim library not found. Skipping version logging.")
    except Exception as e:
        print(f"  [Warning] Failed to log OpenSim version: {e}")
    
    # 1. config から study_id を取得
    from harmo.config.loader import load_config, get_primary_study_id
    config = load_config(config_path)
    study_id = get_primary_study_id(config.get("definitions", {}))
    if study_id is None:
        print("[Error] config から study_id を取得できませんでした。")
        return
    if g6_source_manifest is not None and str(study_id).lower() not in {
        "31",
        "study31",
    }:
        raise ValueError("G6 execution is restricted to the Study 31 config")

    # osim_analysis_config.yaml の読み込み 
    osim_cfg = None
    try:
        from harmo.analysis.osim_config import load_osim_analysis_config
        config_dir = os.path.dirname(os.path.abspath(config_path))
        osim_analysis_path = os.path.join(config_dir, "osim_analysis_config.yaml")
        # marker_metadata.yaml のパスを config 定義から解決
        marker_meta_path = None
        markers_def = config.get("definitions", {}).get("markers", {})
        if markers_def.get("path"):
            marker_meta_path = os.path.join(config_dir, markers_def["path"])
        osim_cfg = load_osim_analysis_config(
            config_path=osim_analysis_path if os.path.isfile(osim_analysis_path) else None,
            marker_metadata_path=marker_meta_path,
        )
        print("  [Info] osim_analysis_config loaded successfully.")
    except FileNotFoundError:
        if g6_source_manifest is not None:
            raise
        print("  [Info] osim_analysis_config.yaml not found. Using defaults.")
    except Exception as e:
        if g6_source_manifest is not None:
            raise
        print(f"  [Warning] Failed to load osim_analysis_config: {e}. Using defaults.")

    if g6_source_manifest is not None:
        from harmo.analysis.osim_config import resolve_workflow

        workflow = resolve_workflow(osim_cfg or {})
        required_steps = ("IK", "RRA", "ID", "MocoInverse_effort", "AnalyzeTool")
        disabled = [step for step in required_steps if not workflow.get(step)]
        rra_config = ((osim_cfg or {}).get("RRA", {}) or {})
        if disabled:
            raise ValueError(
                "G6 config disables required workflow steps: {}".format(
                    ",".join(disabled)
                )
            )
        if int(rra_config.get("passes", 0)) != 2:
            raise ValueError("G6 requires exactly two RRA passes")
        if rra_config.get("adjust_com_to_reduce_residuals") is not True:
            raise ValueError("G6 requires RRA center-of-mass adjustment")
        if not str(opensim_version or "").startswith("4.5.2"):
            raise RuntimeError(
                "G6 requires runtime-reported OpenSim 4.5.2; observed {!r}".format(
                    opensim_version
                )
            )
        if not hasattr(osim, "RRATool") or not hasattr(osim, "MocoInverse"):
            raise RuntimeError("G6 runtime lacks RRATool or MocoInverse")

    # Optional G6 execution contract. It is validated before any OpenSim work.
    g6_plan = None
    g6_source_audit = None
    g6_sources = None
    pending_g6 = None
    if g6_source_manifest is not None:
        if g6_registry is None or g6_ledger is None:
            raise ValueError("G6 execution requires --g6-registry and --g6-ledger")
        manifest_path = Path(g6_source_manifest).resolve()
        bundle_root = Path(g6_source_bundle_root or manifest_path.parent).resolve()
        registry = load_registry(Path(g6_registry).resolve())
        ledger = json.loads(Path(g6_ledger).resolve().read_text(encoding="utf-8"))
        g6_source_audit = validate_opensim_source_bundle(
            manifest_path,
            bundle_root,
            registry["experiments"]["E"]["subjects"],
        )
        g6_sources = source_map_from_audit(g6_source_audit)
        g6_plan = build_execution_plan(
            registry,
            ledger,
            g6_source_audit,
            Path(AGGREGATED_RESULTS_DIR),
            run_id,
            resume=resume,
        )
        os.makedirs(AGGREGATED_RESULTS_DIR, exist_ok=True)
        plan_path = os.path.join(AGGREGATED_RESULTS_DIR, f"{run_id}_g6_plan.json")
        with open(plan_path, "w", encoding="utf-8") as stream:
            json.dump(g6_plan, stream, indent=2, sort_keys=True)
        if not g6_plan["pass"]:
            raise ValueError("G6 execution plan failed its frozen-contract checks")
        pending_g6 = {
            (row["subject_id"], row["session_id"])
            for row in g6_plan["trials"]
            if row["execution_status"] == "pending"
        }
        print(
            "  [G6] frozen=336 available=330 absent=6 pending={} resumed={}".format(
                g6_plan["counts"]["pending"],
                g6_plan["counts"]["resume_complete"],
            )
        )

    # 2. トライアルリストの取得
    all_trials = get_all_trials_from_db(study_id=study_id)
    if pending_g6 is not None:
        matching = {
            (str(row["subject_id"]), str(row["session_id"])): row
            for row in all_trials
            if (str(row["subject_id"]), str(row["session_id"])) in pending_g6
        }
        missing = sorted(pending_g6 - set(matching))
        if missing:
            raise ValueError(
                "G6 warehouse is missing {} planned dynamic trials".format(len(missing))
            )
        all_trials = [matching[key] for key in sorted(matching)]
    
    # 3. グループ化 (Study, Subject単位)
    df_trials = pd.DataFrame(all_trials)
    groups = (
        df_trials.groupby(['study_id', 'subject_id'])
        if not df_trials.empty
        else []
    )
    
    # ---------------------------------------------
    # Phase 1: モデル生成 (被験者ごと・直列実行推奨)
    # ---------------------------------------------
    g6_scaled_models = {}
    for (study, subj), group in groups:
        # グループ情報をタプルにして渡す
        meta = (study, subj, group.to_dict('records'))
        source_row = g6_sources.get(str(subj)) if g6_sources is not None else None
        model_result = process_model_generation(
            meta, run_id, osim_cfg=osim_cfg, source_row=source_row
        )
        if not model_result:
            raise RuntimeError("subject-specific model generation failed: {}".format(subj))
        if source_row is not None:
            g6_scaled_models[str(subj)] = str(model_result)
        
    # ---------------------------------------------
    # Phase 2: 解析実行 (全トライアル並列実行)
    # ---------------------------------------------
    # リストに戻す
    trials_list = df_trials.to_dict('records')
    
    def _context_for(trial):
        if g6_sources is None:
            return None
        return {
            "source_audit": g6_source_audit,
            "source_row": g6_sources[str(trial["subject_id"])],
            "scaled_model_path": g6_scaled_models[str(trial["subject_id"])],
        }
    # Mocoは内部で並列化(CasADi/IPOPT)されているため、プロセス並列数を増やすと
    # 逆にコンテキストスイッチやリソース競合で遅くなる可能性があるため max_workers=2 程度に抑える
    results_summary = []
    with ProcessPoolExecutor(max_workers=1) as executor:
        futures = {
            executor.submit(
                process_with_run_id,
                trial,
                run_id,
                osim_cfg,
                _context_for(trial),
            ): trial
            for trial in trials_list
        }
        for future in as_completed(futures):
            trial = futures[future]
            uid = f"{trial['study_id']}_{trial['subject_id']}_{trial['session_id']}"
            try:
                result = future.result()
                results_summary.append({"trial": uid, "status": "success", "detail": result})
            except Exception as e:
                tb = traceback.format_exc()
                print(f"\n[Error] Trial {uid} failed: {e}")
                results_summary.append({"trial": uid, "status": "error", "detail": str(e), "traceback": tb})
    
    # 並列実行の結果サマリーを保存
    summary_path = os.path.join(AGGREGATED_RESULTS_DIR, f"{run_id}_execution_summary.json")
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(results_summary, f, indent=2, ensure_ascii=False)
    
    succeeded = [r for r in results_summary if r["status"] == "success"]
    failed = [r for r in results_summary if r["status"] == "error"]
    print(f"\n  Phase 2 Summary: {len(succeeded)} succeeded, {len(failed)} failed out of {len(results_summary)} trials")
    
    # Phase 3 & 4: 今回新規実行した結果のみ一括登録。
    if trials_list:
        _shared_spark = get_spark_session(app_name="harmo-bulk-register")
        try:
            osim2db.ingest_directory_bulk(AGGREGATED_RESULTS_DIR, run_id=run_id, header_table="local.warehouse.osim_header", value_table="local.warehouse.osim_value", spark=_shared_spark,)
            ingest_bids_like_data(input_dir=AGGREGATED_RESULTS_DIR, run_id=run_id, spark=_shared_spark,)
            print("\n" + "="*70)
            register_provenance_bulk(AGGREGATED_RESULTS_DIR, run_id, spark=_shared_spark)
        finally:
            try:
                _shared_spark.stop()
            except Exception:
                pass
    else:
        print("  [Resume] No new trials required registration.")

    if g6_plan is not None:
        final_plan = build_execution_plan(
            registry,
            ledger,
            g6_source_audit,
            Path(AGGREGATED_RESULTS_DIR),
            run_id,
            resume=True,
        )
        final_plan_path = os.path.join(
            AGGREGATED_RESULTS_DIR, f"{run_id}_g6_plan_final.json"
        )
        with open(final_plan_path, "w", encoding="utf-8") as stream:
            json.dump(final_plan, stream, indent=2, sort_keys=True)
        if final_plan["counts"]["resume_complete"] != 330:
            raise RuntimeError(
                "G6 execution incomplete: {} of 330 available trials complete".format(
                    final_plan["counts"]["resume_complete"]
                )
            )
    
    print("Complete: All trials registered.")
    # OpenSimログを移動・リネーム
    move_and_rename_opensim_log(run_id)
    print("="*70)
    """
    try:
        if os.path.exists(AGGREGATED_RESULTS_DIR):
            shutil.rmtree(AGGREGATED_RESULTS_DIR)  # フォルダごと削除
            
        # 次回実行のために、空のディレクトリだけ再作成しておくと親切です
        os.makedirs(AGGREGATED_RESULTS_DIR, exist_ok=True)
        
    except Exception as e:
        print(f"Cleanup failed: {e}")
    """
if __name__ == "__main__":
    main()
