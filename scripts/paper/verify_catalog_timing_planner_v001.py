"""Replay all registered Study 38 cycles through catalog-bound plans.

This is regression verification, not independent or unseen-study validation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from lxml import etree
from harmo.catalog_timing_plan import prepare, current
from harmo.timing_planner import execute, sha
from harmo.timing_workflow import checked_profile, bind_catalog


def run(root, catalog, sources, article, output):
    protocol_path=root/'paper/physiology_positive_control_protocol.yaml'
    protocol=checked_profile(protocol_path)
    from harmo.catalog_evidence import ARTICLE_HASH
    if sha(article)!=ARTICLE_HASH:raise ValueError('unregistered_article')
    tree=etree.fromstring(article.read_bytes(),etree.XMLParser(resolve_entities=False,no_network=True))
    paragraphs=[' '.join(e.itertext()) for e in tree.iter('p')]
    boundary=next(p for p in paragraphs if 'Two consecutive foot-strike instants' in p)
    dependencies=[dict(path=str(article),sha256=sha(article)),
                  dict(path=str(protocol_path),sha256=sha(protocol_path))]
    for name, spec in protocol['source_assets']['study_38'].items():
        source=sources/name
        if source.stat().st_size!=spec['bytes'] or hashlib.md5(source.read_bytes()).hexdigest()!=spec['md5']:
            raise ValueError('registered_source_changed:'+name)
        dependencies.append(dict(path=str(source),sha256=sha(source)))
    binding=bind_catalog(catalog.parent,sources,protocol)
    output.mkdir(parents=True,exist_ok=False)
    (output/'reference.binding.json').write_text(json.dumps(binding,indent=2))
    rows=[];grouped={}
    for record in binding['bindings']:
        scoped_dependencies=dependencies[:2]+[d for d in dependencies[2:] if Path(d['path']).name==record['source']]
        for muscle, definition in protocol['muscles'].items():
            index=record['channels'].index(definition['study_38_label'])
            rid=record['record_id'];cid='ch-'+str(index)
            bundle,_,channel=current(catalog,rid,cid)
            if channel.name!=definition['study_38_label']:raise ValueError('channel_identity_changed')
            directory=output/(rid+'-'+cid);directory.mkdir()
            values=dict(task='walking',muscle=muscle,side='right',
                        sampling_rate_hz=bundle.timebase.sampling_rate_hz,
                        purpose='activation-timing',processing_protocol='registered-study38-gait120',
                        representation='raw',cycle_boundary='same-side-heel-strike-to-heel-strike')
            receipt=dict(record=record,channel_id=cid,facts=values,article_boundary=boundary,
                         interpretation='Registered walking-stride boundary interpretation; no new event detection. Raw means released EMG before the common envelope, not absence of acquisition filtering.',
                         scope='Deterministic registered-profile replay; not new human confirmation.',dependencies=dependencies)
            proof_path=directory/'binding-evidence.json';proof_path.write_text(json.dumps(receipt,indent=2))
            proof=dict(path=str(proof_path),sha256=sha(proof_path),quote=rid,
                       source_uri='urn:harmo:registered-profile-binding',pointer='record',
                       record_id=rid,channel_id=cid,scope_reason='Exact source cycle and channel verified by bind_catalog; frozen protocol and reviewed article interpretation.',
                       dependencies=scoped_dependencies)
            facts={k:dict(value=v,status='confirmed',evidence=proof) for k,v in values.items()}
            assertion_path=directory/'assertions.json';assertion_path.write_text(json.dumps(facts,indent=2))
            proposal=prepare(catalog,rid,cid,protocol_path,directory/'plan',assertion_path)
            if proposal['status']!='planned':raise ValueError(str(proposal['reasons']))
            execute(proposal,directory/'execution')
            with np.load(directory/'execution/timing.npz') as result:curve=result['values'].copy()
            grouped.setdefault((record['subject'],muscle),[]).append(curve)
            rows.append(dict(record_id=rid,channel_id=cid,subject=record['subject'],muscle=muscle,steps=len(proposal['steps'])))
        print('verified cycle '+record['subject']+' '+record['data_key'],flush=True)
    comparisons=[]
    for reference in binding['curves']:
        key=(reference['subject_id'],reference['muscle'])
        actual=np.median(np.vstack(grouped[key]),axis=0)
        comparisons.append(dict(subject=key[0],muscle=key[1],cycles=len(grouped[key]),
                                exact=np.array_equal(actual,reference['values']),
                                max_absolute_difference=float(np.max(np.abs(actual-reference['values'])))))
    for dependency in dependencies:
        if sha(dependency['path'])!=dependency['sha256']:raise ValueError('dependency_changed_during_replay')
    summary=dict(selected_records=len(binding['bindings']),channel_plans=len(rows),
                 subject_curves=len(comparisons),all_exact=all(c['exact'] for c in comparisons),
                 comparisons=comparisons,plans=rows,dependencies=dependencies,
                 scientific_approval=False,scope='Study 38 registered 10 subjects, 5 cycles, 2 muscles; regression parity only. No new cross-study release or unseen-study validation.')
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    if not summary['all_exact']:raise ValueError('numerical_parity_failed')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('root','catalog','sources','article','output'):parser.add_argument('--'+key,required=True,type=Path)
    args=parser.parse_args()
    result=run(**{k:v.resolve() for k,v in vars(args).items()})
    print(json.dumps({k:result[k] for k in ('selected_records','channel_plans','subject_curves','all_exact')}))
