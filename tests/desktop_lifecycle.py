"""Cold restart and singleton test for an already populated synthetic app directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--app',required=True)
    parser.add_argument('--data',required=True)
    parser.add_argument('--report',required=True)
    args=parser.parse_args()
    data=Path(args.data).resolve()
    executable=Path(args.app).resolve()/'Contents/MacOS/Harmo'
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','VIRTUAL_ENV','PYTHONHOME')}
    env['PATH']='/usr/bin:/bin'
    active=data/'active.json'
    if active.exists(): raise RuntimeError('Stop the test app before testing a cold restart.')
    command=[str(executable),'--data-dir',str(data),'--no-browser']
    process=subprocess.Popen(command,cwd=tempfile.gettempdir(),env=env)
    opener=build_opener(ProxyHandler({}))
    home=None
    def request(url,path,payload=None):
        u=urlsplit(url)
        origin=u.scheme+'://'+u.netloc
        req=Request(origin+path,data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Authorization':'Bearer '+u.fragment,'Origin':origin,'Content-Type':'application/json'})
        with opener.open(req,timeout=10) as response:return json.load(response)
    try:
        start=time.monotonic()
        while not active.exists():
            if process.poll() is not None: raise RuntimeError('Application exited during startup')
            if time.monotonic()-start>30: raise TimeoutError('No launcher within 30 seconds')
            time.sleep(.1)
        startup=time.monotonic()-start
        home=json.loads(active.read_text())['url']
        before=json.loads(active.read_text())
        subprocess.run(command,cwd=tempfile.gettempdir(),env=env,check=True,timeout=20)
        assert json.loads(active.read_text())==before, 'Second launch replaced a live instance'
        status=request(home,'/api/status')
        project=next(p for p in status['projects'] if p['kind']=='demo')
        request(home,'/api/open',{'id':project['id']})
        start=time.monotonic()
        while True:
            state=request(home,'/api/status')['state']
            if state['status']!='running':break
            if time.monotonic()-start>30:raise TimeoutError('Project did not reopen')
            time.sleep(.1)
        assert state['status']=='completed',state
        review=state['result']['url']
        collection=request(review,'/api/collection')
        assert len(collection['entries'])>=1
        library=request(review,'/api/library')
        assert len(library['common_records'])==2 and len(library['derived_signals'])==2
        assert all(not a['derived_signal_approval'] for a in library['use_assessments'])
        request(home,'/api/quit',{})
        process.wait(timeout=15)
        assert process.returncode==0 and not active.exists()
        result=dict(status='passed',startup_seconds=startup,app=str(executable),
            developer_python_not_on_PATH=True,single_instance=True,restart_saved_data=True,
            common_records=2,derived_signals=2,normal_quit=True,scientific_approval=False)
        with Path(args.report).open('x') as stream:json.dump(result,stream,indent=2)
    finally:
        if process.poll() is None:
            if home:
                try:request(home,'/api/quit',{})
                except Exception:pass
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.terminate()


if __name__=='__main__':main()
