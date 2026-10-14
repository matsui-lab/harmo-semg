#!/usr/bin/env python3
"""Build a blinded semantic packet or evaluate two human annotations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from harmo.semantic_gold_standard import (  # noqa: E402
    build_annotation_template,
    build_blinded_packet,
    evaluate_gold_standard,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    packet = sub.add_parser("packet")
    packet.add_argument("--protocol", default="paper/semantic_gold_standard_protocol.yaml")
    packet.add_argument("--plan", default="paper/literature_semantic_enrichment.yaml")
    packet.add_argument("--report", default="paper/results/validation/literature_semantic_enrichment.json")
    packet.add_argument("--output", required=True)
    template = sub.add_parser("template")
    template.add_argument("--packet", required=True)
    template.add_argument("--annotator-id", required=True)
    template.add_argument("--output", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--protocol", default="paper/semantic_gold_standard_protocol.yaml")
    evaluate.add_argument("--packet", required=True)
    evaluate.add_argument("--report", default="paper/results/validation/literature_semantic_enrichment.json")
    evaluate.add_argument("--annotation", action="append", required=True)
    evaluate.add_argument("--adjudication")
    evaluate.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "packet":
        result = build_blinded_packet(
            protocol_path=args.protocol,
            plan_path=args.plan,
            report_path=args.report,
        )
    elif args.command == "template":
        result = build_annotation_template(
            packet_path=args.packet,
            annotator_id=args.annotator_id,
        )
    else:
        result = evaluate_gold_standard(
            protocol_path=args.protocol,
            packet_path=args.packet,
            report_path=args.report,
            annotation_paths=args.annotation,
            adjudication_path=args.adjudication,
        )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return (
        0
        if args.command in {"packet", "template"} or result["summary"]["pass"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
