from __future__ import annotations

import os
import json
import argparse
import pandas as pd
from pyspark.sql import SparkSession
from pyspark.sql.functions import to_timestamp, col, lit

from harmo.warehouse.connection import get_spark_session as _warehouse_spark_session

def get_spark_session(warehouse_path: str = None) -> SparkSession:
    """Use the shared local-or-REST Iceberg connection policy."""
    return _warehouse_spark_session(warehouse_path, app_name="harmo-provenance")

def write_df_to_iceberg(spark, df, table_name, partition_col=None):
    """
    Writes a Spark DataFrame to a specified Iceberg table, creating it if necessary.
    If the table exists, automatically adds missing columns (with NULL values) to match the existing schema.
    """
    full_table_name = f"local.warehouse.{table_name}"
    try:
        if spark.catalog.tableExists(full_table_name):
            # 既存テーブルの全列名を取得
            target_columns = spark.table(full_table_name).columns
            
            # データ側に足りない列を NULL で埋める
            for col_name in target_columns:
                if col_name not in df.columns:
                    df = df.withColumn(col_name, lit(None))
            
            # 【重要】テーブルの列順と「完全に同じ」順番に並べ替える
            # これがないと、列数が合っていてもエラーになることがあります
            df = df.select(*target_columns)
            
            # 書き込み実行
            df.writeTo(full_table_name).append()
        else:
            writer = df.writeTo(full_table_name)
            if partition_col:
                writer = writer.partitionedBy(partition_col)
            writer.create()
    except Exception as e:
        print(f"Error writing to {table_name}: {e}")
        raise

def process_analysis_runs(spark, provenance_csv, scaling_params_csv):
    provenance_df = pd.read_csv(provenance_csv)
    scaling_params_df = pd.read_csv(scaling_params_csv)
    
    study_id = scaling_params_df['study_id'].iloc[0]
    studymodel_path = scaling_params_df.loc[scaling_params_df['key'] == 'model_file', 'value'].iloc[0]

    provenance_df['study_id'] = study_id
    provenance_df['basemodel_id'] = ''  # This seems to be empty
    provenance_df['studymodel_path'] = studymodel_path
    
    spark_df = spark.createDataFrame(provenance_df)
    spark_df = spark_df.withColumn("created_at", to_timestamp(col("execution_timestamp"), "yyyyMMdd_HHmm"))
    final_df = spark_df.select("run_id", "study_id", "opensim_version", "basemodel_id", "studymodel_path", "created_at")
    return final_df

def process_session_specific_parameters(spark, analysis_params_csv, moco_json):
    analysis_params_df = pd.read_csv(analysis_params_csv)
    with open(moco_json, 'r') as f:
        moco_data = json.load(f)
    
    # moco_model_file の取得 (存在しない場合は None)
    moco_path_rows = analysis_params_df.loc[analysis_params_df['key'] == 'moco_model_file', 'value']
    moco_model_path = str(moco_path_rows.iloc[0]) if not moco_path_rows.empty else None
    
    # ログファイルのパスを取得 (存在しない場合は None)
    effort_log_file = moco_data.get('effort_log_file')
    tracking_log_file = moco_data.get('tracking_log_file')

    data = {
        'run_id': str(analysis_params_df['run_id'].iloc[0]),
        'study_id': str(analysis_params_df['study_id'].iloc[0]),
        'subject_id': str(analysis_params_df['subject_id'].iloc[0]),
        'session_id': str(analysis_params_df['session_id'].iloc[0]),
        'subj_model_path': str(analysis_params_df.loc[analysis_params_df['key'] == 'model_file', 'value'].iloc[0]),
        'moco_model_path': moco_model_path,
        'Moco_start_time': float(moco_data['start_time']),
        'Moco_end_time': float(moco_data['end_time']),
        'mesh_interval': float(moco_data.get('mesh_interval')),
        'effort_log_file': effort_log_file,
        'tracking_log_file': tracking_log_file
    }
    return spark.createDataFrame([data])

def process_marker_task_parameters(spark, scaling_params_csv, analysis_params_csv):
    scaling_params_df = pd.read_csv(scaling_params_csv)
    analysis_params_df = pd.read_csv(analysis_params_csv)
    run_id = analysis_params_df['run_id'].iloc[0]

    all_markers = []
    
    # Process scaling markers
    scaling_markers_row = scaling_params_df[scaling_params_df['key'] == 'marker_placer_tasks_df']
    if not scaling_markers_row.empty:
        scaling_markers_json = scaling_markers_row['value'].iloc[0]
        scaling_markers_data = json.loads(scaling_markers_json)
        for m in scaling_markers_data:
            all_markers.append({'run_id': run_id, 'step_type': 'scaling', 'target_name': m.get('marker'), 'weight': m.get('scale_weight'), 'is_enabled': m.get('scale_apply')})

    # Process IK markers
    ik_markers_row = analysis_params_df[analysis_params_df['key'] == 'marker_tasks_df']
    if not ik_markers_row.empty:
        ik_markers_json = ik_markers_row['value'].iloc[0]
        ik_markers_data = json.loads(ik_markers_json)
        for m in ik_markers_data:
            all_markers.append({'run_id': run_id, 'step_type': 'ik', 'target_name': m.get('marker'), 'weight': m.get('ik_weight'), 'is_enabled': m.get('ik_apply')})
            
    if not all_markers:
        return None
    return spark.createDataFrame(all_markers)

def process_muscle_parameters(spark, moco_json, run_id):
    with open(moco_json, 'r') as f:
        moco_data = json.load(f)

    muscle_data = []
    for item in moco_data.get('emg_scale_factors', []):
        muscle_data.append({
            'run_id': run_id,
            'step_type': 'moco',
            'Moco_emg_name': item.get('name'),
            'Moco_emg_path': item.get('path'),
            'Moco_emg_column': item.get('emg_column'),
            'Moco_emg_boundsMin': item.get('bounds', [None, None])[0],
            'Moco_emg_boundsMax': item.get('bounds', [None, None])[1],
            'emg_tracking_weight': moco_data.get('emg_tracking_weight'),
            'constraint_tolerance': moco_data.get('constraint_tolerance'),
            'convergence_tolerance': moco_data.get('convergence_tolerance'),
        })

    if not muscle_data:
        return None
    return spark.createDataFrame(muscle_data)
#==============================================================
#ワークフローテーブルを生成する関数。まだ実装はしていない
#==============================================================
def process_workflow_steps(spark, workflow_json_path, run_id):
    """Generates workflow steps data from JSON."""
    if not workflow_json_path or not os.path.exists(workflow_json_path):
        return None
        
    try:
        with open(workflow_json_path, 'r') as f:
            steps_data = json.load(f) # ここでPythonのリスト[{}, {}, ...]になる

        # 念のためリスト形式か確認（辞書ならリストに入れる）
        if isinstance(steps_data, dict):
            steps_data = [steps_data]

        # 各ステップのデータを整形
        processed_steps = []
        for step in steps_data:
            # 辞書をコピーして安全に操作
            row = step.copy()
            # run_id
            row['run_id'] = run_id
            # 整形した辞書をリストに追加
            processed_steps.append(row)

        return spark.createDataFrame(processed_steps)

    except Exception as e:
        print(f"[Provenance] Error reading workflow JSON: {e}")
        return None

def process_osim_validation(spark, validation_json, reserve_analysis_json, analysis_params_csv, run_id):
    """Processes validation metrics from a JSON file, adding session info."""
    if not validation_json or not os.path.exists(validation_json):
        return None
    
    try:
        with open(validation_json, 'r') as f:
            data = json.load(f)
        
        # Handle both single dict and list of dicts
        if isinstance(data, dict):
            data = [data]
            
        if not data:
            return None
            
        # Load session info to ensure IDs are present
        params_df = pd.read_csv(analysis_params_csv)
        study_id = str(params_df['study_id'].iloc[0])
        subject_id = str(params_df['subject_id'].iloc[0])
        session_id = str(params_df['session_id'].iloc[0])
        
        all_reserves_passed = True
        if reserve_analysis_json and os.path.exists(reserve_analysis_json):
            with open(reserve_analysis_json, 'r') as f:
                res_data = json.load(f)
                # 全関節が is_hicks_validated = True かどうかをチェック
                metrics = res_data.get('reserve_metrics', {})
                if metrics:
                    all_reserves_passed = all(m.get('is_hicks_validated', False) for m in metrics.values())
                else:
                    all_reserves_passed = False
                    
        # --- [2] 最終的な品質 Grade の判定 ---
        for record in data:
            # 指標の取得
            moco_ok = record.get('moco_success', False)
            # IK判定 (4.0を0.04に修正)
            ik_ok = (record.get('ik_rms_error', 1.0) < 0.02 and 
                    record.get('ik_max_error', 1.0) < 0.04)
            # 残差判定
            residual_ok = (record.get('is_residual_force_rms_valid', False) and 
                        record.get('is_residual_moment_rms_valid', False))

            # Grade判定
            if not moco_ok or not ik_ok:
                final_status = "REJECT"
            elif residual_ok and all_reserves_passed:
                final_status = "GOLD"
            else:
                final_status = "SILVER"

            # レコードの更新
            record['run_id'] = run_id
            record['quality_status'] = final_status
            record['total_reserve_ok'] = bool(all_reserves_passed)
            if 'study_id' not in record: 
                record['study_id'] = study_id
            if 'subject_id' not in record: 
                record['subject_id'] = subject_id
            if 'session_id' not in record: 
                record['session_id'] = session_id
            if 'dtw_details' in record:
                del record['dtw_details']
            # Remove nested structures that Spark cannot handle
            keys_to_remove = []
            for key, value in record.items():
                if isinstance(value, (dict, list)):
                    keys_to_remove.append(key)
            for key in keys_to_remove:
                del record[key]
            # ik_worst_marker_name は文字列なので保持する
            # (上記のフィルタでは削除されないが、明示的に確認)

        return spark.createDataFrame(data)
    except Exception as e:
        print(f"[Provenance] Error processing validation JSON: {e}")
        return None

def process_emg_tracking_metrics(spark, validation_json, analysis_params_csv, run_id):
    """Processes detailed EMG tracking metrics (DTW details) into a long-format table."""
    if not validation_json or not os.path.exists(validation_json):
        return None
    
    try:
        with open(validation_json, 'r') as f:
            data = json.load(f)
        
        # Handle both single dict and list of dicts
        if isinstance(data, dict):
            data = [data]
            
        # Load session info
        params_df = pd.read_csv(analysis_params_csv)
        study_id = str(params_df['study_id'].iloc[0])
        subject_id = str(params_df['subject_id'].iloc[0])
        session_id = str(params_df['session_id'].iloc[0])
        
        dtw_records = []
        for record in data:
            if 'dtw_details' in record and isinstance(record['dtw_details'], list):
                for detail in record['dtw_details']:
                    # Add keys
                    detail['run_id'] = run_id
                    detail['study_id'] = study_id
                    detail['subject_id'] = subject_id
                    detail['session_id'] = session_id
                    dtw_records.append(detail)

        if not dtw_records:
            return None
            
        return spark.createDataFrame(dtw_records)
    except Exception as e:
        print(f"[Provenance] Error processing EMG tracking metrics: {e}")
        return None

def process_ik_marker_errors(spark, validation_json, analysis_params_csv, run_id):
    """Processes per-marker IK errors from validation JSON into a long-format table."""
    if not validation_json or not os.path.exists(validation_json):
        return None

    try:
        with open(validation_json, 'r') as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = [data]

        # Load session info
        params_df = pd.read_csv(analysis_params_csv)
        study_id = str(params_df['study_id'].iloc[0])
        subject_id = str(params_df['subject_id'].iloc[0])
        session_id = str(params_df['session_id'].iloc[0])

        marker_records = []
        for record in data:
            worst_markers = record.get('ik_worst_markers', [])
            if not isinstance(worst_markers, list):
                continue
            for m in worst_markers:
                marker_records.append({
                    'run_id': run_id,
                    'study_id': study_id,
                    'subject_id': subject_id,
                    'session_id': session_id,
                    'marker_name': str(m.get('marker', '')),
                    'mean_error': float(m.get('mean_error', 0.0)),
                    'max_error': float(m.get('max_error', 0.0)),
                })

        if not marker_records:
            return None

        return spark.createDataFrame(marker_records)
    except Exception as e:
        print(f"[Provenance] Error processing IK marker errors: {e}")
        return None

def process_reserve_actuators(spark, reserve_analysis_json, analysis_params_csv, run_id):
    if not reserve_analysis_json or not os.path.exists(reserve_analysis_json):
        print(f"[Provenance] reserve_analysis_json not found or empty: {reserve_analysis_json}")
        return None
    
    try:
        with open(reserve_analysis_json, 'r') as f:
            data = json.load(f)
        
        # Load session info
        params_df = pd.read_csv(analysis_params_csv)
        study_id = str(params_df['study_id'].iloc[0])
        subject_id = str(params_df['subject_id'].iloc[0])
        session_id = str(params_df['session_id'].iloc[0])
        
        reserve_records = []
        
        # Extract reserve metrics for each joint (coordinate_base)
        # Schema: run_osim.analyze_reserve_actuators() の出力フォーマットに対応
        if 'reserve_metrics' in data and isinstance(data['reserve_metrics'], dict):
            metrics_dict_outer = data['reserve_metrics']
            for coord_base, metrics_dict in metrics_dict_outer.items():
                record = {
                    'run_id': run_id,
                    'study_id': study_id,
                    'subject_id': subject_id,
                    'session_id': session_id,
                    'coordinate_name': coord_base,
                    # Hicks et al. (2015) ルール検証結果
                    'nm_peak': float(metrics_dict.get('nm_peak', 0.0)),
                    'peak_ratio_at_load': float(metrics_dict.get('peak_ratio_at_load', 0.0)),
                    'is_hicks_validated': bool(metrics_dict.get('is_hicks_validated', False)),
                    'validation_status': str(metrics_dict.get('status', 'UNKNOWN'))
                }
                reserve_records.append(record)
        else:
            print("[Provenance] 'reserve_metrics' key not found or not a dict")
        
        if not reserve_records:
            print("[Provenance] No reserve metrics found in reserve_analysis.json")
            return None
        
        print(f"[Provenance] Creating DataFrame with {len(reserve_records)} records")
        return spark.createDataFrame(reserve_records)
    except Exception as e:
        print(f"[Provenance] Error processing reserve actuators: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Process and write OpenSim provenance data to Iceberg.")
    parser.add_argument("--warehouse", default="warehouse", help="Path to the Iceberg warehouse.")
    parser.add_argument("--provenance-csv", required=True, help="Path to provenance CSV.")
    parser.add_argument("--scaling-params-csv", required=True, help="Path to scaling parameters CSV.")
    parser.add_argument("--analysis-params-csv", required=True, help="Path to analysis parameters CSV.")
    parser.add_argument("--moco-json", required=True, help="Path to moco settings JSON.")
    parser.add_argument("--workflow-json", required=False, help="Path to workflow steps JSON.")
    parser.add_argument("--validation-json", required=False, help="Path to validation metrics JSON.")
    parser.add_argument("--reserve-analysis-json", required=False, help="Path to reserve actuator analysis JSON.")

    args = parser.parse_args()
    
    spark = None
    try:
        print("[Provenance] Initializing Spark for Iceberg writing...")
        spark = get_spark_session(args.warehouse)

        # Get run_id from a source file
        run_id = pd.read_csv(args.provenance_csv)['run_id'].iloc[0]
        print(f"[Provenance] Processing data for run_id: {run_id}")

        # Process each data type into a DataFrame
        analysis_runs_df = process_analysis_runs(spark, args.provenance_csv, args.scaling_params_csv)
        session_params_df = process_session_specific_parameters(spark, args.analysis_params_csv, args.moco_json)
        marker_params_df = process_marker_task_parameters(spark, args.scaling_params_csv, args.analysis_params_csv)
        muscle_params_df = process_muscle_parameters(spark, args.moco_json, run_id)
        workflow_steps_df = process_workflow_steps(spark, args.workflow_json, run_id)
        osim_validation_df = process_osim_validation(spark, args.validation_json, args.reserve_analysis_json, args.analysis_params_csv, run_id)
        emg_metrics_df = process_emg_tracking_metrics(spark, args.validation_json, args.analysis_params_csv, run_id)
        reserve_actuators_df = process_reserve_actuators(spark, args.reserve_analysis_json, args.analysis_params_csv, run_id)
        ik_marker_errors_df = process_ik_marker_errors(spark, args.validation_json, args.analysis_params_csv, run_id)

        # Write DataFrames to Iceberg
        if analysis_runs_df:
            write_df_to_iceberg(spark, analysis_runs_df, "analysis_runs")
        if session_params_df:
            write_df_to_iceberg(spark, session_params_df, "session_specific_parameters", "run_id")
        if marker_params_df:
            write_df_to_iceberg(spark, marker_params_df, "marker_task_parameters", "run_id")
        if muscle_params_df:
            write_df_to_iceberg(spark, muscle_params_df, "muscle_parameters", "run_id")
        if workflow_steps_df:
            write_df_to_iceberg(spark, workflow_steps_df, "workflow_steps", "run_id")
        if osim_validation_df:
            write_df_to_iceberg(spark, osim_validation_df, "osim_validation", "run_id")
        if emg_metrics_df:
            write_df_to_iceberg(spark, emg_metrics_df, "emg_tracking_metrics", "run_id")
        if reserve_actuators_df:
            write_df_to_iceberg(spark, reserve_actuators_df, "reserve_actuators", "run_id")
        if ik_marker_errors_df:
            write_df_to_iceberg(spark, ik_marker_errors_df, "ik_marker_errors", "run_id")

        print("[Provenance] All data written successfully.")

    except Exception as e:
        print(f"[Provenance] An error occurred during Iceberg writing: {e}")
        raise
    finally:
        if spark:
            spark.stop()
            print("[Provenance] Spark session stopped.")
