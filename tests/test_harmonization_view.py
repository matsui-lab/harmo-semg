import numpy as np
import pytest
from test_general_workflow import fixture
from harmo.walking_plan import prepare
from harmo.walking_execute import execute
from harmo.harmonization_view import build


def result(tmp_path):
    plan=prepare(fixture(tmp_path/'input'))
    output=execute(plan,tmp_path/'output')
    return plan,output


def test_display_uses_saved_output_and_actual_input_not_shifted(tmp_path):
    plan,output=result(tmp_path)
    view=build(plan,output,tmp_path/'output')
    assert len(view['curves'])==2 and len(view['pairs'])==1
    for curve,original in zip(view['curves'],output['curves']):
        assert curve['median']==original['median']
        assert curve['steps']['shift_correction_applied'] is False
        assert curve['mappings'][0]['before'] is None
        assert curve['mappings'][0]['after']==original['muscle']
        assert curve['raw']['min']<0 and curve['envelope']['min']>=0
    a,b=view['curves'];pair=view['pairs'][0]
    np.testing.assert_array_equal(pair['difference'],np.asarray(b['median'])-np.asarray(a['median']))
    assert pair['peak_difference']['value']==output['comparisons'][0]['circular_peak_difference_percent']
    assert not pair['shift_correction_applied'] and not view['scientific_approval']


def test_withheld_global_peak_never_promoted_from_diagnostics(tmp_path):
    plan,output=result(tmp_path)
    comparison=output['comparisons'][0]
    comparison['supported_metrics']=dict(provisional_peak_difference_percent=None,
        global_peak_status='held-unobserved-boundary-context',common_supported_shape_RMSE=None)
    comparison['unqualified_peak_diagnostics']=dict(circular_peak_difference_percent=23)
    view=build(plan,output,tmp_path/'output')
    assert view['pairs'][0]['peak_difference']['value'] is None
    assert view['pairs'][0]['peak_difference']['status']=='held'


def test_modified_saved_median_is_rejected(tmp_path):
    plan,output=result(tmp_path)
    output['curves'][0]['median'][0]+=1
    with pytest.raises(ValueError,match='saved_output'):
        build(plan,output,tmp_path/'output')


def test_preview_limits_are_reported(tmp_path):
    plan,output=result(tmp_path)
    view=build(plan,output,tmp_path/'output',maximum_curves=1)
    assert len(view['curves'])==1 and not view['pairs']
    assert view['omitted_curves']==1 and view['omitted_pairs']==1
