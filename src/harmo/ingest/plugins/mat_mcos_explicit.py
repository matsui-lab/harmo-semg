"""Explicit MAT plugin with MCOS support for legacy MATLAB table objects."""

from __future__ import annotations

import re
import struct
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.io import loadmat
from scipy.io.matlab._mio5_params import MatlabOpaque

from harmo.ingest.plugins.base import BaseIngestPlugin
from harmo.ingest.utils.mat_preconvert import preconvert_mat_tables


def _parse_mcos_cells(fw_raw: bytes) -> list:
    pos = 152 + 16
    _st, ss = struct.unpack("<II", fw_raw[pos : pos + 8])
    pos += 8 + ss
    if pos % 8:
        pos += 8 - (pos % 8)
    tag = struct.unpack("<I", fw_raw[pos : pos + 4])[0]
    if tag >> 16 != 0:
        pos += 8
    else:
        ns = struct.unpack("<I", fw_raw[pos + 4 : pos + 8])[0]
        pos += 8 + ns
        if pos % 8:
            pos += 8 - (pos % 8)

    cells: list = []
    while pos < len(fw_raw) - 8:
        st = struct.unpack("<I", fw_raw[pos : pos + 4])[0]
        if st != 14:
            break
        ss = struct.unpack("<I", fw_raw[pos + 4 : pos + 8])[0]
        af = struct.unpack("<II", fw_raw[pos + 16 : pos + 24])
        mclass = af[0] & 0xFF
        cells.append((pos, ss, mclass))
        pos += 8 + ss
        if pos % 8:
            pos += 8 - (pos % 8)
    return cells


def _mcos_parse_cell_of_strings(fw_raw: bytes, cell_pos: int) -> List[str]:
    p = cell_pos + 8 + 16
    _, ds = struct.unpack("<II", fw_raw[p : p + 8])
    ndims = ds // 4
    dims = struct.unpack("<{}I".format(ndims), fw_raw[p + 8 : p + 8 + ds])
    p += 8 + ds
    if p % 8:
        p += 8 - (p % 8)
    tag = struct.unpack("<I", fw_raw[p : p + 4])[0]
    if tag >> 16 != 0:
        p += 8
    else:
        ns = struct.unpack("<I", fw_raw[p + 4 : p + 8])[0]
        p += 8 + ns
        if p % 8:
            p += 8 - (p % 8)

    n_elems = 1
    for d in dims:
        n_elems *= d

    out: List[str] = []
    for _ in range(n_elems):
        mt = struct.unpack("<I", fw_raw[p : p + 4])[0]
        ms = struct.unpack("<I", fw_raw[p + 4 : p + 8])[0]
        if mt != 14:
            break

        ip = p + 8 + 16
        _, ds2 = struct.unpack("<II", fw_raw[ip : ip + 8])
        ip += 8 + ds2
        if ip % 8:
            ip += 8 - (ip % 8)
        nt2 = struct.unpack("<I", fw_raw[ip : ip + 4])[0]
        if nt2 >> 16 != 0:
            ip += 8
        else:
            ns2 = struct.unpack("<I", fw_raw[ip + 4 : ip + 8])[0]
            ip += 8 + ns2
            if ip % 8:
                ip += 8 - (ip % 8)

        dt2 = struct.unpack("<I", fw_raw[ip : ip + 4])[0]
        if dt2 >> 16 != 0:
            sz = dt2 >> 16
            tp = dt2 & 0xFFFF
            chars = fw_raw[ip + 4 : ip + 4 + sz]
        else:
            sz = struct.unpack("<I", fw_raw[ip + 4 : ip + 8])[0]
            tp = dt2
            chars = fw_raw[ip + 8 : ip + 8 + sz]

        if tp == 4:
            s = chars.decode("utf-16-le", errors="replace").strip("\x00")
        else:
            s = chars.decode("ascii", errors="replace").strip("\x00")
        out.append(s)

        p += 8 + ms
        if p % 8:
            p += 8 - (p % 8)

    return out


def _mcos_parse_cell_of_doubles(fw_raw: bytes, cell_pos: int) -> List[np.ndarray]:
    p = cell_pos + 8 + 16
    _, ds = struct.unpack("<II", fw_raw[p : p + 8])
    ndims = ds // 4
    dims = struct.unpack("<{}I".format(ndims), fw_raw[p + 8 : p + 8 + ds])
    p += 8 + ds
    if p % 8:
        p += 8 - (p % 8)
    tag = struct.unpack("<I", fw_raw[p : p + 4])[0]
    if tag >> 16 != 0:
        p += 8
    else:
        ns = struct.unpack("<I", fw_raw[p + 4 : p + 8])[0]
        p += 8 + ns
        if p % 8:
            p += 8 - (p % 8)

    n_elems = 1
    for d in dims:
        n_elems *= d

    arrays: List[np.ndarray] = []
    for _ in range(n_elems):
        m_pos = p
        _mt = struct.unpack("<I", fw_raw[m_pos : m_pos + 4])[0]
        ms = struct.unpack("<I", fw_raw[m_pos + 4 : m_pos + 8])[0]

        ip = m_pos + 8 + 16
        _, ds2 = struct.unpack("<II", fw_raw[ip : ip + 8])
        ip += 8 + ds2
        if ip % 8:
            ip += 8 - (ip % 8)
        nt2 = struct.unpack("<I", fw_raw[ip : ip + 4])[0]
        if nt2 >> 16 != 0:
            ip += 8
        else:
            ns2 = struct.unpack("<I", fw_raw[ip + 4 : ip + 8])[0]
            ip += 8 + ns2
            if ip % 8:
                ip += 8 - (ip % 8)

        _dt2, ds2 = struct.unpack("<II", fw_raw[ip : ip + 8])
        arr = np.frombuffer(fw_raw[ip + 8 : ip + 8 + ds2], dtype=np.float64).copy()
        arrays.append(arr)

        p += 8 + ms
        if p % 8:
            p += 8 - (p % 8)

    return arrays


def _is_mcos_table(obj: Any) -> bool:
    if not isinstance(obj, MatlabOpaque):
        return False
    if obj.size == 0:
        return False
    item = obj.flat[0]
    return bool(
        hasattr(item, "dtype")
        and item.dtype.names
        and "s1" in item.dtype.names
        and "arr" in item.dtype.names
    )


def _mcos_matrix_payload(fw_raw: bytes, cell_pos: int) -> bytes:
    """Return the real-data payload of an outer MCOS cell matrix.

    MATLAB's undocumented subsystem stores its linking metadata in the first
    cell of ``FileWrapper__``.  The value is a regular MAT v5 matrix, so the
    same tag and eight-byte alignment rules apply as for the later table
    property cells.
    """
    p = cell_pos + 8 + 16
    if p + 8 > len(fw_raw):
        raise ValueError("truncated MCOS matrix dimensions")
    _dims_type, dims_size = struct.unpack("<II", fw_raw[p : p + 8])
    p += 8 + dims_size
    if p % 8:
        p += 8 - (p % 8)

    if p + 4 > len(fw_raw):
        raise ValueError("truncated MCOS matrix name")
    name_tag = struct.unpack("<I", fw_raw[p : p + 4])[0]
    if name_tag >> 16:
        p += 8
    else:
        if p + 8 > len(fw_raw):
            raise ValueError("truncated MCOS matrix name tag")
        name_size = struct.unpack("<I", fw_raw[p + 4 : p + 8])[0]
        p += 8 + name_size
        if p % 8:
            p += 8 - (p % 8)

    if p + 4 > len(fw_raw):
        raise ValueError("truncated MCOS matrix data")
    data_tag = struct.unpack("<I", fw_raw[p : p + 4])[0]
    if data_tag >> 16:
        data_size = data_tag >> 16
        payload_start = p + 4
    else:
        if p + 8 > len(fw_raw):
            raise ValueError("truncated MCOS matrix data tag")
        data_size = struct.unpack("<I", fw_raw[p + 4 : p + 8])[0]
        payload_start = p + 8
    payload_end = payload_start + data_size
    if payload_end > len(fw_raw):
        raise ValueError("MCOS matrix payload exceeds subsystem bounds")
    return fw_raw[payload_start:payload_end]


def _mcos_property_cell_ids(metadata: bytes, object_id: int) -> Dict[str, int]:
    """Map one MCOS object to its field-content cell IDs.

    Object IDs and normal-object IDs diverge whenever a saved object (for
    example a MATLAB ``string``) occurs in the subsystem.  Treating the object
    ID as a fixed seven-cell table offset therefore selects a different trial
    after the first such object.  Region 3 supplies the object-to-normal-object
    link and Region 4 supplies the actual property-to-cell mapping.
    """
    if len(metadata) < 40:
        raise ValueError("MCOS linking metadata is too short")
    version, string_count = struct.unpack("<II", metadata[:8])
    if version not in {2, 3, 4}:
        raise ValueError("unsupported MCOS linking metadata version {}".format(version))
    offsets = struct.unpack("<8I", metadata[8:40])
    class_start, _saveobj_start, object_start, property_start, property_end = offsets[
        :5
    ]
    if not (
        40
        <= class_start
        <= object_start
        <= property_start
        <= property_end
        <= len(metadata)
    ):
        raise ValueError("invalid MCOS linking metadata offsets")

    raw_names = metadata[40:class_start].split(b"\0")
    names = [value.decode("utf-8") for value in raw_names if value]
    if len(names) != string_count:
        raise ValueError(
            "MCOS linking metadata declared {} names but stored {}".format(
                string_count, len(names)
            )
        )

    object_bytes = metadata[object_start:property_start]
    if len(object_bytes) % 24:
        raise ValueError("MCOS object region is not aligned to six integers")
    object_records = [
        struct.unpack("<6I", object_bytes[index : index + 24])
        for index in range(0, len(object_bytes), 24)
    ]
    if object_id <= 0 or object_id >= len(object_records):
        raise ValueError("MCOS object ID {} is out of range".format(object_id))
    normal_object_id = int(object_records[object_id][4])
    if normal_object_id <= 0:
        raise ValueError("MCOS object {} has no normal-object record".format(object_id))

    blocks: List[List[Tuple[int, int, int]]] = []
    position = property_start
    while position < property_end:
        if position + 4 > property_end:
            raise ValueError("truncated MCOS property block")
        field_count = struct.unpack("<I", metadata[position : position + 4])[0]
        position += 4
        fields: List[Tuple[int, int, int]] = []
        for _ in range(field_count):
            if position + 12 > property_end:
                raise ValueError("truncated MCOS property field")
            fields.append(struct.unpack("<3I", metadata[position : position + 12]))
            position += 12
        position = (position + 7) // 8 * 8
        if position > property_end:
            raise ValueError("MCOS property padding exceeds region bounds")
        blocks.append(fields)
    if normal_object_id >= len(blocks):
        raise ValueError(
            "MCOS normal-object ID {} is out of range".format(normal_object_id)
        )

    properties: Dict[str, int] = {}
    for name_id, field_type, value in blocks[normal_object_id]:
        if field_type != 1:
            continue
        if name_id <= 0 or name_id > len(names):
            raise ValueError("MCOS property name ID {} is out of range".format(name_id))
        properties[names[name_id - 1]] = int(value)
    return properties


def _linked_mcos_table_cells(
    fw_raw: bytes, cells: List[Tuple[int, int, int]], opaque_obj: Any
) -> Tuple[int, int]:
    if not cells:
        raise ValueError("MCOS subsystem contains no cells")
    item = opaque_obj.flat[0] if opaque_obj.size > 0 else opaque_obj
    object_id = int(item["arr"].flatten()[4])
    metadata = _mcos_matrix_payload(fw_raw, cells[0][0])
    properties = _mcos_property_cell_ids(metadata, object_id)
    missing = {"data", "varnames"}.difference(properties)
    if missing:
        raise ValueError("MCOS table lacks properties {}".format(sorted(missing)))
    data_cell_index = 2 + properties["data"]
    varnames_cell_index = 2 + properties["varnames"]
    if data_cell_index >= len(cells) or varnames_cell_index >= len(cells):
        raise ValueError("MCOS table property points outside the subsystem cell array")
    return cells[data_cell_index][0], cells[varnames_cell_index][0]


def _resolve_mcos_table(
    mat_data: dict, opaque_obj: Any
) -> Tuple[np.ndarray, List[str]]:
    fw = mat_data.get("__function_workspace__")
    if fw is None:
        raise ValueError("__function_workspace__ not found for MCOS table")

    fw_raw = fw.flatten().tobytes()
    cells = _parse_mcos_cells(fw_raw)

    candidate_cells: List[Tuple[int, int]] = []
    linking_error: Optional[Exception] = None
    try:
        candidate_cells.append(_linked_mcos_table_cells(fw_raw, cells, opaque_obj))
    except (KeyError, IndexError, TypeError, ValueError, struct.error) as exc:
        linking_error = exc

    # Compatibility fallback for older subsystem variants that predate the
    # explicit linking-metadata parser.  It is intentionally tried only after
    # the object-to-normal-object mapping above.
    item = opaque_obj.flat[0] if opaque_obj.size > 0 else opaque_obj
    arr = item["arr"]
    obj_idx = int(arr.flatten()[4])
    group_start = 2 + (obj_idx - 1) * 7
    for start in (group_start, group_start + 1, group_start - 1):
        if start >= 0 and start + 5 < len(cells):
            candidate_cells.append((cells[start][0], cells[start + 5][0]))

    tried: List[str] = []
    if linking_error is not None:
        tried.append("linking-metadata={}".format(linking_error))
    seen_cells = set()
    for data_pos, varnames_pos in candidate_cells:
        if (data_pos, varnames_pos) in seen_cells:
            continue
        seen_cells.add((data_pos, varnames_pos))
        try:
            columns = _mcos_parse_cell_of_doubles(fw_raw, data_pos)
            if not columns:
                tried.append("data_pos={} no-columns".format(data_pos))
                continue

            lengths = [int(c.size) for c in columns]
            dominant_len, _ = Counter(lengths).most_common(1)[0]
            if dominant_len <= 0:
                tried.append(
                    "data_pos={} non-positive dominant length".format(data_pos)
                )
                continue

            keep_idx = [i for i, c in enumerate(columns) if int(c.size) == dominant_len]
            if not keep_idx:
                tried.append("data_pos={} no dominant columns".format(data_pos))
                continue

            columns = [columns[i] for i in keep_idx]

            col_names = _mcos_parse_cell_of_strings(fw_raw, varnames_pos)
            if col_names:
                col_names = [col_names[i] for i in keep_idx if i < len(col_names)]
            data_2d = np.column_stack(columns)

            if len(col_names) != data_2d.shape[1]:
                if len(col_names) == 1:
                    base = col_names[0] or "ch"
                    col_names = [
                        "{}_{}".format(base, i + 1) for i in range(data_2d.shape[1])
                    ]
                else:
                    col_names = ["ch{}".format(i + 1) for i in range(data_2d.shape[1])]

            return data_2d, col_names
        except Exception as exc:
            tried.append("data_pos={} {}".format(data_pos, exc))
            continue

    raise ValueError(
        "Failed to resolve MCOS table cells: {}".format("; ".join(tried[:5]))
    )


def _parse_token(token: str) -> Tuple[str, Optional[Tuple[int, ...]]]:
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


def _apply_index(value: Any, index_spec: Optional[Tuple[int, ...]]) -> Any:
    if index_spec is None:
        return value
    if not isinstance(value, np.ndarray):
        raise TypeError("index specified on non-ndarray")
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
                raise KeyError("missing key: {}".format(key))
            current = current[key]
        elif isinstance(current, np.ndarray) and current.dtype.names:
            if key.isdigit():
                current = current.flat[int(key)]
            else:
                if current.ndim == 1:
                    current = current.flat[0][key]
                else:
                    current = current[0, 0][key]
        elif isinstance(current, np.void) and current.dtype.names:
            current = current[key]
        else:
            raise TypeError(
                "unexpected type in key traversal: {}".format(type(current))
            )
        current = _apply_index(current, idx)

    while (
        isinstance(current, np.ndarray)
        and current.dtype == object
        and current.size == 1
    ):
        current = current.item()
    return current


def _get_struct_keys(data: Any) -> List[str]:
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


def _navigate(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        return data[key]
    if isinstance(data, np.ndarray) and data.dtype.names:
        if key.isdigit():
            return data.flat[int(key)]
        return data[0, 0][key]
    if isinstance(data, np.void) and data.dtype.names:
        return data[key]
    raise KeyError("cannot navigate key {}".format(key))


def _expand_template(
    mat_data: dict, template: str, key_patterns: Dict[str, str]
) -> List[Tuple[str, Dict[str, str]]]:
    segments = template.split(".")

    def recurse(
        current: Any, idx: int, parts: List[str], matches: Dict[str, str]
    ) -> List[Tuple[str, Dict[str, str]]]:
        if idx >= len(segments):
            return [(".".join(parts), dict(matches))]

        segment = segments[idx]
        out: List[Tuple[str, Dict[str, str]]] = []
        ph = re.match(r"^\{(\w+)\}$", segment)

        if ph:
            name = ph.group(1)
            pat_str = key_patterns.get(name, "(.*)")
            clean = (
                pat_str[1:-1]
                if pat_str.startswith("(") and pat_str.endswith(")")
                else pat_str
            )
            pat = re.compile("^(?:{})$".format(clean))

            for key in sorted(_get_struct_keys(current)):
                if not pat.match(key):
                    continue
                try:
                    next_data = _navigate(current, key)
                    new_matches = dict(matches)
                    new_matches[name] = key
                    out.extend(recurse(next_data, idx + 1, parts + [key], new_matches))
                except Exception:
                    continue
        else:
            key, index_spec = _parse_token(segment)
            available = _get_struct_keys(current)
            if key in available:
                try:
                    next_data = _apply_index(_navigate(current, key), index_spec)
                    out.extend(recurse(next_data, idx + 1, parts + [segment], matches))
                except Exception:
                    pass
        return out

    return recurse(mat_data, 0, [], {})


def _array_to_wide(
    data_array: Any,
    mat_data: dict,
    reader_options: Dict[str, Any],
    override_cols: Optional[List[str]] = None,
) -> pd.DataFrame:
    if isinstance(data_array, np.ndarray) and data_array.dtype.names:
        raise TypeError("data is struct, not numeric array")

    if isinstance(data_array, (list, tuple)):
        data_array = np.asarray(data_array)
    elif np.isscalar(data_array):
        data_array = np.asarray([[data_array]])

    if not isinstance(data_array, np.ndarray):
        raise TypeError("data is not ndarray")

    if data_array.ndim == 1:
        data_array = data_array.reshape(-1, 1)

    if reader_options.get("transpose_data", False) and not override_cols:
        data_array = data_array.T

    n_samples, n_channels = data_array.shape

    time_vector = None
    time_provenance = "sample-index-from-zero"
    sampling_rate_hz = None
    time_source_key = None
    time_key = reader_options.get("time_key")
    fs_key = reader_options.get("fs_key")
    if time_key:
        time_vector = _get_nested_mat_data(mat_data, time_key)
        if isinstance(time_vector, np.ndarray):
            time_vector = time_vector.flatten()
        time_provenance = "source-file-time-vector"
        time_source_key = str(time_key)
    elif fs_key:
        sr = _get_nested_mat_data(mat_data, fs_key)
        if isinstance(sr, np.ndarray):
            sr = sr.flatten()[0]
        sampling_rate_hz = float(sr)
        if not np.isfinite(sampling_rate_hz) or sampling_rate_hz <= 0:
            raise ValueError(
                "fs_key did not resolve to a positive finite sampling rate"
            )
        time_vector = np.arange(n_samples) / sampling_rate_hz
        time_provenance = "source-file-sampling-rate"
        time_source_key = str(fs_key)
    else:
        time_vector = None

    channel_names = override_cols
    if channel_names is None:
        ch_key = reader_options.get("channel_names_key")
        if ch_key:
            raw = _get_nested_mat_data(mat_data, ch_key)
            if isinstance(raw, np.ndarray) and raw.dtype == object:
                channel_names = [str(x.item()) for x in raw.flatten()]
            elif isinstance(raw, np.ndarray):
                channel_names = [str(x) for x in raw.flatten()]
            elif isinstance(raw, (list, tuple)):
                channel_names = [str(x) for x in raw]

    if channel_names is None:
        channel_names = ["ch{}".format(i + 1) for i in range(n_channels)]

    channel_names = [str(value).strip() for value in channel_names]

    if len(channel_names) != data_array.shape[1]:
        if len(channel_names) == data_array.shape[0]:
            data_array = data_array.T
        elif (
            len(channel_names) == data_array.shape[2] if data_array.ndim == 3 else False
        ):
            data_array = data_array.reshape(data_array.shape[0], -1)

    if len(channel_names) != data_array.shape[1]:
        if len(channel_names) == 1:
            base = channel_names[0]
            channel_names = [
                "{}_{}".format(base, i + 1) for i in range(data_array.shape[1])
            ]
        else:
            # Keep ingestion deterministic by falling back to generic channel names.
            channel_names = ["ch{}".format(i + 1) for i in range(data_array.shape[1])]

    embedded_time_candidates = [
        str(value).strip().lower()
        for value in reader_options.get("embedded_time_column_candidates", [])
    ]
    if time_vector is None and embedded_time_candidates:
        matches = [
            index
            for index, name in enumerate(channel_names)
            if name.strip().lower() in embedded_time_candidates
        ]
        if len(matches) != 1:
            raise ValueError(
                "embedded time column candidates resolved to {} columns".format(
                    len(matches)
                )
            )
        time_index = matches[0]
        time_name = channel_names[time_index]
        time_vector = np.asarray(data_array[:, time_index], dtype=float).flatten()
        if not np.isfinite(time_vector).all():
            raise ValueError("embedded time column contains non-finite values")
        if len(time_vector) > 1 and not np.all(np.diff(time_vector) > 0):
            raise ValueError("embedded time column must be strictly increasing")
        data_array = np.delete(data_array, time_index, axis=1)
        channel_names.pop(time_index)
        time_provenance = "source-file-time-vector"
        time_source_key = "table-column:{}".format(time_name)

    if time_vector is None:
        time_vector = np.arange(data_array.shape[0])

    if time_vector is not None and len(time_vector) != data_array.shape[0]:
        if len(time_vector) == data_array.shape[1]:
            data_array = data_array.T
        else:
            raise ValueError("time length mismatch")

    df = pd.DataFrame(data_array, columns=channel_names)
    if time_vector is not None:
        df["time"] = np.asarray(time_vector).flatten()
    df.attrs.update(
        {
            "time_provenance": time_provenance,
            "sampling_rate_hz": sampling_rate_hz,
            "time_source_key": time_source_key,
        }
    )
    return df


def _to_long(df: pd.DataFrame, max_rows: Optional[int] = None) -> pd.DataFrame:
    source_attrs = dict(df.attrs)
    if max_rows is not None and max_rows > 0:
        df = df.head(int(max_rows)).copy()
    time_col = "time" if "time" in df.columns else None
    if time_col is None:
        for c in ["Header", "Time", "TIME"]:
            if c in df.columns:
                time_col = c
                break
    if time_col is None:
        df = df.copy()
        df["time"] = np.arange(len(df), dtype=float)
        time_col = "time"

    id_vars = [time_col]
    for c in df.columns:
        if c in ("activity", "trial", "step", "session_id", "sub_id", "subject_id"):
            id_vars.append(c)

    numeric_cols = [
        c
        for c in df.columns
        if c not in id_vars and pd.api.types.is_numeric_dtype(df[c])
    ]
    if not numeric_cols:
        raise ValueError("no numeric columns for long conversion")

    long_df = df.melt(
        id_vars=id_vars, value_vars=numeric_cols, var_name="channel", value_name="value"
    )
    if time_col != "time":
        long_df = long_df.rename(columns={time_col: "time"})

    long_df["time"] = pd.to_numeric(long_df["time"], errors="coerce")
    long_df["value"] = pd.to_numeric(long_df["value"], errors="coerce")
    long_df["__time_provenance"] = source_attrs.get(
        "time_provenance", "sample-index-from-zero"
    )
    long_df["__sampling_rate_hz"] = source_attrs.get("sampling_rate_hz")
    long_df["__time_source_key"] = source_attrs.get("time_source_key")
    return long_df


class Plugin(BaseIngestPlugin):
    name = "mat_mcos_explicit"
    version = "1.0.0"

    def _extract_single(
        self,
        mat_data: dict,
        data_key: str,
        reader_options: Dict[str, Any],
        rule_config: Optional[Dict[str, Any]] = None,
    ) -> pd.DataFrame:
        data = _get_nested_mat_data(mat_data, data_key)
        mcos_cols = None
        if _is_mcos_table(data):
            data, mcos_cols = _resolve_mcos_table(mat_data, data)
        wide = _array_to_wide(data, mat_data, reader_options, override_cols=mcos_cols)
        rule_config = rule_config or {}
        transform = rule_config.get("column_transform") or {}
        if transform.get("target_col") == "channel" and transform.get("prepend_col"):
            metadata_key = str(transform["prepend_col"])
            configured_path = str(
                (reader_options.get("extra_metadata_keys") or {}).get(metadata_key, "")
            )
            if not configured_path:
                raise ValueError(
                    "column_transform prepend_col lacks extra_metadata_keys entry"
                )
            data_parts = data_key.split(".")
            metadata_parts = configured_path.split(".")
            if (
                len(data_parts) >= 4
                and len(metadata_parts) == 3
                and data_parts[:2] == metadata_parts[:2]
            ):
                configured_path = ".".join(data_parts[:-1] + [metadata_parts[-1]])
            raw_prefix = _get_nested_mat_data(mat_data, configured_path)
            if isinstance(raw_prefix, np.ndarray):
                if raw_prefix.size != 1:
                    raise ValueError(
                        "column_transform prefix must resolve to one value per expansion"
                    )
                raw_prefix = raw_prefix.flatten()[0]
            prefix = str(raw_prefix).strip()
            separator = str(transform.get("separator", "_"))
            renamed = {
                column: "{}{}{}".format(prefix, separator, str(column).strip())
                for column in wide.columns
                if column != "time"
            }
            wide = wide.rename(columns=renamed)
        return wide

    def _discover_candidate_keys(
        self, mat_data: dict, depth_limit: int = 5
    ) -> List[str]:
        found: List[str] = []

        def walk(node: Any, prefix: str = "", depth: int = 0) -> None:
            if depth > depth_limit:
                return

            if _is_mcos_table(node) and prefix:
                found.append(prefix)
                return
            if isinstance(node, dict):
                for key, val in node.items():
                    if key.startswith("__"):
                        continue
                    path = "{}.{}".format(prefix, key) if prefix else key
                    walk(val, path, depth + 1)
                return

            if isinstance(node, np.ndarray) and node.dtype.names is not None:
                if node.size > 0:
                    base = node.flat[0]
                    for field in node.dtype.names:
                        try:
                            val = base[field]
                        except Exception:
                            continue
                        path = "{}.{}".format(prefix, field) if prefix else field
                        walk(val, path, depth + 1)
                return

            if isinstance(node, np.void) and node.dtype.names is not None:
                for field in node.dtype.names:
                    path = "{}.{}".format(prefix, field) if prefix else field
                    walk(node[field], path, depth + 1)
                return

            if isinstance(node, np.ndarray):
                if (
                    node.dtype.kind in ("f", "i", "u", "c")
                    and node.ndim >= 2
                    and max(node.shape) >= 20
                ):
                    if prefix:
                        found.append(prefix)
                if node.dtype == object and node.size > 0 and prefix:
                    found.append(prefix)
                return

        walk(mat_data)

        uniq: List[str] = []
        seen = set()
        for key in found:
            if key not in seen:
                uniq.append(key)
                seen.add(key)
        return uniq

    def extract(self, file_path: str, options: Dict[str, Any]) -> pd.DataFrame:
        rule_config = options.get("_rule_config", {})
        reader_options = dict(rule_config.get("reader_options", {}))
        reader_options.update(options.get("reader_options", {}))

        template = rule_config.get("mat_data_key_template") or options.get(
            "mat_data_key_template"
        )
        key_patterns = dict(rule_config.get("mat_key_patterns", {}))
        key_patterns.update(options.get("mat_key_patterns", {}))

        max_rows = options.get("max_rows")

        try:
            mat_data = loadmat(file_path, squeeze_me=False)
        except Exception:
            if not options.get("allow_octave_fallback", True):
                raise
            converted = preconvert_mat_tables([file_path], str(Path(file_path).parent))
            converted_path = converted[0] if converted else file_path
            mat_data = loadmat(converted_path, squeeze_me=False)

        if template:
            expanded = _expand_template(mat_data, str(template), key_patterns)
            if not expanded:
                raise RuntimeError("no template expansion matched")

            long_parts: List[pd.DataFrame] = []
            for data_key, match_dict in expanded:
                wide = self._extract_single(
                    mat_data, data_key, reader_options, rule_config
                )
                for mk, mv in match_dict.items():
                    wide[mk] = mv
                long_parts.append(_to_long(wide, max_rows=max_rows))
            return pd.concat(long_parts, ignore_index=True)

        data_key = reader_options.get("data_key")
        if data_key and str(data_key).lower() != "none":
            return _to_long(
                self._extract_single(
                    mat_data, str(data_key), reader_options, rule_config
                ),
                max_rows=max_rows,
            )

        candidates = self._discover_candidate_keys(mat_data)
        if not candidates:
            raise RuntimeError("no candidate MAT data keys discovered")

        preferred = options.get(
            "prefer_key_patterns", ["EMG", "Sensor", "Header", "Data", "Marker", "FP"]
        )
        if isinstance(preferred, str):
            preferred = [preferred]
        pats = [re.compile(p, re.IGNORECASE) for p in preferred]

        def score_key(k: str) -> Tuple[int, int]:
            bonus = 1 if any(p.search(k) for p in pats) else 0
            return (bonus, len(k))

        for key in sorted(candidates, key=score_key, reverse=True):
            try:
                return _to_long(
                    self._extract_single(mat_data, key, reader_options, rule_config),
                    max_rows=max_rows,
                )
            except Exception:
                continue

        raise RuntimeError("all discovered candidate keys failed")
