import json
import numpy as np
from scipy.io import savemat
from harmo.mat_document_semantics import extract


def fixture(tmp_path,side='RX',task='Walking',parent='subject',trial_name='Trials'):
    source=tmp_path/'arbitrary.mat';location=tmp_path/'record';location.mkdir()
    article=tmp_path/'article.xml'
    article.write_text('''<article><body>
<p>The dominant leg from which the EMG signals were acquired (RecordingSide [RX/LX]), the EMG signals (ChannelNames).</p>
<p>Surface EMG signals were recorded using skin electrodes.</p>
<p>EMG signals were recorded unilaterally from the following muscles tibialis anterior.</p>
<p>For each trial, foot-strikes were defined from platform data.</p>
<p>The data reported in the present study includes, for each trial, the portions of kinematic, dynamic and EMG signals between start and end.</p>
<table><tr><td>.Foot</td><td>Indicates the trailing foot, RX (right) or LX (left)</td></tr>
<tr><td>.Activity</td><td>Indicates the type of trial (Walking, HeelWalking, StepUp)</td></tr></table>
</body></article>''')
    key=parent+'.'+trial_name
    savemat(source,{parent:dict(RecordingSide=side,ChannelNames=np.array(['Tibialis Anterior']),
        **{trial_name:dict(EMG=np.arange(100).reshape(1,100),Activity=task,Foot='LX')})})
    proposal=dict(profile='physiology-parent-metadata/1.1',signal_type='EMG',metadata_scope=parent,
        record_key=key,data_key=key+'.EMG',rows=100,channels=['Tibialis Anterior'],
        options={'reader_options':{'channel_names_key':parent+'.ChannelNames'}})
    (location/'proposal.json').write_text(json.dumps(proposal))
    return source,[dict(name='arbitrary::'+key+'.EMG',location=str(location))],article


def test_literal_fields_not_dataset_ids_and_measurement_side_not_Foot(tmp_path):
    s,r,a=fixture(tmp_path)
    d=extract(s,r,a);assert len(d['observations'])==4
    assert next(x for x in d['observations'] if x['field']=='side')['value']=='right'
    assert d['source_segments'][0]['kind']=='source-delimited-stride'
    assert d['source_segments'][0]['reference_side']=='left'
    assert all(x['scope_evidence']['record']['source_sha256'] for x in d['observations'])


def test_other_task_not_silently_walking_and_unknown_side_not_inherited(tmp_path):
    s,r,a=fixture(tmp_path,side='unknown',task='HeelWalking')
    d=extract(s,r,a)
    assert not any(x['field'] in ('side','task') for x in d['observations'])


def test_competing_side_definitions_do_not_pick_first(tmp_path):
    s,r,a=fixture(tmp_path)
    a.write_text(a.read_text().replace('</body>','<p>The dominant leg from which the EMG signals were acquired (OtherSide [RX/LX])</p></body>'))
    assert not any(x['field']=='side' for x in extract(s,r,a)['observations'])
