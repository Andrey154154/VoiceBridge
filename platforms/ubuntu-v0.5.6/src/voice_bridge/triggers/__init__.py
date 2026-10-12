from __future__ import annotations

import os
import platform

from .base import HoldTrigger


def create_trigger(
    *,
    backend: str = "auto",
    kind: str = "mouse",
    button: str = "x1",
    key: str = "f8",
    evdev_device_match: str = "",
    evdev_code: str = "BTN_SIDE",
    consume: bool = False,
) -> HoldTrigger:
    selected = backend.lower()
    system = platform.system().lower()
    if selected == "auto":
        if system == "windows":
            selected = "windows_hook"
        else:
            # On Linux Wayland global pynput hooks are unreliable; evdev is explicit and compositor-agnostic.
            wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            selected = "evdev" if system == "linux" and (wayland or (kind == "mouse" and consume)) else "pynput"
    if selected == "evdev":
        if system != "linux":
            raise RuntimeError("evdev trigger is Linux-only")
        from .evdev_trigger import EvdevHoldTrigger
        return EvdevHoldTrigger(device_match=evdev_device_match, code=evdev_code, consume=consume)
    if selected == "windows_hook":
        if system != "windows":
            raise RuntimeError("Windows native hook is Windows-only")
        from .windows_hook import WindowsMouseHookTrigger
        return WindowsMouseHookTrigger(button=button, consume=consume, kind=kind, key=key)
    if selected == "pynput":
        from .pynput_trigger import PynputHoldTrigger
        return PynputHoldTrigger(kind=kind, button=button, key=key)
    raise ValueError(f"Unknown trigger backend: {backend}")


__all__ = ["HoldTrigger", "create_trigger"]
