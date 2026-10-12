from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

APP_SLUG = "voice-bridge"
APP_NAME = "Voice Bridge"


def config_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "VoiceBridge"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_SLUG


def data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "VoiceBridge"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_SLUG


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config_path() -> Path:
    return config_dir() / "config.toml"


def runtime_command() -> list[str]:
    if platform.system() == "Windows":
        configured = os.environ.get("VOICEBRIDGE_LAUNCHER", "")
        if configured and Path(configured).is_file():
            return [configured]
        launcher = Path(sys.executable).parent.parent / "VoiceBridge.exe"
        if launcher.is_file():
            return [str(launcher)]
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    # Resolving the venv Python symlink loses its virtual environment.
    if platform.system() == "Linux" and Path("/opt/voice-bridge/app/src").is_dir() and Path("/usr/bin/voice-bridge-desktop").exists():
        return ["/usr/bin/voice-bridge-desktop"]
    return [sys.executable, "-m", "voice_bridge.desktop.app"]
