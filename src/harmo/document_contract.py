"""Fill missing attributes from exact, source-bound documentary observations.

This verifies declarations, not experimental truth or scientific comparability.
No trust status, sample value, unit scale or processing state is changed.
"""
import hashlib
import json
import re

from harmo.document_evidence import check_record
from harmo.evidence.models import HarmoRecordingBundle, AnatomicalEntity, OntologyTerm, SemanticMapping, TaskDescriptor
from harmo.semantic_promotion import checked
from harmo.source_evidence import ANATOMY

VERSION='document-contract/0.1'


def fingerprint(bundle):
    return hashlib.sha256(json.dumps(bundle.to_dict(),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def resolve(bundle,record,observations):
    result=HarmoRecordingBundle.from_dict(bundle.to_dict())
    review=check_record(observations,bundle,record)
    decisions=[]
    channels={c.name:c for c in result.channels}
    if len(channels)!=len(result.channels): raise ValueError('ambiguous_channel_names')
    # Laterality must precede anatomy so both representations agree.
    for attribute in sorted(review['attributes'],key=lambda a:0 if a['field']=='side' else 1):
        field=attribute['field']; channel=channels.get(attribute['channel'])
        decision=dict(channel=attribute['channel'],field=field,status='unresolved',evidence=attribute['evidence'])
        decisions.append(decision)
        if attribute['status']!='source_matched':
            decision.update(status='conflicting',reason='source_or_contract_conflict'); continue
        evidence=attribute['evidence']
        if any(not e.get('source_uri') or not e.get('pointer') or not re.fullmatch('[0-9a-f]{64}',e.get('source_sha256','')) or not e.get('scope_evidence',{}).get('record') for e in evidence):
            decision['reason']='source_binding_required'; continue
        value=attribute['normalized_values'][0]
        refs=[e['source_uri']+'#'+e['pointer']+':sha256:'+e['source_sha256'] for e in evidence]
        mapping=SemanticMapping('explicit-document-record-scope',VERSION,'mapped',refs)
        if field=='unit':
            # A unit declaration cannot justify rewriting already decoded samples.
            decision.update(status='source_confirmed' if channel and channel.unit not in ('unknown','',None) else 'unresolved',reason='no_unit_or_sample_reinterpretation')
        elif field in ('side','sensor_type') and channel is not None:
            current=getattr(channel,field)
            allowed=('left','right','midline') if field=='side' else ('surface-emg','needle-emg','footswitch','electrogoniometer')
            if value not in allowed:
                decision['reason']='unsupported_literal'; continue
            if current not in (None,'','unknown'):
                decision.update(status='source_confirmed',reason='existing_attribute_preserved'); continue
            if field=='side' and channel.anatomy is not None:
                decision['reason']='existing_anatomy_requires_joint_reassessment'; continue
            setattr(channel,field,value)
            decision.update(status='filled',value=value)
        elif field=='anatomy' and channel is not None:
            if channel.anatomy is not None or channel.muscle not in (None,'','unknown'):
                decision['reason']='existing_anatomy_preserved'; continue
            if value not in ANATOMY:
                decision['reason']='full_name_not_in_versioned_vocabulary'; continue
            entity_type=ANATOMY[value][0]
            term=OntologyTerm('HarmoSourceTerms','1.0',value.replace(' ','-'),value)
            channel.anatomy=checked(AnatomicalEntity(entity_type,value,term,channel.side or 'unknown',channel.name,mapping),'anatomicalEntity')
            channel.muscle=value if entity_type=='muscle' else None
            decision.update(status='filled',value=value)
        elif field=='task':
            if result.task is not None:
                decision['reason']='existing_task_preserved'; continue
            if value not in ('walking','running','rest'):
                decision['reason']='task_label_not_in_versioned_vocabulary'; continue
            contexts=sorted({e['protocol_context'] for e in evidence if e.get('protocol_context')})
            facets={'annotation_scope':'recording-protocol-not-sample-labels'}
            if contexts: facets['source_protocol_context']=' | '.join(contexts)
            result.task=checked(TaskDescriptor(value,value,[OntologyTerm('HarmoSourceTerms','1.0',value,value)],
                facets,mapping,None),'task')
            decision.update(status='filled',value=value)
        else:
            decision['reason']='unsupported_attribute'
    if result.task is not None or any(c.anatomy is not None for c in result.channels):
        result.schema_version='1.2.0'
    result=HarmoRecordingBundle.from_dict(result.to_dict())
    if any(d['status']=='filled' for d in decisions) and result.schema_version=='1.2.0':
        from jsonschema import validate
        from harmo.schema_resources import schema_path
        validate(result.to_dict(),json.loads(schema_path('recording-bundle-1.2.0.json').read_text()))
    report=dict(version=VERSION,record=record,input_contract_sha256=fingerprint(bundle),output_contract_sha256=fingerprint(result),
        decisions=decisions,changed=any(d['status']=='filled' for d in decisions),
        scientific_use='unassessed',sample_changes=[],processing_changes=[],
        scope='Exact source declaration plus bounded vocabulary; no inferred task equivalence, calibration, event timing or trust upgrade.')
    return result,report
