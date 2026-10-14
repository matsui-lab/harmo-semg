"""Conservative prose/header joins with explicit, reproducible derivation rules.

Not an unrestricted prose interpreter. A source statement, a versioned lexical
label rule and exact header scope are all required; no trust is promoted.
"""
import hashlib
import re
from pathlib import PurePosixPath
from lxml import etree

VERSION = 'scoped-prose/2'
# Full names and long conventional abbreviations only; short TA/GL/etc remain
# review candidates because their meaning is not unique across experiments.
MUSCLES = {
    'tibialis anterior': (('tibialis anterior', 'anterior tibialis'), ('TIBIALISANTERIOR', 'ANTERIORTIBIALIS', 'TIBANT')),
    'gastrocnemius lateralis': (('gastrocnemius lateralis', 'lateral gastrocnemius'), ('GASTROCNEMIUSLATERALIS', 'LATERALGASTROCNEMIUS', 'LATGASTRO')),
    'rectus femoris': (('rectus femoris',), ('RECTUSFEMORIS',)),
    'vastus lateralis': (('vastus lateralis',), ('VASTUSLATERALIS',)),
}

from harmo.lower_limb_terms import FULL_NAMES
for _name in FULL_NAMES:
    MUSCLES.setdefault(_name, ((_name,), (re.sub(r'[^A-Z0-9]', '', _name.upper()),)))


def extract(raw, uri, record, header_raw, header_uri):
    if len(raw) > 2*1024**2 or len(header_raw) > 1024**2:
        raise ValueError('source_size_budget')
    root = etree.fromstring(raw, etree.HTMLParser(no_network=True))
    if root is None:
        raise ValueError('empty_source')
    for node in root.xpath('//script|//style'):
        node.getparent().remove(node)
    tree = root.getroottree()
    digest = hashlib.sha256(raw).hexdigest()
    header_digest = hashlib.sha256(header_raw).hexdigest()
    lines = [(i+1, s.strip()) for i, s in enumerate(header_raw.decode().splitlines()) if s.strip() and not s.lstrip().startswith('#')]
    if not lines:
        raise ValueError('empty_header')
    count = int(lines[0][1].split()[1])
    channels = []
    for number, line in lines[1:1+count]:
        parts = line.split()
        if len(parts) < 9:
            raise ValueError('explicit_channel_label_required')
        label = ' '.join(parts[8:])
        side = re.fullmatch(r'(RT|LT|RIGHT|LEFT)[ ._-]+(.+)', label, re.I)
        body = side[2] if side else label
        body = re.sub(r'[^A-Z0-9]', '', body.upper())
        candidates = [name for name, (_, aliases) in MUSCLES.items() if body in aliases]
        channels.append(dict(label=label, line=number, text=line, muscle=candidates[0] if len(candidates)==1 else None,
                             side=({'RT':'right','RIGHT':'right','LT':'left','LEFT':'left'}[side[1].upper()] if side else None)))
    observations, unresolved = [], []
    def add(node, channel, field, value, rule):
        quote = re.sub(r'\s+', ' ', ''.join(node.itertext())).strip()
        scope = dict(record=dict(value=record, pointer='line:1', source_uri=header_uri, source_sha256=header_digest))
        if channel:
            scope['channel'] = dict(value=channel['label'], pointer='line:'+str(channel['line']),
                                    quote=channel['text'], source_uri=header_uri, source_sha256=header_digest)
        observations.append(dict(record=record, channel=channel['label'] if channel else '', field=field, value=value,
            status='source_observation', source_uri=uri, source_sha256=digest, pointer=tree.getpath(node), quote=quote,
            scope_evidence=scope, scope_rule=VERSION+'/'+rule, derivation='bounded-prose-plus-exact-header-label',
            scientific_approval=False))
    for node in root.xpath('//p'):
        text = re.sub(r'\s+', ' ', ''.join(node.itertext())).strip()
        # Negative, tentative and historical attribution are never declarations.
        if re.search(r'\b(no|not|without|may|might|could|previous|prior|example|hypothetical)\b', text, re.I):
            continue
        assertion = re.search(r'(?:^|\.\s+)(?:Surface EMG|Surface electromyography) of (?:the )?(.+?) muscles? (?:was|were) recorded(?: simultaneously with (?:the )?gait data)?\.', text, re.I)
        if assertion:
            for c in channels:
                if c['muscle'] and any(re.search(r'\b'+re.escape(name)+r'\b', assertion[1], re.I) for name in MUSCLES[c['muscle']][0]):
                    add(node, c, 'sensor_type', 'surface-emg', 'asserted-acquisition-long-muscle-label')
                    add(node, c, 'anatomy', c['muscle'], 'asserted-acquisition-long-muscle-label')
                    if c['side']:
                        add(node, c, 'side', c['side'], 'explicit-header-laterality-token')
        # Folder names are extracted from the source, never supplied by dataset id.
        for match in re.finditer(r'files under the ([A-Za-z0-9_-]+) folder containing ([^.]+?)(?= and the files under|\.|$)', text, re.I):
            if match[1] not in PurePosixPath(record).parts[:-1]:
                continue
            phrase = match[2]
            activities = [a for a in ('walking', 'running', 'rest') if re.search(r'\b'+a+r'\b', phrase, re.I)]
            if len(activities)==1:
                add(node, None, 'task', activities[0], 'explicit-folder-task-declaration')
                observations[-1]['protocol_context'] = phrase
                observations[-1]['scope_evidence']['folder'] = dict(value=match[1], pointer=tree.getpath(node), quote=match[0])
    for c in channels:
        for field in ('sensor_type', 'anatomy', 'side'):
            if not any(o['channel']==c['label'] and o['field']==field for o in observations):
                unresolved.append(dict(record=record, channel=c['label'], field=field, reason='no_unambiguous_supported_prose_header_join'))
    if not any(o['field']=='task' for o in observations):
        unresolved.append(dict(record=record, channel='', field='task', reason='no_explicit_supported_record_task_scope'))
    return dict(version=VERSION, source_uri=uri, source_sha256=digest, header_uri=header_uri,
                header_sha256=header_digest, observations=observations, unresolved=unresolved,
                coverage='bounded_derived_declarations', scope='No experimental truth, task equivalence, event alignment, processing or trust approval.')
