"""Connect a registered timing protocol to automatically catalogued raw cycles.

This is a bounded executable profile, not a general publication interpreter.
Original contracts are never promoted in place. Release remains a separate gate.
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
from scipy.io import loadmat

from harmo.config.automap import require, save
from harmo.evidence.io import sha256_file
from harmo.evidence.models import ReleaseDecision
from harmo.physiology_control import (
    load_physiology_protocol, preprocess_study38_cycle,
    extract_study38_rows, extract_study120_rows, evaluate_physiology_control,
)
from harmo.public_export import load_record, checked_samples

PROTOCOL_SHA256 = "9682b70ae5a1fec55e247ac74f9c4249e58a1249f764e3ba2dfb4ead57771267"


def checked_profile(path):
    protocol = load_physiology_protocol(Path(path))
    require(protocol["protocol_sha256"] == PROTOCOL_SHA256, "unregistered_timing_protocol")
    return protocol


def bind_catalog(store, source_root, protocol):
    """Select by protocol fields, then prove stored samples equal source cycles."""
    store, source_root = Path(store).resolve(), Path(source_root).resolve()
    db = sqlite3.connect((store / "catalog.sqlite").as_uri() + "?mode=ro", uri=True)
    try:
        records = db.execute("SELECT id,dataset,name,location FROM records ORDER BY id").fetchall()
    finally:
        db.close()
    from harmo.catalog_evidence import DATASET
    by_name = {}
    for row in records:
        if row[1] == DATASET:
            by_name.setdefault(row[2], []).append(row)
    bindings, curves = [], []
    for subject in protocol["required_studies"]["study_38"]["subjects"]:
        name = "Subject%s.mat" % subject
        path = source_root / name
        source_hash = sha256_file(str(path))
        source = loadmat(path, squeeze_me=True, struct_as_record=False)["s"]
        side = str(source.EMGSide).strip()
        # The frozen cross-study profile compares right-side curves only.
        require(side == "RX", "profile_requires_right_emg")
        labels = [str(v).strip() for v in np.atleast_1d(source.EmgVarName)]
        selected = [(i, trial) for i, trial in enumerate(np.atleast_1d(source.Data).ravel())
                    if str(trial.Task).strip() == "Walking" and str(trial.Foot).strip() == side][:5]
        require(len(selected) == 5, "profile_cycle_count_not_met")
        by_muscle = {muscle: [] for muscle in protocol["muscles"]}
        for index, trial in selected:
            key = name + "::s.Data.%d.EMG" % index
            matches = by_name.get(key, [])
            require(len(matches) == 1, "missing_or_ambiguous_catalog_cycle:" + key)
            row = matches[0]
            require(store in Path(row[3]).resolve().parents, "catalog_path_outside_store")
            bundle, trust, directory = load_record(row)
            _, values = checked_samples(bundle, directory)
            require([c.name for c in bundle.channels] == labels, "catalog_channel_order_changed")
            require(any(s.sha256 == source_hash for s in bundle.sources), "catalog_source_identity_changed")
            require(bundle.task is not None and bundle.task.activity == "Walking", "catalog_task_not_bound")
            require(bundle.task.facets.get("reference_foot") == side, "catalog_reference_foot_conflict")
            require(all(c.side == "right" and c.unit == "mV" and c.sensor_type == "surface-emg"
                        for c in bundle.channels), "catalog_semantics_not_bound")
            require(bundle.timebase.sampling_rate_hz == float(source.EMGFreq), "catalog_rate_conflict")
            require(np.array_equal(values, np.asarray(trial.EMG, dtype=float).T), "catalog_values_differ_from_source")
            bindings.append(dict(record_id=row[0], subject=str(subject), source=name,
                source_sha256=source_hash, data_key="s.Data.%d.EMG" % index,
                sample_sha256=bundle.samples.checksum, channels=labels,
                cycle_rule="first five source-ordered Walking cycles with Foot=EMGSide=RX"))
            for muscle, spec in protocol["muscles"].items():
                require(labels.count(spec["study_38_label"]) == 1, "ambiguous_muscle_label")
                ch = labels.index(spec["study_38_label"])
                by_muscle[muscle].append(preprocess_study38_cycle(values[:, ch],
                    float(source.EMGFreq), protocol["study_38_envelope"]))
        for muscle, cycles in by_muscle.items():
            curves.append(dict(subject_id=str(subject), muscle=muscle,
                               values=np.median(np.vstack(cycles), axis=0).tolist()))
    return dict(bindings=bindings, curves=curves, selected_cycles=len(bindings),
                trust_boundary="Original catalog trust is unchanged; derived timing only.")


def verify_catalog_curves(binding, extracted):
    expected = {(str(row["subject_id"]), row["muscle"]): row["values"] for row in extracted}
    require(len(binding["curves"]) == len(expected) == 20, "curve_denominator_mismatch")
    for row in binding["curves"]:
        require(np.array_equal(row["values"], expected[(row["subject_id"], row["muscle"])]),
                "catalog_and_registered_processing_differ")


def execute(store, source_root, protocol_path, output):
    """Recompute the control, resolve timing, retain decisions and gated outputs."""
    from harmo.registered_timing import run
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    protocol_path = Path(protocol_path).resolve()
    protocol = checked_profile(protocol_path)
    output.mkdir(parents=True, exist_ok=False)
    try:
        print("timing: checking source assets and catalog cycles", flush=True)
        # Registered extractors attest original size/checksum before processing.
        rows38, sources38 = extract_study38_rows(source_root, protocol)
        binding = bind_catalog(store, source_root, protocol)
        verify_catalog_curves(binding, rows38)
        save(output / "catalog.binding.json", binding)
        rows120, sources120 = extract_study120_rows(source_root / "Gait120_011_to_020.zip", protocol)
        control = evaluate_physiology_control(rows38 + rows120, protocol, sources38 + sources120)
        save(output / "physiology.result.json", control)
        require(control["positive_control_pass"] is True, "fresh_physiological_control_failed")
        print("timing: fresh control passed; resolving the registered 20 comparisons twice", flush=True)
        result = run(source_root, source_root / "Gait120_011_to_020.zip", output / "resolution",
                     protocol_path, output / "physiology.result.json", output / "resolution.result.json")
        require(result["all_timing_pairs_resolved_comparable"] and
                result["recording_identities_and_decisions_reproducible"], "timing_resolution_not_reproduced")
        selections = []
        for index in sorted((output / "resolution" / "repeat-1").glob("pair-*/*/resolved/comparability.resolution.index.json")):
            item = json.loads(index.read_text())
            # Never export internal arrays merely because comparison passed.
            releases = {role: json.loads((index.parent / role / "release.decision.json").read_text())
                        for role in ("left", "right")}
            permitted = all(ReleaseDecision.from_dict(r).status.value == "allow" for r in releases.values())
            signal_export = None
            if permitted:
                from harmo.timing_export import export_pair
                signal_export = export_pair(index.parent, index.parent / "selected-signals")
            selections.append(dict(comparison=str(index.relative_to(output)), purpose="activation-timing",
                comparison_status=item["resolved_status"], status="released" if permitted else "held",
                releases=releases, export_directories=[str((index.parent / role / "exchange").relative_to(output))
                    for role in ("left", "right")] if permitted else [],
                signal_export=signal_export, amplitude="not-established"))
        require(len(selections) == 20, "selection_denominator_mismatch")
        save(output / "selection.json", selections)
        summary = dict(profile="registered-study38-gait120-timing/1.0", selected_catalog_cycles=binding["selected_cycles"],
            catalog_subject_curves_verified=20, physiological_control_pass=True, comparisons=20,
            timing_comparable=20, released=sum(r["status"] == "released" for r in selections),
            held=sum(r["status"] == "held" for r in selections), amplitude_comparability="not-established",
            reproducible=True, scope="Two registered studies, two muscles, prescribed gait cycles; no new-study scientific approval.",
            status="completed" if all(r["status"] == "released" for r in selections) else "completed-with-release-holds")
        save(output / "summary.json", summary)
        return summary
    except Exception as exc:
        save(output / "failure.json", dict(status="failed", error=str(exc), type=type(exc).__name__))
        raise
