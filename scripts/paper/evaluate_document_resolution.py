"""Development replay on frozen local public inputs, not a held-out benchmark."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

from harmo.document_evidence import extract
from harmo.document_contract import resolve
from harmo.evidence.models import HarmoRecordingBundle
from harmo.related_documents import related_dois,retrieve


def run(root,output,fetch_article=False):
    output.mkdir(parents=True,exist_ok=False)
    cohort=root/'paper/results/mixed_batch_v002_2026.09.13-r2'
    catalog=cohort/'catalog/catalog.sqlite'
    documents=[]; acquisitions=[]
    metadata=list(cohort.glob('runs/*/source-001/repository.metadata.json'))
    if len(metadata)!=1: raise ValueError('unique_frozen_metadata_required')
    meta=json.loads(metadata[0].read_text())
    dataset='doi:10.6084/m9.figshare.9784964.v1'
    documents.append(dict(dataset=dataset,result=extract(meta['description'].encode(),metadata[0].resolve().as_uri()+'#description')))
    links=related_dois(meta)
    for i,link in enumerate(links):
        if not fetch_article:
            acquisitions.append(dict(**link,status='not_requested')); continue
        try:
            receipt=retrieve(link['doi'],output/f'article-{i}.xml')
            acquisitions.append(dict(**link,receipt=receipt,status='acquired'))
            documents.append(dict(dataset=dataset,result=extract(Path(receipt['path']).read_bytes(),receipt['url'],'xml')))
        except Exception as exc:
            acquisitions.append(dict(**link,status='failed',reason=str(exc)))
    descriptions=list((cohort/'catalog/runs').glob('*/emgdb/1.0.0/description.html'))
    if len(descriptions)!=1: raise ValueError('unique_frozen_description_required')
    documents.append(dict(dataset='emgdb/1.0.0',result=extract(descriptions[0].read_bytes(),descriptions[0].resolve().as_uri())))
    with sqlite3.connect(catalog.resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=db.execute('select id,dataset,name,location from records order by id').fetchall()
    results=[]; input_hashes={}
    for identity,dataset,name,location in rows:
        directory=Path(location)
        for filename in ('recording.bundle.json','trust.report.json'):
            path=directory/filename
            input_hashes[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        original=HarmoRecordingBundle.from_dict(json.loads((directory/'recording.bundle.json').read_text()))
        observations=[o for d in documents if d['dataset']==dataset for o in d['result']['observations']]
        resolved,report=resolve(original,name,observations)
        report.update(record_id=identity,dataset=dataset,channel_count=len(original.channels),matched_attributes=len(report['decisions']))
        if report['changed']:
            from harmo.public_export import checked_samples
            checked_samples(original,directory)
            (output/(identity+'.bundle.json')).write_text(json.dumps(resolved.to_dict(),indent=2))
        results.append(report)
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==s for p,s in input_hashes.items())
    summary=dict(scope='Development replay of previously used public inputs, original contracts; not unseen validation or full harmonize/export execution.',
        catalog=str(catalog),records=len(results),datasets=dict(Counter(r['dataset'] for r in results)),
        changed_records=sum(r['changed'] for r in results),filled_attributes=sum(d['status']=='filled' for r in results for d in r['decisions']),
        matched_attributes=sum(r['matched_attributes'] for r in results),original_contracts_and_trust_unchanged=unchanged,
        acquisitions=acquisitions,documents=documents,records_detail=results,input_hashes=input_hashes,
        no_match_interpretation='No applicable observation under these rules, not absence of information in the source.')
    (output/'report.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('documents','records_detail','input_hashes')},indent=2))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fetch-article',action='store_true')
    args=parser.parse_args()
    run(args.root.resolve(),args.output.resolve(),args.fetch_article)
