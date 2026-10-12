"""Build an offline application-only hotfix alongside the full online installer."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
HERE=Path(__file__).resolve().parent

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--new-wheel',type=Path,required=True)
    parser.add_argument('--baseline-wheel',type=Path,action='append',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    folder=out/'VoiceBridge-Windows-0.5.4-hotfix'
    if folder.exists():raise SystemExit('Use a clean output directory.')
    folder.mkdir()
    for path in (HERE/'hotfix').iterdir():
        if path.is_file() and path.suffix in {'.py','.ps1','.cmd','.md'}:shutil.copy2(path,folder/path.name)
    payloads=[]
    baselines = {p.name: p for p in args.baseline_wheel}
    expected_baselines = {f'voice_input_bridge-{v}-py3-none-any.whl' for v in ('0.5.0','0.5.1','0.5.2','0.5.3')}
    if set(baselines) != expected_baselines:
        raise SystemExit('Supply 0.5.0, 0.5.1, 0.5.2 and 0.5.3 baseline wheels for restoration.')
    inputs=[(args.new_wheel,'0.5.4')] + [(baselines[f'voice_input_bridge-{v}-py3-none-any.whl'],v) for v in ('0.5.0','0.5.1','0.5.2','0.5.3')]
    for path,version in inputs:
        expected=f'voice_input_bridge-{version}-py3-none-any.whl'
        if path.name!=expected:raise SystemExit('Unexpected wheel filename: '+str(path))
        with zipfile.ZipFile(path) as archive:
            if archive.testzip():raise SystemExit('Corrupt wheel: '+str(path))
            meta=archive.read(f'voice_input_bridge-{version}.dist-info/METADATA').decode('utf-8')
            if f'\nVersion: {version}\n' not in meta:raise SystemExit('Wrong wheel metadata version.')
        shutil.copy2(path,folder/expected);payloads.append(folder/expected)
    for name in ('verify-runtime.py','activation.py','rollback.ps1'):
        dest=folder/name;shutil.copy2(HERE/name,dest);payloads.append(dest)
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in payloads}
    (folder/'payload-hashes.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')
    sums=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(folder.iterdir()) if p.is_file())
    (folder/'SHA256SUMS.txt').write_text(sums,encoding='ascii')
    destination=out/(folder.name+'.zip')
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for p in sorted(folder.iterdir()):archive.write(p,folder.name+'/'+p.name)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip():raise SystemExit('Hotfix archive CRC failed.')
    print(destination)
    print('SHA256',hashlib.sha256(destination.read_bytes()).hexdigest())
if __name__=='__main__':main()
