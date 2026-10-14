"""Disambiguate CSV time units using an explicit acquisition interval.

Only a bounded repository declaration grammar and a matching complete regular
clock are accepted. No timestamp reconstruction, resampling, or title guessing.
"""
import csv
import io
import json
import re
from pathlib import Path
import numpy as np
from lxml import etree
from harmo.public_batch import sha

VERSION = 'document-interval-clock/1'


def propose(path, metadata_path):
    path, metadata_path = Path(path), Path(metadata_path)
    if path.suffix.lower() not in ('.csv','.tsv') or path.stat().st_size>32*1024**2:
        return None
    doc = json.loads(metadata_path.read_text())
    description = doc.get('description','')
    root = etree.fromstring(('<div>'+description+'</div>').encode(),etree.HTMLParser(no_network=True))
    text = ' '.join(' '.join(root.itertext()).split()) if root is not None else ''
    declarations = list(re.finditer(r'\b(?:The )?(\d+) sensors read data every ([\d.]+)\s*(ms|milliseconds|s|seconds)\.', text, re.I))
    if len(declarations)!=1 or not re.search(r'\bThis data is stored in a CSV file with the timestamp\.',text,re.I):
        return None
    if re.search(r'\b(not|may|might|example|previously|resampled|downsampled)\b',text,re.I):
        return None
    m=declarations[0]; interval=float(m[2])*(.001 if m[3].lower() in ('ms','milliseconds') else 1.)
    if not np.isfinite(interval) or interval<=0: return None
    rows=list(csv.reader(io.StringIO(path.read_text(encoding='utf-8-sig')),delimiter='\t' if path.suffix.lower()=='.tsv' else ','))
    labels=rows[0]
    if len(rows)<4 or len(rows)*len(labels)>2_000_000 or len(set(labels))!=len(labels):return None
    times=[c for c in labels if c.lower() in ('time','timestamp')]
    signal=[c for c in labels if re.fullmatch(r'emg[1-9][0-9]*',c,re.I)]
    indices=[c for c in labels if c.lower() in ('slno','index','sample')]
    if len(times)!=1 or len(signal)!=int(m[1]) or set(labels)!=set(times+signal+indices):return None
    values=np.asarray(rows[1:],dtype=float)
    if not np.isfinite(values).all():return None
    for col in indices:
        x=values[:,labels.index(col)]
        if x[0] not in (0,1) or not np.array_equal(x,x[0]+np.arange(len(x))):return None
    t=values[:,labels.index(times[0])]
    matches=[unit for unit,scale in (('s',1.),('ms',.001))
             if np.allclose((t-t[0])*scale,np.arange(len(t))*interval,rtol=0,atol=1e-9)]
    if len(matches)!=1:return None
    proof=dict(rule=VERSION,source_uri=metadata_path.resolve().as_uri(),source_sha256=sha(metadata_path),
        pointer='/description',quote=m[0],csv_scope_quote='This data is stored in a CSV file with the timestamp.',
        payload_sha256=sha(path),column=times[0],interval_seconds=interval,rows_checked=len(t),
        candidates=['s','ms'],accepted_unit=matches[0],scientific_approval=False)
    return dict(time_unit=matches[0],time_column=times[0],index_columns=indices,
                source_reference=proof['source_uri']+'#/description',clock_evidence=proof)
