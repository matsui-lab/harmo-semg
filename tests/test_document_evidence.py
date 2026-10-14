import pytest
from harmo.document_evidence import extract,match

TABLE='<table><tr><th>Record</th><th>Channel</th><th>Muscle</th><th>Side</th><th>Task</th></tr><tr><td>r1</td><td>EMG1</td><td>Tibialis anterior</td><td>left</td><td>walking</td></tr></table>'

@pytest.mark.parametrize('kind',['html','xml'])
def test_explicit_source_scope(kind):
    result=extract(TABLE.encode(),'https://example.org/source',kind)
    assert len(result['observations'])==3
    assert all(r['pointer'] and r['source_sha256'] and r['quote']==r['value'] for r in result['observations'])
    selected=match(result['observations'],'r1',['EMG1'])
    assert len(selected['attributes'])==3
    assert not match(result['observations'],'other',['EMG1'])['attributes']
    assert selected['scientific_use']=='not_assessed'

def test_conflicting_source_definitions_not_promoted():
    first=extract(TABLE.encode(),'https://example.org/one')
    second=extract(TABLE.replace('left','right').encode(),'https://example.org/two')
    selected=match(first['observations']+second['observations'],'r1',['EMG1'])
    assert next(r for r in selected['attributes'] if r['field']=='side')['status']=='conflicting'

@pytest.mark.parametrize('table',[TABLE.replace('>r1<','>all<'),TABLE.replace('<th>Record</th>','<th>Unknown</th>'),TABLE.replace('<td>left</td>','<td colspan="2">left</td>')])
def test_ambiguous_scope_is_not_applied(table):
    result=extract(table.encode(),'https://example.org/')
    assert not result['observations'] and result['unresolved']

def test_external_entities_rejected():
    with pytest.raises(ValueError):
        extract(b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>','https://example.org/','xml')


def test_explicit_rowspan_preserves_scope_origin():
    raw='<table><tr><th>File name</th><th>Channel</th><th>Side</th></tr><tr><td rowspan="2">r1</td><td>A</td><td>left</td></tr><tr><td>B</td><td>right</td></tr></table>'
    result=extract(raw.encode(),'https://example.org/')
    a,b=result['observations']
    assert (a['record'],b['record'])==('r1','r1')
    assert (a['channel'],b['channel'])==('A','B')
    assert a['scope_evidence']['record']['pointer']==b['scope_evidence']['record']['pointer']
    assert a['row_pointer']!=b['row_pointer']


@pytest.mark.parametrize('span',['0','-1','1000000','bad'])
def test_invalid_rowspans_fail_closed(span):
    raw=TABLE.replace('<td>r1</td>',f'<td rowspan="{span}">r1</td>')
    result=extract(raw.encode(),'https://example.org/')
    assert not result['observations'] and result['unresolved']


def test_nested_tables_are_not_reinterpreted_as_independent_scope():
    raw='<table><tr><td>'+TABLE+'</td></tr></table>'
    result=extract(raw.encode(),'https://example.org/')
    assert not result['observations']
    assert result['unresolved'][0]['reason']=='nested_table_requires_disambiguation'


def test_blank_record_cell_is_not_forward_filled():
    raw=TABLE.replace('>r1<','><')
    result=extract(raw.encode(),'https://example.org/')
    assert not result['observations']
    assert result['coverage']=='not_extracted_by_supported_rules'


def test_lexical_normalization_does_not_convert_scale():
    from harmo.document_evidence import normalize_literal
    assert normalize_literal('unit','µV')==normalize_literal('unit','uV')
    assert normalize_literal('unit','mV')!=normalize_literal('unit','uV')
    assert normalize_literal('side','L')=='left'
    assert normalize_literal('anatomy','TA')!='tibialis anterior'


@pytest.mark.parametrize('kind,raw',[
    ('json',b'[{"record":"r1","channel":"A","side":"left"}]'),
    ('json',b'{"records":[{"record":"r1","channel":"A","side":"left"}]}'),
    ('csv',b'record,channel,side\nr1,A,left\n'),
    ('tsv',b'record\tchannel\tside\nr1\tA\tleft\n')])
def test_structured_metadata_has_same_scope(kind,raw):
    result=extract(raw,'https://example.org/metadata',kind)
    selected=match(result['observations'],'r1',['A'])
    assert len(selected['attributes'])==1
    assert selected['attributes'][0]['values']==['left']
    assert result['observations'][0]['scope_evidence']['record']['value']=='r1'


@pytest.mark.parametrize('kind,raw',[
    ('json',b'[{"record":"r1","record":"r2"}]'),
    ('csv',b'record,record\nr1,r2\n'),
    ('csv',b'record,channel\nr1,A,left\n')])
def test_structured_malformed_tables_rejected(kind,raw):
    with pytest.raises(ValueError): extract(raw,'https://example.org/',kind)


def test_numeric_record_identifier_not_coerced():
    result=extract(b'[{"record":1,"channel":"A","side":"left"}]','https://example.org/','json')
    assert not result['observations'] and result['unresolved']


def test_nested_json_not_guessed():
    with pytest.raises(ValueError):
        extract(b'{"subjects":{"r1":{"side":"left"}}}','https://example.org/','json')


def test_multiline_csv_quote_and_logical_row_pointer():
    result=extract(b'record,channel,task\nr1,A,"walking\noverground"\n','https://example.org/','csv')
    value=result['observations'][0]
    assert value['quote']=='walking\noverground'
    assert value['pointer_kind']=='logical-row-column'
    assert value['pointer']=='/data-rows/0/task'
