#!/usr/bin/env python3
"""Verify frozen public-source releases against official repository APIs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from harmo.real_data_validation import load_source_registry
from harmo.source_release import (
    source_rights_report,
    verify_figshare_source,
    verify_mendeley_source,
)
from harmo.validation_study import write_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", default="paper/source_releases.yaml")
    parser.add_argument(
        "--output", default="paper/results/validation/source_release_verification.json"
    )
    parser.add_argument(
        "--online", action="store_true", help="query official Figshare APIs"
    )
    args = parser.parse_args(argv)

    registry = load_source_registry(Path(args.registry))
    rights = source_rights_report(registry)
    online = []
    if args.online:
        for study_id, source in sorted(registry["sources"].items()):
            release = source.get("release") or {}
            if release.get("repository") == "figshare":
                online.append(verify_figshare_source(study_id, source))
            elif release.get("repository") == "mendeley-data":
                online.append(verify_mendeley_source(study_id, source))
    payload = {
        "registry_version": registry["registry_version"],
        "scope": "official-source-release-and-rights-verification",
        "rights": rights,
        "online_repositories": online,
        "online_requested": args.online,
        "pass": rights["pass"] and (
            not args.online or bool(online) and all(row["pass"] for row in online)
        ),
    }
    write_json(Path(args.output), payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
