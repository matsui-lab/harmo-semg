"""Prevent blank templates or developer drafts from becoming human gold silently."""


def require_reviewed_reference(document, target_ids):
    if document.get('status')!='human_reviewed':
        raise ValueError('human_reviewed_reference_required')
    rows=document.get('labels')
    if not isinstance(rows,list): raise ValueError('reference_labels_required')
    found={}
    for row in rows:
        identity=row.get('target_id')
        if not isinstance(identity,str) or identity in found: raise ValueError('invalid_or_duplicate_reference_target')
        if row.get('reviewed') is not True or not isinstance(row.get('reviewer'),str) or not row['reviewer'].strip():
            raise ValueError('reviewer_confirmation_required')
        status=row.get('reference_status')
        if status not in ('proposed','ambiguous','insufficient'): raise ValueError('reference_status_required')
        if status=='proposed' and (not isinstance(row.get('reference_value'),str) or not row['reference_value'].strip()):
            raise ValueError('reference_value_required')
        if status!='proposed' and row.get('reference_value') is not None: raise ValueError('nonproposed_reference_value')
        if status in ('proposed','ambiguous') and not row.get('evidence'): raise ValueError('reference_evidence_required')
        found[identity]=row
    if set(found)!=set(target_ids): raise ValueError('reference_target_set_mismatch')
    return found
