"""Portable registered inputs; rebuild contracts and releases, never import approval.

Packages contain data, not executable code. Restore uses the installed Harmo
implementation and frozen source identities. Hash inventories are not signatures.
"""
import hashlib
import io
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
import numpy as np
from harmo.config.automap import require, save
from harmo.evidence.io import sha256_file, write_json_atomic
from harmo.reuse_workflow import URLS, Stages, acquire_sources, acquire_article, implementation_identity
from harmo.timing_workflow import checked_profile

VERSION='registered-timing-transfer/1'
MAX_BYTES=3*1024**3


def checked_reference(raw):
    require(len(raw)<=65536,'reference_size_limit')
    keys={'left_phase','right_phase','left_values','right_values'}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries=archive.infolist()
        require(len(entries)==4 and {e.filename for e in entries}=={k+'.npy' for k in keys}
                and sum(e.file_size for e in entries)<=65536,'reference_expansion_limit')
    with np.load(io.BytesIO(raw),allow_pickle=False) as arrays:
        for key in keys:
            values=arrays[key]
            require(values.dtype.kind in 'fi' and values.shape==(101,) and np.isfinite(values).all(),'invalid_reference_array')
            if key.endswith('_phase'):
                require(np.array_equal(values,np.linspace(0,100,101)),'invalid_reference_phase')


def reference_names():
    from harmo.registered_timing import MUSCLES
    return ['reference/pair-%02d/%s.npz'%(i,m['canonical']) for i in range(1,11) for m in MUSCLES.values()]


def checked_archive(archive, max_bytes=MAX_BYTES):
    """Check names, counts, sizes, file types and streaming hashes before extraction."""
    require(type(max_bytes) is int and 0<max_bytes<=MAX_BYTES,'invalid_package_budget')
    archive=Path(archive)
    require(archive.stat().st_size<=max_bytes+1024**2,'package_size_limit')
    with zipfile.ZipFile(archive) as z:
        infos=z.infolist();names=[i.filename for i in infos]
        require(len(infos)<=64 and len(names)==len(set(names)),'duplicate_or_excess_package_entries')
        require('manifest.json' in names,'package_manifest_missing')
        require(sum(i.file_size for i in infos)<=max_bytes,'package_expansion_limit')
        for info in infos:
            p=PurePosixPath(info.filename)
            require(not p.is_absolute() and '..' not in p.parts and '\\' not in info.filename
                    and str(p)==info.filename and not info.is_dir(),'unsafe_package_path')
            kind=stat.S_IFMT(info.external_attr>>16)
            require(kind in (0,stat.S_IFREG),'package_nonregular_entry')
            require(not info.flag_bits&1,'encrypted_package_unsupported')
        require(z.getinfo('manifest.json').file_size<=512*1024,'manifest_size_limit')
        manifest=json.loads(z.read('manifest.json'))
        require(manifest['version']==VERSION and manifest['urls']==sorted(URLS),'unsupported_transfer_profile')
        files=manifest['files']
        require(isinstance(files,dict) and set(files)==set(names)-{'manifest.json'},'package_inventory_mismatch')
        expected={'protocol.yaml','article.xml','origin.json'} | set(reference_names()) | {
            'sources/Subject%d.mat'%i for i in range(1,11)} | {'sources/Gait120_011_to_020.zip'}
        require(set(files)==expected,'unexpected_package_contents')
        from harmo.timing_workflow import PROTOCOL_SHA256
        from harmo.catalog_evidence import ARTICLE_HASH
        require(files['protocol.yaml']['sha256']==PROTOCOL_SHA256 and files['article.xml']['sha256']==ARTICLE_HASH,
                'unregistered_package_evidence')
        for name,record in files.items():
            require(type(record['bytes']) is int and record['bytes']==z.getinfo(name).file_size,'package_size_mismatch')
            if name in reference_names():
                require(record['bytes']<=65536,'reference_size_limit')
                checked_reference(z.read(name))
            digest=hashlib.sha256()
            with z.open(name) as source:
                for chunk in iter(lambda:source.read(1024**2),b''):digest.update(chunk)
            require(digest.hexdigest()==record['sha256'],'package_hash_mismatch:'+name)
    return manifest


def create(workflow, source_cache, protocol, article, output):
    from harmo.timing_export import export_pair
    workflow,source_cache,protocol,article,output=(Path(p).resolve() for p in (workflow,source_cache,protocol,article,output))
    require(not output.exists(),'package_output_exists')
    profile=checked_profile(protocol)
    sources=acquire_sources(source_cache,profile,0,False)
    article=acquire_article(source_cache,article,False)
    receipt=json.loads((workflow/'timing-planned.receipt.json').read_text())
    require(receipt['result']['status']=='completed' and receipt['result']['released']==20,'completed_planned_workflow_required')
    for relative,digest in receipt['artifacts'].items():
        path=(workflow/relative).resolve()
        require(workflow in path.parents and sha256_file(str(path))==digest,'workflow_artifact_changed')
    timing=Path(receipt['result']['output']).resolve()
    require(workflow in timing.parents,'timing_output_outside_workflow')
    files={'protocol.yaml':protocol,'article.xml':article}
    files.update({'sources/'+s['name']:Path(s['path']) for s in sources})
    with tempfile.TemporaryDirectory(prefix='harmo-transfer-') as temp:
        temp=Path(temp)
        for i,name in enumerate(reference_names()):
            relative=PurePosixPath(name)
            resolved=timing/'resolution/repeat-1'/relative.parts[1]/Path(relative.name).stem/'resolved'
            # Freshly check existing release-bound lineage, not just a success flag.
            export_pair(resolved,temp/('check-%02d'%i))
            files[name]=temp/('check-%02d'%i)/'timing.npz'
        origin=temp/'origin.json'
        save(origin,dict(source_summary=json.loads((timing/'summary.json').read_text()),
                         source_summary_sha256=sha256_file(str(timing/'summary.json')),
                         source_implementation=json.loads((timing/'inputs.json').read_text())['implementation'],
                         packaging_implementation=implementation_identity(),
                         scope='Historical reference only; never authorization at destination. Install Harmo separately.'))
        files['origin.json']=origin
        inventory={name:dict(bytes=p.stat().st_size,sha256=sha256_file(str(p))) for name,p in files.items()}
        require(sum(v['bytes'] for v in inventory.values())<=MAX_BYTES-512*1024,'package_size_limit')
        manifest=dict(version=VERSION,urls=sorted(URLS),files=inventory)
        # Only data are archived; do not execute or install code from a package.
        with output.open('xb') as target,zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
            for name,p in files.items():z.write(p,name)
            z.writestr('manifest.json',json.dumps(manifest,sort_keys=True,indent=2))
    checked_archive(output)
    return dict(status='packaged',path=str(output),sha256=sha256_file(str(output)),bytes=output.stat().st_size,
                files=len(files),scope='Registered public inputs and reference curves; no portable approval or bundled runtime.')


def restore(package, output, resume=False):
    from harmo.reuse_workflow import run
    package,output=Path(package).resolve(),Path(output).resolve()
    manifest=checked_archive(package)
    package_hash=sha256_file(str(package))
    state=Stages(output,resume)
    stage='unpack'
    def status(value,**details):
        write_json_atomic(str(output/'restore.status.json'),dict(status=value,stage=stage,**details))
    try:
        status('running')
        def unpack(attempt):
            attempt.mkdir()
            with zipfile.ZipFile(package) as z:
                for name in manifest['files']:
                    target=attempt/name;target.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(name) as source,target.open('xb') as dest:shutil.copyfileobj(source,dest,1024**2)
                    require(sha256_file(str(target))==manifest['files'][name]['sha256'],'package_changed_during_unpack')
            require(sha256_file(str(package))==package_hash,'package_changed_during_unpack')
            checked_profile(attempt/'protocol.yaml')
            acquire_article(attempt/'sources',attempt/'article.xml',False)
            acquire_sources(attempt/'sources',checked_profile(attempt/'protocol.yaml'),0,False)
            return dict(inputs=str(attempt))
        unpacked=state.run('inputs',dict(package_sha256=package_hash,version=VERSION),unpack)
        inputs=Path(unpacked['inputs'])
        stage='rebuild';status('running')
        rebuilt=run(sorted(URLS),inputs/'sources',inputs/'protocol.yaml',inputs/'article.xml',output/'workflow',
                    resume=resume,planned=True)
        stage='reference-comparison';status('running')
        timing=Path(rebuilt['timing']['output']);checks=[]
        if rebuilt['status']=='completed':
            for name in reference_names():
                relative=PurePosixPath(name)
                actual=timing/'resolution/repeat-1'/relative.parts[1]/Path(relative.name).stem/'resolved/selected-signals/timing.npz'
                with np.load(inputs/name,allow_pickle=False) as old,np.load(actual,allow_pickle=False) as new:
                    require(set(old.files)==set(new.files)=={'left_phase','right_phase','left_values','right_values'},'reference_array_keys')
                    checks.append(dict(reference=name,exact=all(np.array_equal(old[k],new[k]) for k in new.files)))
        equal=len(checks)==20 and all(c['exact'] for c in checks)
        final='completed' if rebuilt['status']=='completed' and equal else ('completed-with-release-holds' if rebuilt['status']!='completed' else 'reference-mismatch')
        report=dict(status=final,package_sha256=package_hash,released=rebuilt['timing']['released'],
                    comparisons=checks,all_reference_arrays_exact=equal,
                    scope='Rebuilt using destination implementation and fresh gates; historical approvals not imported.')
        write_json_atomic(str(output/'restore.result.json'),report)
        stage='complete';status(final)
        return report
    except Exception as exc:
        status('failed',error=str(exc),error_type=type(exc).__name__)
        raise
