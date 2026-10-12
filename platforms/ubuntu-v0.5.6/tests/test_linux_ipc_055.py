"""A real Linux child process, actual controller/injector; only hardware/ASR are fake."""
from pathlib import Path
import subprocess
import time

from voice_bridge.desktop.runtime import DesktopRuntime
from test_worker_ipc import model_hardware_for_child,wait_state


def test_linux_clipboard_failure_then_two_dictations_one_model_and_pid(tmp_path,monkeypatch):
    cfg=model_hardware_for_child(tmp_path,monkeypatch)
    events=[]
    bootstrap=tmp_path/'fake'/'boot_worker.py'
    bootstrap.write_text('import fixture_adapters\nfrom voice_bridge.desktop.worker import main\nraise SystemExit(main())\n')
    popen=subprocess.Popen
    def spawn(command,*args,**kwargs):
        command=list(command)
        if '-m' in command and command[command.index('-m')+1]=='voice_bridge.desktop.worker':
            idx=command.index('-m');command[idx:idx+2]=[str(bootstrap)]
        return popen(command,*args,**kwargs)
    monkeypatch.setattr(subprocess,'Popen',spawn)
    marker=tmp_path/'fail-clipboard'
    monkeypatch.setenv('VB_FAIL_CLIP',str(marker))
    copy=tmp_path/'fake'/'wl-copy'
    copy.write_text('''#!/usr/bin/env python3
import os,sys
from pathlib import Path
if Path(os.environ['VB_FAIL_CLIP']).exists():
    sys.stderr.write('intentional clipboard failure')
    sys.exit(5)
Path(os.environ['VB_CLIP']).write_bytes(sys.stdin.buffer.read())
''')
    copy.chmod(0o755)
    mailbox=tmp_path/'trigger-command.txt'
    def key(value):
        temp=tmp_path/'key.tmp';temp.write_text(value);temp.replace(mailbox)
    def dictate():
        start=len(events);key('press');deadline=time.monotonic()+6
        while not any(e.state=='recording' for e in events[start:]):
            assert time.monotonic()<deadline,events;time.sleep(.02)
        key('release')
        while not any(e.state=='operation_idle' for e in events[start:]):
            assert time.monotonic()<deadline,events;time.sleep(.02)
        return events[start:]
    runtime=DesktopRuntime(cfg,events.append,diagnostics_only=True)
    try:
        runtime.gui_active=False;runtime.start(reason='linux-native-process-test');wait_state(events,'delivery_ready')
        pid=runtime._process.pid
        runtime.load_model();wait_state(events,'ready')
        marker.touch()
        failed=dictate()
        assert any(e.state=='transcript' for e in failed)
        assert any(e.state in {'error','delivery_deferred'} for e in failed)
        assert '[1, 29, 1]' not in (tmp_path/'hardware.jsonl').read_text()
        marker.unlink()
        for _ in range(2):
            recent=dictate()
            assert not any(e.state=='error' for e in recent)
            assert (tmp_path/'clipboard.txt').read_text()=='Привет, мир.'
        assert runtime._process.pid==pid
        log=(tmp_path/'model-events.txt').read_text()
        assert log.count('model-created')==1 and log.count('transcribed')==3
        assert (tmp_path/'hardware.jsonl').read_text().count('[1, 29, 1]')==2
    finally:
        process=runtime._process;runtime.stop(reason='test-complete')
        assert process.poll() is not None
    assert 'closed' in (tmp_path/'hardware.jsonl').read_text()
