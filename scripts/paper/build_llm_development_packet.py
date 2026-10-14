"""Freeze raw-source development inputs separately from reviewer labels."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import numpy as np
from scipy.io import loadmat
from lxml import etree
from harmo.llm_candidates import PROMPT,digest


def build(root,out):
    out.mkdir(parents=True,exist_ok=False)
    cohort=root/'paper/results/mixed_batch_v002_2026.09.13-r2'
    article=root/'paper/results/document_resolution_real_v002_2026.09.13-r2/article-0.xml'
    raw=article.read_bytes(); article_hash=hashlib.sha256(raw).hexdigest()
    tree=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,no_network=True,load_dtd=False))
    sources=[]
    for i,node in enumerate(tree.xpath('//body//p | //body//tr')):
        text=' '.join(''.join(node.itertext()).split())
        if text:
            sources.append(dict(source_id=f'article-{i:04d}',text=text,uri='https://doi.org/10.1038/s41597-019-0323-z',
                source_sha256=article_hash,pointer=node.getroottree().getpath(node)))
    with sqlite3.connect((cohort/'catalog/catalog.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('select id,dataset,name,metadata from records order by id').fetchall()
    selected=[(rid,dataset,name,json.loads(meta)) for rid,dataset,name,meta in rows if json.loads(meta).get('proposal',{}).get('signal_type')=='EMG']
    hashes={s for _,_,_,meta in selected for s in meta['source_sha256'].values()}
    if len(hashes)!=1: raise ValueError('single_development_source_required')
    source_hash=hashes.pop(); mat_path=cohort/'catalog/sources'/source_hash
    if hashlib.sha256(mat_path.read_bytes()).hexdigest()!=source_hash: raise ValueError('MAT_hash_mismatch')
    source=loadmat(mat_path)['s'][0,0]
    def small(value):
        return isinstance(value,np.ndarray) and value.size<=64 and value.dtype.kind in 'USfiu' and (value.dtype.kind in 'US' or np.isfinite(value).all())
    attributes={k:source[k].tolist() for k in source.dtype.names if small(source[k])}
    sources.append(dict(source_id='mat-parent',text=json.dumps(attributes,ensure_ascii=False),source_sha256=source_hash,pointer='s',uri='mat:sha256:'+source_hash))
    cases=[]; labels=[]
    for rid,dataset,name,meta in selected:
        proposal=meta['proposal']; index=int(proposal['data_key'].split('.')[2]); trial=source['Data'].flat[index]
        values=trial['EMG']; channels=[str(v).strip() for v in source['EmgVarName'].flat]
        if values.shape[0]!=len(channels) or proposal['channels']!=channels: raise ValueError('raw_channel_binding_mismatch')
        scalars={k:trial[k].tolist() for k in trial.dtype.names if small(trial[k])}
        sid=f'mat-trial-{index}'
        trial_source=dict(source_id=sid,text=json.dumps(scalars,ensure_ascii=False),source_sha256=source_hash,pointer=f's.Data.{index}',uri='mat:sha256:'+source_hash)
        targets=[]
        for channel in channels:
            for field in ('anatomy','measurement_side','sensor_type','unit'):
                target=dict(target_id=digest([rid,channel,field]),record_id=rid,channel=channel,field=field)
                if field=='measurement_side': target['canonical_choices']=['left','right','midline']
                if field=='sensor_type': target['canonical_choices']=['surface-emg','needle-emg']
                targets.append(target)
        for field in ('task','reference_side','complete_processing_protocol','amplitude_normalization'):
            targets.append(dict(target_id=digest([rid,'',field]),record_id=rid,channel='',field=field))
        cases.append(dict(case_id=rid,dataset=dataset,record_name=name,data_key=proposal['data_key'],
            raw_array_shape=list(values.shape),raw_channel_labels=channels,sources=sources+[trial_source],targets=targets))
        labels.extend(dict(**t,reference_status=None,reference_value=None,evidence=[],reviewer=None,reviewed=False,notes='') for t in targets)
    (out/'model_input.json').write_text(json.dumps(dict(split='development',prompt=PROMPT,cases=cases),indent=2,ensure_ascii=False))
    (out/'human_reference_template.json').write_text(json.dumps(dict(status='unreviewed_blank_template',labels=labels),indent=2))
    manifest=dict(version='llm-development-packet/1',split='development',cases=len(cases),targets=len(labels),
        article_sha256=article_hash,mat_sha256=source_hash,
        files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir())},
        reference_status='not_annotated',model_runs=0,
        limitations=['previously used study; not held out','curated source preparation','no model download or inference performed','no independent reference judgments yet'])
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); build(Path.cwd(),args.output.resolve())
