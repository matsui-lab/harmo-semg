"""Repository-grounded validation of task and anatomy comparability gates."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Dict

import yaml

from harmo.evidence.io import sha256_file
from harmo.evidence.models import (
    AnatomicalEntity,
    ArrayReference,
    ChannelDescriptor,
    HarmoRecordingBundle,
    OntologyTerm,
    SemanticMapping,
    SourceAsset,
    TaskDescriptor,
    Timebase,
    TrustEvidence,
    TrustReport,
    TrustStatus,
)
from harmo.evidence.policy import ComparabilityPolicyEngine, ComparabilityRequest
from harmo.evidence.run import current_repository_state


MUSCLES = (
    "tibialis_anterior",
    "gastrocnemius_medialis",
)


def _read_channel_rows(path: Path) -> list:
    with path.open("r", encoding="utf-8") as handle:
        return list((yaml.safe_load(handle) or {}).get("channels", []))


def _read_hed(path: Path) -> Dict[str, str]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return {
        str(key): str(item)
        for key, item in value["task_name"]["HED"].items()
    }


def _right_muscles(path: Path) -> Dict[str, Dict[str, str]]:
    result = {}
    for row in _read_channel_rows(path):
        muscle = str(row.get("muscle", ""))
        side = str(row.get("side", "")).lower()
        if muscle in MUSCLES and side == "right":
            result[muscle] = {
                "source_label": str(row["column_name"]),
                "unit": str(row["unit"]),
                "sensor_type": str(row["sensor_type"]),
            }
    missing = sorted(set(MUSCLES) - set(result))
    if missing:
        raise ValueError(
            "required right-side muscles are absent from {}: {}".format(
                path, ", ".join(missing)
            )
        )
    return result


def _mapping(path: Path, label: str) -> SemanticMapping:
    digest = sha256_file(str(path))
    return SemanticMapping(
        method="curated-study-definition",
        mapping_version="harmo-public-semantics/1.0.0",
        status="mapped",
        evidence_refs=[
            "{}#{}:sha256:{}".format(path.as_uri(), label, digest)
        ],
        confidence=1.0,
    )


def _task(
    hed_path: Path,
    source_label: str,
    hed_value: str,
    *,
    terrain: str,
    direction: str,
    compatibility_group: str,
) -> TaskDescriptor:
    if "Walk" not in hed_value:
        raise ValueError("{} does not declare HED Walk".format(source_label))
    return TaskDescriptor(
        activity="walking",
        source_label=source_label,
        ontology_terms=[OntologyTerm("HED", "8.4.0", "Walk", "Walk")],
        facets={
            "terrain": terrain,
            "direction": direction,
            "speed": "self-selected",
            "state": "steady-state",
        },
        mapping=_mapping(hed_path, source_label),
        compatibility_group=compatibility_group,
    )


def _anatomy(
    channel_path: Path, muscle: str, source_label: str, unit: str
) -> ChannelDescriptor:
    canonical = muscle.replace("_", "-")
    return ChannelDescriptor(
        channel_id="emg",
        name=canonical,
        unit=unit,
        sensor_type="surface-emg",
        muscle=canonical,
        side="right",
        source_name=source_label,
        anatomy=AnatomicalEntity(
            entity_type="muscle",
            canonical_label=canonical,
            ontology_term=OntologyTerm(
                "HarmoAnatomy",
                "1.0.0",
                "HARMOMUSCLE:{}".format(muscle),
                "{} muscle".format(muscle.replace("_", " ")),
            ),
            laterality="right",
            source_label=source_label,
            mapping=_mapping(channel_path, source_label),
        ),
    )


def _bundle(
    study_id: str,
    channel_path: Path,
    task,
    channel: ChannelDescriptor,
) -> HarmoRecordingBundle:
    digest = sha256_file(str(channel_path))
    return HarmoRecordingBundle(
        recording_id="{}/semantic-validation/{}/emg".format(
            study_id, channel.muscle
        ),
        study_id=study_id,
        subject_id="semantic-validation",
        session_id="levelground",
        samples=ArrayReference(
            uri="urn:harmo:semantic-validation:{}".format(study_id),
            format="semantic-fixture",
            dtype="float64",
            shape=[2, 1],
            checksum=digest,
        ),
        timebase=Timebase(1000.0, 2),
        channels=[channel],
        sources=[
            SourceAsset(
                channel_path.as_uri(),
                digest,
                channel_path.stat().st_size,
                "application/yaml",
            )
        ],
        task=task,
        schema_version="1.2.0",
    )


def _trust(recording_id: str) -> TrustReport:
    return TrustReport(
        recording_id,
        [
            TrustEvidence(
                dimension,
                TrustStatus.PASS,
                "semantic-validation.{}".format(dimension),
                "repository definition is checksum-bound",
            )
            for dimension in (
                "semantic_fidelity",
                "scientific_validity",
                "computational_reproducibility",
                "traceability",
            )
        ],
    )


def run_public_semantic_validation(repository_root: str) -> Dict[str, object]:
    """Evaluate real Study 38/Gait120 dictionaries and negative controls."""
    root = Path(repository_root).resolve()
    channel_paths = {
        "study_38": root / "studies/study_38/definitions/channels.yaml",
        "study_120": root / "studies/study_120/definitions/channels.yaml",
    }
    hed_paths = {
        "study_38": root / "studies/study_38/definitions/hed_task.json",
        "study_120": root / "studies/study_120/definitions/hed_task.json",
    }
    for path in [*channel_paths.values(), *hed_paths.values()]:
        if not path.is_file():
            raise ValueError("semantic source is missing: {}".format(path))

    channels = {
        study: _right_muscles(path)
        for study, path in channel_paths.items()
    }
    hed = {study: _read_hed(path) for study, path in hed_paths.items()}
    tasks = {
        study: _task(
            hed_paths[study],
            "{}:levelground".format(study),
            values["levelground"],
            terrain="level",
            direction="forward",
            compatibility_group="walking/level/forward",
        )
        for study, values in hed.items()
    }
    engine = ComparabilityPolicyEngine()
    positives = []
    bundles = {}
    for muscle in MUSCLES:
        left = _bundle(
            "study_38",
            channel_paths["study_38"],
            tasks["study_38"],
            _anatomy(
                channel_paths["study_38"],
                muscle,
                channels["study_38"][muscle]["source_label"],
                channels["study_38"][muscle]["unit"],
            ),
        )
        right = _bundle(
            "study_120",
            channel_paths["study_120"],
            tasks["study_120"],
            _anatomy(
                channel_paths["study_120"],
                muscle,
                channels["study_120"][muscle]["source_label"],
                channels["study_120"][muscle]["unit"],
            ),
        )
        assertion = engine.evaluate(
            left,
            right,
            _trust(left.recording_id),
            _trust(right.recording_id),
            ComparabilityRequest(
                "{}.activation-timing".format(muscle),
                "activation-timing",
                "emg",
                "emg",
            ),
        )
        positives.append(
            {
                "muscle": muscle,
                "left_source_label": left.channels[0].source_name,
                "right_source_label": right.channels[0].source_name,
                "status": assertion.status.value,
                "semantic_gate_pass": (
                    assertion.status.value == "conditionally_comparable"
                    and not any(
                        token in assertion.rationale
                        for token in (
                            "task",
                            "anatomical",
                            "muscle",
                            "laterality",
                        )
                    )
                ),
                "remaining_conditions": assertion.conditions,
            }
        )
        bundles[muscle] = (left, right)

    left, right = bundles["tibialis_anterior"]
    stair = deepcopy(right)
    stair.recording_id = "study_120/semantic-validation/stair/emg"
    stair.session_id = "stairascent"
    stair.task = _task(
        hed_paths["study_120"],
        "study_120:stairascent",
        hed["study_120"]["stairascent"],
        terrain="stairs",
        direction="upward",
        compatibility_group="walking/stairs/upward",
    )
    missing = deepcopy(right)
    missing.recording_id = "study_120/semantic-validation/unmapped/emg"
    missing.task = None
    wrong_muscle = deepcopy(right)
    wrong_muscle.recording_id = "study_120/semantic-validation/wrong-muscle/emg"
    wrong_muscle.channels[0] = _anatomy(
        channel_paths["study_120"],
        "gastrocnemius_medialis",
        channels["study_120"]["gastrocnemius_medialis"]["source_label"],
        channels["study_120"]["gastrocnemius_medialis"]["unit"],
    )
    request = ComparabilityRequest(
        "tibialis_anterior.activation-timing",
        "activation-timing",
        "emg",
        "emg",
    )
    negatives = []
    for control, value, expected in (
        ("level-versus-stairs", stair, "not_comparable"),
        ("missing-task", missing, "unassessed"),
        ("different-muscle", wrong_muscle, "not_comparable"),
    ):
        assertion = engine.evaluate(
            left,
            value,
            _trust(left.recording_id),
            _trust(value.recording_id),
            request,
        )
        negatives.append(
            {
                "control": control,
                "expected": expected,
                "observed": assertion.status.value,
                "pass": assertion.status.value == expected,
                "rationale": assertion.rationale,
            }
        )

    source_attestations = [
        {
            "path": str(path.relative_to(root)),
            "sha256": sha256_file(str(path)),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted([*channel_paths.values(), *hed_paths.values()])
    ]
    overall = all(item["semantic_gate_pass"] for item in positives) and all(
        item["pass"] for item in negatives
    )
    repository_state = current_repository_state()
    return {
        "schema_version": "1.0.0",
        "implementation_commit": repository_state.get("commit"),
        "code_dirty": repository_state.get("dirty"),
        "scope": (
            "repository-grounded semantic mapping validation; no signal, "
            "physiology, amplitude, or population inference"
        ),
        "source_attestations": source_attestations,
        "positive_alias_controls": positives,
        "negative_controls": negatives,
        "summary": {
            "positive_alias_controls_passed": sum(
                item["semantic_gate_pass"] for item in positives
            ),
            "positive_alias_controls_total": len(positives),
            "negative_controls_passed": sum(
                item["pass"] for item in negatives
            ),
            "negative_controls_total": len(negatives),
            "pass": overall,
        },
        "claim_boundary": (
            "The result validates checksum-bound task/anatomy mapping and "
            "fail-closed comparability decisions for two muscles in the "
            "Study 38 and Gait120 repository definitions. It does not validate "
            "the source signals, cross-study amplitude, all tasks, all anatomy, "
            "external ontology coverage, or rehabilitation AI utility."
        ),
    }
