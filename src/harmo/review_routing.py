"""Conservative next-action routing, never evidence promotion or permission.

Candidate existence and source availability guide presentation, not correctness.
Unrecognized blockers remain technical holds; no status is changed by this module.
"""
from collections import Counter

FIELDS = {'anatomy': 'Muscle', 'measurement_side': 'Electrode side', 'task': 'Task'}
NEEDED = {
    'anatomy': 'Acquisition methods or channel definitions mapping labels to muscles',
    'measurement_side': 'Metadata or methods specifying electrode side, not merely the contacting foot',
    'task': 'Protocol or trial table linking this record to its task and conditions',
}
IMPACT = {
    'anatomy': 'Used to match records measuring the same muscle.',
    'measurement_side': 'Used to keep measurement sides distinct.',
    'task': 'Used to select matching task conditions.',
}


def question(item):
    field = item['target']['field']
    sources = item.get('sources', [])
    usable = [s for s in sources if str(s.get('text', '')).strip()]
    definitions = [s for s in usable if s['source_id'] != 'header']
    values = list(dict.fromkeys(item.get('rule', {}).get('values', []) +
                               ([item['llm_candidate']['value']] if item.get('llm_candidate') else [])))
    sufficient_to_review = bool(usable) and (field == 'measurement_side' or bool(definitions))
    if not usable or not sufficient_to_review:
        category = 'additional_evidence'
        title = FIELDS[field]+': evidence needed'
        instruction = 'The review packet lacks required evidence. No value entry is needed.'
        next_action = NEEDED[field]+' must be added to the review packet. Review later or hold for now.'
    elif item.get('status') == 'rule_llm_conflict' or len(values) > 1:
        category = 'resolve_conflict'
        title = FIELDS[field]+': conflicting candidates'
        instruction = 'Check the source definition and its scope, then choose one supported candidate.'
        next_action = 'Hold if unresolved. Explain the evidence when editing a candidate.'
    elif values:
        category = 'confirm_scope'
        title = FIELDS[field]+': confirm applicability'
        instruction = 'Check that the evidence supports this candidate for this record.'
        next_action = 'Confirm only if supported. Source availability alone is not confirmation.'
    else:
        category = 'inspect_evidence'
        title = FIELDS[field]+': inspect sources for a candidate'
        instruction = 'Sources exist, but no candidate was generated. This does not establish that the information is absent.'
        next_action = 'Use Edit only after finding a definition. Otherwise review later.'
    return dict(version='review-routing/1', category=category, title=title,
                instruction=instruction, next_action=next_action,
                required_evidence=NEEDED[field], impact=IMPACT[field],
                target_ids=[item['target']['target_id']], candidates=values,
                source_ids=[s['source_id'] for s in usable],
                confirmation_allowed=sufficient_to_review,
                evidence_entailment_verified=False, automatic_resolution=False,
                scientific_approval=False)


# Exact reason contracts: do not guess a fix from arbitrary exception text.
RULES = {
    'axis_semantics_require_evidence; no sampling-rate inheritance': ('additional_evidence', 'Array axes and timebase need definitions', 'Identify time, channel and coordinate axes and the timebase of this signal. EMG sampling rates must not be inherited by another signal.'),
    'additional_intake_issues_in_source_report': ('source_repair', 'Additional issues in the acquisition report', 'Check issues omitted by display limits in the acquisition report. Record review does not imply complete acquisition.'),
    'record_not_converted': ('source_repair', 'Check file reading', 'Check format and reader conditions in the acquisition/conversion report. Entering a muscle name cannot resolve this.'),
    'no_acquired_source_report': ('source_repair', 'Acquisition report required', 'Provide the acquisition report and complete file retrieval and validation.'),
    'surface_EMG_evidence': ('additional_evidence', 'Sensor definition required', 'Find channel definitions or methods establishing surface EMG.'),
    'supported_lower_limb_muscle_evidence': ('additional_evidence', 'Muscle mapping required', 'Establish the channel-to-muscle mapping. Do not relabel an out-of-scope muscle as a lower-limb muscle.'),
    'measurement_side_evidence': ('additional_evidence', 'Electrode side required', NEEDED['measurement_side']+' must be checked.'),
    'source_scoped_walking_required': ('design_or_scope', 'Check walking applicability', 'Determine whether the task is unknown or not walking. Other tasks require a separately validated recipe.'),
    'no_source_resolved_lower_limb_EMG': ('additional_evidence', 'Channel evidence required', 'Resolve missing sensor, muscle and measurement-side evidence.'),
    'sampling_rate_incompatible_with_registered_filter': ('unsupported_processing', 'Filter incompatible with input', 'Check the source rate and filter settings; validate an alternative. Do not change declared frequency to bypass the hold.'),
    'source_stride_duration_outside_registered_bounds': ('unsupported_processing', 'Cycle duration outside recipe bounds', 'Check cycle boundaries and time units to distinguish an error from an out-of-scope input.'),
    'reviewed_input_not_supported_by_raw_EMG_recipe': ('unsupported_processing', 'Raw EMG recipe cannot be applied', 'Check processing history and explicit holds; validate an appropriate recipe.'),
    'document_or_explicit_review_conflict': ('resolve_conflict', 'Evidence or review conflict', 'Check conflicting sources, scope and explicit holds. Previous decisions are not automatically overwritten.'),
    'conflicting_source_stride_references': ('resolve_conflict', 'Conflicting cycle references', 'Check the reference foot, boundary definition and applicable records.'),
    'input_representation_and_processing_history': ('additional_evidence', 'Input processing history required', 'Raw EMG versus rectified/envelope input must be established from methods, not signal appearance alone.'),
    'source_segmentation_and_filter_boundary_validation': ('method_validation', 'Cycle-boundary effects need validation', 'Validate source boundaries, neighboring samples and filter-edge sensitivity. A confirmation button cannot resolve this.'),
    'detector_ground_truth_validation': ('method_validation', 'Event detection requires reference validation', 'Obtain independent event times and measure detection error.'),
    'physiological_event_identity_and_detector_validation': ('method_validation', 'Event meaning and detection need validation', 'Establish contact, voltage and polarity definitions, then compare with independent reference times.'),
    'scientific_comparison_design': ('design_or_scope', 'Comparison design required', 'Define participant, trial and condition pairing and assess sensitivity. Shape differences alone do not authorize scientific comparison.'),
}


def route_reason(reason):
    code = str(reason)
    # One known typed prefix carries the actual representation as a suffix.
    if code.startswith('existing_processing_representation_requires_other_recipe: '):
        row = ('unsupported_processing', 'Input representation requires a different recipe', 'The signal is already processed. Do not apply a raw EMG filter again; validate a compatible recipe.')
    else:
        row = RULES.get(code, ('technical_investigation', 'Further investigation required', 'Unclassified reason. Preserve the original error and target and investigate; never release by guessing.'))
    category, title, action = row
    return dict(reason=code, category=category, title=title, next_action=action,
                automatic_resolution=False, scientific_approval=False)


def route_work(items):
    counts = Counter()
    routed = []
    for item in items:
        routes = [route_reason(code) for code in item['reason'].split(',')]
        counts.update(r['category'] for r in routes)
        routed.append(dict(item, routing=routes))
    return dict(version='review-routing/1', items=routed, counts=dict(counts),
                automatic_resolutions=0, scientific_approvals=0)
