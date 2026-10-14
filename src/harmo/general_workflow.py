"""One bounded, resumable batch for source-to-walking-EMG diagnostics.

Acquisition, semantic review, numerical processing and scientific permission
are separate states. Completed computation is never a permission upgrade.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import time

from harmo.walking_plan import (prepare, review_template, read, save, digest, sha,
                               limits, verify, checked_review, code_bindings)
from harmo.walking_execute import execute

VERSION = 'harmonize-batch/1'


def checked_jobs(jobs):
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 16:
        raise ValueError('1_to_16_jobs_required')
    normalized, identities = [], set()
    for job in jobs:
        if not isinstance(job, dict) or set(job)-{'url', 'summary', 'semantic_review', 'event_review'}:
            raise ValueError('job_requires_URL_or_summary_and_optional_review_paths')
        if ('url' in job) == ('summary' in job) or any(not isinstance(v, str) or not v for v in job.values()):
            raise ValueError('exactly_one_URL_or_summary_required')
        result = dict(job)
        for k in ('summary', 'semantic_review', 'event_review'):
            if k in result: result[k] = str(Path(result[k]).resolve())
        if 'url' in result:
            from harmo.mixed_batch import route
            spec = route(result['url'])
            if spec['repository'] not in ('physionet','figshare','mendeley','zenodo'):
                raise ValueError('versioned_single_dataset_URL_required')
            result['url'] = spec['canonical']
        identity = result.get('url', result.get('summary'))
        if identity in identities: raise ValueError('duplicate_job_source')
        identities.add(identity)
        normalized.append(result)
    return normalized


@contextmanager
def locked(root):
    from harmo.local_platform import exclusive_file
    try:
        with exclusive_file(root/'batch.lock'): yield
    except BlockingIOError:
        raise ValueError('batch_already_running') from None


def artifact_manifest(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink(): raise ValueError('symlink_in_batch_artifacts')
        if path.is_file() and path.name != 'completion.json':
            result[str(path.relative_to(directory))] = sha(path)
    return result


def check_artifacts(directory, hashes):
    for relative, checksum in hashes.items():
        path = (directory/relative).resolve()
        if not path.is_relative_to(directory.resolve()) or sha(path) != checksum:
            raise ValueError('cached_artifact_changed: '+relative)


def cache_key(plan, event_review, model, port, max_targets):
    _, extra, _ = checked_review(plan, event_review)
    return digest(dict(plan_id=plan['plan_id'], review_inputs=extra,
                       model=model, port=port, max_targets=max_targets))


def queue(plan, result):
    """Actionable, counted requests, not estimates of human effort or accuracy."""
    items = []
    for issue in plan.get('intake_issues', []):
        items.append(dict(record_id=None,record=issue.get('record'),dataset=issue.get('dataset'),
            scope='source-intake',reason=issue['reason'],
            required_action='Resolve the reported source format, timebase, acquisition or resource limit before record-level review; see the preserved intake report.'))
    def add(record, scope, code, action):
        items.append(dict(record_id=record['record_id'], record=record['record'],
                          scope=scope, reason=code, required_action=action))
    for r in plan['records']:
        actual = next(x for x in result['records'] if x['record_id'] == r['record_id'])
        computed = [c for c in result['curves'] if c['record_id'] == r['record_id']]
        for reason in r['blockers']:
            add(r, 'record', reason, 'Check source metadata and supported input conditions; do not override the hold without resolving its cause.')
        for request in r['review_required']:
            add(r, request.get('channel', request.get('side', 'record')), ','.join(request['missing']),
                'Use the source-bound semantic packet to confirm sensor, exact muscle, side or task; unsupported array-to-muscle mappings need a separate validated design.')
        if r['status'] == 'diagnostic_ready':
            for reason in actual['blockers']:
                if reason not in r['blockers']:
                    add(r, 'record', reason, 'Resolve the explicit processing hold in a new source-bound review; changing a status flag is insufficient.')
            if not computed or any(c['representation_status'] != 'named-human-source-review' for c in computed):
                add(r, 'record', 'input_representation_and_processing_history',
                    'Read the acquisition/processing description. Confirm raw EMG with a quote in a copy of event-review.template.json; known envelopes must not receive the raw-EMG filter.')
            for a in r['anchors']:
                if a['anchor']=='source-stride-boundaries':
                    add(r,'source-stride','source_segmentation_and_filter_boundary_validation',
                        'Check the source-defined stride boundaries and their side/reference, raw-versus-processed representation, and filtering sensitivity without neighboring samples. One stride supplies no within-record distribution.')
                    continue
                selected = [c for c in computed if c['side'] == a['side'] and c['anchor'] == a['anchor']]
                reviewed = selected and all(c['event_evidence_status'] == 'named-human-source-review' for c in selected)
                add(r, a['side']+':'+a['anchor'], 'detector_ground_truth_validation' if reviewed else 'physiological_event_identity_and_detector_validation',
                    'Validate detected event times against independent reference labels.' if reviewed else
                    'Find the voltage-state/contact/polarity definition for this record and quote it in the event review. Separately validate detected event times against reference labels.')
            add(r, 'record', 'scientific_comparison_design',
                'Specify participant/condition pairing and check sensitivity before interpreting diagnostic record-to-record differences.')
    from harmo.review_routing import route_work
    routing = route_work(items)
    return dict(version='walking-work-queue/1', items=routing['items'], item_count=len(items),
                routing_counts=routing['counts'],
                event_review_metrics=result['event_review_metrics'],
                human_time_measured=False, LLM_accuracy_evaluated=False,
                automatic_scientific_approvals=0)


def perform_job(job, folder, config, runtime_inputs):
    folder.mkdir(parents=True, exist_ok=True)
    if 'summary' in job:
        summary_path = Path(job['summary'])
    else:
        intake_receipt = folder/'intake.json'
        if intake_receipt.exists():
            receipt = read(intake_receipt)
            summary_path = Path(receipt['summary'])
            if sha(summary_path) != receipt['sha256']:
                raise ValueError('saved_intake_summary_changed')
        else:
            from harmo.automatic_workflow import run as acquire
            intake_number = len(list(folder.glob('acquisition-*')))+1
            report = acquire([job['url']], folder/('acquisition-%03d' % intake_number),
                ['activation-timing', 'amplitude-pooling'], config['r_library'],
                config['max_bytes_per_url'], config['limits']['records'], config['limits']['comparisons'])
            summary_path = Path(report['intake']['run_dir'])/'automatic.summary.json'
            save(intake_receipt, dict(summary=str(summary_path), sha256=sha(summary_path)))
    verify(runtime_inputs)
    plan = prepare(summary_path, config['limits'], config['protocol'], job.get('semantic_review'))
    key = cache_key(plan, job.get('event_review'), config['local_llm_model'], config['local_llm_port'], config['max_llm_targets'])
    for attempt in sorted(folder.glob('attempt-*')):
        receipt = attempt/'completion.json'
        if not receipt.is_file(): continue
        completed = read(receipt)
        if completed['cache_key'] != key: continue
        verify(completed['inputs'])
        check_artifacts(attempt, completed['artifacts'])
        llm_state = completed['summary'].get('local_llm')
        if llm_state and (llm_state['status'] == 'failed' or llm_state.get('errors')):
            continue  # Explicit resume retries optional LLM failures; old files remain.
        return dict(completed['summary'], cache_reused=True)
    attempt = folder/('attempt-%03d' % (len(list(folder.glob('attempt-*')))+1))
    attempt.mkdir()
    save(attempt/'plan.json', plan)
    save(attempt/'event-review.template.json', review_template(plan))
    from harmo.semantic_review import prepare as prepare_semantics, propose
    review_state = prepare_semantics(summary_path, attempt/'semantic-review', config['limits']['channels'],
                                    record_ids={r['record_id'] for r in plan['records']})
    llm = None
    if config['local_llm_model']:
        try:
            llm_result = propose(attempt/'semantic-review/packet.json', attempt/'local-llm',
                config['local_llm_model'], config['local_llm_port'], config['max_llm_targets'])
            llm = dict(status='candidates_generated', attempted=len(llm_result['rows']),
                unprocessed=llm_result['unprocessed_targets'],
                errors=sum('error' in r for r in llm_result['rows']), automatic_approvals=0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            llm = dict(status='failed', error=str(exc), automatic_approvals=0)
    result = execute(plan, attempt/'processing', job.get('event_review'))
    work = queue(plan, result)
    save(attempt/'work-queue.json', work)
    counts = dict(records=len(plan['records']), diagnostic_records=sum(r['curves'] > 0 for r in result['records']),
        curves=len(result['curves']), comparisons=len(result['comparisons']),
        held_records=sum(not r['curves'] for r in result['records']),
        work_items=work['item_count'], semantic_review_items=review_state['review_items'],
        omitted_semantic_review_fields=review_state['omitted_channel_fields'],
        omitted_records=plan['omitted_records'], omitted_curves=result['omitted_curves'],
        omitted_comparisons=result['omitted_comparisons'],
        omitted_channels=sum(r['omitted_channels'] for r in plan['records']),
        omitted_cycles=sum(a['omitted_cycles'] for r in plan['records'] for a in r['anchors']))
    summary = dict(status='processed-with-holds' if result['curves'] else 'held',
                   output=str(attempt), counts=counts, scientific_approval=False, local_llm=llm,
                   intake_status=plan['intake_status'], upstream_assessment_status=plan['upstream_assessment_status'])
    inputs = result['inputs']
    verify(inputs)
    save(attempt/'completion.json', dict(version=VERSION, cache_key=key, inputs=inputs,
         artifacts=artifact_manifest(attempt), summary=summary))
    return dict(summary, cache_reused=False)


def run(jobs, output, *, resume=False, budget=None, protocol=None, r_library='',
        max_bytes_per_url=128*1024**2, local_llm_model=None, local_llm_port=11439, max_llm_targets=32):
    jobs = checked_jobs(jobs)
    budget = limits(budget)
    if type(max_bytes_per_url) is not int or not 1 <= max_bytes_per_url <= 1024**3:
        raise ValueError('per_URL_byte_budget_required')
    if local_llm_model is not None and (not isinstance(local_llm_model, str) or not local_llm_model or
                                       'cloud' in local_llm_model.lower()):
        raise ValueError('installed_local_model_required')
    if type(local_llm_port) is not int or not 1024 <= local_llm_port <= 65535 or type(max_llm_targets) is not int or not 1 <= max_llm_targets <= 256:
        raise ValueError('bounded_local_LLM_options_required')
    from harmo.footswitch_timing import profile
    profile(protocol)  # Validate before acquisition or any writes.
    config = dict(version=VERSION, jobs=jobs, limits=budget, protocol=str(Path(protocol).resolve()) if protocol else None,
        r_library=r_library, max_bytes_per_url=max_bytes_per_url, local_llm_model=local_llm_model,
        local_llm_port=local_llm_port, max_llm_targets=max_llm_targets)
    root = Path(output).resolve()
    if root.exists():
        if not resume or not (root/'batch.json').is_file() or read(root/'batch.json') != config:
            raise ValueError('matching_managed_batch_and_explicit_resume_required')
    else:
        root.mkdir(parents=True)
        save(root/'batch.json', config)
    with locked(root):
        runtime_inputs = {}
        code_bindings(runtime_inputs)
        entries = []
        started = time.monotonic()
        for index, job in enumerate(jobs):
            try:
                result = perform_job(job, root/('job-%03d' % index), config, runtime_inputs)
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
                result = dict(status='failed', error=str(exc), scientific_approval=False)
            entries.append(dict(job=index, source=job, **result))
        report = dict(version=VERSION, entries=entries,
            status='partial' if any(e['status'] == 'failed' for e in entries) else 'completed-with-holds',
            elapsed_seconds=time.monotonic()-started, scientific_approval=False,
            scope='Bounded public-repository intake and lower-limb walking diagnostics; source review is not event ground-truth validation.')
        number = len(list(root.glob('batch-report-v*.json')))+1
        path = root/('batch-report-v%03d.json' % number)
        save(path, report)
    return dict(report, report=str(path))


def configure(p):
    p.add_argument('--jobs', required=True, help='JSON list of {url: ...} or {summary: ...}, optional semantic_review/event_review paths')
    p.add_argument('--output', required=True)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--max-records', type=int, default=2)
    p.add_argument('--max-channels', type=int, default=16)
    p.add_argument('--max-curves', type=int, default=256)
    p.add_argument('--max-cycles', type=int, default=500)
    p.add_argument('--max-comparisons', type=int, default=128)
    p.add_argument('--max-bytes-per-url', type=int, default=128*1024**2)
    p.add_argument('--protocol')
    p.add_argument('--r-library', default='')
    p.add_argument('--local-llm-model')
    p.add_argument('--local-llm-port', type=int, default=11439)
    p.add_argument('--max-llm-targets', type=int, default=32)
    return p


def command(a):
    try:
        result = run(read(a.jobs), a.output, resume=a.resume,
            budget=dict(records=a.max_records, channels=a.max_channels, curves=a.max_curves,
                        cycles=a.max_cycles, comparisons=a.max_comparisons),
            protocol=a.protocol, r_library=a.r_library, max_bytes_per_url=a.max_bytes_per_url,
            local_llm_model=a.local_llm_model, local_llm_port=a.local_llm_port, max_llm_targets=a.max_llm_targets)
        print(json.dumps(dict(status=result['status'], report=result['report'],
                              jobs=[{k: v for k, v in e.items() if k in ('job', 'status', 'counts', 'cache_reused', 'error')} for e in result['entries']]), indent=2))
        return 1 if result['status'] == 'partial' else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('ERROR: '+str(exc))
        return 1


def main(argv=None):
    return command(configure(argparse.ArgumentParser(description=__doc__)).parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
