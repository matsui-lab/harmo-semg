"""Display raw EMG extrema without aliasing away peaks; keep all foot samples."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
from harmo.footswitch_timing import envelope,foot_side,sha
from harmo.public_export import load_record,checked_samples


def write(path,rows):
    with path.open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args()
    report=json.loads((a.root/'diagnostic.json').read_text())
    if any(sha(path)!=digest for path,digest in report['inputs'].items()):raise ValueError('diagnostic_inputs_changed')
    summary_path=next(path for path in report['inputs'] if path.endswith('automatic.summary.json'))
    acquired=json.loads(Path(summary_path).read_text())['intake']
    with sqlite3.connect(Path(acquired['catalog']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        records=db.execute('SELECT id,dataset,name,location FROM records ORDER BY name').fetchall()
    waves=[];feet=[]
    for row in records:
        selected={(c['channel'],c['side']):c for c in report['curves'] if c['record_id']==row[0]}
        if not selected:continue
        bundle,_,directory=load_record(row);time,values=checked_samples(bundle,directory);fs=bundle.timebase.sampling_rate_hz
        n=min(len(time),int(10*fs))
        for ci,ch in enumerate(bundle.channels):
            side=foot_side(ch)
            if side:
                for i in range(n):feet.append(dict(record=row[2],side=side,time=time[i],value=values[i,ci]))
            match=[c for (name,side),c in selected.items() if name==ch.name]
            if not match:continue
            c=match[0];env=envelope(values[:,ci],fs,report['profile'])
            for start in range(0,n,5):
                end=min(start+5,n);mid=(start+end-1)//2;x=values[start:end,ci]
                waves.append(dict(record=row[2],side=c['side'],muscle=c['muscle'],unit=ch.unit,time=time[mid],
                                  minimum=float(x.min()),maximum=float(x.max()),envelope=float(env[mid])))
    write(a.root/'source_EMG_extrema_v002.csv',waves);write(a.root/'source_footswitch_full_v002.csv',feet)
    print(len(waves),len(feet))


if __name__=='__main__':main()
