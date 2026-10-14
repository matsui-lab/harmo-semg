"""Freeze two previously collected studies, excluding derived semantic labels."""
import hashlib
import json
from pathlib import Path
from lxml import html
from harmo.llm_candidates import PROMPT,digest


def build(root,out):
    base=root/'downloads/physionet_batch_v001_2026.09.13-r1/runs/ff2e47a15406490db9a17e5e68f8102e'
    cases=[];labels=[]
    specs=[('emgdb','1.0.0','emg_healthy',0),('semg','1.0.1','S01',2),('semg','1.0.1','S01',5)]
    for study,version,record,index in specs:
        folder=base/study/version
        sources=[]
        path=folder/'description.html';raw=path.read_bytes()
        tree=html.fromstring(raw)
        # Same unranked main-content paragraphs for both studies; no keyword filtering.
        for i,node in enumerate(tree.xpath('//main//p')):
            text=' '.join(node.text_content().split())
            if text:
                sources.append(dict(source_id=f'description-{i:03d}',text=text,
                    source_sha256=hashlib.sha256(raw).hexdigest(),pointer=node.getroottree().getpath(node),
                    uri=f'https://physionet.org/content/{study}/{version}/'))
        header=folder/'raw'/f'{record}.hea';raw=header.read_bytes();text=raw.decode()
        lines=text.splitlines();count=int(lines[0].split()[1])
        channels=[' '.join(line.split()[8:]) for line in lines[1:1+count]]
        channel=channels[index]
        sources.append(dict(source_id='wfdb-header',text=text,source_sha256=hashlib.sha256(raw).hexdigest(),
            pointer=header.name,uri=f'https://physionet.org/files/{study}/{version}/{record}.hea'))
        caseid=digest([study,version,record,index]);targets=[]
        for field in ('anatomy','measurement_side','sensor_type','unit','task','reference_side','complete_processing_protocol','amplitude_normalization'):
            target=dict(target_id=digest([caseid,field]),record_id=caseid,
                        channel=channel if field in ('anatomy','measurement_side','sensor_type','unit') else '',field=field)
            if field=='measurement_side':target['canonical_choices']=['left','right','midline']
            if field=='sensor_type':target['canonical_choices']=['surface-emg','needle-emg']
            targets.append(target)
        cases.append(dict(case_id=caseid,dataset=study,record_name=record,raw_channel_labels=channels,
                          selected_channel_index=index,sources=sources,targets=targets))
        labels.extend(dict(**t,reference_status=None,reference_value=None,evidence=[],reviewed=False,reviewer=None) for t in targets)
    out.mkdir(parents=True,exist_ok=False)
    for name,value in [('model_input.json',dict(split='cross-study-transfer',prompt=PROMPT,cases=cases)),
                       ('human_reference_template.json',dict(status='unreviewed_blank_template',labels=labels))]:
        (out/name).write_text(json.dumps(value,indent=2,ensure_ascii=False))
    (out/'manifest.json').write_text(json.dumps(dict(studies=2,cases=3,targets=24,
        files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()},
        limitations=['Studies used previously in software development, not in preceding model pilot.',
                      'No independent human reference; no accuracy estimate.',
                      'Curated record/channel selection; full main paragraphs, no relevance ranking.',
                      'Header and repository-description interpretation only; waveform conversion not evaluated.',
                      'Two SEMG channels are correlated, not independent studies.']),indent=2))


if __name__=='__main__':
    build(Path.cwd(),Path('paper/results/llm_transfer_packet_v001_2026.09.14-r1'))
