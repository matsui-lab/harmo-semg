"""Attach the verified article unit to original-array-checked EMG records."""
import argparse
import json
from harmo.catalog_evidence import promote_units


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", required=True)
    p.add_argument("--article", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--record-semantics", action="store_true", help="Add registered study38 task/anatomy/measurement-side evidence in a new version")
    args = p.parse_args()
    result = promote_units(args.store,args.article,args.output,record_semantics=args.record_semantics)
    print(json.dumps({k:v for k,v in result.items() if k != "records"}, indent=2))


if __name__ == "__main__":
    main()
