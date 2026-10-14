"""MAT pre-conversion helpers (Octave-based) and readability probes."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from scipy.io import loadmat

try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None


_CURRENT_DIR = Path(__file__).resolve().parent.parent / "readers"
CONVERT_SCRIPT = str(_CURRENT_DIR / "convert_mat_tables.m")
CONVERTED_DIR_NAME = "_converted"


def _fallback_converted_path(original_path: str, base_input_dir: str) -> str:
    base = Path(base_input_dir)
    orig = Path(original_path)
    try:
        rel = orig.relative_to(base)
    except ValueError:
        rel = Path(orig.name)
    safe_base = Path("/tmp") / "harmo_converted" / str(abs(hash(str(base.resolve()))))
    safe_path = safe_base / rel
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    return str(safe_path)


def is_mat_v73(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            header = f.read(512)
            return b"MATLAB 7.3" in header or b"\x89HDF" in header
    except Exception:
        return False


def _get_converted_path(original_path: str, base_input_dir: str) -> str:
    base = Path(base_input_dir)
    orig = Path(original_path)
    try:
        rel = orig.relative_to(base)
    except ValueError:
        rel = Path(orig.name)
    preferred = base / CONVERTED_DIR_NAME / rel
    try:
        parent = preferred.parent
        parent.mkdir(parents=True, exist_ok=True)
        if os.access(str(parent), os.W_OK):
            return str(preferred)
    except Exception:
        pass

    # Read-only datasets fallback.
    return _fallback_converted_path(original_path, base_input_dir)


def _needs_conversion(original_path: str, converted_path: str) -> bool:
    if not os.path.exists(converted_path):
        return True
    return os.path.getmtime(original_path) > os.path.getmtime(converted_path)


def _safe_nested_get(mat_data: dict, key_path: str) -> Any:
    def _parse_token(token: str):
        m = re.match(r"^(?P<key>[^\[\]]+)(?:\[(?P<i>\d+)(?:,(?P<j>\d+))?\])?$", token)
        if not m:
            raise ValueError("Invalid token: {}".format(token))
        key = m.group("key")
        i = m.group("i")
        j = m.group("j")
        if i is None:
            return key, None
        if j is None:
            return key, (int(i),)
        return key, (int(i), int(j))

    def _apply_index(value: Any, index_spec):
        if index_spec is None:
            return value
        if not isinstance(value, np.ndarray):
            raise TypeError("Index specified on non-ndarray")
        if len(index_spec) == 2:
            return value[index_spec[0], index_spec[1]]
        if value.ndim == 1:
            return value[index_spec[0]]
        return value[index_spec[0], 0]

    current: Any = mat_data
    for token in key_path.split("."):
        key, idx = _parse_token(token)
        if isinstance(current, dict):
            current = current[key]
        elif isinstance(current, np.ndarray) and current.dtype.names:
            if key.isdigit():
                current = current.flat[int(key)]
            else:
                current = current[0, 0][key]
        elif isinstance(current, np.void) and current.dtype.names:
            current = current[key]
        else:
            raise TypeError("Unexpected type while traversing key path")
        current = _apply_index(current, idx)

    while isinstance(current, np.ndarray) and current.dtype == object and current.size == 1:
        current = current.item()
    return current


def probe_mat_readable(path: str, rule_config: Dict[str, Any]) -> bool:
    if is_mat_v73(path):
        return h5py is not None

    try:
        mat_data = loadmat(path, squeeze_me=False)
    except Exception:
        return False

    reader_options = rule_config.get("reader_options", {})
    data_key = reader_options.get("data_key")
    template = rule_config.get("mat_data_key_template")
    if not data_key or str(data_key).lower() == "none":
        # Without explicit data_key, only treat as readable when template mode is used.
        return bool(template)

    try:
        data = _safe_nested_get(mat_data, str(data_key))
    except Exception:
        return False

    if isinstance(data, np.ndarray) and data.dtype.kind in ("f", "i", "u", "c") and data.size > 0:
        return True
    return False


def filter_files_needing_conversion(file_path_list: List[str], rule_config: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    readable: List[str] = []
    needs_conversion: List[str] = []
    for path in file_path_list:
        try:
            if probe_mat_readable(path, rule_config):
                readable.append(path)
            else:
                needs_conversion.append(path)
        except Exception:
            needs_conversion.append(path)
    print("  [結果] 直接読み込み可能: {}, Octave変換必要: {}".format(len(readable), len(needs_conversion)))
    return readable, needs_conversion


def preconvert_mat_tables(file_path_list: List[str], base_input_dir: str) -> List[str]:
    if not os.path.exists(CONVERT_SCRIPT):
        print("  [警告] 変換スクリプトが見つかりません: {}".format(CONVERT_SCRIPT))
        return file_path_list

    converted_list: List[str] = []
    n_converted = 0
    n_skipped = 0

    for orig_path in file_path_list:
        conv_path = _get_converted_path(orig_path, base_input_dir)
        if not _needs_conversion(orig_path, conv_path):
            converted_list.append(conv_path)
            n_skipped += 1
            continue

        try:
            os.makedirs(os.path.dirname(conv_path), exist_ok=True)
        except OSError:
            conv_path = _fallback_converted_path(orig_path, base_input_dir)
            os.makedirs(os.path.dirname(conv_path), exist_ok=True)
        print("  [Octave変換] {} ...".format(os.path.basename(orig_path)))

        try:
            result = subprocess.run(
                ["octave", "--no-gui", "--norc", CONVERT_SCRIPT, orig_path, conv_path],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                print("    [エラー] Octave 変換失敗: {}".format(result.stderr.strip()))
                converted_list.append(orig_path)
                continue

            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    print("    {}".format(line))

            converted_list.append(conv_path)
            n_converted += 1
        except FileNotFoundError:
            print("  [警告] Octave がインストールされていません。事前変換をスキップします。")
            return file_path_list
        except subprocess.TimeoutExpired:
            print("    [エラー] Octave 変換タイムアウト: {}".format(orig_path))
            converted_list.append(orig_path)

    print("  [Octave変換] 完了: {} 変換, {} キャッシュ利用".format(n_converted, n_skipped))
    return converted_list
