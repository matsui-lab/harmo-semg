"""Keep C3D streams separate and generate explicit native-reading proposals."""
from pathlib import Path
import numpy as np
from harmo.config.automap import MAX_BYTES, MAX_CELLS, digest, require, save
from harmo.ingest.readers.c3d import read_c3d_file


def inspect_records(path, output):
    import ezc3d
    path, output = Path(path), Path(output)
    require(path.stat().st_size <= MAX_BYTES, "file_size_limit")
    original = path.read_bytes()
    data = ezc3d.c3d(str(path))
    records, held = [], []
    require(sum(data["data"][k].size for k in ("points", "analogs")) <= MAX_CELLS, "cell_count_limit")
    for section, key, mode in (("POINT", "points", "point"), ("ANALOG", "analogs", "analog-native")):
        array = data["data"][key]
        if not array.size:
            continue
        try:
            parameters = data["parameters"][section]
            raw_labels = [str(v) for v in parameters["LABELS"]["value"]]
            require(array.ndim == 3 and len(raw_labels) == array.shape[1], "labels_shape_conflict")
            rate_values = parameters["RATE"]["value"]
            require(len(rate_values) == 1, "ambiguous_rate")
            rate = float(rate_values[0])
            require(np.isfinite(rate) and rate > 0, "invalid_sampling_rate")
            header_rate = float(data["header"][key]["frame_rate"])
            require(np.isclose(rate, header_rate, rtol=1e-7, atol=0), "header_parameter_rate_conflict")
            units = [str(v).strip() for v in parameters.get("UNITS", {}).get("value", [])]
            if section == "POINT":
                require(array.shape[0] == 4, "unsupported_point_shape")
                labels = [v.strip().rsplit(":", 1)[-1] for v in raw_labels]
                channels = [label + "_" + axis for label in labels for axis in ("x", "y", "z")]
                values = array[:3].transpose(2, 1, 0).reshape(array.shape[2], -1)
            else:
                require(array.shape[0] == 1, "unsupported_analog_shape")
                labels = [v.strip() for v in raw_labels]
                channels = labels
                values = array[0].T
            require(all(labels) and len(set(labels)) == len(labels) and "time" not in channels, "empty_duplicate_or_reserved_label")
            require(len(values) > 1 and not np.isinf(values).any(), "invalid_signal")
            time = np.arange(len(values)) / rate
            frame = read_c3d_file(str(path), data_source=mode)
            require(list(frame.columns) == channels + ["time"], "reader_labels_changed")
            np.testing.assert_array_equal(frame[channels].to_numpy(), values)
            np.testing.assert_array_equal(frame["time"].to_numpy(), time)
            directory = output / ("record-%04d" % len(records))
            directory.mkdir()
            auxiliary = {"point_fourth_component": array[3]} if section == "POINT" else {}
            with (directory / "converted.npz").open("xb") as stream:
                np.savez(stream, time=time, values=values, **auxiliary)
            with np.load(directory / "converted.npz", allow_pickle=False) as restored:
                np.testing.assert_array_equal(restored["values"], values)
                np.testing.assert_array_equal(restored["time"], time)
            entry = dict(status="accepted", stream=section, channels=channels, source_labels=raw_labels,
                rows=len(time), sampling_rate_hz=rate, declared_units=units,
                unit_status="declared" if len(units) == (1 if section == "POINT" else len(labels)) and all(units) else "unresolved",
                coordinate_system="not_qualified", scientific_use="not_evaluated",
                time_basis="relative stream clock; no source-origin or cross-stream synchronization claim",
                processing="decoder-scaled values; no additional calibration, filtering, rotation or resampling",
                point_fourth_component_retained=section == "POINT",
                options={"c3d_data_source": mode, "static_metadata": {"time_unit": "s"}},
                values_sha256=digest(values.tobytes()), time_sha256=digest(time.tobytes()))
            save(directory / "result.json", entry)
            records.append(entry)
        except (ValueError, AssertionError, KeyError) as exc:
            held.append(dict(stream=section, reason=str(exc)))
    require(digest(path.read_bytes()) == digest(original), "source_changed_during_read")
    return dict(status="partial" if records and held else "accepted" if records else "needs_review",
        source_sha256=digest(original), records=records, held=held,
        reason="stream_requires_review" if held else None, scientific_use="not_evaluated",
        scope="POINT and ANALOG values only; not all C3D metadata or derived mechanics")
