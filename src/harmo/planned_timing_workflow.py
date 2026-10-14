"""One-command registered catalog planning, fresh validation, and gated export."""
import json
from pathlib import Path
from harmo.config.automap import save, require
from harmo.timing_workflow import checked_profile
from harmo.timing_planner import sha


def run(catalog, source_cache, protocol, article, output):
    from harmo.registered_planner import run as plan_cycles
    from harmo.physiology_control import extract_study38_rows, extract_study120_rows, evaluate_physiology_control
    from harmo.registered_timing import run as compare
    from harmo.timing_export import export_pair
    from harmo.evidence.run import current_repository_state
    catalog, source_cache, protocol, article, output = (
        Path(p).resolve() for p in (catalog, source_cache, protocol, article, output))
    output.mkdir(parents=True, exist_ok=False)
    stage='input-validation'
    def checkpoint(status, **details):
        temporary=output/'status.pending.json'
        temporary.write_text(json.dumps(dict(status=status,stage=stage,**details),indent=2))
        temporary.replace(output/'status.json')
    try:
        checkpoint('running')
        profile=checked_profile(protocol)
        inputs=dict(catalog=str(catalog),source_cache=str(source_cache),protocol=str(protocol),
                    article=str(article),protocol_sha256=sha(protocol),article_sha256=sha(article),
                    catalog_sha256=sha(catalog),implementation=current_repository_state())
        save(output/'inputs.json',inputs)
        stage='cycle-planning';checkpoint('running')
        planned=plan_cycles(catalog,source_cache,article,protocol,output/'planner')
        require(planned['all_exact'] and planned['channel_plans']==100,'registered_cycle_plan_failed')
        stage='physiology-validation';checkpoint('running')
        rows38, sources38=extract_study38_rows(source_cache,profile)
        rows120, sources120=extract_study120_rows(source_cache/'Gait120_011_to_020.zip',profile)
        physiology=evaluate_physiology_control(rows38+rows120,profile,sources38+sources120)
        save(output/'physiology.result.json',physiology)
        require(physiology['positive_control_pass'],'fresh_physiological_control_failed')
        stage='comparison';checkpoint('running')
        result=compare(source_cache,source_cache/'Gait120_011_to_020.zip',output/'resolution',protocol,
                       output/'physiology.result.json',output/'resolution.result.json',planner_replay=output/'planner')
        require(result['all_timing_pairs_resolved_comparable'] and
                result['recording_identities_and_decisions_reproducible'],'registered_comparison_failed')
        stage='gated-export';checkpoint('running')
        selections=[]
        for index in sorted((output/'resolution/repeat-1').glob('*/*/resolved/comparability.resolution.index.json')):
            resolved=index.parent
            releases={role:json.loads((resolved/role/'release.decision.json').read_text()) for role in ('left','right')}
            allowed=all(r['status']=='allow' for r in releases.values())
            signal=export_pair(resolved,resolved/'selected-signals') if allowed else None
            selections.append(dict(comparison=str(resolved.relative_to(output)),status='released' if allowed else 'held',
                                   releases=releases,signals=signal))
        require(len(selections)==20,'comparison_denominator_changed')
        save(output/'selection.json',selections)
        released=sum(s['status']=='released' for s in selections)
        summary=dict(status='completed' if released==20 else 'completed-with-release-holds',
                     profile='study38-gait120',selected_cycles=50,channel_plans=100,subject_curves=20,
                     comparisons=20,released=released,held=20-released,fresh_physiology_pass=True,
                     reproducible=True,amplitude_comparability='not-established',
                     scope='Registered two-study timing workflow only; local exports, no external publication.',
                     portability='References original catalog and source paths; not a self-contained relocatable archive.')
        save(output/'summary.json',summary)
        stage='complete';checkpoint(summary['status'])
        inventory=[dict(path=str(p.relative_to(output)),sha256=sha(p),bytes=p.stat().st_size)
                   for p in sorted(output.rglob('*')) if p.is_file() and p.name!='inventory.json']
        save(output/'inventory.json',dict(files=inventory,scope='Output files only; original input paths retained in inputs and execution evidence.'))
        return summary
    except Exception as exc:
        checkpoint('failed',error=str(exc),error_type=type(exc).__name__)
        raise
