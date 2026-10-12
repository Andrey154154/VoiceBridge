#!/usr/bin/env python3
"""Build wheel + Ubuntu package from exactly one source tree, without networking."""
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
OUT = ROOT/'dist'
OUT.mkdir(exist_ok=True)
subprocess.run([sys.executable,'-m','pip','wheel','--no-build-isolation','--no-deps','--no-index',str(ROOT),'-w',str(OUT)],check=True)
wheel = OUT/f'voice_input_bridge-{VERSION}-py3-none-any.whl'
assert wheel.is_file()
staging = OUT/'deb-root'
if staging.exists(): shutil.rmtree(staging)
shutil.copytree(ROOT/'packaging/deb-root',staging)
control = staging/'DEBIAN/control'
assert f'Version: {VERSION}\n' in control.read_text()
app = staging/'opt/voice-bridge/app'
app.mkdir(parents=True,exist_ok=True)
for name in ('src','config'):
    shutil.copytree(ROOT/name,app/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.egg-info','*-local.toml'))
for name in ('pyproject.toml','README.md','README_RU.md','LICENSE','NOTICE','THIRD_PARTY_NOTICES.md'):
    if (ROOT/name).exists(): shutil.copy2(ROOT/name,app/name)
wheel_dir = staging/'opt/voice-bridge/wheels'
wheel_dir.mkdir()
shutil.copy2(wheel,wheel_dir/wheel.name)
shutil.copy2(ROOT/'packaging/linux/preflight.py',staging/'opt/voice-bridge/preflight.py')
for path in staging.rglob('*'):
    if path.is_dir(): path.chmod(0o755)
    elif path.suffix == '.sh' or path.parent == staging/'usr/bin' or path == staging/'DEBIAN/postinst': path.chmod(0o755)
    else: path.chmod(0o644)
subprocess.run(['dpkg-deb','--root-owner-group','--build',str(staging),str(OUT/f'VoiceBridge-Ubuntu-{VERSION}-amd64.deb')],check=True)
print(OUT)
