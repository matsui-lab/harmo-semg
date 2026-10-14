"""Paired candidate agreement against actually confirmed semantic review items.

This measures reviewed-subset agreement, not independent biological truth or
time saved. Unreviewed items never become ground truth.
"""
import argparse
import json
from pathlib import Path

from harmo.semantic_review import checked_packet, reviewed_documents
from harmo.walking_plan import read, save, sha


def evaluate(packet_path, decisions_path, candidates_path=None):
    packet = checked_packet(packet_path)
    _, review = reviewed_documents(packet_path, decisions_path)
    decisions = read(decisions_path)
    references = {d['target_id']: d['value'] for d in decisions['decisions'] if d['action'] == 'confirm'}
    rules = {r['target_id']: r['values'][0] for c in packet['cases'] for r in c.get('rules', [])
             if r['status'] == 'proposed' and len(r['values']) == 1}
    targets = {t['target_id'] for c in packet['cases'] for t in c['targets']}
    canon = lambda x: ' '.join(x.casefold().split())
    def score(predictions, subset):
        proposed = [t for t in subset if t in predictions]
        correct = sum(canon(predictions[t]) == canon(references[t]) for t in proposed)
        return dict(reviewed_targets=len(subset), proposed=len(proposed), abstained=len(subset)-len(proposed),
                    agreed=correct, disagreed=len(proposed)-correct,
                    agreement_among_proposals=correct/len(proposed) if proposed else None,
                    confirmed_value_coverage=correct/len(subset) if subset else None)
    output = dict(version='semantic-review-metrics/1', packet_id=packet['packet_id'],
        confirmed_reference_items=len(references), unconfirmed_items=len(targets)-len(references),
        rules_reviewed_subset=score(rules, references), review=review,
        inputs={str(Path(packet_path).resolve()):sha(packet_path), str(Path(decisions_path).resolve()):sha(decisions_path)},
        independent_ground_truth=False, time_saving_measured=False,
        interpretation='Agreement conditional on the human-confirmed subset; no unreviewed item is scored as correct or incorrect.')
    if candidates_path:
        from harmo.llm_candidates import validate_candidates
        contexts = {t['target_id']: (t, c['sources']) for c in packet['cases'] for t in c['targets']}
        data = read(candidates_path)
        if data['packet_id'] != packet['packet_id']:
            raise ValueError('candidate_packet_mismatch')
        predictions, attempted = {}, set()
        for row in data['rows']:
            target = row['target_id']
            if target not in targets or target in attempted:
                raise ValueError('unknown_or_duplicate_candidate_target')
            attempted.add(target)
            for entry in row.get('validation', {}).get('entries', []):
                c = entry['candidate']
                if c.get('target_id') != target:
                    raise ValueError('candidate_target_mismatch')
                target_spec, sources = contexts[target]
                checked = validate_candidates(dict(candidates=[c]), [target_spec], sources)
                if checked['structurally_complete'] and c['status'] == 'proposed':
                    if target in predictions or not isinstance(c['value'], str):
                        raise ValueError('duplicate_or_invalid_candidate_value')
                    predictions[target] = c['value']
        shared = sorted(attempted & references.keys())
        output['paired_attempted_and_confirmed'] = dict(targets=shared, rules=score(rules, shared), local_llm=score(predictions, shared))
        output['LLM_attempted_targets'] = len(attempted)
        output['LLM_unattempted_targets'] = len(targets)-len(attempted)
        output['inputs'][str(Path(candidates_path).resolve())] = sha(candidates_path)
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--packet', required=True); p.add_argument('--decisions', required=True)
    p.add_argument('--candidates'); p.add_argument('--output', required=True)
    a = p.parse_args(); result = evaluate(a.packet, a.decisions, a.candidates)
    save(a.output, result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
