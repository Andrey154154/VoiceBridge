"""Installer preflight: imports, version contracts, and ABI, not desktop E2E."""
import argparse
import ctypes
import importlib.metadata
import platform
import sys
p=argparse.ArgumentParser();p.add_argument('--asr',action='store_true');args=p.parse_args()
assert sys.platform=='win32' and ctypes.sizeof(ctypes.c_void_p)==8, 'Windows x64 required'
from PySide6 import QtCore, QtGui, QtWidgets
import sounddevice
from voice_bridge.desktop.window import MainWindow
from voice_bridge.models import CATALOG, ModelDownloader
assert len(CATALOG) == 2
from voice_bridge.win32 import INPUT
from voice_bridge.injectors.windows import WindowsTextInjector
from voice_bridge.triggers.windows_hook import WindowsMouseHookTrigger
assert ctypes.sizeof(INPUT)==40
injector=WindowsTextInjector();print(injector.prepare());injector.close()
print('Python:',platform.python_version())
for name in ('voice-input-bridge','PySide6','numpy','sounddevice'):
    print(name,importlib.metadata.version(name))
if args.asr:
    import torch
    from transformers import AutoModelForMultimodalLM,AutoProcessor,AutoConfig
    AutoConfig.for_model('qwen3_asr')
    import accelerate,librosa
    print('Torch:',torch.__version__,'CUDA build:',torch.version.cuda,'GPU available:',torch.cuda.is_available())
    print('HF-native Qwen3-ASR API: import OK; model/inference not tested')
# Construct all UI pages without a model, network, microphone stream, or worker.
import tempfile
from pathlib import Path
from voice_bridge.config import Config, load_config
from voice_bridge.desktop.settings_store import save_config
from voice_bridge.desktop.theme import STYLE
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
app.setStyle('Fusion'); app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory(prefix='vb-ui-check-') as folder:
    cfg = Config(); cfg.asr.model = ''; cfg.text.vocabulary_file = ''
    cfg_path = Path(folder) / 'config.toml'
    save_config(cfg, cfg_path)
    # Test the optional resource contract, not just construction of widgets.
    from voice_bridge.config import resolve_optional_config_path
    from voice_bridge.text_engine import TextEngine
    loaded = load_config(cfg_path)
    assert loaded.text.vocabulary_file == ''
    assert resolve_optional_config_path(loaded, loaded.text.vocabulary_file) is None
    text_engine = TextEngine(vocabulary_file=resolve_optional_config_path(loaded, loaded.text.vocabulary_file))
    assert text_engine.vocabulary == [] and text_engine.vocabulary_warning is None
    assert text_engine.process('hello comma', mode='raw').text == 'hello comma'
    bad_file = TextEngine(vocabulary_file=Path(folder))
    assert bad_file.vocabulary == [] and bad_file.vocabulary_warning
    print('Optional vocabulary: empty disables; directory warns without crash')
    window = MainWindow(load_config(cfg_path), cfg_path, start_engine=False)
    assert window.pages.count() == 4
    assert window.catalog_choice.count() == 2
    assert window.vocabulary_file.text() == ''
    for index in range(4):
        window._navigate(index)
        window.ensurePolished()
        app.processEvents()
    try:
        from voice_bridge.desktop.preflight import check_ui_lifecycle
        check_ui_lifecycle(window, app)
        print('Qt lifecycle: queued slots, loading/save guard, live settings ACK, cancellation passed')
    finally:
        window.shutdown(); window._allow_close = True; window.close()
print('Desktop widget construction: passed (no dictation or model network request)')
print('PREFLIGHT PASSED (not a live microphone/paste test)')
