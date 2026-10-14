from pathlib import Path
import pytest
from harmo import schema_resources as resources
from harmo.reuse_workflow import implementation_identity


def test_current_environment_schema_identity():
    directory=resources.schema_directory()
    names={p.name for p in directory.glob('*.json')}
    assert 'recording-bundle-1.2.0.json' in names
    assert {n[8:] for n in implementation_identity() if n.startswith('schemas/')} == names


def test_installed_layout_ignores_cwd(tmp_path,monkeypatch):
    package=tmp_path/'env/lib/python/site-packages/harmo'
    package.mkdir(parents=True)
    data=tmp_path/'env/share/harmo/schemas'
    data.mkdir(parents=True)
    (data/'x.json').write_text('{}')
    monkeypatch.setattr(resources,'__file__',str(package/'schema_resources.py'))
    monkeypatch.setattr(resources.sysconfig,'get_path',lambda key:str(tmp_path/'env'))
    monkeypatch.chdir(tmp_path)
    assert resources.schema_path('x.json') == data/'x.json'
    with pytest.raises(RuntimeError): resources.schema_path('missing.json')


@pytest.mark.parametrize('name',['../x.json','/x.json','a\\x.json','x.txt'])
def test_invalid_name(name):
    with pytest.raises(ValueError): resources.schema_path(name)


def test_missing_data_does_not_search_other_directories(tmp_path,monkeypatch):
    monkeypatch.setattr(resources,'__file__',str(tmp_path/'installed/harmo/schema_resources.py'))
    monkeypatch.setattr(resources.sysconfig,'get_path',lambda key:str(tmp_path/'empty'))
    with pytest.raises(RuntimeError,match='missing'):resources.schema_directory()
