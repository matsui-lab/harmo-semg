"""Available-sample RMS and explicitly provisional peak support for cropped EMG."""
import numpy as np
from scipy import signal

VERSION='source-delimited-stride-envelope/2'
FEATURE_VERSION='supported-multiple-peaks/1'
POLICY=dict(pole_decay_tolerance=.001,minimum_peak_prominence=.05,
            peak_distance_percent=3,competing_peak_height_gap=.05,
            status='engineering-screen-not-physiologically-validated')


def available_rms(filtered,window):
    x=np.asarray(filtered,dtype=float)
    if x.ndim!=1 or not np.isfinite(x).all() or type(window) is not int or not 1<=window<len(x):
        raise ValueError('finite_signal_and_shorter_positive_window_required')
    weights=np.ones(window)
    counts=np.convolve(np.ones(len(x)),weights,mode='same')
    return np.sqrt(np.maximum(0,np.convolve(x*x,weights,mode='same')/counts)),counts


def envelope(values,fs,settings):
    x=np.asarray(values,dtype=float)
    if x.ndim!=1 or len(x)<32 or not np.isfinite(x).all():raise ValueError('finite_source_EMG_required')
    if not np.isfinite(fs) or fs<=0 or settings['bandpass_hz'][1]>=fs/2:raise ValueError('bandpass_above_nyquist')
    sos=signal.butter(settings['filter_order'],settings['bandpass_hz'],btype='bandpass',fs=fs,output='sos')
    filtered=signal.sosfiltfilt(sos,x-x.mean())
    window=max(1,int(round(settings['rms_seconds']*fs)))
    if window%2==0:window+=1
    rms,counts=available_rms(filtered,window)
    radius=float(np.max(np.abs(signal.sos2zpk(sos)[1])))
    if not 0<radius<1:raise ValueError('stable_filter_required')
    # Pole decay is a screening heuristic, not a bound on error for arbitrary
    # missing samples. Add the RMS half-window because smoothing spreads errors.
    filter_margin=int(np.ceil(np.log(POLICY['pole_decay_tolerance'])/np.log(radius)))
    margin=filter_margin+window//2
    indices=np.arange(len(x));support=(indices>=margin)&(indices<len(x)-margin)&(counts==window)
    return rms,dict(version=VERSION,policy=dict(POLICY),rms_window_samples=window,
        boundary_normalization='observed-sample-count',filter_margin_samples=filter_margin,
        combined_margin_samples=margin,supported_samples=int(support.sum()),
        support_mask=support,minimum_RMS_sample_fraction=float(counts.min()/window),
        uncertainty='Incomplete RMS windows have less information; missing context and filter boundary errors are not reconstructed.')


def phase_support(metadata, sample_count):
    # A phase point is supported only if both interpolation neighbors are.
    coordinate=np.linspace(0,sample_count-1,101)
    mask=metadata['support_mask']
    return mask[np.floor(coordinate).astype(int)] & mask[np.ceil(coordinate).astype(int)]


def features(curve,supported):
    y=np.asarray(curve,dtype=float);supported=np.asarray(supported,dtype=bool)
    if y.shape!=(101,) or supported.shape!=(101,) or not np.isfinite(y).all() or np.max(y)<=0:
        raise ValueError('finite_positive_101_point_curve_required')
    y=y/np.max(y)
    positions,info=signal.find_peaks(y,prominence=POLICY['minimum_peak_prominence'],distance=POLICY['peak_distance_percent'])
    peaks=[dict(phase_percent=int(p),height=float(y[p]),prominence=float(v),
        supported=bool(supported[p]),endpoint=False) for p,v in zip(positions,info['prominences'])]
    for p,q in ((0,1),(100,99)):
        if y[p]>y[q]:peaks.append(dict(phase_percent=p,height=float(y[p]),prominence=None,supported=False,endpoint=True))
    peaks.sort(key=lambda p:p['phase_percent'])
    ranked=sorted(peaks,key=lambda p:p['height'],reverse=True)
    maximum=int(np.argmax(y));ties=np.flatnonzero(np.isclose(y,y[maximum],rtol=0,atol=1e-12))
    gap=ranked[0]['height']-ranked[1]['height'] if len(ranked)>1 else None
    reasons=[]
    if not supported[maximum]:reasons.append('maximum_in_boundary_uncertainty_region')
    if not any(p['phase_percent']==maximum for p in peaks):reasons.append('maximum_not_a_resolved_peak')
    if len(ties)>1:reasons.append('nonunique_maximum')
    if gap is not None and gap<=POLICY['competing_peak_height_gap']:reasons.append('competing_peaks_close_in_height')
    return dict(version=FEATURE_VERSION,peaks=peaks,global_maximum_percent=maximum,
        global_peak_status='held-unobserved-boundary-context' if not supported.all() else 'not-independently-validated',
        single_peak_scope='interior-candidate-not-confirmed-full-cycle-maximum',
        peak_height_gap=gap,single_peak_status='held' if reasons else 'provisional-supported',
        single_peak_percent=None if reasons else maximum,reasons=reasons,
        supported_phase_percent=np.flatnonzero(supported).tolist(),
        supported_fraction=float(supported.mean()),scientific_approval=False)


def compare_supported(left,right):
    a,b=left['timing_features'],right['timing_features']
    mask=np.zeros(101,dtype=bool)
    mask[list(set(a['supported_phase_percent']) & set(b['supported_phase_percent']))]=True
    x,y=np.asarray(left['median']),np.asarray(right['median'])
    # Shape-only normalization on common supported points avoids letting an
    # uncertain endpoint maximum rescale the entire interior comparison.
    rmse=None
    if mask.sum()>=3 and max(x[mask])>0 and max(y[mask])>0:
        rmse=float(np.sqrt(np.mean((x[mask]/max(x[mask])-y[mask]/max(y[mask]))**2)))
    p,q=a['single_peak_percent'],b['single_peak_percent']
    return dict(common_supported_points=int(mask.sum()),common_supported_shape_RMSE=rmse,
        global_peak_difference_percent=None,global_peak_status='held-unobserved-boundary-context',
        provisional_peak_difference_percent=None if p is None or q is None else (q-p+50)%100-50,
        peak_status='held' if p is None or q is None else 'provisional-supported',
        peak_correspondence_established=False,scientific_comparison_approved=False,
        interpretation='Multiple candidates retained; interior coverage is reported, not substituted for full-cycle equivalence.')
