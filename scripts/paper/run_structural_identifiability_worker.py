#!/usr/bin/env python3
"""Run the checksum-bound G6c-S6c structural identifiability worker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.dynamic_calibration import sha256_file  # noqa: E402
from harmo.structural_identifiability import (  # noqa: E402
    inspect_structural_identifiability_plan,
    write_requirement_rows,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--report", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    plan_path = Path(args.plan).resolve()
    work_dir = Path(args.work_dir).resolve()
    report_path = Path(args.report).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("scope") != "g6c-s6c-structural-identifiability-plan":
        raise ValueError("worker received an invalid G6c-S6c plan")
    inspected, rows = inspect_structural_identifiability_plan(plan)
    table_path = work_dir / "g6c_s6c_identifiability_requirements.csv"
    write_requirement_rows(rows, table_path)
    artifacts = {
        "plan_sha256": sha256_file(plan_path),
        "requirements_table_sha256": sha256_file(table_path),
    }
    worker = {
        "schema_version": "1.0.0",
        "scope": "g6c-s6c-structural-identifiability-worker",
        "input_sha256": inspected["input_sha256"],
        "prior_result_sha256": plan.get("prior_result_sha256"),
        "source": inspected["source"],
        "diagnosis": inspected["diagnosis"],
        "analysis_complete": True,
        "artifacts_complete": all(
            len(value) == 64 for value in artifacts.values()
        ),
        "artifacts": artifacts,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(worker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
