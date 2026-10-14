"""Fixed offline regression profile with a machine-readable environment receipt."""
import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

TESTS = ['schema_resources','zenodo_intake','collection_intake','archive_intake','mixed_batch','automap',
         'c3d_automap','motion_automap','proposal_catalog','repository_bridge',
         'range_download','public_batch','public_export','public_schemas',
         'automated_corpus','catalog_evidence','semantic_promotion','timing_export','timing_workflow']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--installed',action='store_true',help='Test the installed package, with source PYTHONPATH disabled')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=Path(args.output).resolve()
    if output == root or root in output.parents:
        parser.error('Keep validation outputs outside the tested checkout')
    output.mkdir(parents=True,exist_ok=False)
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
    versions={}
    constraints=root/'validation/reuse_core_constraints_v001.txt'
    for line in constraints.read_text().splitlines():
        if not line or line.startswith('#'):continue
        name,expected=line.split('==')
        try: actual=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: actual=None
        versions[name]=dict(expected=expected,actual=actual,match=actual==expected)
    receipt=dict(profile='reuse-software/v002',commit=git('rev-parse','HEAD'),
        clean_before=not git('status','--porcelain'),python=sys.version,executable=sys.executable,
        platform=platform.platform(),dependencies=versions,sys_path=sys.path,
        module_origins={name: importlib.util.find_spec(name).origin for name in
                        ['numpy','scipy','pandas','pytest','pydantic']},
        scope='Offline core regression only; not full installation, live repository or independent validation.')
    env=dict(os.environ,PYTHONPATH='' if args.installed else str(root/'src'),OPENBLAS_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1')
    origin=Path(subprocess.check_output([sys.executable,'-c','import harmo; print(harmo.__file__)'],cwd=output,env=env,text=True).strip()).resolve()
    if args.installed and root in origin.parents:
        parser.error('Installed validation must not import the source checkout')
    receipt.update(mode='installed' if args.installed else 'source',harmo_origin=str(origin))
    command=[sys.executable,'-m','pytest','-p','no:cacheprovider',
             *['tests/test_'+n+'.py' for n in TESTS],'-q','--junitxml='+str(output/'tests.xml')]
    with (output/'tests.log').open('x') as log:
        result=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
    receipt.update(test_exit_code=result.returncode,clean_after=not git('status','--porcelain'),
                   commit_after=git('rev-parse','HEAD'))
    receipt['pass']=result.returncode==0 and receipt['clean_before'] and receipt['clean_after'] and (
        receipt['commit']==receipt['commit_after']) and all(v['match'] for v in versions.values())
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(passed=receipt['pass'],commit=receipt['commit'],output=str(output))))
    return 0 if receipt['pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
