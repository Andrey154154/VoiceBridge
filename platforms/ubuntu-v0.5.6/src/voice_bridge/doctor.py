from __future__ import annotations

import glob
import grp
import importlib.metadata
import importlib.util
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _in_group(name: str) -> bool:
    try:
        gid = grp.getgrnam(name).gr_gid
    except KeyError:
        return False
    return gid == os.getgid() or gid in os.getgroups()


def _event_nodes() -> tuple[list[str], list[str]]:
    nodes = sorted(glob.glob('/dev/input/event*'))
    readable = [path for path in nodes if os.access(path, os.R_OK)]
    return nodes, readable


def _ydotool_daemon_status() -> tuple[bool, str]:
    if not shutil.which('ydotool'):
        return False, 'ydotool is not installed'
    try:
        proc = subprocess.run(
            ['ydotool', 'debug'],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (proc.stdout or '').strip().replace('\n', ' ')
    if proc.returncode == 0:
        return True, output or 'daemon reachable'
    return False, output or f'ydotool debug exited {proc.returncode}'


def _version_at_least(version: str | None, wanted: tuple[int, ...]) -> bool:
    if not version:
        return False
    parts: list[int] = []
    for item in version.split('.'):
        digits = ''.join(ch for ch in item if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) >= wanted


def _pkg(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_doctor() -> list[Check]:
    system = platform.system()
    transformers = _pkg('transformers')
    torch = _pkg('torch')
    qwen_asr = _pkg('qwen-asr')

    checks = [
        Check('Python sounddevice', importlib.util.find_spec('sounddevice') is not None, 'microphone capture'),
        Check('NumPy', importlib.util.find_spec('numpy') is not None, 'audio buffers'),
        Check('PyTorch', torch is not None, torch or 'not installed'),
        Check('Transformers >=5.13', _version_at_least(transformers, (5, 13, 0)), transformers or 'not installed'),
        Check('legacy qwen-asr absent', qwen_asr is None, qwen_asr or 'not installed'),
    ]

    if importlib.util.find_spec('torch') is not None:
        try:
            import torch as torch_mod
            cuda_ok = torch_mod.cuda.is_available()
            detail = 'available'
            if cuda_ok:
                detail = f"{torch_mod.cuda.get_device_name(0)}; CUDA {torch_mod.version.cuda}"
                try:
                    free_b, total_b = torch_mod.cuda.mem_get_info(0)
                    free_gb = free_b / (1024 ** 3)
                    total_gb = total_b / (1024 ** 3)
                    checks.append(Check('GPU memory', True, f"{free_gb:.2f}/{total_gb:.2f} GiB free; adaptive ASR can fall back to CPU RAM"))
                except Exception:
                    pass
            checks.append(Check('CUDA', cuda_ok, detail))
        except Exception as exc:  # pragma: no cover - diagnostic path
            checks.append(Check('CUDA', False, str(exc)))

    if system == 'Linux':
        wayland = bool(os.environ.get('WAYLAND_DISPLAY'))
        nodes, readable = _event_nodes()
        uinput_exists = os.path.exists('/dev/uinput')
        checks.extend(
            [
                Check('Wayland session', wayland, os.environ.get('WAYLAND_DISPLAY', 'not detected')),
                Check('clipboard tool', bool(shutil.which('wl-copy')) if wayland else bool(shutil.which('xclip') or shutil.which('xsel')), 'wl-copy on Wayland; xclip/xsel on X11'),
                Check('evdev', importlib.util.find_spec('evdev') is not None, 'global mouse/button trigger'),
                Check('/dev/input', bool(nodes), f'{len(nodes)} event node(s) found'),
                Check('evdev read access', bool(readable), f'{len(readable)}/{len(nodes)} event node(s) readable'),
                Check('/dev/uinput exists', uinput_exists, 'kernel uinput device'),
                Check('/dev/uinput write', uinput_exists and os.access('/dev/uinput', os.W_OK), 'persistent Voice Bridge keyboard / selective mouse proxy'),
            ]
        )
    elif system == 'Windows':
        checks.append(Check('Win32 native mouse hook', True, 'WH_MOUSE_LL via ctypes'))
    return checks
