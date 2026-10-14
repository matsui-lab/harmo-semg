import json
from types import SimpleNamespace
import pytest
from harmo.planned_timing_workflow import run
from harmo.cli import _cmd_timing_run


def test_failure_records_stage_without_overwriting(tmp_path):
    out=tmp_path/'run'
    with pytest.raises(OSError):run(tmp_path/'catalog',tmp_path/'sources',tmp_path/'protocol',tmp_path/'article',out)
    state=json.loads((out/'status.json').read_text())
    assert state['status']=='failed' and state['stage']=='input-validation'
    before=(out/'status.json').read_bytes()
    with pytest.raises(FileExistsError):run('a','b','c','d',out)
    assert (out/'status.json').read_bytes()==before


@pytest.mark.parametrize('status,code',[('completed',0),('completed-with-release-holds',2)])
def test_cli_preserves_release_holds(monkeypatch,status,code):
    monkeypatch.setattr('harmo.planned_timing_workflow.run',lambda *args:dict(status=status))
    args=SimpleNamespace(catalog='c',source_cache='s',protocol='p',article='a',output='o')
    assert _cmd_timing_run(args)==code
