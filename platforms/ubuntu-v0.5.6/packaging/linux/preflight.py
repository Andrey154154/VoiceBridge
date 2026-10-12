#!/usr/bin/env python3
"""Ubuntu preflight. No model download, ASR inference, microphone stream or input grab."""
from __future__ import annotations
import argparse
import importlib.metadata
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--asr', action='store_true')
    p.add_argument('--devices', action='store_true')
    p.add_argument('--config')
    args = p.parse_args(argv)
    from voice_bridge import __version__
    from voice_bridge.config import Config, load_config
    from voice_bridge.desktop.settings_store import save_config
    print(f'Voice Bridge {__version__}; Python {platform.python_version()}; {sys.executable}', flush=True)
    for name in ('numpy', 'sounddevice', 'evdev', 'PySide6'):
        print(f'{name}: {importlib.metadata.version(name)}', flush=True)
    import sounddevice, evdev
    # This process owns its QApplication. Never reuse an active desktop session's GUI.
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    from voice_bridge.desktop.window import MainWindow
    from voice_bridge.desktop.theme import STYLE
    from voice_bridge.desktop.preflight import check_ui_lifecycle
    app = QApplication([])
    app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory(prefix='voice-bridge-preflight-') as folder:
        cfg = Config(); cfg.asr.model = ''; cfg.text.vocabulary_file = ''
        path = Path(folder) / 'config.toml'; save_config(cfg, path)
        window = MainWindow(load_config(path), path, start_engine=False)
        try:
            assert window.pages.count() == 4 and window.catalog_choice.count() == 2
            for page in range(4):
                window._navigate(page); window.ensurePolished(); app.processEvents()
            check_ui_lifecycle(window, app)
            print('Qt: four pages and lifecycle handlers passed (offscreen, not a paste test)', flush=True)
        finally:
            window.shutdown(); window._allow_close = True; window.close()
    if args.asr:
        import torch, accelerate, librosa
        from transformers import AutoModelForMultimodalLM, AutoProcessor, AutoConfig
        AutoConfig.for_model('qwen3_asr')
        print(f'ASR imports passed; Torch {torch.__version__}; CUDA available={torch.cuda.is_available()}', flush=True)
        print('Model not loaded; audio/inference NOT tested.', flush=True)
    warnings = []
    if args.devices:
        cfg = load_config(args.config) if args.config and Path(args.config).exists() else Config()
        wayland = bool(os.environ.get('WAYLAND_DISPLAY')) or os.environ.get('XDG_SESSION_TYPE','').lower() == 'wayland'
        print(f'Session: {os.environ.get("XDG_SESSION_TYPE", "unknown")}; Wayland={wayland}', flush=True)
        if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
            warnings.append('Нет графического сеанса. Запускайте приложение в терминале рабочего стола.')
        for tool in (['wl-copy'] if wayland else ['xclip']):
            print(f'{tool}: {shutil.which(tool) or "NOT FOUND"}')
            if not shutil.which(tool): warnings.append(f'Не найдена команда {tool}')
        use_uinput = cfg.injector.keyboard_backend == 'uinput' or (cfg.injector.keyboard_backend == 'auto' and wayland)
        if use_uinput or cfg.trigger.consume:
            rw = os.access('/dev/uinput', os.R_OK | os.W_OK)
            print(f'/dev/uinput R/W: {rw}')
            if not rw: warnings.append('Нет доступа к /dev/uinput; выполните bash repair-input.sh.')
        from voice_bridge.triggers.evdev_trigger import EvdevHoldTrigger
        try:
            dev, code = EvdevHoldTrigger(device_match=cfg.trigger.evdev_device_match,
                                         code=cfg.trigger.evdev_code)._select_device()
            print(f'Trigger readable: {dev.path} — {dev.name}; code={code}; NO GRAB')
            dev.close()
        except RuntimeError as exc:
            warnings.append(str(exc))
    for warning in warnings:
        print('ВНИМАНИЕ: ' + warning, flush=True)
    print(('DEVICE CHECK WARNINGS' if warnings else 'PREFLIGHT PASSED') + '. Реальная вставка проверяется в приложении, не этим скриптом.', flush=True)
    return 2 if warnings else 0

if __name__ == '__main__':
    raise SystemExit(main())
