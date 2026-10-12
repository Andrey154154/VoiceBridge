"""Focus policy contract through the real Engine/Controller/Windows injector.

Only the OS, microphone and model are doubles. This is NOT native Windows
acceptance; the tests prove that our policy no longer binds the default CLI
workflow to an obsolete child HWND, without dropping the short delivery guard.
"""
from copy import deepcopy
import ctypes as C
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from voice_bridge.config import Config, load_config
from voice_bridge.desktop.settings_store import save_config
from voice_bridge.desktop.settings_policy import change_kind
from voice_bridge.desktop.worker import GuardedInjector
from voice_bridge.desktop.runtime import RuntimeEvent
from voice_bridge.injectors.errors import DeliveryDeferred
from voice_bridge.injectors.windows import NativeDeliveryAPI, WindowsTextInjector
from voice_bridge.win32 import DWORD, GUITHREADINFO
from test_lifecycle_053 import engine_fixture, load, dictate, wait_for
from test_ui_lifecycle_053 import ui
from test_ui_contracts import method


def set_policy(f, policy):
    cfg=load_config(f.path);cfg.app.paste_target=policy;save_config(cfg,f.path)


def start_and_switch(f, target):
    f.asr.proceed.clear()
    f.asr.entered.clear()
    f.hooks[-1].press()
    wait_for(lambda:f.engine.controller.recorder.is_recording)
    f.hooks[-1].release()
    assert f.asr.entered.wait(2)
    f.api.position=target
    f.asr.proceed.set()
    wait_for(lambda:not f.engine.controller.busy)


@pytest.mark.parametrize('target',[(101,303),(999,555)])
def test_default_cli_mode_delivers_to_current_target_after_asr(engine_fixture,target):
    f=engine_fixture;load(f)
    assert f.engine.config.app.paste_target=='active'
    start_and_switch(f,target)
    assert len(f.api.sent)==1 and f.api.text=='Привет, мир.'
    assert f.engine.controller.last_result.delivery=='windows-input-sent; application-unverified'
    assert not any(e[0] in {'error','model_error','delivery_deferred'} for e in f.events)
    dictate(f)
    assert len(f.models)==1 and len(f.api.sent)==2


@pytest.mark.parametrize('policy,target,allow',[
    ('window',(101,303),True), ('window',(999,555),False),
    ('strict',(101,202),True), ('strict',(101,303),False),
    ('strict',(999,555),False)])
def test_explicit_binding_modes_are_retained(engine_fixture,policy,target,allow):
    f=engine_fixture;set_policy(f,policy);load(f)
    start_and_switch(f,target)
    assert len(f.api.sent)==int(allow)
    assert f.engine.last_text=='Привет, мир.'
    if not allow:
        assert any(e[0]=='delivery_deferred' for e in f.events)
        assert not any(e[0]=='error' for e in f.events)
        assert f.engine.controller.last_result.delivery=='deferred'
        assert f.engine._trigger_online
    dictate(f)
    assert len(f.models)==1 and len(f.api.sent)==int(allow)+1


@pytest.mark.parametrize('policy',['active','window','strict'])
def test_mid_clipboard_focus_change_is_never_automatically_pasted(engine_fixture,policy):
    f=engine_fixture;set_policy(f,policy);load(f)
    original=f.api.set_text
    def change_during_write(*a,**kw):
        original(*a,**kw)
        f.api.position=(700,701)
    f.api.set_text=change_during_write
    dictate(f)
    assert not f.api.sent and f.engine.last_text=='Привет, мир.'
    assert f.engine.controller.last_result.delivery=='deferred'
    assert any(e[0]=='delivery_deferred' for e in f.events)
    f.api.set_text=original
    dictate(f)
    assert len(f.api.sent)==1 and len(f.models)==1


@pytest.mark.parametrize('pid',[os.getpid(),os.getppid()])
def test_native_process_identity_blocks_own_ui_even_if_qt_focus_ack_is_late(engine_fixture,pid):
    f=engine_fixture;load(f)
    start_and_switch(f,(800,801,pid,123))
    assert not f.api.sent and f.engine.last_text
    assert f.engine.controller.last_result.delivery=='deferred'


@pytest.mark.parametrize('policy',['window','strict'])
def test_reused_hwnd_for_a_different_process_is_not_the_original_window(engine_fixture,policy):
    f=engine_fixture;set_policy(f,policy);load(f)
    f.api.position=(101,202,900001,333)
    start_and_switch(f,(101,202,900002,444))
    assert not f.api.sent and f.engine.controller.last_result.delivery=='deferred'


def test_no_active_target_is_deferred_not_sent(engine_fixture):
    f=engine_fixture;load(f)
    start_and_switch(f,(0,0,0,0))
    assert not f.api.sent
    assert f.engine.controller.last_result.delivery=='deferred'


def test_cancellation_still_beats_active_policy(engine_fixture):
    f=engine_fixture;load(f);f.asr.proceed.clear()
    f.hooks[-1].press();wait_for(lambda:f.engine.controller.recorder.is_recording)
    f.hooks[-1].release();assert f.asr.entered.wait(2)
    f.engine.command({'command':'cancel'})
    f.api.position=(999,555);f.asr.proceed.set()
    wait_for(lambda:not f.engine.controller.busy)
    assert not f.api.sent
    assert f.engine.controller.last_result.delivery=='cancelled'


def test_gui_focus_blocks_direct_guard_with_a_retained_transcript(engine_fixture):
    f=engine_fixture;load(f);f.engine.gui_active=True
    with pytest.raises(DeliveryDeferred):GuardedInjector(f.engine).insert('test')
    assert not f.api.sent


def test_delivery_only_configure_changes_policy_without_model_reload(engine_fixture):
    f=engine_fixture;load(f)
    model=f.engine.controller.asr;injector=f.engine.injector
    cfg=load_config(f.path);cfg.app.paste_target='window';cfg.app.language='English'
    save_config(cfg,f.path)
    f.engine.command({'command':'configure_delivery'})
    wait_for(lambda:any(e[0]=='config_applied' for e in f.events))
    assert f.engine.config.app.paste_target=='window'
    assert f.engine.config.app.language=='Russian'  # only delivery requested
    assert f.engine.controller.asr is model and f.engine.injector is injector
    assert len(f.models)==1


@pytest.mark.parametrize('policy',['active','window','strict'])
def test_policy_roundtrip_and_live_comparison(tmp_path,policy):
    cfg=Config();other=deepcopy(cfg);other.app.paste_target=policy
    path=tmp_path/'c.toml';save_config(other,path)
    assert load_config(path).app.paste_target==policy
    assert change_kind(cfg,other)==('none' if policy=='active' else 'live')


def test_legacy_configuration_uses_cli_default_without_editing_it(tmp_path):
    path=tmp_path/'c.toml';path.write_text('[app]\nmode="raw"\n',encoding='utf-8')
    before=path.read_bytes()
    assert load_config(path).app.paste_target=='active'
    assert path.read_bytes()==before


@pytest.mark.parametrize('value',['"wrong"','true','27'])
def test_unknown_policy_fails_validation(tmp_path,value):
    path=tmp_path/'c.toml';path.write_text('[app]\npaste_target='+value+'\n')
    with pytest.raises(ValueError,match='paste_target'):load_config(path)


def test_ui_deferral_is_not_model_failure_or_reload(tmp_path):
    w=ui(tmp_path);w._model_ready=True;w._model_enabled=True;w._operation_busy=True
    w.tray=Mock();w.last_text=Mock()
    handler=method('_on_runtime_event')
    handler(w,RuntimeEvent('delivery_deferred','Выберите поле'))
    assert w._model_ready and w._model_enabled
    assert w._last_state=='delivery_deferred'
    assert w.status_badge.setText.call_args.args[0]=='Текст готов — выберите поле'
    handler(w,RuntimeEvent('operation_idle'))
    assert not w._busy() and w._model_ready
    w._request_restart.assert_not_called();w.runtime.stop.assert_not_called()
    w.last_text.setPlainText.assert_not_called()


def test_ui_delivery_save_sends_exactly_one_command_for_policy_only(tmp_path):
    w=ui(tmp_path)
    w.keyboard_backend=Mock();w.keyboard_backend.currentData.return_value=w.cfg.injector.keyboard_backend
    w.paste_combo=Mock();w.paste_combo.currentData.return_value=w.cfg.injector.paste_shortcut
    w.target_policy=Mock();w.target_policy.currentData.return_value='window'
    method('_apply_delivery')(w)
    assert load_config(w.cfg_path).app.paste_target=='window'
    assert w._pending_config.app.paste_target=='window'
    w.runtime.configure_delivery.assert_called_once()
    w.runtime.stop.assert_not_called();w._request_restart.assert_not_called()


class NativeUserStub:
    def __init__(self, *, process=112233, thread=445566, focus=0x200000333,
                 fg=(0x100000777,0x100000777), gui_ok=True):
        self.process=process;self.thread=thread;self.focus=focus
        self.fg=iter(fg);self.gui_ok=gui_ok
    def GetForegroundWindow(self):return next(self.fg)
    def GetWindowThreadProcessId(self,hwnd,pid):
        assert hwnd==0x100000777
        C.cast(pid,C.POINTER(DWORD)).contents.value=self.process
        return self.thread
    def GetGUIThreadInfo(self,thread,info):
        assert thread==self.thread
        data=C.cast(info,C.POINTER(GUITHREADINFO)).contents
        assert data.cbSize==C.sizeof(GUITHREADINFO)
        data.hwndFocus=self.focus
        return self.gui_ok


def snapshot(**kw):
    api=NativeDeliveryAPI.__new__(NativeDeliveryAPI);api.u=NativeUserStub(**kw)
    return api.target()


def test_native_snapshot_preserves_x64_handles_and_owner_identifiers():
    assert snapshot()==(0x100000777,0x200000333,112233,445566)


def test_snapshot_rejects_mixed_activation_sample():
    assert snapshot(fg=(0x100000777,0x111))==(0,0,0,0)


@pytest.mark.parametrize('kw',[{'fg':(0,)},{'process':0},{'thread':0}])
def test_invalid_native_target_is_not_accepted(kw):
    assert snapshot(**kw)==(0,0,0,0)


def test_missing_child_focus_is_explicit_not_a_fake_field():
    assert snapshot(gui_ok=False)==(0x100000777,0,112233,445566)
