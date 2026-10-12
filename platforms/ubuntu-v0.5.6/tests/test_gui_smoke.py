"""Optional REAL Qt smoke, intentionally skipped where Qt is unavailable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtWidgets import QApplication
from voice_bridge.config import Config, load_config
from voice_bridge.desktop.settings_store import save_config
from voice_bridge.desktop.window import MainWindow
from voice_bridge.desktop.theme import STYLE

@pytest.mark.parametrize('width,height',[(820,580),(1000,700),(1140,850)])
def test_actual_qt_all_pages_construct_without_runtime(tmp_path,monkeypatch,width,height):
    monkeypatch.setattr('voice_bridge.desktop.window.list_audio_devices',lambda:[])
    monkeypatch.setattr('voice_bridge.desktop.autostart.is_enabled',lambda:False)
    cfg=Config();cfg.asr.model='';path=tmp_path/'config.toml';save_config(cfg,path)
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    window=MainWindow(load_config(path),path,start_engine=False)
    window.resize(width,height);window.show();app.processEvents()
    assert window.pages.count()==4 and window.catalog_choice.count()==2
    assert window.runtime is None and window._download_thread is None
    for index in range(4):
        window._navigate(index);app.processEvents()
        assert window.pages.currentIndex()==index
        assert window.pages.currentWidget().horizontalScrollBar().maximum()==0
    window.shutdown();window._allow_close=True;window.close()
