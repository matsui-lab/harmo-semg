"""Read-only, bounded inspection of the exact Lencioni public archive.

Print evidence JSON; never download the complete archive or execute source scripts.
"""
import argparse
import hashlib
import io
import json
import struct
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from scipy.io import loadmat


def bounded_range(url, start, end, total):
    request = Request(url + f'?harmo_range={start}-{end}', headers={
        'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity'})
    with urlopen(request, timeout=45) as response:
        if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
            raise ValueError('Server did not return the exact bounded range')
        content = response.read(end - start + 2)
    if len(content) != end - start + 1:
        raise ValueError('Incorrect response size')
    return content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--subject', type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    articles = {item['id']: item for item in inventory['items']}
    archive = articles[9784970]['files'][0]
    total = archive['size']
    start = total - 65557
    tail = bounded_range(archive['download_url'], start, total - 1, total)
    end_record = struct.unpack_from('<4s4H2IH', tail, tail.rfind(b'PK\x05\x06'))
    if end_record[1:3] != (0, 0) or end_record[6] < start:
        raise ValueError('Unsupported archive layout')
    with zipfile.ZipFile(io.BytesIO(tail)) as archive_index:
        members = archive_index.infolist()
        target = archive_index.getinfo('Subject1.mat')
    member_records = [dict(name=m.filename, size=m.file_size,
                           compressed_size=m.compress_size, crc32=m.CRC,
                           header_offset=m.header_offset + start) for m in members]
    offset = target.header_offset + start
    header = bounded_range(archive['download_url'], offset, offset + 29, total)
    fields = struct.unpack('<4s5H3I2H', header)
    if fields[0] != b'PK\x03\x04' or fields[3] != zipfile.ZIP_DEFLATED or fields[2] & 1:
        raise ValueError('Unexpected ZIP member encoding')
    payload_start = offset + 30 + fields[-2] + fields[-1]
    if target.compress_size > 10_000_000 or target.file_size > 10_000_000:
        raise ValueError('Subject member exceeds inspection budget')
    compressed = bounded_range(archive['download_url'], payload_start,
                               payload_start + target.compress_size - 1, total)
    decoder = zlib.decompressobj(-15)
    subject_bytes = decoder.decompress(compressed, target.file_size + 1)
    if (len(subject_bytes) != target.file_size or not decoder.eof
            or decoder.unused_data or zlib.crc32(subject_bytes) != target.CRC):
        raise ValueError('Member size/CRC/decompression verification failed')
    local_bytes = args.subject.read_bytes()
    local = loadmat(io.BytesIO(local_bytes), squeeze_me=True, struct_as_record=False)['s']
    trials = local.Data.ravel()
    result = dict(
        checked_at_utc=datetime.now(timezone.utc).isoformat(),
        archive=archive, bytes_requested=len(tail) + len(header) + len(compressed),
        tail_sha256=hashlib.sha256(tail).hexdigest(), members=member_records,
        subject1=dict(local_path=str(args.subject), size=len(subject_bytes),
                      archive_sha256=hashlib.sha256(subject_bytes).hexdigest(),
                      local_sha256=hashlib.sha256(local_bytes).hexdigest(),
                      archive_md5=hashlib.md5(subject_bytes).hexdigest(),
                      expected_individual_md5=articles[9784964]['files'][0]['computed_md5'],
                      byte_identical=subject_bytes == local_bytes,
                      parent_fields=local._fieldnames,
                      trial_fields=trials[0]._fieldnames,
                      trial_count=len(trials),
                      first_three_emg_start_timestamps=[float(t.TimeStampEmg) for t in trials[:3]]),
        archive_whole_checksum_verified=False,
        scope='ZIP directory and Subject1 payload only; other subject payloads not fetched',
    )
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
