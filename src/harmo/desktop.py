"""Double-click launcher for the local, offline Harmo review application."""
import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import uuid
import webbrowser
import time

from harmo.walking_plan import read, save
from harmo.local_platform import data_directory, exclusive_file

EDITION = '0.3.0-local-browser.1'


def choose_file(kind):
    prompts = {'project':'Choose a portable Harmo project (.harmo)',
               'workspace':'Choose an existing workspace.json (original source files must be available)'}
    # No paths or document text are interpolated into AppleScript.
    script = 'POSIX path of (choose file with prompt '+json.dumps(prompts[kind])+')'
    result = subprocess.run(['/usr/bin/osascript','-e',script],capture_output=True,text=True,timeout=180)
    if result.returncode:
        if '-128' in result.stderr: return None
        raise ValueError('The file picker could not open. Check macOS permissions and try again.')
    return Path(result.stdout.strip()).resolve()


class Desktop:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.projects = self.root/'projects'
        self.projects.mkdir(exist_ok=True)
        self.servers = {}
        self.state = dict(status='idle')
        self.lock = threading.Lock()
        self.upload_lock = threading.Lock()
        self.home_url = None

    def listing(self):
        rows=[]
        for path in sorted(self.projects.glob('*/project.json')):
            row=read(path)
            rows.append({k:row[k] for k in ('id','name','kind')})
        return rows

    def project(self, identity):
        if not isinstance(identity,str) or not re_id(identity): raise ValueError('Choose an existing project.')
        return read(self.projects/identity/'project.json')

    def open(self, identity):
        from harmo.semantic_server import ReviewSession, make_server
        if identity in self.servers: return self.servers[identity].session_url
        project=self.project(identity)
        session=ReviewSession(project['workspace'], self.root/'runs'/uuid.uuid4().hex,
                              collection=self.root/'collection')
        server=make_server(session)
        server.desktop_home=self.home_url
        server.desktop_demo=bool(read(session.summary).get('synthetic',False))
        if server.desktop_demo:
            # Display-only correction: the fixture uses a WFDB intake layout,
            # but its signals did not come from PhysioNet.
            for item in session.data.get('items',[]):
                repo=item.get('context',{}).get('repository')
                if repo: repo.update(name='Synthetic demonstration',url='')
        server.desktop_session=session
        self.servers[identity]=server
        threading.Thread(target=server.serve_forever,daemon=True).start()
        return server.session_url

    def add(self, kind, selected=None):
        from harmo.desktop_project import import_project, make_workspace
        identity=uuid.uuid4().hex
        project=self.projects/identity
        project.mkdir()
        if kind=='demo':
            from harmo.desktop_demo import create
            summary=create(project/'data')
            workspace=make_workspace(summary,project/'review')
            name='Synthetic demonstration'
        elif kind=='project':
            summary=import_project(selected,project/'data')
            workspace=make_workspace(summary,project/'review')
            imported=read(summary)
            datasets=sorted({e.get('dataset','') for e in imported['intake']['entries']} - {''})
            name='Synthetic demonstration' if imported.get('synthetic') else (', '.join(datasets[:2]) or 'Imported project')
        elif kind=='workspace':
            workspace=Path(selected).resolve()
            name=workspace.parent.name
        else: raise ValueError('Unsupported project type.')
        # Validate before making a project appear in the launcher.
        from harmo.semantic_review import checked_packet
        from harmo.walking_plan import verify
        data=read(workspace)
        verify(data.get('context_inputs',{}))
        if checked_packet(data['packet_path'])['packet_id'] != data['packet_id']:
            raise ValueError('The review data does not match its source packet.')
        save(project/'project.json',dict(id=identity,name=name,kind=kind,workspace=str(workspace)))
        return dict(id=identity,url=self.open(identity))

    def submit(self, action, payload, selected=None):
        if action not in ('demo','project','workspace','open','export'): raise ValueError('Unknown action.')
        with self.lock:
            if self.state['status']=='running': raise ValueError('Another operation is still running.')
            self.state=dict(status='running',action=action)
        def work():
            try:
                if action=='open': result=dict(url=self.open(payload.get('id')))
                elif action=='export':
                    from harmo.desktop_project import export_project
                    from harmo.semantic_review import checked_packet
                    row=self.project(payload.get('id'))
                    workspace=read(row['workspace'])
                    summary=checked_packet(workspace['packet_path'])['summary_path']
                    destination=self.root/'exports'/('project-'+uuid.uuid4().hex+'.harmo')
                    destination.parent.mkdir(exist_ok=True)
                    export_project(summary,destination)
                    result=dict(download='/api/download/'+destination.name)
                else:
                    chosen=selected or (choose_file(action) if action in ('project','workspace') else None)
                    result=self.add(action,chosen) if chosen or action=='demo' else dict(cancelled=True)
                state=dict(status='completed',result=result)
            except Exception as exc:
                # Keep diagnostics local; never log session tokens or source text.
                with (self.root/'errors.log').open('a') as stream:
                    stream.write(type(exc).__name__+': '+str(exc)+'\n')
                state=dict(status='failed',error=str(exc))
            with self.lock: self.state=state
        threading.Thread(target=work,daemon=True).start()

    def busy(self):
        return self.state['status']=='running' or any(
            server.desktop_session.state['status']=='running' for server in self.servers.values())

    def close(self):
        for server in self.servers.values():
            server.shutdown()
            server.server_close()


def re_id(value):
    return len(value)==32 and all(c in '0123456789abcdef' for c in value)


def make_launcher(desktop, port=0):
    from harmo.semantic_server import LoopbackServer
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def respond(self,code,value,mime='application/json; charset=utf-8',attachment=None):
            body=json.dumps(value,ensure_ascii=False).encode() if mime.startswith('application/json') else value
            self.send_response(code)
            for key,val in {'Content-Type':mime,'Content-Length':str(len(body)),'Cache-Control':'no-store',
                'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"}.items():
                self.send_header(key,val)
            if attachment: self.send_header('Content-Disposition','attachment; filename="'+attachment+'"')
            self.end_headers()
            self.wfile.write(body)
        def allowed(self,post=False):
            return (self.headers.get('Host')==self.server.host
                and self.headers.get('Origin') in (('http://'+self.server.host,) if post else (None,'http://'+self.server.host))
                and secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token))
        def do_GET(self):
            if self.headers.get('Host')!=self.server.host: return self.respond(403,dict(error='Local connection required.'))
            if self.path=='/': return self.respond(200,Path(__file__).with_name('desktop.html').read_bytes(),'text/html; charset=utf-8')
            if not self.allowed(): return self.respond(403,dict(error='Session authorization required.'))
            if self.path=='/api/status':
                with desktop.lock: state=deepcopy(desktop.state)
                return self.respond(200,dict(state=state,projects=desktop.listing(),edition=EDITION,
                    data_directory=str(desktop.root)))
            if self.path.startswith('/api/download/'):
                name=self.path.rsplit('/',1)[-1]
                if name.startswith('project-') and name.endswith('.harmo') and re_id(name[8:-6]):
                    path=desktop.root/'exports'/name
                    if path.is_file(): return self.respond(200,path.read_bytes(),'application/octet-stream',name)
            return self.respond(404,dict(error='Not found.'))
        def do_POST(self):
            if not self.allowed(True): return self.respond(403,dict(error='Same-origin authorization required.'))
            if self.path == '/api/import':
                from harmo.desktop_project import MAX_BYTES
                if not desktop.upload_lock.acquire(blocking=False):
                    return self.respond(409,dict(error='Another import is in progress.'))
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=MAX_BYTES or self.headers.get('Content-Type')!='application/octet-stream':
                        raise ValueError('Choose a .harmo project of at most 512 MiB.')
                    if desktop.busy(): raise ValueError('Wait for the current operation to finish.')
                    uploads=desktop.root/'imports'; uploads.mkdir(exist_ok=True)
                    selected=uploads/('project-'+uuid.uuid4().hex+'.harmo')
                    self.connection.settimeout(30)
                    with selected.open('xb') as stream:
                        remaining=size
                        while remaining:
                            block=self.rfile.read(min(1024**2,remaining))
                            if not block: raise ValueError('Incomplete local file transfer; choose the file again.')
                            stream.write(block);remaining-=len(block)
                    desktop.submit('project',{},selected=selected)
                    return self.respond(202,dict(status='running'))
                except (ValueError,OSError) as exc:
                    return self.respond(400,dict(error=str(exc)))
                finally: desktop.upload_lock.release()
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<8192 or self.headers.get('Content-Type')!='application/json': raise ValueError('Small JSON request required.')
                payload=json.loads(self.rfile.read(size))
                if not isinstance(payload,dict) or set(payload)-{'id'}: raise ValueError('Invalid request.')
                if desktop.upload_lock.locked(): raise ValueError('Wait for the local file import to finish.')
                if self.path=='/api/quit':
                    if desktop.busy(): raise ValueError('Wait for the current operation to finish before quitting.')
                    self.respond(200,dict(stopped=True))
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                if not self.path.startswith('/api/'): return self.respond(404,dict(error='Not found.'))
                desktop.submit(self.path[5:],payload)
                return self.respond(202,dict(status='running'))
            except (ValueError,TypeError,KeyError) as exc: return self.respond(400,dict(error=str(exc)))
    server=LoopbackServer(('127.0.0.1',port),Handler)
    server.host='127.0.0.1:'+str(server.server_port)
    server.session_url='http://'+server.host+'/#'+token
    desktop.home_url=server.session_url
    return server


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--self-test', metavar='REPORT_JSON', help='Run an isolated offline diagnostic check and exit')
    parser.add_argument('--test-project', help='Use a portable project instead of synthetic signals for --self-test')
    args=parser.parse_args(argv)
    if args.self_test:
        self_test(args.self_test, args.test_project)
        return 0
    os.umask(0o077)
    root=data_directory(args.data_dir)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with application_lock(root) as acquired:
        if not acquired:
            # The lock, not an obsolete PID file, establishes a live instance.
            active=read(root/'active.json')
            if not args.no_browser: webbrowser.open(active['url'])
            return
        desktop=Desktop(root)
        server=make_launcher(desktop)
        # Atomic, owner-readable rendezvous for repeat launches.
        temporary=root/('active-'+uuid.uuid4().hex+'.json')
        save(temporary,dict(url=server.session_url,pid=os.getpid()))
        temporary.replace(root/'active.json')
        if not args.no_browser: webbrowser.open(server.session_url)
        if sys.stdout: print(server.session_url,flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally:
            desktop.close()
            server.server_close()
            (root/'active.json').unlink(missing_ok=True)


from contextlib import contextmanager


@contextmanager
def application_lock(root):
    lock=exclusive_file(root/'desktop.lock')
    try: lock.__enter__()
    except BlockingIOError:
        # A repeat click may occur before the first launcher wrote its URL.
        for _ in range(50):
            if (root/'active.json').exists(): break
            time.sleep(.1)
        yield False
    else:
        try: yield True
        finally: lock.__exit__(None,None,None)


def self_test(report_path, project=None):
    import tempfile
    from harmo.desktop_project import make_workspace, import_project, export_project
    from harmo.desktop_demo import create
    from harmo.semantic_server import ReviewSession
    from harmo.schema_resources import schema_directory
    root=Path(tempfile.mkdtemp(prefix='harmo-desktop-check-')).resolve()
    try:
        summary=import_project(project,root/'source') if project else create(root/'source')
        workspace=make_workspace(summary,root/'review')
        session=ReviewSession(workspace,root/'runs',collection=root/'collection')
        result=session.evaluate(deepcopy(session.data['decisions']))
        execution=session.execute(result['run_id'])
        registered=session.register(result['run_id'])
        export_project(summary,root/'roundtrip.harmo')
        relocated=import_project(root/'roundtrip.harmo',root/'relocated')
        second=ReviewSession(make_workspace(relocated,root/'relocated-review'),root/'runs-second')
        assessed=second.evaluate(deepcopy(second.data['decisions']))
        rerun=second.execute(assessed['run_id'])
        assert result['review_metrics']['reviewed_items']==0
        assert not execution['scientific_approval'] and not execution['amplitude_pooling_allowed']
        assert execution['curves']>0 and rerun['curves']==execution['curves']
        first_curves=(session.runs[result['run_id']]['folder']/'processing/curves.csv').read_bytes()
        second_curves=(second.runs[assessed['run_id']]['folder']/'processing/curves.csv').read_bytes()
        assert first_curves==second_curves, 'Relocation changed waveform values'
        report=dict(status='passed',edition=EDITION,frozen=bool(getattr(sys,'frozen',False)),
            synthetic=project is None,root=str(root),schema_files=len(list(schema_directory().glob('*.json'))),
            curves=execution['curves'],comparisons=execution['comparisons'],
            registration=registered,portable_roundtrip_identical_curves=True,
            human_confirmations=0,scientific_approval=False)
    except Exception as exc:
        report=dict(status='failed',root=str(root),error=repr(exc))
        save(report_path,report)
        raise
    save(report_path,report)
    return report


if __name__=='__main__': main()
