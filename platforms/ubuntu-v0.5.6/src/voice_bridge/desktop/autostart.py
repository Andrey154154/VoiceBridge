from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from ..platform_paths import runtime_command

NAME = "VoiceBridge"


def _linux_desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "voice-bridge.desktop"


def _desktop_exec_argument(value: str) -> str:
    """Desktop Entry Exec quoting is not shell quoting (single quotes are literal)."""
    if any(char in value for char in ('\n', '\r', '\0')):
        raise ValueError("Invalid newline/NUL in desktop executable path")
    # General Desktop Entry escaping is processed before Exec argument parsing.
    escaped = value.replace("%", "%%").replace("\\", "\\\\\\\\")
    for char in ('"', '`', '$'):
        escaped = escaped.replace(char, "\\\\" + char)
    return '"' + escaped + '"'


def is_enabled() -> bool:
    system = platform.system()
    if system == "Linux":
        return _linux_desktop_path().exists()
    if system == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, NAME)
            return True
        except (FileNotFoundError, OSError):
            return False
    return False


def set_enabled(enabled: bool) -> None:
    system = platform.system()
    command = runtime_command()
    if system == "Linux":
        path = _linux_desktop_path()
        if not enabled:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        exec_line = " ".join(_desktop_exec_argument(part) for part in command)
        path.write_text(
            "[Desktop Entry]\nType=Application\nName=Voice Bridge\n"
            f"Exec={exec_line} --background\n"
            "Terminal=false\nX-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )
        return
    if system == "Windows":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if enabled:
                cmdline = subprocess.list2cmdline(command + ["--background"])
                winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, cmdline)
            else:
                try:
                    winreg.DeleteValue(key, NAME)
                except FileNotFoundError:
                    pass
        return
    raise RuntimeError(f"Autostart is not supported on {system}")
