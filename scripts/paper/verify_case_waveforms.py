#!/usr/bin/env python3
"""Recover a partial public ZIP member and verify actual two-subject waveforms."""
import argparse,csv,hashlib,io,json,struct,sys,zlib,zipfile
from pathlib import Path
import numpy as np
import scipy
from scipy.io import loadmat

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from harmo.physiology_control import (load_physiology_protocol,extract_study38_rows,
    preprocess_study38_cycle,_gait120_cycles,_subject_metric,_expand_template,_numeric_mat_value)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-dir',type=Path,required=True);args=ap.parse_args()
    src=args.source_dir
    out=ROOT/'paper/figures/candidates/waveform_verification_v001_2026.09.12-r1'
    out.mkdir(parents=True,exist_ok=True)
    protocol_path=ROOT/'paper/physiology_positive_control_protocol.yaml'
    p=load_physiology_protocol(protocol_path)
    reference=json.loads((ROOT/'paper/results/validation/physiology_positive_control.json').read_text())
    directory=(src/'gait120-directory.bin').read_bytes()
    z=zipfile.ZipFile(io.BytesIO(directory))
    info=z.getinfo('S011/EMG/ProcessedData.mat')
    assert info.header_offset+1416035378-len(directory)==0
    block=(src/'gait120-s011-entry.bin').read_bytes()
    header=struct.unpack_from('<4s5H3L2H',block)
    assert header[0]==b'PK\x03\x04' and header[3]==8
    n,extra=header[-2:];name=block[30:30+n].decode()
    assert name==info.filename
    content=zlib.decompress(block[30+n+extra:30+n+extra+info.compress_size],-15)
    assert len(content)==info.file_size and zlib.crc32(content)==info.CRC
    member=src/'S011-ProcessedData.mat';member.write_bytes(content)
    p['required_studies']['study_38']['subjects']=['1']
    left_rows,attest=extract_study38_rows(src,p)
    left=next(r for r in left_rows if r['muscle']=='tibialis anterior')
    gait,labels=_gait120_cycles(member)
    right=dict(study_id='study_120',subject_id='S011',muscle='tibialis anterior',side='right',
               cycle_count=len(gait['tibialis anterior']),values=np.median(gait['tibialis anterior'],axis=0).tolist())
    curves=[];observations=[]
    numeric=['activation_centroid_percent_gait_cycle','peak_timing_percent_gait_cycle','log2_phase_contrast',
             'active_phase_mean_within_subject_scale','reference_phase_mean_within_subject_scale']
    for row in [left,right]:
        metric=_subject_metric(row,p['muscles']['tibialis anterior'])
        expected=next(r for r in reference['metrics'] if r['study_id']==row['study_id'] and r['subject_id']==row['subject_id'] and r['muscle']==row['muscle'])
        errors={k:abs(metric[k]-expected[k]) for k in numeric}
        assert all(np.isclose(metric[k],expected[k],rtol=1e-10,atol=1e-10) for k in numeric),errors
        assert row['cycle_count']==expected['cycle_count'] and metric['pattern_pass']==expected['pattern_pass']
        observations.append(dict(study=row['study_id'],subject=row['subject_id'],cycle_count=row['cycle_count'],
                                 metric=metric,absolute_errors=errors))
        for i,v in enumerate(row['values']):curves.append(dict(panel='Study 38: five-cycle median' if row is left else 'Gait120: ten-cycle median',curve='median',x=i,value=v))
    s=loadmat(src/'Subject1.mat',squeeze_me=True,struct_as_record=False)['s'];fs=float(s.EMGFreq)
    channel=[str(v).strip() for v in s.EmgVarName].index('Tibialis Anterior')
    selected=[(j,item) for j,item in enumerate(np.atleast_1d(s.Data).ravel()) if str(item.Task).strip()=='Walking' and str(item.Foot).strip()==str(s.EMGSide).strip()][:5]
    for cyc,(j,item) in enumerate(selected):
        raw=np.asarray(item.EMG)[channel,:]
        norm=preprocess_study38_cycle(raw,fs,p['study_38_envelope'])
        for i,v in enumerate(norm):curves.append(dict(panel='Study 38: five-cycle median',curve=f'cycle-{cyc+1}',x=i,value=v))
        if cyc==0:
            for i,v in enumerate(raw):curves.append(dict(panel='Study 38: first raw cycle (s)',curve='source',x=i/fs,value=v))
    mat=loadmat(member)
    for template in ['LevelWalking.{trial}.{step}.EMGs_interpolated','LevelWalking.{trial}.{step}.EMGs_norm','LevelWalking.{trial}.{step}.EMGs_env']:
        matches=_expand_template(mat,template,{'trial':r'Trial\d+','step':r'Step\d+'})
        if matches:break
    key=matches[0][0];a,cols=_numeric_mat_value(mat,key)
    if a.shape[1]!=12:a=a.T
    a=a[:,cols.index('TibialisAnterior')]
    for i,v in enumerate(a):curves.append(dict(panel='Gait120: first released envelope (sample)',curve='source',x=i,value=v))
    for cyc,arr in enumerate(gait['tibialis anterior']):
        for i,v in enumerate(arr):curves.append(dict(panel='Gait120: ten-cycle median',curve=f'cycle-{cyc+1}',x=i,value=v))
    with (out/'waveforms.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['panel','curve','x','value']);w.writeheader();w.writerows(curves)
    with (out/'subject_curves.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['phase_percent','study38_subject1','gait120_S011'])
        w.writerows(zip(range(101),left['values'],right['values']))
    report=dict(status='verified_two_subject_waveforms',tolerance=dict(rtol=1e-10,atol=1e-10),
                observations=observations,study38_sampling_rate_hz=fs,study38_source_cycle_indices_zero_based=[j for j,_ in selected],
                study38_sha256=hashlib.sha256((src/'Subject1.mat').read_bytes()).hexdigest(),
                study38_full_source_digest_matches=True,gait120_member=info.filename,gait120_first_source_key=key,
                gait120_member_crc32=info.CRC,gait120_member_crc32_verified=True,
                gait120_member_sha256=hashlib.sha256(content).hexdigest(),gait120_full_archive_sha256_verified=False,
                source_note='Gait120 member obtained by HTTP Range; full 1.4 GB archive hash not recomputed. CRC32 checks transport integrity, not identity against the historical full-archive SHA-256.',
                scipy_version=scipy.__version__,numpy_version=np.__version__,
                protocol_sha256=hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
                code_sha256=hashlib.sha256((ROOT/'src/harmo/physiology_control.py').read_bytes()).hexdigest(),
                curve_sha256=hashlib.sha256((out/'subject_curves.csv').read_bytes()).hexdigest(),
                claim_boundary='Two selected subjects only; not a rerun of the full cohort, qualification policy, or independent validation.')
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
