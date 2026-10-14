"""Resolve explicitly linked article DOIs through bounded Europe PMC requests."""
import hashlib
import re
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from harmo.mixed_batch import NoRedirect, metadata


def related_dois(document):
    results=[]
    for index,row in enumerate(document.get('related_materials') or []):
        if not isinstance(row,dict): continue
        if str(row.get('identifier_type','')).lower()!='doi': continue
        # A dataset supplied as supplementary material may identify its paper
        # this way. Resolve article identity, never inherit its record scope.
        if str(row.get('relation','')).lower() not in ('isdocumentedby','isdescribedby','issupplementto'):
            continue
        doi=re.sub(r'^https?://(?:dx\.)?doi.org/','',str(row.get('identifier','')),flags=re.I)
        if not re.fullmatch(r'10\.\d{4,9}/[^\s]+',doi): continue
        results.append(dict(doi=doi,relation=row['relation'],pointer='/related_materials/'+str(index)))
    return results


def retrieve(doi, destination, fetch_json=metadata):
    from pathlib import Path
    if not re.fullmatch(r'10\.\d{4,9}/[A-Za-z0-9._;()/:-]+',doi):
        raise ValueError('unsupported_DOI_syntax')
    api='https://www.ebi.ac.uk/europepmc/webservices/rest/search?'+urlencode(dict(query='DOI:"'+doi+'"',format='json',resultType='core',pageSize=25))
    response=fetch_json(dict(api=api))
    results=response.get('resultList',{}).get('result',[])
    matching=[r for r in results if str(r.get('doi','')).lower()==doi.lower()]
    ids={r.get('pmcid') for r in matching if r.get('pmcid')}
    if len(ids)!=1: raise ValueError('no_unique_open_full_text_match')
    pmcid=ids.pop()
    if not re.fullmatch(r'PMC[1-9][0-9]*',pmcid): raise ValueError('invalid_full_text_identifier')
    url='https://www.ebi.ac.uk/europepmc/webservices/rest/'+pmcid+'/fullTextXML'
    with build_opener(NoRedirect()).open(Request(url,headers={'User-Agent':'harmo-document-discovery/0.1'}),timeout=60) as stream:
        raw=stream.read(2*1024**2+1)
    if len(raw)>2*1024**2: raise ValueError('full_text_size_budget')
    from lxml import etree
    root=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True))
    dtd=root.getroottree().docinfo.internalDTD
    if dtd is not None and list(dtd.iterentities()): raise ValueError('entity_definitions_not_supported')
    # Article identity must be present in the fetched document, not just search metadata.
    declared=root.xpath('//*[local-name()="article-id" and @pub-id-type="doi"]/text()')
    if doi.lower() not in {s.strip().lower() for s in declared}: raise ValueError('full_text_DOI_mismatch')
    destination=Path(destination)
    with destination.open('xb') as target: target.write(raw)
    return dict(doi=doi,pmcid=pmcid,url=url,path=str(destination),sha256=hashlib.sha256(raw).hexdigest(),
        status='identity_verified_document',scope='Repository-linked article identity; not evidence of applicability to every record.')
