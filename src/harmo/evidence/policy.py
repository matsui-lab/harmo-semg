"""Executable comparability and release policies.

The rules in this module are deliberately conservative.  They turn missing
evidence into ``unassessed``/``quarantine`` rather than inferring that two
recordings are comparable or safe to publish for an intended use.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .anatomy_crosswalk import (
    VerifiedAnatomyCrosswalk,
    exact_equivalence_evidence,
)
from .io import sha256_contract
from .models import (
    AnatomicalEntity,
    ChannelDescriptor,
    Comparability,
    ComparabilityAssertion,
    EvidenceManifest,
    HarmoRecordingBundle,
    ReleaseDecision,
    ReleaseStatus,
    TrustReport,
    TrustStatus,
)


TRUST_DIMENSIONS = (
    "semantic_fidelity",
    "scientific_validity",
    "computational_reproducibility",
    "traceability",
)
COMPARABILITY_RULE_VERSION = "harmo-comparability/1.3.0"
RELEASE_POLICY_VERSION = "1.0.0"
SUPPORTED_INTENDED_USES = (
    "activation-timing",
    "waveform-shape",
    "amplitude-pooling",
)


@dataclass(frozen=True)
class ComparabilityRequest:
    """A pairwise question whose scope is explicit and machine-readable."""

    variable: str
    intended_use: str
    left_channel_id: str
    right_channel_id: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.variable,
                self.intended_use,
                self.left_channel_id,
                self.right_channel_id,
            )
        ):
            raise ValueError("comparability request fields are required")
        if self.intended_use not in SUPPORTED_INTENDED_USES:
            raise ValueError(
                "unsupported intended use {}; expected one of {}".format(
                    self.intended_use, ", ".join(SUPPORTED_INTENDED_USES)
                )
            )


def _channel(bundle: HarmoRecordingBundle, channel_id: str) -> ChannelDescriptor:
    matches = [value for value in bundle.channels if value.channel_id == channel_id]
    if not matches:
        raise ValueError(
            "channel {} is absent from recording {}".format(
                channel_id, bundle.recording_id
            )
        )
    return matches[0]


def _trust_gate(
    report: TrustReport, required_dimensions: Sequence[str]
) -> Tuple[List[str], List[str]]:
    failed = []
    inconclusive = []
    for dimension in required_dimensions:
        state = report.dimension_status(dimension)
        if state == TrustStatus.FAIL:
            failed.append(
                "{}:{}={}".format(report.subject_id, dimension, state.value)
            )
        elif state in (TrustStatus.WARN, TrustStatus.UNASSESSED):
            inconclusive.append(
                "{}:{}={}".format(report.subject_id, dimension, state.value)
            )
    return failed, inconclusive


def _voltage_scale(unit: str) -> Optional[float]:
    normalized = unit.strip().replace("μ", "u").replace("µ", "u").lower()
    return {
        "v": 1.0,
        "mv": 1e-3,
        "uv": 1e-6,
    }.get(normalized)


def _common_validated_phase_reference(
    left: HarmoRecordingBundle,
    right: HarmoRecordingBundle,
) -> bool:
    left_reference = left.temporal_reference
    right_reference = right.temporal_reference
    if left_reference is None or right_reference is None:
        return False
    if (
        left_reference.axis != "gait-cycle-percent"
        or right_reference.axis != "gait-cycle-percent"
    ):
        return False
    return (
        left_reference.unit == right_reference.unit
        and math.isclose(
            left_reference.start,
            right_reference.start,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and math.isclose(
            left_reference.end,
            right_reference.end,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and left_reference.sample_count == right_reference.sample_count
        and left_reference.landmark_start == right_reference.landmark_start
        and left_reference.landmark_end == right_reference.landmark_end
        and left_reference.side == right_reference.side
        and left_reference.compatibility_group
        == right_reference.compatibility_group
        and bool(left_reference.validation_evidence_refs)
        and bool(right_reference.validation_evidence_refs)
        and sorted(left_reference.validation_evidence_refs)
        == sorted(right_reference.validation_evidence_refs)
    )


def _timing_processing_compatible(
    left: HarmoRecordingBundle,
    right: HarmoRecordingBundle,
) -> bool:
    left_state = left.processing_state
    right_state = right.processing_state
    if left_state is None or right_state is None:
        return False
    return (
        left_state.representation in ("envelope", "normalized-envelope")
        and right_state.representation in ("envelope", "normalized-envelope")
        and left_state.timing_compatibility_group
        == right_state.timing_compatibility_group
        and bool(left_state.validation_evidence_refs)
        and bool(right_state.validation_evidence_refs)
        and sorted(left_state.validation_evidence_refs)
        == sorted(right_state.validation_evidence_refs)
    )


def _uses_first_class_semantics(bundle: HarmoRecordingBundle) -> bool:
    return bundle.schema_version == "1.2.0"


def _compare_legacy_anatomy(
    left: ChannelDescriptor,
    right: ChannelDescriptor,
    failures: List[str],
    unknowns: List[str],
) -> None:
    for field_name in ("muscle", "side"):
        left_value = getattr(left, field_name)
        right_value = getattr(right, field_name)
        if left_value in (None, "unknown") or right_value in (None, "unknown"):
            unknowns.append("{} identity is not fully declared".format(field_name))
        elif left_value != right_value:
            failures.append(
                "{} differs: {} != {}".format(
                    field_name, left_value, right_value
                )
            )


def _compare_anatomical_entities(
    left: AnatomicalEntity,
    right: AnatomicalEntity,
    failures: List[str],
    unknowns: List[str],
    crosswalks: Sequence[VerifiedAnatomyCrosswalk],
    crosswalk_evidence_refs: List[str],
) -> None:
    if left.mapping.status != "mapped" or right.mapping.status != "mapped":
        unknowns.append("anatomical identity is not supported by mapped evidence")
        return
    if left.entity_type != right.entity_type:
        failures.append(
            "anatomical entity type differs: {} != {}".format(
                left.entity_type, right.entity_type
            )
        )
    if left.entity_type != "muscle" or right.entity_type != "muscle":
        failures.append("surface EMG comparison requires muscle entities")

    left_term = left.ontology_term
    right_term = right.ontology_term
    if (
        left_term.ontology != right_term.ontology
        or left_term.ontology_version != right_term.ontology_version
    ):
        evidence = exact_equivalence_evidence(
            left_term, right_term, crosswalks
        )
        if evidence is None:
            unknowns.append(
                "cross-ontology anatomical equivalence is not established: "
                "{} {} vs {} {}".format(
                    left_term.ontology,
                    left_term.ontology_version,
                    right_term.ontology,
                    right_term.ontology_version,
                )
            )
        else:
            crosswalk_evidence_refs.append(evidence)
    elif left_term.term_id != right_term.term_id:
        failures.append(
            "anatomical term differs: {} != {}".format(
                left_term.term_id, right_term.term_id
            )
        )

    if left.laterality == "unknown" or right.laterality == "unknown":
        unknowns.append("anatomical laterality is not fully declared")
    elif left.laterality != right.laterality:
        failures.append(
            "anatomical laterality differs: {} != {}".format(
                left.laterality, right.laterality
            )
        )


def _compare_anatomy(
    left_bundle: HarmoRecordingBundle,
    right_bundle: HarmoRecordingBundle,
    left: ChannelDescriptor,
    right: ChannelDescriptor,
    failures: List[str],
    unknowns: List[str],
    crosswalks: Sequence[VerifiedAnatomyCrosswalk],
    crosswalk_evidence_refs: List[str],
) -> None:
    first_class = (
        _uses_first_class_semantics(left_bundle)
        or _uses_first_class_semantics(right_bundle)
    )
    if not first_class:
        _compare_legacy_anatomy(left, right, failures, unknowns)
        return
    if left.anatomy is None or right.anatomy is None:
        unknowns.append(
            "first-class anatomical identity is absent from one or both recordings"
        )
        return
    _compare_anatomical_entities(
        left.anatomy,
        right.anatomy,
        failures,
        unknowns,
        crosswalks,
        crosswalk_evidence_refs,
    )


def _task_term_keys(bundle: HarmoRecordingBundle) -> set:
    task = bundle.task
    if task is None:
        return set()
    return {
        (term.ontology, term.ontology_version, term.term_id)
        for term in task.ontology_terms
    }


def _compare_tasks(
    left: HarmoRecordingBundle,
    right: HarmoRecordingBundle,
    failures: List[str],
    unknowns: List[str],
    conditions: List[str],
) -> None:
    first_class = (
        _uses_first_class_semantics(left)
        or _uses_first_class_semantics(right)
    )
    if not first_class:
        return
    if left.task is None or right.task is None:
        unknowns.append(
            "recording task is absent from one or both first-class semantic bundles"
        )
        return
    if left.task.mapping.status != "mapped" or right.task.mapping.status != "mapped":
        unknowns.append("task identity is not supported by mapped evidence")
        return
    if left.task.activity != right.task.activity:
        failures.append(
            "task activity differs: {} != {}".format(
                left.task.activity, right.task.activity
            )
        )
        return

    left_terms = _task_term_keys(left)
    right_terms = _task_term_keys(right)
    if not left_terms.intersection(right_terms):
        unknowns.append(
            "task ontology equivalence is not established on a common versioned term"
        )

    if (
        left.task.compatibility_group is not None
        and right.task.compatibility_group is not None
        and left.task.compatibility_group != right.task.compatibility_group
    ):
        failures.append(
            "task compatibility group differs: {} != {}".format(
                left.task.compatibility_group,
                right.task.compatibility_group,
            )
        )

    facet_names = sorted(set(left.task.facets) | set(right.task.facets))
    for name in facet_names:
        left_value = left.task.facets.get(name)
        right_value = right.task.facets.get(name)
        if left_value in (None, "unknown") or right_value in (None, "unknown"):
            unknowns.append(
                "task facet {} is not fully declared".format(name)
            )
        elif left_value != right_value:
            if name == "speed":
                conditions.append(
                    "match or stratify task speed ({} vs {})".format(
                        left_value, right_value
                    )
                )
            else:
                failures.append(
                    "task facet {} differs: {} != {}".format(
                        name, left_value, right_value
                    )
                )


class ComparabilityPolicyEngine:
    """Evaluate pairwise EMG comparability for one declared intended use."""

    required_trust_dimensions = (
        "semantic_fidelity",
        "scientific_validity",
        "traceability",
    )

    def __init__(
        self,
        anatomy_crosswalks: Sequence[VerifiedAnatomyCrosswalk] = (),
    ) -> None:
        self.anatomy_crosswalks = tuple(anatomy_crosswalks)

    def evaluate(
        self,
        left: HarmoRecordingBundle,
        right: HarmoRecordingBundle,
        left_trust: TrustReport,
        right_trust: TrustReport,
        request: ComparabilityRequest,
    ) -> ComparabilityAssertion:
        return self._evaluate(left, right, left_trust, right_trust, request)

    def evaluate_with_details(
        self,
        left: HarmoRecordingBundle,
        right: HarmoRecordingBundle,
        left_trust: TrustReport,
        right_trust: TrustReport,
        request: ComparabilityRequest,
    ) -> Dict[str, object]:
        """Return the unchanged assertion plus pre-aggregation group findings.

        This opt-in sidecar does not change the assertion schema or its digest.
        ``no_findings`` describes an executed group, not independent scientific
        validation or a claim that every internal branch has been assessed.
        """
        groups = []  # type: List[Dict[str, object]]
        assertion = self._evaluate(
            left, right, left_trust, right_trust, request, groups
        )
        for purpose in SUPPORTED_INTENDED_USES:
            if purpose != request.intended_use:
                groups.append({
                    "check_id": "purpose:" + purpose,
                    "execution": "not_selected",
                    "findings": [],
                })
        return {
            "schema_version": "1.0.0",
            "detail_version": "harmo-comparability-details/1.0.0",
            "assertion": assertion.to_dict(),
            "assertion_sha256": sha256_contract(assertion),
            "check_groups": groups,
            "input_evidence_refs": list(assertion.evidence_refs),
            "claim_boundary": (
                "Findings from executed rule groups, including those masked by "
                "status precedence. Input references are context, not independent "
                "validation of every finding. No unrestricted scientific assessment."
            ),
        }

    def _evaluate(
        self,
        left: HarmoRecordingBundle,
        right: HarmoRecordingBundle,
        left_trust: TrustReport,
        right_trust: TrustReport,
        request: ComparabilityRequest,
        check_results: Optional[List[Dict[str, object]]] = None,
    ) -> ComparabilityAssertion:
        if left.recording_id != left_trust.subject_id:
            raise ValueError("left trust report does not match left recording")
        if right.recording_id != right_trust.subject_id:
            raise ValueError("right trust report does not match right recording")
        if left.recording_id == right.recording_id:
            raise ValueError("comparability requires two different recordings")

        left_channel = _channel(left, request.left_channel_id)
        right_channel = _channel(right, request.right_channel_id)
        failures = []  # type: List[str]
        unknowns = []  # type: List[str]
        conditions = []  # type: List[str]
        crosswalk_evidence_refs = []  # type: List[str]

        offsets = [0, 0, 0]

        def record_group(check_id: str) -> None:
            if check_results is None:
                return
            findings = []
            for index, (kind, values) in enumerate((
                ("incompatibility", failures),
                ("unresolved", unknowns),
                ("condition", conditions),
            )):
                findings.extend({"kind": kind, "reason": reason}
                                for reason in values[offsets[index]:])
                offsets[index] = len(values)
            check_results.append({
                "check_id": check_id,
                "execution": "executed",
                "outcome": "findings" if findings else "no_findings",
                "findings": findings,
            })

        for role, report in (("left", left_trust), ("right", right_trust)):
            failed, inconclusive = _trust_gate(
                report, self.required_trust_dimensions
            )
            failures.extend(failed)
            unknowns.extend(inconclusive)
            record_group("required_evidence:" + role)

        if left_channel.sensor_type != right_channel.sensor_type:
            failures.append(
                "sensor_type differs: {} != {}".format(
                    left_channel.sensor_type, right_channel.sensor_type
                )
            )

        record_group("sensor_type")
        _compare_anatomy(
            left,
            right,
            left_channel,
            right_channel,
            failures,
            unknowns,
            self.anatomy_crosswalks,
            crosswalk_evidence_refs,
        )
        record_group("anatomy")
        _compare_tasks(left, right, failures, unknowns, conditions)
        record_group("task")

        if request.intended_use == "activation-timing":
            common_phase = _common_validated_phase_reference(left, right)
            if not common_phase:
                conditions.append(
                    "align recordings to a validated common event or phase reference"
                )
            if not _timing_processing_compatible(left, right):
                conditions.append(
                    "establish a validated processing compatibility group for activation timing"
                )
            if left_channel.unit != right_channel.unit:
                conditions.extend(
                    [
                        "derive timing within each recording before comparison",
                        "do not pool or compare signal amplitudes",
                    ]
                )
        elif request.intended_use == "waveform-shape":
            conditions.extend(
                [
                    "retain or apply a declared compatible preprocessing pipeline",
                    "do not infer direct amplitude comparability",
                ]
            )
            left_scale = _voltage_scale(left_channel.unit)
            right_scale = _voltage_scale(right_channel.unit)
            if left_channel.unit != right_channel.unit:
                if left_scale is None or right_scale is None:
                    unknowns.append(
                        "unit/normalization equivalence is not established: {} vs {}".format(
                            left_channel.unit, right_channel.unit
                        )
                    )
                else:
                    conditions.append(
                        "convert both voltage signals to V using declared linear SI factors"
                    )
        else:
            if left_channel.unit != right_channel.unit:
                failures.append(
                    "direct amplitude pooling requires identical declared units"
                )
            elif (
                left.processing_state is not None
                and right.processing_state is not None
                and left.processing_state.amplitude_comparability
                == "cross-recording-validated"
                and right.processing_state.amplitude_comparability
                == "cross-recording-validated"
                and left.processing_state.amplitude_scale
                == right.processing_state.amplitude_scale
                and sorted(left.processing_state.validation_evidence_refs)
                == sorted(right.processing_state.validation_evidence_refs)
            ):
                pass
            else:
                unknowns.append(
                    "equivalent cross-recording amplitude normalization is not validated"
                )

        record_group("purpose:" + request.intended_use)
        common_phase = _common_validated_phase_reference(left, right)
        if not common_phase:
            left_rate = left.timebase.sampling_rate_hz
            right_rate = right.timebase.sampling_rate_hz
            if (
                left_rate is None
                or right_rate is None
                or not math.isclose(
                    left_rate,
                    right_rate,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            ):
                conditions.append(
                    "resample on a declared common time grid without extrapolation"
                )
            if left.timebase.clock != right.timebase.clock:
                conditions.append(
                    "align the declared clock domains before cross-recording comparison"
                )

        record_group("time_reference")
        evidence_refs = [
            "trust:{}:sha256:{}".format(
                left_trust.subject_id, sha256_contract(left_trust)
            ),
            "trust:{}:sha256:{}".format(
                right_trust.subject_id, sha256_contract(right_trust)
            ),
            "recording:{}:sha256:{}".format(
                left.recording_id, sha256_contract(left)
            ),
            "recording:{}:sha256:{}".format(
                right.recording_id, sha256_contract(right)
            ),
        ]
        evidence_refs.extend(sorted(set(crosswalk_evidence_refs)))

        if failures:
            status = Comparability.NOT_COMPARABLE
            rationale = "; ".join(sorted(set(failures)))
            conditions = []
        elif unknowns:
            status = Comparability.UNASSESSED
            rationale = "; ".join(sorted(set(unknowns)))
            conditions = []
        elif conditions:
            status = Comparability.CONDITIONAL
            rationale = (
                "semantic identity is supported, but declared transformations "
                "are required for the intended use"
            )
        else:
            status = Comparability.COMPARABLE
            rationale = (
                "required trust evidence and channel semantics agree for the "
                "declared intended use"
            )

        return ComparabilityAssertion(
            left_id=left.recording_id,
            right_id=right.recording_id,
            variable=request.variable,
            status=status,
            rationale=rationale,
            conditions=sorted(set(conditions)),
            intended_use=request.intended_use,
            left_channel_id=request.left_channel_id,
            right_channel_id=request.right_channel_id,
            evidence_refs=evidence_refs,
            rule_version=COMPARABILITY_RULE_VERSION,
        )


@dataclass(frozen=True)
class ReleasePolicy:
    """Fail-closed policy for one evidence-qualified export."""

    policy_id: str = "emg-atlas-ai-ready"
    policy_version: str = RELEASE_POLICY_VERSION
    required_dimensions: Tuple[str, ...] = TRUST_DIMENSIONS
    allow_trust_warnings: bool = False
    allow_conditional_comparability: bool = True

    def __post_init__(self) -> None:
        if not self.policy_id or not self.policy_version:
            raise ValueError("release policy ID and version are required")
        if (
            not self.required_dimensions
            or len(self.required_dimensions) != len(set(self.required_dimensions))
            or any(value not in TRUST_DIMENSIONS for value in self.required_dimensions)
        ):
            raise ValueError(
                "release policy requires unique known trust dimensions"
            )

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, object]]) -> "ReleasePolicy":
        values = data or {}
        for key in (
            "allow_trust_warnings",
            "allow_conditional_comparability",
        ):
            if key in values and not isinstance(values[key], bool):
                raise ValueError("{} must be boolean".format(key))
        dimensions = values.get("required_dimensions", TRUST_DIMENSIONS)
        if isinstance(dimensions, (str, bytes)) or not isinstance(
            dimensions, (list, tuple)
        ):
            raise ValueError("required_dimensions must be an array")
        return cls(
            policy_id=str(values.get("policy_id", "emg-atlas-ai-ready")),
            policy_version=str(
                values.get("policy_version", RELEASE_POLICY_VERSION)
            ),
            required_dimensions=tuple(
                str(value) for value in dimensions
            ),
            allow_trust_warnings=bool(
                values.get("allow_trust_warnings", False)
            ),
            allow_conditional_comparability=bool(
                values.get("allow_conditional_comparability", True)
            ),
        )

    def decide(
        self,
        bundle: HarmoRecordingBundle,
        manifest: EvidenceManifest,
        trust: TrustReport,
        comparison: ComparabilityAssertion,
    ) -> ReleaseDecision:
        if bundle.recording_id != trust.subject_id:
            raise ValueError("trust report does not match release subject")
        if bundle.recording_id not in (comparison.left_id, comparison.right_id):
            raise ValueError("comparison does not include release subject")

        states = {
            dimension: trust.dimension_status(dimension)
            for dimension in self.required_dimensions
        }
        failures = [
            "{}={}".format(dimension, state.value)
            for dimension, state in states.items()
            if state == TrustStatus.FAIL
        ]
        unknowns = [
            "{}={}".format(dimension, state.value)
            for dimension, state in states.items()
            if state == TrustStatus.UNASSESSED
        ]
        warnings = [
            "{}={}".format(dimension, state.value)
            for dimension, state in states.items()
            if state == TrustStatus.WARN
        ]
        conditions = list(comparison.conditions)

        if failures or comparison.status == Comparability.NOT_COMPARABLE:
            status = ReleaseStatus.DENY
            reasons = failures or [
                "not comparable for {}".format(comparison.intended_use)
            ]
            if comparison.status == Comparability.NOT_COMPARABLE:
                reasons.append(comparison.rationale)
            conditions = []
        elif unknowns or comparison.status == Comparability.UNASSESSED:
            status = ReleaseStatus.QUARANTINE
            reasons = unknowns
            if comparison.status == Comparability.UNASSESSED:
                reasons.append(
                    "comparability unassessed: {}".format(comparison.rationale)
                )
            conditions = []
        elif warnings and not self.allow_trust_warnings:
            status = ReleaseStatus.QUARANTINE
            reasons = [
                "trust warnings are not accepted by policy: {}".format(
                    ", ".join(warnings)
                )
            ]
            conditions = []
        elif (
            comparison.status == Comparability.CONDITIONAL
            and not self.allow_conditional_comparability
        ):
            status = ReleaseStatus.DENY
            reasons = ["conditional comparability is not accepted by policy"]
            conditions = []
        elif comparison.status == Comparability.CONDITIONAL or warnings:
            status = ReleaseStatus.CONDITIONAL
            reasons = [
                "all required dimensions are assessed without failure",
                "release is limited to the declared intended use",
            ]
            if warnings:
                conditions.append(
                    "retain trust warnings with every downstream use"
                )
        else:
            status = ReleaseStatus.ALLOW
            reasons = [
                "all required trust dimensions pass",
                "resource is comparable for the declared intended use",
            ]

        digests = {
            "recording": sha256_contract(bundle),
            "manifest": sha256_contract(manifest),
            "trust": sha256_contract(trust),
            "comparison": sha256_contract(comparison),
        }
        identity = sha256_contract(
            {
                "subject_id": bundle.recording_id,
                "intended_use": comparison.intended_use,
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "digests": digests,
                "status": status.value,
            }
        )
        return ReleaseDecision(
            decision_id="release-{}".format(identity[:20]),
            subject_id=bundle.recording_id,
            intended_use=comparison.intended_use,
            status=status,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            recording_sha256=digests["recording"],
            manifest_sha256=digests["manifest"],
            trust_report_sha256=digests["trust"],
            comparability_assertion_sha256=digests["comparison"],
            required_dimensions=list(self.required_dimensions),
            reasons=reasons,
            conditions=sorted(set(conditions)),
        )
