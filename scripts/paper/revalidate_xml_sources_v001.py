"""Diagnostic re-extraction of changed XML; never modifies the registered plan."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import yaml
from harmo.evidence.semantic_claims import _acquire_source, _match_claim, _document_sections


def inspect(root, cache):
    plan=yaml.safe_load((root/'paper/literature_semantic_enrichment.yaml').read_text())
    frozen=json.loads((root/'paper/results/validation/literature_semantic_enrichment.json').read_text())
    old={c['claim_id']:c for c in frozen['claims']}
    results=[]
    for declaration in plan['sources']:
        if declaration['source_id'] not in ('study31-article-jats','study1-article-jats'):
            continue
        path=cache/(declaration['source_id']+'.xml')
        raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
        revised=dict(declaration,expected_sha256=digest)
        # Only a diagnostic in-memory declaration; no adoption or gate override.
        source=_acquire_source(revised,root,cache,True,False)
        sections=_document_sections(raw,'jats-xml')
        xml=ET.fromstring(raw)
        claims=[]
        fields=('value','scope_level','scope_selector','propagation','property_path',
                'machine_action','extraction_status','match_count','evidence_sha256')
        for rule in declaration['claims']:
            result=_match_claim(source,rule).to_dict()
            differences={f:dict(old=old[rule['claim_id']].get(f),new=result.get(f))
                         for f in fields if old[rule['claim_id']].get(f)!=result.get(f)}
            claims.append(dict(claim=result,differences=differences,
                section=sections.get(rule.get('locator','document').casefold())))
        results.append(dict(source_id=declaration['source_id'],old_sha256=declaration['expected_sha256'],
            new_sha256=digest,article_ids=[dict(type=e.get('pub-id-type'),value=e.text) for e in xml.findall('.//article-meta/article-id')],
            claims=claims))
    return dict(mode='diagnostic-only',sources=results,
        all_claim_fingerprints_equal=all(not c['differences'] for s in results for c in s['claims']),
        limitation='Original XML not supplied: matching evidence substrings does not prove entire-document or contextual equivalence.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for name in ('root','cache','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    result=inspect(Path(args.root),Path(args.cache))
    with Path(args.output).open('x') as out:json.dump(result,out,indent=2,ensure_ascii=False)
    print(json.dumps(dict(equal=result['all_claim_fingerprints_equal'],
        claims=sum(len(s['claims']) for s in result['sources']))))
