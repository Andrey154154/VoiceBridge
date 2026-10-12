import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from voice_bridge.config import Config, load_config
from voice_bridge.desktop.settings_store import save_config
from voice_bridge.desktop.worker import Engine
from voice_bridge.instance import UserInstanceLock
from voice_bridge.controller import VoiceBridgeController
from voice_bridge.asr.fake import FakeASRProvider
from voice_bridge.text_engine import TextEngine
from test_controller import FakeRecorder, FakeInjector


def make_controller(asr=None, injector=None, callback=None):
    return VoiceBridgeController(recorder=FakeRecorder(), asr=asr or FakeASRProvider('проверка точка'),
                                 text_engine=TextEngine(), injector=injector or FakeInjector(), state_callback=callback)


def test_result_emitted_before_failed_delivery():
    events=[]
    injector = Mock()
    injector.insert.side_effect=RuntimeError('no keyboard')
    controller=make_controller(injector=injector, callback=lambda *args: events.append(args))
    controller._process_clip(controller.recorder.clip)
    assert ('transcript','Проверка.') in events
    assert ('error','no keyboard') in events
    assert events[-1] == ('operation_idle','')
    assert controller.last_result.final_text == 'Проверка.'
    assert controller.last_result.delivery == 'failed'


def test_no_false_inserted_or_confirmed_state():
    events=[]
    controller=make_controller(callback=lambda *args: events.append(args))
    result=controller._process_clip(controller.recorder.clip)
    assert result.delivery == 'application-unverified'
    assert ('paste_requested','application-unverified') in events
    assert events[-1] == ('operation_idle','')
    assert not any('confirmed' in state or 'inserted' in state for state,_ in events)


def test_press_cannot_race_release_before_processing_thread(monkeypatch):
    controller=make_controller()
    class DeferredThread:
        def __init__(self, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr(threading, 'Thread', DeferredThread)
    controller.press()
    controller.release()
    controller.press()
    assert not controller.recorder.is_recording
    assert controller.busy


def test_disable_cancel_during_asr_never_pastes():
    started=threading.Event()
    resume=threading.Event()
    asr=FakeASRProvider('готово')
    old=asr.transcribe
    def transcribe(*args):
        started.set()
        assert resume.wait(2)
        return old(*args)
    asr.transcribe=transcribe
    injector=FakeInjector()
    controller=make_controller(asr=asr, injector=injector)
    thread=threading.Thread(target=controller._process_clip,args=(controller.recorder.clip,))
    thread.start()
    assert started.wait(2)
    controller.cancel()
    resume.set()
    thread.join(2)
    assert not thread.is_alive()
    assert injector.values == []
    assert controller.last_result.delivery == 'cancelled'


def test_cancel_while_recording_stops_capture():
    controller=make_controller()
    controller.press()
    controller.cancel()
    assert not controller.recorder.is_recording
    assert not controller.busy


def test_settings_roundtrip_control_characters_and_user_model(tmp_path):
    cfg=Config()
    cfg.asr.prompt='Строка 1\nСтрока 2\t"Тест" \\ folder'
    cfg.asr.model='/tmp/voicebridge-test-model'
    cfg.injector.keyboard_backend='uinput'
    target=tmp_path/'config.toml'
    save_config(cfg,target)
    loaded=load_config(target)
    assert loaded.asr.prompt==cfg.asr.prompt
    assert loaded.asr.model==cfg.asr.model
    assert loaded.injector.keyboard_backend=='uinput'
    assert target.stat().st_mode & 0o777 == 0o600


def test_invalid_save_preserves_previous_valid_config(tmp_path):
    target=tmp_path/'config.toml'
    cfg=Config();save_config(cfg,target)
    before=target.read_bytes()
    cfg.injector.key_delay_ms=-1
    with pytest.raises(ValueError): save_config(cfg,target)
    assert target.read_bytes()==before
    assert len(list(tmp_path.iterdir())) == 1


def test_old_config_loads_new_defaults(tmp_path):
    target=tmp_path/'old.toml'
    target.write_text('[injector]\nbackend="linux"\npaste_shortcut="ctrl+v"\n')
    cfg=load_config(target)
    assert cfg.injector.keyboard_backend=='auto'
    assert cfg.injector.paste_shortcut=='ctrl+v'


def test_single_instance_lock_and_reacquisition(tmp_path):
    first=UserInstanceLock('engine',directory=tmp_path)
    second=UserInstanceLock('engine',directory=tmp_path)
    first.acquire()
    with pytest.raises(RuntimeError,match='already running'): second.acquire()
    first.close()
    second.acquire()
    second.close()
    assert (tmp_path/'engine.lock').exists()


def test_runtime_command_does_not_resolve_python_venv_symlink(monkeypatch):
    from voice_bridge import platform_paths as paths
    monkeypatch.setattr(paths.platform,'system',lambda:'test')
    monkeypatch.setattr(sys,'executable','/some/venv/bin/python')
    assert paths.runtime_command()[0]=='/some/venv/bin/python'


def test_trigger_excludes_existing_virtual_mouse_proxy(monkeypatch):
    from voice_bridge.triggers.evdev_trigger import EvdevHoldTrigger
    virtual=Mock(name='virtual')
    virtual.name='Voice Input Bridge proxy - USB Test Mouse';virtual.phys=''
    physical=Mock(name='physical')
    physical.name='USB Test Mouse';physical.phys='usb-1';physical.path='/dev/input/event2'
    physical.capabilities.return_value={1:[275]}
    fake=SimpleNamespace(list_devices=lambda:['virtual','physical'],
                         InputDevice=lambda name: {'virtual':virtual,'physical':physical}[name],
                         ecodes=SimpleNamespace(ecodes={'BTN_SIDE':275},EV_KEY=1))
    monkeypatch.setitem(sys.modules,'evdev',fake)
    selected,code=EvdevHoldTrigger(device_match='USB Test')._select_device()
    assert selected is physical and code==275
    virtual.close.assert_called_once()
    virtual.capabilities.assert_not_called()


def make_engine(monkeypatch):
    import voice_bridge.desktop.worker as w
    injector=Mock()
    injector.prepare.return_value='fake transport'
    monkeypatch.setattr(w,'create_injector',lambda **_:injector)
    events=[]
    engine=Engine(Config(),lambda *args:events.append(args))
    engine.delivery_ready.set()
    return engine,injector,events


def test_engine_guard_rejects_focused_settings(monkeypatch):
    from voice_bridge.desktop.worker import GuardedInjector
    engine,injector,_=make_engine(monkeypatch)
    with pytest.raises(RuntimeError,match='Выберите поле'):
        GuardedInjector(engine).insert('text')
    injector.insert.assert_not_called()


def test_engine_disabled_cancels_recording(monkeypatch):
    engine,_,events=make_engine(monkeypatch)
    engine.controller=Mock()
    engine.command({'command':'enabled','enabled':False})
    engine.controller.cancel.assert_called_once()
    assert not engine.enabled


def test_test_cannot_paste_into_external_window_when_internal_loses_focus(monkeypatch):
    engine,injector,events=make_engine(monkeypatch)
    engine.gui_active=False
    engine._test_lock.acquire()
    engine._paste_test('test',0,True)
    injector.insert.assert_not_called()
    assert events[-1][0]=='test_error'
    assert not engine._test_lock.locked()


def test_external_test_does_not_paste_in_settings(monkeypatch):
    engine,injector,events=make_engine(monkeypatch)
    engine.gui_active=True
    engine._test_lock.acquire()
    engine._paste_test('test',0,False)
    injector.insert.assert_not_called()
    assert events[-1][0]=='test_error'


def test_explicit_test_reports_sent_not_verified(monkeypatch):
    engine,injector,events=make_engine(monkeypatch)
    injector.insert.return_value='application-unverified'
    engine.gui_active=True
    engine._test_lock.acquire()
    engine._paste_test('test',0,True)
    injector.insert.assert_called_once_with('test')
    assert events[-1]==('test_sent','application-unverified')


def test_stop_sets_cancellation_before_any_further_actions(monkeypatch):
    engine,injector,events=make_engine(monkeypatch)
    engine.controller=Mock()
    engine.command({'command':'stop'})
    assert engine.stopping.is_set()
    engine.close()
    engine.controller.cancel.assert_called_once()
    injector.close.assert_called_once()


def test_audio_start_failure_resets_capture_state(monkeypatch):
    from voice_bridge.audio import AudioRecorder
    stream=Mock()
    stream.start.side_effect=RuntimeError('device missing')
    monkeypatch.setitem(sys.modules,'sounddevice',SimpleNamespace(InputStream=Mock(return_value=stream)))
    recorder=AudioRecorder()
    with pytest.raises(RuntimeError): recorder.start()
    assert not recorder.is_recording and recorder._stream is None
    stream.close.assert_called_once()


def test_worker_module_does_not_load_qt_or_torch_on_import():
    import subprocess
    code='import sys; import voice_bridge.desktop.worker; assert "PySide6" not in sys.modules; assert "torch" not in sys.modules'
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr


def test_installer_does_not_match_unrelated_processes():
    import importlib.util
    path=Path(__file__).resolve().parents[1]/'packaging/stop_old.py'
    spec=importlib.util.spec_from_file_location('stop_old',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.matches(['/opt/voice-bridge/runtime/bin/python','-m','voice_bridge.desktop.app'])
    assert module.matches(['/opt/voice-bridge/runtime/bin/python','-u','-m','voice_bridge.desktop.worker'])
    assert module.matches(['/some/venv/bin/python','/some/venv/bin/voice-bridge','run'])
    assert not module.matches(['bash','-c','pkill -f voice-bridge-desktop'])
    assert not module.matches(['grep','voice-bridge'])
    assert not module.matches(['python','-m','voice_bridge.cli','devices'])
    assert not module.matches(['python','/tmp/voicebridge-example.py','voice-bridge-desktop'])


def test_ui_focus_and_restart_source_regressions():
    root=Path(__file__).resolve().parents[1]/'src/voice_bridge/desktop'
    source=(root/'window.py').read_text()
    app=(root/'app.py').read_text()
    assert 'StatusOverlay' not in source
    assert 'WindowStaysOnTopHint' not in source
    assert 'diagnostics_only=not self._model_enabled' in source
    assert 'self._model_enabled = True' in source
    assert 'app.applicationStateChanged.connect(window._publish_focus)' in app
    external=source.split('    def _external_paste(self, text):',1)[1].split('    def _verify_test_field',1)[0]
    assert 'self.showMinimized()' in external
    assert 'generation == self._runtime_generation' in source
