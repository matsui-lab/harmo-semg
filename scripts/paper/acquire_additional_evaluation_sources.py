"""Recover only the already registered physiological-control source assets."""
import argparse
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    protocol = yaml.safe_load((ROOT / 'paper/physiology_positive_control_protocol.yaml').read_text())
    reference = json.loads((ROOT / 'paper/results/validation/physiology_positive_control.json').read_text())
    hashes = {row['source_asset']: row['source_asset_sha256'] for row in reference['metrics']}
    jobs = [(name, meta) for assets in protocol['source_assets'].values() for name, meta in assets.items()]

    def recover(job):
        name, meta = job
        target = args.output / name
        partial = args.output / (name + '.partial')
        url = 'https://ndownloader.figshare.com/files/{}'.format(meta['file_id'])
        if not target.exists():
            if name.endswith('.zip'):
                chunks = args.output / (name + '.chunks')
                chunks.mkdir(exist_ok=True)
                step = 16 * 1024 * 1024
                def part(start):
                    end = min(start + step, meta['bytes']) - 1
                    path = chunks / str(start)
                    if not path.exists() or path.stat().st_size != end - start + 1:
                        subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--silent', '--show-error',
                                        '--max-time', '600', '--range', '{}-{}'.format(start, end),
                                        '--output', str(path), url + '?segment={}'.format(start)], check=True)
                    if path.stat().st_size != end - start + 1:
                        raise ValueError('range size mismatch: {}'.format(path))
                    print('range verified length: {}'.format(start), flush=True)
                    return path
                with ThreadPoolExecutor(max_workers=8) as transfers:
                    parts = list(transfers.map(part, range(0, meta['bytes'], step)))
                with partial.open('wb') as stream:
                    for path in parts:
                        with path.open('rb') as chunk:
                            for block in iter(lambda: chunk.read(1024 * 1024), b''):
                                stream.write(block)
            else:
                subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--silent', '--show-error',
                                '--max-time', '1800', '--output', str(partial), url], check=True)
            candidate = partial
        else:
            candidate = target
        sha, md5 = hashlib.sha256(), hashlib.md5()
        with candidate.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                sha.update(chunk); md5.update(chunk)
        row = dict(name=name, url=url, bytes=candidate.stat().st_size,
                   sha256=sha.hexdigest(), md5=md5.hexdigest())
        row['verified'] = row['bytes'] == meta['bytes'] and row['md5'] == meta['md5'] and row['sha256'] == hashes[name]
        if not row['verified']:
            raise ValueError('source identity mismatch: {}'.format(row))
        if candidate == partial:
            partial.rename(target)
        print(json.dumps(row), flush=True)
        return row

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(recover, jobs))
    (args.output / 'source_verification.json').write_text(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    main()
