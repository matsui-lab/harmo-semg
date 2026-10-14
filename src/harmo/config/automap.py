"""Bounded, deterministic mapping proposals; never ingest or grant scientific use."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

VERSION = "automap/1.5.0"
MAX_BYTES = 32 * 1024 * 1024
MAX_CELLS = 2_000_000
TIME_NAMES = {"time_s": "s", "time_seconds": "s", "time_ms": "ms",
              "time [s]": "s", "time [ms]": "ms"}
IDENTIFIERS = {"id", "subject", "subject_id", "participant_id", "session_id",
               "trial", "trial_id", "frame", "index", "sample", "study_id", "sub_id",
               "channel", "value", "sensor_type", "unit", "data_type", "processed_id",
               "event_name", "activity", "step", "side", "ses_speed", "ses_trial",
               "source_file_path", "_source_sha256", "_relative_path", "time_unit"}


def declared_channel(label):
    """Only explicit, fully spelled labels; abbreviations remain unresolved."""
    result = {"muscle": "unknown", "side": "unknown", "unit": "unknown", "sensor": "unknown"}
    match = re.fullmatch(r"(Right|Left) (Tibialis Anterior|Medial Gastrocnemius|Soleus)(?: \[(V|mV|uV)\])?", label)
    if match:
        result.update(muscle=match[2], side=match[1].lower(), unit=match[3] or "unknown")
        result["basis"] = "explicit_source_label; not independently validated"
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, obj):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def discover(root, max_entries=10000):
    """Bounded local inventory; never follow links or unpack archives."""
    root = Path(root).resolve()
    require(root.is_dir(), "input_root_required")
    inventory, files = [], []
    signal_suffixes = {".csv", ".tsv", ".txt", ".mat", ".c3d", ".trc", ".mot", ".sto", ".h5", ".hdf5", ".edf", ".hea"}
    entries = 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + names):
            entries += 1
            require(entries <= max_entries, "discovery_entry_limit")
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                inventory.append(dict(source=relative, role="excluded_symlink"))
                continue
            if path.is_dir():
                continue
            suffix = path.suffix.lower()
            role = "signal_candidate" if suffix in signal_suffixes else (
                "archive_requires_extraction" if suffix in {".zip", ".gz", ".tar", ".7z"} else
                "documentation" if suffix in {".pdf", ".html", ".xml", ".md"} else "unclassified")
            inventory.append(dict(source=relative, role=role, bytes=path.stat().st_size))
            if role == "signal_candidate":
                files.append(relative)
                require(len(files) <= 1000, "discovery_file_limit")
    return dict(files=sorted(files), metadata={}, inventory=inventory,
                classification="extension-based candidates; contents are validated by workers")


def mat_leaves(data):
    """Enumerate singleton struct leaves without choosing a trial or dropping cells."""
    import numpy as np
    leaves = {}
    cells = 0
    def visit(path, value, depth):
        nonlocal cells
        require(depth <= 6 and len(leaves) < 1000, "mat_structure_limit")
        require(isinstance(value, np.ndarray), "unsupported_mat_leaf")
        if value.dtype.names:
            require(value.shape == (1, 1), "struct_array_requires_trial_selection")
            for field in value.dtype.names:
                require(bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field)), "unsupported_mat_key")
                visit(path + "." + field, value[0, 0][field], depth + 1)
        else:
            require(value.dtype.kind != "O", "unsupported_nested_or_object_mat")
            cells += value.size
            require(cells <= MAX_CELLS, "cell_count_limit")
            leaves[path] = value
    for key, value in data.items():
        if not key.startswith("__"):
            require(bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)), "unsupported_mat_key")
            visit(key, value, 0)
    return leaves


def inspect_file(path, metadata=None):
    """Return a proposal and losslessly parsed arrays, or an explicit review reason.

    Call through the CLI for a subprocess timeout. Metadata is explicitly
    supplied, not discovered or asserted to be independent evidence.
    """
    import numpy as np
    metadata = metadata or {}
    require(isinstance(metadata, dict), "metadata_object_required")
    require(set(metadata) <= {"time_unit", "time_column", "source_reference", "index_columns", "clock_evidence"},
            "unsupported_metadata_key")
    if metadata:
        require(bool(metadata.get("source_reference")), "metadata_source_required")
    path = Path(path)
    if metadata.get('clock_evidence'):
        from urllib.parse import urlsplit, unquote
        from harmo.document_clock import propose
        proof=metadata['clock_evidence'];uri=urlsplit(proof['source_uri'])
        require(uri.scheme=='file' and not uri.netloc,'local_clock_evidence_required')
        require(propose(path,Path(unquote(uri.path)))==metadata,'clock_evidence_or_payload_changed')
    require(path.stat().st_size <= MAX_BYTES, "file_size_limit")
    raw = path.read_bytes()
    require(len(raw) <= MAX_BYTES, "file_size_limit")
    evidence = []
    fmt = path.suffix.lower()
    if fmt in (".csv", ".tsv", ".txt"):
        from harmo.ingest.readers.csv import read_csv_file
        text = raw.decode("utf-8-sig")
        candidates = []
        for separator in (",", "\t", ";"):
            rows = list(csv.reader(io.StringIO(text), delimiter=separator, strict=True))
            if rows and len(rows[0]) > 1 and all(len(r) == len(rows[0]) for r in rows):
                candidates.append((separator, rows))
        require(len(candidates) == 1, "ambiguous_or_malformed_delimiter")
        separator, rows = candidates[0]
        labels = rows[0]
        require(len(rows) > 2 and (len(rows)-1)*len(labels) <= MAX_CELLS, "cell_count_limit_or_too_short")
        require(all(labels) and len(set(labels)) == len(labels), "empty_or_duplicate_label")
        require(all(not re.fullmatch(r"[+-]?[\d.eE]+", x) for x in labels), "header_required")
        possible = [x for x in labels if x.lower() in TIME_NAMES or x.lower() in ("time", "timestamp")]
        if metadata.get("time_column") and metadata["time_column"] not in possible:
            possible.append(metadata["time_column"])
        require(len(possible) == 1 and possible[0] in labels, "ambiguous_or_missing_time")
        time_name = possible[0]
        unit = TIME_NAMES.get(time_name.lower())
        require(not (unit and metadata.get("time_unit") and unit != metadata["time_unit"]), "time_unit_conflict")
        unit = unit or metadata.get("time_unit")
        require(unit in ("s", "ms"), "explicit_time_unit_required")
        indices=metadata.get('index_columns',[])
        require(isinstance(indices,list) and len(set(indices))==len(indices) and all(c in labels and c.lower() in ('slno','index','sample') for c in indices), 'explicit_index_columns_required')
        require(not any(x.lower() in IDENTIFIERS or x.lower()=='slno' for x in labels if x != time_name and x not in indices), "identifier_column_requires_review")
        def number(value):
            if re.fullmatch(r"[+-]?\d+", value.strip()):
                require(abs(int(value)) <= 2**53, "integer_precision_loss")
            return np.nan if value.strip().lower() in ("", "nan", "na", "n/a") else float(value)
        original = np.array([[number(x) for x in row] for row in rows[1:]], dtype=float)
        for c in indices:
            index=original[:,labels.index(c)]
            require(index[0] in (0,1) and np.array_equal(index,index[0]+np.arange(len(index))), 'index_is_not_consecutive_row_counter')
        # Independent lexical parsing versus the existing reader catches skipped rows/columns.
        frame = read_csv_file(str(path), sep=separator, comment=None, header=True, float_precision="round_trip")
        require(list(frame.columns) == labels and frame.shape == original.shape, "reader_shape_or_labels_changed")
        np.testing.assert_allclose(frame.to_numpy(dtype=float), original, rtol=0, atol=0, equal_nan=True)
        time = original[:, labels.index(time_name)]
        channels = [x for x in labels if x != time_name and x not in indices]
        values = original[:, [labels.index(x) for x in channels]]
        options = {"reader_options": {"sep": separator, "comment": None, "header": True, "float_precision": "round_trip"},
                   "melt_options": {"time_column_candidates": [time_name], "var_name": "channel", "value_name": "value"}}
        if indices:
            options['melt_options']['id_columns']=indices
            evidence.append(dict(field='auxiliary_row_counters',value=indices,origin='all_rows_checked',retained_in_original_source=True))
        evidence.append({"field": "time_unit", "value": unit, "location": "header:"+time_name,
                         "origin": "explicit_header" if time_name.lower() in TIME_NAMES else "user_metadata"})
        structure = {"columns": labels, "rows": len(rows)-1}
    elif fmt == ".mat":
        require("time_column" not in metadata, "time_column_metadata_is_csv_only")
        from scipy.io import loadmat, whosmat
        from harmo.ingest.readers.mat import read_mat_file
        inventory = whosmat(str(path))
        require(sum(int(np.prod(shape)) for _, shape, _ in inventory) <= MAX_CELLS, "cell_count_limit")
        data = mat_leaves(loadmat(str(path)))
        basename = lambda k: k.rsplit(".", 1)[-1].lower()
        times = [k for k in data if basename(k) in TIME_NAMES or basename(k) in ("time", "timestamp")]
        rates = [k for k in data if basename(k) in ("fs_hz", "sampling_rate_hz")]
        require(len(times) <= 1 and len(rates) <= 1 and (times or rates), "ambiguous_or_missing_time")
        label_keys = [k for k in data if basename(k) in ("channel_names", "labels")]
        require(len(label_keys) == 1, "explicit_channel_labels_required")
        label_key = label_keys[0]
        require(data[label_key].dtype.kind in "US", "unsupported_labels")
        channels = [str(x).strip() for x in data[label_key].reshape(-1)]
        require(all(channels) and len(set(channels)) == len(channels) and "time" not in channels, "empty_duplicate_or_reserved_label")
        require(not any(c.lower() in IDENTIFIERS or c.lower() in TIME_NAMES for c in channels), "identifier_column_requires_review")
        choices = [k for k, v in data.items() if not k.startswith("__") and k not in times+rates+label_keys
                   and isinstance(v, np.ndarray) and v.ndim == 2 and v.dtype.kind in "fiu" and v.size > 1]
        require(len(choices) == 1, "ambiguous_signal_array")
        key = choices[0]
        require(len({k.rpartition(".")[0] for k in [key] + times + rates + label_keys}) == 1,
                "cross_scope_metadata_requires_review")
        array = data[key]
        if array.dtype.kind in "iu":
            require(all(abs(int(v)) <= 2**53 for v in array.flat), "integer_precision_loss")
        orientations = [t for t in (False, True) if (array.T if t else array).shape[1] == len(channels)]
        require(len(orientations) == 1, "ambiguous_array_orientation")
        transpose = orientations[0]
        values = (array.T if transpose else array).astype(float)
        require(values.size <= MAX_CELLS, "cell_count_limit")
        unit = TIME_NAMES.get(basename(times[0])) if times else "s"
        require(not (unit and metadata.get("time_unit") and unit != metadata["time_unit"]), "time_unit_conflict")
        unit = unit or metadata.get("time_unit")
        require(unit in ("s", "ms"), "explicit_time_unit_required")
        options = {"reader_options": {"data_key": key, "channel_names_key": label_key,
                                      "transpose_data": transpose, "strip_channel_names": True}}
        evidence.append({"field": "channel_labels", "location": "MAT:"+label_key,
                         "origin": "explicit_key", "normalization": "strip surrounding whitespace",
                         "source_values": [str(x) for x in data[label_key].reshape(-1)], "value": channels})
        if times:
            require(data[times[0]].ndim <= 2 and 1 in data[times[0]].shape, "time_must_be_vector")
            time = data[times[0]].reshape(-1).astype(float)
            options["reader_options"]["time_key"] = times[0]
        if rates:
            rate = data[rates[0]]
            require(rate.size == 1 and np.isfinite(rate.item()) and rate.item() > 0, "invalid_sampling_rate")
            expected = np.arange(values.shape[0]) / float(rate.item())
            if times:
                require(len(time) == len(expected) and np.allclose(np.diff(time)*(0.001 if unit == "ms" else 1), np.diff(expected), rtol=1e-6, atol=1e-12), "time_rate_conflict")
            else:
                time = expected
                options["reader_options"]["fs_key"] = rates[0]
        frame = read_mat_file(str(path), options["reader_options"])
        require(list(frame.columns) == channels+["time"], "reader_labels_changed")
        np.testing.assert_array_equal(frame[channels].to_numpy(), values)
        np.testing.assert_array_equal(frame["time"].to_numpy(), time)
        evidence.append({"field": "signal", "value": key, "location": "MAT:"+key, "origin": "unique_numeric_array"})
        evidence.append({"field": "time_unit", "value": unit, "location": "MAT:"+(times or rates)[0], "origin": "explicit_key" if unit == TIME_NAMES.get(basename((times or rates)[0])) or not times else "user_metadata"})
        structure = {"keys": [{"key": k, "shape": list(shape), "type": kind} for k, shape, kind in inventory],
                     "leaves": [{"key": k, "shape": list(v.shape), "dtype": str(v.dtype)} for k, v in data.items()]}
    else:
        raise NotImplementedError("unsupported_format")
    require(len(time) == len(values) and len(time) > 1 and np.isfinite(time).all() and (np.diff(time) > 0).all(), "invalid_time_axis")
    require(not np.isinf(values).any(), "infinite_signal")
    options["static_metadata"] = {"time_unit": unit}
    require(digest(path.read_bytes()) == digest(raw), "source_changed_during_read")
    # Explicit column-major reshape, retaining all-NaN rows and all-NaN channels.
    long_values = values.T.reshape(-1)
    np.testing.assert_array_equal(long_values.reshape(values.shape[1], values.shape[0]).T, values)
    result = {"status": "accepted", "source_sha256": digest(raw), "bytes": len(raw), "structure": structure,
              "values_sha256": digest(values.tobytes()), "time_sha256": digest(time.tobytes()),
              "sampling_rate_hz": (float(1 / (time[1] - time[0])) if unit == "s" and
                  np.allclose(time, time[0] + np.arange(len(time)) * (time[1] - time[0]), rtol=1e-12, atol=1e-12) else None),
              "time_unit": unit, "channels": channels, "options": options, "evidence": evidence,
              "semantic_status": {c: declared_channel(c) for c in channels},
              "scientific_use": "not_evaluated", "all_rows_checked": True,
              "supplied_metadata": metadata,
              "checks": {"reader_values_equal": True, "reshape_roundtrip_equal": True,
                         "rows": len(time), "channels": len(channels), "missing_cells": int(np.isnan(values).sum()),
                         "all_missing_rows": int(np.isnan(values).all(axis=1).sum())}}
    return result, time, values


def run(root, output, files, metadata=None, timeout=30):
    """Inspect an explicit manifest in bounded child processes; output must be new."""
    root, output = Path(root).resolve(), Path(output).resolve()
    require(root.is_dir(), "input_root_required")
    require(not output.exists(), "output_already_exists")
    require(output != root and root not in output.parents, "output_must_be_outside_input_root")
    require(isinstance(files, list) and all(isinstance(f, str) and f for f in files), "file_list_required")
    require(0 < len(files) <= 1000 and len(files) == len(set(files)), "empty_duplicate_or_oversize_manifest")
    metadata = metadata or {}
    require(isinstance(metadata, dict), "metadata_object_required")
    require(set(metadata) <= set(files), "metadata_not_in_manifest")
    output.mkdir(parents=True)
    reports, mappings = [], []
    for index, relative in enumerate(sorted(files)):
        row = {"source": relative, "status": "needs_review"}
        target = root / relative
        try:
            require(not Path(relative).is_absolute() and ".." not in Path(relative).parts, "unsafe_path")
            require(root in target.resolve().parents, "path_outside_root")
            require(target.is_file() and not target.is_symlink(), "regular_file_required")
            row["bytes"] = target.stat().st_size
            require(row["bytes"] <= MAX_BYTES, "file_size_limit")
            before_hash = digest(target.read_bytes())
            row["source_sha256"] = before_hash
            task = output / ("file-{:04d}".format(index))
            task.mkdir()
            request = {"path": str(target), "output": str(task), "metadata": metadata.get(relative, {})}
            save(task / "request.json", request)
            child = subprocess.run([sys.executable, "-m", "harmo.config.automap", "--worker", str(task / "request.json")],
                                   capture_output=True, text=True, timeout=timeout)
            require(child.returncode == 0, "worker_failed")
            row.update(json.loads((task / "result.json").read_text()))
            require(row["source_sha256"] == before_hash and target.stat().st_size <= MAX_BYTES and digest(target.read_bytes()) == before_hash, "source_changed_during_trial")
            candidates = row.get("records", []) if "records" in row else ([row] if row["status"] == "accepted" else [])
            for record_index, candidate in enumerate(candidates):
                # Anchors and escaped literals prevent implicit expansion of the input set.
                mapping = {"name": "auto-"+str(index)+"-"+str(record_index), "enabled": True, "allow_empty": False,
                           "base_input_dir_alias": "source", "iceberg_table": "local.warehouse.raw_data",
                           "input_path_template": "{file}"+target.suffix,
                           "path_patterns": {"file": "^"+re.escape(relative[:-len(target.suffix)])+"(?="+re.escape(target.suffix)+"$)"},
                           "file_format": target.suffix.lstrip(".").lower(), **candidate["options"]}
                mappings.append(mapping)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            row["status"] = "needs_review"
            row["reason"] = str(exc)
        reports.append(row)
    config = {"dataset_status": "draft", "readiness_notes": "Auto-mapping candidates only; no warehouse or scientific release authorized.",
              "definitions": {}, "input_dir_aliases": {"source": str(root)}, "file_mappings": mappings}
    from harmo.config.schema import validate_config
    from harmo.config.validator import validate_study
    errors = validate_config(config) + validate_study(config)
    if errors:
        for mapping in mappings:
            mapping["enabled"] = False
        for row in reports:
            if row["status"] in ("accepted", "partial"):
                row.update(status="needs_review", reason="config_validation_failed")
    save(output / "config.json", config)
    report = {"version": VERSION, "files": reports, "config_errors": errors,
              "counts": {state: sum(r["status"] == state for r in reports) for state in ("accepted", "partial", "needs_review", "unsupported")},
              "boundary": "Full-file structural trials and explicit-label interpretation only; no independent semantic validation, warehouse writes, or scientific qualification.",
              "limits": {"file_bytes": MAX_BYTES, "cells": MAX_CELLS, "worker_seconds": timeout,
                         "address_space_limit": "4 GiB on Linux; not enforced on macOS"}}
    report["review_items"] = [{"source": r["source"], "status": r["status"],
                               "reason": r.get("reason", "unspecified"),
                               "action": "Confirm source structure/metadata; keep this result and rerun to a new output directory."}
                              for r in reports if r["status"] != "accepted"]
    save(output / "report.json", report)
    return report


def worker(request_path):
    request = json.loads(Path(request_path).read_text())
    destination = Path(request["output"])
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        # Bound compressed-input expansion in addition to file/cell limits.
        if sys.platform.startswith("linux"):
            resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
        if Path(request["path"]).suffix.lower() in (".trc", ".mot", ".sto"):
            require(not request["metadata"], "motion_metadata_override_not_supported")
            from harmo.config.motion_tables import inspect_records
            result = inspect_records(request["path"], destination)
            save(destination / "result.json", result)
            return
        if Path(request["path"]).suffix.lower() == ".c3d":
            require(not request["metadata"], "c3d_metadata_override_not_supported")
            from harmo.config.c3d_records import inspect_records
            result = inspect_records(request["path"], destination)
            save(destination / "result.json", result)
            return
        try:
            result, time, values = inspect_file(request["path"], request["metadata"])
        except ValueError as exc:
            if str(exc) != "struct_array_requires_trial_selection" or request["metadata"]:
                raise
            from harmo.config.mat_records import inspect_records
            result = inspect_records(request["path"], destination)
            save(destination / "result.json", result)
            return
        import numpy as np
        with (destination / "converted.npz").open("xb") as stream:
            np.savez(stream, time=time, values=values)
        result["converted_content_sha256"] = digest(time.tobytes()+values.tobytes())
    except NotImplementedError as exc:
        result = {"status": "unsupported", "reason": str(exc)}
    except Exception as exc:
        result = {"status": "needs_review", "reason": type(exc).__name__+": "+str(exc)}
    save(destination / "result.json", result)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(sys.argv[2])
    else:
        raise SystemExit("Use harmo propose-mapping --help")
