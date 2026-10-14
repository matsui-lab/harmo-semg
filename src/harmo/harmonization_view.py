"""Verified display products of an executed plan; never shift or approve curves."""
from pathlib import Path
import numpy as np

from harmo.public_export import load_record, checked_samples
from harmo.semantic_context import extrema_preview
from harmo.walking_plan import verify, sha
from harmo.physiology_control import _normalise_cycle
from harmo.footswitch_timing import envelope as continuous_envelope
from harmo import segment_envelope


def build(plan, result, processing, maximum_curves=32, maximum_pairs=64):
    verify(plan['inputs'])
    selected = result['curves'][:maximum_curves]
    curves, pairs, inputs = [], [], {}
    by_record = {r['record_id']: r for r in plan['records']}
    for identity in dict.fromkeys(c['record_id'] for c in selected):
        record = by_record[identity]
        bundle, _, directory = load_record(record['row'])
        time, values = checked_samples(bundle, directory)
        envelopes = {}
        for curve in (c for c in selected if c['record_id']==identity):
            index = next(i for i,c in enumerate(bundle.channels) if c.channel_id==curve['channel_id'])
            source = bundle.channels[index]
            if index not in envelopes:
                envelopes[index] = (segment_envelope.envelope(values[:,index],record['sampling_rate_hz'],plan['profile'])[0]
                    if curve.get('recipe')==segment_envelope.VERSION else
                    continuous_envelope(values[:,index],record['sampling_rate_hz'],plan['profile']))
            path = Path(processing)/(curve['key']+'.npz')
            inputs[str(path)] = sha(path)
            with np.load(path,allow_pickle=False) as saved:
                cycles=saved['cycles'].copy();start=int(saved['starts'][0]);end=int(saved['ends'][0])
                phase=saved['phase'].copy()
            if not 0<=start<end<len(time) or phase.shape!=(101,) or not np.array_equal(phase,np.linspace(0,100,101)):
                raise ValueError('display_cycle_coordinates_changed')
            env = envelopes[index][start:end+1]
            if not np.allclose(_normalise_cycle(env,101),cycles[0],rtol=1e-12,atol=1e-12):
                raise ValueError('display_processing_does_not_reproduce_saved_cycle')
            if not np.array_equal(np.median(cycles,axis=0),np.asarray(curve['median'])):
                raise ValueError('display_median_does_not_match_saved_output')
            local_time=time[start:end+1]-time[start]
            mappings=[dict(field='muscle',before=source.muscle,after=curve['muscle'],source_label=source.name),
                      dict(field='side',before=source.side,after=curve['side']),
                      dict(field='task',before=bundle.task.activity if bundle.task else None,after=curve['task']['activity']),
                      dict(field='unit',before=source.unit,after=source.unit)]
            curves.append(dict(key=curve['key'],record_id=identity,record=curve['record'],
                channel=source.name,muscle=curve['muscle'],side=curve['side'],mappings=mappings,
                raw=extrema_preview(local_time,values[start:end+1,index],600),
                envelope=extrema_preview(local_time,env,600),unit=source.unit,
                input_interval_seconds=[float(time[start]),float(time[end])],
                source_samples_sha256=curve['source_samples_sha256'],output_sha256=inputs[str(path)],
                phase=phase.tolist(),median=curve['median'],q25=curve['q25'],q75=curve['q75'],
                cycles=curve['cycles'],source_window='first accepted cycle',
                aggregation='single cycle' if curve['cycles']==1 else 'median of accepted cycles',
                event_identity=curve['event_identity'],comparison_reference=curve['comparison_reference'],
                supported_phase=curve.get('timing_features',{}).get('supported_phase_percent'),
                recipe=curve.get('recipe',plan['registry']['processing']),
                timing_features=curve.get('timing_features'),
                steps=dict(bandpass_hz=plan['profile']['bandpass_hz'],filter_order=plan['profile']['filter_order'],
                    rms_requested_ms=1000*plan['profile']['rms_seconds'],phase_points=101,
                    boundaries='source-provided' if curve.get('segmentation') else 'electrical-state',
                    amplitude='per-cycle peak scaling before and after linear phase interpolation; then median',
                    shift_correction_applied=False),
                note='Raw/RMS show the first accepted cycle; phase plot shows saved median. No MVC scaling, lag correction or scientific approval.'))
    lookup={c['key']:c for c in curves}
    eligible=[p for p in result['comparisons'] if p['left_curve'] in lookup and p['right_curve'] in lookup]
    for pair in eligible[:maximum_pairs]:
        a,b=lookup[pair['left_curve']],lookup[pair['right_curve']]
        supported = pair.get('supported_metrics')
        # Never promote withheld endpoint maxima from unqualified_peak_diagnostics.
        if supported is not None:
            delta=supported['provisional_peak_difference_percent']
            peak=dict(value=delta,unit='percentage-points-of-cycle',status='held' if delta is None else 'provisional-interior-candidate',
                global_status=supported['global_peak_status'],correspondence_established=False,
                left_phase=(a.get('timing_features') or {}).get('single_peak_percent'),
                right_phase=(b.get('timing_features') or {}).get('single_peak_percent'))
        else:
            peak=dict(value=pair.get('circular_peak_difference_percent'),unit='percentage-points-of-cycle',
                status='diagnostic-only',global_status='not-independently-validated',correspondence_established=False,
                left_phase=pair.get('left_peak_percent'),right_phase=pair.get('right_peak_percent'))
        mask=None if a['supported_phase'] is None or b['supported_phase'] is None else sorted(set(a['supported_phase'])&set(b['supported_phase']))
        pairs.append(dict(left=a['key'],right=b['key'],muscle=pair['muscle'],side=pair['side'],
            difference=(np.asarray(b['median'])-np.asarray(a['median'])).tolist(),
            supported_phase=mask,peak_difference=peak,
            shape_rmse=supported.get('common_supported_shape_RMSE') if supported is not None else pair.get('shape_RMSE'),
            shape_scope='common-supported-points' if supported is not None else 'diagnostic-cycle',
            shift_correction_applied=False,scientific_approval=False))
    verify(plan['inputs']);verify(inputs)
    return dict(version='harmonization-view/1',curves=curves,pairs=pairs,
                omitted_curves=len(result['curves'])-len(curves),
                omitted_pairs=len(result['comparisons'])-len(pairs),inputs=inputs,
                waveform_units='source unit for raw/RMS; dimensionless peak-scaled values for phase plots',
                shift_correction_applied=False,scientific_approval=False)
