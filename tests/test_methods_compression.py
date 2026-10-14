"""Protect scientific detail and section scope in the Methods compression."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_methods_compression_preserves_other_sections_and_validation():
    old = (ROOT / 'paper/versions/harmo-semg_manuscript_v008_2026.09.12-r2.md').read_text()
    new = (ROOT / 'paper/paper.md').read_text()
    assert old.split('# Methods', 1)[0] == new.split('# Methods', 1)[0]
    assert old.split('# Results', 1)[1] == new.split('# Results', 1)[1]
    def validation(s):
        return s.split('## Validation of the framework', 1)[1].split('# Results', 1)[0]
    before = ('The documentary evidence evaluation concerned the execution of registered\n'
              'extraction rules, reconciliation with repository observations, and coverage of\n'
              'prespecified study--property pairs. It did not estimate unrestricted\n'
              'information-extraction accuracy on unseen publications. Likewise,\n'
              'purpose-specific comparisons tested the consequences of the declared evidence\n'
              "and decision rules, rather than automatic interpretation of an analyst's\n"
              'research question.')
    after = ('Documentary evaluation tested registered extraction rules, reconciliation with\n'
             'repository observations, and prespecified property coverage, not extraction\n'
             'accuracy on unseen publications. Purpose-specific comparisons tested the\n'
             'consequences of the declared evidence and decision rules.')
    assert validation(old).replace(before, after) == validation(new)


def test_processing_parameters_and_decision_precedence_are_unchanged():
    old = (ROOT / 'paper/versions/harmo-semg_manuscript_v008_2026.09.12-r2.md').read_text()
    new = (ROOT / 'paper/paper.md').read_text()
    for start, end in [
        ('1. Any explicit incompatibility:', '[Table 2.'),
        ('For activation timing, absent common event references', 'For example,'),
        ('For a cycle containing', 'The resolver linked'),
    ]:
        block = old.split(start, 1)[1].split(end, 1)[0]
        assert start + block in new
    methods = new.split('# Methods', 1)[1].split('# Results', 1)[0]
    for phrase in ('capture a typed value', 'assign a predefined value',
                   'count matches', 'collect distinct', 'No match',
                   'multiple distinct values for a single-value capture',
                   'disagreement with a registered expected value',
                   'Conflicting values took precedence',
                   'not inferred from free-text research questions'):
        assert phrase in methods
