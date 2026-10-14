"""Rerun H with an explicitly supplied frozen implementation on PYTHONPATH."""
import argparse
import json
from pathlib import Path
from harmo.full_record_benchmark import run_benchmark

p=argparse.ArgumentParser()
p.add_argument('--root',required=True)
p.add_argument('--output',required=True)
args=p.parse_args()
root=Path(args.root);out=Path(args.output)
out.mkdir(parents=True,exist_ok=False)
result=run_benchmark(source_dir=str(root/'downloads/additional_framework_v001_2026.09.12-r1'),
    work_root=str(out/'work'),protocol_path=str(root/'paper/full_record_materialization_protocol.yaml'),
    positive_control_path=str(root/'paper/results/validation/physiology_positive_control.json'))
(out/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result['summary'],indent=2))
