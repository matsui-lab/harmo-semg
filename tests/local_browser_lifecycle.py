"""Verify release update/rollback against an existing local test collection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--release',required=True)
    p.add_argument('--previous',required=True)
    p.add_argument('--data',required=True)
    p.add_argument('--report',required=True)
    args=p.parse_args()
    data=Path(args.data).resolve()
    opener=build_opener(ProxyHandler({}))
    def api(url,path,payload=None):
        u=urlsplit(url);origin=u.scheme+'://'+u.netloc
        req=Request(origin+path,data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+u.fragment,'Origin':origin,'Content-Type':'application/json'})
        with opener.open(req,timeout=20) as r:return json.load(r)
    counts=[]
    for release in (Path(args.release).resolve(),Path(args.previous).resolve()):
        python=release/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        command=[str(python),str(release/'manage.py'),'start','--no-browser','--data-dir',str(data)]
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV')}
        process=subprocess.Popen(command,cwd=release,env=env,stdout=subprocess.DEVNULL)
        home=None
        try:
            start=time.monotonic()
            while not (data/'active.json').exists():
                if process.poll() is not None:raise RuntimeError('Launcher exited')
                if time.monotonic()-start>30:raise TimeoutError('Startup')
                time.sleep(.1)
            active=json.loads((data/'active.json').read_text());home=active['url']
            subprocess.run(command,cwd=release,env=env,timeout=20,check=True,stdout=subprocess.DEVNULL)
            assert json.loads((data/'active.json').read_text())==active
            install=release/'.runtime/installed.json'
            before=hashlib.sha256(install.read_bytes()).hexdigest()
            refused=subprocess.run([str(python),str(release/'manage.py'),'setup','--uv','unused'],
                cwd=release,env=env,capture_output=True,timeout=15)
            assert refused.returncode!=0 and 'Quit this release' in refused.stderr.decode()
            assert hashlib.sha256(install.read_bytes()).hexdigest()==before
            project=api(home,'/api/status')['projects'][0]
            api(home,'/api/open',{'id':project['id']})
            start=time.monotonic()
            while True:
                state=api(home,'/api/status')['state']
                if state['status']!='running':break
                if time.monotonic()-start>40:raise TimeoutError('Reopen project')
                time.sleep(.1)
            assert state['status']=='completed',state
            library=api(state['result']['url'],'/api/library')
            value=(len(library['common_records']),len(library['derived_signals']))
            assert value==(2,16),value
            counts.append(value)
            api(home,'/api/quit',{})
            process.wait(timeout=15)
            assert process.returncode==0 and not (data/'active.json').exists()
        finally:
            if process.poll() is None:
                try:api(home,'/api/quit',{})
                except Exception:pass
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.terminate()
    with Path(args.report).open('x') as stream:
        json.dump(dict(status='passed',update_preserved_collection=True,rollback_preserved_collection=True,
            single_instance=True,live_setup_rejected=True,records=2,derived_signals=16),stream,indent=2)


if __name__=='__main__':main()
