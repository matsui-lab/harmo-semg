import importlib.util
import json
from pathlib import Path, PureWindowsPath
import sys
from types import SimpleNamespace

import pytest

from harmo import local_platform
from harmo.desktop_project import encoder, expand, relative


def test_windows_paths_are_portable_tokens():
    encode=encoder([(PureWindowsPath('C:/Users/Alice/Data'),'data')])
    assert encode(r'C:\Users\Alice\Data\record.json')=='@project/data/record.json'
    assert encode('file:///C:/Users/Alice/Data/record.json')=='@project/data/record.json'
    assert encode(r'c:\users\alice\data\record.json')=='@project/data/record.json'
    with pytest.raises(ValueError):encode(r'D:\other\record.json')


@pytest.mark.parametrize('path',[r'C:\secret',r'\\server\share','file:///C:/secret','@project/../secret'])
def test_external_windows_paths_rejected(path,tmp_path):
    with pytest.raises(ValueError):expand(path,tmp_path)


@pytest.mark.parametrize('path',['CON','a/NUL.json','LPT1','COM9.txt','a./b','a/b '])
def test_device_and_nonportable_names_rejected(path):
    with pytest.raises(ValueError):relative(path)


def test_linux_data_root(monkeypatch,tmp_path):
    monkeypatch.setattr(local_platform.sys,'platform','linux')
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path))
    assert local_platform.data_directory()==tmp_path/'harmo'


def test_windows_data_root(monkeypatch,tmp_path):
    monkeypatch.setattr(local_platform.sys,'platform','win32')
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path))
    assert local_platform.data_directory()==tmp_path/'Harmo'


def test_windows_lock_adapter(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(LK_UNLCK=0,LK_NBLCK=2,
                         locking=lambda fd,mode,n:calls.append((mode,n))))
    monkeypatch.setattr(local_platform.sys,'platform','win32')
    with local_platform.exclusive_file(tmp_path/'lock'):pass
    assert calls==[(2,1),(0,1)]


def test_local_lock_excludes_competing_processes(tmp_path):
    path=tmp_path/'lock'
    with local_platform.exclusive_file(path):
        with pytest.raises(BlockingIOError):
            with local_platform.exclusive_file(path):pass
    with local_platform.exclusive_file(path):pass


def test_installer_never_weakens_system_security():
    root=Path(__file__).resolve().parents[1]/'packaging/local_browser'
    shell=(root/'bootstrap.sh').read_text()
    powershell=(root/'bootstrap.ps1').read_text()
    assert '--locked' in (root/'manage.py').read_text()
    assert '--managed-python' in shell and '--managed-python' in powershell
    assert 'checksum' in shell and 'Get-FileHash' in powershell
    assert not any(term in shell+powershell for term in ('sudo ', 'curl -k', 'ExecutionPolicy Bypass', 'Set-ExecutionPolicy', 'Invoke-Expression'))
