"""Bind diagnostic timing plans to current catalog contracts and scoped evidence.

An evidence assertion remains the caller's responsibility, not an automated
interpretation of literature or permission to export a scientific comparison.
"""
import json
from pathlib import Path
import numpy as np
from harmo.contract_revision import _row, digest, _check_evidence
from harmo.public_export import load_record, checked_samples


def check_dependencies(proof):
    from harmo.timing_planner import sha
    for dependency in proof.get('dependencies', []):
        if sha(dependency['path']) != dependency['sha256']:
            raise ValueError('assertion_dependency_changed')


def current(catalog, record_id, channel_id):
    bundle, _, directory = load_record(_row(catalog, record_id))
    _, values = checked_samples(bundle, directory)
    indices = [i for i, c in enumerate(bundle.channels) if c.channel_id == channel_id]
    if len(indices) != 1:
        raise ValueError('unique_channel_required')
    return bundle, values[:, indices[0]].copy(), bundle.channels[indices[0]]


def validate_origin(spec):
    origin = spec['catalog_origin']
    for fact in spec['facts'].values():
        if fact.get('status') == 'confirmed':
            _check_evidence(fact['evidence'], origin['record_id'], origin['channel_id'])
            check_dependencies(fact['evidence'])
    bundle, values, _ = current(origin['catalog'], origin['record_id'], origin['channel_id'])
    if digest(bundle.to_dict()) != origin['contract_sha256']:
        raise ValueError('catalog_contract_changed')
    if bundle.samples.checksum != origin['sample_sha256']:
        raise ValueError('catalog_samples_changed')
    with np.load(spec['sample_path'], allow_pickle=False) as extracted:
        if not np.array_equal(extracted['values'], values):
            raise ValueError('extracted_channel_differs_from_catalog')


def prepare(catalog, record_id, channel_id, protocol, output, assertions=None):
    from harmo.timing_planner import plan, sha
    from harmo.timing_workflow import checked_profile
    profile = checked_profile(protocol)
    bundle, values, channel = current(catalog, record_id, channel_id)
    observed = dict(task=bundle.task.activity if bundle.task else None,
                    muscle=channel.anatomy.canonical_label if channel.anatomy else channel.muscle,
                    side=channel.side, sampling_rate_hz=bundle.timebase.sampling_rate_hz)
    facts = {k: dict(value=v, status='candidate') for k, v in observed.items()}
    supplied = {} if assertions is None else json.loads(Path(assertions).read_text())
    allowed = set(observed) | {'purpose', 'processing_protocol', 'representation', 'cycle_boundary'}
    for key, fact in supplied.items():
        if key not in allowed or fact.get('status') != 'confirmed':
            raise ValueError('unsupported_or_unconfirmed_assertion:'+key)
        # No assertion may silently replace the current task/anatomy/timebase.
        if key in observed:
            actual, claimed = observed[key], fact.get('value')
            if isinstance(actual, str) and isinstance(claimed, str):
                actual, claimed = actual.casefold(), claimed.casefold()
                if key == 'muscle' and claimed in profile['muscles']:
                    definition = profile['muscles'][claimed]
                    if actual == definition['study_38_label'].casefold():
                        actual = claimed
            if actual is None or actual != claimed:
                raise ValueError('assertion_conflicts_with_contract:'+key)
        _check_evidence(fact['evidence'], record_id, channel_id)
        check_dependencies(fact['evidence'])
        facts[key] = fact
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    np.savez(out/'channel.npz', values=values)
    spec = dict(sample_path=str(out/'channel.npz'), sample_sha256=sha(out/'channel.npz'),
                sampling_rate_hz=bundle.timebase.sampling_rate_hz, facts=facts,
                catalog_origin=dict(catalog=str(Path(catalog).resolve()), record_id=record_id,
                                    channel_id=channel_id, contract_sha256=digest(bundle.to_dict()),
                                    sample_sha256=bundle.samples.checksum))
    result = plan(spec, protocol)
    (out/'state.json').write_text(json.dumps(spec, indent=2))
    (out/'plan.json').write_text(json.dumps(result, indent=2))
    return result
