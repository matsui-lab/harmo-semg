from copy import deepcopy
import json
from pathlib import Path

from harmo.evidence.models import (
    AnatomicalEntity,
    Comparability,
    HarmoRecordingBundle,
    OntologyTerm,
    ReleaseStatus,
    SemanticMapping,
    TaskDescriptor,
    TrustEvidence,
    TrustReport,
    TrustStatus,
)
from harmo.evidence.policy import (
    ComparabilityPolicyEngine,
    ComparabilityRequest,
    ReleasePolicy,
)
from test_evidence_contracts import bundle, manifest, passing_trust


ROOT = Path(__file__).resolve().parents[1]


def _other_bundle():
    value = HarmoRecordingBundle.from_dict(deepcopy(bundle().to_dict()))
    value.recording_id = "study-2/sub-2/ses-1/emg"
    value.study_id = "study-2"
    value.subject_id = "sub-2"
    return value


def _request(intended_use="activation-timing"):
    return ComparabilityRequest(
        variable="tibialis-anterior.{}".format(intended_use),
        intended_use=intended_use,
        left_channel_id="ch-1",
        right_channel_id="ch-1",
    )


def _mapping(identifier):
    return SemanticMapping(
        method="curated-source-dictionary",
        mapping_version="test-mapping/1.0.0",
        status="mapped",
        evidence_refs=[
            "mapping:{}:sha256:{}".format(identifier, identifier * 64)
        ],
        confidence=1.0,
    )


def _semantic_bundle(
    value,
    *,
    task_source,
    terrain="level",
    speed="self-selected",
    task=True,
    anatomy=True,
):
    value.schema_version = "1.2.0"
    value.task = (
        TaskDescriptor(
            activity="walking",
            source_label=task_source,
            ontology_terms=[
                OntologyTerm("HED", "8.4.0", "Walk", "Walk")
            ],
            facets={
                "terrain": terrain,
                "direction": "forward",
                "speed": speed,
                "state": "steady-state",
            },
            mapping=_mapping("1"),
            compatibility_group="walking/level/forward",
        )
        if task
        else None
    )
    if anatomy:
        entity = AnatomicalEntity(
            entity_type="muscle",
            canonical_label="tibialis-anterior",
            ontology_term=OntologyTerm(
                "HarmoAnatomy",
                "1.0.0",
                "HARMOMUSCLE:tibialis_anterior",
                "tibialis anterior muscle",
            ),
            laterality="left",
            source_label=value.channels[0].source_name or value.channels[0].name,
            mapping=_mapping("2"),
        )
        value.channels[0].anatomy = entity
    value.__post_init__()
    return value


def test_comparability_engine_emits_scoped_timing_assertion():
    left = bundle()
    right = _other_bundle()
    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )

    assert assertion.status == Comparability.CONDITIONAL
    assert assertion.intended_use == "activation-timing"
    assert assertion.left_channel_id == "ch-1"
    assert len(assertion.evidence_refs) == 4
    assert "common event or phase reference" in " ".join(assertion.conditions)


def test_first_class_task_and_anatomy_ignore_source_aliases():
    left = _semantic_bundle(
        bundle(), task_source="Walking", anatomy=True
    )
    right = _semantic_bundle(
        _other_bundle(), task_source="LevelWalking", anatomy=True
    )

    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )

    assert assertion.status == Comparability.CONDITIONAL
    assert "task" not in assertion.rationale
    assert assertion.rule_version == "harmo-comparability/1.3.0"


def test_level_walking_and_stairs_are_not_comparable():
    left = _semantic_bundle(bundle(), task_source="Walking")
    right = _semantic_bundle(
        _other_bundle(), task_source="Stair ascent", terrain="stairs"
    )
    right.task.compatibility_group = "walking/stairs/upward"

    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )
    decision = ReleasePolicy().decide(
        right,
        manifest_for(right),
        passing_trust(right.recording_id),
        assertion,
    )

    assert assertion.status == Comparability.NOT_COMPARABLE
    assert "task" in assertion.rationale
    assert decision.status == ReleaseStatus.DENY
    assert decision.export_permitted is False


def test_missing_first_class_task_or_anatomy_is_unassessed():
    left = _semantic_bundle(bundle(), task_source="Walking")
    right = _semantic_bundle(
        _other_bundle(),
        task_source="Walking",
        task=False,
        anatomy=False,
    )

    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )
    decision = ReleasePolicy().decide(
        right,
        manifest_for(right),
        passing_trust(right.recording_id),
        assertion,
    )

    assert assertion.status == Comparability.UNASSESSED
    assert "absent" in assertion.rationale
    assert decision.status == ReleaseStatus.QUARANTINE
    assert decision.export_permitted is False


def test_anatomical_term_and_laterality_mismatches_fail_closed():
    left = _semantic_bundle(bundle(), task_source="Walking")
    right = _semantic_bundle(_other_bundle(), task_source="Walking")
    right.channels[0].anatomy.ontology_term.term_id = (
        "HARMOMUSCLE:gastrocnemius_medialis"
    )
    right.channels[0].anatomy.laterality = "right"
    right.channels[0].side = "right"

    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )

    assert assertion.status == Comparability.NOT_COMPARABLE
    assert "anatomical term differs" in assertion.rationale
    assert "laterality differs" in assertion.rationale


def test_cross_ontology_anatomy_is_unassessed_without_crosswalk():
    left = _semantic_bundle(bundle(), task_source="Walking")
    right = _semantic_bundle(_other_bundle(), task_source="Walking")
    right.channels[0].anatomy.ontology_term.ontology = "OtherAnatomy"

    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )

    assert assertion.status == Comparability.UNASSESSED
    assert "cross-ontology" in assertion.rationale


def test_task_speed_difference_requires_stratification():
    left = _semantic_bundle(
        bundle(), task_source="Walking", speed="self-selected"
    )
    right = _semantic_bundle(
        _other_bundle(), task_source="Fast walking", speed="fast"
    )

    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )

    assert assertion.status == Comparability.CONDITIONAL
    assert any("stratify task speed" in value for value in assertion.conditions)


def test_timing_units_are_conditional_and_amplitude_pooling_is_unassessed():
    left = bundle()
    right = _other_bundle()
    right.channels[0].unit = "mV"
    engine = ComparabilityPolicyEngine()
    timing = engine.evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )
    amplitude = engine.evaluate(
        left,
        _other_bundle(),
        passing_trust(left.recording_id),
        passing_trust(_other_bundle().recording_id),
        _request("amplitude-pooling"),
    )

    assert timing.status == Comparability.CONDITIONAL
    assert "do not pool or compare signal amplitudes" in timing.conditions
    assert amplitude.status == Comparability.UNASSESSED
    assert "normalization" in amplitude.rationale


def test_semantic_mismatch_is_not_comparable_and_denied():
    left = bundle()
    right = _other_bundle()
    right.channels[0].side = "right"
    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )
    decision = ReleasePolicy().decide(
        right, manifest_for(right), passing_trust(right.recording_id), assertion
    )

    assert assertion.status == Comparability.NOT_COMPARABLE
    assert decision.status == ReleaseStatus.DENY
    assert decision.export_permitted is False


def manifest_for(value):
    result = manifest()
    from harmo.evidence.io import sha256_contract

    result.outputs[0].sha256 = sha256_contract(value)
    return result


def test_missing_required_trust_is_quarantined_not_promoted():
    left = bundle()
    right = _other_bundle()
    right_trust = TrustReport(
        right.recording_id,
        [
            TrustEvidence(
                "traceability",
                TrustStatus.PASS,
                "trace.pass",
                "traceability passes",
            )
        ],
    )
    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        right_trust,
        _request(),
    )
    decision = ReleasePolicy().decide(
        right, manifest_for(right), right_trust, assertion
    )

    assert assertion.status == Comparability.UNASSESSED
    assert decision.status == ReleaseStatus.QUARANTINE


def test_conditional_comparability_produces_conditional_release():
    left = bundle()
    right = _other_bundle()
    right.timebase.sampling_rate_hz = 2000.0
    assertion = ComparabilityPolicyEngine().evaluate(
        left,
        right,
        passing_trust(left.recording_id),
        passing_trust(right.recording_id),
        _request(),
    )
    decision = ReleasePolicy().decide(
        right,
        manifest_for(right),
        passing_trust(right.recording_id),
        assertion,
    )

    assert assertion.status == Comparability.CONDITIONAL
    assert decision.status == ReleaseStatus.CONDITIONAL
    assert decision.export_permitted is True
    assert decision.conditions


def test_real_data_qualification_pilot_retains_conditional_boundary():
    result = json.loads(
        (
            ROOT
            / "paper/results/validation/evidence_qualification_pilot.json"
        ).read_text(encoding="utf-8")
    )
    qualified = result["qualified_recording"]

    assert all(item["pass"] for item in result["source_attestations"])
    assert qualified["comparability"]["status"] == "conditionally_comparable"
    assert qualified["release_status"] == "conditional"
    assert qualified["exchange_exported"] is True
    assert qualified["code_dirty"] is False
    assert "does not materialize all 78 mappings" in result["claim_boundary"]

    reproducibility = json.loads(
        (
            ROOT
            / "paper/results/validation/"
            "evidence_qualification_pilot_reproducibility.json"
        ).read_text(encoding="utf-8")
    )
    assert reproducibility["pass"] is True
    assert reproducibility["independent_work_directories"] == 2
    assert reproducibility["recording_identity_equal"] is True
    assert reproducibility["release_conditions_equal"] is True
