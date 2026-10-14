import pytest
from harmo.automatic_workflow import run


def test_registered_route_uses_checked_pipeline(tmp_path,monkeypatch):
    from harmo import reuse_workflow
    calls=[]
    def registered(*args):
        calls.append(args)
        return dict(status='completed',timing=dict(released=20,status='completed'))
    monkeypatch.setattr(reuse_workflow,'run',registered)
    result=run(['registered-urls'],tmp_path,['activation-timing'],timing_profile='study38-gait120',
               source_cache='cache',timing_protocol='protocol')
    assert calls[0][0]==['registered-urls']
    assert result['assessment']['exported']==20
    assert (tmp_path/'automatic.summary.json').exists()


@pytest.mark.parametrize('status,expected',[('completed','completed'),('completed-with-release-holds','completed-with-holds')])
def test_planned_route_explicitly_selected(tmp_path,monkeypatch,status,expected):
    from harmo import reuse_workflow
    calls=[]
    def registered(*args,**kwargs):
        calls.append(kwargs)
        return dict(status=status,timing=dict(released=20 if status=='completed' else 0,status=status))
    monkeypatch.setattr(reuse_workflow,'run',registered)
    result=run(['registered-urls'],tmp_path,['activation-timing'],timing_profile='study38-gait120-planned',
               source_cache='cache',timing_protocol='protocol',resume=True)
    assert calls==[dict(planned=True)]
    assert result['status']==expected


@pytest.mark.parametrize('kwargs',[
    dict(purposes=['amplitude-pooling']),dict(max_records=1),dict(max_comparisons=19),
    dict(local_llm_model='model'),dict(source_cache=None),dict(timing_protocol=None),
])
def test_incompatible_profile_options_fail_before_acquisition(tmp_path,kwargs):
    args=dict(purposes=['activation-timing'],timing_profile='study38-gait120',source_cache='cache',timing_protocol='protocol')
    args.update(kwargs)
    with pytest.raises(ValueError):run([],tmp_path/'new',**args)
    assert not (tmp_path/'new').exists()


def test_unregistered_urls_rejected_before_writes(tmp_path):
    with pytest.raises(ValueError,match='exact_two_registered'):
        run(['https://physionet.org/content/semg/1.0.1/'],tmp_path/'new',['activation-timing'],
            timing_profile='study38-gait120',source_cache='cache',timing_protocol='protocol')
    assert not (tmp_path/'new').exists()
