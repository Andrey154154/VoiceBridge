"""Installer-only GUI lifecycle assertions; no microphone, model or native keys.

Run against a newly constructed test window and temporary config. The runtime
is a command spy: this checks real Qt slots/buttons, not recognition hardware.
"""
from __future__ import annotations
from copy import deepcopy
from types import SimpleNamespace
import threading
import platform
from unittest.mock import patch


def check_ui_lifecycle(window, app):
    from . import window as module
    from .runtime import RuntimeEvent
    from .settings_store import save_config
    from .settings_policy import change_kind

    class RuntimeSpy:
        running = True
        enabled = True
        def __init__(self): self.commands=[]
        def configure(self): self.commands.append('configure')
        def configure_delivery(self): self.commands.append('delivery')
        def load_model(self): self.commands.append('load')
        def stop(self, **kwargs): self.commands.append('stop'); self.running=False
        def cancel(self): self.commands.append('cancel')

    w=window;spy=RuntimeSpy()
    original_start=w._start_engine
    original_runtime=w.runtime
    w.runtime=spy;w._start_engine=True
    try:
        # Normalize form exactly as a normal Save does, without a user profile.
        w._apply_form();save_config(w.cfg,w.cfg_path)
        w._applied_cfg=deepcopy(w.cfg)
        w._delivery_ready=True;w._runtime_model_requested=True
        w._model_ready=True;w._model_enabled=True;w._last_state='ready'
        w._refresh_home()
        with patch.object(module.autostart,'set_enabled'):
            w._save_and_restart()
            assert not spy.commands, 'No-op Save must not restart or configure'
            before=w.cfg_path.read_bytes()
            generation=w._runtime_generation
            sender=threading.Thread(target=lambda:w.signals.runtime_event.emit(generation,RuntimeEvent('loading','test stage')))
            sender.start();sender.join(2)
            assert not sender.is_alive()
            app.processEvents()
            assert w._model_loading and w._busy()
            assert not w.save_settings.isEnabled()
            w._save_and_restart()  # direct dispatch is guarded, not only the button
            assert w.cfg_path.read_bytes()==before and not spy.commands
            w.signals.runtime_event.emit(generation-1,RuntimeEvent('ready'))
            app.processEvents()
            assert w._model_loading, 'Stale generation must not change UI state'
            w.signals.runtime_event.emit(generation,RuntimeEvent('ready'))
            app.processEvents()
            assert w._model_ready and not w._busy()
            assert w.save_settings.isEnabled()
            index=w.mode.findData('raw');assert index>=0;w.mode.setCurrentIndex(index)
            w.save_settings.click()
            assert spy.commands==['configure'], 'Live Save must send exactly one configure, no stop'
            assert w._config_pending and not w.save_settings.isEnabled()
            w.signals.runtime_event.emit(generation,RuntimeEvent('config_applied','test ack'))
            app.processEvents()
            assert not w._config_pending and w._model_ready
            assert change_kind(w._applied_cfg,w.cfg)=='none'
            w._operation_busy=True;w._last_state='processing'
            w._cancel_operation()
            assert spy.commands[-1]=='cancel' and w._operation_busy
            w.signals.runtime_event.emit(generation,RuntimeEvent('operation_idle'))
            app.processEvents()
            assert not w._operation_busy and not w._busy()
            # 0.5.4: run the actual new controls/slots before activating update.
            if platform.system() == 'Windows':
                index=w.target_policy.findData('window');assert index>=0
                w.target_policy.setCurrentIndex(index)
            else:
                assert not w.target_policy.isEnabled()
                index=w.paste_combo.findData('ctrl+shift+v');assert index>=0
                w.paste_combo.setCurrentIndex(index)
            w.apply_delivery.click()
            assert spy.commands[-1]=='delivery' and w._config_pending
            if platform.system() == 'Windows':
                assert w._pending_config.app.paste_target=='window'
            else:
                assert w._pending_config.injector.paste_shortcut=='ctrl+shift+v'
                assert w._pending_config.app.paste_target=='active'
            w.signals.runtime_event.emit(generation,RuntimeEvent('config_applied','test delivery ack'))
            app.processEvents()
            assert w._model_ready
            assert w._applied_cfg == w._pending_config or w._pending_config is None
            w._operation_busy=True
            w.signals.runtime_event.emit(generation,RuntimeEvent('delivery_deferred','Текст готов, выберите поле'))
            w.signals.runtime_event.emit(generation,RuntimeEvent('operation_idle'))
            app.processEvents()
            assert w._model_ready and not w._busy()
            assert w._last_state=='delivery_deferred'
            assert 'Текст готов' in w.status_badge.text()
            assert 'stop' not in spy.commands and 'load' not in spy.commands
    finally:
        w._start_engine=original_start
        w.runtime=original_runtime
