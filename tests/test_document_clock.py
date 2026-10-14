import json
import numpy as np
import pytest
from harmo.document_clock import propose
from harmo.config.automap import inspect_file


def fixture(tmp_path):
    p=tmp_path/'signal.csv';m=tmp_path/'repository.metadata.json'
    p.write_text('slno,emg1,emg2,Time\n'+'\n'.join(f'{i},{i+1},{i+2},{i*.005:.6f}' for i in range(20))+'\n')
    m.write_text(json.dumps({'description':'The armband consists of 2 Surface EMG sensor units. The 2 sensors read data every 5ms. This data is stored in a CSV file with the timestamp.'}))
    return p,m


def test_document_interval_disambiguates_clock_and_retains_counter(tmp_path):
    p,m=fixture(tmp_path);meta=propose(p,m)
    assert meta['time_unit']=='s' and meta['clock_evidence']['rows_checked']==20
    r,t,x=inspect_file(p,meta)
    assert r['channels']==['emg1','emg2'] and x.shape==(20,2)
    assert r['options']['melt_options']['id_columns']==['slno']
    np.testing.assert_allclose(t,np.arange(20)*.005)
    assert r['sampling_rate_hz']==200 and p.read_text().startswith('slno,')


@pytest.mark.parametrize('change',['interval','negation','duplicate','scope','clock','counter','count'])
def test_unresolved_or_inconsistent_declarations_do_not_supply_units(tmp_path,change):
    p,m=fixture(tmp_path)
    d=json.loads(m.read_text());text=d['description']
    if change=='interval':text=text.replace('5ms','7ms')
    if change=='negation':text='Previously, '+text
    if change=='duplicate':text+=' The 2 sensors read data every 10ms.'
    if change=='scope':text=text.replace('This data is stored in a CSV file with the timestamp.','')
    if change=='count':text=text.replace('The 2 sensors','The 3 sensors')
    if change=='clock':p.write_text(p.read_text().replace('0.095000','0.097000'))
    if change=='counter':p.write_text(p.read_text().replace('19,20,21','18,20,21'))
    m.write_text(json.dumps({'description':text}))
    assert propose(p,m) is None


def test_clock_proof_rechecked_before_conversion(tmp_path):
    p,m=fixture(tmp_path);meta=propose(p,m)
    m.write_text(m.read_text().replace('5ms','7ms'))
    with pytest.raises(ValueError,match='changed'):inspect_file(p,meta)
