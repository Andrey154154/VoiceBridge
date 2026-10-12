"""Test update transaction semantics without claiming native Windows execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('vb_hotfix',ROOT/'packaging/windows/hotfix/apply_fix.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)


def ini_root(tmp_path):
    root=tmp_path/'VoiceBridge';root.mkdir()
    for name in h.HELPERS: (tmp_path/name).write_bytes(('new '+name).encode())
    (root/'current.ini').write_text('[runtime]\r\npython=C:\\example\\runtime\\pythonw.exe\r\nprevious=old-python\r\nversion=0.5.0\r\n',encoding='utf-16')
    return root


def test_hotfix_is_offline_and_never_installs_dependencies(tmp_path):
    runner=Mock();h.install_wheel(tmp_path,h.NEW,runner)
    args=runner.call_args.args[0]
    assert '--no-deps' in args and '--no-index' in args and '--isolated' in args
    assert 'torch' not in ' '.join(args) and '--user' not in args
    assert args[-1]==str(tmp_path/h.WHEELS[h.NEW])


def test_success_verifies_before_ini_activation(tmp_path):
    root=ini_root(tmp_path);before=(root/'current.ini').read_bytes();seen=[]
    def runner(args,**kwargs):
        assert (root/'current.ini').read_bytes()==before
        seen.append(args)
    h.apply_update(root,tmp_path,h.OLD,h.NEW,runner)
    assert len(seen)==4 and 'verify-runtime.py' in seen[2][-1] and seen[3][-1]=='self-test'
    text,_=h.read_ini(root/'current.ini')
    assert 'version='+h.NEW in text and 'previous=old-python' in text and 'python=C:\\example' in text


@pytest.mark.parametrize('failed_at',[1,2,3,4])
def test_install_version_or_preflight_failure_restores_prior_package_and_ini(tmp_path,failed_at):
    root=ini_root(tmp_path);before=(root/'current.ini').read_bytes();seen=[]
    def runner(args,**kwargs):
        seen.append(args)
        if len(seen)==failed_at:raise RuntimeError('simulated failure')
    with pytest.raises(RuntimeError,match='simulated failure'):h.apply_update(root,tmp_path,h.OLD,h.NEW,runner)
    assert (root/'current.ini').read_bytes()==before
    assert seen[-2][-1]==str(tmp_path/h.WHEELS[h.OLD])
    assert "== '0.5.0'" in seen[-1][-1]


def test_restore_failure_is_not_reported_as_success(tmp_path):
    root=ini_root(tmp_path)
    with pytest.raises(RuntimeError,match='Automatic restore failed'):
        h.apply_update(root,tmp_path,h.OLD,h.NEW,Mock(side_effect=RuntimeError('locked')))


def test_rollback_does_not_run_new_version_preflight(tmp_path):
    root=ini_root(tmp_path);runner=Mock()
    h.apply_update(root,tmp_path,h.NEW,h.OLD,runner)
    assert runner.call_count==2
    assert 'version=0.5.0' in h.read_ini(root/'current.ini')[0]


def test_manifest_validates_every_executed_payload(tmp_path):
    names=[*h.WHEELS.values(),'verify-runtime.py',*h.HELPERS]
    for name in names:(tmp_path/name).write_bytes(name.encode())
    digests={name:hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() for name in names}
    (tmp_path/'payload-hashes.json').write_text(json.dumps(digests))
    h.verify_payload(tmp_path)
    (tmp_path/'verify-runtime.py').write_text('changed')
    with pytest.raises(RuntimeError,match='SHA256'):h.verify_payload(tmp_path)


def test_manifest_refuses_unexpected_path(tmp_path):
    (tmp_path/'payload-hashes.json').write_text(json.dumps({'../external.py':'abc'}))
    with pytest.raises(RuntimeError,match='manifest'):h.verify_payload(tmp_path)


def test_wrong_runtime_outside_installation_is_rejected(tmp_path):
    root=ini_root(tmp_path)
    with pytest.raises(RuntimeError,match='outside'):h.configured_runtime(root)


def test_active_runtime_resolution(tmp_path):
    root=ini_root(tmp_path);runtime=root/'versions'/'0.5.0-test'/'runtime';runtime.mkdir(parents=True)
    for name in ['python.exe','pythonw.exe']:(runtime/name).touch()
    (root/'current.ini').write_text('[runtime]\npython='+str(runtime/'pythonw.exe')+'\n',encoding='utf-16')
    assert h.configured_runtime(root)==runtime/'python.exe'


def test_installer_and_runtime_locks_used_without_killing_other_processes():
    source=(ROOT/'packaging/windows/hotfix/apply_fix.py').read_text()
    assert 'Local\\\\VoiceBridge.Setup.0.4' in source
    assert "('desktop','engine')" in source
    assert 'stack.enter_context(UserInstanceLock' in source
    assert 'taskkill' not in source and 'TerminateProcess' not in source


def test_update_from_051_keeps_same_python_and_previous_pointer(tmp_path):
    root=ini_root(tmp_path)
    (root/'current.ini').write_text('[runtime]\npython=C:\\runtime\\pythonw.exe\nprevious=older\nversion=0.5.1\n',encoding='utf-16')
    h.apply_update(root,tmp_path,'0.5.1',h.NEW,Mock())
    assert 'previous=older' in h.read_ini(root/'current.ini')[0]
    assert (root/'rollback.ps1').read_bytes()==(tmp_path/'rollback.ps1').read_bytes()
    assert list((root/'patch-backups').rglob('current.ini'))


def test_failure_restores_exact_051_package_not_050(tmp_path):
    root=ini_root(tmp_path);calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        if len(calls)==3: raise RuntimeError('preflight failed')
    with pytest.raises(RuntimeError,match='preflight'):h.apply_update(root,tmp_path,'0.5.1',h.NEW,runner)
    assert calls[-2][-1].endswith('voice_input_bridge-0.5.1-py3-none-any.whl')


def test_helper_write_failure_restores_files_and_ini(tmp_path,monkeypatch):
    root=ini_root(tmp_path)
    (root/'rollback.ps1').write_bytes(b'original rollback')
    before=(root/'current.ini').read_bytes();real=h.write_atomic;failed=[]
    def write(path,data):
        if path==root/'rollback.ps1' and not failed:
            failed.append(1);raise PermissionError('simulated helper lock')
        real(path,data)
    monkeypatch.setattr(h,'write_atomic',write)
    with pytest.raises(PermissionError):h.apply_update(root,tmp_path,'0.5.1',h.NEW,Mock())
    assert (root/'current.ini').read_bytes()==before
    assert (root/'rollback.ps1').read_bytes()==b'original rollback'
    assert not (root/'activation.py').exists()


def test_052_is_supported_and_failure_restores_052_not_other_version(tmp_path):
    assert '0.5.2' in h.SUPPORTED
    root=ini_root(tmp_path);calls=[]
    def runner(args,**kwargs):
        calls.append(args)
        if len(calls)==3:raise RuntimeError('Qt preflight failed')
    with pytest.raises(RuntimeError,match='Qt preflight'):
        h.apply_update(root,tmp_path,'0.5.2',h.NEW,runner)
    assert calls[-2][-1].endswith('voice_input_bridge-0.5.2-py3-none-any.whl')
