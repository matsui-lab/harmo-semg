from __future__ import annotations

import os
import shutil
import opensim as osim
import pandas as pd
import xml.etree.ElementTree as ET
import ast
import json
import glob
import numpy as np
import traceback
from pathlib import Path
from scipy.spatial.distance import euclidean
from fastdtw import fastdtw
from scipy import stats
from scipy import signal

from harmo.analysis import xml_builder as make_xml
from harmo.g6_execution import (
    rra_angle_change_metrics,
    rra_residual_metrics,
    sha256_file,
)


def _update_workflow_log(output_dir, run_id, method_id, step_type):
    """
    ワークフローの実行履歴をJSONファイルに追記保存するヘルパー関数
    """
    log_path = os.path.join(output_dir, 'workflow.json')
    workflow_log = []
    
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                workflow_log = json.load(f)
        except Exception as e:
            print(f"[Warning] Failed to load existing workflow log: {e}")

    step_order = len(workflow_log) + 1
    workflow_log.append({"run_id": run_id, "step_order": step_order, "method_id": method_id, "step_type": step_type})

    with open(log_path, 'w') as f:
        json.dump(workflow_log, f, indent=4)


def scaling(setup_file_path):
    if not os.path.exists(setup_file_path):
        raise FileNotFoundError(f"Scaling セットアップファイルが見つかりません: {setup_file_path}")
    try:
        scale_tool = osim.ScaleTool(setup_file_path)
        scale_tool.run()
    except Exception as e:
        print(f"[Error] Scalingツール実行エラー: {e}")
        raise


def ik(ik_setup_file):
    if not os.path.exists(ik_setup_file):
        raise FileNotFoundError(f"IK セットアップファイルが見つかりません: {ik_setup_file}")
    try:
        tree = ET.parse(ik_setup_file)
        root = tree.getroot()
        ik_tool_elem = root.find(".//InverseKinematicsTool")  
        if ik_tool_elem is not None:
            for key in ["model_file", "marker_file", "output_motion_file"]:
                elem = ik_tool_elem.find(key)
                if elem is not None and elem.text:
                    file_path = elem.text
                    exists = os.path.exists(file_path)
                    status = "✓" if exists else "✗ NOT FOUND"
                    print(f"  {key}: {file_path} {status}")
    except Exception as e:
        print(f"[Warning] セットアップファイルの検証中にエラー: {e}")
    
    try:
        iktool = osim.InverseKinematicsTool(ik_setup_file)
        iktool.run()
    except Exception as e:
        print(f"[Error] IKツール実行エラー: {e}")
        raise
    

def inverse_dynamics(id_setup_file):
    if not os.path.exists(id_setup_file):
        raise FileNotFoundError(f"ID セットアップファイルが見つかりません: {id_setup_file}")
    try:
        tree = ET.parse(id_setup_file)
        root = tree.getroot()
        id_tool_elem = root.find(".//InverseDynamicsTool")   
        if id_tool_elem is not None:
            # 重要なファイルパスをチェック
            for key in ["model_file", "coordinates_file", "external_loads_file"]:
                elem = id_tool_elem.find(key)
                if elem is not None and elem.text:
                    file_path = elem.text
                    exists = os.path.exists(file_path)
    except Exception as e:
        print(f"[Warning] セットアップファイルの検証中にエラー: {e}")
    
    try:
        id_tool = osim.InverseDynamicsTool(id_setup_file)
        id_tool.run()
    except Exception as e:
        print(f"[Error] InverseDynamicsツール実行エラー: {e}")
        print(f"[Info] セットアップファイルを確認してください: {id_setup_file}")
        raise


def run_rra_quality(paths, osim_cfg=None):
    """Run the configured two-pass RRA and compare final kinematics with IK."""
    required = (
        "model",
        "ik_result",
        "grf_xml",
        "rra_setup_template",
        "rra_actuators_template",
        "rra_task_template",
    )
    missing = [
        key
        for key in required
        if not paths.get(key) or not os.path.isfile(paths[key])
    ]
    if missing:
        raise FileNotFoundError("RRA inputs missing: {}".format(",".join(missing)))
    output_dir = os.path.dirname(paths["ik_result"])
    grf_mot = get_grf_path_from_xml(paths["grf_xml"])
    threshold = ((osim_cfg or {}).get("external_loads", {}) or {}).get(
        "threshold", 20.0
    )
    initial_time, final_time = get_time_range_with_pandas(
        grf_mot, threshold=threshold
    )
    config = ((osim_cfg or {}).get("RRA", {}) or {})
    passes = int(config.get("passes", 2))
    if passes < 1 or passes > 3:
        raise ValueError("RRA.passes must be between 1 and 3")
    actuators = os.path.join(output_dir, "rra_actuators.xml")
    tasks = os.path.join(output_dir, "rra_task.xml")
    shutil.copy2(paths["rra_actuators_template"], actuators)
    shutil.copy2(paths["rra_task_template"], tasks)
    current_model = paths["model"]
    final_kinematics = None
    final_actuation = None
    pass_records = []
    for iteration in range(1, passes + 1):
        pass_name = "g6_rra_pass{}".format(iteration)
        pass_dir = os.path.join(output_dir, pass_name)
        os.makedirs(pass_dir, exist_ok=True)
        output_model = os.path.join(
            output_dir, "rra_adjusted_pass{}.osim".format(iteration)
        )
        setup = os.path.join(output_dir, "setup_rra_pass{}.xml".format(iteration))
        values = {
            "name": pass_name,
            "model_file": current_model,
            "force_set_files": actuators,
            "results_directory": pass_dir,
            "initial_time": initial_time,
            "final_time": final_time,
            "external_loads_file": paths["grf_xml"],
            "desired_kinematics_file": paths["ik_result"],
            "task_set_file": tasks,
            "lowpass_cutoff_frequency": float(
                config.get("lowpass_cutoff_frequency", 6.0)
            ),
            "adjust_com_to_reduce_residuals": bool(
                config.get("adjust_com_to_reduce_residuals", True)
            ),
            "adjusted_com_body": str(config.get("adjusted_com_body", "torso")),
            "output_model_file": output_model,
        }
        make_xml.update_rra(paths["rra_setup_template"], setup, values)
        tool = osim.RRATool(setup)
        if not tool.run():
            raise RuntimeError("RRA pass {} returned failure".format(iteration))
        candidates = sorted(glob.glob(os.path.join(pass_dir, "*Kinematics_q.sto")))
        if len(candidates) != 1:
            raise RuntimeError(
                "RRA pass {} must create exactly one Kinematics_q.sto".format(
                    iteration
                )
            )
        final_kinematics = candidates[0]
        actuation_candidates = sorted(
            glob.glob(os.path.join(pass_dir, "*Actuation_force.sto"))
        )
        if len(actuation_candidates) != 1:
            raise RuntimeError(
                "RRA pass {} must create exactly one Actuation_force.sto".format(
                    iteration
                )
            )
        final_actuation = actuation_candidates[0]
        if not os.path.isfile(output_model):
            raise RuntimeError(
                "RRA pass {} did not create adjusted model".format(iteration)
            )
        pass_records.append(
            {
                "pass": iteration,
                "setup": os.path.basename(setup),
                "kinematics": os.path.relpath(final_kinematics, output_dir),
                "actuation": os.path.relpath(final_actuation, output_dir),
                "adjusted_model": os.path.basename(output_model),
            }
        )
        current_model = output_model
    metrics = rra_angle_change_metrics(
        Path(paths["ik_result"]), Path(str(final_kinematics))
    )
    metrics.update(rra_residual_metrics(Path(str(final_actuation))))
    retained_kinematics = os.path.join(output_dir, "rra_kinematics.sto")
    retained_actuation = os.path.join(output_dir, "rra_actuation_force.sto")
    retained_model = os.path.join(output_dir, "rra_adjusted_model.osim")
    shutil.copy2(final_kinematics, retained_kinematics)
    shutil.copy2(final_actuation, retained_actuation)
    shutil.copy2(current_model, retained_model)
    metrics["rra_kinematics_sha256"] = sha256_file(Path(retained_kinematics))
    metrics["rra_actuation_sha256"] = sha256_file(Path(retained_actuation))
    metrics["rra_adjusted_model_sha256"] = sha256_file(Path(retained_model))
    metrics["rra_pass_count"] = passes
    metrics["rra_passes"] = pass_records
    metrics["rra_kinematics_file"] = os.path.basename(retained_kinematics)
    metrics["rra_actuation_file"] = os.path.basename(retained_actuation)
    metrics["rra_adjusted_model_file"] = os.path.basename(retained_model)
    metrics["downstream_kinematics_source"] = "final-rra-kinematics"
    metrics["downstream_model_source"] = "final-rra-adjusted-model"
    paths["rra_kinematics"] = retained_kinematics
    paths["rra_actuation"] = retained_actuation
    paths["rra_adjusted_model"] = retained_model
    paths["pre_rra_model"] = paths["model"]
    paths["pre_rra_ik_result"] = paths["ik_result"]
    paths["model"] = retained_model
    paths["ik_result"] = retained_kinematics
    if paths.get("id_setup") and os.path.isfile(paths["id_setup"]):
        tree = ET.parse(paths["id_setup"])
        tool = tree.getroot().find(".//InverseDynamicsTool")
        if tool is None:
            raise ValueError("ID setup has no InverseDynamicsTool")
        replacements = {
            "model_file": retained_model,
            "coordinates_file": retained_kinematics,
        }
        for key, replacement in replacements.items():
            element = tool.find(key)
            if element is None:
                raise ValueError("ID setup has no {}".format(key))
            element.text = replacement
        tree.write(paths["id_setup"], encoding="UTF-8", xml_declaration=True)
    return metrics
    

def generate_emg_mapping_config(input_map_df, master_model_df, available_emg_columns):
    """
    EMGトラッキングのための設定情報（指示書）を生成する純粋なPython関数。
    OpenSimのオブジェクトは一切扱わない。

    Args:
        input_map_df (pd.DataFrame): original_name, seniam_name, side
        master_model_df (pd.DataFrame): seniam_name, model_base_names
        available_emg_columns (list of str): EMGファイルに含まれる列名のリスト

    Returns:
        list of dict: OpenSim側でループ処理するための設定リスト
    """
    mapping_config = []

    for index, row in input_map_df.iterrows():
        orig_name = row['original_name']
        seniam_name = row['seniam_name']
        side_raw = row['side']

        # 1. 列名の存在チェック (文字列リストとの比較)
        if orig_name not in available_emg_columns:
            print(f"[Skip] Column '{orig_name}' not found in EMG columns.")
            continue

        # 2. マスター定義チェック
        if seniam_name not in master_model_df.index:
            print(f"[Error] SENIAM name '{seniam_name}' not found in Master CSV.")
            continue

        # 3. Sideの正規化
        side_suffix = ""
        s = str(side_raw).lower()
        if s in ['r', 'right', 'rt']:
            side_suffix = "_r"
        elif s in ['l', 'left', 'lt']:
            side_suffix = "_l"
        else:
            print(f"[Error] Invalid side '{side_raw}' for {orig_name}.")
            continue

        # 4. モデルパス情報の展開
        base_names_str = master_model_df.loc[seniam_name, 'model_base_names']
        if isinstance(base_names_str, str) and base_names_str.startswith("["):
            base_names = ast.literal_eval(base_names_str)
        else:
            base_names = [base_names_str]

        # 5. コンフィグ（指示書）の作成
        # 1つのEMG列に対して、複数のモデル筋肉パスとパラメータ名が紐付く
        target_paths = []
        param_names = []

        for base in base_names:
            full_path = f"/forceset/{base}{side_suffix}"
            param_name = f"scale_{base}{side_suffix}"
            
            target_paths.append(full_path)
            param_names.append(param_name)

        # 辞書としてリストに追加
        config_item = {
            "emg_column": orig_name,       # 参照するEMG列名
            "model_paths": target_paths,   # 適用するモデルパスのリスト
            "param_names": param_names,    # 作成するパラメータ名のリスト
            "info": f"{seniam_name}{side_suffix}" # ログ用
        }
        mapping_config.append(config_item)

    return mapping_config


def get_grf_path_from_xml(xml_path):
    """
    ExternalLoads.xmlを解析し、datafileタグに記載されたMOTファイルのパスを取得する。
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()
    
    # 全階層から datafile タグを検索
    datafile_node = root.find('.//datafile')
    
    if datafile_node is None or not datafile_node.text:
        return None

    full_path = datafile_node.text.strip()
    
    return full_path


def get_time_range_with_pandas(grf_mot_path, threshold=20.0):
    
    # 1. ヘッダー行数を特定する (with open使用)
    header_rows = 0
    with open(grf_mot_path, 'r') as f:
        for i, line in enumerate(f):
            if "endheader" in line:
                header_rows = i + 1
                break
    
    if header_rows == 0:
        print("Error: 'endheader' keyword not found.")
        return None, None

    # 2. Pandasで読み込む
    try:
        df = pd.read_csv(grf_mot_path, sep='\t', skiprows=header_rows)
    except Exception as e:
        print(f"Read Error: {e}")
        return None, None

    # 列名の空白除去（トラブル防止）
    df.columns = [c.strip() for c in df.columns]
    df.to_csv("/workspace/notebooks/debug_grf_mots.csv", index=False)  # デバッグ用に読み込んだデータを保存


    # 対象の列名を定義
    col_r = 'ground_force_r_vy'
    col_l = 'ground_force_l_vy'
    time_col = next((c for c in df.columns if c.lower() == 'time'), None)

    target_cols = [c for c in [col_r, col_l] if c in df.columns]
    
    if not target_cols:
        print(f"Error: Neither {col_r} nor {col_l} found in columns.")
        return None, None
    
    if time_col is None:
        print("Error: Time column not found.")
        return None, None
    
    condition = (df[target_cols] > threshold).any(axis=1)
    valid_rows = df[condition]

    if valid_rows.empty:
        return None, None

    # 最初の時間の取得
    t_start_raw = valid_rows[time_col].iloc[0]
    # 最後の時間の取得
    t_end_raw = valid_rows[time_col].iloc[-1]

    # 安全マージン（微分の安定性確保のため、前後0.05秒ほど狭める）
    # ※もし「データがあるギリギリまで使いたい」場合はマージンを0にしてください
    margin = 0
    t_start = t_start_raw + margin
    t_end = t_end_raw - margin

    # 逆転チェック（データが短すぎる場合など）
    if t_start >= t_end:
        return None, None

    return t_start, t_end


def calculate_ik_metrics(ik_result_path, trc_path=None):
    """
    IKの結果ファイル（_marker_errors.sto）から精度指標を算出する。

    返却辞書には以下が含まれる:
      - ik_rms_error, ik_max_error, ik_total_squared_error: 従来の集約メトリクス
      - ik_worst_markers: 個別マーカーの平均誤差で降順ソートした上位リスト
        各要素は {"marker": str, "mean_error": float, "max_error": float}
      - ik_worst_marker_name: 最大平均誤差マーカー名 (単一文字列)
    """
    #出力ファイル名に _marker_errors.sto が付与される
    base = os.path.splitext(ik_result_path)[0]
    error_file = base + "_marker_errors.sto"
    dir_name = os.path.dirname(ik_result_path)
    
    if not os.path.exists(error_file):
        candidates = glob.glob(os.path.join(dir_name, "*_marker_errors.sto"))
        if not candidates:
            return {}
        error_file = candidates[0]

    try:
        # ヘッダー行数を特定して読み込み
        header_rows = 0
        with open(error_file, 'r') as f:
            for i, line in enumerate(f):
                if "endheader" in line:
                    header_rows = i + 1
                    break
        
        df = pd.read_csv(error_file, sep='\t', skiprows=header_rows)
        # 全体のRMS誤差 (全マーカー・全フレームの二乗平均の平方根)
        total_squared = np.mean(df["total_squared_error"])
        rms_error = np.mean(df["marker_error_RMS"])
        max_error = df["marker_error_max"].max()

        # --- 個別マーカー誤差の集計 ---
        # _marker_errors.sto の列: time, marker_error_RMS, marker_error_max,
        #   total_squared_error, 各マーカー名（例: RASIS, LASIS, ...）
        summary_cols = {"time", "marker_error_rms", "marker_error_max",
                        "total_squared_error"}
        marker_cols = [
            c for c in df.columns
            if c.lower() not in summary_cols and c.strip() != ""
        ]

        worst_markers = []
        if marker_cols:
            # _marker_errors.sto にマーカー個別列がある場合（旧フォーマット）
            for mc in marker_cols:
                col_data = pd.to_numeric(df[mc], errors="coerce")
                if col_data.isna().all():
                    continue
                worst_markers.append({
                    "marker": mc,
                    "mean_error": float(col_data.mean()),
                    "max_error": float(col_data.max()),
                })
        else:
            # OpenSim 4.x: _model_marker_locations.sto と TRC から個別エラー算出
            worst_markers = _compute_per_marker_errors(dir_name, trc_path)

        # 平均誤差で降順ソート
        worst_markers.sort(key=lambda x: x["mean_error"], reverse=True)

        result = {
            "ik_rms_error": float(rms_error),
            "ik_max_error": float(max_error),
            "ik_total_squared_error": float(total_squared),
            "ik_worst_markers": worst_markers[:10],  # 上位10マーカー
        }
        if worst_markers:
            result["ik_worst_marker_name"] = worst_markers[0]["marker"]
        return result
    except Exception as e:
        print(f"[Error] Failed to calculate IK metrics: {e}")
        return {}


def _compute_per_marker_errors(output_dir, trc_path):
    """model_marker_locations.sto と TRC から個別マーカーの平均・最大エラーを算出する。"""
    loc_candidates = glob.glob(os.path.join(output_dir, "*_model_marker_locations.sto"))
    if not loc_candidates or not trc_path or not os.path.exists(trc_path):
        return []

    def _load_sto(path):
        header_rows = 0
        with open(path, 'r') as f:
            for i, line in enumerate(f):
                if "endheader" in line:
                    header_rows = i + 1
                    break
        return pd.read_csv(path, sep='\t', skiprows=header_rows)

    try:
        loc_df = _load_sto(loc_candidates[0])
        loc_df.columns = [c.strip() for c in loc_df.columns]

        # TRCを読み込む
        # TRC形式: 行0=PathFileType, 行1=DataRate列名, 行2=DataRate値,
        #   行3=マーカー名(Frame# Time LASIS \t\t LFM1 ...), 行4=XYZラベル, 行5+=データ
        trc_df = pd.read_csv(trc_path, sep='\t', skiprows=5, header=None)
        # マーカー名を行3から取得
        with open(trc_path, 'r') as f:
            lines = [f.readline() for _ in range(5)]
        marker_names = lines[3].strip().split('\t')
        # 先頭2列は Frame# と Time
        if len(marker_names) >= 3:
            marker_names = marker_names[2:]  # マーカー名のみ
        # 各マーカーにX,Y,Zの3列
        trc_col_names = ['frame', 'time']
        for mn in marker_names:
            mn = mn.strip()
            if mn:
                trc_col_names.extend([f"{mn}_tx", f"{mn}_ty", f"{mn}_tz"])
        if len(trc_col_names) <= trc_df.shape[1]:
            trc_df.columns = trc_col_names + [f"_extra_{i}" for i in range(trc_df.shape[1] - len(trc_col_names))]
        else:
            trc_df = trc_df.iloc[:, :len(trc_col_names)]
            trc_df.columns = trc_col_names

        # 時間軸で同期（locの時刻に補間）
        loc_time = loc_df['time'].values

        # モデル位置のマーカー名を抽出（_tx, _ty, _tz を除いて一意にする）
        model_markers = set()
        for c in loc_df.columns:
            if c.endswith('_tx'):
                model_markers.add(c[:-3])
        model_markers = sorted(model_markers)

        worst_markers = []
        for mk in model_markers:
            mx = f"{mk}_tx"
            my = f"{mk}_ty"
            mz = f"{mk}_tz"
            if mx not in loc_df.columns or mx not in trc_df.columns:
                continue
            # TRC側をモデル時刻に補間
            trc_time = trc_df['time'].values
            trc_x = np.interp(loc_time, trc_time, pd.to_numeric(trc_df[mx], errors='coerce').fillna(0).values)
            trc_y = np.interp(loc_time, trc_time, pd.to_numeric(trc_df[my], errors='coerce').fillna(0).values)
            trc_z = np.interp(loc_time, trc_time, pd.to_numeric(trc_df[mz], errors='coerce').fillna(0).values)

            loc_x = loc_df[mx].values
            loc_y = loc_df[my].values
            loc_z = loc_df[mz].values

            dist = np.sqrt((loc_x - trc_x)**2 + (loc_y - trc_y)**2 + (loc_z - trc_z)**2)
            worst_markers.append({
                "marker": mk,
                "mean_error": float(np.mean(dist)),
                "max_error": float(np.max(dist)),
            })

        return worst_markers
    except Exception as e:
        print(f"[Warning] Per-marker error computation failed: {e}")
        return []

def calculate_moco_metrics(solution, effort_file_path, grf_mot_path, body_kinematics_path):
    # ### 修正1.2 start ###
    """
    MocoSolutionオブジェクトと各種結果ファイルから、新しいロジックに基づいて精度検証指標を算出する。

    Args:
        solution (osim.MocoSolution): Mocoのソルバーから返されたsolutionオブジェクト。
        effort_file_path (str): Mocoの努力量ファイル（...effortSolution.sto）。残差力・モーメントが含まれる。
        grf_mot_path (str): 床反力データファイル（...externalloads.mot）。
        body_kinematics_path (str): AnalyzeToolが出力した身体のキネマティクスファイル（...pos_global.sto）。COM（重心）位置が含まれる。

    Returns:
        dict: 計算された精度指標を含む辞書。
    """
    metrics = {}
    try:
        metrics['moco_success'] = solution.success()
        metrics['moco_objective'] = solution.getObjective()
        # --- 必要なファイルをすべて読み込む ---
        # ヘッダー行数を特定してファイルを読み込む内部関数
        def _load_sto_file(filepath):
            if not os.path.exists(filepath):
                print(f"[Error] ファイルが見つかりません: {filepath}")
                return None
            header_rows = 0
            with open(filepath, 'r') as f:
                for i, line in enumerate(f):
                    if "endheader" in line:
                        header_rows = i + 1
                        break
            # sep='\t' または sep='\s+' を状況に応じて使い分ける
            try:
                return pd.read_csv(filepath, sep='\t', skiprows=header_rows)
            except pd.errors.ParserError:
                 return pd.read_csv(filepath, sep=r'\s+', skiprows=header_rows)


        effort_df = _load_sto_file(effort_file_path)
        grf_df = _load_sto_file(grf_mot_path)
        body_kin_df = _load_sto_file(body_kinematics_path)

        # すべてのファイルが正常に読み込めたかチェック
        if effort_df is None or grf_df is None or body_kin_df is None:
            raise FileNotFoundError("検証に必要なファイルが1つ以上見つかりませんでした。")

        # --- 1. 残差力 (Residual Forces) の評価 ---
        # 要求された残差力の列名を定義
        residual_force_cols = ['/forceset/reserve_jointset_ground_pelvis_pelvis_tx', 
                               '/forceset/reserve_jointset_ground_pelvis_pelvis_ty', 
                               '/forceset/reserve_jointset_ground_pelvis_pelvis_tz']
        # データフレームに列が存在するか確認
        if all(c in effort_df.columns for c in residual_force_cols):
            # 3軸の残差力からベクトルノルム（大きさ）を計算
            residual_force_magnitude = np.linalg.norm(effort_df[residual_force_cols], axis=1)
            
            # ピーク値（最大値）とRMS（二乗平均平方根）を計算
            metrics['residual_force_peak'] = float(residual_force_magnitude.max())
            metrics['residual_force_rms'] = float(np.sqrt(np.mean(residual_force_magnitude**2)))
        else:
            print("[Warning] 残差力の列が見つからないため、評価をスキップします。")


        # --- 2. 残差モーメント (Residual Moments) の評価 ---
        # 要求された残差モーメントの列名を定義
        residual_moment_cols = ['/forceset/reserve_jointset_ground_pelvis_pelvis_tilt', 
                                '/forceset/reserve_jointset_ground_pelvis_pelvis_list', 
                                '/forceset/reserve_jointset_ground_pelvis_pelvis_rotation']
        if all(c in effort_df.columns for c in residual_moment_cols):
            # 3軸の残差モーメントからベクトルノルム（大きさ）を計算
            residual_moment_magnitude = np.linalg.norm(effort_df[residual_moment_cols], axis=1)

            # ピーク値とRMSを計算
            metrics['residual_moment_peak'] = float(residual_moment_magnitude.max())
            metrics['residual_moment_rms'] = float(np.sqrt(np.mean(residual_moment_magnitude**2)))
        else:
            print("[Warning] 残差モーメントの列が見つからないため、評価をスキップします。")

        # --- 3. 外部反力 (External Loads) の大きさ計算 ---
        # 要求された外部反力の列名を定義
        external_force_cols = ['ground_force_r_vx', 'ground_force_r_vy', 'ground_force_r_vz',
                               'ground_force_l_vx', 'ground_force_l_vy', 'ground_force_l_vz']
        if all(c in grf_df.columns for c in external_force_cols):
            # 左右の力をx,y,z成分ごとに合計
            sum_fx = grf_df['ground_force_r_vx'].fillna(0) + grf_df['ground_force_l_vx'].fillna(0)
            sum_fy = grf_df['ground_force_r_vy'].fillna(0) + grf_df['ground_force_l_vy'].fillna(0)
            sum_fz = grf_df['ground_force_r_vz'].fillna(0) + grf_df['ground_force_l_vz'].fillna(0)
            
            # 3軸の合計力からベクトルノルム（大きさ）を計算
            external_force_magnitude = np.sqrt(sum_fx**2 + sum_fy**2 + sum_fz**2)
            metrics["ex_loads_peak"] = float(external_force_magnitude.max())
            metrics["ex_loads_rms"] = float(np.sqrt(np.mean(external_force_magnitude**2)))

        else:
            print("[Warning] 外部反力の列が見つからないため、閾値計算をスキップします。")


        # --- 4. COMの高さの計算 ---
        # COMのY座標（高さ）の列名を定義
        com_y_col = 'center_of_mass_Y'
        if com_y_col in body_kin_df.columns:
            #分析期間中のcom_y_col * ex_loadsの積を求める
            moments =  external_force_magnitude * body_kin_df[com_y_col]
            metrics["ex_moment_peak"] = float(moments.max())
            metrics["ex_moment_rms"] = float(np.sqrt(np.mean(moments**2)))
            
        else:
            print("[Warning] COMの高さの列が見つからないため、閾値計算をスキップします。")


        # --- 5. 閾値と比較した評価指標の計算 ---
        # (residual_force_peak) < 0.05 * (ex_loads_peak)
        # (residual_force_rms) < 0.05 * (ex_loads_rms)かチェック
        if 'ex_loads_peak' in metrics and 'residual_force_peak' in metrics and 'residual_force_rms' in metrics and 'ex_loads_rms' in metrics:
            # ピーク値とRMSが閾値以下かどうかのフラグ
            metrics['is_residual_force_peak_valid'] = (metrics['residual_force_peak'] < 0.05 * metrics['ex_loads_peak'])
            metrics['is_residual_force_rms_valid'] = (metrics['residual_force_rms'] < 0.05 * metrics['ex_loads_rms'])
        
        if "residual_moment_peak" in metrics and "residual_moment_rms" in metrics and "ex_moment_peak" in metrics and "ex_moment_rms" in metrics:
            # 閾値: COM高さ * 外部反力 の1%
            # ピーク値とRMSが閾値以下かどうかのフラグ
            metrics['is_residual_moment_peak_valid'] = (metrics['residual_moment_peak'] < 0.01 * metrics['ex_moment_peak'])
            metrics['is_residual_moment_rms_valid'] = (metrics['residual_moment_rms'] < 0.01 * metrics['ex_moment_rms'])

    except Exception as e:
        print(f"[Error] Mocoメトリクスの計算中にエラーが発生しました: {e}")
    
    return metrics
    # ### 修正1.2 end ###

def calculate_dtw_metrics(tracking_solution_path, emg_sto_path, mapping_config):
    """
    DTW (Dynamic Time Warping) を用いてEMGと筋活動の類似度を算出する
    """
    metrics = {}
    
    if not os.path.exists(tracking_solution_path) or not os.path.exists(emg_sto_path):
        print("[Warning] DTW calculation skipped: files not found.")
        return metrics

    # Helper to load STO
    def _load_sto(path):
        header_rows = 0
        with open(path, 'r') as f:
            for i, line in enumerate(f):
                if "endheader" in line:
                    header_rows = i + 1
                    break
        try:
            return pd.read_csv(path, sep='\t', skiprows=header_rows)
        except pd.errors.ParserError:
            return pd.read_csv(path, sep=r'\s+', skiprows=header_rows)

    try:
        emg_df = _load_sto(emg_sto_path)
        sol_df = _load_sto(tracking_solution_path)
        
        # Column cleanup
        emg_df.columns = [c.strip() for c in emg_df.columns]
        sol_df.columns = [c.strip() for c in sol_df.columns]

        dtw_results = []
        
        for item in mapping_config:
            emg_col = item['emg_column']
            model_paths = item['model_paths']
            
            if emg_col not in emg_df.columns:
                continue
            
            x_raw = emg_df[emg_col].dropna().values
            
            for m_path in model_paths:
                # Moco output activation column usually has /activation suffix
                act_col = f"{m_path}/activation"
                
                if act_col in sol_df.columns:
                    y_raw = sol_df[act_col].dropna().values
                    
                    # Resample x (EMG) to match y (OpenSim) length
                    if len(y_raw) < 2 or len(x_raw) < 2:
                        continue
                        
                    target_len = len(y_raw)
                    x_resampled = signal.resample(x_raw, target_len)
                    
                    # Z-score normalization
                    # Handle constant arrays to avoid NaN
                    if np.std(x_resampled) == 0: x_norm = np.zeros_like(x_resampled)
                    else: x_norm = stats.zscore(x_resampled)
                    
                    if np.std(y_raw) == 0: y_norm = np.zeros_like(y_raw)
                    else: y_norm = stats.zscore(y_raw)
                    
                    # Reshape for fastdtw
                    x_input = x_norm.reshape(-1, 1)
                    y_input = y_norm.reshape(-1, 1)
                    
                    distance, path = fastdtw(x_input, y_input, dist=euclidean)
                    normalized_dist = distance / len(path)
                    
                    dtw_results.append({
                        "emg_column": emg_col,
                        "muscle_path": m_path,
                        "dtw_distance": float(distance),
                        "dtw_normalized_distance": float(normalized_dist)
                    })

        if dtw_results:
            avg_norm_dtw = np.mean([r['dtw_normalized_distance'] for r in dtw_results])
            metrics['dtw_avg_normalized_distance'] = float(avg_norm_dtw)
            metrics['dtw_details'] = dtw_results
            
    except Exception as e:
        print(f"[Error] DTW calculation failed: {e}")
        
    return metrics


def analyze_reserve_actuators(coord_and_emgdriven_path, inverse_dynamics_path, output_dir=None):
    """
    Hicks et al. (2015) の95%貢献ルールに準拠しつつ、
    2.5 Nmの絶対閾値で遊脚期のスパイクを処理する判定ロジック
    """
    # --- データの読み込みと時間同期 ---
    def _load_sto(path):
        header_rows = 0
        with open(path, 'r') as f:
            for i, line in enumerate(f):
                if line.strip() == 'endheader':
                    header_rows = i + 1
                    break
        df = pd.read_csv(path, sep='\t', skiprows=header_rows)
        df.columns = [c.strip() for c in df.columns]
        return df

    coord_df = _load_sto(coord_and_emgdriven_path)
    id_df = _load_sto(inverse_dynamics_path)
    # 時間軸の同期: Master = coord_df
    target_time = coord_df['time'].values
    id_df_sync = pd.DataFrame({'time': target_time})
    for col in id_df.columns:
        if col != 'time':
            id_df_sync[col] = np.interp(target_time, id_df['time'].values, id_df[col].values)
    id_df = id_df_sync

    # --- 判定パラメータ ---
    ABS_LIMIT = 2.5      # 絶対閾値 (Nm)
    RATIO_LIMIT = 0.05   # 相対閾値 (5% = 95%貢献ルール)

    reserve_cols = [c for c in coord_df.columns if 'reserve' in c.lower() and 'pelvis' not in c.lower()]

    reserve_metrics = {}

    for res_col in reserve_cols:
        # カラム名の柔軟なマッチング
        coord_base = res_col.split('reserve_jointset_')[-1] if 'jointset' in res_col else res_col.split('/')[-1]
        mom_col = None
        for c in id_df.columns:
            target = c.replace('_moment', '').replace('_force', '')
            if target in coord_base or coord_base in target:
                mom_col = c
                break
        
        if not mom_col:
            continue

        # --- 物理計算 ---
        res_data = coord_df[res_col].values
        mom_data = id_df[mom_col].values
        
        abs_res = np.abs(res_data)
        abs_mom = np.abs(mom_data)
        
        # 瞬時比率 (分母ゼロ対策)
        instant_ratio = abs_res / np.clip(abs_mom, 1e-6, None)
        
        # 判定ロジック:
        # 「絶対値が 2.5 Nm 未満」 または 「瞬時比率が 5% 未満」 であれば妥当
        is_valid_frame = (abs_res < ABS_LIMIT) | (instant_ratio < RATIO_LIMIT)
        
        # 試行全体としての評価指標
        nm_peak = float(np.max(abs_res))
        # 2.5Nmを超えた「危険な瞬間」の中での最大比率を抽出
        serious_violations = instant_ratio[abs_res >= ABS_LIMIT]
        peak_ratio_at_load = float(np.max(serious_violations)) if serious_violations.size > 0 else 0.0
        
        # RMS値の計算 (全フレームでの二乗平均平方根)
        rms_value = float(np.sqrt(np.mean(abs_res**2)))
        
        # 全体フラグ
        all_passed = bool(np.all(is_valid_frame))

        reserve_metrics[coord_base] = {
            'reserve_col': res_col,              # カラムの完全な名前を記録
            'moment_col': mom_col,              # 対応するInverse Dynamics列
            'nm_peak': nm_peak,                 # ピーク残差力 (Nm)
            'peak_ratio_at_load': peak_ratio_at_load,  # 重負荷時の最大比率
            'rms': rms_value,                   # RMS値 (Nm)
            'is_hicks_validated': all_passed,   # Hicks 95%貢献ルール検証結果
            'status': 'PASS' if all_passed else 'FAIL',
            'abs_limit': ABS_LIMIT,             # 使用された絶対閾値
            'ratio_limit': RATIO_LIMIT          # 使用された相対閾値
        }

    result = {
        'reserve_metrics': reserve_metrics,
        'summary': {
            'total_joints': len(reserve_metrics),
            'passed_joints': sum(1 for m in reserve_metrics.values() if m['is_hicks_validated']),
            'validation_method': 'Hicks et al. (2015) 95% contribution rule',
            'absolute_threshold_nm': ABS_LIMIT,
            'relative_threshold_percent': RATIO_LIMIT * 100
        }
    }

    if output_dir:
        with open(os.path.join(output_dir, 'reserve_analysis.json'), 'w') as f:
            json.dump(result, f, indent=4)
    else:
        print(f"[Warning] output_dir is None; reserve_analysis.json NOT saved")
            
    return result

def run_emg_tracking(model_path, grf_xml_path, ik_mot_path, emg_sto_path, mapping_config, paths, osim_cfg=None):
    """
    OpenSimを実行する関数。
    マッピングロジックは持たず、渡された mapping_config に従ってセットアップする。
    MocoInverseの設定と使用したモデルを保存する機能を持つ。

    osim_cfg: osim_analysis_config.yaml の読み込み結果 (dict)。None なら従来のデフォルト値を使用。
    """
    _moco = (osim_cfg or {}).get("MocoInverse", {})
    _exl = (osim_cfg or {}).get("external_loads", {})
    output_dir = os.path.dirname(ik_mot_path)
    effort_file_path = os.path.join(output_dir, 'effortSolution.sto')
    tracking_file_path = os.path.join(output_dir, 'trackingSolution.sto')
    settings_file_path = os.path.join(output_dir, 'moco_settings.json')
    processed_model_path = os.path.join(output_dir, 'model_with_moco_actuators.osim')

    grf_mot_path = get_grf_path_from_xml(grf_xml_path)
    _threshold = _exl.get("threshold", 20.0)
    t_start, t_end = get_time_range_with_pandas(grf_mot_path, threshold=_threshold)

    # --- Part 1: Model Setup ---
    modelProcessor = osim.ModelProcessor(model_path)
    modelProcessor.append(osim.ModOpAddExternalLoads(grf_xml_path))
    modelProcessor.append(osim.ModOpIgnoreTendonCompliance())
    modelProcessor.append(osim.ModOpReplaceMusclesWithDeGrooteFregly2016())
    modelProcessor.append(osim.ModOpIgnorePassiveFiberForcesDGF())
    modelProcessor.append(osim.ModOpScaleActiveFiberForceCurveWidthDGF(1.5))
    modelProcessor.append(osim.ModOpAddReserves(50.0))
    
    # Process the model and save it
    processed_model = modelProcessor.process()
    processed_model.printToXML(processed_model_path)


    # --- Part 2: MocoInverse Setup ---
    inverse = osim.MocoInverse()
    inverse.setModel(modelProcessor)

    # --- Collect settings for provenance ---
    moco_settings = {}

    kinematics_filter_freq = _moco.get("kinematics_filter_freq", 6)
    coordinates = osim.TableProcessor(ik_mot_path)
    coordinates.append(osim.TabOpLowPassFilter(kinematics_filter_freq))
    coordinates.append(osim.TabOpUseAbsoluteStateNames())
    moco_settings['kinematics_low_pass_filter_frequency'] = kinematics_filter_freq

    inverse.setKinematics(coordinates)
    inverse.set_kinematics_allow_extra_columns(True)
    inverse.set_initial_time(t_start)
    inverse.set_final_time(t_end)
    moco_settings['start_time'] = t_start
    moco_settings['end_time'] = t_end

    mesh_interval = _moco.get("mesh_interval", 0.02)
    inverse.set_mesh_interval(mesh_interval)
    moco_settings['mesh_interval'] = mesh_interval
    
    constraint_tolerance = _moco.get("constraint_tolerance", 1e-3)
    inverse.set_constraint_tolerance(constraint_tolerance)
    moco_settings['constraint_tolerance'] = constraint_tolerance
    
    convergence_tolerance = _moco.get("convergence_tolerance", 1e-3)
    inverse.set_convergence_tolerance(convergence_tolerance)
    moco_settings['convergence_tolerance'] = convergence_tolerance
    
    # --- これだけでログファイルは生成されます ---
    effort_log_file = os.path.join(output_dir, 'MocoWithOutEMG.log')
    # 並列実行時の競合を防ぐため、CWDをoutput_dir(temp_dir)に変更してからIPOPTを実行する。
    # IPOPTはCWDの ipopt.opt を読み、ログもCWDに出力するため。
    prev_cwd = os.getcwd()
    os.chdir(output_dir)
    try:
        with open("ipopt.opt", "w") as file:
            file.write("output_file MocoWithOutEMG.log\n")
        # Base Solution (Effort Minimization)
        inverseSolution = inverse.solve()
        solution = inverseSolution.getMocoSolution()
        solution.write(effort_file_path)
    finally:
        os.chdir(prev_cwd)
    # ------------------------------------------
    #pathsにsolutionファイルのパスを追加
    paths['effortSolution'] = effort_file_path
    moco_settings['effort_log_file'] = effort_log_file

    # --- Part 3: EMG Tracking Setup (sEMGがある場合のみ実行) ---
    if emg_sto_path is not None and mapping_config:
        emgReference = osim.TimeSeriesTable(emg_sto_path)
        study = inverse.initialize()
        problem = study.updProblem()

        tracking_weight = _moco.get("tracking_weight", 5)
        tracking = osim.MocoControlTrackingGoal('emg_tracking')
        tracking.setWeight(tracking_weight) 
        tracking.setReference(osim.TableProcessor(emgReference))
        moco_settings['emg_tracking_weight'] = tracking_weight
        moco_settings['emg_tracking_reference_file'] = emg_sto_path

        # ★ここが変更点：Configに従ってループ処理するだけ
        scale_factors = []
        for item in mapping_config:
            emg_col = item['emg_column']
            m_paths = item['model_paths']
            params = item['param_names']

            # 1対多の展開もリストを回すだけ
            for path, param in zip(m_paths, params):
                tracking.setReferenceLabel(path, emg_col)
                tracking.addScaleFactor(param, path, [0.01, 1.0])
                scale_factors.append({
                    'name': param,
                    'path': path,
                    'emg_column': emg_col,
                    'bounds': [0.01, 1.0]
                })
        moco_settings['emg_scale_factors'] = scale_factors

        problem.addGoal(tracking)
        
        solver = osim.MocoCasADiSolver.safeDownCast(study.updSolver())
        solver.resetProblem(problem)
        solver.set_parameters_require_initsystem(False)
        #solver.set_parallel(8)  # 例として4つのスレッドを使用

        # --- これだけでログファイルは生成されます ---
        tracking_log_file = os.path.join(output_dir, 'MocoWithEMG.log')
        # 並列実行時の競合を防ぐため、CWDをoutput_dir(temp_dir)に変更してからIPOPTを実行する。
        prev_cwd = os.getcwd()
        os.chdir(output_dir)
        try:
            with open("ipopt.opt", "w") as file:
                file.write("output_file MocoWithEMG.log\n")
            # Solve Tracking
            solution = study.solve()
            solution.write(tracking_file_path)
        finally:
            os.chdir(prev_cwd)
        # ------------------------------------------
        moco_settings['tracking_log_file'] = tracking_log_file
        paths['trackingSolution'] = tracking_file_path
    else:
        print("[Info] sEMGデータなし: EMG Tracking Setupをスキップし、effortSolutionのみ使用します。")

    # Save settings to JSON
    with open(settings_file_path, 'w') as f:
        import json
        json.dump(moco_settings, f, indent=4)

    #pathsに結果ファイルのパスを追加
    paths['moco_settings'] = settings_file_path
    paths['moco_model'] = processed_model_path

    # solutionを返すように変更
    return solution
    
    
def merge_kinematics_from_effort_solution(paths: dict):
    #efortSolution.sto からキネマティクス情報を抽出し、
    #trackingSolution.sto に挿入して保存する関数
    output_dir = os.path.dirname(paths['effortSolution'])
    effort_file = paths['effortSolution']
    tracking_file = paths['trackingSolution']
    output_file = os.path.join(output_dir, 'coord_and_emgdriven.sto')

    # 1. データの読み込み
    # 両方のファイルをMocoTrajectoryとして読み込みます
    effort_traj = osim.MocoTrajectory(effort_file)
    tracking_traj = osim.MocoTrajectory(tracking_file)

    # 2. データの結合 (Insert States)
    # effort_traj から状態変数のテーブル（States Table）を抽出します。
    # これには関節角度(q)、角速度(u)が含まれます。
    effort_table = effort_traj.exportToStatesTable()
    tracking_traj.insertStatesTrajectory(effort_table)

    # 3. 結果の保存
    tracking_traj.write(output_file)
    #結果のファイルパスをpathsリストの中に追加する
    paths['coord'] = output_file
    

def analyze(model_path, storage_path, ex_loads_path, muscle_analyze = False, force_report = True, JointReaction = True, body_kinematics=True):
    output_dir = os.path.dirname(model_path)
    xml_path = os.path.join(output_dir, 'analysis.xml')
    #analyzeToolの使用。
    #MuscleAnalysis, ForceReporter, JointReactionについて解析できるようにした。
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

        
    analyze = osim.AnalyzeTool()
    analyze.setName("analyze")
    analyze.setResultsDir(output_dir)
    #モデルの登録
    #MocoInverseを実行するときには、アクチュエータを変更している
    #そのため、MocoInverseの結果(.sto)を入れてanalyzeするときには、そのときに使ったモデルを再現する必要がある
    #このモデルはMocoで使用したモデル
    #GUIではanalyzeに成功している
    analyze.setModelFilename(model_path)
    analyze.setStatesFileName(storage_path) 
    analyze.setExternalLoadsFileName(ex_loads_path)

    # .stoファイルを読み込んで時間を取得する
    storage = osim.Storage(storage_path)
    analyze.setInitialTime(storage.getFirstTime())
    analyze.setFinalTime(storage.getLastTime())

    if muscle_analyze == True:
        analyze.updAnalysisSet().cloneAndAppend(osim.MuscleAnalysis())
    if force_report == True:
        analyze.updAnalysisSet().cloneAndAppend(osim.ForceReporter())
    if JointReaction == True:
        #JointReactionのみ、デフォルトで名前がつかない。
        jr = osim.JointReaction()
        jr.setName("JointReaction") 
        analyze.updAnalysisSet().cloneAndAppend(jr)
    if body_kinematics == True:
        analyze.updAnalysisSet().cloneAndAppend(osim.BodyKinematics())
    analyze.updControllerSet().cloneAndAppend(osim.PrescribedController(storage_path)) 
    analyze.printToXML(xml_path)

    analyze = osim.AnalyzeTool(xml_path)
    analyze.run()
        

# =================================================
# Phase 1 スケーリング
# =================================================
def run_scaling_process(paths, osim_cfg=None):
    """
    Scalingのみを実行する関数
    """
    from harmo.analysis.osim_config import get_scaling_iteration_config
    from harmo.analysis.scaling_retry import extract_scale_max_error, update_scale_setup_model_file

    policy = get_scaling_iteration_config(osim_cfg or {})

    mode = policy['mode']
    max_error_threshold = policy['max_error_threshold']
    max_iterations = policy['max_iterations']

    last_max_error = None
    output_dir = os.path.dirname(paths['output_model'])

    for iteration in range(1, max_iterations + 1):
        scaling(paths['scale_setup'])

        # 成功確認
        if not os.path.exists(paths['output_model']):
            raise RuntimeError("Scaling failed: Output model not created.")

        if mode == 'once':
            break

        last_max_error = extract_scale_max_error(paths['scale_setup'])
        if last_max_error is None:
            print("[Warning] MarkerPlacerの *_marker_errors.sto から max error を抽出できないため、反復スケーリングを終了します。")
            break

        print(f"[Scaling] iteration={iteration} max_error={last_max_error:.6f}")
        if last_max_error < max_error_threshold:
            break

        # 直前の出力モデルを次回反復の入力モデルとして使う。
        if iteration < max_iterations:
            next_input_model = os.path.join(output_dir, f"scaled_model_iter{iteration}.osim")
            shutil.copy2(paths['output_model'], next_input_model)
            updated = update_scale_setup_model_file(paths['scale_setup'], next_input_model)
            if not updated:
                raise RuntimeError(
                    "Scaling failed: Could not update GenericModelMaker/model_file for next iteration."
                )
            print(f"[Scaling] next input model set to: {next_input_model}")

    if mode != 'once' and last_max_error is not None and last_max_error >= max_error_threshold:
        print(
            "[Warning] max error が閾値に到達しないまま反復上限に到達しました: "
            f"max_error={last_max_error:.6f}, threshold={max_error_threshold:.6f}, "
            f"max_iterations={max_iterations}"
        )
    
    # ログ記録 (method_id=0: Scaling)
    run_id = paths.get('run_id', 'unknown')
    _update_workflow_log(output_dir, run_id, 0, "Scaling")


# =================================================
# Phase 2 IK~Analysis
# =================================================
def run_analysis_pipeline(paths, input_map_df, master_model_df, osim_cfg=None):
    """
    IK -> ID -> Moco -> Analysis の順で解析を実行する関数
    (ID: Inverse Dynamics)
    osim_cfg: osim_analysis_config.yaml の読み込み結果 (dict)。None なら従来のデフォルト値を使用。
    workflow セクションで各ステップの有効/無効を制御可能。
    """
    if paths['trc'] is None:
        print("エラー: モーションキャプチャデータ(trc)がないため、このトライアルの解析を中止します。")
        return

    has_force_plate = paths.get('mot') is not None
    has_emg = paths.get('emg') is not None
    
    # Resolve workflow flags (with dependency enforcement)
    from harmo.analysis.osim_config import resolve_workflow
    wf = resolve_workflow(osim_cfg or {})
    
    validation_data = {}
    output_dir = os.path.dirname(paths['ik_setup'])
    run_id = paths.get('run_id', 'unknown')
        
    # 1. Inverse Kinematics
    if not wf["IK"]:
        print("[Skip] IK is disabled in workflow config.")
        return
    
    ik(paths['ik_setup'])
    
    if not os.path.exists(paths['ik_result']):
        raise RuntimeError("IK failed: Motion file not created.")

    # IK指標の計算
    ik_metrics = calculate_ik_metrics(paths['ik_result'], trc_path=paths.get('trc'))
    validation_data.update(ik_metrics)

    # ### 修正1.1 start ###
    # IKマーカーエラーファイルをデータベース登録用にリネームする
    output_dir = os.path.dirname(paths['ik_result'])
    run_id = paths.get('run_id', 'unknown')
    try:
        # 元のエラーファイルパスを探す
        error_file_candidates = glob.glob(os.path.join(output_dir, "*_marker_errors.sto"))
        if error_file_candidates:
            # Prefer latest file if multiple candidates exist.
            original_error_file = max(error_file_candidates, key=os.path.getmtime)

            # Build a robust filename without assuming run_id token count.
            base = os.path.basename(original_error_file)
            if base.endswith("_marker_errors.sto"):
                trial_prefix = base[:-len("_marker_errors.sto")]
            else:
                trial_prefix = os.path.splitext(base)[0]

            new_error_filename = f"{trial_prefix}_{run_id}_InverseKinematics_MarkerErrors.sto"
            new_error_filepath = os.path.join(output_dir, new_error_filename)

            # ファイルをリネーム
            if os.path.abspath(original_error_file) != os.path.abspath(new_error_filepath):
                os.rename(original_error_file, new_error_filepath)
                print(f"マーカーエラーファイルをリネームしました: {new_error_filepath}")
            # ### 修正1.1 end ###

    except Exception as e:
        print(f"[Warning] マーカーエラーファイルのリネーム中にエラーが発生しました: {e}")
    
    # ログ記録 (method_id=1: InverseKinematics)
    _update_workflow_log(output_dir, run_id, 1, "InverseKinematics")

    # 2. Residual Reduction Algorithm (G6 quality endpoint)
    if wf["RRA"] and not has_force_plate:
        raise RuntimeError("RRA requires force-plate data")
    if wf["RRA"]:
        validation_data.update(run_rra_quality(paths, osim_cfg=osim_cfg))
        _update_workflow_log(output_dir, run_id, 8, "ResidualReductionAlgorithm")
    else:
        print("[Skip] RRA is disabled in workflow config.")

    # 3. Inverse Dynamics (ID)
    if wf["ID"] and not has_force_plate:
        print("[Skip] ID skipped: force plate data (mot) not available.")
        wf["ID"] = False
    if wf["ID"]:
        grf_mot_path = get_grf_path_from_xml(paths['grf_xml'])
        _exl = (osim_cfg or {}).get("external_loads", {})
        _threshold = _exl.get("threshold", 20.0)
        t_start, t_end = get_time_range_with_pandas(grf_mot_path, threshold=_threshold)
        if not os.path.exists(paths["id_setup"]):
            raise FileNotFoundError(f"ID Setup file not found: {paths['id_setup']}")
        # IDセットアップの要件ファイルを確認
        try:
            id_tree = ET.parse(paths["id_setup"])
            id_root = id_tree.getroot()
            id_tool_elem = id_root.find(".//InverseDynamicsTool")
            
            if id_tool_elem is not None:
                for key in ["model_file", "coordinates_file", "external_loads_file"]:
                    elem = id_tool_elem.find(key)
                    if elem is not None and elem.text:
                        file_path = elem.text
            """                
            time_range_elem = id_root.find(".//time_range")

            if time_range_elem is not None:
                new_time_range = f"{t_start} {t_end}"
                time_range_elem.text = new_time_range
            id_tree.write(paths["id_setup"], encoding="UTF-8", xml_declaration=True)
            """
        except Exception as e:
            print(f"[Warning] Could not parse ID setup file: {e}")
        
        inverse_dynamics(paths["id_setup"])
        
        id_result_candidates = glob.glob(os.path.join(output_dir, "*dynamics*.sto"))
        if id_result_candidates:
            paths['id_result'] = id_result_candidates[0]
        else:
            raise RuntimeError("ID failed: inverse_dynamics.sto not created.")
        
        if not os.path.exists(paths['id_result']):
            raise RuntimeError("ID failed: inverse_dynamics.sto not created.")

        _update_workflow_log(output_dir, run_id, 7, "InverseDynamics")  # method_id=7
    else:
        print("[Skip] ID is disabled in workflow config.")

    # 3. MocoInverse (Moco)
    solution = None
    mapping_config = None
    # MocoInverse_effort が有効かつ force plate がない場合はスキップ
    if wf["MocoInverse_effort"] and not has_force_plate:
        print("[Skip] MocoInverse skipped: force plate data (mot) not available.")
        wf["MocoInverse_effort"] = False
        wf["MocoInverse_emg"] = False
    if wf["MocoInverse_effort"]:
        # EMGデータの準備（sEMGがあり、かつ MocoInverse_emg が有効な場合のみ）
        use_emg = wf["MocoInverse_emg"] and has_emg
        if has_emg and not wf["MocoInverse_emg"]:
            print("[Info] sEMGデータあり、但し MocoInverse_emg=false のためEMG Trackingをスキップします。")
        if use_emg:
            temp_table = osim.TimeSeriesTable(paths['emg'])
            col_labels = [temp_table.getColumnLabel(i) for i in range(temp_table.getNumColumns())]
            mapping_config = generate_emg_mapping_config(input_map_df, master_model_df, col_labels)
        
        # Moco実行 (use_emg=False の場合、effortSolutionのみ実行される)
        solution = run_emg_tracking(
            model_path=paths['model'],      
            grf_xml_path=paths['grf_xml'],
            ik_mot_path=paths['ik_result'],
            emg_sto_path=paths.get('emg') if use_emg else None,
            mapping_config=mapping_config if use_emg else None,
            paths = paths,
            osim_cfg=osim_cfg
        )
        # mocoの成否を早期にチェック
        validation_data['moco_success'] = solution.success()
        validation_data['moco_objective'] = solution.getObjective()
        
        # ログ記録 (method_id=2: MocoInverse)
        _update_workflow_log(output_dir, run_id, 2, "MocoInverse")
        
        # trackingにjointsetを追加（EMG有効で trackingSolution がある場合のみmerge）
        if use_emg and paths.get('trackingSolution'):
            merge_kinematics_from_effort_solution(paths=paths)
        else:
            # EMG無効 or sEMGなし → effortSolution を下流の分析に使用
            paths['coord'] = paths['effortSolution']
        
        # 4. AnalyzeTool (BodyKinematicsなどを計算)
        if wf["AnalyzeTool"]:
            analyze(model_path= paths['moco_model'],
                    storage_path= paths['coord'],
                    ex_loads_path= paths['grf_xml'],
                    muscle_analyze= True,
                    force_report= True,
                    JointReaction= True,
                    body_kinematics=True)
            
            # ログ記録 (AnalyzeTool steps)
            _update_workflow_log(output_dir, run_id, 3, "AnalyzeTool_MuscleAnalysis")
            _update_workflow_log(output_dir, run_id, 5, "AnalyzeTool_ForceReporter")
            _update_workflow_log(output_dir, run_id, 4, "AnalyzeTool_JointReaction")
            _update_workflow_log(output_dir, run_id, 6, "AnalyzeTool_BodyKinematics")
        else:
            print("[Skip] AnalyzeTool is disabled in workflow config.")

        # 5. Reserve Actuator 分析 (MocoInverse + ID の両方が必要)
        if wf["ID"]:
            try:
                reserve_analysis = analyze_reserve_actuators(
                    coord_and_emgdriven_path=paths['coord'],
                    inverse_dynamics_path=paths['id_result'],
                    output_dir=output_dir
                )
                
                # JSON ファイルの生成を確認
                reserve_json_path = os.path.join(output_dir, 'reserve_analysis.json')
                if not os.path.exists(reserve_json_path):
                    print(f"[Warning] reserve_analysis.json NOT created at {reserve_json_path}")
                
                validation_data['reserve_analysis'] = reserve_analysis
                
                # validation_jsonにサマリー情報を追加
                if reserve_analysis.get('reserve_metrics'):
                    validation_data['reserve_avg_rms'] = reserve_analysis.get('avg_rms', 0.0)
                    validation_data['reserve_max_peak'] = reserve_analysis.get('max_peak', 0.0)
                    validation_data['reserve_success'] = reserve_analysis.get('reserve_success', False)
                
            except Exception as e:
                print(f"[Warning] Reserve Actuator analysis failed: {e}")
                traceback.print_exc()
                validation_data['reserve_analysis'] = {}
                validation_data['reserve_avg_rms'] = 0.0
                validation_data['reserve_max_peak'] = 0.0
                validation_data['reserve_success'] = False
        else:
            print("[Skip] ReserveAnalysis skipped (ID is disabled).")

        # 6. Moco精度検証メトリクスの計算
        try:
            # 検証に必要なファイルのパスを収集
            effort_file = paths.get('effortSolution')
            grf_mot_file = get_grf_path_from_xml(paths['grf_xml']) # grf.xmlから.motファイルパスを取得
            # Analyzeツールが出力したBodyKinematicsファイルを探す
            kinematics_file_candidates = glob.glob(os.path.join(output_dir, "*_BodyKinematics_pos_global.sto"))

            if effort_file and grf_mot_file and kinematics_file_candidates:
                kinematics_file = kinematics_file_candidates[0]
                
                # 精度検証関数を呼び出す
                moco_metrics = calculate_moco_metrics(
                    solution=solution,
                    effort_file_path=effort_file,
                    grf_mot_path=grf_mot_file,
                    body_kinematics_path=kinematics_file
                )
                validation_data.update(moco_metrics)
                if wf["RRA"]:
                    validation_data["residual_force_rms"] = validation_data[
                        "rra_residual_force_rms"
                    ]
                    validation_data["residual_moment_rms"] = validation_data[
                        "rra_residual_moment_rms"
                    ]
            else:
                print("[Warning] Moco精度検証に必要なファイルが不足しているため、計算をスキップします。")
                
            # --- DTW Calculation (EMG tracking有効時のみ) ---
            if use_emg and paths.get('trackingSolution') and paths.get('emg'):
                 dtw_metrics = calculate_dtw_metrics(paths['trackingSolution'], paths['emg'], mapping_config)
                 validation_data.update(dtw_metrics)
                 
        except Exception as e:
            print(f"[Error] Moco精度検証メトリクスの計算中にエラー: {e}")
    else:
        print("[Skip] MocoInverse_effort is disabled in workflow config.")
    
    # ID情報の付与 (pathsや引数から取れる場合)
    validation_data['run_id'] = run_id
    
    # reserve_analysisから詳細情報を削除（別ファイルに保存されているため）
    if 'reserve_analysis' in validation_data and isinstance(validation_data['reserve_analysis'], dict):
        if 'reserve_metrics' in validation_data['reserve_analysis']:
            del validation_data['reserve_analysis']['reserve_metrics']
    
    # JSON保存
    val_json_path = os.path.join(output_dir, 'validation.json')
    with open(val_json_path, 'w') as f:
        json.dump(validation_data, f, indent=4)
