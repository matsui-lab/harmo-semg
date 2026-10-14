"""Execute only registered numerical operations from a checked walking plan."""
from itertools import combinations
from pathlib import Path
import csv

import numpy as np

from harmo.footswitch_timing import envelope, compare, peak
from harmo.physiology_control import _normalise_cycle
from harmo.public_export import load_record, checked_samples
from harmo.walking_plan import digest, verify, checked_review, save
from harmo import segment_envelope


def execute(plan, output, review=None):
    identity = dict(plan)
    if identity.pop('plan_id') != digest(identity):
        raise ValueError('plan_changed')
    verify(plan['inputs'])
    decisions, review_inputs, metrics = checked_review(plan, review)
    inputs = dict(plan['inputs'], **review_inputs)
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    curves, records, segment_sensitivity = [], [], []
    omitted_curves = 0
    for item in plan['records']:
        record = dict(record_id=item['record_id'], record=item['record'], status='held',
                      blockers=list(item['blockers']), curves=0)
        records.append(record)
        if item['status'] != 'diagnostic_ready':
            continue
        representation = decisions.get((item['record_id'], 'input_representation', None, None))
        if representation and (representation['action'] in ('hold', 'reject') or representation['value'] != 'raw-emg'):
            record['blockers'].append('reviewed_input_not_supported_by_raw_EMG_recipe')
            continue
        # No filtering of an explicitly rectified/envelope contract.
        bundle, _, directory = load_record(item['row'])
        known = bundle.processing_state.representation if bundle.processing_state else None
        if known not in (None, 'unknown', 'raw', 'raw-emg', 'unprocessed'):
            record['blockers'].append('existing_processing_representation_requires_other_recipe: '+str(known))
            continue
        _, values = checked_samples(bundle, directory)
        envelopes, support_metadata = {}, {}
        for anchor in item['anchors']:
            if anchor['status'] != 'sufficient_for_diagnostic':
                continue
            decision = decisions.get((item['record_id'], 'event_identity', anchor['side'], anchor['anchor']))
            if decision and decision['action'] in ('hold', 'reject'):
                record['blockers'].append('explicit_event_review_hold:'+anchor['side']+':'+anchor['anchor'])
                continue
            recipe=item.get('recipe')
            segmented=recipe in ('source-delimited-stride-envelope/1',segment_envelope.VERSION)
            corrected=recipe==segment_envelope.VERSION
            label = decision['value'] if decision else 'source-delimited-stride' if segmented else 'unverified-electrical-state'
            for channel in item['channels']:
                if channel['side'] != anchor['side']:
                    continue
                if len(curves) >= plan['limits']['curves']:
                    omitted_curves += 1
                    continue
                ci = channel['index']
                try:
                    if ci not in envelopes:
                        if corrected:
                            envelopes[ci],support_metadata[ci]=segment_envelope.envelope(values[:,ci],item['sampling_rate_hz'],plan['profile'])
                        else:envelopes[ci] = envelope(values[:, ci], item['sampling_rate_hz'], plan['profile'])
                    samples = np.vstack([_normalise_cycle(envelopes[ci][c['start']:c['end']+1], 101)
                                         for c in anchor['cycles']])
                    key = 'curve-%06d' % len(curves)
                    np.savez(out/(key+'.npz'), phase=np.linspace(0, 100, 101), cycles=samples,
                             starts=np.array([c['start'] for c in anchor['cycles']]),
                             ends=np.array([c['end'] for c in anchor['cycles']]))
                    task_group = digest(dict(activity=item['task']['activity'], facets=item['task']['facets']))
                    curve = dict(key=key, record_id=item['record_id'], record=item['record'], dataset=item['dataset'],
                        source_samples_sha256=item['samples_sha256'], task_group=task_group, task=item['task'],
                        muscle=channel['muscle'], side=channel['side'], channel=channel['channel'],
                        channel_id=channel['channel_id'], anchor=anchor['anchor'], event_identity=label,
                        comparison_reference=(('reviewed:'+label if decision else 'source-segment')+':'+item['source_phase_reference']+':cropped'
                            if segmented else 'reviewed:'+label if decision else 'electrical:'+anchor['anchor']),
                        event_evidence_status='named-human-source-review' if decision else 'source-declaration-not-independent-validation' if segmented else 'unverified',
                        representation_status='named-human-source-review' if representation else 'raw-EMG-assumption',
                        detector_validated_against_ground_truth=False, cycles=len(samples),
                        median=np.median(samples, axis=0).tolist(), q25=np.quantile(samples, .25, axis=0).tolist(),
                        q75=np.quantile(samples, .75, axis=0).tolist())
                    if segmented:
                        curve.update(recipe=item['recipe'],segmentation='source-provided-not-detected',
                            aggregation='single-stride-no-within-record-distribution',
                            filter_boundary_effects_validated=False,source_boundaries_independently_validated=False,
                            boundary_warning='Filtering a cropped stride lacks neighboring raw samples; endpoint values and timing require sensitivity review.')
                        if corrected:
                            support=segment_envelope.phase_support(support_metadata[ci],len(values))
                            curve['boundary_support']={k:v for k,v in support_metadata[ci].items() if k!='support_mask'}
                            curve['timing_features']=segment_envelope.features(samples[0],support)
                            curve['comparison_reference']+=':'+segment_envelope.VERSION
                        padding=min(len(values)-1,int(round(item['sampling_rate_hz']*plan['profile']['edge_margin_seconds'])))
                        for mode in ('reflect','edge'):
                            padded=np.pad(values[:,ci],padding,mode=mode)
                            alt_env=segment_envelope.envelope(padded,item['sampling_rate_hz'],plan['profile'])[0] if corrected else envelope(padded,item['sampling_rate_hz'],plan['profile'])
                            alt=_normalise_cycle(alt_env[padding:-padding],101)
                            segment_sensitivity.append(dict(curve=key,padding=mode,padding_samples=padding,
                                maximum_normalized_difference=float(np.max(np.abs(alt-samples[0]))),
                                peak_shift_percent_stride=(peak(alt)-peak(samples[0])+50)%100-50,
                                interpretation='Assumed missing-context sensitivity, not observed neighboring data or ground truth.'))
                    curves.append(curve)
                    record['curves'] += 1
                except ValueError as exc:
                    record['blockers'].append(channel['channel']+':'+str(exc))
        if record['curves']:
            record['status'] = 'diagnostic_curves_created'
    # Partition before enumeration. No all-channel Cartesian products and no
    # implicit equivalence across study, task context, side or event meaning.
    groups = {}
    for c in curves:
        key = tuple(c[k] for k in ('dataset', 'task_group', 'muscle', 'side',
                                   'comparison_reference', 'representation_status'))
        groups.setdefault(key, []).append(c)
    comparisons = []
    eligible = 0
    duplicate_pairs = 0
    for key, group in sorted(groups.items()):
        for a, b in combinations(group, 2):
            if a['record_id'] == b['record_id']:
                continue
            if a['source_samples_sha256'] == b['source_samples_sha256']:
                duplicate_pairs += 1
                continue
            eligible += 1
            if len(comparisons) < plan['limits']['comparisons']:
                comparisons.append(dict(compare(a, b), left_curve=a['key'], right_curve=b['key'],
                    task_group=a['task_group'], event_identity=a['event_identity'],
                    left_anchor=a['anchor'], right_anchor=b['anchor'], comparison_reference=a['comparison_reference'],
                    population_matching_performed=False, detector_ground_truth_available=False))
                if a.get('segmentation')=='source-provided-not-detected':
                    comparisons[-1].update(interpretation_status='source-segment-boundary-sensitivity-unresolved',
                        left_max_peak_sensitivity_percent_stride=max(abs(s['peak_shift_percent_stride']) for s in segment_sensitivity if s['curve']==a['key']),
                        right_max_peak_sensitivity_percent_stride=max(abs(s['peak_shift_percent_stride']) for s in segment_sensitivity if s['curve']==b['key']),
                        scientific_comparison_approved=False)
                    if 'timing_features' in a and 'timing_features' in b:
                        comparisons[-1]['supported_metrics']=segment_envelope.compare_supported(a,b)
                        fields=('left_peak_percent','right_peak_percent','circular_peak_difference_percent')
                        comparisons[-1]['unqualified_peak_diagnostics']={k:comparisons[-1][k] for k in fields}
                        comparisons[-1].update({k:None for k in fields})
                        comparisons[-1]['global_peak_status']='held-unobserved-boundary-context'
    sensitivity = []
    by_channel = {}
    for c in curves:
        by_channel.setdefault((c['record_id'], c['channel_id']), []).append(c)
    for group in by_channel.values():
        for a, b in combinations(group, 2):
            if a['anchor'] != b['anchor']:
                sensitivity.append(dict(record=a['record'], channel=a['channel'], anchor_a=a['anchor'], anchor_b=b['anchor'],
                    peak_difference=(peak(b['median'])-peak(a['median'])+50)%100-50,
                    cycles_a=a['cycles'], cycles_b=b['cycles'],
                    interpretation='combined_anchor_and_cycle_selection_sensitivity'))
    verify(inputs)
    report = dict(version='walking-execution/1', plan_id=plan['plan_id'], inputs=inputs,
        profile=plan['profile'], records=records, curves=curves, comparisons=comparisons,
        anchor_sensitivity=sensitivity, event_review_metrics=metrics,
        segment_boundary_sensitivity=segment_sensitivity,
        omitted_curves=omitted_curves, omitted_comparisons=eligible-len(comparisons),
        duplicate_sample_pairs_excluded=duplicate_pairs,
        scientific_approval=False, amplitude_pooling_allowed=False,
        status='diagnostic_only' if curves else 'held',
        interpretation='Separate record curves, not pooled subjects or qualified scientific comparisons.')
    save(out/'diagnostic.json', report)
    with (out/'curves.csv').open('x') as stream:
        fields = ['record', 'channel', 'muscle', 'side', 'anchor', 'event_identity', 'cycles', 'phase', 'median', 'q25', 'q75']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for c in curves:
            for phase in range(101):
                writer.writerow(dict({k: c[k] for k in fields[:7]}, phase=phase,
                                     **{k: c[k][phase] for k in ('median', 'q25', 'q75')}))
    return report
