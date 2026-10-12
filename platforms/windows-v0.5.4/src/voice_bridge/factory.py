from __future__ import annotations

from typing import Callable

from .asr.qwen import QwenASRProvider
from .audio import AudioRecorder
from .config import Config, resolve_optional_config_path
from .controller import VoiceBridgeController
from .injectors import create_injector
from .text_engine import TextEngine


def create_controller(
    config: Config,
    *,
    injector_override=None,
    state_callback: Callable[[str, str], None] | None = None,
) -> VoiceBridgeController:
    if config.asr.provider != "qwen":
        raise ValueError(f"Unsupported ASR provider: {config.asr.provider}")
    if state_callback:
        state_callback("model_stage", "Проверка словаря и параметров записи")
    # Optional text resources are checked before allocating/loading a model.
    # A disabled vocabulary must never be resolved to the configuration directory.
    vocabulary_path = resolve_optional_config_path(config, config.text.vocabulary_file)
    text_engine = TextEngine(
        vocabulary_file=vocabulary_path,
        capitalize_sentences=config.text.capitalize_sentences,
    )
    if text_engine.vocabulary_warning and state_callback:
        state_callback("warning", text_engine.vocabulary_warning)
    recorder = AudioRecorder(
        sample_rate=config.audio.sample_rate,
        device=config.audio.input_device,
        dtype=config.audio.dtype,
        max_seconds=config.audio.max_seconds,
    )
    if state_callback:
        state_callback("model_stage", "Импорт PyTorch / Transformers и загрузка выбранной модели")
    asr = QwenASRProvider(
        model=config.asr.model,
        backend=config.asr.backend,
        device=config.asr.device,
        dtype=config.asr.dtype,
        max_new_tokens=config.asr.max_new_tokens,
        prompt=config.asr.prompt,
        placement=config.asr.placement,
        gpu_reserve_gb=config.asr.gpu_reserve_gb,
        min_gpu_offload_gb=config.asr.min_gpu_offload_gb,
        cpu_max_memory_gb=config.asr.cpu_max_memory_gb,
    )
    if state_callback:
        state_callback("model_stage", "Модель загружена; подготовка контроллера диктовки")
    injector = injector_override or create_injector(
        backend=config.injector.backend,
        paste_shortcut=config.injector.paste_shortcut,
        keyboard_backend=config.injector.keyboard_backend,
        ydotool_syntax=config.injector.ydotool_syntax,
        key_delay_ms=config.injector.key_delay_ms,
        clipboard_settle_ms=config.injector.clipboard_settle_ms,
    )
    return VoiceBridgeController(
        recorder=recorder,
        asr=asr,
        text_engine=text_engine,
        injector=injector,
        mode=config.app.mode,
        language=config.app.language,
        state_callback=state_callback,
    )
