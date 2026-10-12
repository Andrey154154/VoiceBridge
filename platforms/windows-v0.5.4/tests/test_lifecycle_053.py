"""Actual engine/controller/injector code, deterministic external-device doubles.

Not native Win32 or microphone acceptance. These tests exercise operation
ordering, repetitions, configuration and cancellation through the real objects.
"""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from voice_bridge.audio import AudioClip
from voice_bridge.asr.base import Transcription
from voice_bridge.config import Config, load_config
from voice_bridge.controller import VoiceBridgeController
from voice_bridge.desktop.worker import Engine
from voice_bridge.desktop.settings_policy import change_kind
from voice_bridge.desktop.settings_store import save_config
from voice_bridge.injectors.windows import WindowsTextInjector
from voice_bridge.text_engine import TextEngine


def wait_for(predicate, timeout=4):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.005)
    assert predicate(), 'Timed out waiting for the tested state'


class Recorder:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_recording = False
    def start(self):
        self.is_recording = True
    def stop(self):
        self.is_recording = False
        return AudioClip(np.ones(4000, dtype=np.float32), 16000)


class ASR:
    def __init__(self):
        self.calls = 0
        self.entered = threading.Event()
        self.proceed = threading.Event(); self.proceed.set()
    def transcribe(self, samples, sample_rate, language):
        self.calls += 1
        self.entered.set()
        assert self.proceed.wait(4)
        return Transcription('привет запятая мир точка', language)


class API:
    def __init__(self):
        self.text = ''; self.sent = []; self.position = (101, 202)
        self.fail = False
    def target(self): return self.position
    def modifiers_down(self): return False
    def set_text(self, text, *, timeout, guard):
        guard(); self.text = text
    def read_text(self, *, timeout, guard):
        guard(); return self.text
    def send(self, events):
        if self.fail:
            raise RuntimeError('injected test transport failure')
        self.sent.append(events)
        return len(events)
    def close(self): pass


class Trigger:
    on_ready = None
    def __init__(self, **cfg):
        self.cfg=cfg; self.stopped=threading.Event(); self.enabled=False
        self.press=None; self.release=None
    def run(self, press, release):
        self.press=press; self.release=release
        if self.on_ready: self.on_ready()
        self.stopped.wait(8)
    def stop(self): self.stopped.set()
    def set_enabled(self, value): self.enabled=value


@pytest.fixture
def engine_fixture(tmp_path, monkeypatch):
    import voice_bridge.factory as factory
    import voice_bridge.triggers as triggers
    import voice_bridge.desktop.worker as worker
    import voice_bridge.audio as audio
    api = API(); asr = ASR(); models=[]; hooks=[]; events=[]
    def model(**cfg): models.append(cfg); return asr
    def hook(**cfg):
        obj = Trigger(**cfg); hooks.append(obj); return obj
    monkeypatch.setattr(factory, 'QwenASRProvider', model)
    monkeypatch.setattr(factory, 'AudioRecorder', Recorder)
    monkeypatch.setattr(audio, 'AudioRecorder', Recorder)
    monkeypatch.setattr(triggers, 'create_trigger', hook)
    monkeypatch.setattr(worker, 'create_injector', lambda **kw: WindowsTextInjector(api=api, paste_shortcut=kw['paste_shortcut']))
    cfg=Config(); cfg.text.vocabulary_file=''; cfg.asr.model='test-model'; cfg.injector.backend='windows'
    cfg.trigger.backend='windows_hook'; path=tmp_path/'settings.toml'
    save_config(cfg,path);cfg=load_config(path)
    engine=Engine(cfg, lambda *event:events.append(event))
    engine.gui_active=False
    engine.start(load_model=False)
    wait_for(lambda:engine.delivery_ready.is_set())
    state=SimpleNamespace(engine=engine, api=api, asr=asr, models=models, hooks=hooks, events=events, path=path)
    yield state
    asr.proceed.set(); engine.close()


def load(f):
    f.engine.load_model()
    wait_for(lambda:any(e[0]=='ready' for e in f.events))
    assert f.engine._trigger_online


def dictate(f):
    hook=f.hooks[-1]
    before=f.asr.calls
    hook.press();wait_for(lambda:f.engine.controller.recorder.is_recording)
    hook.release()
    wait_for(lambda:f.asr.calls>before)
    wait_for(lambda:not f.engine.controller.busy)


def test_repeated_dictation_uses_one_model_and_real_injector(engine_fixture):
    f=engine_fixture;load(f)
    for _ in range(3): dictate(f)
    assert len(f.models)==1 and f.asr.calls==3 and len(f.api.sent)==3
    assert f.api.text=='Привет, мир.' and f.engine.last_text=='Привет, мир.'
    assert sum(e[0]=='operation_idle' for e in f.events)==3
    assert sum(e[0]=='paste_requested' for e in f.events)==3
    assert not any(e[0] in {'error','model_error'} for e in f.events)


def test_duplicate_load_is_noop_after_ready(engine_fixture):
    f=engine_fixture;load(f);controller=f.engine.controller;hook=f.engine.trigger
    f.engine.load_model();f.engine.load_model()
    assert len(f.models)==1 and f.engine.controller is controller and f.engine.trigger is hook


def test_saved_profile_is_read_when_loading_not_diagnostic_start(engine_fixture):
    f=engine_fixture;cfg=load_config(f.path);cfg.audio.input_device='USB microphone'
    cfg.asr.model='new-selection';save_config(cfg,f.path)
    load(f)
    assert f.models[0]['model']=='new-selection'
    assert f.engine.controller.recorder.kwargs['device']=='USB microphone'


def test_live_settings_preserve_asr_but_rebind_trigger(engine_fixture):
    f=engine_fixture;load(f);controller=f.engine.controller;old_hook=f.engine.trigger
    cfg=load_config(f.path);cfg.audio.input_device='Second microphone';cfg.trigger.button='x2';cfg.app.mode='raw'
    save_config(cfg,f.path);f.engine.command({'command':'configure'})
    wait_for(lambda:any(e[0]=='config_applied' for e in f.events))
    assert f.engine.controller is controller and controller.asr is f.asr and len(f.models)==1
    assert controller.recorder.kwargs['device']=='Second microphone'
    assert old_hook.stopped.is_set() and len(f.hooks)==2
    assert f.engine.trigger.cfg['button']=='x2'
    dictate(f)
    assert f.api.text=='привет запятая мир точка'


def test_delivery_change_does_not_reload_model(engine_fixture):
    f=engine_fixture;load(f);cfg=load_config(f.path);cfg.injector.paste_shortcut='shift+insert'
    save_config(cfg,f.path);f.engine.command({'command':'configure_delivery'})
    wait_for(lambda:any(e[0]=='config_applied' for e in f.events))
    assert f.engine.injector.paste_shortcut=='shift+insert' and len(f.models)==1
    dictate(f);assert len(f.api.sent)==1


def test_config_rejected_during_recording_without_interrupting_capture(engine_fixture):
    f=engine_fixture;load(f);f.hooks[-1].press();wait_for(lambda:f.engine.controller.recorder.is_recording)
    cfg=load_config(f.path);cfg.app.mode='raw';save_config(cfg,f.path)
    f.engine.command({'command':'configure'})
    assert f.events[-1][0]=='config_error'
    assert f.engine.controller.recorder.is_recording and f.engine.controller.mode=='dictation'
    f.hooks[-1].release();wait_for(lambda:len(f.api.sent)==1)


def test_loading_reserved_before_thread_and_not_interrupted(engine_fixture, monkeypatch):
    import voice_bridge.factory as factory
    f=engine_fixture;entered=threading.Event();release=threading.Event();original=factory.create_controller
    def slow(*args,**kw):
        entered.set();assert release.wait(3);return original(*args,**kw)
    monkeypatch.setattr(factory,'create_controller',slow)
    f.engine.load_model();assert entered.wait(2)
    f.engine.load_model()
    f.engine.command({'command':'configure'})
    f.engine.command({'command':'paste_test','text':'should not be pasted'})
    assert f.engine._loading and not f.models and not f.api.sent
    assert any(e[0]=='config_error' for e in f.events) and any(e[0]=='test_error' for e in f.events)
    release.set();wait_for(lambda:f.engine._trigger_online)
    assert len(f.models)==1


def test_cancel_during_asr_keeps_busy_and_never_pastes_after_reenable(engine_fixture):
    f=engine_fixture;load(f);f.asr.proceed.clear()
    f.hooks[-1].press();wait_for(lambda:f.engine.controller.recorder.is_recording);f.hooks[-1].release()
    assert f.asr.entered.wait(2)
    f.engine.command({'command':'enabled','enabled':False})
    f.engine.command({'command':'enabled','enabled':True})
    assert f.engine.controller.busy
    f.engine.command({'command':'configure'})
    assert f.events[-1][0]=='config_error'
    f.asr.proceed.set();wait_for(lambda:not f.engine.controller.busy)
    assert not f.api.sent and f.engine.last_text=='Привет, мир.'
    dictate(f)
    assert len(f.api.sent)==1 and len(f.models)==1


def test_delivery_failure_retains_text_and_next_dictation_works(engine_fixture):
    f=engine_fixture;load(f);f.api.fail=True;dictate(f)
    assert f.engine.last_text=='Привет, мир.' and not f.api.sent
    assert any(e[0]=='error' for e in f.events)
    f.api.fail=False;dictate(f)
    assert len(f.models)==1 and len(f.api.sent)==1


def test_focus_change_during_asr_cancels_target_delivery(engine_fixture):
    # 0.5.4 keeps this 0.5.3 behaviour as an explicit strict policy.
    f=engine_fixture;cfg=load_config(f.path);cfg.app.paste_target="strict";save_config(cfg,f.path)
    load(f);f.asr.proceed.clear()
    f.hooks[-1].press();wait_for(lambda:f.engine.controller.recorder.is_recording);f.hooks[-1].release()
    assert f.asr.entered.wait(2)
    f.api.position=(999,555)
    f.asr.proceed.set();wait_for(lambda:not f.engine.controller.busy)
    assert not f.api.sent and f.engine.last_text=='Привет, мир.'
    dictate(f);assert len(f.api.sent)==1


def test_stop_during_load_does_not_install_late_hook(engine_fixture, monkeypatch):
    import voice_bridge.factory as factory
    f=engine_fixture;entered=threading.Event();release=threading.Event();original=factory.create_controller
    def slow(*a,**kw): entered.set();assert release.wait(3);return original(*a,**kw)
    monkeypatch.setattr(factory,'create_controller',slow)
    f.engine.load_model();assert entered.wait(2)
    f.engine.close();release.set();wait_for(lambda:not f.engine._loading)
    assert f.engine.controller is None and not f.hooks and not f.api.sent


def test_failed_trigger_rebind_restores_previous_without_model_reload(engine_fixture, monkeypatch):
    import voice_bridge.triggers as triggers
    f=engine_fixture;load(f);original=triggers.create_trigger
    def broken(**cfg):
        if cfg['button']=='x2':raise RuntimeError('test hook failure')
        return original(**cfg)
    monkeypatch.setattr(triggers,'create_trigger',broken)
    cfg=load_config(f.path);cfg.trigger.button='x2';cfg.app.mode='raw';save_config(cfg,f.path)
    f.engine.command({'command':'configure'})
    wait_for(lambda:any(e[0]=='config_error' for e in f.events))
    assert f.engine._trigger_online and f.engine.config.trigger.button=='x1'
    assert f.engine.controller.mode=='dictation' and len(f.models)==1
    dictate(f);assert len(f.api.sent)==1


def test_old_trigger_callbacks_are_ignored_after_rebind(engine_fixture):
    f=engine_fixture;load(f);old=f.hooks[-1]
    cfg=load_config(f.path);cfg.trigger.button='x2';save_config(cfg,f.path)
    f.engine.command({'command':'configure'});wait_for(lambda:len(f.hooks)==2 and not f.engine._reconfiguring)
    old.press();old.release();time.sleep(.03)
    assert not f.engine.controller.busy and not f.asr.calls
    dictate(f);assert len(f.api.sent)==1


def test_model_change_cannot_sneak_through_live_apply(engine_fixture):
    f=engine_fixture;load(f);cfg=load_config(f.path);cfg.asr.model='another-model';save_config(cfg,f.path)
    f.engine.command({'command':'configure'});wait_for(lambda:any(e[0]=='config_error' for e in f.events))
    assert f.engine.config.asr.model=='test-model' and len(f.models)==1


@pytest.mark.parametrize('area,field,value,kind',[
    ('app','beep',True,'none'),('app','models_directory','D:/models','none'),
    ('app','start_model_on_launch',False,'none'),('app','mode','raw','live'),
    ('audio','input_device','other','live'),('trigger','button','x2','live'),
    ('text','vocabulary_file','','live'),('injector','paste_shortcut','shift+insert','live'),
    ('asr','model','new','model'),('asr','placement','cpu','model'),
])
def test_settings_policy(area,field,value,kind):
    old=Config();new=deepcopy(old);setattr(getattr(new,area),field,value)
    assert change_kind(old,new)==kind


def test_paste_test_ack_means_lock_already_released(engine_fixture):
    f=engine_fixture
    seen=[];sink=f.engine._sink
    def emit(state,detail):
        if state in {'test_sent','test_error'}:seen.append(f.engine._test_lock.locked())
        sink(state,detail)
    f.engine._sink=emit
    f.engine.command({'command':'paste_test','text':'probe','internal':False})
    wait_for(lambda:bool(seen))
    assert seen==[False]


def test_independent_cli_does_not_construct_desktop_engine(tmp_path,monkeypatch):
    import voice_bridge.cli as cli
    import voice_bridge.factory as factory
    import voice_bridge.triggers as triggers
    import voice_bridge.desktop.worker as worker
    from voice_bridge.instance import UserInstanceLock
    controller=Mock();controller.injector.prepare.return_value='test only'
    hook=Mock();hook.run.side_effect=lambda press,release:(press(),release())
    monkeypatch.setattr(factory,'create_controller',lambda *a,**k:controller)
    monkeypatch.setattr(triggers,'create_trigger',lambda **k:hook)
    monkeypatch.setattr(worker,'Engine',Mock(side_effect=AssertionError('CLI used desktop worker')))
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'data'))
    assert cli.command_run(Config())==0
    controller.press.assert_called_once();controller.release.assert_called_once()
    controller.cancel.assert_called_once();hook.stop.assert_called_once();controller.injector.close.assert_called_once()


def test_config_ack_does_not_clear_next_config_reservation(engine_fixture,monkeypatch):
    f=engine_fixture;load(f)
    original=f.engine._apply_config;calls=[];entered=threading.Event();release=threading.Event()
    def apply(cfg):
        calls.append(1)
        if len(calls)==2:
            entered.set();assert release.wait(3)
        return original(cfg)
    monkeypatch.setattr(f.engine,'_apply_config',apply)
    sink=f.engine._sink;started=[]
    def emit(state,detail):
        sink(state,detail)
        if state=='config_applied' and not started:
            started.append(1)
            f.engine.command({'command':'configure'})
    f.engine._sink=emit
    try:
        f.engine.command({'command':'configure'})
        assert entered.wait(2)
        time.sleep(.02)
        assert f.engine._reconfiguring
    finally:
        release.set()
    wait_for(lambda:not f.engine._reconfiguring)
