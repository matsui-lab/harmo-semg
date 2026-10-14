"""Bounded checks for the v012 implementation-validation revision."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_scientific_rules_unchanged_from_pre_revision():
    before = (ROOT/'paper/versions/harmo-semg_manuscript_v011_2026.09.12-r5.md').read_text()
    after = (ROOT/'paper/paper.md').read_text()
    for start,end in [('### Structured use requests and comparability rules', '### Conditional processing and reassessment'),
                      ('### Conditional processing and reassessment','### Release decisions and implementation scope')]:
        assert before.split(start,1)[1].split(end,1)[0] == after.split(start,1)[1].split(end,1)[0]

def test_current_snapshot_and_claim_boundaries():
    current = (ROOT/'paper/paper.md').read_bytes()
    assert current == (ROOT/'paper/versions/harmo-semg_manuscript_v012_2026.09.13-r1.md').read_bytes()
    text = current.decode()
    assert '195 tests' in text and '170 unresolved mapping proposals' in text
    assert 'live\nZenodo acquisition was therefore not established' in text
    assert 'not demonstrate automatic inference of the\n78 mappings' in text

def test_saved_final_validation_agrees():
    root = ROOT/'paper/results/submission_validation_v001_2026.09.13-r1'
    result = json.loads((root/'final_consistency.json').read_text())
    assert result['passed'] and all(result['assertions'].values())
    assert result['test_count'] == 195
    live = json.loads((root/'live_resume_comparison.json').read_text())
    assert live['second']['downloaded_payloads'] == 0
    assert len(live['second']['ingestion']['held']) == 170
