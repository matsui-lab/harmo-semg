"""Bounded extraction of documentary observations from verified ZIP assets.

Members are read in memory, never written to paths supplied by the archive.
Nested archives and ambiguous member identities are not interpreted.
"""
from pathlib import PurePosixPath
import hashlib
import stat
import zipfile
import csv
from urllib.parse import quote

from harmo.document_evidence import extract, HEADERS

KINDS={'.html':'html','.htm':'html','.xml':'xml','.json':'json','.csv':'csv','.tsv':'tsv'}


def read_documents(path, expected_sha256, *, max_documents=100, max_bytes=2*1024**2,
                   max_total_bytes=16*1024**2):
    for limit in (max_documents,max_bytes,max_total_bytes):
        if type(limit) is not int or limit<1: raise ValueError('positive_document_budget_required')
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024**2),b''): digest.update(chunk)
        if digest.hexdigest()!=expected_sha256: raise ValueError('document_acquisition_hash_mismatch')
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            members=archive.infolist()
            if len(members)>10000: raise ValueError('archive_member_count_budget')
            names=[m.filename for m in members]
            if len(names)!=len(set(names)): raise ValueError('duplicate_archive_member_names')
            documents,held=[],[]
            consumed=0
            selected=[m for m in members if not m.is_dir() and PurePosixPath(m.filename).suffix.lower() in KINDS]
            for index,member in enumerate(selected):
                identity=dict(archive_sha256=expected_sha256,member=member.filename)
                if index>=max_documents:
                    held.append(dict(**identity,reason='document_count_budget')); continue
                relative=PurePosixPath(member.filename)
                mode=(member.external_attr>>16)&0xffff
                if relative.is_absolute() or '..' in relative.parts or '\\' in member.filename or stat.S_ISLNK(mode):
                    held.append(dict(**identity,reason='unsafe_archive_member')); continue
                if member.flag_bits&1:
                    held.append(dict(**identity,reason='encrypted_archive_member')); continue
                if member.file_size>max_bytes or consumed+member.file_size>max_total_bytes:
                    held.append(dict(**identity,reason='document_size_budget')); continue
                # Reserve even failed attempts so malformed inputs cannot reset the budget.
                consumed+=member.file_size
                try:
                    with archive.open(member) as stream:
                        raw=stream.read(max_bytes+1)
                    if len(raw)>max_bytes or len(raw)!=member.file_size:
                        raise ValueError('archive_member_size_mismatch')
                    if relative.suffix.lower() in ('.csv','.tsv'):
                        first = raw.split(b'\n',1)[0].decode('utf-8-sig')
                        header = next(csv.reader([first],delimiter='\t' if relative.suffix.lower()=='.tsv' else ','),[])
                        if not any(HEADERS.get(h.strip().lower().replace('_',' '))=='record' for h in header):
                            continue  # Numeric waveform tables are not metadata.
                    uri=path.resolve().as_uri()+'#zip-member='+quote(member.filename,safe='')
                    result=extract(raw,uri,KINDS[relative.suffix.lower()],max_bytes=max_bytes)
                    documents.append(dict(acquisition=dict(**identity,member_sha256=hashlib.sha256(raw).hexdigest()),result=result))
                except Exception as exc:
                    held.append(dict(**identity,reason=str(exc)))
    return dict(documents=documents,held=held,reserved_bytes=consumed,
        scope='Direct supported document members of one verified ZIP; no nested archives or PDF interpretation.')
