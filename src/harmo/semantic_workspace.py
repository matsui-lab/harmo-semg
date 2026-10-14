"""Bounded semantic investigation and a local, one-target-at-a-time review UI.

This is a controller over frozen packet sources, not an autonomous web agent.
LLM proposals never become human decisions or scientific authorization.
"""
import argparse
import json
import re
from html import unescape
from pathlib import Path

from harmo.llm_candidates import validate_candidates
from harmo.semantic_review import checked_packet, propose, reviewed_documents, save

FIELDS = ('anatomy', 'measurement_side', 'task')


def evidence_preview(source, target, values):
    """Display excerpt only: not proof of applicability or entailment."""
    raw = source['text']
    if source.get('kind') == 'derived-record-schema' and target['field'] == 'anatomy':
        try:
            metadata = json.loads(raw)
            channels = metadata.get('channels', [])
            position = channels.index(target.get('channel'))
            return dict(text=f'channels[{position}]: ' + json.dumps(channels[position], ensure_ascii=False),
                        matched_term=channels[position], note='Exact metadata field; anatomy not independently verified.')
        except (ValueError, AttributeError):
            pass
    plain = unescape(re.sub(r'<[^>]+>', ' ', raw)) if raw.lstrip().startswith('<') else raw
    plain = re.sub(r'[ \t]+', ' ', plain)
    terms = {'anatomy': [target.get('channel', '')] + list(values),
             'measurement_side': ['EmgSide', 'EMG side', 'electrode'] + list(values),
             'task': ['"Task"', 'task'] + list(values)}[target['field']]
    match = next((m for term in terms if term for m in [re.search(re.escape(term), plain, re.I)] if m), None)
    start = max(0, match.start()-180) if match else 0
    end = min(len(plain), start+1000)
    return dict(text=('…' if start else '')+plain[start:end]+('…' if end < len(plain) else ''),
                matched_term=match.group() if match else None,
                note='Display excerpt only; applicability is not verified.')


def candidate(row, target, sources):
    entries = row.get('validation', {}).get('entries', [])
    if len(entries) != 1:
        return None
    value = entries[0].get('candidate')
    result = validate_candidates(dict(candidates=[value]), [target], sources)
    return value if result['structurally_complete'] else None


def source_order(target, sources):
    words = set(re.findall(r'[a-z0-9]+', (target['channel']+' '+target['record']).lower()))
    terms = dict(anatomy=('muscle', 'emgvarname', 'anatom'),
                 measurement_side=('emgside', 'side', 'electrode'),
                 task=('task', 'walking', 'trial'))[target['field']]
    def score(source):
        text = source['text'].lower()
        return (4 * sum(term in text for term in terms)
                + len(words & set(re.findall(r'[a-z0-9]+', text))))
    return sorted(sources, key=lambda source: (-score(source), source['source_id']))


def source_subset(ordered, count, char_budget=85000):
    chosen = []
    size = 0
    for source in ordered:
        length = len(json.dumps(source, ensure_ascii=True))
        if size + length <= char_budget:
            chosen.append(source['source_id'])
            size += length
        if len(chosen) == count:
            break
    return chosen


def prepare(packet_path, output, model=None, port=11439, max_targets=8,
            max_calls=8, decisions_path=None, transport=None):
    if type(max_targets) is not int or not 1 <= max_targets <= 256:
        raise ValueError('target_budget_required')
    if type(max_calls) is not int or not 0 <= max_calls <= 512:
        raise ValueError('call_budget_required')
    packet = checked_packet(packet_path)
    decisions = dict(packet_id=packet['packet_id'], reviewer='', active_seconds=None,
                     decisions=[dict(target_id=t['target_id'], action='unreviewed', value=None,
                                     evidence_ids=[], scope_reason='')
                                for c in packet['cases'] for t in c['targets']])
    if decisions_path:
        reviewed_documents(packet_path, decisions_path)
        supplied = json.loads(Path(decisions_path).read_text())
        updates = {d['target_id']: d for d in supplied['decisions']}
        decisions.update(reviewer=supplied['reviewer'], active_seconds=supplied.get('active_seconds'))
        if supplied.get('review_actor'):
            decisions['review_actor'] = supplied['review_actor']
        decisions['decisions'] = [updates.get(d['target_id'], d) for d in decisions['decisions']]
    final_decisions = {d['target_id']: d for d in decisions['decisions']}
    pairs = [(c, t) for c in packet['cases'] for t in c['targets'] if t['field'] in FIELDS]
    # Review holds/rejections must never be silently re-investigated or reversed.
    pending = [(c, t) for c, t in pairs if final_decisions[t['target_id']]['action'] == 'unreviewed']
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    items, rows, calls = [], {}, 0
    for case, target in pending[:max_targets]:
        tid = target['target_id']
        rule = next((r for r in case.get('rules', []) if r['target_id'] == tid),
                    dict(target_id=tid, values=[], status='insufficient'))
        item = dict(target=target, dataset=case['dataset'], rule=rule, sources=case['sources'],
                    investigation=[], status='needs_human_review', llm_candidate=None)
        ordered = source_order(target, case['sources'])
        item['sources'] = ordered
        previous_ids = None
        for name, limit in [('focused', 3), ('expanded', 12)]:
            if not model:
                item['stop_reason'] = 'model_not_requested'
                break
            if calls >= max_calls:
                item['stop_reason'] = 'call_budget_exhausted'
                break
            ids = source_subset(ordered, limit)
            if not ids or ids == previous_ids:
                item['stop_reason'] = 'no_additional_bounded_sources'
                break
            previous_ids = ids
            calls += 1
            attempt = dict(stage=name, source_ids=ids, source_selection='lexical-ranking/1')
            folder = out / ('attempt-%03d' % calls)
            try:
                result = propose(packet_path, folder, model, port, 1, transport,
                                 target_ids=[tid], source_ids_by_target={tid: ids})
                row = result['rows'][0]
                rows[tid] = row
                checked = candidate(row, target, case['sources'])
                attempt.update(status=row['status'], candidate=checked, output=str(folder))
                if checked and checked['status'] == 'proposed':
                    item['llm_candidate'] = checked
                    if rule.get('values') and checked['value'] not in rule['values']:
                        item['status'] = 'rule_llm_conflict'
                    item['stop_reason'] = 'candidate_requires_human_scope_review'
                    item['investigation'].append(attempt)
                    break
                attempt['error'] = row.get('error')
                item['stop_reason'] = 'bounded_sources_did_not_resolve_target'
            except Exception as exc:
                # Keep the rule path and UI usable if the explicitly selected
                # local runtime is unavailable. Do not fall back to a cloud API.
                attempt.update(status='failed', error=str(exc))
                rows[tid] = dict(target_id=tid, status='failed', error=str(exc))
                item['stop_reason'] = 'local_model_failed'
            item['investigation'].append(attempt)
            if attempt['status'] == 'failed':
                break
        values = rule.get('values', [])
        if item['llm_candidate']:
            values = values + [item['llm_candidate']['value']]
        item['sources'] = [dict(s, preview=evidence_preview(s, target, values)) for s in ordered]
        # Presentation preference only; no change to investigation or approval.
        item['sources'].sort(key=lambda s: (not bool(s['preview']['matched_term']),
                                           len(s['text']) > 4000))
        items.append(item)
    from harmo.semantic_context import build as build_context
    context_inputs = build_context(packet, items)
    from harmo.review_routing import question
    for item in items:
        item['guidance'] = question(item)
    checked_packet(packet_path)
    report = dict(version='semantic-workspace/1', packet_id=packet['packet_id'],
                  packet_path=str(Path(packet_path).resolve()), items=items, decisions=decisions,
                  context_inputs=context_inputs,
                  review_routing=dict(version='review-routing/1',
                      counts={key: sum(i['guidance']['category'] == key for i in items)
                              for key in sorted({i['guidance']['category'] for i in items})},
                      automatic_resolutions=0),
                  selected_fields=list(FIELDS), pending_items=len(pending),
                  displayed_items=len(items), omitted_pending_items=len(pending)-len(items),
                  retained_human_decisions=sum(d['action'] != 'unreviewed' for d in decisions['decisions']),
                  call_attempts=calls, model=model, automatic_approvals=0,
                  scientific_approval=False, accuracy_evaluated=False,
                  scope='Frozen-source bounded controller; no external discovery, execution generation, or semantic entailment proof.')
    save(out / 'workspace.json', report)
    save(out / 'candidates.json', dict(packet_id=packet['packet_id'], rows=list(rows.values()),
                                     automatic_approvals=0, accuracy_evaluated=False))
    template = Path(__file__).with_name('semantic_workspace.html').read_text()
    payload = json.dumps(report, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    with (out / 'review.html').open('x') as f:
        f.write(template.replace('/*WORKSPACE_DATA*/null', payload))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model')
    parser.add_argument('--port', type=int, default=11439)
    parser.add_argument('--max-targets', type=int, default=8)
    parser.add_argument('--max-calls', type=int, default=8)
    parser.add_argument('--decisions')
    args = parser.parse_args()
    result = prepare(args.packet, args.output, args.model, args.port, args.max_targets,
                     args.max_calls, args.decisions)
    print(json.dumps({k: v for k, v in result.items() if k not in ('items', 'decisions')}, indent=2))


if __name__ == '__main__':
    main()
