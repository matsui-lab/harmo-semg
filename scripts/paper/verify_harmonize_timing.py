"""Compare fresh harmonize results against the frozen registered baseline."""
import argparse
import json
from pathlib import Path


def compare(baseline, current):
    def read(root, name):
        return json.loads((Path(root)/name).read_text())
    old = read(baseline, 'catalog.binding.json')
    new = read(current, 'catalog.binding.json')
    old_control = read(baseline, 'physiology.result.json')
    new_control = read(current, 'physiology.result.json')
    metrics = ['input_rows_sha256', 'metrics', 'study_muscle_results',
               'cross_study_centroid_concordance', 'positive_control_pass']
    checks = dict(curves_exact=old['curves']==new['curves'],
                  cycles_equal=old['selected_cycles']==new['selected_cycles'],
                  physiological_endpoints_exact=all(old_control[k]==new_control[k] for k in metrics))
    return dict(checks=checks, all_equal=all(checks.values()),
                baseline_summary=read(baseline, 'summary.json'),
                current_summary=read(current, 'summary.json'),
                boundary='Numerical and endpoint comparison; release permission reported separately.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',required=True)
    parser.add_argument('--current',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    result=compare(args.baseline,args.current)
    with Path(args.output).open('x') as out:
        json.dump(result,out,indent=2)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['all_equal'] else 1)
