"""Purpose-specific catalog selection with conservative export gates.

This exports a collection of separately identified records, not pooled arrays.
Stored comparison decisions are never trusted without re-evaluation.
"""
import json
import sqlite3
from pathlib import Path

from harmo.public_batch import sha, write_json
from harmo.evidence.models import HarmoRecordingBundle, TrustReport
from harmo.evidence.policy import ComparabilityPolicyEngine, ComparabilityRequest, TRUST_DIMENSIONS

VERSION = "public-export/0.1.0"


def load_record(row):
    record_id, dataset, name, location = row
    directory = Path(location)
    semantic = directory / "recording.semantic.bundle.json"
    if (directory / "recording.semantic.v002.bundle.json").exists():
        semantic = directory / "recording.semantic.v002.bundle.json"
    bundle = HarmoRecordingBundle.from_dict(json.loads((semantic if semantic.exists() else directory / "recording.bundle.json").read_text()))
    trust = TrustReport.from_dict(json.loads((directory / "trust.report.json").read_text()))
    if bundle.recording_id != record_id or trust.subject_id != record_id:
        raise ValueError("catalog, bundle and trust identities differ")
    from harmo.contract_revision import load
    bundle,trust=load(directory,bundle,trust)
    return bundle, trust, directory


def checked_samples(bundle, directory):
    import numpy as np
    path = directory / "normalized.npz"
    if bundle.samples.format != "npz" or Path(bundle.samples.uri).resolve() != path.resolve():
        raise ValueError("export currently requires the catalog's normalized NPZ")
    if not bundle.samples.checksum or sha(path) != bundle.samples.checksum:
        raise ValueError("sample checksum mismatch or absent")
    with np.load(path, allow_pickle=False) as data:
        values, time = data["values"].copy(), data["time"].copy()
    if list(values.shape) != bundle.samples.shape or values.shape[1] != len(bundle.channels):
        raise ValueError("sample shape differs from contract")
    if time.shape != (values.shape[0],) or not np.isfinite(time).all() or not (np.diff(time) > 0).all():
        raise ValueError("invalid timestamps")
    if bundle.timebase.sampling_rate_hz is not None:
        expected = bundle.timebase.start_seconds + np.arange(len(time))/bundle.timebase.sampling_rate_hz
        if not np.allclose(time, expected, rtol=1e-12, atol=1e-12):
            raise ValueError("timestamps differ from regular timebase contract")
    if not np.isfinite(values).all():
        raise ValueError("nonfinite samples require an explicit handling protocol")
    return time, values


def export_catalog(catalog_path, request, output, *, candidate_ids=None, documentary_checks=None, documentary_inputs=None, unit_review=None):
    """Read-only catalog query. Every purpose requires its own pairwise gate."""
    import numpy as np
    allowed = {"reference", "purposes"}
    if not isinstance(request, dict) or set(request) != allowed or not isinstance(request["reference"], dict) or set(request["reference"]) != {"dataset", "name", "channel"}:
        raise ValueError("request requires reference {dataset,name,channel} and purposes")
    purposes = request["purposes"]
    if not isinstance(purposes, list) or not purposes or any(not isinstance(p, str) for p in purposes) or len(purposes) != len(set(purposes)) or any(
        p not in ("activation-timing", "amplitude-pooling") for p in purposes
    ):
        raise ValueError("unique activation-timing and/or amplitude-pooling purposes required")
    path = Path(catalog_path).resolve()
    from harmo.unit_review_store import load_review
    persisted_unit_review = load_review(path)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT id,dataset,name,location FROM records ORDER BY id").fetchall()
    finally:
        db.close()
    ref = request["reference"]
    if any(not isinstance(v, str) or not v for v in ref.values()):
        raise ValueError("reference fields must be nonempty strings")
    references = [r for r in rows if r[1:3] == (ref["dataset"], ref["name"])]
    if len(references) != 1:
        raise ValueError("reference must resolve to exactly one catalog record version")
    left, lt, ld = load_record(references[0])
    if documentary_inputs is not None:
        from harmo.document_contract import resolve
        from harmo.document_evidence import check_record
        # Recompute against the loaded contract, never accept a stored resolved view.
        documentary_checks=dict(documentary_checks or {})
        observations=documentary_inputs.get(left.recording_id,[])
        documentary_checks[left.recording_id]=check_record(observations,left,references[0][2])
        left,_=resolve(left,references[0][2],observations)
    channels = [c for c in left.channels if c.name == ref["channel"]]
    if len(channels) != 1:
        raise ValueError("reference channel must resolve exactly once")
    reference_channel = channels[0]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = dict(version=VERSION, request=request, entries=[], exported=0,
                  scope="Pairwise-compatible record collection; no cross-record averaging or automatic transformation.")
    engine = ComparabilityPolicyEngine()
    for row in rows:
        if row[0] == left.recording_id:
            continue
        if candidate_ids is not None and row[0] not in candidate_ids:
            continue
        try:
            right, rt, directory = load_record(row)
            if documentary_inputs is not None:
                observations=documentary_inputs.get(right.recording_id,[])
                documentary_checks[right.recording_id]=check_record(observations,right,row[2])
                right,_=resolve(right,row[2],observations)
            # Broad candidate enumeration is intentional: the policy, not a
            # lexical alias or same label, decides scientific compatibility.
            for channel in right.channels:
                if "emg" not in channel.name.lower() and channel.sensor_type not in ("surface-emg", "needle-emg"):
                    continue
                for purpose in purposes:
                    result = engine.evaluate_with_details(left, right, lt, rt, ComparabilityRequest(
                        reference_channel.name + "." + purpose, purpose, reference_channel.channel_id, channel.channel_id))
                    entry = dict(record_id=row[0], channel=channel.channel_id, purpose=purpose,
                                 decision=result, status="held", reasons=[])
                    screen_path = directory / "source.screen.json"
                    if screen_path.exists():
                        screen = json.loads(screen_path.read_text())
                        entry["source_screen"] = dict(path=str(screen_path), sha256=sha(screen_path),
                                                      issues=screen.get("issues", []), coverage=screen.get("coverage", {}))
                    report["entries"].append(entry)
                    if unit_review is not None or persisted_unit_review is not None:
                        from harmo.catalog_unit_review import amplitude_holds
                        unit_issues=[]
                        for review in (unit_review,persisted_unit_review):
                            for b,c in ((left,reference_channel),(right,channel)):
                                unit_issues.extend(amplitude_holds(review,b.recording_id,c.channel_id,b.samples.checksum))
                        unit_issues=list(dict.fromkeys(unit_issues))
                        entry['unit_review_issues']=unit_issues
                        if purpose=='amplitude-pooling' and unit_issues:
                            entry['reasons'].extend(unit_issues)
                            continue
                    # Empty channel denotes a record-wide fact (e.g. task).
                    # Otherwise constrain only the explicitly named channel.
                    source_conflicts = []
                    for record_id, selected_channel in ((left.recording_id, reference_channel), (right.recording_id, channel)):
                        check = (documentary_checks or {}).get(record_id, {})
                        for conflict in check.get('conflicts', []):
                            if conflict['channel'] in ('', selected_channel.name):
                                source_conflicts.append(dict(record_id=record_id, **conflict))
                    if source_conflicts:
                        entry['documentary_conflicts'] = source_conflicts
                        entry['reasons'].append('applicable_documentary_contract_conflict')
                        continue
                    if any(b.schema_version != "1.2.0" or b.task is None for b in (left, right)):
                        entry["reasons"].append("first_class_task_and_anatomy_contracts_required")
                        continue
                    if result["assertion"]["status"] != "comparable":
                        entry["reasons"].append("pairwise_policy_does_not_allow_unconditional_export")
                        continue
                    if entry.get("source_screen", {}).get("issues"):
                        entry["reasons"].append("unresolved_source_screen_issues")
                        continue
                    reference_screen = ld / "source.screen.json"
                    if reference_screen.exists() and json.loads(reference_screen.read_text()).get("issues"):
                        entry["reasons"].append("unresolved_reference_source_screen_issues")
                        continue
                    if any(t.dimension_status(d).value != "pass" for t in (lt, rt) for d in TRUST_DIMENSIONS):
                        entry["reasons"].append("all_trust_dimensions_must_pass")
                        continue
                    try:
                        ltime, lvalues = checked_samples(left, ld)
                        rtime, rvalues = checked_samples(right, directory)
                        target = output / ("pair-%05d.npz" % len(report["entries"]))
                        staging = target.with_suffix(".npz.partial")
                        li = left.channels.index(reference_channel)
                        ri = right.channels.index(channel)
                        with staging.open("xb") as f:
                            np.savez(f, reference_time=ltime, reference_values=lvalues[:,li],
                                     target_time=rtime, target_values=rvalues[:,ri])
                        with np.load(staging, allow_pickle=False) as saved:
                            np.testing.assert_array_equal(saved["target_values"], rvalues[:,ri])
                            np.testing.assert_array_equal(saved["reference_values"], lvalues[:,li])
                        staging.rename(target)
                        entry.update(status="exported", file=target.name, sha256=sha(target),
                                     reference_record_id=left.recording_id,
                                     reference_channel_id=reference_channel.channel_id,
                                     units=[reference_channel.unit,channel.unit],
                                     input_sha256=[left.samples.checksum,right.samples.checksum])
                        entry["input_contracts"] = dict(reference=left.to_dict(), target=right.to_dict(),
                                                       reference_trust=lt.to_dict(), target_trust=rt.to_dict())
                        report["exported"] += 1
                    except (ValueError, OSError, KeyError, AssertionError) as exc:
                        entry["reasons"].append(str(exc))
        except (ValueError, OSError, KeyError) as exc:
            report["entries"].append(dict(record_id=row[0],status="held",reasons=[str(exc)]))
    report["status"] = "exported" if report["exported"] else "no_eligible_pairs"
    write_json(output / "selection.report.json", report)
    return report
