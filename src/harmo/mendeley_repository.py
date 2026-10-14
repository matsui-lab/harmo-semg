"""Bounded read-only adapter for Mendeley Data's public snapshot API.

No OAuth, page-script execution, authentication bypass, or upload operations.
The public website uses these endpoints; response schemas are checked strictly.
"""
import json
import re
from pathlib import PurePosixPath
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Mendeley_metadata_redirect_requires_review')


def fetch_json(url):
    if not url.startswith('https://data.mendeley.com/public-api/datasets/'):
        raise ValueError('Mendeley_public_API_required')
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, headers={'User-Agent':'harmo-public-reuse/1','Accept':'application/json'}), timeout=45) as response:
        raw = response.read(2*1024**2+1)
    if len(raw) > 2*1024**2: raise ValueError('Mendeley_metadata_size_budget')
    return json.loads(raw)


def metadata(spec, fetch=fetch_json):
    identity, version = spec['id'], spec['version']
    if not re.fullmatch('[a-z0-9]{10}', identity) or type(version) is not int or version < 1:
        raise ValueError('Mendeley_pinned_identity_required')
    base = 'https://data.mendeley.com/public-api/datasets/'+identity
    snapshot = fetch(base+'/snapshot/'+str(version))
    if snapshot.get('id') != identity or snapshot.get('version') != version or snapshot.get('doi') != '10.17632/'+identity+'.'+str(version):
        raise ValueError('Mendeley_snapshot_identity_mismatch')
    if snapshot.get('is_confidential') is not False:
        raise ValueError('Mendeley_public_snapshot_required')
    folders = fetch(base+'/folders/'+str(version))
    if not isinstance(folders, list) or len(folders) > 100:
        raise ValueError('Mendeley_folder_inventory_budget_or_schema')
    identifiers = ['root']
    for folder in folders:
        fid = folder.get('id')
        if not isinstance(fid, str) or not re.fullmatch(UUID, fid) or fid in identifiers:
            raise ValueError('Mendeley_unique_folder_id_required')
        identifiers.append(fid)
    files, pages = [], []
    for fid in identifiers:
        url = base+'/files?folder_id='+fid+'&version='+str(version)
        rows = fetch(url)
        if not isinstance(rows, list): raise ValueError('Mendeley_file_list_schema_changed')
        pages.append(dict(url=url, files=rows))
        files.extend(dict(row, source_folder_id=fid) for row in rows)
        if len(files) > 1000: raise ValueError('Mendeley_file_inventory_budget')
    # Keep the original snapshot and full response data, not only selected names.
    return dict(snapshot, files=files, repository_folders=folders, file_pages=pages,
                acquisition_api='Mendeley-public-website-snapshot/1')


def file_assets(spec, document):
    if document.get('id') != spec['id'] or document.get('version') != spec['version']:
        raise ValueError('Mendeley_metadata_identity_mismatch')
    files = document.get('files')
    if not isinstance(files, list) or not 0 < len(files) <= 1000:
        raise ValueError('Mendeley_bounded_files_required')
    result, seen = [], set()
    for row in files:
        fid, name, detail = row.get('id'), row.get('filename'), row.get('content_details', {})
        if not isinstance(fid, str) or not re.fullmatch(UUID, fid) or fid in seen:
            raise ValueError('Mendeley_unique_file_id_required')
        seen.add(fid)
        if not isinstance(name, str) or name in ('', '.', '..') or PurePosixPath(name).name != name or any(c in name for c in '\\:\x00') or any(ord(c)<32 for c in name) or name != name.rstrip('. '):
            raise ValueError('Mendeley_safe_filename_required')
        size = detail.get('size'); checksum = detail.get('sha256_hash')
        if type(size) is not int or size < 1 or row.get('size', size) != size or not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum):
            raise ValueError('Mendeley_size_and_SHA256_required')
        expected = 'https://data.mendeley.com/public-files/datasets/'+spec['id']+'/files/'+fid+'/file_downloaded'
        if detail.get('download_url') not in (expected, expected+'?version='+str(spec['version'])):
            raise ValueError('Mendeley_file_download_identity_mismatch')
        result.append(dict(name=name, local_name='file-'+fid+'/'+name, file_id=fid,
            source_folder_id=row.get('source_folder_id'), size_bytes=size,
            checksum_algorithm='sha256', checksum=checksum, source_record_doi=spec['dataset'][4:],
            download_url=expected+'?version='+str(spec['version']),
            container='zip' if name.lower().endswith('.zip') else 'file'))
    return result
