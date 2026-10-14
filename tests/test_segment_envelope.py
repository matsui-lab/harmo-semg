import numpy as np
import pytest
from harmo.segment_envelope import available_rms,envelope,phase_support,features,compare_supported
from harmo.footswitch_timing import DEFAULT,envelope as legacy


def test_constant_RMS_does_not_treat_missing_samples_as_zero():
    result,counts=available_rms(np.ones(40),5)
    np.testing.assert_array_equal(result,np.ones(40))
    assert counts[0]==3 and counts[20]==5


def test_corrected_interior_equals_legacy_and_boundary_is_flagged():
    x=np.random.default_rng(12).normal(size=1500)
    result,meta=envelope(x,1500,DEFAULT);old=legacy(x,1500,DEFAULT)
    h=meta['rms_window_samples']//2
    np.testing.assert_allclose(result[h:-h],old[h:-h],atol=1e-14,rtol=1e-14)
    assert not meta['support_mask'][0] and not meta['support_mask'][-1]
    assert meta['combined_margin_samples']>h and phase_support(meta,len(x))[50]


def test_short_segment_has_no_supported_peak_not_fake_recovery():
    x=np.random.default_rng(3).normal(size=200)
    result,meta=envelope(x,1000,DEFAULT)
    assert not meta['support_mask'].any()
    y=np.interp(np.linspace(0,199,101),np.arange(200),result)
    assert features(y,phase_support(meta,len(x)))['single_peak_percent'] is None


def test_two_peaks_retained_and_near_tie_not_forced_to_one():
    t=np.arange(101);y=np.exp(-((t-35)/5)**2)+.98*np.exp(-((t-70)/5)**2)
    result=features(y,np.ones(101,dtype=bool))
    assert [p['phase_percent'] for p in result['peaks']]==[35,70]
    assert result['single_peak_percent'] is None and 'competing_peaks_close_in_height' in result['reasons']


def test_boundary_maximum_does_not_promote_smaller_interior_peak():
    t=np.arange(101);y=.7*np.exp(-((t-40)/5)**2)+np.exp(-((t-98)/3)**2)
    support=(t>=20)&(t<=80);r=features(y,support)
    assert r['global_maximum_percent']==98 and r['single_peak_percent'] is None
    assert r['global_peak_status']=='held-unobserved-boundary-context'
    assert any(p['phase_percent']==40 and p['supported'] for p in r['peaks'])


def test_interior_shape_not_rescaled_by_uncertain_endpoint_peak():
    t=np.arange(101);x=.2+np.exp(-((t-45)/15)**2);y=x.copy();y[0]=10
    support=(t>=20)&(t<=80)
    a=dict(median=x/x.max(),timing_features=features(x,support))
    b=dict(median=y/y.max(),timing_features=features(y,support))
    r=compare_supported(a,b)
    assert r['common_supported_shape_RMSE']<1e-14 and r['provisional_peak_difference_percent'] is None


def test_flat_peak_and_nonfinite_input_held():
    assert features(np.ones(101),np.ones(101,dtype=bool))['single_peak_percent'] is None
    with pytest.raises(ValueError):available_rms(np.array([1,np.nan,2]),1)
