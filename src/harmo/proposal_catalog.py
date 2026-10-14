"""Append checked proposal artifacts to a conservative, purpose-aware catalog."""
import json
import shutil
import sqlite3
import uuid
from pathlib import Path
import numpy as np
from harmo.config.automap import digest, require, save
from harmo.public_batch import catalog, contracts, sha
from harmo.public_export import load_record
from harmo.evidence.policy import ComparabilityPolicyEngine, ComparabilityRequest


def ingest(proposals, source_root, store, dataset):
    proposals, source_root, store = map(lambda p: Path(p).resolve(), (proposals, source_root, store))
    require(bool(dataset), "dataset_release_required")
    if store.exists():
        require((store / "proposal-store.json").is_file(), "not_a_proposal_store")
        require(json.loads((store / "proposal-store.json").read_text()) == {"version": 1}, "unsupported_store_version")
    else:
        store.mkdir(parents=True)
        save(store / "proposal-store.json", {"version": 1})
    report = json.loads((proposals / "report.json").read_text())
    require(not report["config_errors"], "invalid_proposal_config")
    db = sqlite3.connect(store / "catalog.sqlite")
    catalog(db)
    db.execute("CREATE TABLE IF NOT EXISTS proposal_holds(run_id TEXT, source TEXT, details TEXT)")
    run_id = uuid.uuid4().hex
    summary = dict(run_id=run_id, inserted=0, reused=0, held=[], records=[], pairwise_checks=0)
    try:
        for file_index, file in enumerate(report["files"]):
            try:
                source = (source_root / file["source"]).resolve()
                require(source_root in source.parents and source.is_file(), "unsafe_or_missing_source")
                require(sha(source) == file["source_sha256"], "source_checksum_mismatch")
                stored_source = store / "sources" / file["source_sha256"]
                if stored_source.exists():
                    require(sha(stored_source) == file["source_sha256"], "stored_source_changed")
                if file["status"] not in ("accepted", "partial"):
                    raise ValueError(file.get("reason", "proposal_not_accepted"))
                for record_index, entry in enumerate(file.get("records", [file])):
                    task = proposals / ("file-%04d" % file_index)
                    if "records" in file:
                        task = task / ("record-%04d" % record_index)
                    try:
                        require(entry["status"] == "accepted", "record_not_accepted")
                        require(entry.get("options", {}).get("static_metadata", {}).get("time_unit") == "s", "seconds_timebase_required")
                        sample = task / "converted.npz"
                        require(proposals in sample.resolve().parents, "unsafe_sample_path")
                        with np.load(sample, allow_pickle=False) as saved:
                            values, time = saved["values"], saved["time"]
                            require(values.dtype == np.float64 and values.ndim == 2, "float64_matrix_required")
                            require(values.shape == (entry.get("rows", entry.get("checks", {}).get("rows")), len(entry["channels"])), "shape_mismatch")
                            require(time.shape == (len(values),) and len(time) > 1 and np.isfinite(time).all() and (np.diff(time) > 0).all(), "invalid_time")
                            require(not np.isinf(values).any(), "infinite_signal")
                            expected = entry.get("values_sha256")
                            require(expected and digest(values.tobytes()) == expected, "missing_or_changed_value_hash")
                            if entry.get("time_sha256"):
                                require(digest(time.tobytes()) == entry["time_sha256"], "changed_time_hash")
                            else:
                                raise ValueError("explicit_time_hash_required")
                            rate = entry.get("sampling_rate_hz")
                            require(rate and np.allclose(time, time[0]+np.arange(len(time))/rate, rtol=1e-12, atol=1e-12), "regular_clock_contract_required")
                        identity = json.dumps([dataset, file["source"], file["source_sha256"], entry, sha(sample)], sort_keys=True)
                        record_id = digest(identity.encode())
                        existing = db.execute("SELECT location,name FROM records WHERE id=?", (record_id,)).fetchone()
                        if existing:
                            require(sha(Path(existing[0]) / "normalized.npz") == sha(sample), "stored_sample_changed")
                            summary["reused"] += 1
                            summary['records'].append(dict(id=record_id,name=existing[1],location=existing[0],status='converted',source=file['source']))
                            continue
                        dest = store / "records" / record_id
                        dest.mkdir(parents=True, exist_ok=False)
                        shutil.copyfile(sample, dest / "normalized.npz")
                        require(sha(dest / "normalized.npz") == sha(sample), "copy_checksum_mismatch")
                        # Keep original input bytes in a content-addressed store.
                        original = store / "sources" / file["source_sha256"]
                        original.parent.mkdir(exist_ok=True)
                        if not original.exists():
                            shutil.copyfile(source, original)
                        require(sha(original) == file["source_sha256"], "stored_source_changed")
                        meta = dict(rows=len(time), fs=rate, signals=[dict(label=c, unit="unknown") for c in entry["channels"]],
                            sha256=sha(dest / "normalized.npz"), source_sha256={original.name: file["source_sha256"]},
                            validation="Stored values and timestamps match proposal hashes; no independent rerun implied", proposal=entry)
                        contracts(record_id, dataset, meta, dest, original.parent.as_uri()+"/")
                        # The shared helper defaults to zero; retain the actual start.
                        bundle_path = dest / "recording.bundle.json"
                        bundle = json.loads(bundle_path.read_text())
                        bundle["timebase"]["start_seconds"] = float(time[0])
                        # Generated artifact update, not a source or user document edit.
                        bundle_path.write_text(json.dumps(bundle, indent=2))
                        save(dest / "proposal.json", entry)
                        name = file["source"]+"::"+entry.get("data_key", entry.get("stream", str(record_index)))
                        save(dest/'record.json',dict(id=record_id,name=name,metadata=meta,scientific_use='unassessed'))
                        db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)", (record_id,dataset,name,"converted-not-qualified",str(dest),json.dumps(meta)))
                        db.execute("INSERT OR IGNORE INTO assets VALUES(?,?,?,?)", (dataset,file["source"],file["source_sha256"],str(original)))
                        for purpose in ("activation-timing", "amplitude-pooling"):
                            db.execute("INSERT INTO assessments VALUES(?,?,?,?)", (record_id,purpose,"unassessed",json.dumps(["semantic and processing contracts incomplete"])))
                        db.commit()
                        summary["inserted"] += 1
                        summary['records'].append(dict(id=record_id,name=name,location=str(dest),status='converted',source=file['source']))
                    except (ValueError, OSError, KeyError) as exc:
                        db.rollback()
                        summary["held"].append(dict(source=file["source"], record=record_index, reason=str(exc)))
                summary["held"].extend(dict(source=file["source"], **row) for row in file.get("held", []))
            except (ValueError, OSError, KeyError) as exc:
                summary["held"].append(dict(source=file.get("source"), reason=str(exc)))
        for row in summary["held"]:
            db.execute("INSERT INTO proposal_holds VALUES(?,?,?)", (run_id,row.get("source"),json.dumps(row)))
        db.execute("INSERT INTO runs VALUES(?,?)", (run_id,json.dumps(summary)))
        db.commit()
        save(store / ("run-"+run_id+".json"), summary)
        return summary
    finally:
        db.close()


def compare(store, left_id, right_id, channel):
    """Explicit pair/channel request using the existing scientific policy engine."""
    db = sqlite3.connect("file:"+str(Path(store).resolve()/"catalog.sqlite")+"?mode=ro", uri=True)
    try:
        pairs = []
        for rid in (left_id, right_id):
            row = db.execute("SELECT id,dataset,name,location FROM records WHERE id=?", (rid,)).fetchone()
            require(row is not None, "record_not_found")
            pairs.append(load_record(row))
        left, lt, _ = pairs[0]
        right, rt, _ = pairs[1]
        lc = next(c.channel_id for c in left.channels if c.name == channel)
        rc = next(c.channel_id for c in right.channels if c.name == channel)
        engine = ComparabilityPolicyEngine()
        return {purpose: engine.evaluate_with_details(left,right,lt,rt,ComparabilityRequest("catalog-"+purpose,purpose,lc,rc))
                for purpose in ("activation-timing", "amplitude-pooling")}
    finally:
        db.close()
