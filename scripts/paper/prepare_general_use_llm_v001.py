"""Same-source rules/LLM development packet; no human reference is generated."""
import argparse
import hashlib
import json
from pathlib import Path
from lxml import etree
from harmo.llm_candidates import PROMPT
from harmo.scoped_prose import extract


def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    cases=[];baseline=[]
    for slug in ('taichidb','grabmyo','hd-semg'):
        descriptions=list((a.root/slug).rglob('description.html'))
        if len(descriptions)!=1:raise ValueError('unique_description_required')
        description=descriptions[0];stage=description.parent
        headers=sorted((stage/'raw').rglob('*.hea'))
        if not headers:raise ValueError('downloaded_header_required')
        header=headers[0];record=header.relative_to(stage/'raw').as_posix()
        raw=description.read_bytes();tree=etree.fromstring(raw,etree.HTMLParser(no_network=True))
        uri=json.loads((stage/'source.evidence.json').read_text())['source_uri']
        sources=[]
        for i,node in enumerate(tree.xpath('//p')):
            text=' '.join(''.join(node.itertext()).split())
            if text:
                sources.append(dict(source_id=f'p{i}',text=text,source_uri=uri,
                                    source_sha256=hashlib.sha256(raw).hexdigest(),pointer=tree.getroottree().getpath(node)))
        text=header.read_text()
        sources.append(dict(source_id='header',text=text,source_uri=header.resolve().as_uri(),
                            source_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),pointer='whole-file'))
        if sum(len(s['text']) for s in sources)>100000:raise ValueError('packet_source_budget')
        lines=[s.split() for s in text.splitlines() if s.strip() and not s.lstrip().startswith('#')]
        label=' '.join(lines[1][8:])
        targets=[]
        for field in ('anatomy','measurement_side','sensor_type','unit','task','complete_processing_protocol','amplitude_normalization'):
            target=dict(target_id=slug+':'+field,field=field,record=record,
                        channel=label if field in ('anatomy','measurement_side','sensor_type','unit') else '')
            if field=='sensor_type':target['canonical_choices']=['surface-emg','needle-emg','footswitch','force','unknown']
            if field=='measurement_side':target['canonical_choices']=['left','right','midline']
            targets.append(target)
        case=dict(case_id=slug,record=record,raw_metadata=dict(header_path=record,channel_label=label),targets=targets,sources=sources)
        cases.append(case)
        rules=extract(raw,uri,record,header.read_bytes(),header.resolve().as_uri())['observations']
        for target in targets:
            field='side' if target['field']=='measurement_side' else target['field']
            values=sorted({o['value'] for o in rules if o['field']==field and o['channel']==target['channel']})
            if field=='unit':
                explicit=lines[1][2].split('/')
                if len(explicit)==2:values=[explicit[1]]
            baseline.append(dict(target_id=target['target_id'],values=values,
                                 status='proposed' if len(values)==1 else ('ambiguous' if values else 'insufficient'),
                                 semantic_accuracy_evaluated=False))
    (a.output/'packet.json').write_text(json.dumps(dict(prompt=PROMPT,cases=cases),indent=2,ensure_ascii=False))
    (a.output/'rules.json').write_text(json.dumps(baseline,indent=2))
    print(json.dumps(dict(cases=len(cases),targets=len(baseline),rule_proposals=sum(r['status']=='proposed' for r in baseline))))


if __name__=='__main__':main()
