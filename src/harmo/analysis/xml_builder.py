from __future__ import annotations

"""
Opensimで使うセットアップファイル（.xml）を作るための関数
このセットアップファイルの中にマーカーファイル(.trc)やモデルファイル(.osim)のパスを指定しておくと、
OpenSimで読み込むときに自動的にそれらのファイルを読み込んでくれる。
"""

# set_upファイル（.xml）の作成
import xml.etree.ElementTree as ET
import xml.dom.minidom
import pandas as pd
import sys

def indent_xml(tree, space="\t", level=0):
    """
    Python 3.8互換性を保つためのXMLインデント関数。
    ET.indent()が利用可能な場合(Python 3.9+)はそれを使い、
    そうでない場合はminidomを使ってインデント処理を行う。
    """
    if sys.version_info >= (3, 9):
        # Python 3.9以降
        try:
            ET.indent(tree, space=space, level=level)
            return
        except AttributeError:
            pass
    
    # Python 3.8以前の互換性処理: minidomを使用
    rough_string = ET.tostring(tree.getroot(), encoding='unicode')
    reparsed = xml.dom.minidom.parseString(rough_string)
    pretty_xml = reparsed.toprettyxml(indent=space)
    
    # XMLの最初の行（宣言）を削除して、tree.writeで自動生成させるようにする
    lines = pretty_xml.split('\n')
    if lines[0].startswith('<?xml'):
        pretty_xml = '\n'.join(lines[1:]).lstrip('\n')
    
    # 元のtreeの内容を置き換え
    new_root = ET.fromstring(pretty_xml.encode('utf-8') if isinstance(pretty_xml, str) else pretty_xml)
    tree._setroot(new_root)


def build_measurement_set_xml(measurements_config):
    """osim_analysis_config.yaml の measurements 辞書から
    <MeasurementSet> の <objects> 内に挿入する Measurement 要素リストを生成する。

    Args:
        measurements_config (dict): e.g.
            {"pelvis": {"marker_pair_set": [["RASIS","LASIS"]], "body_scale_set": {"pelvis": ["X","Y","Z"]}}}

    Returns:
        list[ET.Element]: Measurement 要素のリスト
    """
    elements = []
    for meas_name, meas in measurements_config.items():
        m_elem = ET.Element("Measurement", attrib={"name": str(meas_name)})
        apply_elem = ET.SubElement(m_elem, "apply")
        apply_elem.text = "true"

        # MarkerPairSet
        mps = ET.SubElement(m_elem, "MarkerPairSet")
        mps_objects = ET.SubElement(mps, "objects")
        for pair in (meas.get("marker_pair_set") or []):
            mp = ET.SubElement(mps_objects, "MarkerPair")
            markers_elem = ET.SubElement(mp, "markers")
            markers_elem.text = " " + " ".join(str(p) for p in pair)
        ET.SubElement(mps, "groups")

        # BodyScaleSet
        bss = ET.SubElement(m_elem, "BodyScaleSet")
        bss_objects = ET.SubElement(bss, "objects")
        for body_name, axes in meas.get("body_scale_set", {}).items():
            bs = ET.SubElement(bss_objects, "BodyScale", attrib={"name": str(body_name)})
            axes_elem = ET.SubElement(bs, "axes")
            axes_elem.text = " " + " ".join(str(a) for a in axes)
        ET.SubElement(bss, "groups")

        elements.append(m_elem)
    return elements


def update_scaletool(input_xml_path, output_xml_path, new_values):
    """
    OpenSimのScaleTool設定XMLファイルを更新する関数。

    Args:
        input_xml_path (str): 入力XMLファイルのパス。
        output_xml_path (str): 更新されたXMLを保存する出力ファイルのパス。
        new_values (dict): 更新したい値を格納した辞書。 キーはXML要素名に対応し、値は更新する値。
                           追加キー 'measurements_config' で MeasurementSet を差し替え可能。
    """
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    scale_tool = root.find(".//ScaleTool")
    if scale_tool is None:
        print("ScaleTool要素が見つかりません。")
        return

    # ScaleToolの直接の子要素を更新
    if "name" in new_values:
        scale_tool.set("name", new_values["name"])
    if "mass" in new_values:
        mass_elem = scale_tool.find("mass")
        if mass_elem is not None:
            mass_elem.text = str(new_values["mass"])
    if "height" in new_values:
        height_elem = scale_tool.find("height")
        if height_elem is not None:
            height_elem.text = str(new_values["height"])
    if "age" in new_values:
        age_elem = scale_tool.find("age")
        if age_elem is not None:
            age_elem.text = str(new_values["age"])
    if "notes" in new_values:
        notes_elem = scale_tool.find("notes") # <-- 修正: 変数名を 'age_elem' から 'notes_elem' に変更
        if notes_elem is not None:
            notes_elem.text = new_values["notes"]

    # GenericModelMaker内の要素を更新
    generic_model_maker = scale_tool.find("GenericModelMaker")
    if generic_model_maker is not None:
        if "model_file" in new_values:
            model_file_elem = generic_model_maker.find("model_file")
            if model_file_elem is not None:
                model_file_elem.text = new_values["model_file"]
        if "marker_set_file" in new_values:
            marker_set_file_elem = generic_model_maker.find("marker_set_file")
            if marker_set_file_elem is not None:
                marker_set_file_elem.text = new_values["marker_set_file"]

    # ModelScaler内の要素を更新
    model_scaler = scale_tool.find("ModelScaler")
    if model_scaler is not None:
        if "marker_file" in new_values:
            marker_file_elem = model_scaler.find("marker_file")
            if marker_file_elem is not None:
                marker_file_elem.text = new_values["marker_file"]
        if "time_range_start" in new_values and "time_range_end" in new_values:
            time_range_elem = model_scaler.find("time_range")
            if time_range_elem is not None:
                time_range_elem.text = f"{new_values['time_range_start']:.8f} {new_values['time_range_end']:.8f}"
        if "output_model_file" in new_values:
            output_model_file_elem = model_scaler.find("output_model_file")
            if output_model_file_elem is not None:
                output_model_file_elem.text = new_values["output_model_file"]
        if "output_scale_file" in new_values:
            output_scale_file_elem = model_scaler.find("output_scale_file")
            if output_scale_file_elem is not None:
                output_scale_file_elem.text = new_values["output_scale_file"]

    # MarkerPlacer内の要素を更新
    marker_placer = scale_tool.find("MarkerPlacer")
    if marker_placer is not None:
        if "output_model_file" in new_values:
            mp_output_model_file_elem = marker_placer.find("output_model_file")
            if mp_output_model_file_elem is not None:
                mp_output_model_file_elem.text = new_values["output_model_file"]
        if "output_motion_file" in new_values:
            mp_output_motion_file_elem = marker_placer.find("output_motion_file") # <-- 修正: 変数名を変更
            if mp_output_motion_file_elem is not None:
                mp_output_motion_file_elem.text = new_values["output_motion_file"]
        if "marker_file" in new_values:
            mp_marker_file_elem = marker_placer.find("marker_file") # <-- 修正: 変数名を変更
            if mp_marker_file_elem is not None:
                mp_marker_file_elem.text = new_values["marker_file"]
        if "time_range_start" in new_values and "time_range_end" in new_values:
            mp_time_range_elem = marker_placer.find("time_range") # <-- 修正: 変数名を変更
            if mp_time_range_elem is not None:
                mp_time_range_elem.text = f"{new_values['time_range_start']:.8f} {new_values['time_range_end']:.8f}"
                
    # --- MeasurementSet の差し替え (osim_analysis_config.yaml 由来) ---
    if "measurements_config" in new_values:
        meas_cfg = new_values["measurements_config"]
        if meas_cfg and isinstance(meas_cfg, dict):
            model_scaler = scale_tool.find("ModelScaler")
            if model_scaler is not None:
                # scaling_order を確実にセット
                so_elem = model_scaler.find("scaling_order")
                if so_elem is not None and (not so_elem.text or not so_elem.text.strip()):
                    so_elem.text = " measurements"

                ms_elem = model_scaler.find("MeasurementSet")
                if ms_elem is not None:
                    obj = ms_elem.find("objects")
                    if obj is None:
                        obj = ET.SubElement(ms_elem, "objects")
                    else:
                        # 既存の Measurement を全削除
                        for child in list(obj):
                            obj.remove(child)
                    for m_elem in build_measurement_set_xml(meas_cfg):
                        obj.append(m_elem)

    mp_task_set = marker_placer.find("IKTaskSet/objects")
    if mp_task_set is not None and "marker_placer_tasks_df" in new_values:
        marker_df = new_values["marker_placer_tasks_df"]
        if 'body' not in marker_df.columns:
            if 'coord' in marker_df.columns:
                marker_df.rename(columns={'coord': 'body'}, inplace=True)
        # DataFrameに必要な列があるかチェック
        if not all(col in marker_df.columns for col in ['marker', 'scale_apply', 'scale_weight']):
            print("エラー: 'marker_placer_tasks_df' には 'marker', 'scale_apply', 'scale_weight' 列が必要です。")
        else:
            # DataFrameをイテレートしてXMLを更新
            for index, row in marker_df.iterrows():
                marker_name = row['marker']
                apply_value = row['scale_apply']
                weight_value = row['scale_weight']

                # XPathで該当するマーカータスクを検索 (MarkerTask)
                marker_task = mp_task_set.find(f"IKMarkerTask[@name='{marker_name}']")

                if marker_task is not None:
                    # 既存タスクの更新
                    apply_elem = marker_task.find("scale_apply")
                    if apply_elem is not None:
                        apply_elem.text = str(apply_value).lower()

                    weight_elem = marker_task.find("scale_weight")
                    if weight_elem is not None:
                        weight_elem.text = str(weight_value)
                else:
                    # 新規タスクの作成 (MarkerTask)
                    new_marker_task = ET.SubElement(mp_task_set, "IKMarkerTask", attrib={"name": marker_name})

                    apply_elem = ET.SubElement(new_marker_task, "apply")
                    apply_elem.text = str(apply_value).lower()

                    weight_elem = ET.SubElement(new_marker_task, "weight")
                    weight_elem.text = str(weight_value)
                        
    # 更新されたXMLを整形して保存
    #ET.indent(tree, space="\t", level=0) # 整形のためのインデント
    tree.write(output_xml_path, encoding="UTF-8", xml_declaration=True)
    print(f"ScaleTool設定ファイルが '{output_xml_path}' に更新されました。") # <-- 修正: メッセージを具体化
    return new_values

"""
# --- update_scaletool 使用例 ---
input_xml_scale = "../model/subject01_Setup_Scale.xml" # OpenSimモデルのScaleTool設定ファイル
output_xml_scale = "s31_1_Setup_Scale.xml"  # 新しい出力ファイル名

# 更新したい値を辞書で定義
# 身長とかは、データベースから取り出してくる
my_new_scale_values = {
    "name": "31_1",
    "mass": 66.2,
    "height": 1810.0,
    "age": 25.0,
    "model_file": "/home/ota/Desktop/osim/model/gait2392_simbody.osim",
    "marker_set_file": "/home/ota/Desktop/osim/model/gait2392_Scale_MarkerSet.xml",
    "marker_file": "/home/ota/Desktop/osim/s31_p1_1_2/s1_1_2.trc", # ModelScaler と MarkerPlacer の両方で使われる
    "time_range_start": 0.0, # ModelScaler と MarkerPlacer の両方で使われる
    "time_range_end": 0.01,
    "output_model_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_scaled.osim", # ModelScaler と MarkerPlacer の両方で使われる
    "output_scale_file": "/home/ota/Desktop/osim/s31_p1_1_2/applied.xml",
    "output_motion_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_applied.mot"
}

# 関数呼び出し
# update_scaletool(input_xml_scale, output_xml_scale, my_new_scale_values)
"""


def update_ik(input_xml_path, output_xml_path, new_values):
    """
    OpenSimのInverseKinematicsTool設定XMLファイルを更新する関数。

    Args:
        input_xml_path (str): 入力XMLファイルのパス。
        output_xml_path (str): 更新されたXMLを保存する出力ファイルのパス。
        new_values (dict): 更新したい値を格納した辞書。
                           マーカー設定は 'marker_tasks_df' キーに
                           'marker', 'ik_apply', 'weight' 列を持つDataFrameを指定。
    """
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    ik_tool = root.find(".//InverseKinematicsTool")
    if ik_tool is None:
        print("InverseKinematicsTool要素が見つかりません。")
        return

    # InverseKinematicsToolの直接の子要素を更新
    if "name" in new_values:
        ik_tool.set("name", new_values["name"])
    if "results_directory" in new_values:
        results_dir_elem = ik_tool.find("results_directory")
        if results_dir_elem is not None:
            results_dir_elem.text = new_values["results_directory"]
    if "input_directory" in new_values:
        input_dir_elem = ik_tool.find("input_directory")
        if input_dir_elem is not None:
            input_dir_elem.text = new_values["input_directory"]
    if "model_file" in new_values:
        model_file_elem = ik_tool.find("model_file")
        if model_file_elem is not None:
            model_file_elem.text = new_values["model_file"]
    if "constraint_weight" in new_values:
        constraint_weight_elem = ik_tool.find("constraint_weight")
        if constraint_weight_elem is not None:
            constraint_weight_elem.text = str(new_values["constraint_weight"])
    if "accuracy" in new_values:
        accuracy_elem = ik_tool.find("accuracy")
        if accuracy_elem is not None:
            accuracy_elem.text = str(new_values["accuracy"])
    if "marker_file" in new_values:
        marker_file_elem = ik_tool.find("marker_file")
        if marker_file_elem is not None:
            marker_file_elem.text = new_values["marker_file"]
    if "coordinate_file" in new_values:
        coordinate_file_elem = ik_tool.find("coordinate_file")
        if coordinate_file_elem is not None:
            coordinate_file_elem.text = new_values["coordinate_file"]
    if "time_range_start" in new_values and "time_range_end" in new_values:
        time_range_elem = ik_tool.find("time_range")
        if time_range_elem is not None:
            time_range_elem.text = f"{new_values['time_range_start']:.8f} {new_values['time_range_end']:.8f}"
    if "report_errors" in new_values:
        report_errors_elem = ik_tool.find("report_errors")
        if report_errors_elem is not None:
            report_errors_elem.text = str(new_values["report_errors"]).lower()
    if "output_motion_file" in new_values:
        output_motion_file_elem = ik_tool.find("output_motion_file")
        if output_motion_file_elem is not None:
            output_motion_file_elem.text = new_values["output_motion_file"]

    # IKTaskSet内のIKMarkerTaskをDataFrameに基づいて更新
    ik_task_set = ik_tool.find("IKTaskSet/objects")
    if ik_task_set is not None and "marker_tasks_df" in new_values:
        marker_df = new_values["marker_tasks_df"]
        
        if not all(col in marker_df.columns for col in ['marker', 'ik_apply', 'ik_weight']):
            print("エラー: 'marker_tasks_df' には 'marker', 'ik_apply', 'ik_weight' 列が必要です。")
        else:
            for index, row in marker_df.iterrows():
                marker_name = row['marker']
                apply_value = row['ik_apply']
                weight_value = row['ik_weight']
                
                marker_task = ik_task_set.find(f"IKMarkerTask[@name='{marker_name}']")
                
                if marker_task is not None:
                    # 既存タスクの更新
                    apply_elem = marker_task.find("apply")
                    if apply_elem is not None:
                        apply_elem.text = str(apply_value).lower() 
                    
                    weight_elem = marker_task.find("weight")
                    if weight_elem is not None:
                        weight_elem.text = str(weight_value)
                else:
                    # 新規タスクの作成
                    new_marker_task = ET.SubElement(ik_task_set, "IKMarkerTask", attrib={"name": marker_name})
                    
                    apply_elem = ET.SubElement(new_marker_task, "apply")
                    apply_elem.text = str(apply_value).lower()
                    
                    weight_elem = ET.SubElement(new_marker_task, "weight")
                    weight_elem.text = str(weight_value)
                    
    # 更新されたXMLを整形して保存
    #ET.indent(tree, space="\t", level=0)
    tree.write(output_xml_path, encoding="UTF-8", xml_declaration=True)
    print(f"IK設定ファイルが '{output_xml_path}' に更新されました。")
    return new_values
    
    
"""
# --- update_ik 使用例 ---
input_xml_ik = "/home/ota/Desktop/osim/model/subject01_Setup_IK.xml"
output_xml_ik = "s31_1_V35_T2_Setup_IK.xml"

# マーカー設定をDataFrameで定義
marker_data_ik = {
    'marker': [
        'R.Shoulder', 'L.Shoulder', 'R.ASIS', 'L.ASIS', 'R.PSIS', 'L.PSIS', 
        'R.Knee', 'L.Knee', 'R.Ankle', 'L.Ankle', 'R.Heel', 'L.Heel', 
        'R.MT5', 'L.MT5', 'R.Toe', 'L.Toe', 'R.Biceps'
    ],
    'ik_apply': [
        True, True, True, True, True, True, 
        True, True, True, True, True, True, 
        True, True, False, False, True
    ],
    'weight': [
        5, 5, 100, 100, 50, 50, 
        75, 75, 75, 75, 50, 50, 
        50, 50, 10, 10, 2
    ]
}
marker_df_ik = pd.DataFrame(marker_data_ik)

# 更新したい値を辞書で定義
my_new_ik_values = {
    "name": "31_1",
    "results_directory": "/home/ota/Desktop/osim/s31_p1_1_2/",
    "model_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_scaled.osim",
    "marker_file": "/home/ota/Desktop/osim/s31_p1_1_2/s1_V35_T2.trc",
    "time_range_start": 1.05,
    "time_range_end": 2.385,
    "output_motion_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_V35_T2_ik.mot",
    "marker_tasks_df": marker_df_ik # DataFrameを辞書に追加
}

# 関数呼び出し
# update_ik(input_xml_ik, output_xml_ik, my_new_ik_values)
"""


def update_grf_xml(input_xml_path: str, output_xml_path: str, updates: dict):
    """
    OpenSimのExternalLoads設定XMLファイルを更新する関数。

    Args:
        input_xml_path (str): 入力XMLファイルのパス。
        output_xml_path (str): 更新されたXMLを保存する出力ファイルのパス。
        updates (dict): 更新したい値を格納した辞書。
    """
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    external_loads = root.find(".//ExternalLoads")
    if external_loads is None:
        print("警告: XMLファイル内に<ExternalLoads>要素が見つかりません。")
        return

    # ExternalLoads の属性と子要素を更新
    if "datafile" in updates:
        data_file_elem = external_loads.find("datafile")
        if data_file_elem is not None:
            data_file_elem.text = updates["datafile"]
            
    if "kinematics_lowpass_cutoff" in updates:
        # これは属性なので .set() を使用
        external_loads.set("kinematics_lowpass_cutoff", str(updates["kinematics_lowpass_cutoff"]))
    objects_elem = external_loads.find(".//objects")
    # ExternalForce オブジェクトの更新
    for ext_force in external_loads.findall(".//ExternalForce"):
        name = ext_force.get("name")
        
        if name == "Right_GRF" and updates.get("right_leg") == "delete":
            if objects_elem is not None:
                objects_elem.remove(ext_force)
                print("情報: XMLから Right_GRF 要素を削除しました。")
                continue
        if name == "Left_GRF" and updates.get("left_leg") == "delete":
            if objects_elem is not None:
                objects_elem.remove(ext_force)
                print("情報: XMLから Left_GRF 要素を削除しました。")
                continue
        
        if name == "Right_GRF":
            if "ground_force_r_v" in updates:
                force_id_elem = ext_force.find("force_identifier")
                if force_id_elem is not None: force_id_elem.text = updates["ground_force_r_v"]
            if "ground_force_r_p" in updates:
                point_id_elem = ext_force.find("point_identifier")
                if point_id_elem is not None: point_id_elem.text = updates["ground_force_r_p"]
            if "ground_torque_r_" in updates:
                torque_id_elem = ext_force.find("torque_identifier")
                if torque_id_elem is not None: torque_id_elem.text = updates["ground_torque_r_"]

        elif name == "Left_GRF":
            if "ground_force_l_v" in updates:
                force_id_elem = ext_force.find("force_identifier")
                if force_id_elem is not None: force_id_elem.text = updates["ground_force_l_v"]
            if "ground_force_l_p" in updates:
                point_id_elem = ext_force.find("point_identifier")
                if point_id_elem is not None: point_id_elem.text = updates["ground_force_l_p"]
            if "ground_torque_l_" in updates:
                torque_id_elem = ext_force.find("torque_identifier")
                if torque_id_elem is not None: torque_id_elem.text = updates["ground_torque_l_"]

    #ET.indent(tree, space="\t", level=0) # <-- 修正: インデントをタブに
    
    tree.write(output_xml_path, encoding="UTF-8", xml_declaration=True)
    print(f"ExternalLoads設定ファイルが '{output_xml_path}' に更新されました。") # <-- 修正: メッセージを具体化
    return updates

"""
# --- update_grf_xml 使用例 ---

input_xml_grf = "/home/ota/Desktop/harmo-semg-main/opensim-ad-docker/data/P02/externalLoads.xml"
output_xml_grf = "s31_1_grf.xml"

# 更新内容を定義
updates_to_apply_grf = {
    "datafile": "/home/ota/Desktop/osim/s31_p1_1_2/1_V35_T2.mot",
    "kinematics_lowpass_cutoff": 6.0,
    "ground_force_r_v": "ground_force_r_v", # .motファイル内の列名
    "ground_force_r_p": "ground_force_r_p", # .motファイル内の列名
    "ground_force_l_v": "ground_force_l_v", # .motファイル内の列名
    "ground_force_l_p": "ground_force_l_p"  # .motファイル内の列名
}

# 関数を呼び出し
# update_grf_xml(input_xml_grf, output_xml_grf, updates_to_apply_grf)
"""


def update_markerset_xml(input_xml_path: str, output_xml_path: str, updates: dict):
    """
    OpenSimのMarkerSet設定XMLファイルを更新する関数。
    
    Args:
        input_xml_path (str): 入力XMLファイルのパス。
        output_xml_path (str): 更新されたXMLを保存する出力ファイルのパス。
        updates (dict): 更新したい値を格納した辞書。
                        マーカー設定は 'marker_df' キーに
                        'marker', 'body', 'location' 列を持つDataFrameを指定。
    """
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    markerset = root.find(".//MarkerSet")
    if markerset is None:
        print("警告: XMLファイル内に<MarkerSet>要素が見つかりません。")
        return

    if "name" in updates:
        markerset.set("name", updates["name"])
        
    marker_objects = markerset.find("objects")
    if marker_objects is not None and "marker_df" in updates: # <-- 修正: キー名を 'marker_tasks_df' から 'marker_df' に
        marker_df = updates["marker_df"]
        
        if not all(col in marker_df.columns for col in ['marker', 'body', 'location']):
            print("エラー: 'marker_df' には 'marker', 'body', 'location' 列が必要です。")
        else:
            for index, row in marker_df.iterrows():
                marker_name = row['marker']
                body_value = row['body']
                location_value = row['location']
                
                marker_task = marker_objects.find(f"Marker[@name='{marker_name}']") # <-- 修正: 親要素を 'marker_objects' に
                
                if marker_task is not None:
                    # <body> 要素を更新
                    body_elem = marker_task.find("body")
                    if body_elem is not None:
                        body_elem.text = str(body_value) # <-- 修正: .lower() を削除
                    
                    # <location> 要素を更新
                    location_elem = marker_task.find("location")
                    if location_elem is not None:
                        location_elem.text = str(location_value)
                else:
                    # 新規マーカーの作成
                    new_marker_task = ET.SubElement(marker_objects, "Marker", attrib={"name": marker_name}) # <-- 修正: タグ名を 'Marker' に
                    
                    body_elem = ET.SubElement(new_marker_task, "body")
                    body_elem.text = str(body_value) # <-- 修正: .lower() を削除
                    
                    location_elem = ET.SubElement(new_marker_task, "location")
                    location_elem.text = str(location_value)
                    
                    fixed_elem = ET.SubElement(new_marker_task, "fixed")
                    fixed_elem.text = "true"
                    
    #ET.indent(tree, space="\t", level=0) # <-- 修正: インデントをタブに
    
    tree.write(output_xml_path, encoding="UTF-8", xml_declaration=True)
    print(f"MarkerSet設定ファイルが '{output_xml_path}' に更新されました。") # <-- 修正: メッセージを具体化
    return updates

"""
# --- update_markerset_xml 使用例 ---
input_xml_markers = "/home/ota/Desktop/osim/model/gait2392_Scale_MarkerSet.xml"
output_xml_markers = "s31_1_MarkerSet.xml"

# マーカー設定をDataFrameで定義
# 更新したい値を辞書で定義
my_new_marker_values = {
    "name": "s31_1_markers",
    "marker_df": marker_df_ms # DataFrameを辞書に追加
}

# 関数呼び出し
# update_markerset_xml(input_xml_markers, output_xml_markers, my_new_marker_values)
"""


def update_id(input_xml_path, output_xml_path, new_values):
    """
    OpenSimのInverseDynamicsTool設定XMLファイルを更新する関数。

    Args:
        input_xml_path (str): 入力XMLファイルのパス。
        output_xml_path (str): 更新されたXMLを保存する出力ファイルのパス。
        new_values (dict): 更新したい値を格納した辞書。
    """
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    id_tool = root.find(".//InverseDynamicsTool")
    if id_tool is None:
        print("InverseDynamicsTool要素が見つかりません。")
        return

    # InverseDynamicsToolの直接の子要素を更新
    if "name" in new_values:
        id_tool.set("name", new_values["name"])
    if "results_directory" in new_values:
        results_dir_elem = id_tool.find("results_directory")
        if results_dir_elem is not None:
            results_dir_elem.text = new_values["results_directory"]
    if "input_directory" in new_values:
        input_dir_elem = id_tool.find("input_directory")
        if input_dir_elem is not None:
            input_dir_elem.text = new_values["input_directory"]
    if "model_file" in new_values:
        model_file_elem = id_tool.find("model_file")
        if model_file_elem is not None:
            model_file_elem.text = new_values["model_file"]
    if "lowpass_cutoff_frequency_for_coordinates" in new_values:
        cutoff_elem = id_tool.find("lowpass_cutoff_frequency_for_coordinates") # <-- 修正: 変数名を変更
        if cutoff_elem is not None:
            cutoff_elem.text = str(new_values["lowpass_cutoff_frequency_for_coordinates"])
    if "output_gen_force_file" in new_values:
        output_gen_force_elem = id_tool.find("output_gen_force_file") # <-- 修正: 変数名を変更
        if output_gen_force_elem is not None:
            output_gen_force_elem.text = new_values["output_gen_force_file"]
    if "output_body_forces_file" in new_values:
        output_body_forces_elem = id_tool.find("output_body_forces_file") # <-- 修正: 変数名とキー名を変更
        if output_body_forces_elem is not None:
            output_body_forces_elem.text = new_values["output_body_forces_file"]
    if "time_range_start" in new_values and "time_range_end" in new_values:
        time_range_elem = id_tool.find("time_range")
        if time_range_elem is not None:
            time_range_elem.text = f"{new_values['time_range_start']:.8f} {new_values['time_range_end']:.8f}"
    if "external_loads_file" in new_values:
        external_loads_elem = id_tool.find("external_loads_file") # <-- 修正: 変数名を変更
        if external_loads_elem is not None:
            external_loads_elem.text = new_values["external_loads_file"]
    if "coordinates_file" in new_values:
        coordinates_file_elem = id_tool.find("coordinates_file") # <-- 修正: 変数名を変更
        if coordinates_file_elem is not None:
            coordinates_file_elem.text = new_values["coordinates_file"]

    # 更新されたXMLを整形して保存
    indent_xml(tree, space="\t", level=0)
    tree.write(output_xml_path, encoding="UTF-8", xml_declaration=True)
    print(f"ID設定ファイルが '{output_xml_path}' に更新されました。") # <-- 修正: メッセージを具体化
    return new_values

"""
# --- update_id 使用例 ---
input_xml_id = "/home/ota/Desktop/osim/model/subject01_Setup_ID.xml"
output_xml_id = "s31_1_Setup_ID.xml"

# 更新したい値を辞書で定義
my_new_id_values = {
    "name": "s31_1_id",
    "results_directory": "/home/ota/Desktop/osim/s31_p1_1_2/ID_Results/",
    "model_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_scaled.osim",
    "lowpass_cutoff_frequency_for_coordinates": 6.0,
    "output_gen_force_file": "s31_1_id_output.sto",
    "time_range_start": 1.05,
    "time_range_end": 2.385,
    "external_loads_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_grf.xml",
    "coordinates_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_V35_T2_ik.mot"
}

# 関数呼び出し
# update_id(input_xml_id, output_xml_id, my_new_id_values)
"""


def update_cmc(input_xml_path, output_xml_path, new_values):
    """
    OpenSimのCMCTool設定XMLファイルを更新する関数。

    Args:
        input_xml_path (str): 入力XMLファイルのパス。
        output_xml_path (str): 更新されたXMLを保存する出力ファイルのパス。
        new_values (dict): 更新したい値を格納した辞書。
    """
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    cmc_tool = root.find(".//CMCTool")
    if cmc_tool is None:
        print("CMCTool要素が見つかりません。")
        return

    if "name" in new_values:
        cmc_tool.set("name", new_values["name"])
    
    if "model_file" in new_values:
        elem = cmc_tool.find("model_file")
        if elem is not None:
            elem.text = new_values["model_file"]

    if "force_set_files" in new_values:
        elem = cmc_tool.find("force_set_files")
        if elem is not None:
            elem.text = new_values["force_set_files"]

    if "results_directory" in new_values:
        elem = cmc_tool.find("results_directory")
        if elem is not None:
            elem.text = new_values["results_directory"]

    if "initial_time" in new_values:
        elem = cmc_tool.find("initial_time")
        if elem is not None:
            elem.text = str(new_values["initial_time"])

    if "final_time" in new_values:
        elem = cmc_tool.find("final_time")
        if elem is not None:
            elem.text = str(new_values["final_time"])

    if "external_loads_file" in new_values:
        elem = cmc_tool.find("external_loads_file")
        if elem is not None:
            elem.text = new_values["external_loads_file"]

    if "desired_kinematics_file" in new_values:
        elem = cmc_tool.find("desired_kinematics_file")
        if elem is not None:
            elem.text = new_values["desired_kinematics_file"]

    if "task_set_file" in new_values:
        elem = cmc_tool.find("task_set_file")
        if elem is not None:
            elem.text = new_values["task_set_file"]

    if "constraints_file" in new_values:
        elem = cmc_tool.find("constraints_file")
        if elem is not None:
            elem.text = new_values["constraints_file"]

    if "rra_controls_file" in new_values:
        elem = cmc_tool.find("rra_controls_file")
        if elem is not None:
            elem.text = new_values["rra_controls_file"]

    if "lowpass_cutoff_frequency" in new_values:
        elem = cmc_tool.find("lowpass_cutoff_frequency")
        if elem is not None:
            elem.text = str(new_values["lowpass_cutoff_frequency"])
    
    if "cmc_time_window" in new_values:
        elem = cmc_tool.find("cmc_time_window")
        if elem is not None:
            elem.text = str(new_values["cmc_time_window"])
            
    if "use_fast_optimization_target" in new_values:
        elem = cmc_tool.find("use_fast_optimization_target")
        if elem is not None:
            elem.text = str(new_values["use_fast_optimization_target"]).lower()

    indent_xml(tree, space="\t", level=0)
    tree.write(output_xml_path, encoding="UTF-8", xml_declaration=True)
    print(f"CMC設定ファイルが '{output_xml_path}' に更新されました。")
    return new_values

"""
# --- update_cmc の使用例 ---
input_xml_cmc = "/home/ota/Desktop/osim/model/cmc_setup_P02_V3.xml"
output_xml_cmc = "s31_1_Setup_CMC.xml"

my_new_cmc_values = {
    "name": "s31_1_cmc",
    "model_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_rra_adjusted.osim",
    "results_directory": "/home/ota/Desktop/osim/s31_p1_1_2/CMC_Results/",
    "initial_time": 1.05,
    "final_time": 2.385,
    "external_loads_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_grf.xml",
    "desired_kinematics_file": "/home/ota/Desktop/osim/s31_p1_1_2/RRA_Results/s31_1_RRA_Kinematics_q.sto",
    "task_set_file": "/home/ota/Desktop/osim/model/cmc_tasks_custom.xml",
    "constraints_file": "/home/ota/Desktop/osim/model/cmc_control_constraints_custom.xml",
    "rra_controls_file": "/home/ota/Desktop/osim/s31_p1_1_2/RRA_Results/s31_1_RRA_Controls.xml"
}

# 関数呼び出し
# update_cmc(input_xml_cmc, output_xml_cmc, my_new_cmc_values)
"""


def update_rra(input_xml_path, output_xml_path, new_values):
    """
    OpenSimのRRATool設定XMLファイルを更新する関数。

    Args:
        input_xml_path (str): 入力XMLファイルのパス。
        output_xml_path (str): 更新されたXMLを保存する出力ファイルのパス。
        new_values (dict): 更新したい値を格納した辞書。
    """
    tree = ET.parse(input_xml_path)
    root = tree.getroot()

    rra_tool = root.find(".//RRATool")
    if rra_tool is None:
        print("RRATool要素が見つかりません。")
        return

    if "name" in new_values:
        rra_tool.set("name", new_values["name"])
    
    if "model_file" in new_values:
        elem = rra_tool.find("model_file")
        if elem is not None:
            elem.text = new_values["model_file"]

    if "force_set_files" in new_values:
        elem = rra_tool.find("force_set_files")
        if elem is not None:
            elem.text = new_values["force_set_files"]

    if "results_directory" in new_values:
        elem = rra_tool.find("results_directory")
        if elem is not None:
            elem.text = new_values["results_directory"]

    if "initial_time" in new_values:
        elem = rra_tool.find("initial_time")
        if elem is not None:
            elem.text = str(new_values["initial_time"])

    if "final_time" in new_values:
        elem = rra_tool.find("final_time")
        if elem is not None:
            elem.text = str(new_values["final_time"])

    if "external_loads_file" in new_values:
        elem = rra_tool.find("external_loads_file")
        if elem is not None:
            elem.text = new_values["external_loads_file"]

    if "desired_kinematics_file" in new_values:
        elem = rra_tool.find("desired_kinematics_file")
        if elem is not None:
            elem.text = new_values["desired_kinematics_file"]

    if "task_set_file" in new_values:
        elem = rra_tool.find("task_set_file")
        if elem is not None:
            elem.text = new_values["task_set_file"]

    if "lowpass_cutoff_frequency" in new_values:
        elem = rra_tool.find("lowpass_cutoff_frequency")
        if elem is not None:
            elem.text = str(new_values["lowpass_cutoff_frequency"])
            
    if "adjust_com_to_reduce_residuals" in new_values:
        elem = rra_tool.find("adjust_com_to_reduce_residuals")
        if elem is not None:
            elem.text = str(new_values["adjust_com_to_reduce_residuals"]).lower()
            
    if "adjusted_com_body" in new_values:
        elem = rra_tool.find("adjusted_com_body")
        if elem is not None:
            elem.text = new_values["adjusted_com_body"]
            
    if "output_model_file" in new_values:
        elem = rra_tool.find("output_model_file")
        if elem is not None:
            elem.text = new_values["output_model_file"]

    indent_xml(tree, space="\t", level=0)
    tree.write(output_xml_path, encoding="UTF-8", xml_declaration=True)
    print(f"RRA設定ファイルが '{output_xml_path}' に更新されました。")
    return new_values

"""
# --- update_rra の使用例 ---
input_xml_rra = "/home/ota/Desktop/osim/model/RRA_setup.xml"
output_xml_rra = "s31_1_Setup_RRA.xml"

my_new_rra_values = {
    "name": "s31_1_rra",
    "model_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_scaled.osim",
    "force_set_files": "/home/ota/Desktop/osim/model/rra_actuators.xml",
    "results_directory": "/home/ota/Desktop/osim/s31_p1_1_2/RRA_Results/",
    "initial_time": 1.05,
    "final_time": 2.385,
    "external_loads_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_grf.xml",
    "desired_kinematics_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_V35_T2_ik.mot",
    "task_set_file": "/home/ota/Desktop/osim/model/rra_tasks_custom.xml",
    "lowpass_cutoff_frequency": 6.0,
    "adjust_com_to_reduce_residuals": True,
    "adjusted_com_body": "torso",
    "output_model_file": "/home/ota/Desktop/osim/s31_p1_1_2/s31_1_rra_adjusted.osim"
}

# 関数呼び出し
# update_rra(input_xml_rra, output_xml_rra, my_new_rra_values)
"""