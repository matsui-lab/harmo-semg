#!/usr/bin/env python3
"""Verify that release archives retain scientific and runtime contracts."""

from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path


SCHEMAS = {
    "comparability-assertion-1.0.0.json",
    "evidence-manifest-1.0.0.json",
    "exchange-1.0.0.json",
    "opensim-artifact-1.0.0.json",
    "recording-bundle-1.0.0.json",
    "trust-report-1.0.0.json",
    "validation-gate-audit-1.0.0.json",
    "validation-registry-1.0.0.json",
}
PAPER_SCRIPTS = {
    "scripts/paper/contract_benchmark.py",
    "scripts/paper/inventory_studies.py",
    "scripts/paper/run_real_data.py",
    "scripts/paper/run_validation.py",
    "scripts/paper/run_validation_corpus.py",
    "scripts/paper/verify_source_releases.py",
}
PAPER_EVIDENCE = {
    "paper/g5-requirements.txt",
    "paper/g6-environment.yml",
    "paper/source_releases.yaml",
    "paper/validation_corpus.yaml",
    "paper/results/validation/source_release_verification.json",
    "paper/results/validation/source_inventory.json",
    "paper/results/validation/mapping_outcomes.json",
    "paper/results/validation/corpus_execution.json",
    "paper/results/validation/format_fidelity.json",
    "paper/results/validation/reproducibility.json",
    "paper/results/validation/opensim_quality.json",
    "paper/results/validation/opensim_quality_summary.json",
    *{
        "paper/results/validation/reproducibility_runs/"
        "{}-run-{}.json".format(environment, run_number)
        for environment in ("macos", "ubuntu")
        for run_number in range(1, 4)
    },
    "paper/results/validation/study32_real_data_cohort.json",
    *{
        "paper/results/validation/study32_participants/"
        "study32_p{:02d}_real_data_pilot.json".format(index)
        for index in range(1, 11)
    },
}


def _single(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(
            "expected exactly one {} in {}, found {}".format(
                pattern, directory, len(matches)
            )
        )
    return matches[0]


def verify(dist_dir: Path):
    wheel = _single(dist_dir, "*.whl")
    sdist = _single(dist_dir, "*.tar.gz")

    with zipfile.ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
        schema_names = {
            Path(name).name
            for name in wheel_names
            if "/share/harmo/schemas/" in name and name.endswith(".json")
        }
        metadata_name = next(
            (name for name in wheel_names if name.endswith(".dist-info/METADATA")),
            None,
        )
        if metadata_name is None:
            raise RuntimeError("wheel metadata is missing")
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
        requirements = metadata.get_all("Requires-Dist", [])

    missing_schemas = sorted(SCHEMAS - schema_names)
    required_dependencies = {
        "core-jsonschema": lambda item: item.startswith("jsonschema>=4.0"),
        "spark-pyspark": lambda item: (
            item.startswith("pyspark<4,>=3.5") and 'extra == "spark"' in item
        ),
        "c3d-ezc3d": lambda item: (
            item.startswith("ezc3d>=1.5.16")
            and 'python_version >= "3.10"' in item
            and 'extra == "c3d"' in item
        ),
        "c3d-pyarrow": lambda item: (
            item.startswith("pyarrow>=12") and 'extra == "c3d"' in item
        ),
    }
    missing_dependencies = sorted(
        name
        for name, predicate in required_dependencies.items()
        if not any(predicate(item) for item in requirements)
    )

    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = archive.getnames()
    missing_scripts = sorted(
        script
        for script in PAPER_SCRIPTS
        if not any(name.endswith("/" + script) for name in sdist_names)
    )
    missing_release_files = sorted(
        filename
        for filename in ("CITATION.cff", "LICENSE")
        if not any(name.endswith("/" + filename) for name in sdist_names)
    )
    missing_validation_registry = not any(
        name.endswith("/paper/validation_registry.yaml") for name in sdist_names
    )
    missing_paper_evidence = sorted(
        filename
        for filename in PAPER_EVIDENCE
        if not any(name.endswith("/" + filename) for name in sdist_names)
    )

    result = {
        "wheel": wheel.name,
        "sdist": sdist.name,
        "wheel_schemas": sorted(schema_names),
        "missing_schemas": missing_schemas,
        "missing_dependencies": missing_dependencies,
        "missing_paper_scripts": missing_scripts,
        "missing_release_files": missing_release_files,
        "missing_validation_registry": missing_validation_registry,
        "missing_paper_evidence": missing_paper_evidence,
    }
    failures = (
        missing_schemas
        + missing_dependencies
        + missing_scripts
        + missing_release_files
        + missing_paper_evidence
        + (["paper/validation_registry.yaml"] if missing_validation_registry else [])
    )
    if failures:
        raise RuntimeError("distribution verification failed: {}".format(result))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default="dist")
    args = parser.parse_args()
    result = verify(Path(args.dist).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
