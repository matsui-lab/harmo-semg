"""Bounded manuscript-alignment checks; no new scientific validation."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'paper/versions/harmo-semg_manuscript_v012_2026.09.13-r1.md'
NEW = ROOT / 'paper/versions/harmo-semg_manuscript_v013_2026.09.16-r1.md'


def sections(text, level):
    matches = list(re.finditer(r'^' + '#' * level + r' (.+)\n', text, re.M))
    return {m[1]: text[m.end():matches[i+1].start() if i+1 < len(matches) else len(text)].strip()
            for i, m in enumerate(matches)}


def test_frozen_results_preserved_as_blocks():
    before = OLD.read_text().split('# Results\n')[1].split('# Discussion\n')[0]
    after = NEW.read_text().split('# Results\n')[1].split('# Discussion\n')[0]
    for name, block in sections(before, 2).items():
        # Terminology edit only, not a change to the analyst's results.
        block = block.replace('source audit--330', 'source inventory--330')
        assert block in after, name


def test_comparison_rules_and_registered_validation_preserved():
    before, after = OLD.read_text(), NEW.read_text()
    start = '### Structured use requests and comparability rules\n'
    block = before.split(start)[1].split('### Conditional processing and reassessment')[0]
    assert block in after
    validation = before.split('## Validation of the framework\n')[1].split('## Statistical analysis')[0]
    for name, block in sections(validation, 3).items():
        assert block in after, name


def test_snapshot_and_figure_identity():
    assert (ROOT / 'paper/MANUSCRIPT_VERSION').read_text().strip() == 'v013_2026.09.16-r1'
    assert NEW.read_bytes() == (ROOT / 'paper/paper.md').read_bytes()
    for ext in ('tex', 'pdf'):
        assert NEW.with_suffix('.'+ext).read_bytes() == (ROOT / f'paper/cmpb/manuscript.{ext}').read_bytes()
    figure_pattern = r'!\[Figure \d+\.[\s\S]*?\]\(figures/[^)]+\)'
    assert re.findall(figure_pattern, OLD.read_text()) == re.findall(figure_pattern, NEW.read_text())


def test_abstract_and_evidence_boundaries():
    text = NEW.read_text()
    abstract = text.split('# Abstract')[1].split('# Introduction')[0]
    assert len(re.findall(r'\b[\w–-]+\b', re.sub(r'\*\*[^*]+:\*\*', '', abstract))) <= 250
    for phrase in ('four evidence\nlayers', 'not an independent sample of unseen studies',
                   'No human confirmations were supplied', '202-test suite',
                   'all\neight full-cycle peak comparisons therefore remained held',
                   'URL acquisition remained a separate command-line',
                   'not new independent physiological observations or a repeat of the second analyst',
                   '19/21-source and 57/67-claim', '20/21-source rerun'):
        assert ' '.join(phrase.split()) in ' '.join(text.split()), phrase


def test_additional_results_match_saved_machine_outputs():
    result = json.loads((ROOT / 'paper/results/segment_boundary_v003_2026.09.15-r3/validation.json').read_text())
    assert result['windows'] == 160 and result['records'] == 4
    expected = {'legacy': .02085, 'available_sample_RMS': .01757, 'real_context_0.5s': 1.19e-12}
    for key, rounded in expected.items():
        value = result['methods'][key]['full_cycle_shape_RMSE']['median']
        assert abs(value-rounded) / rounded < .005
    claims = json.loads((ROOT / 'paper/results/all_claims_v001_2026.09.14-r1/literature_revalidated_v002.json').read_text())['summary']
    assert claims['source_acquired'] == 21 and claims['extracted_claim_count'] == 67
    join = json.loads((ROOT / 'paper/results/document_field_join_v003_2026.09.15-r3/verification.json').read_text())
    assert join['all_CSV_samples_equal'] and join['mendeley_records'] == 2
    assert join['human_reviews'] == 0 and not join['scientific_approval']
    life = json.loads((ROOT / 'paper/results/local_browser_distribution_v001_2026.09.16-r1/lifecycle.json').read_text())
    assert life['status'] == 'passed' and life['records'] == 2 and life['derived_signals'] == 16
