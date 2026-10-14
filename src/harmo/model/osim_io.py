from __future__ import annotations

#-------------------------------------------------------
#スタディにあったモデルを生成するスクリプト
#-------------------------------------------------------
import os
import duckdb
import yaml
import sys
import opensim as osim
import pandas as pd
import glob
import json

from harmo.config.loader import load_config, get_primary_study_id
from harmo.warehouse.connection import get_warehouse_path

def remove_all_markers(model):
    """
    指定されたOpenSimモデルから、すべてのマーカーを削除します。

    Args:
        model (osim.Model): OpenSimモデルオブジェクト

    Returns:
        None
    """
    marker_set = model.getMarkerSet()
    markers_to_remove = list(marker_set)
    for marker in markers_to_remove:
        marker_set.remove(marker)
    print(f"モデル '{model.getName()}' からすべてのマーカーを削除しました。")
    

def get_marker_list(study_id):
    """
    icebergテーブルからマーカーデータを持ってくる。
    マーカー情報のリストを返す
    """
    
    _warehouse_path = get_warehouse_path()
    query = f"""
    SELECT marker, location, body
    FROM iceberg_scan('{_warehouse_path}/warehouse/dim_marker', allow_moved_paths = true)
    WHERE study_id = {study_id}
    """
    #マーカーデータのdfを取得
    df = duckdb.sql(query).fetchdf()
    #dfの列名は marker, location, body
    #それを
    # marker_list (list[dict]): マーカー情報のリスト
    # 例: [{"name": str, "segment": str, "coord": [float, float, float]}, ...]
    #の形に展開していく
    marker_list = []
    for index, row in df.iterrows():
        name = row['marker']
        segment = row['body']
        coord_str = row['location'] 
        coord = [float(x) for x in coord_str.split(",")]
        marker_list.append({
            "name": name,
            "segment": segment,
            "coord": coord
        })
    return marker_list
    


def add_marker(model_path, marker_list, output_path):
    """
    モデルにマーカーを追加し、新しいモデルファイルとして保存する

    Args:
        model_path (str): OpenSimモデルファイル(.osim)のパス
        marker_list (list[dict]): マーカー情報のリスト
            例: [{"name": str, "segment": str, "coord": [float, float, float]}, ...]
        output_path (str): 新しいモデル保存先のパス

    Returns:
        None
    """
    model = osim.Model(model_path)
    remove_all_markers(model)
    for marker in marker_list:
        name = marker["name"]
        segment = marker["segment"]
        coord = marker["coord"]
        try:
            body_obj = model.getBodySet().get(segment)
        except Exception:
            print(f"モデルにないセグメントです: {segment}")
            continue
        
        add = osim.Marker(name, body_obj, osim.Vec3(*coord))
        model.addMarker(add)
    # マーカーが追加できているか確認
    marker_set = model.getMarkerSet()
    opensim_names_array = osim.ArrayStr()
    marker_set.getMarkerNames(opensim_names_array)
    #marker_names_from_array = [opensim_names_array.get(i) for i in range(opensim_names_array.getSize())]
    # モデルを初期化する（システムを構築する）
    model.finalizeConnections()
    # モデルをファイルに保存する
    model.printToXML(output_path)
    print(f"{output_path}として保存")
    

def main(config_path: str):
    _osim_dir = os.environ.get('HARMO_OSIM_DIR', '/workspace/src/harmo/osim')
    output_dir = os.path.join(_osim_dir, "model")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    config = load_config(config_path)

    definitions = config.get('definitions', {})
    # --- study_id の取得 ---
    study_id = get_primary_study_id(definitions)
    model_path = os.path.join(_osim_dir, "model", "model_temp.osim")
    output_path = os.path.join(_osim_dir, "model", f"study{study_id}_pre_GUI.osim")
 
    marker_list = get_marker_list(study_id)
    add_marker(model_path, marker_list, output_path)


def export_provenance_data(run_id, search_dir, output_file):
    """
    指定されたrun_idに関連するすべての来歴CSVファイルを集約し、単一のJSONファイルに出力する。

    Args:
        run_id (str): 収集対象のバッチ実行ID。
        search_dir (str): 来歴CSVファイルが保存されているディレクトリ。
        output_file (str): 出力するJSONファイルのパス。
    """
    provenance_bundle = {}

    # 1. ワークフロー全体の来歴情報を読み込む
    workflow_prov_path = os.path.join(search_dir, f"{run_id}_provenance.csv")
    if os.path.exists(workflow_prov_path):
        workflow_df = pd.read_csv(workflow_prov_path)
        provenance_bundle['workflow_provenance'] = workflow_df.to_dict(orient='records')[0]
    else:
        print(f"  [Warning] Workflow provenance file not found: {workflow_prov_path}")
        provenance_bundle['workflow_provenance'] = {"error": "File not found"}

    # 2. 全トライアルのパラメータ情報を検索して読み込む
    param_files = glob.glob(os.path.join(search_dir, f"*{run_id}*_params.csv"))
    
    trial_parameters = []
    for f in param_files:
        params_df = pd.read_csv(f)
        if params_df.empty:
            continue
            
        # trialごとに情報をまとめる
        trial_info = params_df[['study_id', 'subject_id', 'session_id']].iloc[0].to_dict()
        
        # setup_name ごとにパラメータをグループ化
        setups = {}
        for setup_name, group in params_df.groupby('setup_name'):
            # 'value'列のJSON文字列を辞書に変換しようと試みる
            params_list = []
            for _, row in group.iterrows():
                try:
                    # 'value'がJSON形式の文字列（例：DataFrameを保存したもの）であればパースする
                    value_parsed = json.loads(row['value'])
                except (json.JSONDecodeError, TypeError):
                    # パースできなければ元の文字列をそのまま使う
                    value_parsed = row['value']
                params_list.append({'key': row['key'], 'value': value_parsed})

            setups[setup_name] = params_list
        
        trial_info['setups'] = setups
        trial_parameters.append(trial_info)

    provenance_bundle['trial_parameters'] = trial_parameters
    
    # 3. JSONファイルに書き出す
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(provenance_bundle, f, indent=4, ensure_ascii=False)
        print(f"  [Provenance] Exported aggregated provenance for run '{run_id}' to {output_file}")
    except Exception as e:
        print(f"  [Error] Failed to write provenance JSON file: {e}")