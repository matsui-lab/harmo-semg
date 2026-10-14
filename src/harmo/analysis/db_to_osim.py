from __future__ import annotations

import pandas as pd
import numpy as np
import scipy.signal as sp
import sys
import os
import re
from typing import Iterable, Optional
from scipy.signal import butter, filtfilt
from scipy.optimize import linear_sum_assignment

def lowpass_filter(data, cutoff, fs, order=4):
    """
    データにゼロ位相ローパスフィルタを適用します。
    """
    nyq = 0.5 * fs
    normal_cutoff = cutoff / nyq
    if normal_cutoff >= 1.0:
        print(f"情報: カットオフ周波数({cutoff}Hz)がナイキスト周波数({nyq}Hz)以上のため、フィルタをスキップします。")
        return pd.Series(data).fillna(0)

    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    series_data = pd.Series(data)
    data_len = len(series_data)
    ideal_padlen = int((4 / cutoff) * fs)
    padlen = max(15, min(ideal_padlen, data_len - 1))
    data_interp = series_data.interpolate(method='linear', limit=5, limit_direction='both')
    data_filled = data_interp.fillna(0)
    y = filtfilt(b, a, data_filled, padlen=padlen)
    return pd.Series(y, index=series_data.index)


def bandpass_filter(data, lowcut, highcut, fs, order=4):
    """
    データにゼロ位相バンドパスフィルタを適用します。
    """
    nyq = 0.5 * fs
    low = max(lowcut / nyq, 1e-9)
    high = min(highcut / nyq, 1.0 - 1e-9)

    if low >= high:
        print(f"警告: バンドパスフィルタの周波数設定が不正です (low: {lowcut}, high: {highcut})。フィルタをスキップします。")
        return pd.Series(data).fillna(0)

    b, a = butter(order, [low, high], btype='band')
    series_data = pd.Series(data)
    data_interp = series_data.interpolate(method='linear', limit=5, limit_direction='both')
    data_filled = data_interp.fillna(0)
    y = filtfilt(b, a, data_filled, axis=0)
    return pd.Series(y, index=series_data.index)


def scale_series(series: pd.Series, unit: str) -> pd.Series:
    """
    単位文字列に基づき、時系列データをOpenSim標準単位 (N, Nm, m) に変換します。
    """
    u = str(unit).lower().strip().replace("*", "")
    scale_factors = {
        "knm": (1000, "Moment"), "nmm": (0.001, "Moment"), "ncm": (0.01, "Moment"), "nm": (1.0, "Moment"),
        "mm": (0.001, "Length"), "cm": (0.01, "Length"), "m": (1.0, "Length"),
        "kn": (1000, "Force"), "n": (1.0, "Force")
    }
    if u in scale_factors:
        factor, _ = scale_factors[u]
        if factor != 1.0:
            return series * factor
    return series

def unify_coordinate_system(data, coord_sys_definition):
    """
    指定された定義に基づき、データフレームの座標系を統一します。
    目標座標系: X=前方(+), Y=上方(+), Z=右方(+)

    Args:
        data (pd.DataFrame): 処理対象のデータ。列名に_X, _Y, _Zを含む。
        coord_sys_definition (dict, pd.Series, pd.DataFrame): 座標系の定義。
    """
    
    # 座標系定義を辞書形式に正規化
    definition_dict = {}
    if isinstance(coord_sys_definition, pd.DataFrame):
        if coord_sys_definition.empty:
            print("警告: 座標系定義のDataFrameが空です。処理を中断します。")
            return data
        definition_dict = coord_sys_definition.iloc[0].to_dict()
    elif isinstance(coord_sys_definition, pd.Series):
        definition_dict = coord_sys_definition.to_dict()
    elif isinstance(coord_sys_definition, dict):
        definition_dict = coord_sys_definition
    else:
        print(f"警告: 座標系定義の型が不正です ({type(coord_sys_definition)})。処理を中断します。")
        return data

    # 目標座標系へのマッピング
    target_direction_map = {
        "Forward(+)": ("X", 1),
        "Anterior(+)": ("X", 1),
        "Backward(+)": ("X", -1),
        "Posterior(+)": ("X", -1),
        "Upward(+)": ("Y", 1),
        "Upright(+)": ("Y", 1),
        "Downward(+)": ("Y", -1),
        "Inferior(+)": ("Y", -1),
        "Right(+)": ("Z", 1),
        "Left(+)": ("Z", -1),
    }

    source_axis_map = {
        'X': definition_dict.get("x_positive"),
        'Y': definition_dict.get("y_positive"),
        'Z': definition_dict.get("z_positive")
    }

    output_data = {}
    processed_cols, unprocessed_cols = [], []
    axis_pattern = re.compile(r"(.+?)(_?)([XYZ])$", re.IGNORECASE)

    for col_name in data.columns:
        match = axis_pattern.match(col_name)
        if match:
            base_name, underscore, original_axis_char = match.groups()
            original_axis = original_axis_char.upper()
            direction = source_axis_map.get(original_axis)
            
            if direction and direction in target_direction_map:
                target_axis, sign = target_direction_map[direction]
                new_axis_char = target_axis if original_axis_char.isupper() else target_axis.lower()
                new_col_name = f"{base_name}{underscore}{new_axis_char}"
                
                # 辞書に格納
                output_data[new_col_name] = data[col_name] * sign
                processed_cols.append(new_col_name)
            else:
                output_data[col_name] = data[col_name]
                processed_cols.append(col_name)
        else:
            output_data[col_name] = data[col_name]
            unprocessed_cols.append(col_name)

    # 最後に一括でDataFrameを作成し、インデックスを引き継ぐ
    result_df = pd.DataFrame(output_data, index=data.index)
    # ------------------------------------------

    final_column_order = unprocessed_cols + sorted(list(set(processed_cols)))
    return result_df[final_column_order]


def _normalize_marker_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    データフレームの列名をすべて「マーカー名_軸」の統一規格に強制変換する前処理関数。
    
    変換例:
      - 先頭軸: 'X_RASIS' -> 'RASIS_X',  'x_psis' -> 'psis_X',  'XRASIS' -> 'RASIS_X'
      - 末尾軸: 'RPSIS_X' -> 'RPSIS_X',  'RPSISX'  -> 'RPSIS_X',  'LTH1X'   -> 'LTH1_X'
      - 除外:   'Frame#', 'Time', 'time' などはそのまま維持
    """
    new_cols = {}
    
    # 1. 先頭パターンのチェック用 (例: X_RASIS, x_psis, XRASIS)
    # ^([XYZ])     -> 先頭の軸名（グループ1）
    # _?           -> アンダースコア（あってもなくても良い）
    # (.+)$        -> 残りの文字列すべて（グループ2：ベース名）
    prefix_re = re.compile(r"^([XYZ])_?(.+)$", re.IGNORECASE)
    
    # 2. 末尾パターンのチェック用 (例: RPSIS_X, RPSISX, LTH1X)
    # ^(.+?)       -> 任意の文字列（最小マッチ）
    # (\d*)        -> マーカー名の末尾にあるかもしれない数字（例: LTH1 の '1'）を確実に回収
    # _?           -> アンダースコア（あってもなくても良い）
    # ([XYZ])$     -> 末尾の軸名（大文字小文字問わず）
    suffix_re = re.compile(r"^(.+?\d*)_?([XYZ])$", re.IGNORECASE)

    for col in df.columns:
        col_str = str(col)
        
        # 時間やフレームに関する列は、既存のロジックが依存しているためスキップ
        if col_str.lower() in ('frame#', 'time', 'timestamp'):
            new_cols[col] = col
            continue

        # 先頭軸パターンへのマッチング試行
        match_pre = prefix_re.match(col_str)
        if match_pre:
            axis, base = match_pre.groups()
            new_cols[col] = f"{base}_{axis.upper()}"
            continue
            
        # 末尾軸パターンへのマッチング試行
        match_suf = suffix_re.match(col_str)
        if match_suf:
            base, axis = match_suf.groups()
            new_cols[col] = f"{base}_{axis.upper()}"
            continue

        # どちらのパターンにも該当しない列は、そのまま維持
        new_cols[col] = col

    return df.rename(columns=new_cols)

# --- TRCファイル生成 ---

def _prepare_trc_data(data: pd.DataFrame, unit: str, allowed_markers: Optional[Iterable[str]] = None) -> tuple:
    """
    Returns:
        tuple: (data_df, excluded_allnan_markers)
            data_df: 前処理済みDataFrame (失敗時はNone)
            excluded_allnan_markers: 全行NaNのため除外されたマーカー名のリスト
    """
    df = data.copy()
    excluded_allnan_markers = []
    
    # もし列名に 'time' がなく、かつインデックスに名前がない（またはNone）場合
    time_exists_in_cols = any(str(col).lower() == 'time' for col in df.columns)
    
    if not time_exists_in_cols:
        # インデックスを列に戻す。
        df = df.reset_index()
        # 0番目の列（元インデックス）の名前を強制的に 'Time' に書き換える
        df = df.rename(columns={df.columns[0]: 'Time'})
    
    # 再度、列名から 'Time' (リネーム後) を探す
    time_cols = [col for col in df.columns if str(col).lower() == 'time']
    
    if not time_cols:
        print(f"警告: time列が見つかりません。現在の列名: {df.columns.tolist()[:5]}")
        return None, []
    
    official_time_col = time_cols[0]
    other_cols = [col for col in df.columns if col != official_time_col]
    
    # データを整理（Timeを先頭に）
    df = df[[official_time_col] + other_cols].rename(columns={official_time_col: 'Time'})
    df = _normalize_marker_column_names(df)
    # TRCとして有効なマーカー座標列(X/Y/Z)のみを抽出。
    # allowed_markers が指定された場合はそのマーカー集合に限定する。
    axis_pattern = re.compile(r"(.+?)(_?)([XYZ])$", re.IGNORECASE)
    allowed_set = None
    if allowed_markers is not None:
        allowed_set = {str(m).strip().upper() for m in allowed_markers if str(m).strip()}

    marker_axis_map = {}
    marker_order = []
    dropped_non_marker_cols = []
    dropped_outside_allowed_cols = []

    for col in [c for c in df.columns if c != 'Time']:
        match = axis_pattern.match(str(col))
        if not match:
            dropped_non_marker_cols.append(col)
            continue

        base_name, _, axis_char = match.groups()
        base_key = str(base_name).strip().upper()
        if allowed_set is not None and base_key not in allowed_set:
            dropped_outside_allowed_cols.append(col)
            continue

        axis = axis_char.upper()
        if base_key not in marker_axis_map:
            marker_axis_map[base_key] = {}
            marker_order.append(base_key)
        marker_axis_map[base_key][axis] = col

    marker_cols = []
    dropped_incomplete_markers = []
    for marker in marker_order:
        axis_cols = marker_axis_map.get(marker, {})
        if {'X', 'Y', 'Z'}.issubset(axis_cols.keys()):
            # 全行NaN チェック: X/Y/Z すべてが全行 NaN なら除外
            cols_xyz = [axis_cols['X'], axis_cols['Y'], axis_cols['Z']]
            if df[cols_xyz].isna().all().all():
                excluded_allnan_markers.append(marker)
                continue
            marker_cols.extend(cols_xyz)
        else:
            dropped_incomplete_markers.append(marker)

    if dropped_non_marker_cols:
        print(f"情報: TRC作成時に非マーカー列を除外しました: {dropped_non_marker_cols}")
    if dropped_outside_allowed_cols:
        print(f"情報: TRC作成時にallowed_markers外の列を除外しました: {dropped_outside_allowed_cols[:6]}{'...' if len(dropped_outside_allowed_cols) > 6 else ''}")
    if dropped_incomplete_markers:
        print(f"警告: X/Y/Z が揃わないマーカーを除外しました: {dropped_incomplete_markers}")
    if excluded_allnan_markers:
        print(f"警告: 全行NaNのマーカーをTRCから除外しました: {excluded_allnan_markers}")

    df = df[['Time'] + marker_cols]

    if len(marker_cols) == 0:
        print("警告: TRCに出力可能なマーカー座標列が見つかりませんでした。")
        return None, excluded_allnan_markers

    # 単位変換
    non_time_cols = df.columns.drop('Time')    
    if unit == "mm":
        df[non_time_cols] /= 1000
    elif unit == "cm":
        df[non_time_cols] /= 100
    elif unit == "km":
        df[non_time_cols] *= 1000
    elif unit != "m":
        print(f"警告: 未知の単位 '{unit}' です。")
        return None, excluded_allnan_markers
    
    df = df.copy()
    
    if 'Frame#' not in df.columns:
        df.insert(0, 'Frame#', np.arange(1, len(df) + 1))
        
    return df, excluded_allnan_markers

def _write_trc_file(data_df: pd.DataFrame, fs: float, output_path: str):
    """
    前処理済みデータフレームから.trcファイルを書き出します。
    """
    data_df = _normalize_marker_column_names(data_df)
    columns = data_df.columns.tolist()
    num_frames = len(data_df)
    num_markers = (len(columns) - 2) // 3
    
    header = [
        f"PathFileType\t4\t(X/Y/Z)\t{os.path.basename(output_path)}",
        "DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames",
        f"{fs}\t{fs}\t{num_frames}\t{num_markers}\tm\t{fs}\t1\t{num_frames}"
    ]
    
    marker_names = ["Frame#", "Time"]
    coord_labels = ["", ""]
    for i in range(2, len(columns), 3):
        # 末尾の X, Y, Z (大文字小文字問わず、アンダーバーがあってもなくても対応) を除去
        # 例: "LASISX" -> "LASIS", "R_ANKLE_X" -> "R_ANKLE"
        marker_name = re.sub(r'[_]?[XYZ]$', '', columns[i], flags=re.IGNORECASE)
        marker_names.extend([marker_name, "", ""])
        idx = (len(marker_names) - 3) // 3 + 1
        coord_labels.extend([f"X{idx}", f"Y{idx}", f"Z{idx}"])

    header.extend(["\t".join(marker_names), "\t".join(coord_labels), ""])

    with open(output_path, "w") as f:
        f.write("\n".join(header))
        np.savetxt(f, data_df.to_numpy(), fmt=['%d', '%.6f'] + ['%.6f'] * (data_df.shape[1] - 2), delimiter='\t')
    
    print(f"'{output_path}'が正常に作成されました。")


def _build_static_median_trc_frames(data_df: pd.DataFrame) -> pd.DataFrame:
    """
    静的トライアル用に、安定区間の各座標列の中央値を代表値として
    2フレーム(0.000s, 0.010s)のTRCデータを構築する。
    """
    coord_cols = [c for c in data_df.columns if c not in ("Frame#", "Time")]
    median_values = data_df[coord_cols].median(axis=0, skipna=True)

    static_data = {"Frame#": [1, 2], "Time": [0.000, 0.010]}
    for col in coord_cols:
        static_data[col] = [median_values[col], median_values[col]]

    return pd.DataFrame(static_data)

def create_trc(
    data: pd.DataFrame,
    unit: str,
    fs: float,
    dir: str = "temp_dir",
    output_file_name: str = "data.trc",
    use_static_frames: bool = False,
    use_static_median: bool = False,
    allowed_markers: Optional[Iterable[str]] = None,
    marker_cutoff: Optional[float] = None,
    ref_trc_path: Optional[str] = None,
) -> list:
    """
    マーカーデータからOpenSim用の.trcファイルを作成します。

    Args:
        data (pd.DataFrame): マーカーデータ。'Time'列を含むこと。
        unit (str): マーカーデータの長さの単位 ('m', 'cm', 'mm', 'km')。
        fs (float): サンプリング周波数 (Hz)。
        dir (str): 出力ディレクトリ。
        output_file_name (str): 出力ファイル名。
        use_static_frames (bool): Trueの場合、データは最初の2フレームに短縮されます。
        use_static_median (bool): Trueの場合、全フレーム中央値から
            2フレーム(0.000s, 0.010s)の静的TRCを生成します。
        marker_cutoff (float, optional): マーカー座標に適用するローパスフィルタの
            カットオフ周波数 (Hz)。Noneの場合フィルタなし。
        ref_trc_path (str, optional): マーカースワップ検出の参照TRCファイルパス。
            Noneの場合スワップ検出をスキップ。
    
    Returns:
        list: 全行NaNのため除外されたマーカー名のリスト。
    """
    if not output_file_name.endswith(".trc"):
        print("エラー: 出力ファイル名は.trcで終わる必要があります。")
        return []
        
    os.makedirs(dir, exist_ok=True)
    data_df, excluded_markers = _prepare_trc_data(data, unit, allowed_markers=allowed_markers)
    if data_df is None: return excluded_markers

    # マーカー座標にローパスフィルタを適用（静的TRCには不要）
    if marker_cutoff is not None and not use_static_frames and not use_static_median:
        coord_cols = [c for c in data_df.columns if c not in ('Frame#', 'Time')]
        for col in coord_cols:
            data_df[col] = lowpass_filter(data_df[col], marker_cutoff, fs)
        print(f"情報: マーカー座標にローパスフィルタを適用しました (cutoff={marker_cutoff}Hz, fs={fs}Hz)")

    if use_static_median:
        data_df = _build_static_median_trc_frames(data_df)
    elif use_static_frames:
        if len(data_df) >= 12:
            data_df = data_df.iloc[10:12, :].copy()
        else:
            print(f"警告: データが{len(data_df)}行しかないため、最初の2行を使用します。")
            data_df = data_df.iloc[:2, :].copy()

    _write_trc_file(data_df, fs, os.path.join(dir, output_file_name))
    return excluded_markers

# --- 後方互換性のための旧TRC関数 ---

def scale_trc_static(data, unit, fs, dir="temp_dir", output_file_name="scale.trc", allowed_markers: Optional[Iterable[str]] = None, marker_cutoff: Optional[float] = None):
    create_trc(data, unit, fs, dir, output_file_name, use_static_frames=False, use_static_median=True, allowed_markers=allowed_markers, marker_cutoff=marker_cutoff)

def scale_trc_kinetic(data, unit, fs, dir="temp_dir", output_file_name="scale.trc", allowed_markers: Optional[Iterable[str]] = None, marker_cutoff: Optional[float] = None):
    create_trc(data, unit, fs, dir, output_file_name, use_static_frames=True, allowed_markers=allowed_markers, marker_cutoff=marker_cutoff)

def ik_trc(data, unit, fs, dir="temp_dir", output_file_name="ik.trc", allowed_markers: Optional[Iterable[str]] = None, marker_cutoff: Optional[float] = None, ref_trc_path: Optional[str] = None) -> list:
    """
    Returns:
        list: 全行NaNのため除外されたマーカー名のリスト。
    """
    return create_trc(data, unit, fs, dir, output_file_name, use_static_frames=False, allowed_markers=allowed_markers, marker_cutoff=marker_cutoff, ref_trc_path=ref_trc_path)


# --- MOTファイル生成 ---

def _load_coord_def_for_mot(coord_sys_definition):
    """
    .mot生成用に、物理量ごとの座標系定義を読み込み、正規化された辞書を返します。
    """
    PHYSICAL_QUANTITY_ALIAS_MAP = {
        "grf": "grf", "ground reaction force": "grf", "force": "grf", "forces": "grf",
        "cop": "cop", "center of pressure": "cop", "centre of pressure": "cop",
        "moment": "moment", "moments": "moment", "torque": "moment", "torques": "moment", "force platform moments": "moment",
        "default": "default"
    }
    coord_dict_map = {}
    
    if isinstance(coord_sys_definition, pd.Series):
        coord_dict_map["default"] = coord_sys_definition.to_dict()
    elif isinstance(coord_sys_definition, pd.DataFrame):
        if coord_sys_definition.empty: return None
        temp_df = coord_sys_definition.copy()
        pq_col = None
        if 'physical_quantity' in temp_df.columns: pq_col = 'physical_quantity'
        elif temp_df.index.name == 'physical_quantity':
            temp_df = temp_df.reset_index()
            pq_col = 'physical_quantity'

        if pq_col:
            temp_df[pq_col] = temp_df[pq_col].astype(str).str.lower().str.strip().map(PHYSICAL_QUANTITY_ALIAS_MAP)
            temp_df = temp_df.dropna(subset=[pq_col])
            for _, row in temp_df.iterrows():
                coord_dict_map[row[pq_col]] = row.to_dict()
        else:
            coord_dict_map["default"] = temp_df.iloc[0].to_dict()
    else:
        return None
        
    return coord_dict_map

def _process_mot_data_loop(df, meta_df, coord_dict_map, col_axis):
    """
    メタデータと座標定義に基づき、
    1) プレート単位で軸・単位を OpenSim 仕様へ統一し、
    2) プレート単位で free torque を計算し、
    3) 左右に加算して .mot 用のデータ辞書を作成します。
    col_axis は column_name 列と axis 列を持つ df。空でもよい。
    """
    # Accept both suffix styles: CoPX / Moment_MZ and Fx1 / Mz3.
    axis_pattern = re.compile(r"([XYZ])(?:\d+)?$", re.IGNORECASE)
    plate_digit_re = re.compile(r"(\d+)")
    
    target_direction_map = {
        "Forward(+)": ("X", 1),
        "Anterior(+)": ("X", 1),
        "Backward(+)": ("X", -1),
        "Posterior(+)": ("X", -1),
        "Upward(+)": ("Y", 1),
        "Upright(+)": ("Y", 1),
        "Downward(+)": ("Y", -1),
        "Inferior(+)": ("Y", -1),
        "Right(+)": ("Z", 1),
        "Left(+)": ("Z", -1),
    }
    
    grf_names = {
        ("left", "X"): "ground_force_l_vx", ("left", "Y"): "ground_force_l_vy", ("left", "Z"): "ground_force_l_vz",
        ("right", "X"): "ground_force_r_vx", ("right", "Y"): "ground_force_r_vy", ("right", "Z"): "ground_force_r_vz",
    }
    moment_names = {
        ("left", "X"): "ground_torque_l_x", ("left", "Y"): "ground_torque_l_y", ("left", "Z"): "ground_torque_l_z",
        ("right", "X"): "ground_torque_r_x", ("right", "Y"): "ground_torque_r_y", ("right", "Z"): "ground_torque_r_z",
    }
    cop_names = {
        ("left", "X"): "ground_force_l_px", ("left", "Y"): "ground_force_l_py", ("left", "Z"): "ground_force_l_pz",
        ("right", "X"): "ground_force_r_px", ("right", "Y"): "ground_force_r_py", ("right", "Z"): "ground_force_r_pz",
    }

    def _side_key(raw_side, channel_name_meta=None):
        s = str(raw_side).lower().strip()
        if s.startswith("l"):
            return "left"
        if s.startswith("r"):
            return "right"
        name = str(channel_name_meta or "").lower()
        if re.search(r"(^|[^a-z])l(?:eft)?([^a-z]|$)", name):
            return "left"
        if re.search(r"(^|[^a-z])r(?:ight)?([^a-z]|$)", name):
            return "right"
        return None

    def _get_original_axis(channel_name_meta):
        if col_axis is not None and not col_axis.empty:
            matched_rows = col_axis[col_axis["column_name"] == channel_name_meta]
            if not matched_rows.empty:
                raw_axis = matched_rows.iloc[0].get("axis")
                if pd.notna(raw_axis):
                    tmp_axis = str(raw_axis).upper().strip()
                    if tmp_axis in ["X", "Y", "Z"]:
                        return tmp_axis

        m_axis = axis_pattern.search(str(channel_name_meta))
        if m_axis:
            return m_axis.group(1).upper()
        return None

    def _plate_id_from_name(col_name):
        digits = plate_digit_re.findall(str(col_name))
        return digits[-1] if digits else "0"

    def _sum_series_list(series_list):
        if not series_list:
            return None
        result = series_list[0].copy()
        for series in series_list[1:]:
            result = result.add(series, fill_value=0)
        return result

    plate_store = {}

    for _, row in meta_df.iterrows():
        channel_name_meta = row.get("column_name")
        raw_pq = str(row.get("physical_quantity", "")).lower().strip()
        pq_alias = {
            "grf": "grf", "force": "grf", "forces": "grf", "ground reaction force": "grf",
            "cop": "cop", "center of pressure": "cop", "centre of pressure": "cop",
            "moment": "moment", "moments": "moment", "torque": "moment"
        }
        physical_quantity = pq_alias.get(raw_pq, raw_pq)
    
        if pd.isna(channel_name_meta) or physical_quantity not in {"grf", "cop", "moment"}:
            continue
        
        side_key = _side_key(row.get("side", ""), channel_name_meta)
        if side_key is None:
            continue

        original_axis = _get_original_axis(channel_name_meta)
        if not original_axis:
            print(f"警告: 列名 '{channel_name_meta}' から軸情報を取得できません。スキップします。")
            continue

        if isinstance(df.columns, pd.MultiIndex):
            matching_cols = [c for c in df.columns if str(c[0]).lower().strip() == channel_name_meta.lower().strip()]
        else:
            matching_cols = [c for c in df.columns if str(c).lower().strip() == channel_name_meta.lower().strip()]
        if not matching_cols:
            continue

        plate_key = (side_key, _plate_id_from_name(channel_name_meta))
        plate_bucket = plate_store.setdefault(plate_key, {"grf": {}, "cop": {}, "moment": {}})

        coord_dict = coord_dict_map.get(physical_quantity, coord_dict_map.get("default"))
        if not coord_dict:
            continue

        source_axis_map = {
            "X": coord_dict.get("x_positive"),
            "Y": coord_dict.get("y_positive"),
            "Z": coord_dict.get("z_positive"),
        }
        pos_dir = source_axis_map.get(original_axis)
        if not pos_dir or pos_dir not in target_direction_map:
            print(f"警告: 列 '{channel_name_meta}' の方向定義が不明です。スキップします。")
            continue

        axis, sign = target_direction_map[pos_dir]

        for col_key in matching_cols:
            if isinstance(df.columns, pd.MultiIndex):
                raw_unit = col_key[1]
                meta_unit = row.get("unit")
                unit = meta_unit if pd.notna(meta_unit) and str(meta_unit).strip() not in ("", "nan", "None") else raw_unit
            else:
                unit = row.get("unit")

            series_raw = pd.to_numeric(df[col_key], errors='coerce').astype(float).reset_index(drop=True)
            series = series_raw if physical_quantity == "cop" else series_raw.fillna(0.0)
            series = scale_series(series * sign, unit)

            current = plate_bucket[physical_quantity].get(axis)
            plate_bucket[physical_quantity][axis] = series if current is None else current.add(series, fill_value=0)

    if not plate_store:
        return {}

    plate_store = _convert_plate_moments_to_free_torque(plate_store)

    side_groups = {
        "left": {"grf": {}, "moment": {}, "cop_num": {}, "cop_den": {}},
        "right": {"grf": {}, "moment": {}, "cop_num": {}, "cop_den": {}},
    }

    for (side_key, _plate_id), plate_bucket in plate_store.items():
        if side_key not in side_groups:
            continue

        for quantity in ("grf", "moment"):
            for axis, series in plate_bucket.get(quantity, {}).items():
                side_groups[side_key][quantity].setdefault(axis, []).append(series)

        y_force = plate_bucket.get("grf", {}).get("Y")
        if y_force is None:
            continue

        valid_weight = pd.to_numeric(y_force, errors='coerce').fillna(0.0).clip(lower=0.0)
        for axis in ("X", "Y", "Z"):
            cop_series = plate_bucket.get("cop", {}).get(axis)
            if cop_series is None:
                continue
            cop_numeric = pd.to_numeric(cop_series, errors='coerce')
            valid_mask = cop_numeric.notna()
            side_groups[side_key]["cop_num"].setdefault(axis, []).append(cop_numeric.fillna(0.0) * valid_weight.where(valid_mask, 0.0))
            side_groups[side_key]["cop_den"].setdefault(axis, []).append(valid_weight.where(valid_mask, 0.0))

    targets = {}
    for side_key in ("right", "left"):
        for axis in ("X", "Y", "Z"):
            grf_series = _sum_series_list(side_groups[side_key]["grf"].get(axis, []))
            if grf_series is not None:
                targets[grf_names[(side_key, axis)]] = grf_series

            cop_num = _sum_series_list(side_groups[side_key]["cop_num"].get(axis, []))
            cop_den = _sum_series_list(side_groups[side_key]["cop_den"].get(axis, []))
            if cop_num is not None and cop_den is not None:
                with np.errstate(divide='ignore', invalid='ignore'):
                    cop_series = cop_num.divide(cop_den.replace(0, np.nan))
                targets[cop_names[(side_key, axis)]] = cop_series.fillna(0.0)

            moment_series = _sum_series_list(side_groups[side_key]["moment"].get(axis, []))
            if moment_series is not None:
                targets[moment_names[(side_key, axis)]] = moment_series

    return targets

def _filter_and_build_mot_df(targets, time_series, cutoff_freq, sampling_rate):
    """
    処理済みデータ辞書にフィルタを適用し、最終的なDataFrameを構築します。
    """
    result_df = pd.DataFrame({'time': time_series}) 
    ordered = [
        "ground_force_r_vx","ground_force_r_vy","ground_force_r_vz", 
        "ground_force_r_px","ground_force_r_py","ground_force_r_pz",
        "ground_torque_r_x","ground_torque_r_y","ground_torque_r_z", 
        "ground_force_l_vx","ground_force_l_vy","ground_force_l_vz",
        "ground_force_l_px","ground_force_l_py","ground_force_l_pz", 
        "ground_torque_l_x","ground_torque_l_y","ground_torque_l_z",
    ]
    
    # Preserve original CoP valid frames (derived from c3d NaN pattern)
    # before interpolation/filtering.
    cop_ground_masks = {k: v.notna().reset_index(drop=True) for k, v in targets.items() if "_p" in k}
    should_filter = (cutoff_freq > 0 and sampling_rate > 0 and cutoff_freq < sampling_rate / 2)

    processed_targets = {}

    # 1) Non-CoP channels: existing filter path
    for k, v in targets.items():
        is_cop_column = "_p" in k
        series = v.reset_index(drop=True)
        if k in ordered and not is_cop_column and should_filter:
            processed_targets[k] = lowpass_filter(series, cutoff_freq, sampling_rate).reset_index(drop=True)
        else:
            processed_targets[k] = series

    # 2) CoP channels: interpolate -> filter -> restore original contact mask
    for k, v in targets.items():
        if "_p" not in k:
            continue

        series = v.reset_index(drop=True)
        cop_pre = series.interpolate(method='linear').bfill().ffill()

        if k in ordered and should_filter:
            cop_post = lowpass_filter(cop_pre, cutoff_freq, sampling_rate).reset_index(drop=True)
        else:
            cop_post = cop_pre.reset_index(drop=True)

        mask = cop_ground_masks.get(k)
        if mask is not None:
            cop_post = cop_post.where(mask, 0.0)

        processed_targets[k] = cop_post

    for name in ordered:
        if name in processed_targets:
            result_df[name] = processed_targets[name]
            
    return result_df.fillna(0)

def _write_mot_file(result_df, out_path, output_file_name):
    """
    最終的なDataFrameを.motファイル形式で書き出します。
    """
    header = [
        f'{output_file_name}', 'version=1', f'nRows={len(result_df)}',
        f'nColumns={len(result_df.columns)}', 'inDegrees=no', 'endheader'
    ]
    with open(out_path, "w") as f:
        f.write("\n".join(header) + "\n")
        f.write("\t".join(result_df.columns) + "\n")
        np.savetxt(f, result_df.to_numpy(), fmt='%.6f', delimiter='\t')
    print(f"'{out_path}'が正常に作成されました。")


def _convert_plate_moments_to_free_torque(
    plate_store,
    threshold: float = 20.0,
) -> pd.DataFrame:
    """正規化済みのプレート単位データに対して free torque を計算する。"""
    for plate_data in plate_store.values():
        grf = plate_data.get("grf", {})
        moment = plate_data.get("moment", {})
        cop = plate_data.get("cop", {})

        if not {"X", "Y", "Z"}.issubset(grf) or not {"X", "Y", "Z"}.issubset(moment):
            continue

        fx = pd.to_numeric(grf["X"], errors="coerce").fillna(0.0)
        fy = pd.to_numeric(grf["Y"], errors="coerce").fillna(0.0)
        fz = pd.to_numeric(grf["Z"], errors="coerce").fillna(0.0)
        mx = pd.to_numeric(moment["X"], errors="coerce").fillna(0.0)
        my = pd.to_numeric(moment["Y"], errors="coerce").fillna(0.0)
        mz = pd.to_numeric(moment["Z"], errors="coerce").fillna(0.0)

        cop_x = pd.to_numeric(cop.get("X", pd.Series(0.0, index=fx.index)), errors="coerce").fillna(0.0)
        cop_z = pd.to_numeric(cop.get("Z", pd.Series(0.0, index=fx.index)), errors="coerce").fillna(0.0)

        contact = fy.abs() > threshold
        tau_free_y = pd.Series(0.0, index=fx.index)
        tau_free_y[contact] = my[contact] - (fz[contact] * cop_x[contact] - fx[contact] * cop_z[contact])

        moment["X"] = pd.Series(0.0, index=fx.index)
        moment["Y"] = tau_free_y
        moment["Z"] = pd.Series(0.0, index=fx.index)

    return plate_store


def ex_mot(data: pd.DataFrame, metadata: pd.DataFrame, sampling_rate: float, coord_sys_definition, dir="temp_dir", output_file_name="force.mot", cutoff_freq=6, col_axis = None):
    """
    床反力データ(data)とメタデータから、OpenSim用の.motファイルを作成します。
    物理量(GRF, CoP, Moment)ごとに異なる座標系定義に対応可能です。
    """
    if not output_file_name.endswith(".mot"): print("エラー: ファイル名は.motである必要があります。\n"); return
    os.makedirs(dir, exist_ok=True)
    if data.index.name is None or 'time' not in data.index.name.lower(): print("エラー: dataのindexは'time'である必要があります。\n"); return

    time_series = data.index.to_series().astype(float).reset_index(drop=True)
    
    coord_dict_map = _load_coord_def_for_mot(coord_sys_definition)
    if not coord_dict_map: print("エラー: 座標系定義を読み込めませんでした。\n"); return

    targets = _process_mot_data_loop(data, metadata, coord_dict_map, col_axis)
    result_df = _filter_and_build_mot_df(targets, time_series, cutoff_freq, sampling_rate)
    _write_mot_file(result_df, os.path.join(dir, output_file_name), output_file_name)
    
    return result_df.columns

# --- EMG処理 ---

def _process_emg_channel(emg_df, lowcut, highcut, envelope_cutoff, sampling_rate):
    """
    EMGデータフレームに対し、time列を除外して信号処理を適用し、正規化します。
    """
    # 1. 時間列の退避
    # 'time' または 'Time' カラムを特定して退避させる（configのmelt_options候補に準拠）
    time_col = next((c for c in emg_df.columns if c.lower() == 'time'), None)
    
    if time_col:
        time_series = emg_df[time_col]
        # 信号データのみを抽出（time列以外）
        signal_df = emg_df.drop(columns=[time_col])
    else:
        signal_df = emg_df
    # 2. 各チャンネル（列）ごとにフィルタリングを適用
    emg_filtered = signal_df.apply(lambda x: bandpass_filter(x, lowcut, highcut, sampling_rate, order=4))
    # 3. 整流 (絶対値)
    emg_rect = np.abs(emg_filtered)
    # 4. 包絡線 (ローパスフィルタ)
    emg_envelope = emg_rect.apply(lambda x: lowpass_filter(x, envelope_cutoff, sampling_rate, order=4))
    # 5. 正規化 (各チャンネルの最大値で割る)
    max_vals = emg_envelope.max(axis=0)
    max_vals[max_vals == 0] = 1.0  # ゼロ除算を回避
    emg_norm = emg_envelope / max_vals
    # 6. 時間列の再結合
    if time_col:
        return pd.concat([time_series, emg_norm], axis=1)
    
    return emg_norm


def _write_sto_file(df_out, out_path):
    """
    処理済みEMGデータフレームを.stoファイル形式で書き出します。
    """
    header = [f'nRows={len(df_out)}', f'nColumns={len(df_out.columns)}', 'endheader']
    with open(out_path, 'w') as f:
        f.write("\n".join(header) + "\n")
        df_out.to_csv(f, sep='\t', index=False, float_format='%.6f')
    print(f"'{out_path}'が正常に作成されました。")

def emg(data: pd.DataFrame, sampling_rate, dir="temp_dir", output_file_name="processed_emg.sto", lowcut=6, highcut=450, envelope_cutoff=6):
    """
    EMGデータに対し、フィルタ処理、包絡線処理、正規化を行い、.stoファイルとして保存します。
    """
    if not output_file_name.endswith(".sto"): print("エラー: ファイル名は.stoである必要があります。\n"); return
    os.makedirs(dir, exist_ok=True)

    df = data.reset_index()
    time_cols = [c for c in df.columns if c.lower() == "time"]
    if not time_cols: print("警告: time列がありません。\n"); return
    time_col = time_cols[0]
    emg_cols = [c for c in df.columns if c != time_col]
    if not emg_cols: print("警告: EMGチャンネルが見つかりません。\n"); return

    # ここでは単純なfloat変換のみ行う (補間は _process_emg_channel 内のフィルタ関数で行う)
    emg_df = df[emg_cols].apply(pd.to_numeric, errors='coerce')
    processed_emg = _process_emg_channel(emg_df, lowcut, highcut, envelope_cutoff, sampling_rate)
    df_out = pd.concat([df[[time_col]], processed_emg], axis=1)
    _write_sto_file(df_out, os.path.join(dir, output_file_name))
