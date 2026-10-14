"""Standard-library entry point; setup must succeed before a release starts.

Updates use a new release directory. User study data and previous environments
are never replaced. Setup requires network; start/check do not install anything.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parent


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def verify():
    manifest=json.loads((ROOT/'RELEASE.json').read_text())
    for name,checksum in manifest['files'].items():
        if name.startswith('/') or '\\' in name or '..' in Path(name).parts: raise ValueError('Invalid release path')
        if sha(ROOT/name)!=checksum: raise ValueError('Release file changed: '+name)
    return sha(ROOT/'RELEASE.json')


def doctor():
    from harmo.schema_resources import schema_directory
    from importlib.metadata import version
    return dict(python=sys.version,platform=platform.platform(),
        dependencies={n:version(n) for n in ('harmo','numpy','scipy','pandas','h5py','pyarrow','wfdb','ezc3d')},
        schema_count=len(list(schema_directory().glob('*.json'))),
        Rscript_on_PATH=shutil.which('Rscript') is not None,
        scope='Python local review/processing is installed. R/PhysioIO and local LLM models are optional external environments; their versions/functionality are not verified by this check.')


def run():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('setup','start','check'))
    parser.add_argument('--uv')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--data-dir')
    args=parser.parse_args()
    os.chdir(ROOT)
    if sys.version_info[:2]!=(3,12):raise RuntimeError('This release requires managed Python 3.12.')
    fingerprint=verify()
    receipt=ROOT/'.runtime/installed.json'
    if args.action=='setup':
        if not args.uv: raise RuntimeError('Use the Setup launcher.')
        subprocess.run([args.uv,'sync','--locked','--no-dev','--no-build','--no-install-project','--python',sys.executable],check=True)
        report=doctor()
        subprocess.run([sys.executable,'-m','harmo.desktop','--self-test',str(ROOT/'.runtime'/('setup-check-'+uuid.uuid4().hex+'.json'))],check=True)
        temp=receipt.with_name('installed-'+uuid.uuid4().hex+'.json')
        temp.write_text(json.dumps(dict(release_sha256=fingerprint,environment=report),indent=2))
        temp.replace(receipt)
        print('Setup complete. Open Start to launch Harmo. Study data stays separate from this folder.')
        return
    if not receipt.exists() or json.loads(receipt.read_text())['release_sha256']!=fingerprint:
        raise RuntimeError('Run Setup for this release before starting.')
    if args.action=='check':
        report=doctor()
        print(json.dumps(report,indent=2))
        target=ROOT/'.runtime'/('check-'+uuid.uuid4().hex+'.json')
        subprocess.run([sys.executable,'-m','harmo.desktop','--self-test',str(target)],check=True)
        print('Offline synthetic processing, storage and project-transfer check passed: '+str(target))
    else:
        from harmo.desktop import main as launch
        options=(['--no-browser'] if args.no_browser else [])+(['--data-dir',args.data_dir] if args.data_dir else [])
        launch(options)


if __name__=='__main__':
    from guard import exclusive_file
    (ROOT/'.runtime').mkdir(exist_ok=True)
    try:
        with exclusive_file(ROOT/'.runtime/application.lock'):run()
    except BlockingIOError:
        # Start a second launcher only to reopen the existing browser session;
        # setup/check must never mutate an environment used by a live server.
        if sys.argv[1:2]==['start']:
            from harmo.desktop import main
            main(sys.argv[2:])
        else:raise SystemExit('Quit this release before setup or checking it.')
