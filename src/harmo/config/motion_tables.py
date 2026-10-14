"""Strict structural proposals for declared TRC and MOT/STO time series."""
from pathlib import Path
from decimal import Decimal
import numpy as np
from harmo.config.automap import MAX_BYTES, MAX_CELLS, require, digest, save
from harmo.ingest.readers.sto import read_opensim_sto_trc


def inspect_records(path, output):
    path, output = Path(path), Path(output)
    require(path.stat().st_size <= MAX_BYTES, "file_size_limit")
    raw = path.read_bytes()
    lines = raw.decode("utf-8").splitlines()
    require(len(lines) > 6, "incomplete_motion_table")
    auxiliary = {}
    if path.suffix.lower() == ".trc":
        require(lines[0].startswith("PathFileType\t"), "trc_signature_required")
        keys, vals = lines[1].split("\t"), lines[2].split("\t")
        require(len(keys) == len(vals) and len(keys) == len(set(keys)), "invalid_metadata_header")
        metadata = dict(zip(keys, vals))
        count = int(metadata["NumMarkers"])
        require(0 < count <= MAX_CELLS // 3, "marker_count_limit")
        head = lines[3].split("\t")
        require(head[:2] in (["Frame#", "Time"], ["Frame#", "Times"]), "explicit_time_column_required")
        markers = [v.strip() for v in head[2:] if v.strip()]
        require(len(markers) == count and len(set(markers)) == count, "invalid_marker_labels")
        axes = [v.strip() for v in lines[4].split("\t")[2:] if v.strip()]
        require(axes == [axis + str(i+1) for i in range(count) for axis in ("X", "Y", "Z")], "axis_layout_requires_review")
        labels = ["frame_num", "time"] + [m + "_" + a for m in markers for a in ("X", "Y", "Z")]
        rows = []
        for line in lines[5:]:
            if not line.strip():
                continue
            row = line.split("\t")
            require(all(not v.strip() for v in row[len(labels):]), "unexpected_extra_values")
            require(len(row) >= len(labels), "missing_columns")
            rows.append(row[:len(labels)])
        require(len(rows) == int(metadata["NumFrames"]), "frame_count_conflict")
        unit = "s"
        time_basis = "TRC Time column and DataRate convention; source time origin retained"
    else:
        end = [i for i, line in enumerate(lines) if line.strip() == "endheader"]
        require(len(end) == 1, "unique_endheader_required")
        metadata = {}
        for line in lines[:end[0]]:
            if "=" in line:
                key, value = [v.strip() for v in line.split("=", 1)]
                require(key.lower() not in metadata, "duplicate_metadata")
                metadata[key.lower()] = value
        body = [line for line in lines[end[0]+1:] if line.strip()]
        labels = body[0].lower().split()
        require(labels.count("time") == 1 and len(labels) == len(set(labels)), "invalid_time_or_duplicate_labels")
        unit = metadata.get("time_units", metadata.get("timeunit"))
        require(unit in ("s", "ms"), "explicit_time_unit_required; normalized axes are not seconds")
        if "time_units" in metadata and "timeunit" in metadata:
            require(metadata["time_units"] == metadata["timeunit"], "time_unit_conflict")
        rows = [line.split() for line in body[1:]]
        require(all(len(row) == len(labels) for row in rows), "row_width_conflict")
        for key, actual in (("nrows", len(rows)), ("ncolumns", len(labels)), ("datarows", len(rows)), ("datacolumns", len(labels))):
            if key in metadata:
                require(int(metadata[key]) == actual, "declared_shape_conflict")
        time_basis = "explicit time-unit header; no inference from row count or range"
    require(1 < len(rows) and len(rows)*len(labels) <= MAX_CELLS, "cell_count_limit")
    def number(token):
        if not token.strip() or token.lower() in ("nan", "na"):
            return np.nan
        decimal = Decimal(token)
        require(not decimal.is_finite() or abs(decimal) <= 2**53 or decimal != decimal.to_integral_value(), "integer_precision_loss")
        return float(token)
    expected = np.array([[number(v) for v in row] for row in rows])
    time = expected[:, labels.index("time")]
    require(np.isfinite(time).all() and (np.diff(time) > 0).all(), "invalid_time_axis")
    require(not np.isinf(expected).any(), "infinite_value")
    if path.suffix.lower() == ".trc":
        frames = expected[:, 0]
        require(np.isfinite(frames).all() and (frames == np.floor(frames)).all() and (np.diff(frames) == 1).all(), "invalid_frame_sequence")
        rate = float(metadata["DataRate"])
        require(np.isfinite(rate) and rate > 0, "invalid_rate")
        # Permit export rounding up to 0.1% of one frame, never rewrite times.
        require((np.abs(time-time[0]-(frames-frames[0])/rate) <= 0.001/rate + 1e-12).all(), "time_rate_conflict")
        auxiliary["frame_num"] = frames
    _, frame = read_opensim_sto_trc(str(path), round_trip=True)
    require(list(frame.columns) == labels, "reader_changed_labels")
    np.testing.assert_array_equal(frame.to_numpy(dtype=float), expected)
    channels = [label for label in labels if label not in ("time", "frame_num")]
    values = expected[:, [labels.index(label) for label in channels]]
    directory = output / "record-0000"
    directory.mkdir()
    with (directory / "converted.npz").open("xb") as stream:
        np.savez(stream, time=time, values=values, **auxiliary)
    with np.load(directory / "converted.npz", allow_pickle=False) as restored:
        np.testing.assert_array_equal(restored["values"], values)
        np.testing.assert_array_equal(restored["time"], time)
    entry = dict(status="accepted", stream=path.suffix[1:].upper(), rows=len(time), channels=channels,
        source_metadata=metadata, time_basis=time_basis, time_unit=unit,
        coordinate_system="not_qualified", scientific_use="not_evaluated", trc_clock_tolerance_frames=0.001,
        options={"reader_options": {"round_trip": True}, "static_metadata": {"time_unit": unit}}, values_sha256=digest(values.tobytes()))
    save(directory / "result.json", entry)
    require(digest(path.read_bytes()) == digest(raw), "source_changed_during_read")
    return dict(status="accepted", records=[entry], held=[], source_sha256=digest(raw), scientific_use="not_evaluated")
