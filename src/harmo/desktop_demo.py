"""Deterministic synthetic walk-like signals for installation checks, not evidence."""
import json
from pathlib import Path
import sqlite3
import numpy as np


def create(root):
    from harmo.public_batch import catalog, contracts, sha
    from harmo.walking_plan import save
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    stage = root/'source'
    raw = stage/'raw/walk'
    raw.mkdir(parents=True)
    description = stage/'description.html'
    description.write_text('<p>SYNTHETIC INSTALLATION DEMO. These are generated signals, not participant measurements.</p>'
        '<p>Surface EMG of the anterior tibialis muscles was recorded.</p>'
        '<p>The files under the walk folder containing walking while counting.</p>'
        '<p>The preceding sentences are synthetic metadata for testing the review interface only. '
        'Do not interpret these signals as physiological evidence or validate real-world accuracy with them.</p>')
    save(stage/'source.evidence.json', dict(source_uri='urn:harmo:synthetic-demo',source_sha256=sha(description)))
    entries = []
    with sqlite3.connect(root/'catalog.sqlite') as db:
        catalog(db)
        for index in range(2):
            identity = 'synthetic-record-'+str(index+1)
            name = 'walk/'+identity+'.hea'
            header = stage/'raw'/name
            header.write_text(f'{identity} 2 1500 15000\n'
                f'{identity}.dat 16 1000(0)/uV 16 0 0 0 0 RT TIB.ANT.\n'
                f'{identity}.dat 16 1000(0)/V 16 0 0 0 0 RT FOOTSWITCH\n')
            t = np.arange(15000, dtype=float)/1500
            emg = (2+np.sin(2*np.pi*(t-index*.1)))*np.sin(2*np.pi*100*t)
            foot = (((t%1)>=.2) & ((t%1)<.5)).astype(float)
            digital = np.round(np.column_stack([emg,foot])*1000).astype('<i2')
            data = header.with_suffix('.dat')
            data.write_bytes(digital.tobytes())
            folder = root/identity
            folder.mkdir()
            np.savez(folder/'normalized.npz', time=t, values=digital.astype(float)/1000)
            meta = dict(rows=len(t),fs=1500,signals=[dict(label='RT TIB.ANT.',unit='uV'),
                        dict(label='RT FOOTSWITCH',unit='V')],sha256=sha(folder/'normalized.npz'),
                        source_sha256={name:sha(header),data.relative_to(stage/'raw').as_posix():sha(data)},
                        validation='Synthetic generated int16 samples divided by gain 1000; not an independent reader validation.')
            contracts(identity,'synthetic-demo/1.0.0',meta,folder,(stage/'raw').as_uri()+'/')
            save(folder/'record.json',dict(metadata=meta,synthetic=True))
            db.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',
                (identity,'synthetic-demo/1.0.0',name,'converted',str(folder),'{}'))
            entries.append(dict(id=identity,name=name,status='converted',location=str(folder)))
    path = root/'automatic.summary.json'
    save(path,dict(synthetic=True,scientific_approval=False,intake=dict(catalog=str(root/'catalog.sqlite'),
        entries=[dict(repository='physionet',dataset='synthetic-demo/1.0.0',source_report=str(stage),
                      details=dict(records=entries))])))
    return path
