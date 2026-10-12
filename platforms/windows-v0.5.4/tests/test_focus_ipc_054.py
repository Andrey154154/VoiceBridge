"""Real subprocess protocol + production Windows injector, simulated Win32/ASR.
No native Windows or CUDA acceptance is implied by this test.
"""
import json
import subprocess
import time
from pathlib import Path

from voice_bridge.desktop.runtime import DesktopRuntime
from voice_bridge.desktop.settings_store import save_config
from test_worker_ipc import model_hardware_for_child, wait_state


def test_window_switch_live_policy_and_repeated_dictation_in_one_process(tmp_path,monkeypatch):
    cfg=model_hardware_for_child(tmp_path,monkeypatch)
    native=tmp_path/'native-target.json';native.write_text('[101,202]')
    permit=tmp_path/'finish-recognition';sent=tmp_path/'sent.jsonl'
    monkeypatch.setenv('VB_TEST_TARGET',str(native))
    monkeypatch.setenv('VB_TEST_PERMIT',str(permit))
    monkeypatch.setenv('VB_TEST_SENT',str(sent))
    # Test-only bootstrap adapters live OUTSIDE the shipped application package.
    adapter=tmp_path/'fake'/'windows_fixture.py'
    adapter.write_text('''
import json,os,time
from pathlib import Path
import fixture_adapters as fixtures
import voice_bridge.desktop.worker as worker
from voice_bridge.injectors.windows import WindowsTextInjector
class API:
    def target(self):return tuple(json.loads(Path(os.environ['VB_TEST_TARGET']).read_text()))
    def modifiers_down(self):return False
    def set_text(self,text,*,timeout,guard):guard();self.text=text
    def read_text(self,*,timeout,guard):guard();return self.text
    def send(self,events):
        with open(os.environ['VB_TEST_SENT'],'a',encoding='utf-8') as f:
            f.write(json.dumps({'target':self.target(),'text':self.text,'count':len(events)})+'\\n')
        return len(events)
api=API()
worker.create_injector=lambda **kw:WindowsTextInjector(api=api,paste_shortcut=kw['paste_shortcut'])
original=fixtures.Model.transcribe
def gated(self,*args):
    permit=Path(os.environ['VB_TEST_PERMIT'])
    deadline=time.monotonic()+8
    while not permit.exists():
        if time.monotonic()>deadline:raise RuntimeError('Test recognition gate timeout')
        time.sleep(.01)
    permit.unlink()
    return original(self,*args)
fixtures.Model.transcribe=gated
''')
    bootstrap=tmp_path/'fake'/'windows_boot.py'
    bootstrap.write_text('import windows_fixture\nfrom voice_bridge.desktop.worker import main\nraise SystemExit(main())\n')
    popen=subprocess.Popen
    def spawn(command,*a,**kw):
        command=list(command)
        if '-m' in command and command[command.index('-m')+1]=='voice_bridge.desktop.worker':
            index=command.index('-m');command[index:index+2]=[str(bootstrap)]
        return popen(command,*a,**kw)
    monkeypatch.setattr(subprocess,'Popen',spawn)
    events=[];runtime=DesktopRuntime(cfg,events.append,diagnostics_only=True)
    mailbox=tmp_path/'trigger-command.txt'
    def write_atomic(path,text):
        temp=path.with_suffix('.tmp');temp.write_text(text,encoding='utf-8');temp.replace(path)
    def wait_from(start,state):
        deadline=time.monotonic()+6
        while not any(e.state==state for e in events[start:]):
            assert time.monotonic()<deadline,events[start:]
            time.sleep(.01)
    def cycle(start_target,end_target):
        write_atomic(native,json.dumps(start_target))
        start=len(events)
        write_atomic(mailbox,'press');wait_from(start,'recording')
        write_atomic(mailbox,'release');wait_from(start,'processing')
        write_atomic(native,json.dumps(end_target));permit.touch()
        wait_from(start,'operation_idle')
        return events[start:]
    try:
        runtime.gui_active=False
        runtime.start(reason='focus-integration-test');wait_state(events,'delivery_ready')
        pid=runtime._process.pid
        runtime.load_model();wait_state(events,'ready')
        first=cycle((101,202),(303,404))
        assert any(e.state=='paste_requested' for e in first)
        assert json.loads(sent.read_text().splitlines()[0])['target']==[303,404]
        cfg.app.paste_target='window';save_config(cfg,cfg.source_path)
        start=len(events);runtime.configure_delivery();wait_from(start,'config_applied')
        second=cycle((101,202),(303,404))
        assert any(e.state=='delivery_deferred' for e in second)
        assert len(sent.read_text().splitlines())==1
        third=cycle((505,606),(505,707))
        assert any(e.state=='paste_requested' for e in third)
        assert len(sent.read_text().splitlines())==2
        assert runtime._process.pid==pid
        record=(tmp_path/'model-events.txt').read_text()
        assert record.count('model-created')==1 and record.count('transcribed')==3
        assert not any(e.state in {'error','model_error','config_error'} for e in events)
    finally:
        permit.touch();process=runtime._process;runtime.stop(reason='test-complete')
        assert process.poll() is not None
