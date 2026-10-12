from __future__ import annotations

import importlib.metadata
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .base import Transcription

LOG = logging.getLogger(__name__)
MIN_TRANSFORMERS = (5, 13, 0)
_GIB = 1024 ** 3


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in version.split('.'):
        digits = ''.join(ch for ch in item if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def read_local_model_config(model: str) -> dict[str, Any] | None:
    path = Path(model).expanduser()
    if not path.is_dir():
        return None
    config_path = path / 'config.json'
    if not config_path.is_file():
        raise RuntimeError(f'Local model directory has no config.json: {path}')
    try:
        data = json.loads(config_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'Cannot read model config {config_path}: {exc}') from exc
    if not isinstance(data, dict):
        raise RuntimeError(f'Model config must be a JSON object: {config_path}')
    return data


def is_hf_native_qwen3_asr(model: str, data: dict[str, Any] | None = None) -> bool:
    if data is None:
        data = read_local_model_config(model)
    if data is not None:
        return (
            data.get('model_type') == 'qwen3_asr'
            and isinstance(data.get('audio_config'), dict)
            and isinstance(data.get('text_config'), dict)
        )
    return model.lower().endswith('-hf')


def transformers_version() -> str | None:
    try:
        return importlib.metadata.version('transformers')
    except importlib.metadata.PackageNotFoundError:
        return None


def _cuda_index(device: str) -> int:
    if ':' not in device:
        return 0
    try:
        return int(device.split(':', 1)[1])
    except ValueError as exc:
        raise ValueError(f'Invalid CUDA device: {device}') from exc


@dataclass(frozen=True, slots=True)
class PlacementPlan:
    mode: str
    device_map: Any
    max_memory: dict[Any, str] | None
    free_gpu_gb: float
    usable_gpu_gb: float


def choose_placement_plan(
    *,
    cuda_available: bool,
    requested_device: str,
    placement: str,
    gpu_free_bytes: int = 0,
    gpu_reserve_gb: float = 1.5,
    min_gpu_offload_gb: float = 1.0,
    cpu_max_memory_gb: float = 96.0,
) -> PlacementPlan:
    placement = placement.lower().strip()
    if placement not in {'adaptive', 'gpu', 'cpu'}:
        raise ValueError(f'Unsupported ASR placement: {placement!r}')
    if gpu_reserve_gb < 0 or min_gpu_offload_gb < 0 or cpu_max_memory_gb <= 0:
        raise ValueError('ASR memory limits must be non-negative and CPU max memory must be > 0')

    if placement == 'cpu' or not requested_device.startswith('cuda'):
        return PlacementPlan('cpu', {'': 'cpu'}, None, 0.0, 0.0)
    if not cuda_available:
        if placement == 'gpu':
            raise RuntimeError('GPU placement requested but CUDA is unavailable')
        return PlacementPlan('cpu', {'': 'cpu'}, None, 0.0, 0.0)

    index = _cuda_index(requested_device)
    free_gb = gpu_free_bytes / _GIB
    usable_gb = max(0.0, free_gb - gpu_reserve_gb)

    if placement == 'gpu':
        return PlacementPlan('gpu', {'': index}, None, free_gb, usable_gb)

    if usable_gb < min_gpu_offload_gb:
        return PlacementPlan('cpu-fallback', {'': 'cpu'}, None, free_gb, usable_gb)

    # Allow Accelerate to place as much as safely fits on the GPU and spill the
    # remainder into system RAM. Disk offload is intentionally not used here.
    usable_mib = max(256, int(usable_gb * 1024))
    cpu_gib = max(1, int(cpu_max_memory_gb))
    return PlacementPlan(
        'gpu+cpu',
        'auto',
        {index: f'{usable_mib}MiB', 'cpu': f'{cpu_gib}GiB'},
        free_gb,
        usable_gb,
    )


class QwenASRProvider:
    """HF-native Qwen3-ASR provider with adaptive GPU/CPU placement."""

    def __init__(
        self,
        *,
        model: str,
        device: str = 'cuda:0',
        dtype: str = 'bfloat16',
        max_new_tokens: int = 512,
        backend: str = 'hf_native',
        prompt: str | None = None,
        placement: str = 'adaptive',
        gpu_reserve_gb: float = 1.5,
        min_gpu_offload_gb: float = 1.0,
        cpu_max_memory_gb: float = 96.0,
    ) -> None:
        if backend not in {'auto', 'hf', 'hf_native', 'transformers'}:
            raise RuntimeError(
                f'Unsupported ASR backend {backend!r}. v0.2 supports only HF-native Qwen3-ASR.'
            )

        raw = read_local_model_config(model)
        if not is_hf_native_qwen3_asr(model, raw):
            raise RuntimeError(
                f'{model!r} is not an HF-native Qwen3-ASR checkpoint. '
                'Use a Qwen3-ASR-*-hf checkpoint with top-level audio_config/text_config.'
            )

        version = transformers_version()
        if version is None or _version_tuple(version) < MIN_TRANSFORMERS:
            found = version or 'not installed'
            raise RuntimeError(
                f'HF-native Qwen3-ASR requires transformers>=5.13.0; found {found}. '
                'Install the ASR runtime using the Voice Bridge installer.'
            )

        try:
            import torch
            from transformers import AutoModelForMultimodalLM, AutoProcessor
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                'HF-native Qwen3-ASR dependencies are incomplete. Rebuild the project venv.'
            ) from exc

        dtype_map = {
            'bfloat16': torch.bfloat16,
            'float16': torch.float16,
            'float32': torch.float32,
        }
        if dtype not in dtype_map:
            raise ValueError(f'Unsupported ASR dtype: {dtype}')

        cuda_available = bool(torch.cuda.is_available())
        free_bytes = 0
        if device.startswith('cuda') and cuda_available:
            index = _cuda_index(device)
            try:
                free_bytes, _ = torch.cuda.mem_get_info(index)
            except Exception:  # pragma: no cover - conservative fallback
                free_bytes = 0

        plan = choose_placement_plan(
            cuda_available=cuda_available,
            requested_device=device,
            placement=placement,
            gpu_free_bytes=free_bytes,
            gpu_reserve_gb=gpu_reserve_gb,
            min_gpu_offload_gb=min_gpu_offload_gb,
            cpu_max_memory_gb=cpu_max_memory_gb,
        )

        self._torch = torch
        requested_dtype = dtype_map[dtype]
        # CPU-only inference is kept on float32 for maximum kernel compatibility.
        # The 1.7B model still fits comfortably in system RAM.
        if plan.mode in {'cpu', 'cpu-fallback'} and requested_dtype is not torch.float32:
            LOG.warning('CPU ASR fallback uses float32 for compatibility instead of %s.', dtype)
            self._dtype = torch.float32
        else:
            self._dtype = requested_dtype
        self._max_new_tokens = max_new_tokens
        self._prompt = prompt.strip() if prompt else None
        self._placement_mode = plan.mode

        local = Path(model).expanduser().is_dir()
        load_kwargs: dict[str, Any] = {'local_files_only': True} if local else {}
        load_kwargs['trust_remote_code'] = False
        load_kwargs['device_map'] = plan.device_map
        if plan.max_memory is not None:
            load_kwargs['max_memory'] = plan.max_memory

        if plan.mode == 'cpu-fallback':
            LOG.warning(
                'Only %.2f GiB GPU memory is free (%.2f GiB usable after reserve); '
                'loading Qwen3-ASR into system RAM/CPU.',
                plan.free_gpu_gb,
                plan.usable_gpu_gb,
            )
        elif plan.mode == 'gpu+cpu':
            LOG.info(
                'Adaptive Qwen3-ASR placement: %.2f GiB GPU free, %.2f GiB usable; '
                'Accelerate may spill remaining layers into system RAM.',
                plan.free_gpu_gb,
                plan.usable_gpu_gb,
            )
        else:
            LOG.info('Qwen3-ASR placement mode: %s', plan.mode)

        LOG.info('Loading HF-native Qwen3-ASR model %s', model)
        self._processor = AutoProcessor.from_pretrained(model, trust_remote_code=False, **({'local_files_only': True} if local else {}))
        self._model = AutoModelForMultimodalLM.from_pretrained(
            model,
            dtype=self._dtype,
            **load_kwargs,
        ).eval()

        # The official Qwen3-ASR HF example moves processor output to
        # model.device/model.dtype even with device_map="auto". Accelerate's
        # hooks then route tensors across any CPU/GPU split during generation.
        self._input_device = self._model.device
        self._input_dtype = self._model.dtype
        device_map = getattr(self._model, 'hf_device_map', None)
        if device_map:
            devices = sorted({str(value) for value in device_map.values()})
            LOG.info('HF device map active across: %s', ', '.join(devices))
        LOG.info(
            'HF-native Qwen3-ASR model loaded; input device=%s dtype=%s placement=%s',
            self._input_device,
            self._input_dtype,
            self._placement_mode,
        )

    def transcribe(
        self,
        samples: np.ndarray,
        sample_rate: int,
        language: str | None = None,
    ) -> Transcription:
        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim != 1:
            audio = audio.reshape(-1)
        if audio.size == 0:
            return Transcription(text='', language=language)

        if sample_rate != 16000:
            raise ValueError(f'Qwen3-ASR expects 16000 Hz audio; received {sample_rate} Hz')
        # Qwen3ASRProcessorKwargs already defaults audio_kwargs.sampling_rate to
        # 16000. Passing sampling_rate as a top-level apply_chat_template kwarg
        # produces a Transformers 5.x deprecation warning.
        kwargs: dict[str, Any] = {
            'audio': audio,
        }
        if language:
            kwargs['language'] = language
        if self._prompt:
            kwargs['prompt'] = self._prompt

        inputs = self._processor.apply_transcription_request(**kwargs)
        inputs = inputs.to(self._input_device, self._input_dtype)

        with self._torch.inference_mode():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=self._max_new_tokens,
                do_sample=False,
            )

        generated_ids = output_ids[:, inputs['input_ids'].shape[1]:]
        parsed = self._processor.decode(generated_ids, return_format='parsed')[0]
        if isinstance(parsed, dict):
            text = str(parsed.get('transcription', '')).strip()
            detected_language = parsed.get('language') or language
        else:
            text = str(parsed).strip()
            detected_language = language
        return Transcription(text=text, language=detected_language)
