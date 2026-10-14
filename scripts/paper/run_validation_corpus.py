#!/usr/bin/env python3
"""Validate or execute the checksum-bound active-mapping real-data corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harmo.validation_corpus import (
    create_corpus_reproducibility_manifest,
    download_validation_corpus_assets,
    execute_validation_corpus,
    load_validation_corpus,
)
from harmo.validation_study import load_registry, write_json, write_rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--registry", default="paper/validation_registry.yaml")
    parser.add_argument("--corpus", default="paper/validation_corpus.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    download = commands.add_parser("download")
    download.add_argument("--data-root", required=True)
    download.add_argument("--output", default=None)
    execute = commands.add_parser("execute")
    execute.add_argument("--data-root", required=True)
    execute.add_argument("--work-root", required=True)
    execute.add_argument("--results", default="paper/results/validation")
    manifest = commands.add_parser("manifest")
    manifest.add_argument("--results", required=True)
    manifest.add_argument("--environment-id", required=True)
    manifest.add_argument("--run-id", required=True)
    manifest.add_argument("--output", required=True)
    return parser


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _portable_outputs(outputs, root: Path):
    portable = {}
    for name, raw_path in outputs.items():
        path = Path(raw_path)
        try:
            portable[name] = str(path.relative_to(root))
        except ValueError:
            portable[name] = str(path)
    return portable


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    registry = load_registry(_resolve(root, args.registry))
    corpus = load_validation_corpus(_resolve(root, args.corpus), root, registry)
    if args.command == "validate":
        payload = {
            "corpus": str(_resolve(root, args.corpus)),
            "study_count": len(corpus["studies"]),
            "asset_count": sum(
                len(study["assets"]) for study in corpus["studies"].values()
            ),
            "mapping_denominator": sum(
                len(asset["expected_mappings"])
                for study in corpus["studies"].values()
                for asset in study["assets"].values()
            ),
            "status": "valid",
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "download":
        payload = download_validation_corpus_assets(
            corpus, _resolve(root, args.data_root)
        )
        if args.output:
            write_json(_resolve(root, args.output), payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["pass"] else 2

    if args.command == "manifest":
        payload = create_corpus_reproducibility_manifest(
            root=root,
            registry=registry,
            corpus=corpus,
            results_dir=_resolve(root, args.results),
            environment_id=args.environment_id,
            run_id=args.run_id,
        )
        write_json(_resolve(root, args.output), payload)
        print(
            json.dumps(
                {
                    "output": str(_resolve(root, args.output)),
                    "run_id": payload["run_id"],
                    "environment_id": payload["environment_id"],
                    "pass": payload["pass"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if payload["pass"] else 2

    results = _resolve(root, args.results)
    sources, mappings, summary = execute_validation_corpus(
        root=root,
        corpus=corpus,
        data_root=_resolve(root, args.data_root),
        work_root=_resolve(root, args.work_root),
    )
    source_outputs = write_rows(results / "source_inventory.json", sources)
    mapping_outputs = write_rows(results / "mapping_outcomes.json", mappings)
    summary["source_outputs"] = _portable_outputs(source_outputs, root)
    summary["mapping_outputs"] = _portable_outputs(mapping_outputs, root)
    write_json(results / "corpus_execution.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["trust_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
