"""Verified immutable payloads for repeated mixed-repository intake."""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path


def identity(asset):
    return {k: asset.get(k) for k in ('source_record_doi', 'file_id', 'name',
        'download_url', 'size_bytes', 'checksum_algorithm', 'checksum', 'container')}


def location(root, asset):
    key = hashlib.sha256(json.dumps(identity(asset), sort_keys=True).encode()).hexdigest()
    return Path(root)/key


def digest(path, algorithm):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def verify(path, asset):
    if (path.is_symlink() or not path.is_file() or path.stat().st_size != asset['size_bytes']
            or digest(path, asset['checksum_algorithm']) != asset['checksum'].lower()):
        raise ValueError('payload_size_or_checksum_mismatch; retained unchanged')
    return digest(path, 'sha256')


def lookup(root, asset):
    if Path(root).is_symlink():
        raise ValueError('symlink_payload_cache_rejected')
    folder = location(root, asset)
    if not folder.exists() and not folder.is_symlink():
        return None
    receipt = folder/'receipt.json'
    if folder.is_symlink() or receipt.is_symlink() or not receipt.is_file():
        raise ValueError('invalid_payload_cache; retained unchanged')
    record = json.loads(receipt.read_text())
    if record.get('identity') != identity(asset):
        raise ValueError('payload_cache_identity_mismatch; retained unchanged')
    if verify(folder/'payload', asset) != record.get('sha256'):
        raise ValueError('payload_cache_digest_mismatch; retained unchanged')
    return folder/'payload'


def store(root, asset, source, acquisition):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('symlink_payload_cache_rejected')
    root.mkdir(parents=True, exist_ok=True)
    previous = lookup(root, asset)
    if previous is not None:
        return previous
    stage = Path(tempfile.mkdtemp(prefix='.pending-', dir=root))
    shutil.copyfile(source, stage/'payload')
    sha = verify(stage/'payload', asset)
    (stage/'receipt.json').write_text(json.dumps(dict(identity=identity(asset), sha256=sha,
        source=str(source), acquisition=acquisition), indent=2))
    stage.rename(location(root, asset))
    return location(root, asset)/'payload'
