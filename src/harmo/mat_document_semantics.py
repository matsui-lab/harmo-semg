"""Join an explicit MAT data dictionary to fields in the actual source.

Bounded declaration grammar; the registered parent-metadata layout is required.
No dataset-ID branch, abbreviation guessing, or force-to-contact detection.
"""
import json
import re
from pathlib import Path
import numpy as np
from scipy.io import loadmat
from lxml import etree
from harmo.lower_limb_terms import FULL_NAMES
from harmo.public_batch import sha

VERSION='mat-document-field-join/1'


def at(data, path):
    value=data
    for token in path.split('.'):
        if isinstance(value,dict): value=value[token]
        elif token.isdigit() and isinstance(value,np.ndarray): value=value.flat[int(token)]
        elif token in getattr(value,'_fieldnames',[]): value=getattr(value,token)
        else: raise ValueError('literal_MAT_field_not_found:'+path)
    return value


def extract(source, records, article):
    source,article=Path(source),Path(article)
    if source.stat().st_size>32*1024**2 or article.stat().st_size>2*1024**2:
        raise ValueError('source_size_budget')
    raw=article.read_bytes()
    root=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))
    tree=root.getroottree()
    text=lambda node:' '.join(' '.join(node.itertext()).split())
    paragraphs=[(n,text(n)) for n in root.xpath('//p')]
    tables=[(n,text(n)) for n in root.xpath('//tr')]
    def unique(rows,pattern):
        candidates=[(n,t,re.search(pattern,t,re.I)) for n,t in rows if re.search(pattern,t,re.I)]
        # Do not resolve competing declarations by picking the first.
        return candidates[0] if len(candidates)==1 and not re.search(r'\b(not|hypothetical|previously|example|may|might|could)\b',candidates[0][1],re.I) else None
    side_definition=unique(paragraphs,r'the dominant leg from which the EMG signals were acquired \(([A-Za-z][A-Za-z0-9_]*) \[([A-Z]+)/([A-Z]+)\]\)')
    label_definition=unique(paragraphs,r'EMG signals \(([A-Za-z][A-Za-z0-9_]*)\)')
    sensor_definition=unique(paragraphs,r'\bSurface EMG signals were recorded using\b')
    muscles_definition=unique(paragraphs,r'\bEMG signals were recorded unilaterally.+?from the following muscles\b')
    task_definition=unique(tables,r'^\.([A-Za-z][A-Za-z0-9_]*) Indicates the type of trial \(([^)]+)\)')
    foot_definition=unique(tables,r'^\.([A-Za-z][A-Za-z0-9_]*) Indicates the trailing foot')
    codes={}
    for n,t in tables:
        for m in re.finditer(r'\b([A-Z]+) \((right|left)\)',t):
            codes.setdefault(m[1],set()).add(m[2])
    data=loadmat(source,squeeze_me=True,struct_as_record=False)
    source_hash,article_hash=sha(source),sha(article)
    observations,unresolved,segments=[],[],[]
    def add(record,pointer,node,channel,field,value,details):
        observations.append(dict(record=record['name'],channel=channel,field=field,value=value,
            status='source_observation',source_uri=article.resolve().as_uri(),source_sha256=article_hash,
            pointer=tree.getpath(node),quote=text(node),scope_rule=VERSION,
            scope_evidence=dict(record=dict(value=record['name'],pointer=pointer,
                source_uri=source.resolve().as_uri(),source_sha256=source_hash),
                channel=dict(value=channel,pointer=details.get('labels_key',''))),
            derivation=details,scientific_approval=False))
    for record in records:
        proposal=json.loads((Path(record['location'])/'proposal.json').read_text())
        if not proposal.get('profile','').startswith('physiology-parent-metadata/') or proposal.get('signal_type')!='EMG':continue
        try:
            parent=proposal['metadata_scope'];trial=proposal['record_key'];key=proposal['data_key']
            original=at(data,key)
            if not isinstance(original,np.ndarray) or original.size!=proposal['rows']*len(proposal['channels']):
                raise ValueError('MAT_array_shape_changed')
            details=dict(data_key=key,trial_key=trial,labels_key=proposal['options']['reader_options']['channel_names_key'])
            if label_definition:
                declared=parent+'.'+label_definition[2][1]
                if declared!=details['labels_key']:raise ValueError('document_label_field_differs_from_source')
                labels=[str(v).strip() for v in np.asarray(at(data,declared)).reshape(-1)]
                if labels!=proposal['channels']:raise ValueError('MAT_labels_changed')
            else:labels=[]
            side=None
            if side_definition:
                field,c1,c2=side_definition[2].groups()
                literal=str(at(data,parent+'.'+field)).strip()
                if literal in (c1,c2) and len(codes.get(literal,set()))==1:
                    side=next(iter(codes[literal]));details.update(side_key=parent+'.'+field,side_literal=literal)
            for label in labels:
                muscle=label.casefold()
                if muscle not in FULL_NAMES or not muscles_definition or not re.search(r'\b'+re.escape(muscle)+r'\b',muscles_definition[1],re.I):continue
                if sensor_definition:
                    add(record,key,sensor_definition[0],label,'sensor_type','surface-emg',details)
                    add(record,key,muscles_definition[0],label,'anatomy',muscle,details)
                if side:add(record,key,side_definition[0],label,'side',side,details)
            if task_definition:
                field,choices=task_definition[2].groups()
                value=str(at(data,trial+'.'+field)).strip()
                if value=='Walking' and value in [s.strip() for s in choices.split(',')]:
                    add(record,trial+'.'+field,task_definition[0],'','task','walking',dict(details,task_literal=value))
                else:unresolved.append(dict(record=record['name'],reason='task_not_level_walking',source_value=value))
            # Describe source segmentation, but do not invent additional cycles
            # or authorize the continuous-foot-switch processing recipe.
            segment=unique(paragraphs,r'For each trial, foot-strikes were defined from platform data')
            cropped=unique(paragraphs,r'The data reported in the present study includes, for each trial, the portions of kinematic, dynamic and EMG signals between')
            reference_side=None
            if foot_definition:
                foot_literal=str(at(data,trial+'.'+foot_definition[2][1])).strip()
                if len(codes.get(foot_literal,set()))==1:reference_side=next(iter(codes[foot_literal]))
            if segment and cropped and reference_side:
                segments.append(dict(record=record['name'],data_key=key,kind='source-delimited-stride',
                    reference_role='trailing-foot',reference_side=reference_side,
                    source_uri=article.resolve().as_uri(),source_sha256=article_hash,
                    evidence=[dict(pointer=tree.getpath(n),quote=text(n)) for n in (segment[0],cropped[0],foot_definition[0])],
                    processing_requirement='separate_segment_recipe_and_boundary_validation',scientific_approval=False))
        except (ValueError,KeyError,IndexError,TypeError) as exc:
            unresolved.append(dict(record=record['name'],reason=str(exc)))
    if sha(source)!=source_hash or sha(article)!=article_hash:raise ValueError('source_changed_during_join')
    return dict(version=VERSION,observations=observations,unresolved=unresolved,source_segments=segments)
