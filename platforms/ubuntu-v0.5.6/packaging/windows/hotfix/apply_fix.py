"""Offline 0.5.0/0.5.1/0.5.2 -> 0.5.4 application and installer-state repair. Never install dependencies.

Run with the existing private VoiceBridge runtime, not a system Python.
No model import/download or user-config editing. Native use requires Windows.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

LOG = logging.getLogger('VoiceBridge.hotfix')
OLD, NEW = '0.5.0', '0.5.4'
SUPPORTED = (OLD, '0.5.1', '0.5.2', '0.5.3', NEW)
HELPERS = ('activation.py', 'rollback.ps1')
WHEELS = {v: f'voice_input_bridge-{v}-py3-none-any.whl' for v in SUPPORTED}


def verify_payload(bundle: Path) -> None:
    manifest = json.loads((bundle/'payload-hashes.json').read_text(encoding='utf-8'))
    expected = {*WHEELS.values(), 'verify-runtime.py', *HELPERS}
    if set(manifest) != expected:
        raise RuntimeError('Invalid hotfix manifest; extract the complete official archive.')
    for name in expected:
        actual = hashlib.sha256((bundle/name).read_bytes()).hexdigest()
        if actual != manifest[name]:
            raise RuntimeError(f'SHA256 mismatch: {name}. No update was performed.')


def read_ini(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    return data.decode(encoding), encoding


def configured_runtime(root: Path) -> Path:
    text, _ = read_ini(root/'current.ini')
    entries = re.findall(r'^python=(.+)$', text, flags=re.MULTILINE)
    if len(entries) != 1:
        raise RuntimeError('current.ini must contain exactly one active Python runtime.')
    configured = Path(entries[0].strip()).resolve()
    base = (root/'versions').resolve()
    try:
        configured.relative_to(base)
    except ValueError as exc:
        raise RuntimeError('Refusing a runtime outside VoiceBridge/versions.') from exc
    if configured.name.lower() not in {'pythonw.exe', 'python.exe'} or configured.parent.name != 'runtime':
        raise RuntimeError('Unexpected runtime layout; use the full installer.')
    python = configured.with_name('python.exe')
    if not configured.is_file() or not python.is_file():
        raise RuntimeError('Installed Python is missing; use the full installer.')
    return python


def write_atomic(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix='.vb-hotfix-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(data); file.flush(); os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def mark_version(root: Path, version: str) -> None:
    path=root/'current.ini';text, encoding=read_ini(path)
    if re.search(r'^version=', text, flags=re.MULTILINE):
        text=re.sub(r'^version=[^\r\n]*', 'version='+version, text, flags=re.MULTILINE)
    else:
        text=text.rstrip('\r\n')+'\r\nversion='+version+'\r\n'
    write_atomic(path, text.encode(encoding))


@contextmanager
def setup_mutex():
    k=ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]; k.CreateMutexW.restype=wintypes.HANDLE
    k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];k.WaitForSingleObject.restype=wintypes.DWORD
    k.ReleaseMutex.argtypes=[wintypes.HANDLE];k.ReleaseMutex.restype=wintypes.BOOL
    k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle.restype=wintypes.BOOL
    handle=k.CreateMutexW(None,False,'Local\\VoiceBridge.Setup.0.4')
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    held=False
    try:
        result=k.WaitForSingleObject(handle,0)
        if result not in (0,0x80):
            raise RuntimeError('Another VoiceBridge installer/update is running. Close it first.')
        held=True
        yield
    finally:
        if held:k.ReleaseMutex(handle)
        k.CloseHandle(handle)


def run(args: list[str], *, timeout: int=180, retry_locks: bool=False) -> None:
    for attempt in range(4 if retry_locks else 1):
        LOG.info('Run: %s', subprocess.list2cmdline(args))
        result=subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding='utf-8', errors='replace', timeout=timeout)
        LOG.info('%s', result.stdout.rstrip())
        if result.returncode==0:return
        if retry_locks and re.search(r'WinError\s+(32|33)\b', result.stdout) and attempt<3:
            LOG.warning('Temporary file lock; retrying local installation only.')
            time.sleep((1,2,4)[attempt]);continue
        raise RuntimeError(f'Command failed (exit {result.returncode}); see hotfix log.')


def install_wheel(bundle: Path, version: str, runner=run) -> None:
    runner([sys.executable,'-I','-X','utf8','-m','pip','--isolated','install',
            '--no-index','--no-deps','--disable-pip-version-check','--no-warn-script-location',
            '--force-reinstall',str(bundle/WHEELS[version])],retry_locks=True)


def check_version(version: str, runner=run) -> None:
    code=("import importlib.metadata, voice_bridge; "
          f"assert voice_bridge.__version__ == {version!r}; "
          f"assert importlib.metadata.version('voice-input-bridge') == {version!r}; "
          "print('Application version:',voice_bridge.__version__)")
    runner([sys.executable,'-I','-X','utf8','-c',code],timeout=30)


def apply_update(root: Path, bundle: Path, current: str, target: str, runner=run) -> None:
    """Small wheel transaction, with verified baseline reinstall on any failure."""
    original_ini=(root/'current.ini').read_bytes()
    original_helpers = {name: (root/name).read_bytes() if (root/name).exists() else None for name in HELPERS}
    backup = root/'patch-backups'/('0.5.4-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    (backup/'current.ini').write_bytes(original_ini)
    (backup/'version.txt').write_text(current,encoding='ascii')
    for name, data in original_helpers.items():
        if data is not None: (backup/name).write_bytes(data)
    LOG.info('Pre-update backup: %s', backup)
    try:
        install_wheel(bundle,target,runner)
        check_version(target,runner)
        if target==NEW:
            runner([sys.executable,'-I','-X','utf8',str(bundle/'verify-runtime.py')],timeout=120)
            runner([sys.executable,'-I','-X','utf8',str(bundle/'activation.py'),'self-test'],timeout=30)
            # Store the shared state writer before publishing the updated app version.
            for name in HELPERS: write_atomic(root/name, (bundle/name).read_bytes())
        mark_version(root,target)
    except Exception:
        LOG.exception('Update did not pass; restoring application %s.', current)
        try:
            install_wheel(bundle,current,runner)
            check_version(current,runner)
            write_atomic(root/'current.ini',original_ini)
            for name, data in original_helpers.items():
                if data is None: (root/name).unlink(missing_ok=True)
                else: write_atomic(root/name,data)
        except Exception as restore_error:
            raise RuntimeError('Automatic restore failed. Do not start the app; use the full installer. '
                               'Models and configuration were not changed.') from restore_error
        LOG.warning('Previous application restored. Models, Python and dependencies were not changed.')
        raise


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args=parser.parse_args()
    if sys.platform!='win32' or ctypes.sizeof(ctypes.c_void_p)!=8:
        print('Use Windows x64 and the bundled Apply-Fix.cmd.',file=sys.stderr);return 1
    data=Path(os.environ['LOCALAPPDATA'])/'VoiceBridge'; logs=data/'logs';logs.mkdir(parents=True,exist_ok=True)
    logfile=logs/('hotfix-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.log')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
        handlers=[logging.StreamHandler(sys.stdout),logging.FileHandler(logfile,encoding='utf-8')])
    LOG.info('VoiceBridge 0.5.4 offline repair. Log: %s',logfile)
    root=args.root.resolve();bundle=Path(__file__).resolve().parent
    try:
        verify_payload(bundle)
        expected=configured_runtime(root)
        if not os.path.samefile(expected,sys.executable):
            raise RuntimeError('Wrong Python. Start Apply-Fix.cmd, not a system Python.')
        spec=importlib.util.find_spec('voice_bridge')
        if spec is None or not spec.origin or not Path(spec.origin).resolve().is_relative_to(expected.parent.resolve()):
            raise RuntimeError('Application is not in the active private runtime.')
        current=importlib.metadata.version('voice-input-bridge')
        if current not in SUPPORTED:
            raise RuntimeError(f'This patch supports 0.5.0 / 0.5.1 / 0.5.2 / 0.5.3 / 0.5.4 only; found {current}.')
        target=NEW
        # Hold the same installer mutex and app locks for the entire mutation.
        # Never kill Python processes; fail closed when dictation is still running.
        from voice_bridge.instance import UserInstanceLock
        with setup_mutex(), ExitStack() as stack:
            for name in ('desktop','engine'):
                stack.enter_context(UserInstanceLock(name,directory=data))
            apply_update(root,bundle,current,target)
        # Cosmetic Windows Apps version is not an activation gate.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\VoiceBridge',0,winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key,'DisplayVersion',0,winreg.REG_SZ,target)
        except OSError as exc:
            LOG.warning('Application updated; Apps display version unchanged: %s',exc)
        LOG.info('DONE: VoiceBridge %s. Start it using the existing shortcut.',target)
        LOG.info('The runtime folder name may still start with 0.5.0, 0.5.1 or 0.5.2. This is expected for an application-only patch.')
        return 0
    except Exception as exc:
        LOG.exception('Update stopped: %s',exc)
        LOG.error('Close VoiceBridge via the tray and close other installers before retrying. Log: %s',logfile)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
