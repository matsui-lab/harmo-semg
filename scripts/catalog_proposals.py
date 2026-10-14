"""Store checked proposals and optionally assess an explicit record pair."""
import argparse
import json
from harmo.proposal_catalog import ingest, compare


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest="command", required=True)
    add = commands.add_parser("ingest")
    add.add_argument("--proposals", required=True)
    add.add_argument("--source-root", required=True)
    add.add_argument("--store", required=True)
    add.add_argument("--dataset", required=True)
    query = commands.add_parser("compare")
    query.add_argument("--store", required=True)
    query.add_argument("--left", required=True)
    query.add_argument("--right", required=True)
    query.add_argument("--channel", required=True)
    args = p.parse_args()
    if args.command == "ingest":
        result = ingest(args.proposals,args.source_root,args.store,args.dataset)
        print(json.dumps({k: len(v) if k == "held" else v for k,v in result.items()}, indent=2))
    else:
        print(json.dumps(compare(args.store,args.left,args.right,args.channel), indent=2))


if __name__ == "__main__":
    main()
