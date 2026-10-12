from __future__ import annotations
import argparse
import logging
import platform
import sys
from pathlib import Path
from .. import __version__
from ..config import load_config
from ..instance import UserInstanceLock
from ..platform_paths import default_config_path, log_dir
from .settings_store import ensure_user_config


def _configure_logging(level: str):
    handlers = [logging.FileHandler(log_dir() / 'voice-bridge.log', encoding='utf-8')]
    if sys.stderr is not None: handlers.append(logging.StreamHandler())
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s', handlers=handlers, force=True)


def _parser():
    p = argparse.ArgumentParser(prog='voice-bridge-desktop')
    p.add_argument('--config', default='')
    p.add_argument('--diagnostics', action='store_true', help='Test paste without loading the model')
    p.add_argument('--background', action='store_true', help='Start in the system tray')
    return p


def main(argv=None):
    args = _parser().parse_args(argv)
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette, QColor, QFont
    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
    from .theme import STYLE
    from .window import MainWindow
    path = Path(args.config).expanduser().resolve() if args.config else ensure_user_config()
    if not path.exists(): path = ensure_user_config()
    cfg = load_config(path); _configure_logging(cfg.app.log_level)
    app = QApplication(sys.argv[:1]); app.setApplicationName('Voice Bridge'); app.setApplicationVersion(__version__)
    app.setOrganizationName('VoiceBridge'); app.setQuitOnLastWindowClosed(False)
    app.setStyle('Fusion')
    palette = QPalette(); palette.setColor(QPalette.Window, QColor('#0c111b'))
    palette.setColor(QPalette.WindowText, QColor('#e8edf5')); palette.setColor(QPalette.Base, QColor('#0e1724'))
    palette.setColor(QPalette.Text, QColor('#e8edf5')); palette.setColor(QPalette.Button, QColor('#1c293c'))
    palette.setColor(QPalette.ButtonText, QColor('#e8edf5')); palette.setColor(QPalette.Highlight, QColor('#285e55'))
    palette.setColor(QPalette.HighlightedText, QColor('#ffffff')); app.setPalette(palette)
    app.setStyleSheet(STYLE)
    lock = UserInstanceLock('desktop')
    try: lock.acquire()
    except RuntimeError:
        QMessageBox.information(None, 'Voice Bridge', 'Приложение уже запущено. Откройте его через значок в трее.')
        return 0
    logging.getLogger(__name__).info('Desktop version=%s python=%s source=%s', __version__, sys.executable, __file__)
    try:
        window = MainWindow(cfg, path, diagnostics=args.diagnostics)
        app.applicationStateChanged.connect(window._publish_focus)
        app.focusWindowChanged.connect(window._publish_focus)
        app.aboutToQuit.connect(window.shutdown)
        if not args.background or not QSystemTrayIcon.isSystemTrayAvailable(): window.show()
        return app.exec()
    finally:
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
