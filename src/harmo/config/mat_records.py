"""Bounded record-wise MAT proposals using an explicit, registered EMG layout.

The EMGFreq/EmgVarName convention is inherited from the existing study_38
adapter, not learned from trial values. Other layouts remain review items.
"""
import re
from pathlib import Path

import numpy as np
from scipy.io import loadmat

from harmo.config.automap import MAX_BYTES, MAX_CELLS, digest, require, save
from harmo.ingest.readers.mat import _array_to_dataframe


PROFILE = "physiology-parent-metadata/1.1"
# Reviewed layout conventions from the existing study_38 conversion adapter.
SIGNALS = {"EMG": ("EmgVarName", "EMGFreq", "TimeStampEmg"),
           "Marker": ("MarkerVarName", "KinFreq", "TimeStampKin"),
           "Grf": ("GrfVarName", "GrfFreq", "TimeStampGrf")}


def inspect_records(path, output):
    path, output = Path(path), Path(output)
    require(path.stat().st_size <= MAX_BYTES, "file_size_limit")
    original = path.read_bytes()
    data = loadmat(path, squeeze_me=False)
    records, held, arrays = [], [], []
    visited = 0
    total_cells = {signal: 0 for signal in SIGNALS}

    def visit(value, key, ancestors, depth=0):
        nonlocal visited, total_cells
        visited += 1
        require(visited <= 10000 and depth <= 8, "record_structure_limit")
        if not isinstance(value, np.ndarray) or not value.dtype.names:
            return
        require(value.ndim == 2 and value.size <= 1000, "record_array_limit")
        for index in range(value.size):
            item = value.flat[index]
            location = key if value.shape == (1, 1) else key + "." + str(index)
            fields = {name: item[name] for name in value.dtype.names}
            require(all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in fields), "unsupported_mat_key")
            scopes = [(location, fields)] + ancestors
            for name, array in fields.items():
                if isinstance(array, np.ndarray) and array.dtype.names:
                    visit(array, location + "." + name, scopes, depth + 1)
                elif isinstance(array, np.ndarray) and array.dtype.kind == "O":
                    held.append(dict(data_key=location + "." + name, reason="cell_or_object_requires_review"))
                elif isinstance(array, np.ndarray) and array.dtype.kind in "fiu" and array.size > 1:
                    source_key = location + "." + name
                    # Inventory all numeric arrays, including unsupported modalities.
                    arrays.append(dict(data_key=source_key, shape=list(array.shape)))
                    if name not in SIGNALS:
                        reason = "axis_semantics_require_evidence; no sampling-rate inheritance" if name in ("Ang", "Mom", "Pwr", "Com") else "no_registered_signal_profile"
                        held.append(dict(data_key=source_key, reason=reason))
                        continue
                    try:
                        label_key, rate_key, timestamp_key = SIGNALS[name]
                        choices = []
                        for scope, attributes in scopes:
                            # Any competing local declaration blocks parent inheritance.
                            if any(k in attributes for k in (rate_key, label_key)):
                                require(all(k in attributes for k in (rate_key, label_key)), "incomplete_local_metadata")
                                choices.append((scope, attributes))
                        require(len(choices) == 1, "missing_or_competing_metadata_scope")
                        scope, attributes = choices[0]
                        labels = attributes[label_key]
                        require(isinstance(labels, np.ndarray) and labels.dtype.kind in "US", "explicit_text_labels_required")
                        channels = [str(v).strip() for v in labels.flat]
                        require(channels and all(channels) and len(set(channels)) == len(channels) and "time" not in channels, "invalid_labels")
                        rate = attributes[rate_key]
                        require(isinstance(rate, np.ndarray) and rate.dtype.kind in "fiu" and rate.size == 1,
                                "invalid_sampling_rate")
                        rate = float(rate.item())
                        require(np.isfinite(rate) and rate > 0, "invalid_sampling_rate")
                        require(array.ndim == 2, "signal_must_be_matrix")
                        orientations = [t for t in (False, True) if (array.T if t else array).shape[1] == len(channels)]
                        require(len(orientations) == 1, "ambiguous_array_orientation")
                        values = (array.T if orientations[0] else array)
                        if values.dtype.kind in "iu":
                            require(all(abs(int(v)) <= 2**53 for v in values.flat), "integer_precision_loss")
                        values = values.astype(float)
                        require(len(values) > 1, "single_sample_requires_non_temporal_representation")
                        require(not np.isinf(values).any(), "invalid_signal")
                        total_cells[name] += values.size
                        require(total_cells[name] <= MAX_CELLS, "per_signal_cell_count_limit")
                        options = dict(data_key=source_key, channel_names_key=scope + "." + label_key,
                                       fs_key=scope + "." + rate_key, transpose_data=orientations[0], strip_channel_names=True)
                        frame = _array_to_dataframe(array, options, data)
                        time = np.arange(len(values)) / rate
                        np.testing.assert_array_equal(frame[channels].to_numpy(), values)
                        np.testing.assert_array_equal(frame["time"].to_numpy(), time)
                        entry = dict(record_key=location, data_key=source_key, channels=channels, signal_type=name,
                            rows=len(values), sampling_rate_hz=rate,
                            options=dict(reader_options=options, static_metadata={"time_unit": "s"}),
                            profile=PROFILE, metadata_scope=scope,
                            rate_basis="registered layout convention: " + rate_key + " is Hz; inherited from study_38 adapter",
                            axis_kind="relative_sample_time", signal_units="not_qualified", coordinate_system="not_qualified",
                            time_basis="relative sample clock; source timestamp not applied; no cross-stream synchronization asserted",
                            scientific_use="not_evaluated", missing_cells=int(np.isnan(values).sum()),
                            source_context={k: v.tolist() for k, v in fields.items()
                                if k in ("Task", "Foot", timestamp_key) and isinstance(v, np.ndarray)
                                and v.size <= 16 and v.dtype.kind in "USfiu" and (v.dtype.kind in "US" or np.isfinite(v).all())},
                            values_sha256=digest(values.tobytes()), time_sha256=digest(time.tobytes()),
                            status="accepted")
                        record_dir = output / ("record-%04d" % len(records))
                        record_dir.mkdir()
                        with (record_dir / "converted.npz").open("xb") as stream:
                            np.savez(stream, time=time, values=values)
                        with np.load(record_dir / "converted.npz", allow_pickle=False) as restored:
                            np.testing.assert_array_equal(restored["values"], values)
                            np.testing.assert_array_equal(restored["time"], time)
                        save(record_dir / "result.json", entry)
                        records.append(entry)
                    except (ValueError, TypeError, AssertionError) as exc:
                        held.append(dict(data_key=source_key, reason=str(exc)))

    for key, value in data.items():
        if not key.startswith("__"):
            require(bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key)), "unsupported_mat_key")
            visit(value, key, [])
    require(digest(path.read_bytes()) == digest(original), "source_changed_during_read")
    return dict(status="partial" if records and held else "accepted" if records else "needs_review",
        reason="unhandled_record_fields" if held else "no_registered_record_layout" if not records else None,
        source_sha256=digest(original), records=records, held=held, numeric_array_inventory=arrays,
        profile=PROFILE, scientific_use="not_evaluated", checked_signal_cells=sum(r["rows"]*len(r["channels"]) for r in records))
