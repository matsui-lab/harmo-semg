"""Download small public metadata/code snapshots; no downloaded code is executed."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

API = "https://api.figshare.com/v2/"


def run(out):
    if out.exists():
        raise FileExistsError(out)
    payloads, inventory = {}, []
    def fetch(name, url):
        with urllib.request.urlopen(url, timeout=30) as f:
            data = f.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("unexpected source size")
        payloads[name] = data
        inventory.append({"path": name, "url": url, "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "retrieved_at": datetime.now(timezone.utc).isoformat()})
        return data
    specs = [
        ("lencioni_collection_v1.json", "collections/4494755/versions/1"),
        ("lencioni_collection_current_items.json", "collections/4494755/articles?page_size=100"),
        ("lencioni_subject1_v1.json", "articles/9784964/versions/1"),
        ("lencioni_walking_code_v1.json", "articles/9759092/versions/1"),
        ("lencioni_angles_code_v1.json", "articles/9785117/versions/1"),
        ("gait120_release_v1.json", "articles/27677016/versions/1"),
    ]
    for name, endpoint in specs:
        d = json.loads(fetch(name, API + endpoint))
        if not isinstance(d, dict):
            continue
        for item in d.get("files", []):
            if item["name"].endswith(".m"):
                fetch("lencioni_" + str(item["id"]) + "_" + Path(item["name"]).name, item["download_url"])
    out.mkdir(parents=True, exist_ok=False)
    for name, data in payloads.items():
        (out / name).write_bytes(data)
    (out / "manifest.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(inventory, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output)
