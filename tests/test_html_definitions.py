import pytest
from harmo.html_definitions import extract_html_definitions
from harmo.semantic_promotion import promote
from test_semantic_promotion import fixture

NEEDLE = ('Data were collected. A 25mm concentric needle electrode was placed into the tibialis anterior muscle of each subject. '
          'The patient was then asked to dorsiflex the foot gently against resistance. Later the patient was asked to relax.')


def extract(tmp_path, text, single=True):
    b,_ = fixture()
    if single:
        b.channels = b.channels[:1]
        b.samples.shape[1] = 1
    for c in b.channels: c.name = 'EMG'
    path = tmp_path/'description.html'
    path.write_text(text)
    return b, extract_html_definitions(path,'https://physionet.org/content/'+b.study_id+'/',b,'x.hea','a'*64)


def test_actual_sentence_structure_promotes(tmp_path):
    b,d = extract(tmp_path,'<h3>Data Description</h3><p>'+NEEDLE+'</p>')
    result,r = promote(b,'x.hea','a'*64,[d])
    assert result.channels[0].anatomy.canonical_label == 'tibialis anterior'
    assert result.channels[0].side is None
    assert result.task.facets['annotation_scope'] == 'recording-protocol-not-sample-labels'
    assert result.task.compatibility_group is None
    for decision in r['decisions']:
        if decision['status'] == 'source_verified':
            assert decision['sources'][0]['quote'] == NEEDLE
            assert 'rule_version' in decision['sources'][0]


@pytest.mark.parametrize('heading,text', [('Background',NEEDLE),('References',NEEDLE),
    ('Data Description','Previously, not all subjects were measured. '+NEEDLE),
    ('Data Description',NEEDLE.replace('each subject','some subjects'))])
def test_scope_and_negation_fail_closed(tmp_path, heading, text):
    _,d = extract(tmp_path,'<h3>'+heading+'</h3><p>'+text+'</p>')
    assert not d['data']['definitions']


def test_multiple_channels_not_assigned_same_muscle(tmp_path):
    _,d = extract(tmp_path,'<h3>Data Description</h3><p>'+NEEDLE+'</p>',single=False)
    assert not d['data']['definitions']


def test_wrong_release_refused(tmp_path):
    b,_ = fixture()
    with pytest.raises(ValueError):
        extract_html_definitions(tmp_path/'absent','https://physionet.org/content/wrong/1.0.0/',b,'x','a'*64)
