"""MAT reader for standard numeric MAT files only.

Supported:
- MAT v5/v7 legacy files readable by scipy.io.loadmat
- MAT v7.3 HDF5 files readable by h5py with reference resolution

Intentionally excluded from this module:
- MCOS table parsing (moved to explicit plugins)
- Octave pre-conversion helpers (moved to ingest.utils.mat_preconvert)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.io import loadmat

try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None


def _get_time_column_candidates(reader_options: Optional[dict]) -> List[str]:
    if not isinstance(reader_options, dict):
        return []

    candidates = reader_options.get("time_column_candidates")
    if candidates is None:
        melt_options = reader_options.get("melt_options")
        if isinstance(melt_options, dict):
            candidates = melt_options.get("time_column_candidates")

    if isinstance(candidates, str):
        candidates = [candidates]
    if not isinstance(candidates, (list, tuple)):
        return []

    out: List[str] = []
    for c in candidates:
        v = str(c).strip()
        if v:
            out.append(v)
    return out


def _should_skip_auto_time_generation(reader_options: Optional[dict]) -> bool:
    if not isinstance(reader_options, dict):
        return False
    if reader_options.get("time_key") or reader_options.get("fs_key"):
        return False
    return bool(_get_time_column_candidates(reader_options))


def _is_mat_v73(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            header = f.read(512)
            return b"MATLAB 7.3" in header or b"\x89HDF" in header
    except Exception:
        return False


def _resolve_h5_references(
    h5file: Any,
    data: Any,
    max_depth: int = 5,
    current_depth: int = 0,
    preserve_ref_arrays: bool = False,
) -> Any:
    if current_depth >= max_depth:
        return data

    if isinstance(data, h5py.Dataset):
        arr = data[:]
        if arr.dtype == object and arr.size > 0:
            if preserve_ref_arrays and arr.size > 1:
                return arr
            ref = arr.flat[0]
            if isinstance(ref, h5py.Reference):
                return _resolve_h5_references(h5file, h5file[ref], max_depth, current_depth + 1, preserve_ref_arrays)
        return arr

    if isinstance(data, np.ndarray):
        if data.dtype == object and data.size > 0:
            if preserve_ref_arrays and data.size > 1:
                return data
            ref = data.flat[0]
            if isinstance(ref, h5py.Reference):
                return _resolve_h5_references(h5file, h5file[ref], max_depth, current_depth + 1, preserve_ref_arrays)
        return data

    return data


def _parse_h5_key_path(data_key: str) -> List[Tuple[str, Optional[Tuple[int, ...]]]]:
    tokens = re.findall(r"([^\.\[\]]+)|\[([^\]]+)\]", data_key)
    parsed: List[Tuple[str, Optional[Tuple[int, ...]]]] = []
    for key, indices in tokens:
        if key:
            parsed.append((key, None))
        if indices:
            idx = tuple(int(x.strip()) for x in indices.split(","))
            parsed.append(("", idx))
    return parsed


def _read_mat_h5(path: str, reader_options: Optional[dict] = None) -> pd.DataFrame:
    if h5py is None:
        raise ImportError("h5py is required for MATLAB v7.3 files")

    if reader_options is None:
        reader_options = {}

    data_key = reader_options.get("data_key")
    if not data_key:
        raise ValueError("reader_options.data_key is required for MAT HDF5")

    with h5py.File(path, "r") as f:
        data: Any = f
        for key, idx in _parse_h5_key_path(str(data_key)):
            if key:
                if key not in data:
                    raise KeyError("missing HDF5 key '{}'".format(key))
                data = data[key]
            if idx is not None:
                if isinstance(data, h5py.Dataset):
                    data = data[()]
                if isinstance(data, np.ndarray) and data.dtype == np.object_:
                    ref = data[idx]
                    if isinstance(ref, h5py.Reference):
                        data = f[ref]
                    else:
                        data = ref
                else:
                    data = data[idx]

        expand_refs = bool(reader_options.get("expand_refs", False))
        data_array = _resolve_h5_references(f, data, preserve_ref_arrays=expand_refs)
        if isinstance(data_array, h5py.Dataset):
            data_array = data_array[:]

        if isinstance(data_array, np.ndarray) and data_array.dtype == np.object_ and expand_refs:
            channels: List[np.ndarray] = []
            for ref in data_array.flatten():
                if not isinstance(ref, h5py.Reference):
                    raise TypeError("expand_refs expects h5py.Reference array")
                ch = f[ref][:]
                if ch.ndim == 1:
                    ch = ch.reshape(-1, 1)
                channels.append(ch)
            sample_counts = [c.shape[0] for c in channels]
            if len(set(sample_counts)) != 1:
                if reader_options.get("trim_to_min", False):
                    m = min(sample_counts)
                    channels = [c[:m, :] for c in channels]
                elif reader_options.get("pad_to_max", False):
                    m = max(sample_counts)
                    padded: List[np.ndarray] = []
                    for c in channels:
                        if c.shape[0] < m:
                            pad = np.zeros((m - c.shape[0], c.shape[1]))
                            c = np.vstack([c, pad])
                        padded.append(c)
                    channels = padded
                else:
                    raise ValueError("Expanded reference channels have different sample counts")
            data_array = np.concatenate(channels, axis=1)

    return _array_to_dataframe(data_array, reader_options, mat_data=None)


def _parse_token(token: str) -> Tuple[str, Optional[Tuple[int, ...]]]:
    m = re.match(r"^(?P<key>[^\[\]]+)(?:\[(?P<i>\d+)(?:,(?P<j>\d+))?\])?$", token)
    if not m:
        raise ValueError("invalid token: {}".format(token))
    key = m.group("key")
    i = m.group("i")
    j = m.group("j")
    if i is None:
        return key, None
    if j is None:
        return key, (int(i),)
    return key, (int(i), int(j))


def _apply_index(value: Any, index_spec: Optional[Tuple[int, ...]]) -> Any:
    if index_spec is None:
        return value
    if not isinstance(value, np.ndarray):
        raise TypeError("index specified for non-ndarray")
    if len(index_spec) == 2:
        return value[index_spec[0], index_spec[1]]
    if value.ndim == 1:
        return value[index_spec[0]]
    return value[index_spec[0], 0]


def _get_nested_mat_data(mat_data: dict, key_path: str) -> Any:
    current: Any = mat_data
    for token in key_path.split("."):
        key, idx = _parse_token(token)
        if isinstance(current, dict):
            if key not in current:
                raise KeyError("missing key '{}'".format(key))
            current = current[key]
        elif isinstance(current, np.ndarray) and current.dtype.names:
            if key.isdigit():
                current = current.flat[int(key)]
            else:
                if key not in current.dtype.names:
                    raise KeyError("missing struct field '{}'".format(key))
                current = current[0, 0][key]
        elif isinstance(current, np.void) and current.dtype.names:
            if key not in current.dtype.names:
                raise KeyError("missing struct field '{}'".format(key))
            current = current[key]
        else:
            raise TypeError("unexpected type while traversing: {}".format(type(current)))

        current = _apply_index(current, idx)

    while isinstance(current, np.ndarray) and current.dtype == object and current.size == 1:
        current = current.item()
    return current


def _array_to_dataframe(data_array: Any, reader_options: Dict[str, Any], mat_data: Optional[dict]) -> pd.DataFrame:
    if isinstance(data_array, np.ndarray) and data_array.dtype.names:
        raise TypeError("data_key points to MATLAB struct; use a lower-level numeric field")

    if isinstance(data_array, np.ndarray) and data_array.dtype == object:
        object_array_index = reader_options.get("object_array_index")
        if object_array_index is not None:
            if not isinstance(object_array_index, (list, tuple)):
                raise TypeError("object_array_index must be list/tuple")
            if len(object_array_index) == 1:
                data_array = data_array.flatten()[int(object_array_index[0])]
            elif len(object_array_index) == 2:
                data_array = data_array[int(object_array_index[0]), int(object_array_index[1])]
            else:
                raise ValueError("object_array_index must have 1 or 2 elements")
        elif data_array.size == 1:
            data_array = data_array.item()
        else:
            raise TypeError("object ndarray requires object_array_index")

    if isinstance(data_array, (list, tuple)):
        data_array = np.asarray(data_array)
    elif np.isscalar(data_array):
        data_array = np.asarray([[data_array]])

    if not isinstance(data_array, np.ndarray):
        raise TypeError("data is not ndarray")

    if data_array.ndim == 1:
        data_array = data_array.reshape(-1, 1)

    if reader_options.get("transpose_data", False):
        data_array = data_array.T

    n_samples = data_array.shape[0]

    time_vector = None
    if mat_data is not None:
        time_key = reader_options.get("time_key")
        fs_key = reader_options.get("fs_key")
        if time_key:
            t = _get_nested_mat_data(mat_data, time_key)
            if not isinstance(t, np.ndarray):
                raise TypeError("time_key must point to ndarray")
            time_vector = t.flatten()
        elif fs_key:
            fs = _get_nested_mat_data(mat_data, fs_key)
            if isinstance(fs, np.ndarray):
                fs = np.asarray(fs).flatten()[0]
            time_vector = np.arange(n_samples) / float(fs)

    if time_vector is None and not _should_skip_auto_time_generation(reader_options):
        time_vector = np.arange(n_samples)

    channel_names = None
    channel_names_key = reader_options.get("channel_names_key")
    if mat_data is not None and channel_names_key:
        raw = _get_nested_mat_data(mat_data, channel_names_key)
        if isinstance(raw, np.ndarray) and raw.dtype == object:
            channel_names = [str(item.item()) for item in raw.flatten()]
        elif isinstance(raw, np.ndarray):
            channel_names = [str(item) for item in raw.flatten()]
        elif isinstance(raw, (list, tuple)):
            channel_names = [str(item) for item in raw]
        else:
            raise TypeError("unsupported channel_names format")

    if channel_names is None:
        channel_names = ["ch{}".format(i + 1) for i in range(data_array.shape[1])]
    if reader_options.get("strip_channel_names", False):
        # Explicit opt-in for MATLAB fixed-width string padding.
        channel_names = [label.strip() for label in channel_names]

    if time_vector is not None and data_array.shape[0] != len(time_vector):
        if data_array.shape[1] == len(time_vector):
            data_array = data_array.T
        else:
            raise ValueError("data/time length mismatch")

    if data_array.shape[1] != len(channel_names):
        if len(channel_names) == 1:
            base = channel_names[0]
            channel_names = ["{}_{}".format(base, i + 1) for i in range(data_array.shape[1])]
        else:
            raise ValueError("data/channel name count mismatch")

    df = pd.DataFrame(data_array, columns=channel_names)
    if time_vector is not None:
        df["time"] = time_vector

    extra_keys = reader_options.get("extra_metadata_keys", {})
    if mat_data is not None and extra_keys:
        for col_name, extra_key in extra_keys.items():
            try:
                val = _get_nested_mat_data(mat_data, extra_key)
                if isinstance(val, np.ndarray):
                    if val.size == 1:
                        val = val.flat[0]
                    elif val.ndim == 2 and (val.shape[0] == 1 or val.shape[1] == 1):
                        val = val.flatten()[0]
                if isinstance(val, bytes):
                    val = val.decode("utf-8", errors="replace")
                df[col_name] = val
            except Exception:
                pass

    return df


def read_mat_file(path: str, reader_options: Optional[dict] = None) -> pd.DataFrame:
    if reader_options is None:
        reader_options = {}

    data_key = reader_options.get("data_key")
    if not data_key or str(data_key).lower() == "none":
        raise ValueError("reader_options.data_key must be explicitly specified")

    if _is_mat_v73(path):
        return _read_mat_h5(path, reader_options)

    mat_data = loadmat(path, squeeze_me=False)
    data_array = _get_nested_mat_data(mat_data, str(data_key))
    return _array_to_dataframe(data_array, reader_options, mat_data=mat_data)


def find_timeseries_in_mat(path: str, min_rows: int = 100, max_depth: int = 4) -> List[Tuple[str, Tuple[int, ...], str]]:
    results: List[Tuple[str, Tuple[int, ...], str]] = []

    if _is_mat_v73(path):
        if h5py is None:
            return results

        def walk_h5(node: Any, prefix: str = "", depth: int = 0) -> None:
            if depth >= max_depth:
                return
            if isinstance(node, h5py.Dataset):
                shape = node.shape
                dtype = node.dtype
                if len(shape) == 2 and shape[0] >= min_rows and dtype.kind in ("f", "i", "u"):
                    results.append((prefix, shape, str(dtype)))
            elif isinstance(node, (h5py.File, h5py.Group)):
                for key in node.keys():
                    walk_h5(node[key], "{}.{}".format(prefix, key) if prefix else key, depth + 1)

        try:
            with h5py.File(path, "r") as f:
                walk_h5(f)
        except Exception:
            pass
        return results

    def walk_py(node: Any, prefix: str = "", depth: int = 0) -> None:
        if depth >= max_depth:
            return

        if isinstance(node, dict):
            for key, val in node.items():
                if key.startswith("__"):
                    continue
                walk_py(val, "{}.{}".format(prefix, key) if prefix else key, depth + 1)
            return

        if isinstance(node, np.ndarray):
            if node.dtype.names:
                if node.size > 0:
                    base = node.flat[0]
                    for field in node.dtype.names:
                        try:
                            walk_py(base[field], "{}.{}".format(prefix, field) if prefix else field, depth + 1)
                        except Exception:
                            continue
                return

            if node.dtype.kind in ("f", "i", "u") and node.ndim == 2 and node.shape[0] >= min_rows:
                results.append((prefix, node.shape, str(node.dtype)))
                return

            if node.dtype == object:
                for idx, item in enumerate(node.flat):
                    walk_py(item, "{}[{}]".format(prefix, idx), depth + 1)

    try:
        mat_data = loadmat(path, squeeze_me=False)
        walk_py(mat_data)
    except Exception:
        pass

    return results


def _get_mat_struct_keys(data: Any) -> List[str]:
    if isinstance(data, dict):
        return [k for k in data.keys() if not k.startswith("__")]
    if isinstance(data, np.ndarray) and data.dtype.names:
        keys = list(data.dtype.names)
        if data.size > 0:
            keys.extend([str(i) for i in range(data.size)])
        return keys
    if isinstance(data, np.void) and data.dtype.names:
        return list(data.dtype.names)
    return []


def _navigate_mat_struct(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data[key]
    if isinstance(data, np.ndarray) and data.dtype.names:
        if key.isdigit():
            return data.flat[int(key)]
        return data[0, 0][key]
    if isinstance(data, np.void) and data.dtype.names:
        return data[key]
    raise KeyError("'{}' not found".format(key))


def _expand_mat_data_key_template(
    mat_data: dict,
    template: str,
    mat_key_patterns: Dict[str, str],
) -> List[Tuple[str, Dict[str, str]]]:
    segments = template.split(".")

    def recurse(current_data: Any, seg_idx: int, path_parts: List[str], matches: Dict[str, str]) -> List[Tuple[str, Dict[str, str]]]:
        if seg_idx >= len(segments):
            return [(".".join(path_parts), dict(matches))]

        segment = segments[seg_idx]
        out: List[Tuple[str, Dict[str, str]]] = []
        ph_match = re.match(r"^\{(\w+)\}$", segment)

        if ph_match:
            ph_name = ph_match.group(1)
            pattern_str = mat_key_patterns.get(ph_name, "(.*)")
            clean = pattern_str[1:-1] if pattern_str.startswith("(") and pattern_str.endswith(")") else pattern_str
            pat = re.compile("^(?:{})$".format(clean))

            for key in sorted(_get_mat_struct_keys(current_data)):
                if pat.match(key):
                    try:
                        next_data = _navigate_mat_struct(current_data, key)
                        new_matches = dict(matches)
                        new_matches[ph_name] = key
                        out.extend(recurse(next_data, seg_idx + 1, path_parts + [key], new_matches))
                    except Exception:
                        continue
        else:
            available = _get_mat_struct_keys(current_data)
            if segment in available:
                try:
                    next_data = _navigate_mat_struct(current_data, segment)
                    out.extend(recurse(next_data, seg_idx + 1, path_parts + [segment], matches))
                except Exception:
                    pass

        return out

    return recurse(mat_data, 0, [], {})


def _expand_h5_data_key_template(
    h5file: Any,
    template: str,
    mat_key_patterns: Dict[str, str],
) -> List[Tuple[str, Dict[str, str]]]:
    if h5py is None:
        return []

    segments = template.split(".")

    def recurse(current: Any, seg_idx: int, path_parts: List[str], matches: Dict[str, str]) -> List[Tuple[str, Dict[str, str]]]:
        if seg_idx >= len(segments):
            return [(".".join(path_parts), dict(matches))]

        segment = segments[seg_idx]
        out: List[Tuple[str, Dict[str, str]]] = []
        ph_match = re.match(r"^\{(\w+)\}$", segment)

        if ph_match:
            ph_name = ph_match.group(1)
            pattern_str = mat_key_patterns.get(ph_name, "(.*)")
            clean = pattern_str[1:-1] if pattern_str.startswith("(") and pattern_str.endswith(")") else pattern_str
            pat = re.compile("^(?:{})$".format(clean))
            if isinstance(current, (h5py.File, h5py.Group)):
                for key in sorted(current.keys()):
                    if pat.match(key):
                        new_matches = dict(matches)
                        new_matches[ph_name] = key
                        out.extend(recurse(current[key], seg_idx + 1, path_parts + [key], new_matches))
        else:
            if isinstance(current, (h5py.File, h5py.Group)) and segment in current:
                out.extend(recurse(current[segment], seg_idx + 1, path_parts + [segment], matches))

        return out

    return recurse(h5file, 0, [], {})


def _process_single_mat_template(
    file_path: str,
    template: str,
    mat_key_patterns: Dict[str, str],
    reader_options: dict,
) -> List[pd.DataFrame]:
    out: List[pd.DataFrame] = []

    if _is_mat_v73(file_path):
        if h5py is None:
            print("  [警告] h5py 未インストール。スキップ: {}".format(file_path))
            return out

        with h5py.File(file_path, "r") as f:
            expansions = _expand_h5_data_key_template(f, template, mat_key_patterns)
            if not expansions:
                print("  [警告] テンプレート一致なし (HDF5): {}".format(file_path))
                return out

            for data_key, match_dict in expansions:
                opts = dict(reader_options)
                opts["data_key"] = data_key
                try:
                    df = _read_mat_h5(file_path, opts)
                    df["source_file_path"] = file_path
                    for k, v in match_dict.items():
                        df[k] = v
                    out.append(df)
                except Exception as e:
                    print("    [警告] HDF5 data_key '{}' 読み込み失敗: {}".format(data_key, e))
        return out

    mat_data = loadmat(file_path, squeeze_me=False)
    expansions = _expand_mat_data_key_template(mat_data, template, mat_key_patterns)
    if not expansions:
        print("  [警告] テンプレート一致なし (MATLAB): {}".format(file_path))
        return out

    for data_key, match_dict in expansions:
        try:
            data_array = _get_nested_mat_data(mat_data, data_key)
            df = _array_to_dataframe(data_array, reader_options, mat_data=mat_data)
            df["source_file_path"] = file_path
            for k, v in match_dict.items():
                df[k] = v
            out.append(df)
        except Exception as e:
            print("    [警告] data_key '{}' 読み込み失敗: {}".format(data_key, e))

    return out


def load_mat_with_template(spark: Any, file_path_list: List[str], rule_config: dict) -> Any:
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from harmo.ingest.readers import _batched_pd_to_spark

    template = rule_config["mat_data_key_template"]
    mat_key_patterns = rule_config.get("mat_key_patterns", {})
    reader_options = rule_config.get("reader_options", {})

    all_dfs: List[pd.DataFrame] = []
    total_expansions = 0
    max_workers = min(len(file_path_list), os.cpu_count() or 4)

    if len(file_path_list) <= 1:
        for fp in file_path_list:
            try:
                file_dfs = _process_single_mat_template(fp, template, mat_key_patterns, reader_options)
                all_dfs.extend(file_dfs)
                total_expansions += len(file_dfs)
            except Exception as e:
                print("  [警告] .mat ファイル処理失敗: {}, エラー: {}".format(fp, e))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(_process_single_mat_template, fp, template, mat_key_patterns, reader_options): fp
                for fp in file_path_list
            }
            for future in as_completed(future_to_path):
                fp = future_to_path[future]
                try:
                    file_dfs = future.result()
                    all_dfs.extend(file_dfs)
                    total_expansions += len(file_dfs)
                except Exception as e:
                    print("  [警告] .mat ファイル処理失敗: {}, エラー: {}".format(fp, e))

    print("  [情報] テンプレート展開完了: {} ファイル -> {} データセット".format(len(file_path_list), total_expansions))

    if not all_dfs:
        return None
    return _batched_pd_to_spark(spark, all_dfs)


def load_mat_to_spark(spark: Any, file_path_list: List[str], reader_options: dict) -> Any:
    from functools import partial

    from harmo.ingest.readers import _batched_pd_to_spark, _read_files_parallel

    read_fn = partial(read_mat_file, reader_options=reader_options)
    all_data_dfs = _read_files_parallel(file_path_list, read_fn, format_label=".mat")
    if not all_data_dfs:
        return None
    return _batched_pd_to_spark(spark, all_data_dfs)
