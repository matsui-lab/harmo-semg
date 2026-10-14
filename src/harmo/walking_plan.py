"""Source-bound, data-driven plans for bounded lower-limb EMG diagnostics.

No dataset-specific branch and no metadata confirmation is scientific approval.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import numpy as np
import scipy
from lxml import etree

from harmo.automatic_workflow import collect_saved_documents
from harmo.document_contract import resolve, fingerprint
from harmo.document_evidence import check_record
from harmo.footswitch_timing import profile, sha, foot_side, state_masks, detect_cycles
from harmo.public_export import load_record, checked_samples
from harmo.lower_limb_terms import FULL_NAMES

VERSION = 'walking-plan/1'
LOWER_LIMB = frozenset(FULL_NAMES)
LIMITS = dict(records=100, channels=16, cells=20_000_000, curves=256,
              cycles=500, comparisons=128)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def save(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)


def read(path, maximum=32*1024**2):
    path = Path(path)
    if path.stat().st_size > maximum:
        raise ValueError('JSON_size_budget')
    return json.loads(path.read_text())


def limits(value=None):
    value = dict(LIMITS, **(value or {}))
    ceilings = dict(records=100, channels=256, cells=20_000_000, curves=2048,
                    cycles=2000, comparisons=10000)
    if set(value) != set(ceilings) or any(type(v) is not int or not 1 <= v <= ceilings[k]
                                          for k, v in value.items()):
        raise ValueError('bounded_integer_limits_required')
    return value


def bind(inputs, path):
    path = Path(path).resolve()
    inputs[str(path)] = sha(path)


def verify(inputs):
    for path, checksum in inputs.items():
        if sha(path) != checksum:
            raise ValueError('bound_input_changed: ' + path)


def review_documents(receipt_path, summary_path, inputs):
    """Use the existing review protocol, including explicit hold/reject records."""
    from harmo.semantic_review import checked_packet, reviewed_documents
    receipt = read(receipt_path)
    bind(inputs, receipt_path)
    for key in ('packet', 'decisions'):
        path = receipt[key+'_path']
        if sha(path) != receipt[key+'_sha256']:
            raise ValueError('review_receipt_changed')
        bind(inputs, path)
    packet = checked_packet(receipt['packet_path'])
    if Path(packet['summary_path']).resolve() != Path(summary_path).resolve():
        raise ValueError('review_dataset_scope_mismatch')
    for source in packet['bindings']:
        bind(inputs, source['path'])
    return reviewed_documents(receipt['packet_path'], receipt['decisions_path'])


def code_bindings(inputs):
    # Bind all in-package Python implementations used by this workflow, not just
    # the top-level driver. A code change invalidates cached execution.
    for path in sorted(Path(__file__).parent.rglob('*.py')):
        bind(inputs, path)
    import sys
    if getattr(sys, 'frozen', False):
        # PyInstaller stores executed Python bytecode in its executable. Keep
        # this binding in addition to the bundled, inspectable source files.
        bind(inputs, sys.executable)


def prepare(summary_path, budget=None, protocol=None, semantic_review=None, *, offline=False):
    budget = limits(budget)
    settings = profile(protocol)
    summary_path = Path(summary_path).resolve()
    summary = read(summary_path)
    intake = summary['intake']
    inputs = {}
    bind(inputs, summary_path)
    if protocol:
        bind(inputs, protocol)
    code_bindings(inputs)
    entries = intake['entries']
    from harmo.repository_context import REPOSITORIES, records as entry_records, bound_files, source_paths
    if any(e.get('repository') not in ('physionet', *REPOSITORIES) for e in entries):
        raise ValueError('unsupported_repository_intake')
    documents = collect_saved_documents(intake, allow_retrieve=False) if offline else collect_saved_documents(intake)
    if documents['held']:
        raise ValueError('fresh_source_bindings_required: '+str(documents['held']))
    metrics = None
    if semantic_review:
        additions, metrics = review_documents(semantic_review, summary_path, inputs)
        documents['documents'].extend(additions)
    catalog = Path(intake['catalog']).resolve()
    bind(inputs, catalog)
    with sqlite3.connect(catalog.as_uri()+'?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,dataset,name,location FROM records ORDER BY dataset,name,id').fetchall()
    scoped = {}
    intake_issues = []
    for entry in entries:
        issues = (entry.get('errors', []) + entry.get('unsupported_before_download', [])
                  + entry.get('ingestion', {}).get('held', []))
        for issue in issues[:100]:
            intake_issues.append(dict(dataset=entry.get('dataset'),
                record=issue.get('source',issue.get('name',issue.get('file'))),
                reason=issue.get('reason',issue.get('error','source_not_converted'))))
        if len(issues)>100:
            intake_issues.append(dict(dataset=entry.get('dataset'),reason='additional_intake_issues_in_source_report',omitted=len(issues)-100))
        if not entry.get('source_report'):
            intake_issues.append(dict(dataset=entry.get('dataset'), reason='no_acquired_source_report'))
            continue
        stage = Path(entry['source_report']).resolve()
        if entry['repository'] == 'physionet':
            for name in ('description.html', 'source.evidence.json'): bind(inputs, stage/name)
        else:
            for path in bound_files(entry): bind(inputs,path)
        for record in entry_records(entry):
            if record.get('status') != 'converted':
                intake_issues.append(dict(dataset=entry.get('dataset'), record=record.get('name'), reason='record_not_converted'))
                continue
            key = (record['id'], entry['dataset'], record['name'], str(Path(record['location']).resolve()))
            if key in scoped:
                raise ValueError('duplicate_intake_record')
            scoped[key] = (entry,record)
    selected = [r for r in rows if (r[0], r[1], r[2], str(Path(r[3]).resolve())) in scoped]
    if len(selected) != len(scoped):
        raise ValueError('catalog_intake_scope_mismatch')
    # A declared non-EMG stream is a companion, not an EMG work item. Unknown
    # modalities remain candidates. Exact trial identity does not imply sync.
    companions, targets = [], []
    for row in selected:
        proposal_path=Path(row[3])/'proposal.json'
        proposal=read(proposal_path) if proposal_path.exists() else {}
        if proposal_path.exists():bind(inputs,proposal_path)
        if proposal.get('signal_type') in ('Grf','Marker'):
            companions.append(dict(record=row[2],dataset=row[1],record_key=proposal.get('record_key'),
                modality=proposal['signal_type'],source=scoped[(row[0],row[1],row[2],str(Path(row[3]).resolve()))][1].get('source')))
        else:targets.append(row)
    selected=targets
    omitted = max(0, len(selected)-budget['records'])
    plans = []
    for row in selected[:budget['records']]:
        row = (*row[:3], str(Path(row[3]).resolve()))
        item = dict(record_id=row[0], dataset=row[1], record=row[2], row=list(row),
                    channels=[], anchors=[], blockers=[], review_required=[],
                    status='held', omitted_channels=0)
        plans.append(item)
        try:
            directory = Path(row[3])
            for name in ('record.json', 'recording.bundle.json', 'recording.semantic.bundle.json',
                         'recording.semantic.v002.bundle.json', 'trust.report.json',
                         'contract-revisions.sqlite', 'source.screen.json'):
                if (directory/name).exists():
                    bind(inputs, directory/name)
            bundle, _, directory = load_record(row)
            if len(bundle.samples.shape) != 2 or np.prod(bundle.samples.shape) > budget['cells']:
                raise ValueError('record_cell_budget_before_loading')
            entry, source_record = scoped[tuple(row)]
            metadata = read(directory/'record.json')['metadata']
            proposal=metadata.get('proposal',{})
            item['related_streams']=[c for c in companions if c['dataset']==row[1] and c['source']==source_record.get('source')
                and c['record_key'] and c['record_key']==proposal.get('record_key')]
            item['cross_stream_synchronization_asserted']=False
            segments=[s for d in documents['documents'] if d['dataset']==row[1]
                for s in d['result'].get('source_segments',[]) if s['record']==row[2]]
            item['source_segments']=segments
            for path in source_paths(entry,source_record,catalog): bind(inputs,path)
            bind(inputs, directory/'normalized.npz')
            _, values = checked_samples(bundle, directory)
            observations = [o for d in documents['documents'] if d['dataset'] == row[1]
                            for o in d['result']['observations']]
            check = check_record(observations, bundle, row[2])
            if check['conflicts']:
                item['documentary_conflicts'] = check['conflicts']
                raise ValueError('document_or_explicit_review_conflict')
            bundle, resolution = resolve(bundle, row[2], observations)
            item['resolution'] = resolution
            item['contract_sha256'] = fingerprint(bundle)
            item['samples_sha256'] = bundle.samples.checksum
            item['task'] = bundle.task.to_dict() if bundle.task else None
            if bundle.task is None or bundle.task.activity != 'walking':
                item['blockers'].append('source_scoped_walking_required')
            fs = bundle.timebase.sampling_rate_hz
            item['sampling_rate_hz'] = fs
            if not fs or not np.isfinite(fs) or fs <= 2*settings['bandpass_hz'][1]:
                item['blockers'].append('sampling_rate_incompatible_with_registered_filter')
            feet = {}
            for index, channel in enumerate(bundle.channels):
                side = foot_side(channel)
                if side:
                    feet.setdefault(side, []).append(index)
            candidates = []
            for index, channel in enumerate(bundle.channels):
                if foot_side(channel):
                    continue
                anatomy = channel.anatomy.canonical_label if channel.anatomy else None
                missing = []
                if channel.sensor_type != 'surface-emg': missing.append('surface_EMG_evidence')
                if anatomy not in LOWER_LIMB: missing.append('supported_lower_limb_muscle_evidence')
                if channel.side not in ('left', 'right'): missing.append('measurement_side_evidence')
                if missing:
                    item['review_required'].append(dict(channel=channel.name, missing=missing))
                else:
                    candidates.append(dict(index=index, channel=channel.name, channel_id=channel.channel_id,
                                           muscle=anatomy, side=channel.side, unit=channel.unit))
            item['omitted_channels'] = max(0, len(candidates)-budget['channels'])
            item['channels'] = candidates[:budget['channels']]
            if not candidates:
                item['blockers'].append('no_source_resolved_lower_limb_EMG')
            if segments:
                references={(s['reference_role'],s['reference_side']) for s in segments}
                if len(references)!=1:item['blockers'].append('conflicting_source_stride_references')
                duration=(values.shape[0]-1)/fs if fs else float('inf')
                if not settings['cycle_seconds'][0]<=duration<=settings['cycle_seconds'][1]:
                    item['blockers'].append('source_stride_duration_outside_registered_bounds')
                if not item['blockers']:
                    item['recipe']='source-delimited-stride-envelope/2'
                    item['source_phase_reference']=':'.join(next(iter(references)))
                    for side in sorted({c['side'] for c in item['channels']}):
                        item['anchors'].append(dict(side=side,anchor='source-stride-boundaries',
                            channel=None,channel_index=None,status='sufficient_for_diagnostic',
                            cycles=[dict(start=0,end=values.shape[0]-1,duration_seconds=duration)],
                            omitted_cycles=0,source_boundary_evidence=segments,
                            detector_used=False,independently_validated=False))
                    item['status']='diagnostic_ready'
                continue
            elif not feet:
                item['blockers'].append('same_side_discrete_footswitch_required')
            if item['blockers']:
                continue
            for side in sorted({c['side'] for c in item['channels']}):
                indices = feet.get(side, [])
                if len(indices) != 1:
                    item['review_required'].append(dict(side=side, missing=['unique_same_side_footswitch']))
                    continue
                fi = indices[0]
                try:
                    levels, masks = state_masks(values[:, fi], settings['additive_level_relative_tolerance'])
                    for anchor, mask in masks.items():
                        events = detect_cycles(mask, fs, settings)
                        accepted = events.pop('cycles')
                        events['omitted_cycles'] = max(0, len(accepted)-budget['cycles'])
                        events['cycles'] = accepted[:budget['cycles']]
                        if len(events['cycles']) < settings['minimum_cycles']:
                            events['status'] = 'held'
                        item['anchors'].append(dict(side=side, channel=bundle.channels[fi].name,
                            channel_index=fi, anchor=anchor, levels=levels.tolist(), **events))
                except ValueError as exc:
                    item['review_required'].append(dict(side=side, channel=bundle.channels[fi].name,
                                                       missing=[str(exc)]))
            if any(a['status'] == 'sufficient_for_diagnostic' for a in item['anchors']):
                item['status'] = 'diagnostic_ready'
            else:
                item['blockers'].append('no_sufficient_supported_event_cycles')
        except (ValueError, OSError, KeyError, TypeError) as exc:
            item['blockers'].append(str(exc))
    verify(inputs)
    result = dict(version=VERSION, summary_path=str(summary_path), profile=settings, limits=budget,
                  inputs=inputs, records=plans, omitted_records=omitted, intake_issues=intake_issues,
                  intake_status=intake.get('status', 'not-reported'),
                  upstream_assessment_status=summary.get('assessment', {}).get('status', 'not-reported'),
                  semantic_review_metrics=metrics, numpy_version=np.__version__, scipy_version=scipy.__version__,
                  registry=dict(event='discrete-electrical-rising-edge/1',
                                processing='continuous-bandpass-centered-RMS/1',
                                source_segment_processing='source-delimited-stride-envelope/2',
                                comparison='same-dataset-task-muscle-side-reviewed-event-or-electrical-anchor/2'),
                  scientific_approval=False, amplitude_pooling_allowed=False)
    result['plan_id'] = digest(result)
    return result


def review_template(plan):
    """No invented reviewer, measured time, physiological mapping or approval."""
    decisions = []
    for record in plan['records']:
        if record['status'] != 'diagnostic_ready':
            continue
        decisions.append(dict(record_id=record['record_id'], field='input_representation',
                              side=None, anchor=None, action='unreviewed', value=None,
                              evidence=[], scope_reason=''))
        for anchor in record['anchors']:
            decisions.append(dict(record_id=record['record_id'], field='event_identity',
                                  side=anchor['side'], anchor=anchor['anchor'], action='unreviewed',
                                  value=None, evidence=[], scope_reason=''))
    return dict(version='walking-review/1', plan_id=plan['plan_id'], reviewer='',
                active_seconds=None, decisions=decisions,
                instructions='Confirm only source-supported meaning and exact record scope; hold/reject stops affected processing. Confirmation is not detector validation.')


def checked_review(plan, path=None):
    template = review_template(plan)
    if path is None:
        return {}, {}, dict(reviewed=0, unreviewed=len(template['decisions']), self_reported_active_seconds=None)
    data = read(path, 2*1024**2)
    if data.get('version') != template['version'] or data.get('plan_id') != plan['plan_id']:
        raise ValueError('event_review_plan_changed')
    if not isinstance(data.get('reviewer'), str) or not data['reviewer'].strip():
        raise ValueError('named_reviewer_required')
    seconds = data.get('active_seconds')
    if seconds is not None and (type(seconds) not in (int, float) or not np.isfinite(seconds) or not 0 <= seconds < 86400):
        raise ValueError('finite_self_reported_review_time_required')
    key = lambda d: (d['record_id'], d['field'], d['side'], d['anchor'])
    allowed = {key(d) for d in template['decisions']}
    result, inputs, seen = {}, {}, set()
    bind(inputs, path)
    for decision in data['decisions']:
        k = key(decision)
        if k not in allowed or k in seen:
            raise ValueError('unknown_or_duplicate_event_review_target')
        seen.add(k)
        action = decision['action']
        if action not in ('unreviewed', 'confirm', 'hold', 'reject'):
            raise ValueError('invalid_event_review_action')
        if action == 'unreviewed':
            continue
        if not isinstance(decision.get('scope_reason'), str) or not decision['scope_reason'].strip():
            raise ValueError('explicit_event_scope_reason_required')
        if action == 'confirm':
            choices = ('raw-emg', 'rectified-emg', 'envelope') if decision['field'] == 'input_representation' else ('initial-contact', 'toe-off')
            if decision['value'] not in choices or not decision.get('evidence'):
                raise ValueError('supported_value_and_evidence_required')
            for proof in decision['evidence']:
                source = Path(proof['path']).resolve()
                if source.stat().st_size > 2*1024**2 or sha(source) != proof['sha256']:
                    raise ValueError('event_evidence_changed_or_size_budget')
                raw = source.read_text()
                if source.suffix.lower() in ('.html', '.htm', '.xml'):
                    root = etree.fromstring(raw.encode(), etree.HTMLParser(no_network=True))
                    raw = ' '.join(root.itertext()) if root is not None else ''
                quote = proof.get('quote')
                if not isinstance(quote, str) or not quote.strip() or ' '.join(quote.split()) not in ' '.join(raw.split()):
                    raise ValueError('event_evidence_quote_not_found')
                if not isinstance(proof.get('pointer'), str) or not proof['pointer'].strip():
                    raise ValueError('event_evidence_pointer_required')
                bind(inputs, source)
        result[k] = dict(decision, reviewer=data['reviewer'])
    assigned = set()
    for decision in result.values():
        if decision['action'] == 'confirm' and decision['field'] == 'event_identity':
            identity = (decision['record_id'], decision['side'], decision['value'])
            if identity in assigned:
                raise ValueError('multiple_electrical_anchors_for_same_reviewed_event')
            assigned.add(identity)
    return result, inputs, dict(reviewed=len(result), unreviewed=len(allowed)-len(result),
                               self_reported_active_seconds=seconds, independently_timed=False)
