"""Real filesystem tests of activation; Windows sharing faults are simulated."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('vb_activation', ROOT/'packaging/windows/activation.py')
a = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(a)


@pytest.fixture
def layout(tmp_path):
    root = tmp_path/'Voice Bridge проверка';root.mkdir()
    runtimes = []
    for name in ('old-build', 'new-build'):
        runtime = root/'versions'/name/'runtime';runtime.mkdir(parents=True)
        for filename in ('python.exe','pythonw.exe'):(runtime/filename).touch()
        runtimes.append(str(runtime/'pythonw.exe'))
    return root, *runtimes


def test_first_install_uses_bom_utf16_and_empty_previous(layout):
    root, old, new=layout
    a.activate(root,old,'0.5.1')
    assert (root/'current.ini').read_bytes().startswith(b'\xff\xfe')
    assert a.read_state(root/'current.ini') == {'python':old,'previous':'','version':'0.5.1'}
    assert not list(root.glob('*.bak'))
    assert not list(root.glob('*.tmp'))


def test_upgrade_saves_exact_old_bytes_and_rollback_target(layout):
    root,old,new=layout
    a.activate(root,old,'0.5.1');original=(root/'current.ini').read_bytes()
    a.activate(root,new,'0.5.2')
    assert a.read_state(root/'current.ini')['previous']==old
    assert a.read_state(root/'current.ini')['python']==new
    assert list(root.glob('current.ini.*.bak'))[0].read_bytes()==original


def test_repeated_activation_does_not_destroy_previous(layout):
    root,old,new=layout
    a.activate(root,old,'0.5.1');a.activate(root,new,'0.5.2');a.activate(root,new,'0.5.2')
    assert a.read_state(root/'current.ini')['previous']==old


def test_rollback_and_rollforward_swap_paths_without_guessing_version(layout):
    root,old,new=layout
    a.activate(root,old,'0.5.1');a.activate(root,new,'0.5.2');a.rollback(root)
    assert a.read_state(root/'current.ini')=={'python':old,'previous':new,'version':''}
    a.rollback(root)
    assert a.read_state(root/'current.ini')['python']==new


def test_rollback_without_previous_changes_nothing(layout):
    root,old,new=layout;a.activate(root,old,'0.5.1');before=(root/'current.ini').read_bytes()
    with pytest.raises(ValueError):a.rollback(root)
    assert (root/'current.ini').read_bytes()==before


def test_missing_target_does_not_change_current(layout):
    root,old,new=layout;a.activate(root,old,'0.5.1');before=(root/'current.ini').read_bytes()
    Path(new).unlink()
    with pytest.raises(ValueError,match='missing'):a.activate(root,new,'0.5.2')
    assert (root/'current.ini').read_bytes()==before


@pytest.mark.parametrize('bad', ['','relative/runtime/pythonw.exe','/somewhere/runtime/pythonw.exe','\n','bad\x00path'])
def test_runtime_invalid_paths_fail_before_activation(layout,bad):
    root,old,new=layout
    with pytest.raises(ValueError):a.activate(root,bad,'0.5.2')
    assert not (root/'current.ini').exists()


@pytest.mark.parametrize('bad',['0.5.2\nprevious=bad','0.5','x',''])
def test_version_validation(layout,bad):
    root,old,new=layout
    with pytest.raises(ValueError):a.activate(root,new,bad)
    assert not (root/'current.ini').exists()


@pytest.mark.parametrize('ini', ['[runtime]\npython=x\npython=y\n','[runtime]\nprevious=x\n',
    '[other]\npython=x\n','[runtime]\npython=x\nunknown=y\n','[runtime]\npython=x\n[other]\nfoo=x\n',
    '[DEFAULT]\npython=x\n[runtime]\nprevious=y\n'])
def test_corrupt_ini_is_not_overwritten(layout,ini):
    root,old,new=layout;path=root/'current.ini';path.write_text(ini)
    before=path.read_bytes()
    with pytest.raises((ValueError,a.configparser.Error)):a.activate(root,new,'0.5.2')
    assert path.read_bytes()==before


def test_utf8_legacy_ini_is_read_but_replaced_with_utf16(layout):
    root,old,new=layout;path=root/'current.ini'
    path.write_text('[runtime]\npython='+old+'\n',encoding='utf-8-sig')
    a.activate(root,new,'0.5.2')
    assert path.read_bytes().startswith(b'\xff\xfe')
    assert a.read_state(path)['previous']==old


def test_replacement_error_preserves_old_state_and_cleans_temp(tmp_path):
    path=tmp_path/'current.ini';path.write_bytes(b'original')
    with pytest.raises(PermissionError):
        a.atomic_write(path,b'new',replace=Mock(side_effect=PermissionError('denied')))
    assert path.read_bytes()==b'original'
    assert not list(tmp_path.glob('*.tmp'))
    assert list(tmp_path.glob('*.bak'))[0].read_bytes()==b'original'


def test_symlink_state_file_is_rejected(tmp_path):
    outside=tmp_path/'outside';outside.write_bytes(b'old')
    link=tmp_path/'current.ini';link.symlink_to(outside)
    with pytest.raises(ValueError,match='symbolic'):a.atomic_write(link,b'new')
    assert outside.read_bytes()==b'old'


def test_directory_state_is_rejected(tmp_path):
    p=tmp_path/'current.ini';p.mkdir()
    with pytest.raises(ValueError,match='file'):a.atomic_write(p,b'new')


def test_replacement_does_not_first_unlink_active_file(tmp_path):
    path=tmp_path/'current.ini';path.write_bytes(b'old')
    def replace(source,dest):
        assert dest.read_bytes()==b'old'
        assert source.parent==dest.parent
        assert source.read_bytes()==b'new'
        a.os.replace(source,dest)
    a.atomic_write(path,b'new',replace=replace)
    assert path.read_bytes()==b'new'


@pytest.mark.parametrize('code',[32,33])
def test_sharing_retry_recovery(code):
    err=PermissionError('locked');err.winerror=code
    operation=Mock(side_effect=[err,err,'done']);sleep=Mock()
    assert a.retry_sharing(operation,sleep=sleep)=='done'
    assert operation.call_count==3 and sleep.call_count==2


def test_persistent_sharing_failure_is_bounded():
    err=PermissionError('locked');err.winerror=32
    operation=Mock(side_effect=err);sleep=Mock()
    with pytest.raises(PermissionError):a.retry_sharing(operation,sleep=sleep)
    assert operation.call_count==4 and sleep.call_count==3


def test_access_denied_is_not_retried():
    err=PermissionError('denied');err.winerror=5
    operation=Mock(side_effect=err);sleep=Mock()
    with pytest.raises(PermissionError):a.retry_sharing(operation,sleep=sleep)
    assert operation.call_count==1 and not sleep.called


def test_bootstrap_cli_selftest_runs_without_third_party_packages():
    result=subprocess.run([sys.executable,'-I','-S',str(ROOT/'packaging/windows/activation.py'),'self-test'],
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert 'ACTIVATION SELF-TEST PASSED' in result.stdout


def test_no_powershell_filereplace_and_both_paths_use_shared_module():
    win=ROOT/'packaging/windows'
    setup=(win/'setup-worker.ps1').read_text(encoding='utf-8-sig')
    rollback=(win/'rollback.ps1').read_text(encoding='utf-8-sig')
    assert '::Replace(' not in setup+rollback
    assert "'activation.py'" in setup and "'activation.py'" in rollback
    assert setup.index("'self-test'") < setup.index("'PySide6==")
    assert ' -and -not $ReadyForActivation ' in setup
    assert 'Local\\VoiceBridge.Setup.0.4' in rollback
    assert 'ready-to-activate.json' in setup
    assert "'activation.py'" in (win/'build_release.py').read_text()
