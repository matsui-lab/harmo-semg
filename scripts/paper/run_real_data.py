#!/usr/bin/env python3
"""Acquire and validate a frozen public real-data pilot."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harmo.real_data_validation import (
    fetch_source_archive,
    load_source_registry,
    run_real_data_pilot,
    source_asset,
    summarize_real_data_cohort,
)
from harmo.validation_study import environment_record, file_sha256, write_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Checksum-gated acquisition and real-data pilot for harmo-semg"
    )
    parser.add_argument(
        "--root", default=str(Path(__file__).resolve().parents[2]),
        help="repository root",
    )
    parser.add_argument(
        "--registry", default="paper/source_releases.yaml",
        help="source release registry, relative to root",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="download and verify a registered archive")
    fetch.add_argument("--study", required=True)
    fetch.add_argument("--asset", required=True)
    fetch.add_argument("--destination", required=True)
    fetch.add_argument("--no-resume", action="store_true")

    pilot = commands.add_parser("pilot", help="run the repeated real-data pilot")
    pilot.add_argument("--study", default="study_32")
    pilot.add_argument("--asset", default="P01")
    pilot.add_argument("--archive", required=True)
    pilot.add_argument("--config", default="studies/study_32/config.yaml")
    pilot.add_argument("--extract-root", required=True)
    pilot.add_argument("--parquet", required=True)
    pilot.add_argument(
        "--output", default="paper/results/validation/study32_p01_real_data_pilot.json"
    )
    pilot.add_argument("--repeats", type=int, default=2)
    pilot.add_argument("--max-timepoints", type=int, default=100)

    cohort = commands.add_parser(
        "cohort", help="fetch and validate a complete registered participant cohort"
    )
    cohort.add_argument("--study", default="study_32")
    cohort.add_argument(
        "--assets",
        nargs="+",
        help="registered participant asset IDs; default selects Pnn ZIP assets",
    )
    cohort.add_argument("--config", default="studies/study_32/config.yaml")
    cohort.add_argument("--data-root", required=True)
    cohort.add_argument("--work-root", required=True)
    cohort.add_argument(
        "--output-dir", default="paper/results/validation/study32_participants"
    )
    cohort.add_argument(
        "--summary-output",
        default="paper/results/validation/study32_real_data_cohort.json",
    )
    cohort.add_argument("--repeats", type=int, default=2)
    cohort.add_argument("--max-timepoints", type=int, default=100)
    cohort.add_argument("--no-fetch", action="store_true")
    cohort.add_argument("--no-resume", action="store_true")
    return parser


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _participant_assets(source, requested=None):
    assets = source["assets"]
    selected = list(requested or [])
    if not selected:
        selected = [
            asset_id
            for asset_id, asset in assets.items()
            if asset_id.startswith("P")
            and asset_id[1:].isdigit()
            and str(asset.get("name", "")).lower().endswith(".zip")
        ]
    unknown = sorted(set(selected) - set(assets))
    if unknown:
        raise ValueError("unregistered cohort assets: {}".format(", ".join(unknown)))
    if len(selected) != len(set(selected)):
        raise ValueError("cohort asset IDs must be unique")
    return {asset_id: assets[asset_id] for asset_id in sorted(selected)}


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    registry = load_source_registry(_resolve(root, args.registry))

    if args.command == "fetch":
        _source, asset = source_asset(registry, args.study, args.asset)
        result = fetch_source_archive(
            asset, _resolve(root, args.destination), resume=not args.no_resume
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["pass"] else 1

    if args.repeats < 2:
        raise ValueError("real-data validation requires at least two repeats")
    if args.max_timepoints <= 0:
        raise ValueError("max-timepoints must be positive")

    if args.command == "cohort":
        source = registry["sources"].get(args.study)
        if source is None:
            raise KeyError("unknown source study {}".format(args.study))
        assets = _participant_assets(source, args.assets)
        config_path = _resolve(root, args.config)
        data_root = _resolve(root, args.data_root)
        work_root = _resolve(root, args.work_root)
        output_dir = _resolve(root, args.output_dir)
        cohort_environment = environment_record()
        results = {}
        failures = {}
        for asset_id, asset in assets.items():
            archive_path = data_root / args.study / str(asset["name"])
            participant_work = work_root / args.study / asset_id
            try:
                if args.no_fetch:
                    if not archive_path.is_file():
                        raise FileNotFoundError(str(archive_path))
                else:
                    fetch_source_archive(
                        asset, archive_path, resume=not args.no_resume
                    )
                results[asset_id] = run_real_data_pilot(
                    root=root,
                    source=source,
                    asset=asset,
                    archive_path=archive_path,
                    extract_root=participant_work / "extracted",
                    parquet_path=participant_work / "normalized_sample.parquet",
                    study_id=args.study,
                    config_path=config_path,
                    repeats=args.repeats,
                    max_timepoints=args.max_timepoints,
                )
            except Exception as exc:  # complete-denominator ledger is intentional
                failures[asset_id] = "{}: {}".format(type(exc).__name__, exc)

        result_files = {}
        for asset_id, result in sorted(results.items()):
            output_path = output_dir / "{}_{}_real_data_pilot.json".format(
                args.study.replace("_", ""), asset_id.lower()
            )
            write_json(output_path, result)
            result_files[asset_id] = {
                "result_path": _display_path(root, output_path),
                "result_sha256": file_sha256(output_path),
            }
        summary = summarize_real_data_cohort(
            study_id=args.study,
            source=source,
            expected_assets=assets,
            participant_results=results,
            failures=failures,
            result_files=result_files,
            run_environment=cohort_environment,
        )
        summary_output = _resolve(root, args.summary_output)
        write_json(summary_output, summary)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0 if summary["pass"] else 3

    source, asset = source_asset(registry, args.study, args.asset)
    result = run_real_data_pilot(
        root=root,
        source=source,
        asset=asset,
        archive_path=_resolve(root, args.archive),
        extract_root=_resolve(root, args.extract_root),
        parquet_path=_resolve(root, args.parquet),
        study_id=args.study,
        config_path=_resolve(root, args.config),
        repeats=args.repeats,
        max_timepoints=args.max_timepoints,
    )
    output = _resolve(root, args.output)
    write_json(output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
