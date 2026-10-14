"""Source-addressable HTML/JATS table observations, without scientific promotion."""
import hashlib
import re
import csv
import io
import json
from lxml import etree

HEADERS = {'record':'record','recording':'record','file':'record','filename':'record',
    'record id':'record','recording id':'record','file name':'record',
    'channel':'channel','channel label':'channel','muscle':'anatomy','anatomy':'anatomy',
    'channel name':'channel',
    'side':'side','laterality':'side','unit':'unit','units':'unit','task':'task',
    'sensor type':'sensor_type'}


def table_grid(table):
    """Reconstruct explicit vertical spans; never infer blank-cell inheritance."""
    rows = table.xpath('.//*[local-name()="tr"]')
    if table.xpath('.//*[local-name()="table"]'):
        raise ValueError('nested_table_requires_disambiguation')
    if len(rows)>1001:
        raise ValueError('row_count_budget')
    grid, pending = [], {}
    for row_index,row in enumerate(rows):
        occupied = {col:cell for col,(cell,end) in pending.items() if row_index<end}
        pending = {col:value for col,value in pending.items() if row_index<value[1]}
        column = 0
        for cell in row.xpath('./*[local-name()="th" or local-name()="td"]'):
            while column in occupied: column+=1
            if column>=100: raise ValueError('column_count_budget')
            try:
                width=int(cell.get('colspan','1')); height=int(cell.get('rowspan','1'))
            except ValueError:
                raise ValueError('invalid_cell_span')
            if width!=1:
                raise ValueError('spanning_columns_require_disambiguation')
            if height<1 or row_index+height>len(rows) or (row_index==0 and height!=1):
                raise ValueError('invalid_cell_span')
            occupied[column]=cell
            if height>1: pending[column]=(cell,row_index+height)
            column+=1
        grid.append((row,[occupied.get(i) for i in range(max(occupied,default=-1)+1)]))
    return grid


def extract(raw, uri, kind='html', max_bytes=2*1024**2):
    if len(raw)>max_bytes: raise ValueError('document_size_budget')
    if kind in ('json','csv','tsv'):
        return extract_structured(raw,uri,kind,max_bytes)
    if kind not in ('html','xml'): raise ValueError('unsupported_document_kind')
    parser = etree.HTMLParser(no_network=True) if kind=='html' else etree.XMLParser(
        resolve_entities=False,load_dtd=False,no_network=True)
    root = etree.fromstring(raw,parser)
    if root is None: raise ValueError('empty_document')
    tree = root.getroottree()
    if kind=='xml' and tree.docinfo.internalDTD is not None and list(tree.docinfo.internalDTD.iterentities()):
        raise ValueError('entity_definitions_not_supported')
    for node in root.xpath('//*[local-name()="script" or local-name()="style"]'):
        node.getparent().remove(node)
    digest = hashlib.sha256(raw).hexdigest()
    def text(node): return re.sub(r'\s+',' ',''.join(node.itertext())).strip()
    observations, unresolved = [], []
    tables = root.xpath('//*[local-name()="table"]')
    if len(tables)>100: raise ValueError('table_count_budget')
    for table in tables:
        # A nested table must not independently escape its rejected parent.
        if table.xpath('ancestor::*[local-name()="table"]'): continue
        try:
            grid=table_grid(table)
        except ValueError as exc:
            unresolved.append(dict(pointer=tree.getpath(table),reason=str(exc))); continue
        if not grid: continue
        headers = grid[0][1]
        keys = [HEADERS.get(text(c).lower().replace('_',' ')) if c is not None else None for c in headers]
        reason = None
        if 'record' not in keys: reason='no_explicit_record_column'
        if len([k for k in keys if k]) != len(set(k for k in keys if k)): reason='ambiguous_columns'
        if reason:
            unresolved.append(dict(pointer=tree.getpath(table),reason=reason)); continue
        for row,cells in grid[1:]:
            if len(cells)!=len(keys) or any(c is None for c in cells):
                unresolved.append(dict(pointer=tree.getpath(row),reason='row_width_mismatch')); continue
            fields={k:text(c) for k,c in zip(keys,cells) if k}
            record=fields.get('record',''); channel=fields.get('channel','')
            if not record or record.lower() in ('all','*','all records'):
                unresolved.append(dict(pointer=tree.getpath(row),reason='record_scope_not_explicit')); continue
            for k,c in zip(keys,cells):
                if k in (None,'record','channel') or not text(c): continue
                if k!='task' and not channel:
                    unresolved.append(dict(pointer=tree.getpath(c),reason='channel_scope_not_explicit')); continue
                observations.append(dict(record=record,channel='' if k=='task' else channel,field=k,value=text(c),
                    status='source_observation',source_uri=uri,source_sha256=digest,
                    pointer=tree.getpath(c),row_pointer=tree.getpath(row),quote=text(c),
                    scope_evidence={key:dict(value=text(cell),pointer=tree.getpath(cell))
                        for key,cell in zip(keys,cells) if key in ('record','channel')},
                    scope_rule='explicit_record_and_channel_columns/1.1'))
    return dict(version='document-table-evidence/0.2',source_uri=uri,source_sha256=digest,
        observations=observations,unresolved=unresolved,
        coverage='observations_extracted' if observations else 'not_extracted_by_supported_rules',
        scope='Literal source observations only; no inferred ontology, compatibility group, or scientific approval.')


def extract_structured(raw,uri,kind,max_bytes=2*1024**2):
    """Explicit record/channel metadata only; arbitrary nested JSON is not guessed."""
    if len(raw)>max_bytes: raise ValueError('document_size_budget')
    digest=hashlib.sha256(raw).hexdigest()
    scope_unresolved=[]
    def unique_object(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError('duplicate_json_key')
            result[key]=value
        return result
    if kind=='json':
        data=json.loads(raw.decode('utf-8-sig'),object_pairs_hook=unique_object)
        from harmo.document_scope import rows_with_scope
        rows,scope_unresolved=rows_with_scope(data)
    elif kind in ('csv','tsv'):
        reader=csv.reader(io.StringIO(raw.decode('utf-8-sig')),delimiter=',' if kind=='csv' else '\t',strict=True)
        headers=next(reader,[])
        if len(headers)!=len(set(headers)): raise ValueError('duplicate_column_name')
        rows=[]
        for i,values in enumerate(reader):
            if i>=1000: raise ValueError('row_count_budget')
            if len(values)!=len(headers): raise ValueError('row_width_mismatch')
            rows.append((dict(zip(headers,values)),f'/data-rows/{i}','logical-row-column',{},{}))
    else:
        raise ValueError('unsupported_document_kind')
    observations,unresolved=[],list(scope_unresolved)
    for values,pointer,pointer_kind,origins,scope_links in rows:
        if len(values)>100: raise ValueError('column_count_budget')
        recognized={}
        ambiguous=False
        for label,value in values.items():
            key=HEADERS.get(label.strip().lower().replace('_',' '))
            if key is None: continue
            if key in recognized: ambiguous=True
            recognized[key]=(label,value)
        if ambiguous:
            unresolved.append(dict(pointer=pointer,reason='ambiguous_columns')); continue
        record=recognized.get('record',('',None))[1]
        channel=recognized.get('channel',('',''))[1]
        if not isinstance(record,str) or not record.strip() or record.strip().lower() in ('all','*','all records'):
            unresolved.append(dict(pointer=pointer,reason='record_scope_not_explicit')); continue
        if not isinstance(channel,str):
            unresolved.append(dict(pointer=pointer,reason='channel_scope_not_explicit')); continue
        def source_pointer(label): return origins.get(label,pointer+'/'+label.replace('~','~0').replace('/','~1'))
        for key,(label,value) in recognized.items():
            if key in ('record','channel'): continue
            if not isinstance(value,str) or not value.strip():
                unresolved.append(dict(pointer=source_pointer(label),reason='nonliteral_or_empty_value')); continue
            if key!='task' and not channel.strip():
                unresolved.append(dict(pointer=source_pointer(label),reason='channel_scope_not_explicit')); continue
            observations.append(dict(record=record.strip(),channel='' if key=='task' else channel.strip(),
                field=key,value=value.strip(),quote=value,source_uri=uri,source_sha256=digest,
                pointer=source_pointer(label),pointer_kind=pointer_kind,row_pointer=pointer,
                scope_evidence=dict({k:dict(value=v,pointer=source_pointer(l)) for k,(l,v) in recognized.items() if k in ('record','channel')},**scope_links),
                status='source_observation',scope_rule='explicit_record_and_channel_columns/1.1'))
    return dict(version='document-structured-evidence/0.1',source_uri=uri,source_sha256=digest,
        observations=observations,unresolved=unresolved,
        coverage='observations_extracted' if observations else 'not_extracted_by_supported_rules',
        scope='Explicit string-valued metadata rows only; no scientific approval or nested schema inference.')


def match(observations, record, channels):
    """Exact matching only; preserve disagreements instead of voting."""
    groups={}; rejected=[]
    for item in observations:
        if item['record']!=record or (item['channel'] and item['channel'] not in channels):
            rejected.append(item); continue
        groups.setdefault((item['channel'],item['field']),[]).append(item)
    return dict(attributes=[dict(channel=k[0],field=k[1],
        status='conflicting' if len({r['value'] for r in rows})>1 else 'source_matched',
        values=sorted({r['value'] for r in rows}),evidence=rows) for k,rows in sorted(groups.items())],
        unapplied=rejected,scientific_use='not_assessed')


def normalize_literal(field,value):
    """Only unambiguous lexical equivalents; no muscle abbreviation guessing."""
    value=re.sub(r'\s+',' ',value.strip())
    if field=='unit':
        from harmo.source_evidence import voltage_unit
        return voltage_unit(value) or value
    if field=='side':
        return {'l':'left','r':'right','left':'left','right':'right','midline':'midline'}.get(value.lower(),value.lower())
    if field=='sensor_type':
        return {'surface emg':'surface-emg','needle emg':'needle-emg'}.get(value.lower(),value.lower())
    return value.casefold()


def check_record(observations,bundle,record):
    """Compare explicit source facts with the existing contract, without approval."""
    selected=match(observations,record,[c.name for c in bundle.channels])
    channels={c.name:c for c in bundle.channels}
    conflicts=[]
    for attribute in selected['attributes']:
        field=attribute['field']
        normalized=sorted({normalize_literal(field,v) for v in attribute['values']})
        attribute['normalized_values']=normalized
        if any(e.get('status')=='review_hold' for e in attribute['evidence']):
            attribute['status']='conflicting'
            conflicts.append(dict(channel=attribute['channel'],field=field,reason='explicit_human_review_hold'))
            continue
        attribute['status']='conflicting' if len(normalized)>1 else 'source_matched'
        if len(normalized)>1:
            conflicts.append(dict(channel=attribute['channel'],field=field,reason='source_definitions_disagree'))
            continue
        channel=channels.get(attribute['channel'])
        current=None
        if channel is not None and field in ('unit','side','sensor_type'):
            current=getattr(channel,field,None)
        elif channel is not None and field=='anatomy' and channel.anatomy is not None:
            current=channel.anatomy.canonical_label
        # Task labels alone do not establish or disprove task equivalence.
        if current not in (None,'','unknown') and normalize_literal(field,current)!=normalized[0]:
            attribute['status']='contract_conflict'
            conflicts.append(dict(channel=attribute['channel'],field=field,reason='source_contract_disagreement',
                contract_value=current,source_values=attribute['values']))
    selected['conflicts']=conflicts
    selected['scope']='Literal fact consistency only; task equivalence and scientific approval remain separate.'
    return selected
