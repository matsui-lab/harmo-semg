"""Guard the bounded XML source revision, not independent semantic accuracy."""
import copy
import json
from pathlib import Path
import yaml
from harmo.evidence.semantic_claims import _acquire_source

ROOT=Path(__file__).resolve().parents[1]
RESULTS=ROOT/'paper/results/all_claims_v001_2026.09.14-r1'


def test_revision_changes_only_two_reviewed_source_hashes():
    old=yaml.safe_load((ROOT/'paper/literature_semantic_enrichment.yaml').read_text())
    new=yaml.safe_load((ROOT/'paper/literature_semantic_enrichment_v002_2026.09.14-r1.yaml').read_text())
    restored=copy.deepcopy(new)
    changed=[]
    for a,b in zip(old['sources'],restored['sources']):
        if a.get('expected_sha256')!=b.get('expected_sha256'):
            changed.append(a['source_id'])
            b['expected_sha256']=a['expected_sha256']
    assert changed==['study31-article-jats','study1-article-jats']
    assert restored==old


def test_ten_claim_values_evidence_and_scope_match_frozen_result():
    report=json.loads((RESULTS/'xml_revalidation_v001.json').read_text())
    assert len(report['sources'])==2
    claims=[c for s in report['sources'] for c in s['claims']]
    assert len(claims)==10
    assert all(c['differences']=={} and c['section'] for c in claims)


def test_further_xml_change_is_still_held(tmp_path):
    plan=yaml.safe_load((ROOT/'paper/literature_semantic_enrichment_v002_2026.09.14-r1.yaml').read_text())
    for source in plan['sources']:
        if source['source_id'] not in ('study31-article-jats','study1-article-jats'):continue
        name=source['source_id']+'.xml'
        raw=(RESULTS/'literature-cache'/name).read_bytes()
        (tmp_path/name).write_bytes(raw)
        assert _acquire_source(source,ROOT,tmp_path,True,False).artifact.acquisition_status=='acquired'
        (tmp_path/name).write_bytes(raw+b'\n')
        artifact=_acquire_source(source,ROOT,tmp_path,True,False).artifact
        assert artifact.acquisition_status=='failed'
        assert 'digest differs' in artifact.error
