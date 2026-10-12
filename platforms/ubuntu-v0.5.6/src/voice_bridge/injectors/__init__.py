from __future__ import annotations

import platform

from .base import TextInjector


def create_injector(*, backend: str = "auto", paste_shortcut: str = "ctrl+v", **linux_options) -> TextInjector:
    system = platform.system().lower()
    selected = backend.lower()
    if selected == "auto":
        selected = "windows" if system == "windows" else "linux"
    if selected == "windows":
        if system != "windows":
            raise RuntimeError("Windows injector selected on a non-Windows host")
        from .windows import WindowsTextInjector
        return WindowsTextInjector(paste_shortcut=paste_shortcut)
    if selected == "linux":
        from .linux import LinuxTextInjector
        return LinuxTextInjector(paste_shortcut=paste_shortcut, **linux_options)
    raise ValueError(f"Unknown injector backend: {backend}")


__all__ = ["TextInjector", "create_injector"]
