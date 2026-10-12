"""Shared installer/rollback state writer. Standard library only; no ASR imports.

Use Python's file replacement API instead of PowerShell/.NET string-null binding.
The caller holds the VoiceBridge setup mutex and checks application locks.
"""
from __future__ import annotations
import argparse
import configparser
import logging
import os
from pathlib import Path
import re
import tempfile
import sys
import time
from typing import Callable

LOG = logging.getLogger('VoiceBridge.activation')


def retry_sharing(operation: Callable, *, delays=(0.2, 0.6, 1.2), sleep=time.sleep):
    for attempt in range(len(delays) + 1):
        try:
            return operation()
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (32, 33) or attempt == len(delays):
                raise
            sleep(delays[attempt])


def atomic_write(path: Path, data: bytes, *, backup=True, replace=os.replace) -> Path | None:
    """Write in the destination directory; never delete the active file first.

    Each successful update retains the exact previous bytes in a unique .bak.
    Errors before replacement leave the active file untouched. Sharing retries
    do not cover arbitrary side effects or access-denied errors.
    """
    path = Path(path).absolute()
    if path.is_symlink():
        raise ValueError('Refusing a symbolic-link state file.')
    if path.exists() and not path.is_file():
        raise ValueError('State destination must be a file.')
    old = retry_sharing(path.read_bytes) if path.exists() else None
    saved = None
    temp = None
    try:
        if backup and old is not None:
            fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.bak', dir=path.parent)
            saved = Path(name)
            with os.fdopen(fd, 'wb') as file:
                file.write(old); file.flush(); os.fsync(file.fileno())
        fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
        temp = Path(name)
        with os.fdopen(fd, 'wb') as file:
            file.write(data); file.flush(); os.fsync(file.fileno())
        retry_sharing(lambda: replace(temp, path))
        if saved:
            LOG.info('Previous state saved: %s', saved)
        return saved
    finally:
        if temp is not None:
            try:
                retry_sharing(temp.unlink)
            except FileNotFoundError:
                pass
            except OSError as exc:
                # Cleanup must never turn a completed activation into a failure.
                LOG.warning('Temporary file retained: %s (%s)', temp, exc)


def read_state(path: Path) -> dict[str, str]:
    raw = retry_sharing(path.read_bytes)
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(raw.decode(encoding))
    if parser.sections() != ['runtime'] or parser.defaults():
        raise ValueError('Expected one [runtime] section in current.ini.')
    state = dict(parser['runtime'])
    if not state.get('python') or set(state) - {'python', 'previous', 'version'}:
        raise ValueError('Invalid current.ini runtime keys.')
    return state


def runtime_path(root: Path, value: str) -> Path:
    if not value or any(char in value for char in '\r\n\x00'):
        raise ValueError('Invalid runtime path.')
    candidate = Path(value)
    if not candidate.is_absolute():
        raise ValueError('Runtime path must be absolute.')
    candidate = candidate.resolve()
    base = (root / 'versions').resolve()
    try:
        parts = candidate.relative_to(base).parts
    except ValueError as exc:
        raise ValueError('Runtime is outside VoiceBridge/versions.') from exc
    if len(parts) != 3 or parts[1] != 'runtime' or parts[2].lower() not in ('python.exe', 'pythonw.exe'):
        raise ValueError('Unexpected runtime layout.')
    if not candidate.is_file() or not candidate.with_name('python.exe').is_file():
        raise ValueError('Runtime Python is missing.')
    return candidate


def encode_state(pythonw: str, previous: str, version: str) -> bytes:
    if any(c in pythonw + previous + version for c in '\r\n\x00'):
        raise ValueError('Invalid state value.')
    # BOM + UTF-16LE is read by GetPrivateProfileStringW in the native launcher.
    text = f'[runtime]\r\npython={pythonw}\r\nprevious={previous}\r\nversion={version}\r\n'
    return b'\xff\xfe' + text.encode('utf-16-le')


def activate(root: Path, pythonw: str, version: str) -> None:
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid application version.')
    root = root.resolve()
    target = runtime_path(root, pythonw)
    path = root / 'current.ini'
    previous = ''
    if path.exists():
        current = read_state(path)
        active = runtime_path(root, current['python'])
        previous = str(active)
        if active == target:
            # Idempotent retry must not replace the rollback target with itself.
            previous = current.get('previous', '')
            if previous:
                runtime_path(root, previous)
    atomic_write(path, encode_state(str(target), previous, version))
    print(f'ACTIVATED: VoiceBridge {version}', flush=True)


def rollback(root: Path) -> None:
    root = root.resolve()
    path = root / 'current.ini'
    state = read_state(path)
    active = runtime_path(root, state['python'])
    previous = runtime_path(root, state.get('previous', ''))
    if active == previous:
        raise ValueError('Previous runtime is the active runtime.')
    # The version is not guessed from a folder name: app-only patches retain it.
    atomic_write(path, encode_state(str(previous), str(active), ''))
    print('ROLLBACK: previous runtime selected', flush=True)


def self_test() -> None:
    """Exercise create, update and rollback on REAL local files, not a mocked API."""
    with tempfile.TemporaryDirectory(prefix='vb-activation-check-') as folder:
        root = Path(folder)
        paths = []
        for name in ('old version', 'new version'):
            runtime = root / 'versions' / name / 'runtime'
            runtime.mkdir(parents=True)
            for exe in ('python.exe', 'pythonw.exe'):
                (runtime / exe).touch()
            paths.append(str((runtime / 'pythonw.exe').resolve()))
        activate(root, paths[0], '0.5.1')
        path = root / 'current.ini'; before = path.read_bytes()
        activate(root, paths[1], '0.5.2')
        assert read_state(path)['previous'] == paths[0]
        assert any(p.read_bytes() == before for p in root.glob('current.ini.*.bak'))
        activate(root, paths[1], '0.5.2')
        assert read_state(path)['previous'] == paths[0]
        rollback(root)
        assert read_state(path)['python'] == paths[0]
        assert read_state(path)['previous'] == paths[1]
    print('ACTIVATION SELF-TEST PASSED: create / replace / repeat / rollback', flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('activate', 'rollback', 'self-test'))
    parser.add_argument('--root', type=Path)
    parser.add_argument('--pythonw')
    parser.add_argument('--version')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', stream=sys.stdout)
    if args.action == 'self-test':
        self_test()
    else:
        if args.root is None:
            parser.error('--root is required')
        if args.action == 'activate':
            if not args.pythonw or not args.version:
                parser.error('--pythonw and --version are required')
            activate(args.root, args.pythonw, args.version)
        else:
            rollback(args.root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
