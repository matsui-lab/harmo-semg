"""Verify released arrays against the frozen baseline, without changing outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np


def verify(baseline, current):
    paths=sorted(Path(current).glob('resolution/repeat-1/*/*/resolved/selected-signals/timing.npz'))
    assert len(paths)==20, 'expected_twenty_exports'
    values=0
    for path in paths:
        meta=json.loads(path.with_name('signals.json').read_text())
        assert hashlib.sha256(path.read_bytes()).hexdigest()==meta['sha256']
        assert meta['amplitude_pooling'] is False
        relative=path.relative_to(current)
        with np.load(path,allow_pickle=False) as new, np.load(Path(baseline)/relative,allow_pickle=False) as old:
            assert set(new.files)==set(old.files)=={'left_phase','right_phase','left_values','right_values'}
            for key in new.files:
                assert np.array_equal(new[key],old[key]), relative
                assert new[key].shape==(101,) and np.isfinite(new[key]).all()
                if key.endswith('_phase'):
                    assert np.array_equal(new[key],np.linspace(0,100,101))
                else:
                    values+=new[key].size
        for role in ('left','right'):
            release=json.loads((path.parent.parent/role/'release.decision.json').read_text())
            assert release['status']=='allow'
    return dict(exports=20,curves=40,values=values,hashes_verified=True,
                phase_grids_verified=True,baseline_arrays_exact=True,release_allowed=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('baseline','current','output'):p.add_argument('--'+name,required=True)
    args=p.parse_args()
    result=verify(Path(args.baseline),Path(args.current))
    with Path(args.output).open('x') as out:json.dump(result,out,indent=2)
    print(json.dumps(result))
