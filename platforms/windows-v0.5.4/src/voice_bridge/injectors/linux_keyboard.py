"""Persistent keyboard transports. Clipboard and focus are handled separately.

Never fall back after a shortcut has started: the first transport may already
have delivered it, so retrying on another one can paste twice.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time

LOG = logging.getLogger(__name__)
VIRTUAL_PREFIXES = ("voice bridge", "voice input bridge", "ydotool")
KEYS = {
    "ctrl+v": (29, 47),
    "ctrl+shift+v": (29, 42, 47),
    "shift+insert": (42, 110),
    "ctrl+z": (29, 44),
}


def is_virtual_device(name: str, phys: str = "") -> bool:
    return name.lower().startswith(VIRTUAL_PREFIXES) or phys.startswith("voice-bridge/")


def key_events(combo: str) -> list[tuple[int, int]]:
    try:
        keys = KEYS[combo]
    except KeyError as exc:
        raise ValueError(f"Unsupported shortcut: {combo}") from exc
    return [(k, 1) for k in keys] + [(k, 0) for k in reversed(keys)]


def detect_ydotool_syntax(output: str) -> str:
    """Only a version/help signature counts, never a successful key command."""
    match = re.search(r"(?:ydotool\s*(?:version\s*)?v?|^v?)(\d+)\.(\d+)(?:\.(\d+))?", output, re.I | re.M)
    if match:
        return "modern" if int(match[1]) >= 1 else "legacy"
    text = output.lower()
    if "keycode:state" in text or "28:1" in text or "29:1" in text:
        return "modern"
    if "ctrl+alt" in text or "alt+f4" in text or "ctrl+c" in text or "key sequence" in text:
        return "legacy"
    raise RuntimeError("Cannot identify ydotool syntax. Select uinput or explicitly choose legacy/modern syntax.")


def ydotool_command(binary: str, combo: str, syntax: str, delay_ms: int) -> list[str]:
    events = key_events(combo)
    if syntax == "legacy":
        return [binary, "key", combo]
    if syntax != "modern":
        raise ValueError(f"Unknown ydotool syntax: {syntax}")
    return [binary, "key", "--key-delay", str(delay_ms), *[f"{key}:{state}" for key, state in events]]


class UInputKeyboard:
    name = "uinput (persistent keyboard)"

    def __init__(self, *, delay_ms: int = 25, settle_seconds: float = 0.8) -> None:
        self.delay = delay_ms / 1000
        self.settle_seconds = settle_seconds
        self._device = None
        self._lock = threading.RLock()
        self._pressed: list[int] = []

    def prepare(self) -> None:
        with self._lock:
            if self._device is not None:
                return
            try:
                import evdev
                # Advertise a *keyboard*, not just Ctrl/V: udev/libinput must
                # classify this device as ID_INPUT_KEYBOARD. No mouse buttons.
                self._device = evdev.UInput(
                    {evdev.ecodes.EV_KEY: list(range(1, 249)),
                     evdev.ecodes.EV_LED: [0, 1, 2]},
                    name="Voice Bridge Paste Keyboard",
                    phys="voice-bridge/paste-keyboard",
                    bustype=evdev.ecodes.BUS_USB,
                )
            except Exception as exc:
                raise RuntimeError(
                    "Cannot create Voice Bridge keyboard. evdev and read/write access to /dev/uinput are required. "
                    "Run the supplied input-access setup; do not run the desktop application with sudo. "
                    f"Details: {exc}"
                ) from exc
            # The kernel device existing is not proof of compositor readiness.
            # Create it once, before recording/model loading, and keep it alive.
            time.sleep(self.settle_seconds)
            LOG.info("Persistent uinput keyboard initialized; initial settle %.2fs", self.settle_seconds)

    def _event(self, key: int, value: int) -> None:
        self._device.write(1, key, value)  # EV_KEY
        self._device.syn()

    def send(self, combo: str) -> None:
        events = key_events(combo)
        with self._lock:
            self.prepare()
            try:
                for key, value in events:
                    # Track before writing: a failed SYN may follow a successful key-down.
                    if value and key not in self._pressed:
                        self._pressed.append(key)
                    self._event(key, value)
                    if not value and key in self._pressed:
                        self._pressed.remove(key)
                    time.sleep(self.delay)
            finally:
                self._release_pressed()

    def _release_pressed(self) -> None:
        for key in reversed(self._pressed):
            try:
                self._event(key, 0)
            except OSError:
                LOG.exception("Cannot release virtual key %d", key)
        self._pressed.clear()

    def close(self) -> None:
        with self._lock:
            if self._device is not None:
                self._release_pressed()
                self._device.close()
                self._device = None


class YdotoolKeyboard:
    def __init__(self, *, syntax: str = "auto", delay_ms: int = 25) -> None:
        self.syntax = syntax
        self.delay_ms = delay_ms
        self.binary: str | None = None
        self.name = "ydotool (not initialized)"

    def prepare(self) -> None:
        if self.binary:
            return
        binary = shutil.which("ydotool")
        if not binary:
            raise RuntimeError("ydotool is not installed; select uinput instead")
        if self.syntax == "auto":
            parts = []
            for args in ([binary, "--version"], [binary, "key", "--help"]):
                proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, timeout=2, check=False)
                parts.append(proc.stdout or "")
                try:
                    self.syntax = detect_ydotool_syntax("\n".join(parts))
                    break
                except RuntimeError:
                    pass
            if self.syntax == "auto":
                raise RuntimeError("ydotool version/syntax is unknown; select the native uinput backend")
        self.binary = binary
        self.name = f"ydotool ({self.syntax})"
        LOG.info("Keyboard transport %s; binary=%s", self.name, binary)

    def send(self, combo: str) -> None:
        self.prepare()
        proc = subprocess.run(ydotool_command(self.binary, combo, self.syntax, self.delay_ms),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=5, check=False)
        if proc.returncode:
            raise RuntimeError(f"ydotool exit={proc.returncode}: {(proc.stderr or proc.stdout).strip()[:800]}")
        if proc.stderr.strip():
            LOG.warning("ydotool: %s", proc.stderr.strip()[:800])

    def close(self) -> None:
        pass


class X11Keyboard:
    name = "pynput/XTest (X11 only)"

    def __init__(self, *, delay_ms: int = 25) -> None:
        self.delay = delay_ms / 1000
        self._keyboard = None

    def prepare(self) -> None:
        if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            raise RuntimeError("pynput/XTest is not a system-wide Wayland backend; select uinput")
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("No X11 DISPLAY is available")
        if self._keyboard is None:
            from pynput.keyboard import Controller, Key
            self._keyboard = Controller()
            self._keys = {29: Key.ctrl, 42: Key.shift, 47: "v", 44: "z", 110: Key.insert}

    def send(self, combo: str) -> None:
        self.prepare()
        pressed = []
        try:
            for code, down in key_events(combo):
                key = self._keys[code]
                if down:
                    pressed.append(key)
                    self._keyboard.press(key)
                else:
                    self._keyboard.release(key)
                    pressed.remove(key)
                time.sleep(self.delay)
        finally:
            for key in reversed(pressed):
                self._keyboard.release(key)

    def close(self) -> None:
        pass


def make_keyboard(backend: str, *, syntax: str, delay_ms: int):
    selected = backend
    if selected == "auto":
        wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        selected = "uinput" if wayland or not os.environ.get("DISPLAY") else "pynput"
    if selected == "uinput":
        return UInputKeyboard(delay_ms=delay_ms)
    if selected == "ydotool":
        return YdotoolKeyboard(syntax=syntax, delay_ms=delay_ms)
    if selected == "pynput":
        return X11Keyboard(delay_ms=delay_ms)
    raise ValueError(f"Unknown keyboard backend: {backend}")
