"""Windowed Windows launcher with visible fatal errors instead of silent exit."""
from __future__ import annotations
import sys
import traceback
from ..platform_paths import log_dir

def main():
    try:
        from .app import main as run
        return run()
    except Exception:
        detail=traceback.format_exc()
        path=log_dir()/'startup-error.log'
        path.write_text(detail,encoding='utf-8')
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,
            f'Voice Bridge не запустился.\n\n{detail[-2500:]}\n\nЖурнал: {path}',
            'Voice Bridge — ошибка запуска',0x10)
        return 1

if __name__=='__main__':
    raise SystemExit(main())
