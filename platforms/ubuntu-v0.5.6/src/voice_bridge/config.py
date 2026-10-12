from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(slots=True)
class AppConfig:
    mode: str = "dictation"
    language: str | None = "Russian"
    log_level: str = "INFO"
    beep: bool = False
    # Active-at-delivery matches the original CLI; binding to recording is opt-in.
    paste_target: str = "active"
    start_model_on_launch: bool = True
    models_directory: str = ""
    download_repo: str = "Qwen/Qwen3-ASR-1.7B-hf"


@dataclass(slots=True)
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    dtype: str = "float32"
    input_device: str | int | None = None
    max_seconds: float = 120.0


@dataclass(slots=True)
class ASRConfig:
    provider: str = "qwen"
    backend: str = "auto"
    model: str = "Qwen/Qwen3-ASR-1.7B-hf"
    device: str = "cuda:0"
    dtype: str = "bfloat16"
    max_new_tokens: int = 512
    prompt: str = ""
    placement: str = "adaptive"
    gpu_reserve_gb: float = 1.5
    min_gpu_offload_gb: float = 1.0
    cpu_max_memory_gb: float = 96.0


@dataclass(slots=True)
class TextConfig:
    vocabulary_file: str = "config/vocabulary.txt"
    capitalize_sentences: bool = True


@dataclass(slots=True)
class TriggerConfig:
    backend: str = "auto"
    kind: str = "mouse"
    button: str = "x1"
    key: str = "f8"
    evdev_device_match: str = ""
    evdev_code: str = "BTN_SIDE"
    consume: bool = False


@dataclass(slots=True)
class InjectorConfig:
    backend: str = "auto"
    paste_shortcut: str = "ctrl+v"
    keyboard_backend: str = "auto"
    ydotool_syntax: str = "auto"
    key_delay_ms: int = 25
    clipboard_settle_ms: int = 350


@dataclass(slots=True)
class APIConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(slots=True)
class Config:
    app: AppConfig = field(default_factory=AppConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    text: TextConfig = field(default_factory=TextConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    injector: InjectorConfig = field(default_factory=InjectorConfig)
    api: APIConfig = field(default_factory=APIConfig)
    source_path: Path | None = None


def _clean_device(value: object) -> str | int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (str, int)):
        return value
    raise ValueError("audio.input_device must be a string, integer, or empty")


def load_config(path: str | Path) -> Config:
    source = Path(path).expanduser().resolve()
    with source.open("rb") as fh:
        data = tomllib.load(fh)

    app = AppConfig(**data.get("app", {}))
    audio_data = dict(data.get("audio", {}))
    audio_data["input_device"] = _clean_device(audio_data.get("input_device"))
    audio = AudioConfig(**audio_data)
    asr = ASRConfig(**data.get("asr", {}))
    text = TextConfig(**data.get("text", {}))
    if not isinstance(text.vocabulary_file, str):
        raise ValueError("text.vocabulary_file must be a file path or an empty string")
    text.vocabulary_file = text.vocabulary_file.strip()
    trigger = TriggerConfig(**data.get("trigger", {}))
    injector = InjectorConfig(**data.get("injector", {}))
    api = APIConfig(**data.get("api", {}))

    if app.paste_target not in {"active", "window", "strict"}:
        raise ValueError("Invalid app.paste_target: active, window or strict required")
    if app.mode not in {"raw", "dictation", "smart"}:
        raise ValueError(f"Unsupported app.mode: {app.mode}")
    if audio.sample_rate <= 0 or audio.channels != 1:
        raise ValueError("MVP requires positive sample_rate and mono audio")
    if audio.max_seconds <= 0:
        raise ValueError("audio.max_seconds must be > 0")
    if asr.placement not in {"adaptive", "gpu", "cpu"}:
        raise ValueError(f"Unsupported asr.placement: {asr.placement}")
    if asr.gpu_reserve_gb < 0 or asr.min_gpu_offload_gb < 0 or asr.cpu_max_memory_gb <= 0:
        raise ValueError("Invalid ASR memory policy")

    if injector.paste_shortcut not in {"ctrl+v", "ctrl+shift+v", "shift+insert"}:
        raise ValueError("Invalid injector.paste_shortcut")
    if injector.keyboard_backend not in {"auto", "uinput", "ydotool", "pynput"}:
        raise ValueError("Invalid injector.keyboard_backend")
    if injector.ydotool_syntax not in {"auto", "modern", "legacy"}:
        raise ValueError("Invalid injector.ydotool_syntax")
    if not 5 <= injector.key_delay_ms <= 200 or not 100 <= injector.clipboard_settle_ms <= 2000:
        raise ValueError("Invalid injection timing: key_delay_ms=5..200; clipboard_settle_ms=100..2000")

    return Config(
        app=app,
        audio=audio,
        asr=asr,
        text=text,
        trigger=trigger,
        injector=injector,
        api=api,
        source_path=source,
    )


def resolve_config_path(config: Config, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    if config.source_path is None:
        return path.resolve()
    return (config.source_path.parent.parent / path).resolve()


def resolve_optional_config_path(config: Config, value: str | None) -> Path | None:
    """Keep disabled optional files disabled; Path('') otherwise means a directory.

    Non-empty relative paths keep the original CLI/project resolution rules.
    """
    if value is None or not value.strip():
        return None
    return resolve_config_path(config, value.strip())
