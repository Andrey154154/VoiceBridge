"""Build the Windows online installer; requires Python, setuptools/wheel and LLVM.

Usage:
  python packaging/windows/build_release.py --out /absolute/path/out
The output folder must be outside the source tree. The installer is cross-built;
this command does not certify execution on Windows.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from build_native import build

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out == SOURCE or SOURCE in out.parents:
        parser.error('Choose an output folder outside the source tree.')
    out.mkdir(parents=True, exist_ok=True)
    wheels = out / 'wheels'
    wheels.mkdir(exist_ok=True)
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                    '--no-build-isolation', '--wheel-dir', str(wheels), str(SOURCE)], check=True)
    wheel = wheels / 'voice_input_bridge-0.5.4-py3-none-any.whl'
    if not wheel.is_file():
        raise SystemExit('Application wheel not found.')
    native = out / 'native'
    launcher = build(native)
    payload_dir = out / 'payload'
    payload_dir.mkdir(exist_ok=True)
    for old in payload_dir.iterdir():
        if not old.is_file():
            raise SystemExit(f'Unexpected directory in payload: {old}')
        old.unlink()
    for path in [wheel, launcher, SOURCE / 'README_WINDOWS_RU.md',
                 *[HERE / name for name in ('setup-worker.ps1', 'verify-runtime.py', 'cache_wheel.py',
                                           'uninstall.ps1', 'rollback.ps1', 'activation.py')]]:
        shutil.copy2(path, payload_dir / path.name)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(payload_dir.iterdir()) if p.is_file()}
    (payload_dir / 'payload-hashes.json').write_text(
        json.dumps(hashes, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')
    payload_zip = out / 'payload.zip'
    with zipfile.ZipFile(payload_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(payload_dir.iterdir()):
            z.write(path, path.name)
    with zipfile.ZipFile(payload_zip) as z:
        if z.testzip() is not None:
            raise SystemExit('Payload archive CRC failed.')
        for name, expected in hashes.items():
            if hashlib.sha256(z.read(name)).hexdigest() != expected:
                raise SystemExit(f'Payload hash failed: {name}')
    setup = build(native, payload_zip)
    destination = out / setup.name
    shutil.copy2(setup, destination)
    print('Cross-built online installer:', destination)
    print('SHA256:', hashlib.sha256(destination.read_bytes()).hexdigest())
    print('Native Windows execution has NOT been tested by this build.')


if __name__ == '__main__':
    main()
