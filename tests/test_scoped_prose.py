from harmo.scoped_prose import extract

HEADER = b'x 2 1500 2\nx.dat 16 10(0)/uV 0 0 0 0 0 RT TIB.ANT.\nx.dat 16 10(0)/V 0 0 0 0 0 RT FOOTSWITCH\n'
PROSE = b'<p>Surface EMG of the anterior tibialis and lateral gastrocnemius muscles was recorded simultaneously with the gait data.</p><p>The files under the walking-dual folder containing 90 seconds of walking while counting.</p>'


def test_explicit_prose_header_join_is_scoped_and_does_not_invent_events():
    result = extract(PROSE, 'https://example.org/', 'walking-dual/x.hea', HEADER, 'header-uri')
    obs = result['observations']
    assert {(o['channel'], o['field'], o['value']) for o in obs} == {
        ('RT TIB.ANT.', 'sensor_type', 'surface-emg'), ('RT TIB.ANT.', 'anatomy', 'tibialis anterior'),
        ('RT TIB.ANT.', 'side', 'right'), ('', 'task', 'walking')}
    assert all(o['scope_evidence']['record']['value']=='walking-dual/x.hea' for o in obs)
    assert not any(o['scientific_approval'] for o in obs)
    assert obs[-1]['protocol_context']=='90 seconds of walking while counting'


def test_different_folder_gets_no_task():
    assert not any(o['field']=='task' for o in extract(PROSE, 'uri', 'other/x.hea', HEADER, 'h')['observations'])


def test_negation_and_tentative_or_historical_context_not_promoted():
    for prefix in ('No. ', 'Previously, ', 'This is hypothetical. ', 'It may be so. '):
        raw=('<p>'+prefix+'Surface EMG of the anterior tibialis muscles was recorded.</p>').encode()
        assert not extract(raw, 'u', 'x.hea', HEADER, 'h')['observations']


def test_short_ambiguous_alias_is_not_promoted():
    header=HEADER.replace(b'RT TIB.ANT.', b'RT TA')
    assert not any(o['channel'] for o in extract(PROSE,'u','x.hea',header,'h')['observations'])


def test_wrong_muscle_is_not_promoted():
    raw=PROSE.replace(b'anterior tibialis and lateral gastrocnemius', b'vastus lateralis')
    assert not any(o['channel'] for o in extract(raw,'u','x.hea',HEADER,'h')['observations'])


def test_subset_acquisition_is_not_generalized():
    raw=PROSE.replace(b'recorded simultaneously with the gait data', b'recorded only in the control group')
    assert not any(o['channel'] for o in extract(raw,'u','x.hea',HEADER,'h')['observations'])


def test_all_registered_lower_limb_full_names_have_scoped_path():
    from harmo.lower_limb_terms import FULL_NAMES
    for name in FULL_NAMES:
        raw=('<p>Surface EMG of the '+name+' muscles was recorded.</p>').encode()
        header=HEADER.replace(b'TIB.ANT.',name.upper().encode())
        obs=extract(raw,'u','x.hea',header,'h')['observations']
        assert any(o['field']=='anatomy' and o['value']==name for o in obs),name
        assert all(o['scope_evidence']['record']['value']=='x.hea' for o in obs)
