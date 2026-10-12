import json
from pathlib import Path

from voice_bridge.asr.qwen import (
    _version_tuple,
    is_hf_native_qwen3_asr,
    read_local_model_config,
)


def native_config():
    return {
        "model_type": "qwen3_asr",
        "audio_config": {"model_type": "qwen3_asr_encoder"},
        "text_config": {"model_type": "qwen3"},
    }


def test_hf_native_layout_detected():
    assert is_hf_native_qwen3_asr("unused", native_config()) is True


def test_legacy_layout_rejected():
    legacy = {
        "model_type": "qwen3_asr",
        "thinker_config": {"text_config": {}},
    }
    assert is_hf_native_qwen3_asr("unused", legacy) is False


def test_hf_name_detected_without_local_config():
    assert is_hf_native_qwen3_asr("Qwen/Qwen3-ASR-1.7B-hf", None) is True


def test_read_local_model_config(tmp_path: Path):
    model = tmp_path / "model"
    model.mkdir()
    config = native_config()
    (model / "config.json").write_text(json.dumps(config), encoding="utf-8")
    assert read_local_model_config(str(model)) == config


def test_version_tuple():
    assert _version_tuple("5.13.1") >= (5, 13, 0)
    assert _version_tuple("4.57.6") < (5, 13, 0)
    assert _version_tuple("5.13.0.dev0") >= (5, 13, 0)

from voice_bridge.asr.qwen import choose_placement_plan


def test_adaptive_falls_back_to_cpu_when_vram_is_full():
    plan = choose_placement_plan(
        cuda_available=True,
        requested_device='cuda:0',
        placement='adaptive',
        gpu_free_bytes=55 * 1024 * 1024,
        gpu_reserve_gb=1.5,
        min_gpu_offload_gb=1.0,
        cpu_max_memory_gb=96,
    )
    assert plan.mode == 'cpu-fallback'
    assert plan.device_map == {'': 'cpu'}


def test_adaptive_uses_gpu_cpu_split_when_some_vram_is_available():
    plan = choose_placement_plan(
        cuda_available=True,
        requested_device='cuda:0',
        placement='adaptive',
        gpu_free_bytes=5 * 1024 ** 3,
        gpu_reserve_gb=1.5,
        min_gpu_offload_gb=1.0,
        cpu_max_memory_gb=96,
    )
    assert plan.mode == 'gpu+cpu'
    assert plan.device_map == 'auto'
    assert plan.max_memory[0].endswith('MiB')
    assert plan.max_memory['cpu'] == '96GiB'


def test_cpu_placement_works_without_cuda():
    plan = choose_placement_plan(
        cuda_available=False,
        requested_device='cuda:0',
        placement='adaptive',
    )
    assert plan.mode == 'cpu'
    assert plan.device_map == {'': 'cpu'}
