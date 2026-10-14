"""Source-grounded display context and checked waveform previews, not decisions."""
import json
from pathlib import Path
import re
import sqlite3
from zipfile import ZipFile, BadZipFile

import numpy as np
from lxml import etree

from harmo.public_export import load_record, checked_samples
from harmo.walking_plan import sha


def text(node):
    return ' '.join(' '.join(node.itertext()).split()) if node is not None else ''


def clip(value, limit):
    return value[:limit].rstrip() + ('…' if len(value)>limit else '')


def article(source):
    """Extract saved bibliographic fields and abstract, without generated facts."""
    if not source['text'].lstrip().startswith('<'):
        return None
    try:
        root = etree.fromstring(source['text'].encode(), etree.XMLParser(
            resolve_entities=False, no_network=True, load_dtd=False, recover=False))
    except (etree.XMLSyntaxError, ValueError):
        return None
    if root.tag != 'article':
        return None
    meta = root.find('./front/article-meta')
    if meta is None:
        return None
    title = text(meta.find('./title-group/article-title'))
    abstract = ' '.join(text(p) for p in meta.findall('./abstract/p'))
    sentences = re.split(r'(?<=[.!?])\s+', abstract)
    abstract_summary = clip(' '.join(sentences[:2]), 500)
    doi = next((text(n) for n in meta.findall('./article-id') if n.get('pub-id-type')=='doi'), '')
    return dict(title=title, journal=text(root.find('./front/journal-meta/journal-title-group/journal-title')),
                year=text(meta.find('./pub-date/year')), doi=doi,
                summary=abstract_summary if abstract else '要約に使える抄録がありません。',
                brief_summary=clip(sentences[0],300) if abstract else '抄録未取得',
                summary_method='extractive-abstract', source_id=source['source_id'],
                path=source['path'], pointer='front/article-meta',
                scope='Article-level description, not a verified statement about this recording.')


def extrema_preview(time, values, maximum=1200):
    """Display original samples; chronological min/max per bin retains spikes."""
    if len(time) <= maximum:
        indices = np.arange(len(time))
    else:
        indices = {0, len(time)-1}
        for part in np.array_split(np.arange(len(time)), (maximum-2)//2):
            indices.add(int(part[np.argmin(values[part])]))
            indices.add(int(part[np.argmax(values[part])]))
        indices = np.array(sorted(indices))
    return dict(points=np.column_stack([time[indices], values[indices]]).tolist(),
                source_indices=indices.tolist(), source_samples=len(time),
                displayed_samples=len(indices), min=float(np.min(values)), max=float(np.max(values)),
                method='original-samples' if len(time)<=maximum else 'chronological-bin-min-max',
                time_start=float(time[0]), time_end=float(time[-1]))


def repository(entry, bindings, inputs):
    result = dict(name=entry.get('repository','公開元不明'), url=entry.get('url',''),
                  title='', version=None, license='', summary='', source_path=None)
    path = Path(entry.get('source_report',''))/'repository.metadata.json'
    if str(path.resolve()) not in bindings or not path.is_file():
        return result
    if sha(path) != bindings[str(path.resolve())]:
        raise ValueError('repository_context_changed')
    inputs[str(path.resolve())] = sha(path)
    data = json.loads(path.read_text())
    license_value = data.get('license','')
    result.update(title=str(data.get('title') or data.get('name') or ''),
        version=data.get('version'), license=(license_value.get('name','') if isinstance(license_value,dict) else str(license_value)),
        summary=clip(' '.join(re.sub('<[^>]+>',' ',str(data.get('description',''))).split()),200),
        url=data.get('url_public_html') or entry.get('url',''), source_path=str(path))
    return result


def build(packet, items):
    """Read only exact catalog records in the packet and return display bindings."""
    inputs = {}
    bindings = {str(Path(b['path']).resolve()): b['sha256'] for b in packet['bindings']}
    summary = json.loads(Path(packet['summary_path']).read_text())
    intake = summary.get('intake', {})
    catalog = intake.get('catalog')
    rows = {}
    if catalog and Path(catalog).is_file():
        inputs[str(Path(catalog).resolve())] = sha(catalog)
        with sqlite3.connect(Path(catalog).resolve().as_uri()+'?mode=ro', uri=True) as db:
            for identity in {i['target']['record_id'] for i in items}:
                row = db.execute('SELECT id,dataset,name,location FROM records WHERE id=?',(identity,)).fetchone()
                if row:
                    rows[identity] = row
    by_record = {}
    for item in items:
        by_record.setdefault(item['target']['record_id'], []).append(item)
    for identity, group in by_record.items():
        row = rows.get(identity)
        entry = next((e for e in intake.get('entries',[]) if e.get('dataset')==group[0]['dataset']), {})
        repo = repository(entry, bindings, inputs)
        for item in group:
            docs = [d for s in item['sources'] if (d:=article(s)) is not None]
            item['context'] = dict(repository=repo, literature=docs[:3],
                                   literature_omitted=max(0,len(docs)-3),
                                   metadata=None, waveform=dict(status='unavailable',reason='対応する保存済み波形がありません。'))
        if not row:
            continue
        try:
            if row[1]!=group[0]['dataset'] or any(i['target']['record']!=row[2] for i in group):
                raise ValueError('context_record_scope_mismatch')
            bundle, _, directory = load_record(row)
            # Bounded expansion before NPZ loading; no unbounded archive allocation.
            if not bundle.samples or np.prod(bundle.samples.shape,dtype=object)>20_000_000:
                raise ValueError('waveform_cell_budget')
            path = directory/'normalized.npz'
            if path.stat().st_size>160*1024**2:
                raise ValueError('waveform_file_budget')
            with ZipFile(path) as archive:
                if sum(m.file_size for m in archive.infolist())>320*1024**2:
                    raise ValueError('waveform_expansion_budget')
            times, values = checked_samples(bundle, directory)
            if not len(times):
                raise ValueError('empty_waveform')
            # Bind checked numeric inputs and the record's local contract files.
            inputs[str(path.resolve())] = sha(path)
            for p in directory.glob('*.json'):
                if p.is_file():
                    inputs[str(p.resolve())] = sha(p)
            for item in group:
                target = item['target']
                channel = next((i for i,c in enumerate(bundle.channels) if c.channel_id==target.get('channel_id')),None)
                representative = target.get('channel_id') is None
                if representative:
                    channel = 0
                if channel is None or (not representative and bundle.channels[channel].name!=target['channel']):
                    item['context']['waveform']['reason']='対象チャンネルと保存済み波形が一致しません。'
                    continue
                c = bundle.channels[channel]
                raw_header = next((s for s in item['sources'] if s['source_id']=='header'),None)
                context_fields = {}
                if raw_header:
                    try:
                        metadata = json.loads(raw_header['text'])
                        context_fields = metadata.get('source_context', {})
                    except (ValueError, AttributeError):
                        pass
                item['context']['metadata'] = dict(record=row[2], file=row[2].split('::')[0],
                    data_key=row[2].split('::',1)[1] if '::' in row[2] else None,
                    channels=len(bundle.channels), channel=c.name, channel_id=c.channel_id,
                    samples=len(times), sampling_rate_hz=bundle.timebase.sampling_rate_hz,
                    duration_seconds=float(times[-1]-times[0]), clock=bundle.timebase.clock,
                    source_context=context_fields, unit=c.unit, header_source_id='header' if raw_header else None)
                y = values[:,channel]
                full = extrema_preview(times,y)
                n = max(1, int(np.searchsorted(times,times[0]+2,side='right')))
                initial = extrema_preview(times[:n],y[:n])
                item['context']['waveform'] = dict(status='available',channel=c.name,channel_id=c.channel_id,
                    representative=representative,unit=c.unit,source_sha256=sha(path),
                    full=full,initial=initial,source_path=str(path),
                    processing='Converted stored samples; no added filtering, rectification or amplitude normalization.',
                    scientific_approval=False)
        except (ValueError, OSError, KeyError, TypeError, BadZipFile) as exc:
            for item in group:
                item['context']['waveform'] = dict(status='unavailable',reason=str(exc))
    for path, expected in inputs.items():
        if sha(path)!=expected:
            raise ValueError('display_context_input_changed')
    return inputs
