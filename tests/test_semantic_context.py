from copy import deepcopy
import json

import numpy as np
import pytest

from test_general_workflow import fixture
from harmo.semantic_context import article, extrema_preview
from harmo.semantic_review import prepare as prepare_packet
from harmo.semantic_workspace import prepare
from harmo.semantic_server import ReviewSession


def workspace(tmp_path):
    summary=fixture(tmp_path/'fixture')
    prepare_packet(summary,tmp_path/'packet',max_channels=2)
    return prepare(tmp_path/'packet/packet.json',tmp_path/'workspace',max_targets=6)


def test_minmax_preview_uses_exact_original_samples_and_retains_peak():
    t=np.arange(10000)/1000
    y=np.sin(t);y[2021]=100;y[2077]=-100
    result=extrema_preview(t,y)
    assert result['displayed_samples']<=1200
    assert result['source_indices']==sorted(set(result['source_indices']))
    assert 2021 in result['source_indices'] and 2077 in result['source_indices']
    assert np.array_equal(np.asarray(result['points']),np.column_stack([t,y])[result['source_indices']])


def test_short_preview_retains_all_points():
    result=extrema_preview(np.arange(5.),np.arange(5.)-2)
    assert result['method']=='original-samples'
    assert result['displayed_samples']==5


def test_article_summary_is_abstract_not_a_record_claim():
    source=dict(source_id='paper',path='saved.xml',text='<article><front><journal-meta><journal-title-group><journal-title>Example</journal-title></journal-title-group></journal-meta><article-meta><title-group><article-title>Study title</article-title></title-group><article-id pub-id-type="doi">10.1234/example</article-id><pub-date><year>2026</year></pub-date><abstract><p>Original abstract.</p></abstract></article-meta></front></article>')
    result=article(source)
    assert result['summary']=='Original abstract.'
    assert result['summary_method']=='extractive-abstract'
    assert result['doi']=='10.1234/example'
    assert 'not a verified statement' in result['scope']
    assert article(dict(source,text='<html><p>not an article</p></html>')) is None


def test_xml_entities_are_not_resolved():
    raw='<!DOCTYPE article [<!ENTITY secret "DO-NOT-EXPAND">]><article><front><article-meta><abstract><p>&secret;</p></abstract></article-meta></front></article>'
    result=article(dict(text=raw,path='saved.xml',source_id='paper'))
    assert 'DO-NOT-EXPAND' not in result['summary']


def test_actual_catalog_channel_bound_to_display_without_decision_change(tmp_path):
    result=workspace(tmp_path)
    item=result['items'][0];wave=item['context']['waveform'];meta=item['context']['metadata']
    assert wave['status']=='available'
    assert wave['channel_id']==item['target']['channel_id']
    assert wave['channel']=='RT TIB.ANT.'
    assert meta['sampling_rate_hz']==1500
    assert meta['samples']==15000
    with np.load(wave['source_path']) as source:
        indices=wave['full']['source_indices']
        assert np.array_equal(np.asarray(wave['full']['points'])[:,1],source['values'][indices,0])
    assert all(d['action']=='unreviewed' for d in result['decisions']['decisions'])
    assert not wave['scientific_approval']


def test_changed_display_input_rejects_session_before_execution(tmp_path):
    result=workspace(tmp_path)
    path=result['items'][0]['context']['waveform']['source_path']
    from pathlib import Path
    Path(path).write_bytes(b'changed synthetic preview source')
    with pytest.raises(ValueError,match='bound_input_changed'):
        ReviewSession(tmp_path/'workspace/workspace.json',tmp_path/'session')
    assert not (tmp_path/'session').exists()
