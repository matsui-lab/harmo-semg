"""C3Dファイルリーダー。

``ezc3d``ライブラリを使用して.c3dモーションキャプチャファイルを読み込み、3次元マーカー座標データおよび/または
アナログチャンネルデータを :class:`pandas.DataFrame` に変換します。

設定キー ``c3d_data_source`` により抽出対象を制御します：

* ``"point"`` (デフォルト) – マーカー軌道 (POINTセクション)。
  ``marker_names`` が指定されている場合、それらのマーカーのみを保持します
  （``SubjectName:NAME`` といったプレフィックスを自動的に処理します）。
* ``"analog"`` – アナログチャンネル (ANALOGセクション)。``analog_channel_keywords`` でフィルタリングされます。
  ``cop_channels`` が指定されている場合、生荷重/モーメントデータと FORCE_PLATFORM CORNERS の情報を用いて、
  グローバル座標系での足圧中心 (CoP) を計算します。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from functools import partial
from typing import Any, Dict, List, Optional

# このファイルが外部C3Dワーカーとして実行される際、
# そのディレクトリがsys.pathの先頭に追加されます。これにより、標準ライブラリの`csv`が
# ローカルの`readers/csv.py`で上書き（シャドウイング）され、pandasのインポートが壊れる
# 可能性があるため、修正しています。
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_THIS_DIR_REAL = os.path.realpath(_THIS_DIR)
sys.path = [p for p in sys.path if os.path.realpath(p or os.getcwd()) != _THIS_DIR_REAL]

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


# ---------------------------------------------------------------------------
# 公開API
# ---------------------------------------------------------------------------


def read_c3d_file(
    path: str,
    data_source: str = "point",
    channel_keywords: Optional[List[str]] = None,
    marker_names: Optional[List[str]] = None,
    cop_channels: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """``.c3d``ファイルを読み込み、DataFrameを返します。

    Parameters
    ----------
    path : str
        ``.c3d``ファイルへのパス。
    data_source : ``"point"`` | ``"analog"``
        抽出するセクション。
    channel_keywords : list[str] or None
        アナログチャンネルのキーワードフィルタ (``"analog"`` モード用)。
    marker_names : list[str] or None
        *data_source* が ``"point"`` の場合、これらのマーカーのみを保持。
        C3D内のラベルに ``SubjectName:`` プレフィックスが含まれる場合、サフィックス部分でマッチングを行います。
    cop_channels : list[dict] or None
        CoP計算の定義。各辞書には少なくとも ``plate_index``（1始まり）と ``column_name``（出力名）を
        含める必要があります。詳細なスキーマについては :func:`_compute_cop_columns` を参照してください。
    """
    try:
        return _read_c3d_with_ezc3d(
            path,
            data_source,
            channel_keywords,
            marker_names,
            cop_channels,
        )
    except ModuleNotFoundError as exc:
        # ezc3d itself is optional.  A dependency missing *inside* ezc3d must not
        # be disguised as an absent optional reader dependency.
        if exc.name != "ezc3d":
            raise

    worker_python = os.environ.get("HARMO_C3D_PYTHON")
    if not worker_python:
        raise RuntimeError(
            "C3D読み込みにはezc3dが必要です。Python 3.10以上で "
            "'pip install harmo[c3d]'を実行するか、ezc3dとharmoをインストールした "
            "Python実行ファイルをHARMO_C3D_PYTHONで指定してください。"
        )

    return _read_c3d_with_worker(
        worker_python,
        path,
        data_source,
        channel_keywords,
        marker_names,
        cop_channels,
    )


def _read_c3d_with_worker(
    worker_python: str,
    path: str,
    data_source: str,
    channel_keywords: Optional[List[str]],
    marker_names: Optional[List[str]],
    cop_channels: Optional[List[Dict[str, Any]]],
) -> pd.DataFrame:
    """ezc3d入りの別Python環境でC3Dを読む。"""
    # DataFrameをJSONでstdoutへ渡す方式は、大規模なC3Dでメモリを二重に消費し、
    # 浮動小数点型も不安定になる。ワーカーと呼び出し元の共有一時領域をParquet IPCに使う。
    with tempfile.TemporaryDirectory(prefix="harmo-c3d-") as temp_dir:
        output_path = os.path.join(temp_dir, "recording.parquet")
        cmd = [
            worker_python,
            __file__,
            "--as-c3d-worker",
            path,
            "--data-source",
            data_source,
            "--output-parquet",
            output_path,
        ]
        if channel_keywords:
            cmd += ["--channel-keywords", json.dumps(channel_keywords)]
        if marker_names:
            cmd += ["--marker-names", json.dumps(marker_names)]
        if cop_channels:
            cmd += ["--cop-channels", json.dumps(cop_channels)]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise RuntimeError(
                f"HARMO_C3D_PYTHONの実行に失敗しました: {worker_python}"
            ) from exc

        return _parse_c3d_worker_result(result, output_path)


def _parse_c3d_worker_result(result: Any, output_path: str) -> pd.DataFrame:
    """外部ワーカーの制御JSONを検証し、Parquet結果を読み込む。"""

    # エラー処理の改善: returncodeが0以外の場合でも、stdoutにJSON形式のエラーが含まれていないか確認します
    payload = result.stdout.strip()
    stderr = result.stderr.strip()

    if payload:
        try:
            data = json.loads(payload)
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"C3Dワーカーエラー: {data['error']}")
        except json.JSONDecodeError:
            pass

    if result.returncode != 0:
        raise RuntimeError(
            "C3Dファイルの読み込み中に外部ワーカーが失敗しました。\n"
            f"  終了コード: {result.returncode}\n"
            f"  標準エラー出力: {stderr or 'なし'}\n"
            f"  標準出力: {payload or 'なし'}"
        )

    if not payload:
        raise RuntimeError("C3Dワーカーが空の出力を返しました。")

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        # Worker stdout can occasionally include non-JSON lines; try last JSON line.
        candidate = None
        for line in reversed(payload.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                candidate = line
                break
        if candidate is None:
            raise RuntimeError(
                "C3DワーカーからのJSONペイロードのパースに失敗しました。"
            )
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "C3DワーカーからのJSONペイロードのパースに失敗しました。"
            ) from exc

    if not isinstance(data, dict):
        raise RuntimeError("C3Dワーカーの出力はJSONオブジェクトではありません。")

    if "output_parquet" in data:
        reported_path = data["output_parquet"]
        if not isinstance(reported_path, str):
            raise RuntimeError("C3Dワーカーの出力Parquetパスが不正です。")
        if os.path.realpath(reported_path) != os.path.realpath(output_path):
            raise RuntimeError(
                "C3Dワーカーが要求先と異なるParquetパスを返しました。"
            )
        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("C3Dワーカーの出力Parquetが存在しないか空です。")
        try:
            frame = pd.read_parquet(output_path)
        except ImportError as exc:
            raise RuntimeError(
                "C3DワーカーのParquet出力を読むにはpyarrowが必要です。"
                "呼び出し元にも 'pip install harmo[c3d]' を実行してください。"
            ) from exc
        except Exception as exc:
            raise RuntimeError("C3DワーカーのParquet出力を読めませんでした。") from exc

        expected_rows = data.get("row_count")
        expected_columns = data.get("column_count")
        if expected_rows is not None and expected_rows != len(frame):
            raise RuntimeError("C3Dワーカーの行数メタデータがParquetと一致しません。")
        if expected_columns is not None and expected_columns != len(frame.columns):
            raise RuntimeError("C3Dワーカーの列数メタデータがParquetと一致しません。")
        return frame

    # 旧ワーカーとの後方互換性。新規実行ではParquet IPCを必須で指定する。
    if "df" not in data:
        raise RuntimeError(
            "C3Dワーカーの出力に 'output_parquet' または 'df' がありません。"
        )

    return pd.read_json(io.StringIO(data["df"]), orient="split")


# ---------------------------------------------------------------------------
# ezc3d実装（現在のPythonまたは外部ワーカー）
# ---------------------------------------------------------------------------


def _read_c3d_with_ezc3d(
    path: str,
    data_source: str = "point",
    channel_keywords: Optional[List[str]] = None,
    marker_names: Optional[List[str]] = None,
    cop_channels: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """ezc3dを遅延インポートしてC3DをDataFrameに変換する。"""
    import ezc3d  # type: ignore[import-not-found]

    c3d_data = ezc3d.c3d(path)

    if data_source == "analog-native":
        # Opt-in: retain decoder-scaled channels without force-plate calibration
        # or coordinate rotation. Existing analog behavior is unchanged.
        array = c3d_data["data"]["analogs"]
        labels = [str(v).strip() for v in c3d_data["parameters"]["ANALOG"]["LABELS"]["value"]]
        if array.shape[0] != 1 or len(labels) != array.shape[1] or len(set(labels)) != len(labels) or not all(labels) or "time" in labels:
            raise ValueError("Invalid native analog shape or labels")
        rate = float(c3d_data["parameters"]["ANALOG"]["RATE"]["value"][0])
        if not np.isfinite(rate) or rate <= 0:
            raise ValueError("Invalid native analog rate")
        frame = pd.DataFrame(array[0].T, columns=labels)
        frame["time"] = np.arange(len(frame)) / rate
        return frame
    if data_source == "analog":
        return _extract_analog(c3d_data, channel_keywords, cop_channels)
    return _extract_point(c3d_data, marker_names)


# ---------------------------------------------------------------------------
# POINT (マーカー座標) 抽出
# ---------------------------------------------------------------------------


def _extract_point(
    c3d_data: Any,
    marker_names: Optional[List[str]] = None,
) -> pd.DataFrame:
    """パース済みのC3Dオブジェクトからマーカー (POINT) 軌道を抽出します。

    *marker_names* が指定されている場合、一致するマーカーのみを返します。
    C3D内のラベルに ``SubjectName:`` プレフィックス（例：``Ref01_FB:LFHD``）が含まれている場合、
    最後のコロン **以降** の文字列でマッチングを行います（コロンがない場合はラベル全体で判定）。
    """
    point_data = c3d_data["data"]["points"]
    raw_labels = [
        label.strip() for label in c3d_data["parameters"]["POINT"]["LABELS"]["value"]
    ]
    num_frames = point_data.shape[2]
    frame_rate = c3d_data["parameters"]["POINT"]["RATE"]["value"][0]
    time_vector = np.arange(num_frames) / frame_rate

    # マッピングを作成: raw_label -> 短縮名 (':'以降のサフィックス)
    def _short(label: str) -> str:
        return label.rsplit(":", 1)[-1]

    if marker_names:
        wanted = set(marker_names)
        selected_indices: List[int] = []
        selected_short: List[str] = []
        for i, raw_label in enumerate(raw_labels):
            short = _short(raw_label)
            if short in wanted:
                selected_indices.append(i)
                selected_short.append(short)
    else:
        selected_indices = list(range(len(raw_labels)))
        selected_short = [_short(label) for label in raw_labels]

    columns = [f"{name}_{axis}" for name in selected_short for axis in ["x", "y", "z"]]
    # 選択されたマーカーのみを収集: (3, 選択数, フレーム数)
    sel_data = point_data[:3, selected_indices, :]
    reshaped = sel_data.T.reshape(num_frames, -1)

    df = pd.DataFrame(reshaped, columns=columns)
    df["time"] = time_vector
    return df


# ---------------------------------------------------------------------------
# ANALOG 抽出ロジック
# ---------------------------------------------------------------------------


def _extract_analog(
    c3d_data: Any,
    channel_keywords: Optional[List[str]] = None,
    cop_channels: Optional[List[Dict[str, Any]]] = None,
) -> pd.DataFrame:
    """アナログチャンネルを抽出し、オプションで校正およびCoP計算を行います。"""
    analog_data = c3d_data["data"]["analogs"]  # (1, チャンネル数, フレーム数)
    analog_labels = [
        label.strip() for label in c3d_data["parameters"]["ANALOG"]["LABELS"]["value"]
    ]
    analog_rate = c3d_data["header"]["analogs"]["frame_rate"]
    n_frames = analog_data.shape[2]

    time_vector = np.arange(n_frames) / analog_rate

    # 1. プレート情報の取得
    plate_info = _extract_plate_info(c3d_data)

    # 2. 全てのラベル（インデックス付）に対してプレートチャンネルを特定
    # フィルタリング前に行うことで、名前が Channel_1 等でもインデックスベースで特定可能にする
    all_selected = list(enumerate(analog_labels))
    plate_channels = _identify_plate_channels(all_selected, plate_info)

    # 3. 校正行列の適用 (Type 4 などの電圧データを力学値に変換)
    calibrated_map = _apply_calibration_to_analog_data(
        analog_data, plate_info, plate_channels
    )

    # 4. 全てのチャンネルデータを保持する内部マップを作成（ローカル座標系）
    # 名前は元の Channel_01 等のまま保持する
    internal_data_map: Dict[str, np.ndarray] = {}
    for idx, label in all_selected:
        if idx in calibrated_map:
            internal_data_map[label] = calibrated_map[idx]
        else:
            internal_data_map[label] = analog_data[0, idx, :]

    # 5. CoP (足圧中心) の計算 (ローカル座標系のデータを使用)
    if cop_channels:
        _compute_cop_columns(
            c3d_data,
            analog_data,
            analog_labels,
            internal_data_map,
            cop_channels,
            plate_info,
            plate_channels,
        )

    # 6. プレート回転の適用 (ローカル座標からグローバル座標への変換)
    # これにより internal_data_map 内の Force/Moment データがグローバル化される
    _apply_rotation_to_data_dict(
        internal_data_map, analog_labels, plate_info, plate_channels
    )

    # 7. 出力用辞書の構築
    data_dict: Dict[str, Any] = {"time": time_vector}

    # ユーザーが求めているチャンネル（キーワードに一致するもの）を追加
    matched_any = False
    if channel_keywords:
        for label in analog_labels:
            if any(kw in label for kw in channel_keywords):
                data_dict[label] = internal_data_map[label]
                matched_any = True
        if not matched_any:
            print(
                "  [警告] analog_channel_keywords に一致するチャンネルがないため、全アナログチャンネルを出力します。",
                file=sys.stderr,
            )
            for label in analog_labels:
                data_dict[label] = internal_data_map[label]
    else:
        # キーワード指定がない場合は全チャンネルを出力
        for label in analog_labels:
            data_dict[label] = internal_data_map[label]

    # 8. 計算された CoP 列はフィルタ設定に関わらず必ず追加する
    if cop_channels:
        for spec in cop_channels:
            col_name = spec["column_name"]
            if col_name in internal_data_map:
                data_dict[col_name] = internal_data_map[col_name]

    return pd.DataFrame(data_dict)


def _extract_plate_info(c3d_data: Any) -> Dict[int, Dict[str, Any]]:
    """C3Dメタデータからプレートの幾何情報、タイプ、および校正情報を抽出します。"""
    plate_info = {}
    fp_params = c3d_data["parameters"].get("FORCE_PLATFORM", {})
    if not fp_params:
        return plate_info

    try:
        corners = fp_params["CORNERS"]["value"]
    except KeyError:
        return plate_info
    if corners is None or np.size(corners) == 0:
        return plate_info

    try:
        origins = fp_params["ORIGIN"]["value"]
    except KeyError:
        origins = np.zeros((3, corners.shape[2]))
    if origins is None or np.size(origins) == 0:
        origins = np.zeros((3, corners.shape[2]))

    # 基本情報の取得
    plate_types = fp_params.get("TYPE", {}).get("value", [])
    channel_param = fp_params.get("CHANNEL", {}).get("value")
    cal_matrix_param = fp_params.get("CAL_MATRIX", {}).get("value")

    # 単位の検出：必要に応じてmmにスケーリング
    corners_scale_to_mm = 1.0
    origins_scale_to_mm = 1.0
    try:
        corners_scale_to_mm = 1000.0 if np.nanmax(np.abs(corners)) < 10.0 else 1.0
    except Exception:
        pass
    try:
        origins_scale_to_mm = 1000.0 if np.nanmax(np.abs(origins)) < 10.0 else 1.0
    except Exception:
        pass
    corners_mm = corners * corners_scale_to_mm
    origins_mm = origins * origins_scale_to_mm

    n_plates = corners_mm.shape[2]
    for p_idx in range(n_plates):
        plate_num = p_idx + 1
        plate_corners = corners_mm[:, :, p_idx]
        center = np.mean(plate_corners, axis=1)

        # プレートの向きを構築
        vx = (plate_corners[:, 0] - plate_corners[:, 1]) + (
            plate_corners[:, 3] - plate_corners[:, 2]
        )
        vx = vx / np.linalg.norm(vx)
        vy = (plate_corners[:, 1] - plate_corners[:, 2]) + (
            plate_corners[:, 0] - plate_corners[:, 3]
        )
        vy = vy / np.linalg.norm(vy)
        vz = np.cross(vx, vy)
        vz = vz / np.linalg.norm(vz)

        info = {
            "type": int(plate_types[p_idx]) if p_idx < len(plate_types) else 2,
            "center": center,
            "rotation": np.column_stack((vx, vy, vz)),
            "dz": float(origins_mm[2, p_idx]),
        }

        # 校正行列の抽出 (6x6)
        if cal_matrix_param is not None:
            if cal_matrix_param.ndim == 3:
                info["cal_matrix"] = cal_matrix_param[:, :, p_idx]
            else:
                info["cal_matrix"] = cal_matrix_param

        # 使用アナログチャンネルインデックスの抽出 (C3Dは1-based)
        if channel_param is not None:
            if channel_param.ndim == 2:
                info["analog_channels"] = channel_param[:, p_idx]
            else:
                info["analog_channels"] = channel_param

        plate_info[plate_num] = info
    return plate_info


def _identify_plate_channels(
    selected_list: List[tuple], plate_info: Dict[int, Dict[str, Any]]
) -> Dict[int, Dict[str, int]]:
    """どのチャンネル番号がどの物理成分(fx, fy等)に対応するか特定します。"""
    plate_channels = {}

    force_re = re.compile(r"^(?:Force\.)?F([xyz])(\d+)$", re.IGNORECASE)
    moment_re = re.compile(r"^(?:Moment\.)?M([xyz])(\d+)$", re.IGNORECASE)

    for plate_idx, info in plate_info.items():
        comp_map = {}

        # 1. C3DのCHANNELパラメータを最優先 (AMTI Type 4等)
        if "analog_channels" in info:
            indices = info["analog_channels"]
            if len(indices) >= 6:
                # 0-basedに変換。不正な値(0以下)は除外
                idx_clean = [int(i) - 1 for i in indices[:6]]
                if all(i >= 0 for i in idx_clean):
                    comp_map = {
                        "fx": idx_clean[0],
                        "fy": idx_clean[1],
                        "fz": idx_clean[2],
                        "mx": idx_clean[3],
                        "my": idx_clean[4],
                        "mz": idx_clean[5],
                    }

        # 2. 名前ベースでの検索 (Type 2等)
        if not comp_map:
            for idx, label in selected_list:
                fm = force_re.match(label)
                if fm and int(fm.group(2)) == plate_idx:
                    comp_map[f"f{fm.group(1).lower()}"] = idx
                    continue
                mm = moment_re.match(label)
                if mm and int(mm.group(2)) == plate_idx:
                    comp_map[f"m{mm.group(1).lower()}"] = idx

        if comp_map:
            plate_channels[plate_idx] = comp_map

    return plate_channels


def _apply_calibration_to_analog_data(
    analog_data: np.ndarray, plate_info: dict, plate_channels: dict
) -> Dict[int, np.ndarray]:
    """電圧データを校正行列で力学値に変換した結果をインデックスをキーに返します。"""
    res = {}
    for p_idx, info in plate_info.items():
        # Type 4 (または 3) の場合にのみ校正行列を適用
        if (
            info.get("type") in [3, 4]
            and "cal_matrix" in info
            and p_idx in plate_channels
        ):
            ch = plate_channels[p_idx]
            idx_list = [ch.get(k) for k in ["fx", "fy", "fz", "mx", "my", "mz"]]
            if all(i is not None and i >= 0 for i in idx_list):
                volts = analog_data[0, idx_list, :]
                # F = M @ V
                phys = info["cal_matrix"] @ volts
                for i, idx in enumerate(idx_list):
                    res[idx] = phys[i, :]
    return res


def _apply_rotation_to_data_dict(
    data_dict: dict, analog_labels: list, plate_info: dict, plate_channels: dict
):
    """取得した各成分に回転行列を適用し、グローバル座標系に変換します。"""
    for p_idx, channels in plate_channels.items():
        p_info = plate_info.get(p_idx)
        if not p_info:
            continue

        rot = p_info["rotation"]

        # 荷重 (Force)
        if all(k in channels for k in ["fx", "fy", "fz"]):
            ix, iy, iz = channels["fx"], channels["fy"], channels["fz"]
            lx, ly, lz = analog_labels[ix], analog_labels[iy], analog_labels[iz]

            vec_local = np.vstack([data_dict[lx], data_dict[ly], data_dict[lz]])
            vec_global = rot @ vec_local

            data_dict[lx], data_dict[ly], data_dict[lz] = (
                vec_global[0, :],
                vec_global[1, :],
                vec_global[2, :],
            )

        # モーメント (Moment)
        if all(k in channels for k in ["mx", "my", "mz"]):
            ix, iy, iz = channels["mx"], channels["my"], channels["mz"]
            lx, ly, lz = analog_labels[ix], analog_labels[iy], analog_labels[iz]

            vec_local = np.vstack([data_dict[lx], data_dict[ly], data_dict[lz]])
            vec_global = rot @ vec_local

            data_dict[lx], data_dict[ly], data_dict[lz] = (
                vec_global[0, :],
                vec_global[1, :],
                vec_global[2, :],
            )


def _compute_cop_columns(
    c3d_data: Any,
    analog_data: np.ndarray,
    analog_labels: List[str],
    data_dict: Dict[str, Any],
    cop_channels: List[Dict[str, Any]],
    plate_info: Dict[int, Dict[str, Any]],
    plate_channels: Dict[int, Dict[str, int]],
) -> None:
    """足圧中心 (CoP) を計算し、data_dict に追加します。"""
    n_frames = analog_data.shape[2]

    def _get_val(p_idx, comp_name):
        if p_idx in plate_channels and comp_name in plate_channels[p_idx]:
            idx = plate_channels[p_idx][comp_name]
            label = analog_labels[idx]
            return data_dict.get(label)
        return None

    for spec in cop_channels:
        plate = spec["plate_index"]
        col_name = spec["column_name"]
        axis = spec.get("axis", "x")
        threshold = float(spec.get("fz_threshold", 20.0))

        # 必要な成分を取得
        fz = _get_val(plate, "fz")
        fx = _get_val(plate, "fx")
        fy = _get_val(plate, "fy")
        mx = _get_val(plate, "mx")
        my = _get_val(plate, "my")
        p_data = plate_info.get(plate)

        if any(v is None for v in (fz, fx, fy, mx, my)) or p_data is None:
            data_dict[col_name] = np.full(n_frames, np.nan)
            continue

        contact = np.abs(fz) > threshold
        cop_global = np.full((n_frames, 3), np.nan, dtype=float)

        # 厚み補正を含めた CoP 計算 (ローカル座標系)
        cop_local = np.zeros((3, n_frames), dtype=float)
        dz = abs(p_data["dz"])
        cop_local[0, contact] = (-my[contact] + (fx[contact] * dz)) / fz[contact]
        cop_local[1, contact] = (mx[contact] + (fy[contact] * dz)) / fz[contact]
        cop_local[2, contact] = 0.0

        # グローバル座標への変換
        cop_global[contact] = (p_data["rotation"] @ cop_local[:, contact]).T + p_data[
            "center"
        ]

        axis_idx = {"x": 0, "y": 1, "z": 2}.get(axis.lower())
        if axis_idx is not None:
            data_dict[col_name] = cop_global[:, axis_idx]


# ---------------------------------------------------------------------------
# ワーカーエントリーポイント
# ---------------------------------------------------------------------------


def _run_c3d_worker(
    path: str,
    data_source: str = "point",
    channel_keywords: Optional[List[str]] = None,
    marker_names: Optional[List[str]] = None,
    cop_channels: Optional[List[Dict[str, Any]]] = None,
    output_parquet: Optional[str] = None,
) -> int:
    """C3Dを読み、Parquetへの参照を制御JSONとしてstdoutへ書き出す。"""
    try:
        df = _read_c3d_with_ezc3d(
            path,
            data_source,
            channel_keywords,
            marker_names,
            cop_channels,
        )
        if output_parquet:
            df.to_parquet(output_parquet, index=False)
            payload = {
                "output_parquet": output_parquet,
                "row_count": len(df),
                "column_count": len(df.columns),
            }
        else:
            # 直接ワーカーを呼ぶ既存利用者のための後方互換経路。
            payload = {"df": df.to_json(orient="split")}
        sys.stdout.write(json.dumps(payload))
        return 0
    except Exception as exc:
        sys.stdout.write(json.dumps({"error": str(exc)}))
        sys.stderr.write(f"C3Dワーカー例外: {str(exc)}\n")
        return 1


# ---------------------------------------------------------------------------
# 定義ファイル解決
# ---------------------------------------------------------------------------

_DEFAULT_FZ_THRESHOLD = 20.0  # N


def _resolve_path(path: str, config_dir: Optional[str]) -> str:
    if os.path.isabs(path):
        return path
    if config_dir:
        return os.path.join(config_dir, path)
    return path


def _load_marker_names_from_definitions(yaml_path: str) -> List[str]:
    import yaml

    with open(yaml_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    marker_config = data.get("marker_config", {})
    names = []
    for _body_part, entries in marker_config.items():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and "marker" in entry:
                    names.append(entry["marker"])
    return names


def _load_cop_channels_from_definitions(yaml_path: str) -> List[Dict[str, Any]]:
    import yaml

    with open(yaml_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    channels = data.get("channels", [])
    cop_specs = []
    _cop_re = re.compile(r"FP(\d+)\.CoP([XYZ])", re.IGNORECASE)
    for ch in channels:
        if not ch.get("calc_cop"):
            continue
        col_name = ch.get("column_name", "")
        m = _cop_re.search(col_name)
        if not m:
            continue
        cop_specs.append(
            {
                "plate_index": int(m.group(1)),
                "column_name": col_name,
                "axis": m.group(2).lower(),
                "fz_threshold": _DEFAULT_FZ_THRESHOLD,
            }
        )
    return cop_specs


# ---------------------------------------------------------------------------
# Spark 統合
# ---------------------------------------------------------------------------


def load_c3d_to_spark(
    spark: Any,
    file_path_list: List[str],
    rule_config: Optional[Dict[str, Any]] = None,
) -> Any:
    from harmo.ingest.readers import _read_files_parallel, _batched_pd_to_spark

    if rule_config is None:
        rule_config = {}

    config_dir = rule_config.get("_config_dir")
    data_source = rule_config.get("c3d_data_source", "point")
    channel_keywords = rule_config.get("analog_channel_keywords")

    marker_names = rule_config.get("marker_names")
    if marker_names is None and rule_config.get("filter_markers"):
        defs_path = rule_config.get("marker_definitions_path")
        if defs_path:
            marker_names = _load_marker_names_from_definitions(
                _resolve_path(defs_path, config_dir)
            )

    cop_channels = rule_config.get("cop_channels")
    if cop_channels is None and rule_config.get("calc_cop"):
        defs_path = rule_config.get("channel_definitions_path")
        if defs_path:
            cop_channels = _load_cop_channels_from_definitions(
                _resolve_path(defs_path, config_dir)
            )

    read_fn = partial(
        read_c3d_file,
        data_source=data_source,
        channel_keywords=channel_keywords,
        marker_names=marker_names,
        cop_channels=cop_channels,
    )
    all_data_dfs = _read_files_parallel(file_path_list, read_fn, format_label=".c3d")
    if not all_data_dfs:
        return None
    return _batched_pd_to_spark(spark, all_data_dfs)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--as-c3d-worker",
        "--as-py311-worker",
        dest="as_c3d_worker",
        metavar="PATH",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--data-source", default="point", choices=["point", "analog", "analog-native"])
    parser.add_argument("--channel-keywords", default=None)
    parser.add_argument("--marker-names", default=None)
    parser.add_argument("--cop-channels", default=None)
    parser.add_argument("--output-parquet", default=None, help=argparse.SUPPRESS)
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    if args.as_c3d_worker:
        kw = json.loads(args.channel_keywords) if args.channel_keywords else None
        mn = json.loads(args.marker_names) if args.marker_names else None
        cc = json.loads(args.cop_channels) if args.cop_channels else None
        raise SystemExit(
            _run_c3d_worker(
                args.as_c3d_worker,
                args.data_source,
                kw,
                mn,
                cc,
                args.output_parquet,
            )
        )
