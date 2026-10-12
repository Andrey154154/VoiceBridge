from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

from .linux_keyboard import KEYS, make_keyboard

LOG = logging.getLogger(__name__)


class LinuxTextInjector:
    """One clipboard publication + exactly one paste shortcut.

    No AT-SPI guesses, no shortcut remapping, no XWayland-only fallback,
    no claim that an application's text was verified by subprocess exit=0.
    """
    def __init__(self, *, paste_shortcut: str = "ctrl+v", keyboard_backend: str = "auto",
                 ydotool_syntax: str = "auto", key_delay_ms: int = 25,
                 clipboard_settle_ms: int = 350) -> None:
        self.paste_shortcut = paste_shortcut.lower()
        if self.paste_shortcut not in KEYS or self.paste_shortcut == "ctrl+z":
            raise ValueError(f"Unsupported paste shortcut: {paste_shortcut}")
        self.keyboard = make_keyboard(keyboard_backend, syntax=ydotool_syntax, delay_ms=key_delay_ms)
        self.clipboard_settle_ms = clipboard_settle_ms
        self._lock = threading.RLock()
        self._closed = False

    def prepare(self) -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("Injector is closed")
            self.keyboard.prepare()
            detail = f"{self.keyboard.name}; shortcut={self.paste_shortcut}; clipboard settle={self.clipboard_settle_ms}ms"
            LOG.info("Delivery ready: %s", detail)
            return detail

    @staticmethod
    def _copy_command(command: list[str], text: str) -> None:
        # wl-copy and xclip daemonize and can keep stderr/stdout open in their
        # child. PIPE + communicate() can hang until the clipboard is replaced.
        # A temporary file captures errors without waiting for child pipe EOF.
        with tempfile.TemporaryFile() as errors:
            try:
                proc = subprocess.run(command, input=text.encode("utf-8"),
                                      stdout=subprocess.DEVNULL, stderr=errors,
                                      timeout=5, check=False)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Clipboard publication timed out; no paste shortcut was sent") from exc
            if proc.returncode:
                errors.seek(0)
                detail = errors.read(1600).decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Clipboard command failed (exit={proc.returncode}): {detail}")

    def _copy(self, text: str) -> str:
        wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        if wayland:
            binary = shutil.which("wl-copy")
            if not binary:
                raise RuntimeError("Wayland session requires wl-copy (package wl-clipboard)")
            command = [binary, "--type", "text/plain;charset=utf-8"]
            self._copy_command(command, text)
            if self.paste_shortcut == "shift+insert":
                # Shift+Insert reads PRIMARY in some applications, CLIPBOARD in
                # others. Publish both only when that shortcut was selected.
                self._copy_command([binary, "--primary", "--type", "text/plain;charset=utf-8"], text)
            return "wl-copy"
        if not os.environ.get("DISPLAY"):
            raise RuntimeError("No graphical session: DISPLAY and WAYLAND_DISPLAY are empty")
        binary = shutil.which("xclip")
        if binary:
            self._copy_command([binary, "-selection", "clipboard", "-target", "UTF8_STRING"], text)
            if self.paste_shortcut == "shift+insert":
                self._copy_command([binary, "-selection", "primary", "-target", "UTF8_STRING"], text)
            return "xclip"
        binary = shutil.which("xsel")
        if binary:
            self._copy_command([binary, "--clipboard", "--input"], text)
            if self.paste_shortcut == "shift+insert":
                self._copy_command([binary, "--primary", "--input"], text)
            return "xsel"
        raise RuntimeError("X11 clipboard requires xclip or xsel")

    def insert(self, text: str, *, guard=None) -> str:
        if not text:
            return "empty"
        with self._lock:
            if self._closed:
                raise RuntimeError("Injector is closed")
            if guard:
                guard()
            # Initialize before touching focus/clipboard, not after wl-copy.
            self.keyboard.prepare()
            backend = self._copy(text)
            LOG.info("Clipboard published: backend=%s chars=%d", backend, len(text))
            # Do NOT run wl-paste here. Its focus-acquiring surface on GNOME
            # adds another focus round trip; reading data is not delivery proof.
            time.sleep(self.clipboard_settle_ms / 1000)
            if guard:
                guard()
            self.keyboard.send(self.paste_shortcut)
            detail = f"{self.keyboard.name}; {self.paste_shortcut}; application-unverified"
            LOG.info("Paste requested: %s; chars=%d", detail, len(text))
            return detail

    def undo(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Injector is closed")
            self.keyboard.send("ctrl+z")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self.keyboard.close()
