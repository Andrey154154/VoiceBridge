"""Real Bash transaction, apt/sudo/pip replaced by isolated test executables."""
from pathlib import Path
import hashlib

from test_linux_install_transaction_055 import sandbox,run


def test_replace_055_removes_only_app_then_installs_056_and_keeps_data(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path,old='0.5.5')
    model=tmp_path/'existing-model';model.mkdir();weights=model/'model.safetensors';weights.write_bytes(b'untouched')
    runtime_marker=app/'runtime/pytorch-present';runtime_marker.write_text('keep')
    before=cfg.read_bytes()
    result=run(folder,env,'--replace')
    assert result.returncode==0,result.stdout+result.stderr
    commands=log.read_text();remove=commands.index('apt-get remove -y voice-bridge')
    assert remove<commands.index('apt-get install')<commands.index('--no-index --no-deps')
    assert commands.count('apt-get remove')==1
    assert cfg.read_bytes()==before and weights.read_bytes()==b'untouched' and runtime_marker.exists()
    assert state.read_text()=='0.5.6' and 'DONE: VoiceBridge 0.5.6' in result.stdout
    assert 'bootstrap-runtime' not in commands and 'autoremove' not in commands and 'purge' not in commands


def test_replace_preflight_failure_reinstalls_original_055_not_success(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path,old='0.5.5')
    before=cfg.read_bytes();env['VB_FAIL_PREFLIGHT']='1'
    result=run(folder,env,'--replace')
    assert result.returncode and 'DONE:' not in result.stdout
    assert state.read_text()=='0.5.5' and cfg.read_bytes()==before
    assert 'восстановлено' in result.stderr
    assert 'dpkg -i' in log.read_text() and 'voice_input_bridge-0.5.5' in log.read_text()


def test_replace_refuses_bad_hash_before_removing_anything(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path,old='0.5.5')
    (folder/'voice_input_bridge-0.5.6-py3-none-any.whl').write_text('corrupted')
    result=run(folder,env,'--replace')
    assert result.returncode and not log.exists() and state.read_text()=='0.5.5'


def test_replace_fresh_install_has_nothing_to_remove(tmp_path):
    folder,app,env,cfg,state,log=sandbox(tmp_path,old='')
    result=run(folder,env,'--replace')
    assert result.returncode==0,result.stdout+result.stderr
    assert 'apt-get remove' not in log.read_text()


def test_reinstall_wrapper_enters_transaction_instead_of_unprotected_delete():
    root=Path(__file__).resolve().parents[1]
    wrapper=(root/'packaging/reinstall.sh').read_text()
    assert 'exec bash "$HERE/install.sh" --replace "$@"' in wrapper
    assert 'rm ' not in wrapper and 'sudo ' not in wrapper
