from __future__ import annotations

import argparse
import glob
import importlib.metadata
import logging
import os
import sys
import time
from pathlib import Path

from .audio import list_audio_devices
from .config import load_config, resolve_optional_config_path
from .doctor import run_doctor


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="voice-bridge", description="Universal local push-to-talk dictation bridge")
    from . import __version__
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", default=None, help="Path to TOML config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Run push-to-talk desktop bridge")
    sub.add_parser("devices", help="List microphone and Linux evdev devices")
    sub.add_parser("doctor", help="Check runtime dependencies and permissions")
    sub.add_parser("model-info", help="Inspect configured Qwen3-ASR checkpoint format without loading weights")
    once = sub.add_parser("record-once", help="Record one utterance from the terminal and insert it")
    once.add_argument("--seconds", type=float, default=0.0, help="Auto-stop after N seconds; 0 waits for Enter")
    paste = sub.add_parser("test-paste", help="Test the delivery path without loading ASR")
    paste.add_argument("--text", default="Voice Bridge — проверка вставки 123.")
    paste.add_argument("--delay", type=float, default=5.0)
    sub.add_parser("delivery-info", help="Report delivery environment without loading the model")
    sub.add_parser("serve", help="Run localhost HTTP API")
    return parser


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def command_run(config) -> int:
    """Independent terminal entry: controller + trigger, no desktop worker/IPC.

    Restores the original CLI composition. Windows' trigger dispatches audio
    callbacks outside the low-level hook; the controller serializes ASR work.
    """
    from .factory import create_controller
    from .triggers import create_trigger
    from .instance import UserInstanceLock
    from dataclasses import asdict
    log = logging.getLogger(__name__)
    def report(state, detail=""):
        if state == "transcript":
            detail = f"{len(detail)} characters"
        log.log(logging.ERROR if state == "error" else logging.INFO, "CLI %s: %s", state, detail)
    with UserInstanceLock("engine"):
        controller = create_controller(config, state_callback=report)
        trigger = create_trigger(**asdict(config.trigger))
        try:
            prepare = getattr(controller.injector, "prepare", None)
            if prepare:
                log.info("%s", prepare())
            if hasattr(trigger, "on_ready"):
                trigger.on_ready = lambda: log.info("CLI ready. Hold the configured trigger to dictate.")
            trigger.run(controller.press, controller.release)
        except KeyboardInterrupt:
            pass
        finally:
            controller.cancel()
            stop = getattr(trigger, "stop", None)
            if stop:
                stop()
            close = getattr(controller.injector, "close", None)
            if close:
                close()
    return 0


def command_test_paste(config, text: str, delay: float) -> int:
    from .injectors import create_injector
    from .instance import UserInstanceLock
    from dataclasses import asdict
    if not 0 <= delay <= 30:
        raise ValueError("Test delay must be 0..30 seconds")
    with UserInstanceLock("engine"):
        injector = create_injector(**asdict(config.injector))
        try:
            prepare = getattr(injector, "prepare", None)
            if prepare:
                print(prepare(), flush=True)
            print(f"Click the target text field. Paste in {delay:g} seconds.", flush=True)
            time.sleep(delay)
            print("Paste requested:", injector.insert(text))
            print("Check the target field visually; command completion is not delivery verification.")
        finally:
            close = getattr(injector, "close", None)
            if close:
                close()
    return 0


def command_delivery_info(config) -> int:
    import shutil
    from dataclasses import asdict
    from . import __version__
    print(f"Voice Bridge {__version__}")
    print(f"Python: {sys.executable}\nModule: {__file__}\nConfig: {config.source_path}")
    for name in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP", "YDOTOOL_SOCKET"):
        print(f"{name}: {os.environ.get(name, '(unset)')}")
    for name, value in asdict(config.injector).items():
        print(f"injector.{name}: {value}")
    print(f"/dev/uinput read/write: {os.access('/dev/uinput', os.R_OK | os.W_OK)}")
    for name in ("wl-copy", "xclip", "xsel", "ydotool"):
        print(f"{name}: {shutil.which(name) or '(not installed)'}")
    return 0


def command_devices() -> int:
    print("Microphones:")
    try:
        for item in list_audio_devices():
            print(f"  [{item['index']}] {item['name']} (inputs={item['max_input_channels']}, default_sr={item['default_samplerate']:.0f})")
    except Exception as exc:
        print(f"  ERROR: {exc}")
    if sys.platform.startswith("linux"):
        print("\nEvdev devices:")
        try:
            from .triggers.evdev_trigger import EvdevHoldTrigger
            devices = EvdevHoldTrigger.devices()
            if devices:
                for path, name in devices:
                    print(f"  {path}: {name}")
            else:
                nodes = sorted(glob.glob("/dev/input/event*"))
                readable = [path for path in nodes if os.access(path, os.R_OK)]
                if nodes and not readable:
                    print(f"  PERMISSION: found {len(nodes)} /dev/input/event* nodes, but none are readable")
                    print("  Hint: the current login session needs access to the physical input device(s).")
                elif not nodes:
                    print("  NONE: no /dev/input/event* nodes found")
                else:
                    print("  NONE: evdev returned no devices")
        except PermissionError as exc:
            print(f"  PERMISSION: {exc}")
        except Exception as exc:
            print(f"  ERROR: {exc}")
    return 0


def command_doctor() -> int:
    checks = run_doctor()
    for item in checks:
        mark = "OK" if item.ok else "MISS"
        print(f"[{mark:4}] {item.name}: {item.detail}")
    return 0 if all(c.ok for c in checks if c.name not in {"Wayland session"}) else 2


def command_model_info(config) -> int:
    from .asr.qwen import is_hf_native_qwen3_asr, read_local_model_config

    raw = read_local_model_config(config.asr.model)
    print(f"Model: {config.asr.model}")
    print(f"Configured backend: {config.asr.backend}")
    print(f"Placement policy: {config.asr.placement}")
    print(f"GPU reserve: {config.asr.gpu_reserve_gb:.2f} GiB")
    print(f"Minimum GPU offload budget: {config.asr.min_gpu_offload_gb:.2f} GiB")
    print(f"CPU memory cap: {config.asr.cpu_max_memory_gb:.2f} GiB")
    print(f"HF-native Qwen3-ASR: {is_hf_native_qwen3_asr(config.asr.model, raw)}")
    if raw is not None:
        print(f"model_type: {raw.get('model_type')}")
        print(f"has top-level audio_config: {isinstance(raw.get('audio_config'), dict)}")
        print(f"has top-level text_config: {isinstance(raw.get('text_config'), dict)}")
    for package in ("transformers", "torch", "accelerate", "qwen-asr"):
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "not installed"
        print(f"{package}: {version}")
    return 0


def command_record_once(config, seconds: float) -> int:
    from .factory import create_controller

    controller = create_controller(config)
    controller.press()
    if seconds > 0:
        print(f"Recording for {seconds:.1f}s...")
        time.sleep(seconds)
    else:
        input("Recording... press Enter to stop and insert: ")
    result = controller.release_sync()
    if result is not None:
        print(f"Recognized: {result.final_text}")
    return 0


def command_serve(config) -> int:
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("API dependencies are missing. Install with pip install -e '.[api]'") from exc
    from .asr.qwen import QwenASRProvider
    from .api import create_api
    from .text_engine import TextEngine

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
    text_engine = TextEngine(
        vocabulary_file=resolve_optional_config_path(config, config.text.vocabulary_file),
        capitalize_sentences=config.text.capitalize_sentences,
    )
    app = create_api(asr, text_engine, mode=config.app.mode, language=config.app.language)
    uvicorn.run(app, host=config.api.host, port=config.api.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from .platform_paths import default_config_path
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    if not args.config and not config_path.exists():
        config_path = Path(__file__).resolve().parents[2] / "config/default.toml"
    if not config_path.exists():
        candidate = Path(__file__).resolve().parents[2] / (args.config or "config/default.toml")
        if candidate.exists():
            config_path = candidate
    config = load_config(config_path)
    _configure_logging(config.app.log_level)
    if args.command == "test-paste":
        return command_test_paste(config, args.text, args.delay)
    if args.command == "delivery-info":
        return command_delivery_info(config)
    if args.command == "run":
        return command_run(config)
    if args.command == "devices":
        return command_devices()
    if args.command == "doctor":
        return command_doctor()
    if args.command == "model-info":
        return command_model_info(config)
    if args.command == "record-once":
        return command_record_once(config, args.seconds)
    if args.command == "serve":
        return command_serve(config)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
