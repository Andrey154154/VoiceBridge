"""Actual child Engine + EvdevHoldTrigger + select/read + Controller + Linux injector.

Only input fd producer/ioctls/uinput, microphone, ASR and wl-copy are test adapters.
No test switch/hook is shipped in production code.
"""
import json
from pathlib import Path
import shutil
import subprocess
import time

from voice_bridge.desktop.runtime import DesktopRuntime
from voice_bridge.desktop.settings_store import save_config
from test_worker_ipc import model_hardware_for_child,wait_state


def test_real_evdev_reader_recovers_in_one_worker_and_model(tmp_path,monkeypatch):
    cfg=model_hardware_for_child(tmp_path,monkeypatch)
    cfg.trigger.backend='evdev';cfg.trigger.consume=True;cfg.trigger.evdev_device_match='ROG'
    save_config(cfg,cfg.source_path)
    fake=tmp_path/'fake'
    adapter=fake/'fixture_adapters.py'
    s=adapter.read_text()
    s=s.replace('triggers.create_trigger=lambda **kw:Trigger(**kw)','') # Use the actual evdev trigger factory.
    s=s.replace("        record('transcribed')", """        record('transcribed')
        import time
        gate=Path(os.environ['VB_ASR_GATE'])
        deadline=time.monotonic()+5
        while gate.exists():
            assert time.monotonic()<deadline,'ASR test gate timed out'
            time.sleep(.005)""")
    adapter.write_text(s)
    gate=tmp_path/'asr-block';monkeypatch.setenv('VB_ASR_GATE',str(gate))
    shutil.copyfile(Path(__file__).parent/'support/evdev_pipe_fixture.py',fake/'evdev.py')
    bootstrap=fake/'boot_worker.py'
    bootstrap.write_text('import fixture_adapters\nfrom voice_bridge.desktop.worker import main\nraise SystemExit(main())\n')
    popen=subprocess.Popen
    def spawn(command,*args,**kwargs):
        command=list(command)
        if '-m' in command and command[command.index('-m')+1]=='voice_bridge.desktop.worker':
            i=command.index('-m');command[i:i+2]=[str(bootstrap)]
        return popen(command,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',spawn)
    events=[];runtime=DesktopRuntime(cfg,events.append,diagnostics_only=True)
    mailbox=tmp_path/'trigger-command.txt'
    def send(events):
        temp=tmp_path/'key.tmp';temp.write_text(json.dumps(events));temp.replace(mailbox)
    def key(value):send([[1,275,value],[0,0,0]])
    def wait(predicate):
        end=time.monotonic()+6
        while not predicate():
            assert time.monotonic()<end,events
            time.sleep(.01)
    def recent(state,n):wait(lambda:any(e.state==state for e in events[n:]))
    try:
        runtime.gui_active=False;runtime.start(reason='actual-evdev-reader-integration')
        wait_state(events,'delivery_ready');pid=runtime._process.pid
        runtime.load_model();wait_state(events,'ready')
        n=len(events);key(1);recent('recording',n)
        # Lost release: corrupt edges discarded, current input resynchronized.
        n=len(events);send([[0,3,0],[1,275,0],[2,0,999],[0,0,0]])
        recent('warning',n);recent('operation_idle',n)
        assert 'transcribed' not in (tmp_path/'model-events.txt').read_text()
        assert not (tmp_path/'clipboard.txt').exists()
        # Loss while ASR is already running after a good key-up.
        gate.touch();n=len(events);key(1);recent('recording',n);key(0);recent('processing',n)
        wait(lambda:'transcribed' in (tmp_path/'model-events.txt').read_text())
        n=len(events);send([[0,3,0],[0,0,0]]);recent('warning',n)
        gate.unlink();recent('transcript',n);recent('operation_idle',n)
        assert not (tmp_path/'clipboard.txt').exists()
        for _ in range(2):
            n=len(events);key(1);recent('recording',n);key(0)
            recent('paste_requested',n);recent('operation_idle',n)
        assert (tmp_path/'clipboard.txt').read_text()=='Привет, мир.'
        assert runtime._process.pid==pid
        log=(tmp_path/'model-events.txt').read_text()
        assert log.count('model-created')==1 and log.count('transcribed')==3
        hardware=(tmp_path/'hardware.jsonl').read_text()
        assert hardware.count('proxy-created')==1 and hardware.count('grabbed')==1
        assert hardware.count('[1, 29, 1]')==2 and '999' not in hardware
        assert sum(e.state=='ready' for e in events)==1
        assert not any(e.state in {'error','model_error','delivery_error'} for e in events)
    finally:
        gate.unlink(missing_ok=True)
        process=runtime._process;runtime.stop(reason='test-complete')
        assert process.poll() is not None
    hardware=(tmp_path/'hardware.jsonl').read_text()
    assert 'ungrabbed' in hardware and 'proxy-closed' in hardware and 'device-closed' in hardware
