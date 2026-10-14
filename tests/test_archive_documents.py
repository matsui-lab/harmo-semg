import hashlib
import json
import zipfile

import pytest

from harmo.archive_documents import read_documents

RAW=b'record,channel,side\nr1,A,left\n'


def create(tmp_path,members):
    path=tmp_path/'source.zip'
    with zipfile.ZipFile(path,'w') as archive:
        for name,raw in members: archive.writestr(name,raw)
    return path,hashlib.sha256(path.read_bytes()).hexdigest()


def test_verified_zip_metadata_preserves_both_hashes(tmp_path):
    path,digest=create(tmp_path,[('metadata/table.csv',RAW)])
    result=read_documents(path,digest)
    assert not result['held']
    doc=result['documents'][0]
    assert doc['acquisition']['archive_sha256']==digest
    assert doc['acquisition']['member_sha256']==hashlib.sha256(RAW).hexdigest()
    assert doc['result']['observations'][0]['record']=='r1'
    assert not (tmp_path/'metadata').exists()


def test_mismatch_rejected(tmp_path):
    path,_=create(tmp_path,[('metadata.csv',RAW)])
    with pytest.raises(ValueError,match='hash_mismatch'): read_documents(path,'0'*64)


@pytest.mark.parametrize('name',['../metadata.csv','/metadata.csv','a\\metadata.csv'])
def test_unsafe_member_not_read(tmp_path,name):
    path,digest=create(tmp_path,[(name,RAW)])
    result=read_documents(path,digest)
    assert not result['documents'] and result['held'][0]['reason']=='unsafe_archive_member'


def test_budgets_report_unprocessed_members(tmp_path):
    path,digest=create(tmp_path,[('a.csv',RAW),('b.csv',RAW)])
    result=read_documents(path,digest,max_documents=1)
    assert len(result['documents'])==1 and result['held'][0]['reason']=='document_count_budget'
    result=read_documents(path,digest,max_total_bytes=len(RAW))
    assert len(result['documents'])==1 and result['held'][0]['reason']=='document_size_budget'


def test_invalid_xml_does_not_hide_valid_metadata(tmp_path):
    path,digest=create(tmp_path,[('bad.xml',b'<broken>'),('good.csv',RAW)])
    result=read_documents(path,digest)
    assert len(result['held'])==1 and len(result['documents'])==1


def test_duplicate_member_identity_rejected(tmp_path):
    with pytest.warns(UserWarning): path,digest=create(tmp_path,[('a.csv',RAW),('a.csv',RAW)])
    with pytest.raises(ValueError,match='duplicate_archive'): read_documents(path,digest)


def test_automatic_collection_reads_verified_zip(tmp_path):
    from harmo.automatic_workflow import collect_saved_documents
    raw=tmp_path/'raw'; raw.mkdir()
    path,digest=create(raw,[('metadata.csv',RAW)])
    (tmp_path/'acquisition.json').write_text(json.dumps([
        dict(name=path.name,sha256=digest,verification=dict(pass_=True))]).replace('pass_','pass'))
    result=collect_saved_documents(dict(entries=[dict(source_report=str(tmp_path),dataset='example')]))
    assert len(result['documents'])==1 and not result['held']
    assert result['documents'][0]['dataset']=='example'
