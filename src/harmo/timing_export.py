"""Export actual selected timing arrays only after contract-bound release checks."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from harmo.config.automap import require, save
from harmo.evidence.audit import export_atlas_exchange
from harmo.evidence.io import sha256_file
from harmo.evidence.models import (
    HarmoRecordingBundle, TrustReport, EvidenceManifest, ComparabilityAssertion, ReleaseDecision,
)


def export_pair(resolved, output):
    resolved, output = Path(resolved).resolve(), Path(output).resolve()
    require(not output.exists(), "export_output_exists")
    def read(path):
        return json.loads(path.read_text())
    manifest = EvidenceManifest.from_dict(read(resolved / "evidence.manifest.json"))
    comparison = ComparabilityAssertion.from_dict(read(resolved / "comparability.after.json"))
    require(comparison.intended_use == "activation-timing" and comparison.status.value == "comparable",
            "unconditional_timing_comparison_required")
    arrays, contracts, sources = {}, {}, {}
    for role in ("left", "right"):
        directory = resolved / role
        bundle = HarmoRecordingBundle.from_dict(read(directory / "recording.bundle.json"))
        from harmo.planned_timing_rows import RELEASE, verify_lineage
        from urllib.parse import urlparse, unquote
        for source in bundle.sources:
            if source.dataset_release == RELEASE:
                uri=urlparse(source.uri)
                require(uri.scheme=='file' and not uri.netloc,'local_execution_lineage_required')
                lineage=Path(unquote(uri.path))
                require(sha256_file(str(lineage))==source.sha256,'execution_lineage_changed')
                verify_lineage(lineage)
        trust = TrustReport.from_dict(read(directory / "trust.report.json"))
        release = ReleaseDecision.from_dict(read(directory / "release.decision.json"))
        require(release.status.value == "allow", "unconditional_release_required")
        samples, phase = directory / "resolved.samples.csv", directory / "resolved.phase.csv"
        require(sha256_file(str(samples)) == bundle.samples.checksum, "samples_changed_after_release")
        matches = [a for a in manifest.outputs if a.uri == bundle.timebase.timestamps_uri]
        require(len(matches) == 1 and matches[0].sha256 == sha256_file(str(phase)), "phase_changed_after_release")
        frame = pd.read_csv(samples, float_precision="round_trip")
        time = pd.read_csv(phase, float_precision="round_trip")
        require(list(frame.columns) == [c.name for c in bundle.channels] and list(frame.shape) == bundle.samples.shape,
                "sample_shape_or_labels_changed")
        require(list(time.columns) == ["gait_cycle_percent"] and len(time) == len(frame), "phase_shape_changed")
        reference = bundle.temporal_reference
        require(reference is not None and reference.axis == "gait-cycle-percent", "declared_phase_required")
        grid = time.iloc[:, 0].to_numpy(dtype=float)
        require(np.array_equal(grid, np.linspace(reference.start, reference.end, reference.sample_count)), "phase_grid_conflict")
        channel_id = getattr(comparison, role + "_channel_id")
        channels = [i for i, c in enumerate(bundle.channels) if c.channel_id == channel_id]
        require(len(channels) == 1, "comparison_channel_ambiguous")
        values = frame.iloc[:, channels[0]].to_numpy(dtype=float)
        require(np.isfinite(values).all(), "nonfinite_export_values")
        arrays[role + "_phase"] = grid
        arrays[role + "_values"] = values
        contracts[role] = (bundle, trust, release)
        sources[role] = dict(record_id=bundle.recording_id, channel_id=channel_id,
            unit=bundle.channels[channels[0]].unit, source_samples_sha256=bundle.samples.checksum)
    # Existing exchange checks validate all release-bound contract hashes.
    # No signal file is written until both roles pass these checks.
    output.mkdir(parents=True)
    for role, (bundle, trust, release) in contracts.items():
        export_atlas_exchange(str(output / role), bundle, manifest, trust, comparison, release)
    with (output / "timing.npz").open("xb") as stream:
        np.savez(stream, **arrays)
    result = dict(purpose="activation-timing", sources=sources, file="timing.npz",
                  sha256=sha256_file(str(output / "timing.npz")), amplitude_pooling=False,
                  representation="Separate within-cycle-scaled timing curves; not pooled amplitudes")
    save(output / "signals.json", result)
    return result
