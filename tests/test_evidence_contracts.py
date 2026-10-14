import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from harmo.evidence.audit import (
    audit_manifest,
    compare_contracts,
    export_atlas_exchange,
)
from harmo.evidence.io import read_json, sha256_contract, sha256_file
from harmo.evidence.models import (
    AnatomicalEntity,
    ArrayReference,
    ArtifactReference,
    ChannelDescriptor,
    Comparability,
    ComparabilityAssertion,
    EvidenceManifest,
    EventDescriptor,
    HarmoRecordingBundle,
    OntologyTerm,
    OpenSimArtifact,
    ReleaseDecision,
    ReleaseStatus,
    SourceAsset,
    SemanticMapping,
    TaskDescriptor,
    Timebase,
    TransformationStep,
    TrustEvidence,
    TrustReport,
    TrustStatus,
)
from harmo.evidence.policy import ReleasePolicy
from harmo.evidence.validation import (
    RecordingProfile,
    assess_bundle,
    compare_numeric_series,
)
from harmo.evidence.run import (
    capture_run_manifest,
    current_commit,
    current_repository_state,
    runtime_environment,
)


ZERO_SHA = "0" * 64


def bundle():
    return HarmoRecordingBundle(
        recording_id="study-1/sub-1/ses-1/emg",
        study_id="study-1",
        subject_id="sub-1",
        session_id="ses-1",
        samples=ArrayReference(
            uri="s3://atlas/signal.zarr",
            format="zarr-v3",
            dtype="float32",
            shape=[100, 2],
        ),
        timebase=Timebase(sampling_rate_hz=1000.0, sample_count=100),
        channels=[
            ChannelDescriptor(
                "ch-1",
                "TA",
                "V",
                "surface-emg",
                muscle="tibialis-anterior",
                side="left",
            ),
            ChannelDescriptor(
                "ch-2",
                "GM",
                "V",
                "surface-emg",
                muscle="gastrocnemius-medialis",
                side="left",
            ),
        ],
        sources=[SourceAsset("https://example.test/raw.c3d", ZERO_SHA, 123)],
    )


def manifest():
    return EvidenceManifest(
        run_id="run-1",
        inputs=[ArtifactReference("raw", "https://example.test/raw.c3d", ZERO_SHA)],
        outputs=[
            ArtifactReference(
                "bundle",
                "s3://atlas/bundle.json",
                sha256_contract(bundle()),
                "application/json",
                "sha256-canonical-json",
            )
        ],
        steps=[
            TransformationStep(
                "ingest",
                "C3D ingest",
                "harmo",
                "1.0.0",
                inputs=["raw"],
                outputs=["bundle"],
            )
        ],
        code_commit="abc123",
        config_sha256="2" * 64,
        created_at="2026-01-01T00:00:00Z",
    )


def passing_trust(subject_id=None):
    return TrustReport(
        subject_id or bundle().recording_id,
        [
            TrustEvidence(
                dimension,
                TrustStatus.PASS,
                "test.{}".format(dimension),
                "test evidence passes",
            )
            for dimension in (
                "semantic_fidelity",
                "scientific_validity",
                "computational_reproducibility",
                "traceability",
            )
        ],
    )


def comparison_for_bundle(status=Comparability.COMPARABLE):
    return ComparabilityAssertion(
        "reference-recording",
        bundle().recording_id,
        "activation-timing",
        status,
        "test comparability evidence",
        (
            ["compare within gait-cycle phase only"]
            if status == Comparability.CONDITIONAL
            else []
        ),
        intended_use="activation-timing",
        left_channel_id="ch-1",
        right_channel_id="ch-1",
        evidence_refs=["test:evidence"],
        rule_version="test-policy/1.0.0",
    )


def release_for_bundle(trust=None, comparison=None):
    trust = trust or passing_trust()
    comparison = comparison or comparison_for_bundle()
    return ReleasePolicy().decide(
        bundle(), manifest(), trust, comparison
    )


class EvidenceContractTests(unittest.TestCase):
    def test_bundle_shape_and_round_trip(self):
        value = bundle()
        restored = HarmoRecordingBundle.from_dict(json.loads(value.to_json()))
        self.assertEqual(restored.samples.shape, [100, 2])
        self.assertEqual(sha256_contract(value), sha256_contract(restored))

    def test_recording_1_2_round_trip_preserves_task_and_anatomy(self):
        value = bundle()
        mapping = SemanticMapping(
            "curated-dictionary",
            "test/1.0.0",
            "mapped",
            ["test:mapping:sha256:{}".format("1" * 64)],
            1.0,
        )
        value.schema_version = "1.2.0"
        value.task = TaskDescriptor(
            "walking",
            "LevelWalking",
            [OntologyTerm("HED", "8.4.0", "Walk", "Walk")],
            {
                "terrain": "level",
                "direction": "forward",
                "speed": "self-selected",
            },
            mapping,
            "walking/level/forward",
        )
        value.channels[0].anatomy = AnatomicalEntity(
            "muscle",
            "tibialis-anterior",
            OntologyTerm(
                "HarmoAnatomy",
                "1.0.0",
                "HARMOMUSCLE:tibialis_anterior",
                "tibialis anterior muscle",
            ),
            "left",
            "TA",
            mapping,
        )
        value.__post_init__()

        restored = HarmoRecordingBundle.from_dict(
            json.loads(value.to_json())
        )

        self.assertEqual(restored.task.activity, "walking")
        self.assertEqual(
            restored.channels[0].anatomy.ontology_term.term_id,
            "HARMOMUSCLE:tibialis_anterior",
        )
        self.assertEqual(sha256_contract(value), sha256_contract(restored))

    def test_mapped_semantics_require_checksum_bound_evidence(self):
        with self.assertRaisesRegex(ValueError, "SHA-256-bound"):
            SemanticMapping(
                "manual",
                "test/1.0.0",
                "mapped",
                ["unbound-note"],
            )

    def test_legacy_schema_rejects_silent_first_class_semantics(self):
        value = bundle()
        value.task = TaskDescriptor(
            "walking",
            "walking",
            [OntologyTerm("HED", "8.4.0", "Walk", "Walk")],
            {},
            SemanticMapping("manual", "test/1.0.0", "unmapped", []),
        )
        with self.assertRaisesRegex(ValueError, "schema 1.2.0"):
            value.__post_init__()

    def test_bundle_rejects_silent_channel_loss(self):
        value = bundle()
        value.samples.shape = [100, 1]
        with self.assertRaises(ValueError):
            value.__post_init__()

    def test_manifest_audit(self):
        result = audit_manifest(manifest(), "bundle")
        self.assertEqual(result["lineage"][0]["produced_by"], ["ingest"])

    def test_manifest_rejects_unknown_lineage_artifact(self):
        with self.assertRaisesRegex(ValueError, "unknown artifacts"):
            EvidenceManifest(
                run_id="run-bad",
                inputs=[ArtifactReference("raw", "file:///raw", ZERO_SHA)],
                outputs=[],
                steps=[
                    TransformationStep(
                        "ingest",
                        "ingest",
                        "harmo",
                        "1.0",
                        inputs=["missing"],
                    )
                ],
                code_commit="abc123",
                config_sha256=ZERO_SHA,
            )

    def test_all_versioned_contracts_reject_unknown_schema_versions(self):
        bad_manifest = manifest().to_dict()
        bad_manifest["schema_version"] = "9.0.0"
        with self.assertRaisesRegex(ValueError, "unsupported manifest"):
            EvidenceManifest.from_dict(bad_manifest)
        with self.assertRaisesRegex(ValueError, "unsupported comparability"):
            ComparabilityAssertion(
                "a",
                "b",
                "timing",
                Comparability.UNASSESSED,
                "not assessed",
                schema_version="9.0.0",
            )
        with self.assertRaisesRegex(ValueError, "unsupported OpenSim"):
            OpenSimArtifact(
                "osim",
                "recording",
                ArtifactReference("model", "file:///model", ZERO_SHA),
                [],
                schema_version="9.0.0",
            )
        with self.assertRaisesRegex(ValueError, "unsupported release"):
            ReleaseDecision(
                "decision",
                "recording",
                "timing",
                ReleaseStatus.QUARANTINE,
                "policy",
                "1.0.0",
                ZERO_SHA,
                ZERO_SHA,
                ZERO_SHA,
                ZERO_SHA,
                ["traceability"],
                ["not assessed"],
                schema_version="9.0.0",
            )

    def test_events_must_be_finite_and_unique(self):
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            EventDescriptor("event-1", float("nan"), "heel-strike")
        value = bundle()
        event = EventDescriptor("event-1", 0.1, "heel-strike")
        value.events = [event, event]
        with self.assertRaisesRegex(ValueError, "event IDs must be unique"):
            value.__post_init__()

    def test_numeric_reproduction_tolerance(self):
        self.assertTrue(compare_contracts({"x": 1.0}, {"x": 1.0 + 1e-10})["matches"])
        self.assertFalse(compare_contracts({"x": 1.0}, {"x": 1.1})["matches"])
        self.assertFalse(compare_contracts({"x": 1}, {"x": True})["matches"])
        self.assertFalse(
            compare_contracts({"x": float("inf")}, {"x": float("inf")})["matches"]
        )

    def test_json_reader_rejects_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text('{"metric": NaN}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-finite JSON"):
                read_json(str(path))

    def test_opensim_thresholds_are_evidence_not_hidden_grade(self):
        artifact = OpenSimArtifact(
            artifact_id="osim-1",
            recording_id="rec-1",
            model=ArtifactReference("model", "model.osim", ZERO_SHA),
            outputs=[],
            ik_rms_error_m=0.019,
            ik_max_error_m=0.041,
        )
        evidence = {item.rule_id: item.status for item in artifact.trust_evidence()}
        self.assertEqual(evidence["opensim.ik_rms"], TrustStatus.PASS)
        self.assertEqual(evidence["opensim.ik_max"], TrustStatus.FAIL)
        self.assertEqual(
            evidence["opensim.residual_force_peak"], TrustStatus.UNASSESSED
        )

    def test_opensim_rejects_nonfinite_or_negative_quality_metrics(self):
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            OpenSimArtifact(
                artifact_id="osim-1",
                recording_id="rec-1",
                model=ArtifactReference("model", "model.osim", ZERO_SHA),
                outputs=[],
                ik_rms_error_m=float("nan"),
            )
        with self.assertRaisesRegex(ValueError, "manifest_run_id"):
            OpenSimArtifact(
                artifact_id="osim-1",
                recording_id="rec-1",
                model=ArtifactReference("model", "model.osim", ZERO_SHA),
                outputs=[],
                manifest_run_id="",
            )

    def test_comparability_never_defaults_to_comparable(self):
        assertion = ComparabilityAssertion(
            "study-a",
            "study-b",
            "emg-amplitude",
            Comparability.CONDITIONAL,
            "different normalization",
            ["compare timing only"],
        )
        self.assertEqual(assertion.status, Comparability.CONDITIONAL)

    def test_atlas_exchange_has_verified_index(self):
        trust = passing_trust()
        comparison = comparison_for_bundle()
        decision = release_for_bundle(trust, comparison)
        with tempfile.TemporaryDirectory() as directory:
            files = export_atlas_exchange(
                directory,
                bundle(),
                manifest(),
                trust,
                comparison,
                decision,
            )
            self.assertEqual(
                set(files),
                {
                    "recording",
                    "manifest",
                    "trust",
                    "comparability",
                    "release_decision",
                    "index",
                },
            )
            index = json.loads(Path(files["index"]).read_text(encoding="utf-8"))
            self.assertEqual(index["recording_id"], bundle().recording_id)
            self.assertEqual(index["trust_summary"]["traceability"], "pass")
            self.assertEqual(
                index["trust_summary"]["scientific_validity"], "pass"
            )

    def test_atlas_exchange_rejects_trust_for_another_recording(self):
        trust = TrustReport("another-recording", [])
        comparison = comparison_for_bundle()
        decision = release_for_bundle()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "does not match"):
                export_atlas_exchange(
                    directory,
                    bundle(),
                    manifest(),
                    trust,
                    comparison,
                    decision,
                )

    def test_atlas_exchange_rejects_unlinked_bundle(self):
        unlinked = manifest()
        unlinked.outputs[0].sha256 = "9" * 64
        trust = passing_trust()
        comparison = comparison_for_bundle()
        decision = release_for_bundle(trust, comparison)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                ValueError, "canonical recording bundle digest"
            ):
                export_atlas_exchange(
                    directory,
                    bundle(),
                    unlinked,
                    trust,
                    comparison,
                    decision,
                )

    def test_atlas_exchange_is_fail_closed_for_quarantine(self):
        trust = TrustReport(bundle().recording_id, [])
        comparison = comparison_for_bundle(Comparability.UNASSESSED)
        decision = ReleasePolicy().decide(
            bundle(), manifest(), trust, comparison
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "exchange"
            with self.assertRaisesRegex(ValueError, "forbids"):
                export_atlas_exchange(
                    str(target),
                    bundle(),
                    manifest(),
                    trust,
                    comparison,
                    decision,
                )
            self.assertFalse(target.exists())

    def test_atlas_exchange_rejects_forged_allow_with_unassessed_trust(self):
        trust = TrustReport(bundle().recording_id, [])
        comparison = comparison_for_bundle(Comparability.UNASSESSED)
        decision = ReleasePolicy().decide(
            bundle(), manifest(), trust, comparison
        )
        forged = replace(decision, status=ReleaseStatus.ALLOW)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "exchange"
            with self.assertRaisesRegex(ValueError, "trust evidence blocks"):
                export_atlas_exchange(
                    str(target),
                    bundle(),
                    manifest(),
                    trust,
                    comparison,
                    forged,
                )
            self.assertFalse(target.exists())

    def test_comparability_v1_contract_round_trip_stays_v1(self):
        payload = {
            "left_id": "study-a",
            "right_id": "study-b",
            "variable": "timing",
            "status": "unassessed",
            "rationale": "legacy assertion",
            "conditions": [],
            "rule_version": "1.0.0",
            "schema_version": "1.0.0",
        }
        restored = ComparabilityAssertion.from_dict(payload)
        self.assertEqual(restored.to_dict(), payload)

    def test_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x"
            path.write_bytes(b"abc")
            self.assertEqual(
                sha256_file(str(path)),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )

    def test_fault_injection_detects_unit_error(self):
        value = bundle()
        value.channels[0].unit = "mV"
        report = assess_bundle(
            value,
            RecordingProfile(
                units_by_sensor_type={"surface-emg": ["V"]},
                require_array_checksum=False,
            ),
        )
        self.assertEqual(report.dimension_status("semantic_fidelity"), TrustStatus.FAIL)

    def test_numeric_fidelity_metrics(self):
        metrics = compare_numeric_series([0.0, 1.0, 2.0], [0.0, 1.0, 2.000001])
        self.assertGreater(metrics["pearson_correlation"], 0.999999)
        self.assertLess(metrics["max_absolute_error"], 0.000002)

    def test_run_manifest_is_written_with_repository_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text("warehouse_path: ./warehouse\n", encoding="utf-8")
            source = Path(directory) / "raw.c3d"
            source.write_bytes(b"raw-c3d")
            warehouse = Path(directory) / "warehouse"
            result = capture_run_manifest(
                str(config),
                str(warehouse),
                "ingest",
                "completed",
                run_id="run-fixed",
                source_digests={str(source): sha256_file(str(source))},
            )
            payload = json.loads(Path(result).read_text(encoding="utf-8"))
            self.assertEqual(payload["code_commit"], current_commit())
            self.assertNotEqual(payload["code_commit"], "unknown")
            self.assertEqual(payload["steps"][0]["parameters"]["status"], "completed")
            self.assertIsInstance(payload["code_dirty"], bool)
            self.assertIn("python", payload["environment"])
            self.assertEqual(len(payload["inputs"]), 2)
            self.assertEqual(payload["inputs"][1]["sha256"], sha256_file(str(source)))
            self.assertEqual(payload["steps"][0]["inputs"], ["config", "source-000001"])

    def test_repository_and_runtime_state_are_explicit(self):
        state = current_repository_state()
        self.assertNotEqual(state["commit"], "unknown")
        self.assertIsInstance(state["dirty"], bool)
        if state["dirty"]:
            self.assertRegex(str(state["working_tree_sha256"]), r"^[0-9a-f]{64}$")
        self.assertIn("python", runtime_environment())


if __name__ == "__main__":
    unittest.main()
