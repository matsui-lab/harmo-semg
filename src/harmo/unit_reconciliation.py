"""Reconcile declarations for one unchanged signal representation; never convert."""
import hashlib
from pathlib import Path

QUESTIONS = {
    'physical_unit': 'What physical voltage unit does this source declare for the target EMG channel? Use only this source. Return a unit symbol. If this source does not establish the unit, return insufficient. Do not borrow a declaration from other sources.',
    'digital_to_physical_calibration': 'What digital-to-physical conversion gain and baseline are declared for the target channel? Report only explicit file calibration, not MVC or other physiological normalization. Return insufficient if not stated.',
    'physiological_amplitude_normalization': 'What physiological amplitude normalization is explicitly documented for this recording (for example MVC or a reference contraction)? ADC gain, voltage units and acquisition amplification are not physiological normalization. Return insufficient if not stated.',
}


def canonical_unit(value):
    # Preserve SI prefix case and scale. µV and mV must not collapse to volts.
    aliases={'V':'V','volt':'V','volts':'V','mV':'mV','millivolt':'mV','millivolts':'mV',
             'uV':'uV','µV':'uV','μV':'uV','microvolt':'uV','microvolts':'uV',
             'nV':'nV','nanovolt':'nV','nanovolts':'nV'}
    return aliases.get(value.strip()) if isinstance(value,str) else None


def verify_wfdb_unit(path, expected_sha256, channel_index, channel_label, target_id, source_id='header'):
    """Read explicit header units with WFDB; reject defaults and scope mismatches."""
    import wfdb
    path=Path(path)
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('header_hash_mismatch')
    lines=[line for line in raw.decode().splitlines() if line.strip() and not line.lstrip().startswith('#')]
    if not isinstance(channel_index,int) or channel_index<0:raise ValueError('invalid_channel_index')
    header=wfdb.rdheader(str(path.with_suffix('')))
    if not getattr(header,'sig_name',None) or channel_index>=header.n_sig:raise ValueError('unsupported_header_scope')
    if header.sig_name[channel_index]!=channel_label:raise ValueError('channel_label_mismatch')
    fields=lines[1+channel_index].split()
    if len(fields)<9 or '/' not in fields[2]:raise ValueError('explicit_unit_required')
    declared=fields[2].split('/',1)[1]
    unit=canonical_unit(declared)
    if not unit or unit!=canonical_unit(header.units[channel_index]):raise ValueError('unsupported_or_mismatched_unit')
    return dict(target_id=target_id,source_id=source_id,unit=unit,channel_index=channel_index,
        channel_label=channel_label,source_sha256=expected_sha256,header_path=str(path.resolve()),
        method='wfdb_explicit_header_unit',evidence_line=lines[1+channel_index])


def reconcile_units(claims, target_id, expected_sources, *, file_unit_evidence=None):
    """All claims must concern the same target AND same signal representation.

    Source IDs here identify independently queried documents/files, not paragraphs.
    Mechanical extraction validity is a prerequisite, not semantic certification.
    """
    expected=set(expected_sources)
    if not expected or len(expected)!=len(expected_sources):raise ValueError('invalid_expected_sources')
    seen=set();values=[];unresolved=[]
    for claim in claims:
        if claim.get('target_id')!=target_id:raise ValueError('target_scope_mismatch')
        if claim.get('field')!='physical_unit':raise ValueError('wrong_field')
        source=claim.get('source_id')
        if source not in expected or source in seen:raise ValueError('invalid_or_duplicate_source')
        seen.add(source)
        unit=canonical_unit(claim.get('value'))
        if claim.get('status')!='proposed' or not claim.get('structurally_valid') or not unit:
            unresolved.append(source)
        else:values.append(dict(source_id=source,raw_value=claim['value'],unit=unit,evidence=claim.get('evidence',[])))
    missing=sorted(expected-seen);distinct=sorted({v['unit'] for v in values})
    status='conflict' if len(distinct)>1 else 'insufficient' if missing or unresolved or not values else 'consistent'
    reason={'conflict':'contradictory_declarations','insufficient':'incomplete_or_uncertain_evidence','consistent':'matching_declarations'}[status]
    # This evidence is supplied by the application parser, never accepted from an LLM response.
    if file_unit_evidence is not None:
        proof=file_unit_evidence
        if proof.get('target_id')!=target_id or proof.get('method')!='wfdb_explicit_header_unit':
            raise ValueError('invalid_file_unit_evidence')
        matches=[v for v in values if v['source_id']==proof.get('source_id') and v['unit']==proof.get('unit')]
        if len(matches)!=1:raise ValueError('file_unit_evidence_mismatch')
        absent=[c for c in claims if c.get('status')=='insufficient' and c.get('structurally_valid') and not c.get('errors')]
        if status=='insufficient' and not missing and set(unresolved)=={c['source_id'] for c in absent}:
            status='file_supported';reason='explicit_file_unit_without_competing_declaration'
    ready=status in ('consistent','file_supported')
    return dict(target_id=target_id,field='physical_unit',status=status,declarations=values,
        reason=reason,file_unit_evidence=file_unit_evidence,
        missing_sources=missing,unresolved_sources=unresolved,
        candidate_unit=distinct[0] if ready else None,
        hold=not ready,requires_semantic_review=True,
        automatic_approvals=0,allow_amplitude_integration=False,conversion_performed=False)
