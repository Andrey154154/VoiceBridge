from __future__ import annotations

import shutil
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from ..config import Config, load_config
from ..platform_paths import default_config_path


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _value(value) -> str:
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _quote(value)
    return str(value)


def save_config(config: Config, path: str | Path) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    sections = [
        ("app", config.app), ("audio", config.audio), ("asr", config.asr),
        ("text", config.text), ("trigger", config.trigger),
        ("injector", config.injector), ("api", config.api),
    ]
    lines = ["# Voice Bridge Desktop configuration", ""]
    for name, obj in sections:
        lines.append(f"[{name}]")
        for key, value in asdict(obj).items():
            lines.append(f"{key} = {_value(value)}")
        lines.append("")
    fd, temporary = tempfile.mkstemp(prefix=".voice-bridge-config-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines))
            stream.flush()
            os.fsync(stream.fileno())
        # Parse/validate before replacing the current profile.
        load_config(temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def ensure_user_config(template: str | Path | None = None) -> Path:
    target = default_config_path()
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if template:
        source = Path(template).expanduser()
        if source.is_file():
            shutil.copy2(source, target)
            return target
    # Deliberately generic defaults: model/mic are selected in the desktop UI.
    from ..config import Config
    cfg = Config()
    cfg.trigger.consume = True
    cfg.asr.model = ""
    if os.name == "nt":
        cfg.trigger.backend = "windows_hook"
        cfg.injector.backend = "windows"
        cfg.asr.backend = "hf_native"
        cfg.asr.cpu_max_memory_gb = 16.0
    cfg.text.vocabulary_file = ""
    save_config(cfg, target)
    return target


def reload_config(path: str | Path) -> Config:
    return load_config(path)
