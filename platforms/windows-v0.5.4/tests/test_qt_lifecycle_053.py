"""Native Qt widget/queued-signal test when PySide6 exists. Not a Win32 paste test."""
import os
import pytest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
pytest.importorskip('PySide6',reason='PySide6 is not installed in this test host')
from PySide6.QtWidgets import QApplication
from voice_bridge.config import Config,load_config
from voice_bridge.desktop.window import MainWindow
from voice_bridge.desktop.settings_store import save_config
from voice_bridge.desktop.preflight import check_ui_lifecycle


def test_real_qt_loading_save_ack_cancel(tmp_path):
    app=QApplication.instance() or QApplication([])
    cfg=Config();cfg.asr.model='';cfg.text.vocabulary_file=''
    path=tmp_path/'config.toml';save_config(cfg,path)
    window=MainWindow(load_config(path),path,start_engine=False)
    try:
        check_ui_lifecycle(window,app)
    finally:
        window.shutdown();window.close()
