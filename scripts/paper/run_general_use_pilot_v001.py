"""Fixed-code, bounded untouched-source pilot; no scientific accuracy claims."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

CASES=[('taichidb','1.0.1'),('grabmyo','1.1.0'),('hd-semg','2.0.0')]


def main():
    parser=argparse.ArgumentParser()
    for name in ('code','output','r-library'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();code=Path(args.code).resolve();out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=False)
    expected='3e94f318d248ce15638086696d5dd83c0a97e36e'
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=code,text=True).strip()
    assert commit==expected and not subprocess.check_output(['git','status','--porcelain'],cwd=code,text=True).strip()
    environment=dict(os.environ,PYTHONPATH=str(code/'src'))
    def case(spec):
        slug,version=spec;folder=out/slug;folder.mkdir()
        urls=folder/'urls.json';urls.write_text(json.dumps(['https://physionet.org/content/'+slug+'/'+version+'/']))
        command=[sys.executable,'-m','harmo.cli','harmonize','--urls',str(urls),'--output',str(folder/'workflow'),
                 '--purpose','activation-timing','--purpose','amplitude-pooling','--max-records','2',
                 '--max-bytes',str(128*1024**2),'--max-comparisons','20','--r-library',args.r_library]
        started=time.monotonic();timeout=False
        with (folder/'execution.log').open('w') as log:
            process=subprocess.Popen(command,cwd=code,env=environment,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:exit_code=process.wait(timeout=360)
            except subprocess.TimeoutExpired:
                timeout=True;os.killpg(process.pid,signal.SIGTERM)
                try:exit_code=process.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);exit_code=process.wait()
        row=dict(dataset=slug,version=version,command=command,exit_code=exit_code,timed_out=timeout,
                 wall_seconds=time.monotonic()-started,code_commit=commit,
                 manual_source_configurations=0,code_changes=0,accuracy_evaluated=False)
        (folder/'execution.json').write_text(json.dumps(row,indent=2))
        print(json.dumps(row),flush=True)
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(case,CASES))
    (out/'execution.summary.json').write_text(json.dumps(dict(cases=rows,scope='Exploratory no-adjustment pilot; capped records, no human reference and no model inference.'),indent=2))


if __name__=='__main__':main()
