import importlib.util
import json
from pathlib import Path
import sys

SCRIPTS=Path(__file__).parents[1]/'scripts/paper'
sys.path.insert(0,str(SCRIPTS))
import run_local_llm_comparison as comparison
import run_mac_llm_suite as suite


def test_comparison_preserves_failures_and_does_not_score_accuracy(tmp_path,monkeypatch):
    calls=[]
    def fake_run(packet,folder,model,port,case_index,**kwargs):
        calls.append(kwargs)
        raise ValueError('test_failure')
    monkeypatch.setattr(comparison,'run',fake_run)
    rows=comparison.compare(tmp_path/'unused',tmp_path/'out','model',thinking=True)
    assert len(rows)==8 and all(row['error']=='test_failure' for row in rows)
    assert all(call['thinking'] and call['evidence_ids'] for call in calls)
    saved=json.loads((tmp_path/'out/comparison.json').read_text())
    assert not saved['accuracy_evaluated'] and saved['automatic_approvals']==0


def test_suite_downloads_then_tests_and_unloads_serially(tmp_path,monkeypatch):
    import io
    events=[]
    class Opener:
        def open(self,request,timeout):
            data=json.loads(request.data)
            events.append((request.full_url.rsplit('/',1)[-1],data['model']))
            return io.BytesIO(b'{"status":"success"}')
    monkeypatch.setattr(suite,'build_opener',lambda *args:Opener())
    def fake_compare(packet,folder,model):
        events.append(('test',model))
        return [{} for _ in range(8)]
    monkeypatch.setattr(suite,'compare',fake_compare)
    suite.suite(tmp_path/'unused',tmp_path/'out')
    assert [event[0] for event in events]==['pull','test','generate']*3
    state=json.loads((tmp_path/'out/status.json').read_text())
    assert state['finished'] and all(item['status']=='completed' for item in state['models'])
    assert state['automatic_approvals']==0
