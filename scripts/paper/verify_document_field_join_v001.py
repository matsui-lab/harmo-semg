"""Recheck preserved real payloads; no network, source rewrite or human sign-off."""
import argparse
import json
import shutil
from pathlib import Path
import numpy as np
from harmo.public_batch import sha
from harmo.config.automap import save
from harmo.mixed_batch import run as intake
from harmo.general_workflow import run


def main():
    p=argparse.ArgumentParser();p.add_argument('--source-jobs',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=False)
    original=json.loads(Path(a.source_jobs).read_text())
    fig=Path(original[0]['summary']);md=Path(original[1]['summary'])
    entry=json.loads(md.read_text())['intake']['entries'][0];stage=Path(entry['source_report'])
    metadata=json.loads((stage/'repository.metadata.json').read_text())
    acquired={str(f['file_id']):f for f in json.loads((stage/'acquisition.json').read_text())}
    def copy_verified(asset,destination,library):
        prior=acquired[str(asset['file_id'])];source=stage/'raw'/prior['local_name']
        assert sha(source)==prior['sha256'] and prior['verification']['pass']
        shutil.copyfile(source,destination)
        return {'pass':True,'acquisition_backend':'verified-previous-payload','source':str(source),'source_sha256':sha(source)}
    new=intake([entry['url']],out/'mendeley-intake',max_records=2,max_bytes=32*1024**2,
               fetch_metadata=lambda _:metadata,acquire=copy_verified)
    summary=out/'mendeley.summary.json';save(summary,{'intake':new})
    assert new['entries'][0]['converted']==2,new['entries'][0]
    for r in new['entries'][0]['ingestion']['records']:
        record=json.loads((Path(r['location'])/'record.json').read_text())
        assert len(record['metadata']['signals'])==8 and record['metadata']['fs']==200
        with np.load(Path(r['location'])/'normalized.npz') as arrays:
            raw=Path(new['entries'][0]['source_report'])/'raw'/r['source']
            values=np.loadtxt(raw,delimiter=',',skiprows=1)
            np.testing.assert_array_equal(arrays['values'],values[:,1:9])
            np.testing.assert_array_equal(arrays['time'],values[:,9])
    jobs=[{'summary':str(fig.resolve())},{'summary':str(summary)}];save(out/'jobs.json',jobs)
    report=run(jobs,out/'batch',budget={'records':2,'channels':16,'curves':256,'cycles':500,'comparisons':128})
    assert all(e['status']!='failed' for e in report['entries']),report
    fig_output=Path(report['entries'][0]['output'])
    plan=json.loads((fig_output/'plan.json').read_text())
    assert all(r['record'].endswith('.EMG') and len(r['channels'])==8 and r['task']['activity']=='walking' for r in plan['records'])
    assert all(r['recipe']=='source-delimited-stride-envelope/1' and len(r['related_streams'])==2 for r in plan['records'])
    result=json.loads((fig_output/'processing/diagnostic.json').read_text())
    assert len(result['curves'])==16 and len(result['comparisons'])==8,result['records']
    assert all(c['cycles']==1 and c['segmentation']=='source-provided-not-detected' for c in result['curves'])
    assert not result['scientific_approval'] and not result['amplitude_pooling_allowed']
    resumed=run(jobs,out/'batch',resume=True,budget={'records':2,'channels':16,'curves':256,'cycles':500,'comparisons':128})
    assert all(e['cache_reused'] for e in resumed['entries'])
    save(out/'verification.json',dict(status='passed',new_downloads=0,source_jobs=str(Path(a.source_jobs).resolve()),
        source_jobs_sha256=sha(a.source_jobs),mendeley_records=2,mendeley_channels_each=8,mendeley_sampling_rate_hz=200,
        all_CSV_samples_equal=True,figshare_EMG_records=2,curves=16,comparisons=8,
        cycles_per_curve=1,human_reviews=0,LLM_used=False,scientific_approval=False,
        boundary_and_filter_validation='not_independently_validated',cache_reused=2,
        scope='Previously downloaded development datasets; no unseen-study accuracy claim.'))
    print(json.dumps({'status':'passed','output':str(out),'curves':16,'comparisons':8}))


if __name__=='__main__':main()
