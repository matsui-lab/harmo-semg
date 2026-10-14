import json
import pytest
from test_timing_planner import fixture, PROTOCOL
from harmo.timing_planner import plan,execute,sha
from harmo.planned_timing_rows import checked_execution, verify_lineage


def test_execution_rechecked_and_missing_or_changed_history_rejected(tmp_path):
    _,spec=fixture(tmp_path);p=plan(spec,PROTOCOL);execute(p,tmp_path/'run')
    path=tmp_path/'run/execution.json';digest=sha(path)
    assert checked_execution(path,digest).shape==(101,)
    data=json.loads(path.read_text());data['trace']=[];path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='trace'):checked_execution(path,sha(path))
    path.unlink()
    with pytest.raises(OSError):checked_execution(path,digest)


def test_missing_lineage_fails(tmp_path):
    with pytest.raises(OSError):verify_lineage(tmp_path/'absent.json')


@pytest.mark.parametrize('state',['missing','changed'])
def test_export_requires_bound_execution_lineage(tmp_path,monkeypatch,state):
    from test_timing_export import fixture as resolved_fixture
    from harmo.planned_timing_rows import RELEASE
    from harmo.timing_export import export_pair
    resolved=resolved_fixture(tmp_path,monkeypatch)
    path=resolved/'left/recording.bundle.json'
    data=json.loads(path.read_text())
    lineage=tmp_path/'lineage.json'
    if state=='changed':lineage.write_text('{}')
    data['sources'].append(dict(uri=lineage.as_uri(),sha256='0'*64,dataset_release=RELEASE))
    path.write_text(json.dumps(data))
    with pytest.raises((ValueError,OSError)):export_pair(resolved,tmp_path/'export')
    assert not (tmp_path/'export').exists()
