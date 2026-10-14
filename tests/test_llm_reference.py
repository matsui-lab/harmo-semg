import pytest
from harmo.llm_reference import require_reviewed_reference


@pytest.mark.parametrize('status',['unreviewed_blank_template','developer_prefill_pending_human_review'])
def test_unreviewed_labels_not_gold(status):
    with pytest.raises(ValueError,match='human_reviewed_reference_required'):
        require_reviewed_reference(dict(status=status,labels=[]),[])


def test_reviewed_unknown_is_not_blank():
    row=dict(target_id='x',reviewed=True,reviewer='test-reviewer',reference_status='insufficient',reference_value=None,evidence=[])
    assert require_reviewed_reference(dict(status='human_reviewed',labels=[row]),['x'])
    row['reviewed']=False
    with pytest.raises(ValueError,match='reviewer_confirmation'):
        require_reviewed_reference(dict(status='human_reviewed',labels=[row]),['x'])


def test_missing_reference_target_rejected():
    with pytest.raises(ValueError,match='target_set_mismatch'):
        require_reviewed_reference(dict(status='human_reviewed',labels=[]),['x'])
