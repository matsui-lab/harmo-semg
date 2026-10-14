#!/usr/bin/env python3
"""Run frozen A-G harmo-semg validation experiments and gate audits."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import yaml

from harmo.format_fidelity import run_study31_format_fidelity
from harmo.opensim_quality import (
    probe_opensim_container,
    probe_opensim_python,
    run_study31_opensim_quality,
)
from harmo.opensim_sources import (
    audit_legacy_scaled_models,
    validate_opensim_source_bundle,
)
from harmo.g6_execution import build_execution_plan
from harmo.dynamic_calibration import (
    download_public_source_bundle,
    run_dynamic_calibration_benchmark,
    sha256_file,
    validate_public_source_bundle,
    write_dynamic_calibration_results,
)
from harmo.dynamic_model import (
    build_dynamic_model_plan,
    evaluate_dynamic_model,
    write_dynamic_model_results,
)
from harmo.dynamic_registration import (
    build_dynamic_registration_plan,
    build_registered_mechanics_plan,
    evaluate_dynamic_registration,
    write_dynamic_registration_results,
)
from harmo.dynamic_mechanics import (
    build_dynamic_mechanics_plan,
    evaluate_dynamic_mechanics,
    write_dynamic_mechanics_results,
)
from harmo.mechanics_audit import (
    build_mechanics_audit_plan,
    evaluate_mechanics_audit,
    write_mechanics_audit,
)
from harmo.mechanics_disposition import (
    build_mechanics_disposition,
    write_mechanics_disposition,
)
from harmo.physiology_control import run_public_physiology_control
from harmo.whole_body_balance import (
    build_whole_body_balance_plan,
    evaluate_whole_body_balance,
    write_whole_body_balance,
)
from harmo.mechanics_localization import (
    build_mechanics_localization_plan,
    evaluate_mechanics_localization,
    write_mechanics_localization,
)
from harmo.state_reconstruction import (
    build_state_reconstruction_plan,
    evaluate_state_reconstruction,
    write_state_reconstruction,
)
from harmo.moment_localization import (
    build_moment_localization_plan,
    evaluate_moment_localization,
    write_moment_localization,
)
from harmo.rra_sensitivity import (
    build_rra_sensitivity_plan,
    evaluate_rra_sensitivity,
    write_rra_sensitivity,
)
from harmo.structural_adequacy import (
    build_structural_adequacy_plan,
    evaluate_structural_adequacy,
    write_structural_adequacy,
)
from harmo.structural_contrast import (
    build_structural_contrast_plan,
    evaluate_structural_contrast,
    write_structural_contrast,
)
from harmo.structural_identifiability import (
    build_structural_identifiability_plan,
    evaluate_structural_identifiability,
    write_structural_identifiability,
)
from harmo.arm_sensitivity import (
    build_arm_sensitivity_plan,
    evaluate_arm_sensitivity,
    write_arm_sensitivity,
)
from harmo.dynamic_personalization import (
    build_hidden_evaluation_plan,
    build_personalization_plan,
    evaluate_hidden_subject,
    seal_fit_result,
    write_json_result,
)
from harmo.validation_corpus import load_validation_corpus
from harmo.validation_study import (
    compare_reproducibility_runs,
    compare_signal_records,
    create_reproducibility_snapshot,
    environment_record,
    fault_injection_benchmark,
    gate_audit,
    inventory_source_tree,
    load_registry,
    physiology_timing_metrics,
    subject_hierarchical_bootstrap,
    validate_adapter_metrics,
    write_json,
    write_rows,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Frozen empirical validation runner for the harmo-semg methods paper"
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parents[2]),
        help="repository root",
    )
    parser.add_argument(
        "--registry",
        default="paper/validation_registry.yaml",
        help="registry path, relative to root",
    )
    parser.add_argument(
        "--results",
        default="paper/results/validation",
        help="results directory, relative to root",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("validate", help="validate and freeze the experiment registry")

    inventory = commands.add_parser(
        "inventory", help="run source and mapping inventory"
    )
    inventory.add_argument("--container-root", default="/workspace/data")
    inventory.add_argument("--host-root", default="data")
    inventory.add_argument("--hash-files", action="store_true")

    commands.add_parser("faults", help="run the controlled eight-fault benchmark")

    snapshot = commands.add_parser(
        "repro-snapshot", help="write a deterministic contract-smoke run manifest"
    )
    snapshot.add_argument("--output", required=True)

    fidelity = commands.add_parser(
        "fidelity", help="compare two normalized signal records"
    )
    fidelity.add_argument("--reference", required=True)
    fidelity.add_argument("--candidate", required=True)
    fidelity.add_argument("--output", default=None)

    fidelity_corpus = commands.add_parser(
        "fidelity-corpus",
        help="run checksum-bound Study 31 ASCII/MAT/C3D fidelity validation",
    )
    fidelity_corpus.add_argument("--data-root", required=True)
    fidelity_corpus.add_argument("--repeats", type=int, default=2)
    fidelity_corpus.add_argument("--output", default=None)

    reproducibility = commands.add_parser(
        "reproducibility", help="compare manifests from independent reruns"
    )
    reproducibility.add_argument(
        "--confirmatory",
        action="store_true",
        help="enforce the frozen two-environment by three-run G5 design",
    )
    reproducibility.add_argument("manifests", nargs="+")

    opensim = commands.add_parser(
        "opensim", help="audit sources and assess the 336-trial OpenSim protocol"
    )
    opensim.add_argument("--data-root", required=True)
    opensim.add_argument("--validation-dir", default=None)
    opensim.add_argument(
        "--probe-container",
        default=None,
        help="Docker image to probe for digest, runtime OpenSim version, RRA, and Moco",
    )
    opensim.add_argument(
        "--probe-python",
        default=None,
        help="isolated Python executable to probe for OpenSim package identity and tools",
    )
    opensim.add_argument(
        "--source-manifest",
        default=None,
        help="verified static/model intake manifest, relative to repository root",
    )
    opensim.add_argument(
        "--source-bundle-root",
        default=None,
        help="artifact root for --source-manifest (defaults to the manifest directory)",
    )

    intake = commands.add_parser(
        "opensim-intake",
        help="validate the 16-subject static/model source bundle",
    )
    intake.add_argument("--manifest", required=True)
    intake.add_argument("--bundle-root", default=None)
    intake.add_argument("--output", default=None)

    legacy = commands.add_parser(
        "opensim-model-audit",
        help="inventory legacy scaled models and expose missing source lineage",
    )
    legacy.add_argument("--model-dir", default="osim/model")
    legacy.add_argument("--output", default=None)

    plan = commands.add_parser(
        "opensim-plan",
        help="build the fixed 336-row, checkpoint-aware G6 execution plan",
    )
    plan.add_argument("--source-manifest", required=True)
    plan.add_argument("--source-bundle-root", default=None)
    plan.add_argument(
        "--source-ledger",
        default="paper/results/validation/opensim_quality.json",
    )
    plan.add_argument("--execution-results", required=True)
    plan.add_argument("--run-id", required=True)
    plan.add_argument("--no-resume", action="store_true")
    plan.add_argument("--output", default=None)

    dynamic = commands.add_parser(
        "opensim-dynamic-calibration",
        help="run the checksum-bound G6b static-hidden calibration benchmark",
    )
    dynamic.add_argument(
        "--protocol",
        default="paper/g6b_dynamic_calibration_protocol.yaml",
    )
    dynamic.add_argument(
        "--source-manifest",
        default="paper/g6b_public_sources.yaml",
    )
    dynamic.add_argument("--data-root", required=True)
    dynamic.add_argument(
        "--download",
        action="store_true",
        help="download missing public artifacts before checksum verification",
    )
    dynamic.add_argument("--output", default=None)

    dynamic_model = commands.add_parser(
        "opensim-dynamic-model",
        help="synthesize and assess the leakage-resistant G6b-2 OpenSim model",
    )
    dynamic_model.add_argument(
        "--protocol",
        default="paper/g6b_dynamic_model_protocol.yaml",
    )
    dynamic_model.add_argument(
        "--source-manifest",
        default="paper/g6b_dynamic_model_sources.yaml",
    )
    dynamic_model.add_argument("--data-root", required=True)
    dynamic_model.add_argument("--opensim-python", required=True)
    dynamic_model.add_argument(
        "--download",
        action="store_true",
        help="download missing public artifacts before checksum verification",
    )
    dynamic_model.add_argument(
        "--work-dir",
        default=None,
        help="directory for the reference-free worker plan and report",
    )
    dynamic_model.add_argument(
        "--generated-model",
        default=None,
        help="derived model path (defaults under DATA_ROOT/derived)",
    )
    dynamic_model.add_argument("--output", default=None)

    mechanics = commands.add_parser(
        "opensim-dynamic-mechanics",
        help="run held-out IK, two-pass RRA, and ID for the G6b-3 model comparison",
    )
    mechanics.add_argument(
        "--protocol",
        default="paper/g6b_dynamic_mechanics_protocol.yaml",
    )
    mechanics.add_argument(
        "--source-manifest",
        default="paper/g6b_dynamic_mechanics_sources.yaml",
    )
    mechanics.add_argument("--data-root", required=True)
    mechanics.add_argument("--dynamic-model", required=True)
    mechanics.add_argument(
        "--g6b2-result",
        default="paper/results/validation/g6b_dynamic_model_pilot.json",
    )
    mechanics.add_argument("--opensim-python", required=True)
    mechanics.add_argument(
        "--download",
        action="store_true",
        help="download missing public artifacts before checksum verification",
    )
    mechanics.add_argument("--work-dir", required=True)
    mechanics.add_argument("--output", default=None)

    registration = commands.add_parser(
        "opensim-dynamic-registration",
        help="estimate Subject 02 marker locations from calibration trials only",
    )
    registration.add_argument(
        "--protocol",
        default="paper/g6b_dynamic_registration_protocol.yaml",
    )
    registration.add_argument(
        "--source-manifest",
        default="paper/g6b_dynamic_registration_sources.yaml",
    )
    registration.add_argument("--data-root", required=True)
    registration.add_argument("--base-model", required=True)
    registration.add_argument(
        "--g6b2-result",
        default="paper/results/validation/g6b_dynamic_model_pilot.json",
    )
    registration.add_argument(
        "--retained-g6b3-result",
        default="paper/results/validation/g6b_dynamic_mechanics_pilot.json",
    )
    registration.add_argument("--opensim-python", required=True)
    registration.add_argument("--work-dir", required=True)
    registration.add_argument("--registered-model", required=True)
    registration.add_argument("--output", default=None)
    registration.add_argument(
        "--download",
        action="store_true",
        help="download missing public calibration artifacts before verification",
    )

    registered_mechanics = commands.add_parser(
        "opensim-registered-mechanics",
        help="repeat the unchanged held-out mechanics contract with registered markers",
    )
    registered_mechanics.add_argument(
        "--wrapper-protocol",
        default="paper/g6b_registered_mechanics_protocol.yaml",
    )
    registered_mechanics.add_argument(
        "--baseline-protocol",
        default="paper/g6b_dynamic_mechanics_protocol.yaml",
    )
    registered_mechanics.add_argument(
        "--source-manifest",
        default="paper/g6b_dynamic_mechanics_sources.yaml",
    )
    registered_mechanics.add_argument("--data-root", required=True)
    registered_mechanics.add_argument("--base-model", required=True)
    registered_mechanics.add_argument("--registered-model", required=True)
    registered_mechanics.add_argument(
        "--registration-result",
        default="paper/results/validation/g6b_dynamic_registration_pilot.json",
    )
    registered_mechanics.add_argument(
        "--g6b2-result",
        default="paper/results/validation/g6b_dynamic_model_pilot.json",
    )
    registered_mechanics.add_argument("--opensim-python", required=True)
    registered_mechanics.add_argument("--work-dir", required=True)
    registered_mechanics.add_argument("--output", default=None)

    mechanics_audit = commands.add_parser(
        "opensim-mechanics-audit",
        help="run the G6c source-faithfulness, external-load, RRA, and ID audit",
    )
    mechanics_audit.add_argument(
        "--protocol", default="paper/g6c_mechanics_audit_protocol.yaml"
    )
    mechanics_audit.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    mechanics_audit.add_argument("--data-root", required=True)
    mechanics_audit.add_argument("--opensim-python", required=True)
    mechanics_audit.add_argument("--work-dir", required=True)
    mechanics_audit.add_argument("--output", default=None)

    whole_body_balance = commands.add_parser(
        "opensim-whole-body-balance-audit",
        help="run the G6c-S RRA-independent whole-body balance audit",
    )
    whole_body_balance.add_argument(
        "--protocol", default="paper/g6c_whole_body_balance_protocol.yaml"
    )
    whole_body_balance.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    whole_body_balance.add_argument(
        "--prior-g6c-result",
        default="paper/results/validation/g6c_mechanics_audit.json",
    )
    whole_body_balance.add_argument("--data-root", required=True)
    whole_body_balance.add_argument("--opensim-python", required=True)
    whole_body_balance.add_argument("--work-dir", required=True)
    whole_body_balance.add_argument("--download", action="store_true")
    whole_body_balance.add_argument("--output", default=None)

    mechanics_localization = commands.add_parser(
        "opensim-mechanics-localization-audit",
        help="run the G6c-S2 synchronization/kinematics/model localization",
    )
    mechanics_localization.add_argument(
        "--protocol", default="paper/g6c_s2_mechanics_localization_protocol.yaml"
    )
    mechanics_localization.add_argument(
        "--g6c-protocol", default="paper/g6c_mechanics_audit_protocol.yaml"
    )
    mechanics_localization.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    mechanics_localization.add_argument(
        "--prior-g6cs-result",
        default="paper/results/validation/g6c_whole_body_balance.json",
    )
    mechanics_localization.add_argument("--data-root", required=True)
    mechanics_localization.add_argument("--opensim-python", required=True)
    mechanics_localization.add_argument("--work-dir", required=True)
    mechanics_localization.add_argument("--download", action="store_true")
    mechanics_localization.add_argument("--output", default=None)

    state_reconstruction = commands.add_parser(
        "opensim-state-reconstruction-audit",
        help="run the G6c-S3 RRA-native state reconstruction audit",
    )
    state_reconstruction.add_argument(
        "--protocol", default="paper/g6c_s3_state_reconstruction_protocol.yaml"
    )
    state_reconstruction.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    state_reconstruction.add_argument(
        "--s2-result",
        default="paper/results/validation/g6c_s2_mechanics_localization.json",
    )
    state_reconstruction.add_argument("--s2-work-dir", required=True)
    state_reconstruction.add_argument("--data-root", required=True)
    state_reconstruction.add_argument("--opensim-python", required=True)
    state_reconstruction.add_argument("--work-dir", required=True)
    state_reconstruction.add_argument("--download", action="store_true")
    state_reconstruction.add_argument("--output", default=None)

    moment_localization = commands.add_parser(
        "opensim-moment-localization-audit",
        help="run the G6c-S4 residual-moment localization audit",
    )
    moment_localization.add_argument(
        "--protocol", default="paper/g6c_s4_moment_localization_protocol.yaml"
    )
    moment_localization.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    moment_localization.add_argument(
        "--s3-result",
        default="paper/results/validation/g6c_s3_state_reconstruction.json",
    )
    moment_localization.add_argument("--s3-work-dir", required=True)
    moment_localization.add_argument("--s2-work-dir", required=True)
    moment_localization.add_argument("--data-root", required=True)
    moment_localization.add_argument("--analysis-python", required=True)
    moment_localization.add_argument("--work-dir", required=True)
    moment_localization.add_argument("--download", action="store_true")
    moment_localization.add_argument("--output", default=None)

    rra_sensitivity = commands.add_parser(
        "opensim-rra-sensitivity-audit",
        help="run the G6c-S5 external-wrench/tracking RRA sensitivity audit",
    )
    rra_sensitivity.add_argument(
        "--protocol", default="paper/g6c_s5_rra_sensitivity_protocol.yaml"
    )
    rra_sensitivity.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    rra_sensitivity.add_argument(
        "--s2-result",
        default="paper/results/validation/g6c_s2_mechanics_localization.json",
    )
    rra_sensitivity.add_argument(
        "--s3-result",
        default="paper/results/validation/g6c_s3_state_reconstruction.json",
    )
    rra_sensitivity.add_argument(
        "--s4-result",
        default="paper/results/validation/g6c_s4_moment_localization.json",
    )
    rra_sensitivity.add_argument("--s2-work-dir", required=True)
    rra_sensitivity.add_argument("--data-root", required=True)
    rra_sensitivity.add_argument("--opensim-python", required=True)
    rra_sensitivity.add_argument("--work-dir", required=True)
    rra_sensitivity.add_argument("--download", action="store_true")
    rra_sensitivity.add_argument("--output", default=None)

    structural_adequacy = commands.add_parser(
        "opensim-structural-adequacy-audit",
        help="run the G6c-S6 structural-adequacy audit",
    )
    structural_adequacy.add_argument(
        "--protocol", default="paper/g6c_s6_structural_adequacy_protocol.yaml"
    )
    structural_adequacy.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    structural_adequacy.add_argument(
        "--s2-result",
        default="paper/results/validation/g6c_s2_mechanics_localization.json",
    )
    structural_adequacy.add_argument(
        "--s5-result",
        default="paper/results/validation/g6c_s5_rra_sensitivity.json",
    )
    structural_adequacy.add_argument("--s2-work-dir", required=True)
    structural_adequacy.add_argument("--data-root", required=True)
    structural_adequacy.add_argument(
        "--study1-c3d",
        default="studies/study_1/notebook/Ref01_sss_le_shoes05.c3d",
    )
    structural_adequacy.add_argument(
        "--study1-subject-info",
        default="studies/study_1/definitions/subject_info.csv",
    )
    structural_adequacy.add_argument("--analysis-python", required=True)
    structural_adequacy.add_argument("--work-dir", required=True)
    structural_adequacy.add_argument("--download", action="store_true")
    structural_adequacy.add_argument("--output", default=None)

    structural_contrast = commands.add_parser(
        "opensim-structural-counterfactual-audit",
        help="run the G6c-S6b paired upper-limb structural counterfactual",
    )
    structural_contrast.add_argument(
        "--protocol", default="paper/g6c_s6b_structural_contrast_protocol.yaml"
    )
    structural_contrast.add_argument(
        "--s6-result",
        default="paper/results/validation/g6c_s6_structural_adequacy.json",
    )
    structural_contrast.add_argument(
        "--study1-c3d",
        default="studies/study_1/notebook/Ref01_sss_le_shoes05.c3d",
    )
    structural_contrast.add_argument(
        "--study1-subject-info",
        default="studies/study_1/definitions/subject_info.csv",
    )
    structural_contrast.add_argument("--analysis-python", required=True)
    structural_contrast.add_argument("--work-dir", required=True)
    structural_contrast.add_argument("--output", default=None)

    structural_identifiability = commands.add_parser(
        "opensim-structural-identifiability-audit",
        help="run the G6c-S6c subject/activity-matched identifiability preflight",
    )
    structural_identifiability.add_argument(
        "--protocol",
        default="paper/g6c_s6c_structural_identifiability_protocol.yaml",
    )
    structural_identifiability.add_argument(
        "--source-manifest", default="paper/g6c_mechanics_audit_sources.yaml"
    )
    structural_identifiability.add_argument(
        "--s2-result",
        default="paper/results/validation/g6c_s2_mechanics_localization.json",
    )
    structural_identifiability.add_argument(
        "--s6-result",
        default="paper/results/validation/g6c_s6_structural_adequacy.json",
    )
    structural_identifiability.add_argument(
        "--s6b-result",
        default="paper/results/validation/g6c_s6b_structural_contrast.json",
    )
    structural_identifiability.add_argument("--data-root", required=True)
    structural_identifiability.add_argument("--analysis-python", required=True)
    structural_identifiability.add_argument("--work-dir", required=True)
    structural_identifiability.add_argument("--download", action="store_true")
    structural_identifiability.add_argument("--output", default=None)

    arm_sensitivity = commands.add_parser(
        "opensim-bounded-arm-sensitivity-audit",
        help="run the exploratory G6c-S6d bounded harmonic sensitivity",
    )
    arm_sensitivity.add_argument(
        "--protocol",
        default="paper/g6c_s6d_bounded_arm_sensitivity_protocol.yaml",
    )
    arm_sensitivity.add_argument(
        "--s6-result",
        default="paper/results/validation/g6c_s6_structural_adequacy.json",
    )
    arm_sensitivity.add_argument(
        "--s6b-result",
        default="paper/results/validation/g6c_s6b_structural_contrast.json",
    )
    arm_sensitivity.add_argument(
        "--s6c-result",
        default=(
            "paper/results/validation/"
            "g6c_s6c_structural_identifiability.json"
        ),
    )
    arm_sensitivity.add_argument("--s6-work-dir", required=True)
    arm_sensitivity.add_argument("--analysis-python", required=True)
    arm_sensitivity.add_argument("--work-dir", required=True)
    arm_sensitivity.add_argument("--output", default=None)

    personalization = commands.add_parser(
        "opensim-dynamic-personalization",
        help="run leakage-resistant G6d/G6e dynamic-only personalization",
    )
    personalization.add_argument(
        "--protocol", default="paper/g6_dynamic_personalization_protocol.yaml"
    )
    personalization.add_argument("--inventory", required=True)
    personalization.add_argument("--data-root", required=True)
    personalization.add_argument(
        "--split", choices=("development", "blind", "study31"), required=True
    )
    personalization.add_argument("--subject-id", required=True)
    personalization.add_argument("--opensim-python", required=True)
    personalization.add_argument("--work-dir", required=True)
    personalization.add_argument("--output-model", required=True)
    personalization.add_argument(
        "--engine", choices=("auto", "addbiomechanics", "harmo-native"), default="auto"
    )
    personalization.add_argument("--plan-only", action="store_true")
    personalization.add_argument(
        "--hidden-metrics",
        default=None,
        help="metrics JSON produced by the separately executed hidden evaluator",
    )
    personalization.add_argument("--output", default=None)

    bootstrap = commands.add_parser("bootstrap", help="subject-level bootstrap")
    bootstrap.add_argument("--input", required=True)
    bootstrap.add_argument("--value", required=True)
    bootstrap.add_argument("--subject", default="subject_id")
    bootstrap.add_argument("--output", required=True)

    disposition = commands.add_parser(
        "opensim-mechanics-disposition",
        help="bind the completed negative G6 evidence and assign release states",
    )
    disposition.add_argument(
        "--policy",
        default="paper/g6_mechanics_disposition_policy.yaml",
    )
    disposition.add_argument(
        "--output",
        default="paper/results/validation/g6_mechanics_disposition.json",
    )

    physiology = commands.add_parser(
        "physiology", help="derive timing-only cross-study gait controls"
    )
    physiology.add_argument("--input", required=True)

    physiology_public = commands.add_parser(
        "physiology-public",
        help="run the frozen G3 positive control from checksum-verified public data",
    )
    physiology_public.add_argument("--study38-dir", required=True)
    physiology_public.add_argument("--gait120-archive", required=True)
    physiology_public.add_argument(
        "--protocol",
        default="paper/physiology_positive_control_protocol.yaml",
    )

    adapter = commands.add_parser(
        "adapter", help="validate and freeze held-out adapter metrics"
    )
    adapter.add_argument("--input", required=True)

    commands.add_parser("audit", help="audit G3-G7 result completeness")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    registry_path = Path(args.registry)
    if not registry_path.is_absolute():
        registry_path = root / registry_path
    results = Path(args.results)
    if not results.is_absolute():
        results = root / results
    registry = load_registry(registry_path)

    if args.command == "validate":
        payload = {
            "registry": str(registry_path),
            "environment": environment_record(),
            "status": "valid",
        }
        write_json(results / "registry_validation.json", payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "inventory":
        sources, mappings = inventory_source_tree(
            root,
            registry,
            Path(args.container_root),
            Path(args.host_root),
            args.hash_files,
        )
        source_outputs = write_rows(results / "source_inventory.json", sources)
        mapping_outputs = write_rows(results / "mapping_outcomes.json", mappings)
        payload = {
            "source_files": len(sources),
            "mapping_count": len(mappings),
            "matched_mappings": sum(row["matched_files"] > 0 for row in mappings),
            "source_outputs": source_outputs,
            "mapping_outputs": mapping_outputs,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        expected = int(registry["experiments"]["A"]["denominator"])
        return 0 if len(mappings) == expected else 1

    if args.command == "faults":
        payload = fault_injection_benchmark()
        write_json(results / "fault_detection.json", payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["pass"] else 1

    if args.command == "repro-snapshot":
        payload = create_reproducibility_snapshot(registry)
        write_json(Path(args.output), payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "fidelity":
        reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
        candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        payload = compare_signal_records(reference, candidate)
        output = Path(args.output) if args.output else results / "format_fidelity.json"
        write_json(output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["pass"] else 1

    if args.command == "fidelity-corpus":
        corpus = load_validation_corpus(
            root / "paper/validation_corpus.yaml", root, registry
        )
        payload = run_study31_format_fidelity(
            root,
            registry,
            corpus,
            Path(args.data_root).resolve(),
            repeats=args.repeats,
        )
        output = Path(args.output) if args.output else results / "format_fidelity.json"
        write_json(output, payload)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "denominator": payload["denominator"],
                    "reproducibility": payload["reproducibility"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 1

    if args.command == "reproducibility":
        experiment = registry["experiments"]["D"]
        payload = compare_reproducibility_runs(
            [Path(value) for value in args.manifests],
            expected_environments=(
                experiment["environments"] if args.confirmatory else None
            ),
            reruns_per_environment=(
                int(experiment["reruns_per_environment"]) if args.confirmatory else None
            ),
        )
        write_json(results / "reproducibility.json", payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["pass"] else 1

    if args.command == "opensim-intake":
        manifest_path = Path(args.manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        bundle_root = (
            Path(args.bundle_root)
            if args.bundle_root
            else manifest_path.parent
        )
        if not bundle_root.is_absolute():
            bundle_root = root / bundle_root
        payload = validate_opensim_source_bundle(
            manifest_path,
            bundle_root,
            registry["experiments"]["E"]["subjects"],
        )
        output = (
            Path(args.output)
            if args.output
            else results / "g6_source_intake.json"
        )
        write_json(output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["pass"] else 1

    if args.command == "opensim-model-audit":
        model_dir = Path(args.model_dir)
        if not model_dir.is_absolute():
            model_dir = root / model_dir
        payload = audit_legacy_scaled_models(
            model_dir, registry["experiments"]["E"]["subjects"]
        )
        output = (
            Path(args.output)
            if args.output
            else results / "g6_local_model_audit.json"
        )
        write_json(output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["pass"] else 1

    if args.command == "opensim-plan":
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        bundle_root = Path(args.source_bundle_root or manifest_path.parent)
        if not bundle_root.is_absolute():
            bundle_root = root / bundle_root
        source_ledger = Path(args.source_ledger)
        if not source_ledger.is_absolute():
            source_ledger = root / source_ledger
        execution_results = Path(args.execution_results)
        if not execution_results.is_absolute():
            execution_results = root / execution_results
        audit = validate_opensim_source_bundle(
            manifest_path,
            bundle_root,
            registry["experiments"]["E"]["subjects"],
        )
        ledger = json.loads(source_ledger.read_text(encoding="utf-8"))
        payload = build_execution_plan(
            registry,
            ledger,
            audit,
            execution_results,
            args.run_id,
            resume=not args.no_resume,
        )
        output = Path(args.output) if args.output else results / "g6_execution_plan.json"
        write_json(output, payload)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "counts": payload["counts"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 1

    if args.command == "opensim-dynamic-calibration":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            source_audit = download_public_source_bundle(manifest_path, data_root)
        else:
            source_audit = validate_public_source_bundle(manifest_path, data_root)
        if not source_audit["pass"]:
            raise ValueError("G6b public source bundle failed verification")
        payload = run_dynamic_calibration_benchmark(
            protocol_path, manifest_path, data_root
        )
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6b_dynamic_calibration_pilot.json"
        )
        if not output.is_absolute():
            output = root / output
        write_dynamic_calibration_results(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "summary": payload["summary"],
                    "claim_boundary": payload["scientific_claim_boundary"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 1

    if args.command == "opensim-mechanics-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_mechanics_audit_plan(
            protocol_path, manifest_path, Path(args.data_root).resolve()
        )
        plan_path = work_dir / "g6c_mechanics_audit_plan.json"
        report_path = work_dir / "g6c_mechanics_audit_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(root / "scripts/paper/run_mechanics_audit_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_mechanics_audit(plan, worker)
        payload["environment"] = environment_record()
        output = Path(args.output) if args.output else results / "g6c_mechanics_audit.json"
        if not output.is_absolute():
            output = root / output
        write_mechanics_audit(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "checks": payload["checks"],
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 1

    if args.command == "opensim-whole-body-balance-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        prior_path = Path(args.prior_g6c_result)
        if not prior_path.is_absolute():
            prior_path = root / prior_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            download_public_source_bundle(manifest_path, data_root)
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_whole_body_balance_plan(
            protocol_path, manifest_path, data_root, prior_path
        )
        plan_path = work_dir / "g6c_s_whole_body_balance_plan.json"
        report_path = work_dir / "g6c_s_whole_body_balance_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(root / "scripts/paper/run_whole_body_balance_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_whole_body_balance(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_whole_body_balance.json"
        )
        if not output.is_absolute():
            output = root / output
        write_whole_body_balance(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "classification": payload["diagnosis"].get("classification"),
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-mechanics-localization-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        g6c_protocol_path = Path(args.g6c_protocol)
        if not g6c_protocol_path.is_absolute():
            g6c_protocol_path = root / g6c_protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        prior_path = Path(args.prior_g6cs_result)
        if not prior_path.is_absolute():
            prior_path = root / prior_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            download_public_source_bundle(manifest_path, data_root)
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_mechanics_localization_plan(
            protocol_path,
            g6c_protocol_path,
            manifest_path,
            data_root,
            prior_path,
        )
        plan_path = work_dir / "g6c_s2_mechanics_localization_plan.json"
        report_path = work_dir / "g6c_s2_mechanics_localization_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(root / "scripts/paper/run_mechanics_localization_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_mechanics_localization(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_s2_mechanics_localization.json"
        )
        if not output.is_absolute():
            output = root / output
        write_mechanics_localization(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "cause_summary": worker.get("cause_summary"),
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-state-reconstruction-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        s2_result_path = Path(args.s2_result)
        if not s2_result_path.is_absolute():
            s2_result_path = root / s2_result_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            download_public_source_bundle(manifest_path, data_root)
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_state_reconstruction_plan(
            protocol_path,
            manifest_path,
            data_root,
            s2_result_path,
            Path(args.s2_work_dir).resolve(),
        )
        plan_path = work_dir / "g6c_s3_state_reconstruction_plan.json"
        report_path = work_dir / "g6c_s3_state_reconstruction_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(root / "scripts/paper/run_state_reconstruction_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_state_reconstruction(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_s3_state_reconstruction.json"
        )
        if not output.is_absolute():
            output = root / output
        write_state_reconstruction(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "classification": (worker.get("diagnosis") or {}).get(
                        "classification"
                    ),
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-moment-localization-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        s3_result_path = Path(args.s3_result)
        if not s3_result_path.is_absolute():
            s3_result_path = root / s3_result_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            download_public_source_bundle(manifest_path, data_root)
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_moment_localization_plan(
            protocol_path,
            manifest_path,
            data_root,
            s3_result_path,
            Path(args.s3_work_dir).resolve(),
            Path(args.s2_work_dir).resolve(),
        )
        plan_path = work_dir / "g6c_s4_moment_localization_plan.json"
        report_path = work_dir / "g6c_s4_moment_localization_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.analysis_python).resolve()),
                str(root / "scripts/paper/run_moment_localization_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_moment_localization(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_s4_moment_localization.json"
        )
        if not output.is_absolute():
            output = root / output
        write_moment_localization(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "classification": (worker.get("diagnosis") or {}).get(
                        "classification"
                    ),
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-rra-sensitivity-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        s2_result_path = Path(args.s2_result)
        if not s2_result_path.is_absolute():
            s2_result_path = root / s2_result_path
        s3_result_path = Path(args.s3_result)
        if not s3_result_path.is_absolute():
            s3_result_path = root / s3_result_path
        s4_result_path = Path(args.s4_result)
        if not s4_result_path.is_absolute():
            s4_result_path = root / s4_result_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            download_public_source_bundle(manifest_path, data_root)
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_rra_sensitivity_plan(
            protocol_path,
            manifest_path,
            data_root,
            s2_result_path,
            s3_result_path,
            s4_result_path,
            Path(args.s2_work_dir).resolve(),
        )
        plan_path = work_dir / "g6c_s5_rra_sensitivity_plan.json"
        report_path = work_dir / "g6c_s5_rra_sensitivity_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(root / "scripts/paper/run_rra_sensitivity_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_rra_sensitivity(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_s5_rra_sensitivity.json"
        )
        if not output.is_absolute():
            output = root / output
        write_rra_sensitivity(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "classification": (worker.get("diagnosis") or {}).get(
                        "classification"
                    ),
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-structural-adequacy-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        s2_result_path = Path(args.s2_result)
        if not s2_result_path.is_absolute():
            s2_result_path = root / s2_result_path
        s5_result_path = Path(args.s5_result)
        if not s5_result_path.is_absolute():
            s5_result_path = root / s5_result_path
        study1_c3d_path = Path(args.study1_c3d)
        if not study1_c3d_path.is_absolute():
            study1_c3d_path = root / study1_c3d_path
        subject_info_path = Path(args.study1_subject_info)
        if not subject_info_path.is_absolute():
            subject_info_path = root / subject_info_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            download_public_source_bundle(manifest_path, data_root)
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_structural_adequacy_plan(
            protocol_path,
            manifest_path,
            data_root,
            s2_result_path,
            s5_result_path,
            Path(args.s2_work_dir).resolve(),
            study1_c3d_path,
            subject_info_path,
        )
        plan_path = work_dir / "g6c_s6_structural_adequacy_plan.json"
        report_path = work_dir / "g6c_s6_structural_adequacy_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.analysis_python).resolve()),
                str(root / "scripts/paper/run_structural_adequacy_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_structural_adequacy(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_s6_structural_adequacy.json"
        )
        if not output.is_absolute():
            output = root / output
        write_structural_adequacy(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "classification": (worker.get("diagnosis") or {}).get(
                        "classification"
                    ),
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-structural-counterfactual-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        s6_result_path = Path(args.s6_result)
        if not s6_result_path.is_absolute():
            s6_result_path = root / s6_result_path
        study1_c3d_path = Path(args.study1_c3d)
        if not study1_c3d_path.is_absolute():
            study1_c3d_path = root / study1_c3d_path
        subject_info_path = Path(args.study1_subject_info)
        if not subject_info_path.is_absolute():
            subject_info_path = root / subject_info_path
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_structural_contrast_plan(
            protocol_path,
            s6_result_path,
            study1_c3d_path,
            subject_info_path,
        )
        plan_path = work_dir / "g6c_s6b_structural_contrast_plan.json"
        report_path = work_dir / "g6c_s6b_structural_contrast_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.analysis_python).resolve()),
                str(root / "scripts/paper/run_structural_contrast_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_structural_contrast(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_s6b_structural_contrast.json"
        )
        if not output.is_absolute():
            output = root / output
        write_structural_contrast(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "counterfactual_valid": payload["counterfactual_valid"],
                    "classification": (worker.get("diagnosis") or {}).get(
                        "classification"
                    ),
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-structural-identifiability-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        s2_result_path = Path(args.s2_result)
        if not s2_result_path.is_absolute():
            s2_result_path = root / s2_result_path
        s6_result_path = Path(args.s6_result)
        if not s6_result_path.is_absolute():
            s6_result_path = root / s6_result_path
        s6b_result_path = Path(args.s6b_result)
        if not s6b_result_path.is_absolute():
            s6b_result_path = root / s6b_result_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            download_public_source_bundle(manifest_path, data_root)
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_structural_identifiability_plan(
            protocol_path,
            manifest_path,
            data_root,
            s2_result_path,
            s6_result_path,
            s6b_result_path,
        )
        plan_path = work_dir / "g6c_s6c_structural_identifiability_plan.json"
        report_path = (
            work_dir / "g6c_s6c_structural_identifiability_worker.json"
        )
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.analysis_python).resolve()),
                str(
                    root
                    / "scripts/paper/run_structural_identifiability_worker.py"
                ),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_structural_identifiability(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_s6c_structural_identifiability.json"
        )
        if not output.is_absolute():
            output = root / output
        write_structural_identifiability(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "full_body_rra_authorized": payload[
                        "full_body_rra_authorized"
                    ],
                    "classification": (worker.get("diagnosis") or {}).get(
                        "classification"
                    ),
                    "causal_next_gate": payload["causal_next_gate"],
                    "diagnostic_next_gate": payload["diagnostic_next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-bounded-arm-sensitivity-audit":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        s6_result_path = Path(args.s6_result)
        if not s6_result_path.is_absolute():
            s6_result_path = root / s6_result_path
        s6b_result_path = Path(args.s6b_result)
        if not s6b_result_path.is_absolute():
            s6b_result_path = root / s6b_result_path
        s6c_result_path = Path(args.s6c_result)
        if not s6c_result_path.is_absolute():
            s6c_result_path = root / s6c_result_path
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_arm_sensitivity_plan(
            protocol_path,
            s6_result_path,
            s6b_result_path,
            s6c_result_path,
            Path(args.s6_work_dir).resolve(),
        )
        plan_path = work_dir / "g6c_s6d_bounded_arm_sensitivity_plan.json"
        report_path = work_dir / "g6c_s6d_bounded_arm_sensitivity_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.analysis_python).resolve()),
                str(root / "scripts/paper/run_arm_sensitivity_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(report_path),
            ],
            check=True,
        )
        worker = json.loads(report_path.read_text(encoding="utf-8"))
        payload = evaluate_arm_sensitivity(plan, worker)
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6c_s6d_bounded_arm_sensitivity.json"
        )
        if not output.is_absolute():
            output = root / output
        write_arm_sensitivity(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "diagnostic_complete": payload["diagnostic_complete"],
                    "descriptive_pattern": (
                        (worker.get("diagnosis") or {}).get(
                            "descriptive_pattern"
                        )
                    ),
                    "causal_structural_attribution": payload[
                        "causal_structural_attribution"
                    ],
                    "next_gate": payload["next_gate"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["diagnostic_complete"] else 1

    if args.command == "opensim-dynamic-personalization":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        inventory_path = Path(args.inventory)
        if not inventory_path.is_absolute():
            inventory_path = root / inventory_path
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_personalization_plan(
            protocol_path,
            inventory_path,
            Path(args.data_root).resolve(),
            subject_id=args.subject_id,
            split=args.split,
        )
        plan_path = work_dir / "g6d_personalization_fit_plan.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if args.plan_only:
            print(json.dumps({"plan": str(plan_path), "plan_sha256": plan["plan_sha256"]}, indent=2))
            return 0
        worker_path = work_dir / "g6d_personalization_fit_worker.json"
        candidate_model = Path(args.output_model).resolve()
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(root / "scripts/paper/run_dynamic_personalization_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--output-model",
                str(candidate_model),
                "--report",
                str(worker_path),
                "--engine",
                args.engine,
            ],
            check=True,
        )
        worker = json.loads(worker_path.read_text(encoding="utf-8"))
        seal = seal_fit_result(plan, worker, candidate_model)
        seal_path = work_dir / "g6d_personalization_seal.json"
        write_json_result(seal, seal_path)
        payload = {
            "schema_version": "1.0.0",
            "scope": "g6d-dynamic-personalization",
            "subject_id": plan["subject_id"],
            "split": plan["split"],
            "fit_complete": True,
            "hidden_evaluation_complete": False,
            "worker": worker,
            "seal": seal,
            "pass": False,
            "claim_boundary": "fit completed; dynamic-only validation requires hidden evaluation",
        }
        if args.hidden_metrics:
            hidden_plan = build_hidden_evaluation_plan(
                protocol_path,
                inventory_path,
                Path(args.data_root).resolve(),
                seal,
                candidate_model,
            )
            hidden_plan_path = work_dir / "g6e_hidden_evaluation_plan.json"
            write_json_result(hidden_plan, hidden_plan_path)
            metrics = json.loads(Path(args.hidden_metrics).read_text(encoding="utf-8"))
            hidden_result = evaluate_hidden_subject(hidden_plan, metrics)
            payload.update(
                {
                    "hidden_evaluation_complete": True,
                    "hidden_evaluation": hidden_result,
                    "pass": hidden_result["pass"],
                    "claim_boundary": "single-subject hidden evaluation only",
                }
            )
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6d_{}_subject_{}.json".format(args.split, plan["subject_id"])
        )
        if not output.is_absolute():
            output = root / output
        write_json_result(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "candidate_model": str(candidate_model),
                    "seal": str(seal_path),
                    "fit_complete": True,
                    "hidden_evaluation_complete": payload["hidden_evaluation_complete"],
                    "pass": payload["pass"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else (0 if not args.hidden_metrics else 1)

    if args.command == "opensim-dynamic-model":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            source_audit = download_public_source_bundle(manifest_path, data_root)
        else:
            source_audit = validate_public_source_bundle(manifest_path, data_root)
        if not source_audit["pass"]:
            raise ValueError("G6b-2 public source bundle failed verification")

        plan = build_dynamic_model_plan(protocol_path, manifest_path, data_root)
        work_dir = Path(args.work_dir).resolve() if args.work_dir else data_root / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        plan_path = work_dir / "g6b_dynamic_model_plan.json"
        worker_report_path = work_dir / "g6b_dynamic_model_worker.json"
        generated_model = (
            Path(args.generated_model).resolve()
            if args.generated_model
            else data_root / "derived" / "Sub_02_dynamic_derived.osim"
        )
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        worker_script = root / "scripts/paper/run_dynamic_model_worker.py"
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(worker_script),
                "--plan",
                str(plan_path),
                "--output-model",
                str(generated_model),
                "--report",
                str(worker_report_path),
            ],
            check=True,
        )
        worker_report = json.loads(worker_report_path.read_text(encoding="utf-8"))
        if worker_report.get("hidden_reference_available_to_worker") is not False:
            raise ValueError("G6b-2 worker did not attest reference isolation")
        if worker_report.get("template_model_sha256") != plan["template_model_sha256"]:
            raise ValueError("G6b-2 worker used an unexpected template model")
        if worker_report.get("output_model_sha256") != sha256_file(generated_model):
            raise ValueError("G6b-2 generated model changed after worker completion")
        expected_applied = {
            body: [float(factor)] * 3
            for body, factor in plan["body_scale_factors"].items()
        }
        if worker_report.get("applied_body_scale_factors") != expected_applied:
            raise ValueError("G6b-2 worker applied unexpected scale factors")

        post_audit = validate_public_source_bundle(manifest_path, data_root)
        if not post_audit["pass"]:
            raise ValueError("G6b-2 source bundle changed during model synthesis")
        reference_role = str(plan["hidden_reference_role"])
        protocol = yaml.safe_load(protocol_path.read_text(encoding="utf-8")) or {}
        payload = evaluate_dynamic_model(
            plan,
            generated_model,
            post_audit["assets_by_role"][reference_role],
            protocol.get("thresholds") or {},
        )
        payload["worker"] = worker_report
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6b_dynamic_model_pilot.json"
        )
        if not output.is_absolute():
            output = root / output
        write_dynamic_model_results(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "generated_model": str(generated_model),
                    "pass": payload["pass"],
                    "summary": payload["summary"],
                    "claim_boundary": payload["scientific_claim_boundary"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 1

    if args.command == "opensim-dynamic-mechanics":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        g6b2_result_path = Path(args.g6b2_result)
        if not g6b2_result_path.is_absolute():
            g6b2_result_path = root / g6b2_result_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            source_audit = download_public_source_bundle(manifest_path, data_root)
        else:
            source_audit = validate_public_source_bundle(manifest_path, data_root)
        if not source_audit["pass"]:
            raise ValueError("G6b-3 public source bundle failed verification")
        plan = build_dynamic_mechanics_plan(
            protocol_path,
            manifest_path,
            data_root,
            Path(args.dynamic_model),
            g6b2_result_path,
        )
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan_path = work_dir / "g6b_dynamic_mechanics_plan.json"
        worker_report_path = work_dir / "g6b_dynamic_mechanics_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        worker_script = root / "scripts/paper/run_dynamic_mechanics_worker.py"
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(worker_script),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(worker_report_path),
            ],
            check=True,
        )
        worker_report = json.loads(worker_report_path.read_text(encoding="utf-8"))
        payload = evaluate_dynamic_mechanics(
            protocol_path, plan, worker_report, work_dir
        )
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6b_dynamic_mechanics_pilot.json"
        )
        if not output.is_absolute():
            output = root / output
        write_dynamic_mechanics_results(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "summary": payload.get("summary"),
                    "worker": payload["worker"],
                    "claim_boundary": payload["scientific_claim_boundary"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 1

    if args.command == "opensim-dynamic-registration":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        g6b2_result_path = Path(args.g6b2_result)
        if not g6b2_result_path.is_absolute():
            g6b2_result_path = root / g6b2_result_path
        retained_result_path = Path(args.retained_g6b3_result)
        if not retained_result_path.is_absolute():
            retained_result_path = root / retained_result_path
        data_root = Path(args.data_root).resolve()
        if args.download:
            source_audit = download_public_source_bundle(manifest_path, data_root)
        else:
            source_audit = validate_public_source_bundle(manifest_path, data_root)
        if not source_audit["pass"]:
            raise ValueError("G6b-3R public calibration bundle failed verification")
        plan = build_dynamic_registration_plan(
            protocol_path,
            manifest_path,
            data_root,
            Path(args.base_model),
            g6b2_result_path,
            retained_result_path,
        )
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        registered_model = Path(args.registered_model).resolve()
        plan_path = work_dir / "g6b_dynamic_registration_plan.json"
        worker_report_path = work_dir / "g6b_dynamic_registration_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(root / "scripts/paper/run_dynamic_registration_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--output-model",
                str(registered_model),
                "--report",
                str(worker_report_path),
            ],
            check=True,
        )
        worker_report = json.loads(worker_report_path.read_text(encoding="utf-8"))
        payload = evaluate_dynamic_registration(
            protocol_path, plan, worker_report, registered_model
        )
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6b_dynamic_registration_pilot.json"
        )
        if not output.is_absolute():
            output = root / output
        write_dynamic_registration_results(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "registered_model": str(registered_model),
                    "pass": payload["pass"],
                    "summary": payload["summary"],
                    "claim_boundary": payload["scientific_claim_boundary"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 1

    if args.command == "opensim-registered-mechanics":
        wrapper_protocol_path = Path(args.wrapper_protocol)
        if not wrapper_protocol_path.is_absolute():
            wrapper_protocol_path = root / wrapper_protocol_path
        baseline_protocol_path = Path(args.baseline_protocol)
        if not baseline_protocol_path.is_absolute():
            baseline_protocol_path = root / baseline_protocol_path
        manifest_path = Path(args.source_manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        registration_result_path = Path(args.registration_result)
        if not registration_result_path.is_absolute():
            registration_result_path = root / registration_result_path
        g6b2_result_path = Path(args.g6b2_result)
        if not g6b2_result_path.is_absolute():
            g6b2_result_path = root / g6b2_result_path
        work_dir = Path(args.work_dir).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        plan = build_registered_mechanics_plan(
            wrapper_protocol_path,
            baseline_protocol_path,
            manifest_path,
            Path(args.data_root).resolve(),
            Path(args.base_model),
            Path(args.registered_model),
            g6b2_result_path,
            registration_result_path,
        )
        plan_path = work_dir / "g6b_registered_mechanics_plan.json"
        worker_report_path = work_dir / "g6b_registered_mechanics_worker.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(
            [
                str(Path(args.opensim_python).resolve()),
                str(root / "scripts/paper/run_dynamic_mechanics_worker.py"),
                "--plan",
                str(plan_path),
                "--work-dir",
                str(work_dir),
                "--report",
                str(worker_report_path),
            ],
            check=True,
        )
        worker_report = json.loads(worker_report_path.read_text(encoding="utf-8"))
        payload = evaluate_dynamic_mechanics(
            baseline_protocol_path, plan, worker_report, work_dir
        )
        payload["environment"] = environment_record()
        output = (
            Path(args.output)
            if args.output
            else results / "g6b_registered_mechanics_exploratory.json"
        )
        if not output.is_absolute():
            output = root / output
        write_dynamic_mechanics_results(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "pass": payload["pass"],
                    "summary": payload.get("summary"),
                    "claim_boundary": payload["scientific_claim_boundary"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 1

    if args.command == "opensim":
        if args.probe_container and args.probe_python:
            raise ValueError("choose only one of --probe-container and --probe-python")
        validation_dir = Path(args.validation_dir) if args.validation_dir else None
        paths = (
            sorted(validation_dir.rglob("*.json"))
            if validation_dir is not None and validation_dir.is_dir()
            else []
        )
        if args.probe_container:
            runtime = probe_opensim_container(args.probe_container)
        elif args.probe_python:
            runtime = probe_opensim_python(Path(args.probe_python))
        else:
            runtime = {"probe_status": "not-run"}
        source_audit = None
        if args.source_manifest:
            source_manifest = Path(args.source_manifest)
            if not source_manifest.is_absolute():
                source_manifest = root / source_manifest
            source_bundle_root = (
                Path(args.source_bundle_root)
                if args.source_bundle_root
                else source_manifest.parent
            )
            if not source_bundle_root.is_absolute():
                source_bundle_root = root / source_bundle_root
            source_audit = validate_opensim_source_bundle(
                source_manifest,
                source_bundle_root,
                registry["experiments"]["E"]["subjects"],
            )
            write_json(results / "g6_source_intake.json", source_audit)
        rows, summary = run_study31_opensim_quality(
            root,
            registry,
            Path(args.data_root).resolve(),
            paths,
            runtime,
            source_audit,
        )
        outputs = write_rows(results / "opensim_quality.json", rows)
        write_json(results / "opensim_quality_summary.json", summary)
        payload = {
            "denominator": len(rows),
            "observed": sum(row["source_validation"] is not None for row in rows),
            "source_denominator": summary["source_denominator"],
            "analysis_denominator": summary["analysis_denominator"],
            "audit_complete": summary["audit_complete"],
            "pass": summary["pass"],
            "outputs": outputs,
            "summary": str(results / "opensim_quality_summary.json"),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if summary["pass"] else 1

    if args.command == "opensim-mechanics-disposition":
        policy_path = Path(args.policy)
        if not policy_path.is_absolute():
            policy_path = root / policy_path
        output = Path(args.output)
        if not output.is_absolute():
            output = root / output
        payload = build_mechanics_disposition(policy_path, results, root)
        write_mechanics_disposition(payload, output)
        print(
            json.dumps(
                {
                    "output": str(output),
                    "reporting_complete": payload["reporting_complete"],
                    "scientific_endpoint_pass": payload[
                        "scientific_endpoint_pass"
                    ],
                    "mechanics_readiness": payload["mechanics_readiness"],
                    "default_mechanics_export": payload[
                        "default_mechanics_export"
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["reporting_complete"] else 1

    if args.command == "bootstrap":
        rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
        bootstrap = registry["bootstrap"]
        payload = subject_hierarchical_bootstrap(
            rows,
            args.value,
            args.subject,
            int(bootstrap["iterations"]),
            int(bootstrap["seed"]),
        )
        write_json(Path(args.output), payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "physiology":
        rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
        payload = physiology_timing_metrics(rows, registry)
        write_json(results / "physiology_positive_control.json", payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "physiology-public":
        protocol_path = Path(args.protocol)
        if not protocol_path.is_absolute():
            protocol_path = root / protocol_path
        payload = run_public_physiology_control(
            Path(args.study38_dir).resolve(),
            Path(args.gait120_archive).resolve(),
            protocol_path,
        )
        write_json(results / "physiology_positive_control.json", payload)
        result_path = results / "physiology_positive_control.json"
        run_record = {
            "schema_version": "1.0",
            "experiment": "G cross-study gait physiological positive control",
            "environment": environment_record(),
            "protocol_path": str(protocol_path),
            "protocol_sha256": payload.get("protocol_sha256"),
            "result_path": str(result_path),
            "result_file_sha256": sha256_file(result_path),
            "result_sha256": payload.get("result_sha256"),
            "reporting_complete": payload.get("reporting_complete"),
            "scientific_endpoint_pass": payload.get("scientific_endpoint_pass"),
        }
        write_json(results / "physiology_positive_control_run.json", run_record)
        print(
            json.dumps(
                {
                    "reporting_complete": payload["reporting_complete"],
                    "scientific_endpoint_pass": payload[
                        "scientific_endpoint_pass"
                    ],
                    "positive_control_pass": payload["positive_control_pass"],
                    "result_sha256": payload["result_sha256"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["scientific_endpoint_pass"] else 1

    if args.command == "adapter":
        value = json.loads(Path(args.input).read_text(encoding="utf-8"))
        payload = validate_adapter_metrics(value)
        write_json(results / "adapter_metrics.json", payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["pass"] else 1

    if args.command == "audit":
        payload = gate_audit(registry, results)
        write_json(results / "gate_audit.json", payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["submission_ready"] else 2

    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
