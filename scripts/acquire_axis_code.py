"""Acquire the two paper-linked MATLAB code releases for inspection, never execution."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
from harmo.config.automap import save, require


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for article in (9759092, 9785117):
        with urlopen(f"https://api.figshare.com/v2/articles/{article}/versions/1", timeout=40) as stream:
            meta = json.loads(stream.read(1024*1024))
        save(args.output / f"article-{article}.json", meta)
        for asset in meta["files"]:
            name = asset["name"]
            require(Path(name).name == name and name.lower().endswith(".m"), "unexpected_code_asset")
            require(asset["size"] <= 1024*1024, "size_limit")
            with urlopen(asset["download_url"], timeout=40) as stream:
                raw = stream.read(1024*1024+1)
            require(len(raw) == asset["size"] and hashlib.md5(raw).hexdigest() == asset["computed_md5"], "checksum_mismatch")
            with (args.output / name).open("xb") as stream:
                stream.write(raw)
            rows.append(dict(name=name, doi=meta["doi"], url=asset["download_url"],
                sha256=hashlib.sha256(raw).hexdigest(), md5=asset["computed_md5"], executed=False))
    save(args.output / "sources.json", rows)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
