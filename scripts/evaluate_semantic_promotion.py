"""Offline verification on saved public-batch records, without changing them."""
import argparse
import json
from pathlib import Path
from harmo.semantic_promotion import promote, read_definitions, digest
from harmo.evidence.models import HarmoRecordingBundle
from harmo.html_definitions import extract_html_definitions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    parser.add_argument("output")
    args = parser.parse_args()
    stage, out = Path(args.stage), Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    docs = [read_definitions(p, "https://physionet.org/files/" + stage.parent.name + "/" + stage.name + "/" + p.relative_to(stage/"raw").as_posix())
            for p in sorted((stage/"raw").rglob("*.record-definitions.json"))]
    reports = []
    for record in sorted((stage/"converted").glob("*/record.json")):
        data = json.loads(record.read_text())
        bundle = HarmoRecordingBundle.from_dict(json.loads((record.parent/"recording.bundle.json").read_text()))
        screen = json.loads((record.parent/"source.screen.json").read_text())
        html_doc = extract_html_definitions(stage/"description.html", "https://physionet.org/content/"+bundle.study_id+"/",
            bundle,data["name"],digest(stage/"raw"/data["name"]))
        view, report = promote(bundle, data["name"], digest(stage/"raw"/data["name"]), docs+[html_doc], screen)
        report["html_extraction"] = html_doc
        report["name"] = data["name"]
        reports.append(report)
        (out/(record.parent.name+".bundle.json")).write_text(view.to_json())
    (out/"report.json").write_text(json.dumps(dict(recognized_definition_documents=len(docs), records=reports),indent=2))


if __name__ == "__main__":
    main()
