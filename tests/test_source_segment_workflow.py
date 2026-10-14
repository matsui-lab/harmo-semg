import json
from pathlib import Path
import pytest
import harmo.walking_plan as planner
from harmo.walking_execute import execute
from harmo.footswitch_timing import DEFAULT
from test_general_workflow import fixture


@pytest.mark.parametrize('same_reference',[True,False])
def test_source_stride_uses_single_segment_and_separates_reference_side(tmp_path,monkeypatch,same_reference):
    summary=fixture(tmp_path/'input')
    original=planner.collect_saved_documents
    def documents(intake):
        result=original(intake)
        result['documents'].append(dict(dataset='new-unregistered-study/1.0.0',result=dict(observations=[],source_segments=[
            dict(record=r['name'],reference_role='trailing-foot',reference_side='right' if same_reference or i==0 else 'left')
            for i,r in enumerate(intake['entries'][0]['details']['records'])])))
        return result
    monkeypatch.setattr(planner,'collect_saved_documents',documents)
    protocol=tmp_path/'protocol.json';profile=dict(DEFAULT,cycle_seconds=[.5,10.])
    protocol.write_text(json.dumps(profile))
    plan=planner.prepare(summary,protocol=protocol)
    assert all(r['recipe']=='source-delimited-stride-envelope/2' for r in plan['records'])
    assert all(not r['anchors'][0]['detector_used'] for r in plan['records'])
    result=execute(plan,tmp_path/'result')
    assert len(result['curves'])==2 and len(result['comparisons'])==(1 if same_reference else 0)
    assert all(c['cycles']==1 and not c['filter_boundary_effects_validated'] for c in result['curves'])
    assert len(result['segment_boundary_sensitivity'])==4
    assert all(c['interpretation_status']=='source-segment-boundary-sensitivity-unresolved' for c in result['comparisons'])
    assert all(c['circular_peak_difference_percent'] is None and c['global_peak_status']=='held-unobserved-boundary-context' for c in result['comparisons'])
    assert not result['scientific_approval']
