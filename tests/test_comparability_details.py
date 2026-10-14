"""Controlled logical cases; none of these fixtures is physiological evidence."""
from copy import deepcopy
from itertools import product
import json
from pathlib import Path

import jsonschema
import pytest

from harmo.evidence.models import (
    HarmoRecordingBundle, ProcessingOperation, SignalProcessingState,
    TemporalReference, TrustReport,
)
from harmo.evidence.policy import ComparabilityPolicyEngine, ComparabilityRequest, ReleasePolicy
from test_evidence_contracts import bundle, passing_trust
from test_evidence_policy import manifest_for

ROOT = Path(__file__).resolve().parents[1]


def controlled_pair(mismatch=False, missing=False, outstanding=False):
    left = bundle()
    right = deepcopy(left)
    right.recording_id = 'controlled/right'
    refs = ['urn:harmo:controlled-fixture:sha256:' + 'a' * 64]
    for value in (left, right):
        value.schema_version = '1.1.0'
        value.temporal_reference = TemporalReference(
            'gait-cycle-percent', '%gait-cycle', 0., 100., 100,
            'controlled-start', 'controlled-end', 'left', 'controlled-only',
            'controlled-time-group', refs,
        )
        value.processing_state = SignalProcessingState(
            'normalized-envelope', [ProcessingOperation('controlled-retain')],
            'within-recording', 'not-established', 'controlled-processing', refs, 'b' * 64,
        )
    if mismatch:
        right.channels[0].side = 'right'
    if outstanding:
        right.processing_state = None
    lt = passing_trust(left.recording_id)
    rt = TrustReport(right.recording_id, []) if missing else passing_trust(right.recording_id)
    return left, right, lt, rt, ComparabilityRequest('controlled-timing', 'activation-timing', 'ch-1', 'ch-1')


@pytest.mark.parametrize('mismatch,missing,outstanding', list(product((False, True), repeat=3)))
def test_all_state_combinations_keep_findings_and_legacy_assertion(mismatch, missing, outstanding):
    args = controlled_pair(mismatch, missing, outstanding)
    engine = ComparabilityPolicyEngine()
    inputs_before = [value.to_dict() for value in args[:4]]
    detailed = engine.evaluate_with_details(*args)
    assertion = engine.evaluate(*args).to_dict()
    assert detailed['assertion'] == assertion
    expected = ('not_comparable' if mismatch else 'unassessed' if missing else
                'conditionally_comparable' if outstanding else 'comparable')
    assert assertion['status'] == expected
    kinds = {f['kind'] for g in detailed['check_groups'] for f in g['findings']}
    assert ('incompatibility' in kinds) == mismatch
    assert ('unresolved' in kinds) == missing
    assert ('condition' in kinds) == outstanding
    assert sum(g['execution'] == 'not_selected' for g in detailed['check_groups']) == 2
    assert [value.to_dict() for value in args[:4]] == inputs_before
    jsonschema.validate(assertion, json.loads((ROOT / 'schemas/comparability-assertion-1.1.0.json').read_text()))
    jsonschema.validate(detailed, json.loads((ROOT / 'schemas/comparability-details-1.0.0.json').read_text()))


def test_same_conditional_assertion_under_two_release_policies():
    args = controlled_pair(outstanding=True)
    engine = ComparabilityPolicyEngine()
    comparison = engine.evaluate(*args)
    before = comparison.to_dict()
    statuses = []
    for permitted in (True, False):
        decision = ReleasePolicy(allow_conditional_comparability=permitted).decide(
            args[1], manifest_for(args[1]), args[3], comparison,
        )
        statuses.append(decision.status.value)
        assert comparison.to_dict() == before
        if permitted:
            assert decision.conditions == comparison.conditions
    assert statuses == ['conditional', 'deny']


def test_processing_withdrawal_is_valid_serialized_input_and_restorable():
    args = controlled_pair(outstanding=True)
    restored_input = HarmoRecordingBundle.from_dict(args[1].to_dict())
    assert restored_input.processing_state is None
    with pytest.raises(ValueError, match='processing validation evidence'):
        bad = deepcopy(args[0].to_dict())
        bad['processing_state']['validation_evidence_refs'] = []
        HarmoRecordingBundle.from_dict(bad)


def test_invalid_request_is_not_reported_as_no_findings():
    args = list(controlled_pair())
    args[4] = ComparabilityRequest('invalid-channel', 'activation-timing', 'absent', 'ch-1')
    with pytest.raises(ValueError, match='absent'):
        ComparabilityPolicyEngine().evaluate_with_details(*args)


def test_registered_source_builder_keeps_its_historical_contract(tmp_path):
    import importlib.util
    import numpy as np
    from harmo.physiology_control import load_physiology_protocol
    from harmo.evidence.io import read_json
    path = ROOT / 'scripts/paper/run_comparability_resolution_study.py'
    spec = importlib.util.spec_from_file_location('registered_timing_builder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    protocol_path = ROOT / 'paper/physiology_positive_control_protocol.yaml'
    result_path = ROOT / 'paper/results/validation/physiology_positive_control.json'
    protocol, result = load_physiology_protocol(protocol_path), read_json(str(result_path))
    # Synthetic values exercise contract construction only, not a source replay.
    rows = {row['muscle']: dict(row, values=np.linspace(.1, 1., 101).tolist())
            for row in result['metrics'] if row['study_id']=='study_38' and row['subject_id']=='1'}
    paths = module._write_source_contract('study_38','1',rows,protocol,protocol_path,result_path,result,tmp_path)
    current = HarmoRecordingBundle.from_dict(read_json(str(paths[0])))
    assert current.schema_version == '1.1.0'
    assert current.task is None
    assert all(channel.anatomy is None for channel in current.channels)
