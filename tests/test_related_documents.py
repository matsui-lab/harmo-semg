import io
import pytest
from harmo.related_documents import related_dois,retrieve


def test_only_explicit_documentation_links():
    rows=[dict(identifier='10.1234/example',identifier_type='DOI',relation=r) for r in ('IsDocumentedBy','Cites','IsSupplementTo')]
    result=related_dois(dict(related_materials=rows))
    assert len(result)==2
    assert [r['pointer'] for r in result]==['/related_materials/0','/related_materials/2']
    assert all(r['relation']!='Cites' for r in result)


def test_full_text_identity_checked(tmp_path,monkeypatch):
    xml=b'<article><front><article-id pub-id-type="doi">10.1234/example</article-id></front></article>'
    class Opener:
        def open(self,*args,**kwargs): return io.BytesIO(xml)
    monkeypatch.setattr('harmo.related_documents.build_opener',lambda *a:Opener())
    fetch=lambda spec:dict(resultList=dict(result=[dict(doi='10.1234/example',pmcid='PMC123')]))
    result=retrieve('10.1234/example',tmp_path/'article.xml',fetch_json=fetch)
    assert result['status']=='identity_verified_document'
    xml=b'<article><article-id pub-id-type="doi">10.1234/other</article-id></article>'
    with pytest.raises(ValueError,match='DOI_mismatch'):
        retrieve('10.1234/example',tmp_path/'wrong.xml',fetch_json=fetch)
    assert not (tmp_path/'wrong.xml').exists()


def test_ambiguous_search_not_adopted(tmp_path):
    fetch=lambda spec:dict(resultList=dict(result=[dict(doi='10.1234/example',pmcid=p) for p in ('PMC1','PMC2')]))
    with pytest.raises(ValueError,match='unique'):
        retrieve('10.1234/example',tmp_path/'article.xml',fetch_json=fetch)
