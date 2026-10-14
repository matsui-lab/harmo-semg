"""Standalone, finite remote evaluation; only frozen public-source requests."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import argparse
from urllib.request import Request,build_opener,ProxyHandler

MODEL='gemma4:31b-it-q8_0'
ARCHIVE_URL='https://github.com/ollama/ollama/releases/download/v0.34.0/ollama-linux-amd64.tar.zst'
ARCHIVE_SHA='cf95886728959aa09910bb34de5cca1cc5a8f68003b5597197d3f2c2d57c0804'


def main(container=False):
    root=Path.cwd();state=dict(status='runtime_download',started_at=time.time(),model=MODEL,
        scope='Diagnostic transfer, no independent accuracy score.',automatic_approvals=0)
    def save():
        temp=root/'status.tmp';temp.write_text(json.dumps(state,indent=2));temp.replace(root/'status.json')
    opener=build_opener(ProxyHandler({}));server=None
    def api(path,payload=None,timeout=900):
        data=None if payload is None else json.dumps(payload).encode()
        request=Request('http://127.0.0.1:11459/api/'+path,data=data,headers={'Content-Type':'application/json'})
        return opener.open(request,timeout=timeout)
    def get(path,payload=None):
        with api(path,payload) as response:return json.load(response)
    save()
    try:
        archive=root/'ollama-linux-amd64.tar.zst';h=hashlib.sha256()
        if archive.exists():
            with archive.open('rb') as source:
                for chunk in iter(lambda:source.read(4*1024**2),b''):h.update(chunk)
        else:
            with opener.open(ARCHIVE_URL,timeout=120) as response,archive.open('wb') as dest:
                received=0
                while True:
                    chunk=response.read(4*1024**2)
                    if not chunk:break
                    dest.write(chunk);h.update(chunk);received+=len(chunk)
                    state['runtime_bytes']=received;save()
        if h.hexdigest()!=ARCHIVE_SHA:raise ValueError('runtime_checksum_mismatch')
        state['runtime_sha256']=h.hexdigest();state['status']='runtime_extract';save()
        runtime=root/'runtime'
        if not runtime.exists():
            runtime.mkdir()
            subprocess.run(['tar','--zstd','-xf',str(archive),'-C',str(runtime)],check=True,timeout=120)
        env=os.environ.copy();env.update(OLLAMA_HOST='127.0.0.1:11459',OLLAMA_MODELS=str(root/'models'),
            OLLAMA_NO_CLOUD='1',OLLAMA_MAX_LOADED_MODELS='1',OLLAMA_NUM_PARALLEL='1',OLLAMA_GPU_OVERHEAD='1073741824')
        with (root/'server.log').open('w') as log:
            command=[str(runtime/'bin/ollama'),'serve']
            if container:
                state['container_image']=subprocess.check_output(['docker','image','inspect','ollama/ollama:latest','--format','{{.Id}}'],text=True).strip()
                command=['docker','run','--rm','--name','harmo-gemma31b-v002','--gpus','device=0',
                    '-p','127.0.0.1:11459:11434','-v',str(runtime.resolve())+':/opt/harmo-runtime:ro',
                    '-v',str(root/'models')+':/root/.ollama','-e','OLLAMA_NO_CLOUD=1',
                    '-e','OLLAMA_HOST=0.0.0.0:11434','-e','OLLAMA_MAX_LOADED_MODELS=1',
                    '-e','OLLAMA_NUM_PARALLEL=1','-e','OLLAMA_GPU_OVERHEAD=1073741824',
                    '--entrypoint','/opt/harmo-runtime/bin/ollama',state['container_image'],'serve']
            server=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT)
        for _ in range(60):
            if server.poll() is not None:raise RuntimeError('server_exited')
            try:state['runtime']=get('version');break
            except OSError:time.sleep(0.5)
        else:raise RuntimeError('server_not_ready')
        state['status']='model_download';save()
        with api('pull',dict(model=MODEL,stream=True),timeout=7200) as response:
            last=None
            for line in response:
                last=json.loads(line);state['download']=last;save()
                if 'error' in last:raise RuntimeError(last['error'])
        if not last or last.get('status')!='success':raise RuntimeError('incomplete_download')
        (root/'model.json').write_text(json.dumps(dict(tags=get('tags'),details=get('show',dict(model=MODEL))),indent=2))
        state['status']='testing';state['completed_requests']=0;state['failed_requests']=0;save()
        # All source text, questions, schemas and target identities unchanged.
        # Context reduced to 16k for VRAM headroom; generation cap remains 8192.
        for request_file in sorted((root/'requests').glob('*.json')):
            request=json.loads(request_file.read_text());request['model']=MODEL
            request['options']['num_ctx']=16384
            folder=root/'results'/request_file.stem;folder.mkdir(parents=True)
            (folder/'request.json').write_text(json.dumps(request,indent=2,ensure_ascii=False))
            started=time.time()
            try:
                response=get('chat',request)
                (folder/'response.json').write_text(json.dumps(response,indent=2,ensure_ascii=False))
                (folder/'runtime.json').write_text(json.dumps(dict(wall_seconds=time.time()-started,
                    model_allocation=get('ps'),gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total,memory.used','--format=csv'],text=True)),indent=2))
                state['completed_requests']+=1
            except Exception as exc:
                (folder/'failure.json').write_text(json.dumps(dict(error=str(exc),wall_seconds=time.time()-started)))
                state['failed_requests']+=1
            save()
        state['status']='completed' if state['failed_requests']==0 else 'completed_with_failures'
    except Exception as exc:state.update(status='failed',error=str(exc))
    finally:
        if server is not None:
            try:get('generate',dict(model=MODEL,keep_alive=0,stream=False))
            except Exception:pass
            if container:subprocess.run(['docker','stop','harmo-gemma31b-v002'],capture_output=True,timeout=30)
            else:server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            state['server_returncode']=server.returncode
        state['finished_at']=time.time();save()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--container',action='store_true')
    main(parser.parse_args().container)
