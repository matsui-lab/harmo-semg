"""Retrospective design evaluation; never overwrites frozen A--H results."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import importlib.metadata
from itertools import product
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from harmo.evidence.io import sha256_contract
from harmo.evidence.models import HarmoRecordingBundle, TrustReport, ComparabilityAssertion, EvidenceManifest
from harmo.evidence.policy import ComparabilityPolicyEngine, ComparabilityRequest, ReleasePolicy
from harmo.evidence.semantic_claims import SemanticEnrichmentReport, compare_semantic_coverage, build_semantic_overlay
from test_comparability_details import controlled_pair
from test_evidence_policy import manifest_for


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def protected():
    paths = [ROOT / 'paper/paper.md', ROOT / 'paper/MANUSCRIPT_VERSION']
    paths += sorted((ROOT / 'paper/results/validation').glob('*.json'))
    paths += sorted((ROOT / 'paper/results/atlas_materialization').rglob('*.json'))
    paths += [ROOT / 'paper/physiology_positive_control_protocol.yaml', ROOT / 'paper/literature_semantic_enrichment.yaml']
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def engines(baseline_path):
    spec = importlib.util.spec_from_file_location('harmo.evidence._pre_details_policy', baseline_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return ComparabilityPolicyEngine(), module.ComparabilityPolicyEngine()


def evaluate(engine, baseline, args):
    before = [x.to_dict() for x in args[:4]]
    detail = engine.evaluate_with_details(*args)
    old = baseline.evaluate(*args).to_dict()
    detail['unchanged_from_pre_extension'] = old == detail['assertion']
    detail['input_unchanged'] = before == [x.to_dict() for x in args[:4]]
    return detail


def core(out, baseline_path, control_reference=None):
    if (out / 'core_summary.json').exists():
        raise FileExistsError('use a new output directory for a repeat')
    before = protected()
    save(out / 'protected_inputs_before.json', before)
    shutil.copy2(baseline_path, out / 'policy_before.py')
    engine, baseline = engines(baseline_path)
    report_path = ROOT / 'paper/results/validation/literature_semantic_enrichment.json'
    report = SemanticEnrichmentReport.from_dict(read(report_path))
    frozen = read(ROOT / 'paper/results/validation/semantic_coverage_comparison.json')
    coverage = compare_semantic_coverage(report, report_sha256=digest(report_path),
        study_ids=[r['study_id'] for r in frozen['study_rows']]).to_dict()
    coverage_match = coverage['study_rows'] == frozen['study_rows'] and coverage['summary'] == frozen['summary']
    save(out / 'T1_recomputed_coverage.json', coverage)
    decisions = {d.claim_id: d.to_dict() for d in report.decisions}
    sources = {s.source_id: s.to_dict() for s in report.sources}
    ledger = []
    for row in coverage['study_rows']:
        props = sorted({c.property_path for c in report.claims if c.study_id == row['study_id']})
        for prop in props:
            cs = [c for c in report.claims if c.study_id == row['study_id'] and c.property_path == prop]
            ledger.append(dict(study_id=row['study_id'], property=prop,
                baseline_available=prop in row['file_only_properties'],
                enriched_available=prop in row['paper_assisted_properties'],
                added=prop in row['properties_added_by_literature'],
                claims=[dict(claim=c.to_dict(), decision=decisions.get(c.claim_id), source=sources[c.source_id]) for c in cs],
                decision_effect='not_evaluated_no_direct_enrichment_to_comparison_link'))
    save(out / 'T1_property_ledger.json', dict(rows=ledger,
        baseline_note='Frozen observation sources include analyst-maintained configurations; not raw-file-only discovery.'))

    scope_rows = []
    for claim in sorted(report.claims, key=lambda c: c.claim_id):
        if claim.source_role != 'authority':
            continue
        single = deepcopy(report)
        single.claims = [deepcopy(claim)]
        single.decisions = [d for d in single.decisions if d.claim_id == claim.claim_id]
        dec = decisions[claim.claim_id]
        expected = dec['status'] in ('accepted', 'corroborated') and dec['machine_action'] == 'enrich'
        cases = [('declared_scope', claim.scope_level, dict(claim.scope_selector), None, expected)]
        if claim.scope_selector:
            different = dict(claim.scope_selector)
            different[next(iter(different))] = '__controlled_scope_mismatch__'
            cases.append(('selector_mismatch', claim.scope_level, different, None, False))
        if claim.scope_level != 'channel':
            cases.append(('no_descendant_propagation', 'channel', dict(claim.scope_selector), 'propagation', False))
        cases += [('boundary_only', claim.scope_level, dict(claim.scope_selector), 'boundary', False),
                  ('conflicting', claim.scope_level, dict(claim.scope_selector), 'conflict', False)]
        for case, level, selector, change, wanted in cases:
            current = deepcopy(single)
            if change == 'propagation': current.claims[0].propagation = 'none'
            if change == 'boundary': current.decisions[0].machine_action = 'boundary-only'
            if change == 'conflict': current.decisions[0].status = 'conflicting'
            error = None
            try:
                overlay = build_semantic_overlay(current, report_sha256=sha256_contract(current),
                    study_id=claim.study_id, target_scope_level=level, target_scope_selector=selector)
                admitted = bool(overlay.entries)
            except ValueError as exc:
                error = str(exc)
                admitted = False
                if 'no positive semantic claims' not in error:
                    wanted = None
            scope_rows.append(dict(claim_id=claim.claim_id, case=case, expected= wanted,
                admitted=admitted, error=error, passed=wanted is not None and admitted == wanted,
                target_scope=level, target_selector=selector,
                input_report=current.to_dict(), basis='controlled scope application, not extraction accuracy'))
    save(out / 'E2_scope_controls.json', scope_rows)

    t2 = []
    for pair in sorted((ROOT / 'paper/results/atlas_materialization/pairs').glob('*/comparability.assertion.json')):
        original = read(pair)
        records = ROOT / 'paper/results/atlas_materialization/records' / pair.parent.name
        left = HarmoRecordingBundle.from_dict(read(records / 'reference/recording.bundle.json'))
        right = HarmoRecordingBundle.from_dict(read(records / 'candidate/recording.bundle.json'))
        lt = TrustReport.from_dict(read(records / 'reference/trust.report.json'))
        rt = TrustReport.from_dict(read(records / 'candidate/trust.report.json'))
        for purpose in ('activation-timing', 'amplitude-pooling'):
            request = ComparabilityRequest(original['variable'], purpose, original['left_channel_id'], original['right_channel_id'])
            detail = evaluate(engine, baseline, (left, right, lt, rt, request))
            expected = 'conditionally_comparable' if purpose == 'activation-timing' else 'unassessed'
            t2.append(dict(pair=pair.parent.name, purpose=purpose, detail=detail,
                expected=expected, passed=detail['assertion']['status'] == expected,
                fixed_input_sha256=[sha256_contract(x) for x in (left, right, lt, rt)],
                inputs=[x.to_dict() for x in (left, right, lt, rt)],
                request=vars(request), bounded_source_note='100-point demonstration; not full-waveform inference',
                frozen_assertion=original, frozen_extra_conditions=sorted(set(original['conditions']) - set(engine.evaluate(left,right,lt,rt,ComparabilityRequest(original['variable'],'activation-timing',original['left_channel_id'],original['right_channel_id'])).conditions))))
    save(out / 'T2_purpose_comparison.json', t2)

    combinations = []
    policies = []
    reference_cases = read(control_reference / 'E2_state_combinations.json') if control_reference else []

    def saved_args(row):
        return (HarmoRecordingBundle.from_dict(row['inputs'][0]), HarmoRecordingBundle.from_dict(row['inputs'][1]),
                TrustReport.from_dict(row['inputs'][2]), TrustReport.from_dict(row['inputs'][3]), ComparabilityRequest(**row['request']))

    for mismatch, missing, outstanding in product((False, True), repeat=3):
        prior = next((r for r in reference_cases if (r['mismatch'],r['missing'],r['outstanding']) == (mismatch,missing,outstanding)), None)
        args = saved_args(prior) if prior is not None else controlled_pair(mismatch, missing, outstanding)
        detail = evaluate(engine, baseline, args)
        expected = 'not_comparable' if mismatch else 'unassessed' if missing else 'conditionally_comparable' if outstanding else 'comparable'
        kinds = {f['kind'] for g in detail['check_groups'] for f in g['findings']}
        correct_kinds = (('incompatibility' in kinds) == mismatch and ('unresolved' in kinds) == missing and ('condition' in kinds) == outstanding)
        combinations.append(dict(mismatch=mismatch, missing=missing, outstanding=outstanding,
            inputs=[x.to_dict() for x in args[:4]], request=vars(args[4]), detail=detail,
            expected=expected, passed=detail['assertion']['status'] == expected and correct_kinds,
            artificial_fixture=True))
    args = saved_args(next(r for r in combinations if not r['mismatch'] and not r['missing'] and r['outstanding']))
    assertion = engine.evaluate(*args)
    for permitted in (True, False):
        decision = ReleasePolicy(allow_conditional_comparability=permitted).decide(args[1], manifest_for(args[1]), args[3], assertion)
        policies.append(dict(allow_conditional=permitted, assertion=assertion.to_dict(), decision=decision.to_dict(), inputs=[x.to_dict() for x in args[:4]], request=vars(args[4]),
            passed=decision.status.value == ('conditional' if permitted else 'deny'), artificial_fixture=True))
    save(out / 'E2_state_combinations.json', combinations)
    save(out / 'E2_release_policies.json', policies)
    checks = [x['passed'] for x in scope_rows + t2 + combinations + policies]
    regression = [x['detail']['unchanged_from_pre_extension'] and x['detail']['input_unchanged'] for x in t2 + combinations]
    summary = dict(coverage=coverage['summary'], frozen_coverage_match=coverage_match,
        scope_controls=len(scope_rows), scope_controls_passed=sum(x['passed'] for x in scope_rows),
        real_pairs=len(t2)//2, purpose_evaluations=len(t2), purpose_counts=dict(Counter(x['purpose']+':'+x['detail']['assertion']['status'] for x in t2)),
        state_combinations=len(combinations), state_combinations_passed=sum(x['passed'] for x in combinations),
        policy_controls=len(policies), unchanged_assertions=sum(regression), regression_comparisons=len(regression),
        protected_inputs_unchanged=before == protected(), all_passed=all(checks+regression+[coverage_match, before == protected()]),
        claim_boundary='Retrospective functional and representational checks, not independent scientific accuracy.')
    save(out / 'core_summary.json', summary)
    print(json.dumps(summary, indent=2))


def timing(out, work, baseline_path):
    if (out / 'T3_summary.json').exists():
        raise FileExistsError('use a new output directory for a repeat')
    engine, baseline = engines(baseline_path)
    frozen = read(ROOT / 'paper/results/validation/comparability_resolution_study.json')
    rows, failures = [], []
    for case in frozen['comparisons']:
        muscle = 'tibialis-anterior' if case['muscle'] == 'tibialis anterior' else 'gastrocnemius-medialis'
        directory = work / 'repeat-1' / ('pair-%02d' % case['pair_index']) / muscle / 'resolved'
        try:
            left = HarmoRecordingBundle.from_dict(read(directory / 'left/recording.bundle.json'))
            right = HarmoRecordingBundle.from_dict(read(directory / 'right/recording.bundle.json'))
            lt = TrustReport.from_dict(read(directory / 'left/trust.report.json'))
            rt = TrustReport.from_dict(read(directory / 'right/trust.report.json'))
            comparison = read(directory / 'comparability.after.json')
            manifest = EvidenceManifest.from_dict(read(directory / 'evidence.manifest.json'))
            release_config = read(directory.parent / 'resolution.json')['release_policy']
            saved_state = deepcopy(right.processing_state)
            for condition in ('retained', 'withdrawn', 'restored'):
                current = deepcopy(right)
                current.processing_state = None if condition == 'withdrawn' else deepcopy(saved_state)
                current = HarmoRecordingBundle.from_dict(current.to_dict())
                for purpose in ('activation-timing', 'amplitude-pooling'):
                    request = ComparabilityRequest(comparison['variable'], purpose, comparison['left_channel_id'], comparison['right_channel_id'])
                    detail = evaluate(engine, baseline, (left, current, lt, rt, request))
                    assertion = ComparabilityAssertion.from_dict(detail['assertion'])
                    decision = ReleasePolicy.from_dict(release_config).decide(current, manifest, rt, assertion)
                    expected = ('unassessed' if purpose == 'amplitude-pooling' else 'conditionally_comparable' if condition == 'withdrawn' else 'comparable')
                    operated = current.to_dict()
                    original = right.to_dict()
                    operated.pop('processing_state')
                    original.pop('processing_state')
                    stable = operated == original
                    expected_release = 'quarantine' if purpose == 'amplitude-pooling' else 'deny' if condition == 'withdrawn' else 'allow'
                    rows.append(dict(pair_index=case['pair_index'], muscle=case['muscle'], condition=condition, purpose=purpose,
                        request=vars(request), inputs=[x.to_dict() for x in (left,current,lt,rt)], detail=detail, decision=decision.to_dict(),
                        expected=expected, expected_release=expected_release,
                        passed=assertion.status.value == expected and decision.status.value == expected_release and stable,
                        release_scope='Policy-level replay with fixed trust and historical manifest context; no export materialized for manipulated inputs.',
                        release_policy=release_config,
                        sample_and_time_reference_unchanged=stable, source_case=case,
                        operation='none' if condition == 'retained' else 'withdraw processing-state description' if condition == 'withdrawn' else 'restore exact processing-state description'))
        except Exception as exc:
            failures.append(dict(case=case, error=repr(exc)))
    save(out / 'T3_support_comparison.json', rows)
    save(out / 'T3_errors.json', failures)
    restored_equal = []
    for case in frozen['comparisons']:
        for purpose in ('activation-timing','amplitude-pooling'):
            matched = {r['condition']:r for r in rows if r['pair_index']==case['pair_index'] and r['muscle']==case['muscle'] and r['purpose']==purpose}
            restored_equal.append(len(matched)==3 and matched['retained']['detail']['assertion']==matched['restored']['detail']['assertion'])
    summary = dict(planned_pairs=20, evaluated_pairs=len(rows)//6, evaluations=len(rows), errors=failures,
        outcome_counts=dict(Counter(r['purpose']+':'+r['condition']+':'+r['detail']['assertion']['status'] for r in rows)),
        release_counts=dict(Counter(r['purpose']+':'+r['condition']+':'+r['decision']['status'] for r in rows)),
        exact_restoration_comparisons=len(restored_equal), exact_restorations=sum(restored_equal),
        unchanged_assertions=sum(r['detail']['unchanged_from_pre_extension'] for r in rows),
        all_passed=len(rows)==120 and not failures and all(r['passed'] and r['detail']['unchanged_from_pre_extension'] and r['detail']['input_unchanged'] for r in rows) and all(restored_equal),
        claim_boundary='Fixed-signal processing-description withdrawal, not isolated citation removal or independent physiology validation.')
    save(out / 'T3_summary.json', summary)
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=['core','timing','seal'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-policy', type=Path, required=True)
    parser.add_argument('--resolution-work', type=Path)
    parser.add_argument('--control-reference', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.stage == 'core': core(args.output, args.baseline_policy, args.control_reference)
    elif args.stage == 'timing': timing(args.output, args.resolution_work, args.baseline_policy)
    else:
        before = read(args.output / 'protected_inputs_before.json')
        save(args.output / 'integrity.json', dict(protected_inputs_unchanged=before==protected(), current=protected(),
            python=sys.version, platform=platform.platform(), git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            code_sha256={str(p.relative_to(ROOT)):digest(p) for p in [ROOT/'src/harmo/evidence/policy.py', ROOT/'src/harmo/evidence/models.py', ROOT/'src/harmo/evidence/semantic_claims.py', ROOT/'src/harmo/physiology_control.py', Path(__file__), ROOT/'scripts/paper/run_comparability_resolution_study.py', ROOT/'tests/test_comparability_details.py'] + sorted((ROOT/'paper/revision_plans').glob('additional_evaluation_protocol_v*.md'))},
            dependencies={name:importlib.metadata.version(name) for name in ('numpy','scipy','pandas','pytest','jsonschema','PyYAML')},
            git_worktree_status=subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True)))


if __name__ == '__main__':
    main()
