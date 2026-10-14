"""Development re-test after observed failures; never relabel as held-out."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def digest_source(code):
    return {str(p.relative_to(code)): hashlib.sha256(p.read_bytes()).hexdigest()
            for parent in ('src', 'schemas') for p in sorted((code/parent).rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--code', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--r-library', required=True)
    args=parser.parse_args(); code=args.code.resolve(); output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    manifest=digest_source(code)
    (output/'source.manifest.json').write_text(json.dumps(manifest,indent=2))
    def case(spec):
        slug,version=spec; folder=output/slug;folder.mkdir()
        urls=folder/'urls.json';urls.write_text(json.dumps([f'https://physionet.org/content/{slug}/{version}/']))
        command=[sys.executable,'-m','harmo.cli','harmonize','--urls',str(urls),'--output',str(folder/'workflow'),
                 '--purpose','activation-timing','--purpose','amplitude-pooling','--max-records','2',
                 '--max-bytes',str(128*1024**2),'--max-comparisons','128','--r-library',args.r_library]
        started=time.monotonic(); timed_out=False
        with (folder/'execution.log').open('w') as log:
            proc=subprocess.Popen(command,cwd=code,env=dict(os.environ,PYTHONPATH=str(code/'src')),
                                  stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try: status=proc.wait(timeout=360)
            except subprocess.TimeoutExpired:
                timed_out=True;os.killpg(proc.pid,signal.SIGTERM)
                try:status=proc.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);status=proc.wait()
        result=dict(dataset=slug,version=version,command=command,exit_code=status,timed_out=timed_out,
                    wall_seconds=time.monotonic()-started,manual_source_configurations=0,local_llm_used=False,
                    held_out=False,scientific_accuracy_evaluated=False)
        (folder/'execution.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(case,[('taichidb','1.0.1'),('grabmyo','1.1.0'),('hd-semg','2.0.0')]))
    unchanged=manifest==digest_source(code)
    (output/'execution.summary.json').write_text(json.dumps(dict(rows=rows,source_unchanged=unchanged),indent=2))
    assert unchanged


if __name__=='__main__':main()
