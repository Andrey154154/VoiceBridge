"""Real worker subprocess/IPC lifecycle; evdev and clipboard hardware are fake.

These are integration tests of our process code, NOT a Wayland delivery test.
"""
import os
import threading
import time
from pathlib import Path

from voice_bridge.config import Config, load_config
from voice_bridge.desktop.runtime import DesktopRuntime
from voice_bridge.desktop.settings_store import save_config


def setup_fake_hardware(tmp_path, monkeypatch):
    fake=tmp_path/'fake';fake.mkdir()
    (fake/'evdev.py').write_text('''
import os, json
from types import SimpleNamespace
ecodes=SimpleNamespace(EV_KEY=1,EV_LED=17,BUS_USB=3)
def record(value):
    with open(os.environ['VB_HW_LOG'],'a') as f: f.write(json.dumps(value)+'\\n')
class UInput:
    def __init__(self,*args,**kwargs): record(['created',kwargs['name']])
    def write(self,*args): record(list(args))
    def syn(self): record(['syn'])
    def close(self): record(['closed'])
''')
    copy=fake/'wl-copy'
    copy.write_text('#!/usr/bin/env python3\nimport os,sys\nopen(os.environ["VB_CLIP"],"wb").write(sys.stdin.buffer.read())\n')
    copy.chmod(0o755)
    monkeypatch.setenv('PYTHONPATH',str(fake)+os.pathsep+os.environ.get('PYTHONPATH',''))
    monkeypatch.setenv('PATH',str(fake)+os.pathsep+os.environ.get('PATH',''))
    monkeypatch.setenv('WAYLAND_DISPLAY','test-wayland-not-a-real-compositor')
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path/'data'))
    monkeypatch.setenv('VB_HW_LOG',str(tmp_path/'hardware.jsonl'))
    monkeypatch.setenv('VB_CLIP',str(tmp_path/'clipboard.txt'))
    cfg=Config();cfg.injector.keyboard_backend='uinput'
    cfg.injector.clipboard_settle_ms=100
    save_config(cfg,tmp_path/'config.toml')
    return load_config(tmp_path/'config.toml')


def wait_state(events, state, timeout=6):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        for event in events:
            if event.state==state:
                return event
        time.sleep(.03)
    raise AssertionError(f'Missing {state}: {events}')


def test_diagnostic_worker_full_ipc_without_asr(tmp_path,monkeypatch):
    cfg=setup_fake_hardware(tmp_path,monkeypatch)
    events=[]
    runtime=DesktopRuntime(cfg,events.append,diagnostics_only=True)
    try:
        runtime.start()
        wait_state(events,'delivery_ready')
        runtime.set_gui_active(True)
        runtime.paste_test('Проверка IPC',internal=True)
        sent=wait_state(events,'test_sent')
        assert 'application-unverified' in sent.detail
        assert (tmp_path/'clipboard.txt').read_text()=='Проверка IPC'
        data=(tmp_path/'hardware.jsonl').read_text()
        assert data.count('created')==1
        assert '[1, 29, 1]' in data and '[1, 47, 0]' in data
        assert not any(e.state=='loading' for e in events)
    finally:
        process=runtime._process
        runtime.stop()
        assert process.poll() is not None
    assert 'closed' in (tmp_path/'hardware.jsonl').read_text()


def test_duplicate_worker_is_rejected_and_original_survives(tmp_path,monkeypatch):
    cfg=setup_fake_hardware(tmp_path,monkeypatch)
    events1=[];events2=[]
    one=DesktopRuntime(cfg,events1.append,diagnostics_only=True)
    two=DesktopRuntime(cfg,events2.append,diagnostics_only=True)
    try:
        one.start();wait_state(events1,'delivery_ready')
        two.start()
        error=wait_state(events2,'error')
        assert 'already running' in error.detail
        assert one._process.poll() is None
        assert (tmp_path/'hardware.jsonl').read_text().count('created')==1
    finally:
        two.stop();one.stop()


def test_stop_cancels_pending_external_test(tmp_path,monkeypatch):
    cfg=setup_fake_hardware(tmp_path,monkeypatch)
    events=[]
    runtime=DesktopRuntime(cfg,events.append,diagnostics_only=True)
    try:
        runtime.start();wait_state(events,'delivery_ready')
        runtime.set_gui_active(False)
        runtime.paste_test('must not be pasted',delay=5)
        wait_state(events,'test_pending')
        runtime.stop()
        assert not (tmp_path/'clipboard.txt').exists()
        assert '[1, 29, 1]' not in (tmp_path/'hardware.jsonl').read_text()
    finally:
        runtime.stop()


def test_restart_releases_old_lock_before_new_worker(tmp_path,monkeypatch):
    cfg=setup_fake_hardware(tmp_path,monkeypatch)
    events=[]
    runtime=DesktopRuntime(cfg,events.append,diagnostics_only=True)
    try:
        runtime.start();wait_state(events,'delivery_ready')
        old=runtime._process
        runtime.stop()
        assert old.poll() is not None
        events.clear()
        runtime.start();wait_state(events,'delivery_ready')
        assert not any(e.state=='error' for e in events)
        assert runtime._process.pid != old.pid
    finally:
        runtime.stop()


def model_hardware_for_child(tmp_path, monkeypatch):
    """Install test adapters only via this child process's PYTHONPATH."""
    cfg=setup_fake_hardware(tmp_path,monkeypatch)
    cfg.asr.model='fixture-only';cfg.text.vocabulary_file=''
    save_config(cfg,cfg.source_path)
    monkeypatch.setenv('VB_MODEL_EVENTS',str(tmp_path/'model-events.txt'))
    monkeypatch.setenv('VB_TRIGGER_COMMAND',str(tmp_path/'trigger-command.txt'))
    (tmp_path/'fake'/'fixture_adapters.py').write_text('''
import os, threading
from pathlib import Path
import numpy as np
import voice_bridge.factory as factory
import voice_bridge.audio as audio
import voice_bridge.triggers as triggers
from voice_bridge.asr.base import Transcription

def record(value):
    with open(os.environ['VB_MODEL_EVENTS'],'a') as f: f.write(value+'\\n')
class Recorder:
    def __init__(self, **kw): self.is_recording=False
    def start(self): self.is_recording=True
    def stop(self):
        self.is_recording=False
        return audio.AudioClip(np.zeros(4800,dtype=np.float32),16000)
class Model:
    def __init__(self,**kw): record('model-created')
    def transcribe(self,*args):
        record('transcribed')
        return Transcription(text='привет запятая мир точка',language='Russian')
class Trigger:
    on_ready=None
    def __init__(self,**kw): self.done=threading.Event();self.enabled=True
    def set_enabled(self,value): self.enabled=value
    def stop(self): self.done.set()
    def run(self,press,release):
        record('hook-created')
        self.on_ready()
        mailbox=Path(os.environ['VB_TRIGGER_COMMAND'])
        while not self.done.wait(.015):
            if not mailbox.exists(): continue
            cmd=mailbox.read_text();mailbox.unlink()
            if self.enabled:
                if cmd=='press': press()
                elif cmd=='release': release()
factory.AudioRecorder=audio.AudioRecorder=Recorder
factory.QwenASRProvider=Model
triggers.create_trigger=lambda **kw:Trigger(**kw)
''',encoding='utf-8')
    return cfg


def test_real_process_load_live_config_repeat_and_single_pid(tmp_path,monkeypatch):
    cfg=model_hardware_for_child(tmp_path,monkeypatch);events=[]
    # Explicit child bootstrap supplies hardware adapters; no production hook,
    # test flag, or environment-based fake-ASR capability is shipped.
    bootstrap=tmp_path/'fake'/'boot_worker.py'
    bootstrap.write_text('import fixture_adapters\nfrom voice_bridge.desktop.worker import main\nraise SystemExit(main())\n')
    import subprocess
    real_popen=subprocess.Popen
    def spawn(command, *args, **kwargs):
        command=list(command)
        if '-m' in command and command[command.index('-m')+1]=='voice_bridge.desktop.worker':
            index=command.index('-m');command[index:index+2]=[str(bootstrap)]
        return real_popen(command,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',spawn)
    runtime=DesktopRuntime(cfg,events.append,diagnostics_only=True)
    mailbox=tmp_path/'trigger-command.txt'
    def key(value):
        temp=tmp_path/'key.tmp';temp.write_text(value);temp.replace(mailbox)
    def dictate(expected):
        start=len(events);key('press')
        deadline=time.monotonic()+5
        while not any(e.state=='recording' for e in events[start:]):
            assert time.monotonic()<deadline,events;time.sleep(.02)
        key('release')
        while not any(e.state=='operation_idle' for e in events[start:]):
            assert time.monotonic()<deadline,events;time.sleep(.02)
        assert (tmp_path/'clipboard.txt').read_text()==expected
    try:
        runtime.gui_active=False
        runtime.start(reason='integration-test');wait_state(events,'delivery_ready')
        pid=runtime._process.pid
        runtime.start(reason='duplicate-start')
        runtime.load_model();wait_state(events,'ready')
        assert runtime._process.pid==pid
        dictate('Привет, мир.');dictate('Привет, мир.')
        cfg.app.mode='raw';cfg.trigger.button='x2';save_config(cfg,cfg.source_path)
        runtime.configure();wait_state(events,'config_applied')
        assert runtime._process.pid==pid
        dictate('привет запятая мир точка')
        record=(tmp_path/'model-events.txt').read_text()
        assert record.count('model-created')==1
        assert record.count('transcribed')==3 and record.count('hook-created')==2
        assert not any(e.state in {'error','model_error','config_error'} for e in events)
    finally:
        process=runtime._process;runtime.stop(reason='test-finished')
        assert process.poll() is not None
