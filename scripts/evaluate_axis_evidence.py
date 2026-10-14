"""Link the registered study_38 article table to checksum-bound original arrays."""
import argparse
import json
from pathlib import Path
from urllib.request import urlopen
from scipy.io import loadmat
from harmo.axis_evidence import extract, bind, inspect_registered_plot_code
from harmo.config.automap import digest, save, require


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--article", type=Path)
    p.add_argument("--code-sources", type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC6897988/fullTextXML"
    if args.article:
        raw = args.article.read_bytes()
    else:
        with urlopen(url, timeout=40) as response:
            raw = response.read(2*1024*1024+1)
    require(digest(raw) == "d1e83284ddefe66ee1960c797977f2f16b7b78df8435578aa8613e150de70738", "registered_article_hash_changed")
    with (args.output / "article.xml").open("xb") as stream:
        stream.write(raw)
    claims = extract(raw, "10.1038/s41597-019-0323-z")
    save(args.output / "claims.json", claims)
    code_review = None
    if args.code_sources:
        code_review = inspect_registered_plot_code((args.code_sources / "walking_fig.m").read_bytes())
        save(args.output / "plot_code_evidence.json", code_review)
    inventory = json.loads((args.input_root / "source_verification.json").read_text())
    results = []
    for asset in inventory:
        if not asset["name"].endswith(".mat"):
            continue
        require(Path(asset["name"]).name == asset["name"], "unsafe_source_name")
        path = args.input_root / asset["name"]
        require(asset["verified"] and digest(path.read_bytes()) == asset["sha256"], "source_checksum_mismatch")
        trials = loadmat(path)["s"][0, 0]["Data"]
        for index, trial in enumerate(trials.flat):
            for field in ("Ang", "Mom", "Pwr", "Com"):
                row = bind(claims, field, trial[field].shape, asset["sha256"], "s.Data."+str(index))
                results.append(dict(source=asset["name"], **row))
    save(args.output / "record_axis_evidence.json", results)
    summary = dict(records=len(results), claims=len(claims),
        supported_axis_kinds=sum(r["status"] == "axis_kind_supported_grid_held" for r in results),
        grids_created=0, source_url=url, article_sha256=digest(raw), code_review=code_review,
        binding_basis="Existing study_38 article-release association and local acquisition manifest; not a new independent release verification")
    save(args.output / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
