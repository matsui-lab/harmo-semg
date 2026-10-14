#!/usr/bin/env python3
"""Run the OpenSim-only synthesis phase of the G6b-2 pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED_OPENSIM_VERSION = "4.5.2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output-model", required=True)
    parser.add_argument("--report", required=True)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    plan_path = Path(args.plan).resolve()
    output_model = Path(args.output_model).resolve()
    report_path = Path(args.report).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6b-dynamic-derived-model-plan":
        raise ValueError("worker received an invalid G6b-2 plan")
    if plan.get("hidden_reference_role") in plan.get("estimation_input_roles", []):
        raise ValueError("hidden target model entered worker estimation inputs")
    template = Path(str(plan["template_model_path"])).resolve()
    if _sha256(template) != str(plan["template_model_sha256"]):
        raise ValueError("G6b-2 template model checksum changed")

    import opensim  # Imported only inside the pinned OpenSim environment.

    version = str(opensim.GetVersion())
    if not version.startswith(REQUIRED_OPENSIM_VERSION):
        raise RuntimeError(
            "G6b-2 requires OpenSim {}, observed {}".format(
                REQUIRED_OPENSIM_VERSION, version
            )
        )
    model = opensim.Model(str(template))
    state = model.initSystem()
    scales = opensim.ScaleSet()
    applied = {}
    for body, raw_factor in sorted(plan.get("body_scale_factors", {}).items()):
        factor = float(raw_factor)
        if factor <= 0:
            raise ValueError("G6b-2 body scale factors must be positive")
        scale = opensim.Scale()
        scale.setSegmentName(str(body))
        scale.setScaleFactors(opensim.Vec3(factor, factor, factor))
        scales.cloneAndAppend(scale)
        applied[str(body)] = [factor, factor, factor]

    target_mass = float(plan["target_mass_kg"])
    model.scale(state, scales, False, target_mass)
    model.setName("{}_dynamic_derived".format(plan["target_subject_id"]))
    output_model.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output_model))
    generated = opensim.Model(str(output_model))
    generated.initSystem()
    generated_mass = sum(
        generated.getBodySet().get(index).getMass()
        for index in range(generated.getBodySet().getSize())
    )
    report = {
        "schema_version": "1.0.0",
        "scope": "g6b-dynamic-model-worker",
        "opensim_version": version,
        "template_model_sha256": _sha256(template),
        "output_model_sha256": _sha256(output_model),
        "target_mass_kg": target_mass,
        "generated_mass_kg": generated_mass,
        "applied_body_scale_factors": applied,
        "hidden_reference_available_to_worker": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
