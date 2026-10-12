#!/usr/bin/env python3
"""Stop only this user's recognizable Voice Bridge processes during upgrade."""
import os
import signal
import time
from pathlib import Path


def matches(argv):
    if not argv:
        return False
    for i, item in enumerate(argv[:-1]):
        if item == '-m' and argv[i+1] in ('voice_bridge.desktop.app', 'voice_bridge.desktop.worker', 'voice_bridge.cli'):
            return argv[i+1] != 'voice_bridge.cli' or any(a in ('run','record-once','test-paste') for a in argv[i+2:])
    # Console scripts have the interpreter and script as their first two args.
    for arg in argv[:2]:
        if Path(arg).name == 'voice-bridge-desktop':
            return True
        if Path(arg).name == 'voice-bridge' and any(a in ('run','record-once','test-paste') for a in argv[1:]):
            return True
    return False


def identity(path):
    # Fields after ')' start at field 3; field 22 is process start time.
    return (path/'stat').read_text().rsplit(')',1)[1].split()[19]


def main():
    targets={}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name)==os.getpid():
            continue
        try:
            if path.stat().st_uid != os.getuid():
                continue
            argv=(path/'cmdline').read_bytes().decode(errors='replace').rstrip('\0').split('\0')
            if matches(argv):
                targets[int(path.name)]=identity(path)
        except (OSError, ValueError, IndexError):
            continue
    for pid in targets:
        try:
            os.kill(pid,signal.SIGTERM)
            print(f'Stopping Voice Bridge PID {pid}')
        except ProcessLookupError:
            pass
    deadline=time.monotonic()+3
    while time.monotonic()<deadline and any(Path(f'/proc/{pid}').exists() for pid in targets):
        time.sleep(.1)
    for pid, start in targets.items():
        try:
            if identity(Path(f'/proc/{pid}'))==start:
                os.kill(pid,signal.SIGKILL)
        except (OSError, ValueError, IndexError):
            pass

if __name__=='__main__':
    main()
