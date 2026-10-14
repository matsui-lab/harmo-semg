import sys
import json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts/paper'))
from build_llm_transfer_packet import build


def test_packet_contains_raw_sources_not_derived_claims(tmp_path):
    base=tmp_path/'downloads/physionet_batch_v001_2026.09.13-r1/runs/ff2e47a15406490db9a17e5e68f8102e'
    for study,version,record,n in [('emgdb','1.0.0','emg_healthy',1),('semg','1.0.1','S01',6)]:
        folder=base/study/version;(folder/'raw').mkdir(parents=True)
        (folder/'description.html').write_text('<main><p>Original source paragraph.</p></main>')
        (folder/'source.evidence.json').write_text('{"value":"DO_NOT_READ_DERIVED_LABEL"}')
        (folder/'raw'/f'{record}.hea').write_text(f'{record} {n} 2000 100\n'+''.join(
            f'{record}.dat 16 10/mV 16 0 0 0 0 RAW CHANNEL {i}\n' for i in range(n)))
    out=tmp_path/'out';build(tmp_path,out)
    text=(out/'model_input.json').read_text();data=json.loads(text)
    assert 'DO_NOT_READ_DERIVED_LABEL' not in text
    assert len(data['cases'])==3
    assert data['cases'][2]['targets'][0]['channel']=='RAW CHANNEL 5'
    assert all(len(c['targets'])==8 for c in data['cases'])
    assert all(s['source_sha256'] for c in data['cases'] for s in c['sources'])
    refs=json.loads((out/'human_reference_template.json').read_text())
    assert all(not r['reviewed'] and r['reference_value'] is None for r in refs['labels'])
