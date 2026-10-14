"""Finite, explicit JSON scope joins; no wildcard or inferred inheritance."""


def rows_with_scope(data):
    protocols={}
    if isinstance(data,dict):
        records=data.get('records')
        definitions=data.get('protocols',[])
        if not isinstance(definitions,list) or len(definitions)>1000:
            raise ValueError('explicit_protocol_table_required')
        for i,item in enumerate(definitions):
            if not isinstance(item,dict) or not isinstance(item.get('protocol_id'),str) or not item['protocol_id'].strip():
                raise ValueError('explicit_protocol_identity_required')
            identity=item['protocol_id']
            if identity in protocols: raise ValueError('duplicate_protocol_identity')
            protocols[identity]=(item,f'/protocols/{i}')
        prefix='/records'
    else:
        records=data; prefix=''
    if not isinstance(records,list) or len(records)>1000 or any(not isinstance(r,dict) for r in records):
        raise ValueError('explicit_record_table_required')
    rows,unresolved=[],[]
    for i,item in enumerate(records):
        pointer=f'{prefix}/{i}'
        # Only the exact "record" array syntax has multi-record semantics.
        targets=item.get('record')
        if isinstance(targets,list):
            if not targets or len(targets)>1000 or any(not isinstance(v,str) or not v.strip() or v.strip().lower() in ('*','all','all records') for v in targets):
                unresolved.append(dict(pointer=pointer,reason='invalid_explicit_record_list')); continue
            if len(targets)!=len(set(targets)):
                unresolved.append(dict(pointer=pointer,reason='duplicate_record_scope')); continue
            variants=[(dict(item,record=v),{'record':f'{pointer}/record/{j}'}) for j,v in enumerate(targets)]
        else:
            variants=[(item,{})]
        for variant,origins in variants:
            rows.append((variant,pointer,'json-pointer',origins,{}))
            if len(rows)>2000: raise ValueError('expanded_scope_budget')
            if 'protocol_id' in item:
                identity=item['protocol_id']
                if not isinstance(identity,str) or identity not in protocols:
                    unresolved.append(dict(pointer=pointer+'/protocol_id',reason='protocol_reference_not_resolved')); continue
                definition,definition_pointer=protocols[identity]
                # Only a declared record-level task is inherited. Channel facts
                # need a separately explicit channel-level assignment.
                if 'task' in definition:
                    inherited={k:v for k,v in variant.items() if k.strip().lower().replace('_',' ') in ('record','recording','file','filename','record id','recording id','file name')}
                    inherited['task']=definition['task']
                    rows.append((inherited,pointer,'json-pointer',dict(origins,task=definition_pointer+'/task'),
                        dict(protocol_reference=dict(value=identity,pointer=pointer+'/protocol_id'),
                             protocol_definition=dict(value=identity,pointer=definition_pointer+'/protocol_id'))))
            if len(rows)>2000: raise ValueError('expanded_scope_budget')
    return rows,unresolved
