"""Loopback-only review -> bounded offline plan -> explicit diagnostic execution.

No arbitrary paths, source downloads, generated code, or implicit human decisions
are accepted from HTTP. Original catalog and waveforms stay read-only.
"""
import argparse
from collections import Counter
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from socketserver import TCPServer

from harmo.semantic_review import checked_packet, apply, reassess
from harmo.walking_plan import prepare, read, save, sha, limits, verify, review_template
from harmo.walking_execute import execute
from harmo.general_workflow import queue

MAX_BODY = 2 * 1024**2


class LoopbackServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer normally performs a reverse-DNS lookup even for 127.0.0.1.
        # This offline application has no use for a network-derived server name.
        TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]


def purpose_summary(assessment):
    result = []
    for purpose in ('activation-timing', 'amplitude-pooling'):
        entries = [entry for job in assessment['jobs'] for entry in job.get('result', {}).get('entries', [])
                   if entry.get('purpose') == purpose]
        reasons = Counter()
        for entry in entries:
            for group in entry.get('decision', {}).get('check_groups', []):
                if group.get('execution') == 'executed':
                    for finding in group.get('findings', []):
                        reasons[finding['reason']] += 1
            for reason in entry.get('reasons', []):
                reasons[str(reason)] += 1
        result.append(dict(purpose=purpose, evaluated=len(entries),
            statuses=dict(Counter(e['status'] for e in entries)),
            reasons=[dict(reason=r, count=n) for r, n in reasons.most_common(20)],
            omitted_reason_types=max(0, len(reasons)-20)))
    return result


class ReviewSession:
    def __init__(self, workspace, output, budget=None, collection=None):
        self.workspace = Path(workspace).resolve()
        self.data = read(self.workspace)
        self.workspace_hash = sha(self.workspace)
        verify(self.data.get('context_inputs', {}))
        self.packet_path = self.data['packet_path']
        packet = checked_packet(self.packet_path)
        if packet['packet_id'] != self.data['packet_id']:
            raise ValueError('workspace_packet_mismatch')
        self.summary = packet['summary_path']
        self.budget = limits(dict({'records': 2, 'comparisons': 256}, **(budget or {})))
        self.root = Path(output).resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        from harmo.semantic_collection import Collection
        self.collection = Collection(collection or self.root.parent/'semantic_collection')
        self.runs = {}
        self.lock = threading.Lock()
        self.state = dict(status='idle')
        self.requests = {}
        save(self.root/'session.json', dict(workspace=str(self.workspace), workspace_sha256=self.workspace_hash,
             packet_id=packet['packet_id'], limits=self.budget, offline=True,
             scientific_approval=False, version='semantic-session/1'))

    def check(self):
        verify(self.data.get('context_inputs', {}))
        if sha(self.workspace) != self.workspace_hash:
            raise ValueError('workspace_changed_restart_required')
        packet = checked_packet(self.packet_path)
        if packet['packet_id'] != self.data['packet_id']:
            raise ValueError('packet_changed_restart_required')

    def validate_decisions(self, supplied):
        if not isinstance(supplied, dict) or supplied.get('packet_id') != self.data['packet_id']:
            raise ValueError('review_packet_mismatch')
        original = {d['target_id']: d for d in self.data['decisions']['decisions']}
        editable = {i['target']['target_id'] for i in self.data['items']}
        rows = supplied.get('decisions')
        if not isinstance(rows, list) or any(not isinstance(d, dict) for d in rows):
            raise ValueError('complete_decision_list_required')
        ids = [d.get('target_id') for d in rows]
        if len(ids) != len(set(ids)) or set(ids) != set(original):
            raise ValueError('all_original_targets_must_be_retained')
        if any(d != original[d['target_id']] for d in rows if d['target_id'] not in editable):
            raise ValueError('non_displayed_decisions_must_not_change')
        return deepcopy(supplied)

    def evaluate(self, supplied):
        self.check()
        supplied = self.validate_decisions(supplied)
        if len(self.runs) >= 32:
            raise ValueError('session_run_budget_exhausted')
        run_id = secrets.token_hex(8)
        folder = self.root/run_id
        folder.mkdir()
        save(folder/'decisions.json', supplied)
        reviewed = sum(d['action'] != 'unreviewed' for d in supplied['decisions'])
        receipt = None
        metrics = dict(reviewed_items=0, unreviewed_items=len(supplied['decisions']), scientific_approvals=0)
        if reviewed:
            accepted = apply(self.packet_path, folder/'decisions.json', folder/'review')
            receipt = folder/'review/review.receipt.json'
            metrics = accepted['metrics']
        before = prepare(self.summary, self.budget, offline=True)
        plan = prepare(self.summary, self.budget, semantic_review=receipt, offline=True) if receipt else before
        save(folder/'plan.json', plan)
        save(folder/'event-review.template.json', review_template(plan))
        assessment = reassess(self.summary, folder/'assessment', self.budget['comparisons'], receipt,
                             offline=True, record_ids={r['record_id'] for r in plan['records']})
        self.check()
        verify(plan['inputs'])
        rows = []
        previous = {r['record_id']: r for r in before['records']}
        for record in plan['records']:
            old = previous[record['record_id']]
            rows.append(dict(record_id=record['record_id'], record=record['record'],
                before=old['status'], after=record['status'],
                channels_before=len(old['channels']), channels_after=len(record['channels']),
                blockers=record['blockers'], review_required=record['review_required'],
                recipe=record.get('recipe', plan['registry']['processing'])))
        result = dict(run_id=run_id, phase='evaluated', output=str(folder), review_metrics=metrics,
            records=rows, ready_records=sum(r['after']=='diagnostic_ready' for r in rows),
            omitted_records=plan['omitted_records'], limits=self.budget,
            purposes=purpose_summary(assessment), assessment_status=assessment['status'],
            unprocessed_pairs=assessment['unprocessed_pairs'],
            assessment_errors=[j.get('error') for j in assessment['jobs'] if 'error' in j],
            scientific_approval=False, amplitude_pooling_allowed=False,
            note='Purpose gates and diagnostic readiness are separate. Execution requires a separate explicit action.')
        save(folder/'evaluation.json', result)
        self.runs[run_id] = dict(folder=folder, plan_hash=sha(folder/'plan.json'), evaluation=result)
        return result

    def execute(self, run_id):
        self.check()
        if run_id not in self.runs:
            raise ValueError('evaluate_in_this_session_first')
        run = self.runs[run_id]
        folder = run['folder']
        if sha(folder/'plan.json') != run['plan_hash']:
            raise ValueError('saved_plan_changed')
        plan = read(folder/'plan.json')
        verify(plan['inputs'])
        if 'execution' in run:
            verify(run['artifacts'])
            return run['execution']
        # Failed partial executions are retained; never overwrite and retry them.
        result = execute(plan, folder/'processing')
        self.check()
        work = queue(plan, result)
        save(folder/'work-queue.json', work)
        from harmo.harmonization_view import build as build_view
        visual = build_view(plan, result, folder/'processing')
        save(folder/'harmonization-view.json', visual)
        report = dict(run['evaluation'], phase='executed',
            visual=visual,
            execution_status=result['status'], curves=len(result['curves']), comparisons=len(result['comparisons']),
            omitted_curves=result['omitted_curves'], omitted_comparisons=result['omitted_comparisons'],
            processing_records=result['records'], work_items=work['items'],
            scientific_approval=False, amplitude_pooling_allowed=False,
            note='Separate normalized record curves and diagnostic comparisons, not qualified scientific pooling.')
        save(folder/'execution.json', report)
        run['artifacts'] = {str(p): sha(p) for p in folder.rglob('*') if p.is_file()}
        save(folder/'artifacts.json', run['artifacts'])
        run['execution'] = report
        return report

    def register(self, run_id):
        self.check()
        run = self.runs.get(run_id)
        if not run or 'execution' not in run:
            raise ValueError('execute_in_this_session_before_registration')
        return self.collection.register(run['folder'], run['artifacts'], self.data, self.packet_path)

    def submit(self, action, payload):
        if action not in ('evaluate', 'execute', 'register'):
            raise ValueError('unknown_action')
        request_id = payload.get('request_id')
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 80:
            raise ValueError('request_id_required')
        signature = json.dumps([action, payload], sort_keys=True)
        with self.lock:
            if request_id in self.requests:
                if self.requests[request_id] != signature:
                    raise ValueError('request_id_reused_with_changed_payload')
                return deepcopy(self.state)
            if self.state['status'] == 'running':
                raise ValueError('another_operation_is_running')
            if len(self.requests) >= 64:
                raise ValueError('session_request_budget_exhausted')
            self.requests[request_id] = signature
            self.state = dict(status='running', action=action, request_id=request_id)
        def work():
            try:
                result = (self.evaluate(payload['decisions']) if action == 'evaluate' else
                          self.execute(payload['run_id']) if action == 'execute' else self.register(payload['run_id']))
                state = dict(status='completed', action=action, request_id=request_id, result=result)
            except Exception as exc:
                state = dict(status='failed', action=action, request_id=request_id, error=str(exc))
            with self.lock:
                self.state = state
        threading.Thread(target=work, daemon=True).start()
        return dict(status='running', action=action, request_id=request_id)


def make_server(session, port=0):
    token = secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No tokens, source text or human decisions in HTTP logs.

        def respond(self, code, value, mime='application/json; charset=utf-8'):
            body = json.dumps(value, ensure_ascii=False).encode() if mime.startswith('application/json') else value
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            origin = 'http://'+self.server.expected_host
            return (self.headers.get('Host') == self.server.expected_host
                    and self.headers.get('Origin') in (None, origin)
                    and secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+token))

        def do_GET(self):
            if self.headers.get('Host') != self.server.expected_host:
                return self.respond(403, dict(error='loopback_host_required'))
            if self.path == '/':
                template = Path(__file__).with_name('semantic_workspace.html').read_text()
                payload = json.dumps(session.data, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
                html = template.replace('/*WORKSPACE_DATA*/null', payload).replace("connect-src 'none'", "connect-src 'self'")
                if getattr(self.server, 'desktop_home', None):
                    from html import escape
                    demo = ' · Synthetic demonstration — generated signals, not research data' if getattr(self.server, 'desktop_demo', False) else ''
                    html = html.replace('<body>', '<body><nav style="padding:10px 24px;border-bottom:1px solid #ddd;font:14px -apple-system,sans-serif"><a style="color:#222" href="'+escape(self.server.desktop_home, quote=True)+'">← Projects</a>'+demo+'</nav>')
                return self.respond(200, html.encode(), 'text/html; charset=utf-8')
            if not self.allowed():
                return self.respond(403, dict(error='session_authorization_required'))
            if self.path == '/api/status':
                with session.lock:
                    return self.respond(200, deepcopy(session.state))
            if self.path == '/api/collection':
                return self.respond(200, dict(entries=session.collection.list()))
            if self.path == '/api/library' or self.path.startswith('/api/library/'):
                try:
                    purpose = self.path[len('/api/library/'):] if self.path.startswith('/api/library/') else None
                    return self.respond(200, session.collection.library(purpose))
                except (ValueError, KeyError) as exc:
                    return self.respond(400, dict(error=str(exc)))
            if self.path.startswith('/api/collection/'):
                try:
                    parts = self.path.split('/')
                    if len(parts) == 4:
                        return self.respond(200, session.collection.get(parts[3]))
                    if len(parts) == 5:
                        return self.respond(200, session.collection.artifact(parts[3], parts[4]), 'application/octet-stream')
                except (ValueError, KeyError) as exc:
                    return self.respond(400, dict(error=str(exc)))
            # Only explicitly whitelisted products of this session can be read.
            parts = self.path.split('/')
            if len(parts) == 5 and parts[:3] == ['', 'api', 'artifact']:
                run = session.runs.get(parts[3])
                files = {'curves.csv': 'processing/curves.csv', 'diagnostic.json': 'processing/diagnostic.json',
                         'evaluation.json': 'evaluation.json', 'execution.json': 'execution.json',
                         'decisions.json': 'decisions.json', 'work-queue.json': 'work-queue.json'}
                if run and parts[4] in files:
                    path = run['folder']/files[parts[4]]
                    if path.is_file() and not path.is_symlink():
                        return self.respond(200, path.read_bytes(), 'application/octet-stream')
            return self.respond(404, dict(error='not_found'))

        def do_POST(self):
            if not self.allowed() or self.headers.get('Origin') != 'http://'+self.server.expected_host:
                return self.respond(403, dict(error='same_origin_session_required'))
            if self.path not in ('/api/evaluate', '/api/execute', '/api/register'):
                return self.respond(404, dict(error='not_found'))
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY or self.headers.get('Content-Type') != 'application/json':
                    raise ValueError('bounded_JSON_body_required')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError('JSON_object_required')
                return self.respond(202, session.submit(self.path.rsplit('/', 1)[1], payload))
            except (ValueError, KeyError, TypeError) as exc:
                return self.respond(400, dict(error=str(exc)))
    server = LoopbackServer(('127.0.0.1', port), Handler)
    server.expected_host = '127.0.0.1:'+str(server.server_port)
    server.session_url = 'http://'+server.expected_host+'/#'+token
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--collection', help='Persistent local collection directory, shared across sessions')
    args = parser.parse_args()
    session = ReviewSession(args.workspace, args.output, collection=args.collection)
    server = make_server(session, args.port)
    print(server.session_url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
