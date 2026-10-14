#!/usr/bin/env python3
"""Run the frozen Study 38 full-record materialization benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.full_record_benchmark import run_benchmark  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument(
        "--protocol", default="paper/full_record_materialization_protocol.yaml"
    )
    parser.add_argument(
        "--positive-control",
        default="paper/results/validation/physiology_positive_control.json",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_benchmark(
        source_dir=args.source_dir,
        work_root=args.work_root,
        protocol_path=args.protocol,
        positive_control_path=args.positive_control,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if result["summary"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
