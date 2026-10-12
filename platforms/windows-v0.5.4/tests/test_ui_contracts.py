"""Behavioral controller tests extracted from real methods (Qt is not simulated as rendered)."""
import ast
import copy
import logging
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from voice_bridge.config import Config, load_config
from voice_bridge.desktop.settings_store import save_config
from voice_bridge.models import ModelError, validate_local_model
from test_models import seed_model
from voice_bridge.desktop.settings_policy import change_kind

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(importlib.util.find_spec('voice_bridge').origin).parent/'desktop/window.py'


def method(name, **extra):
    cls=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='MainWindow')
    fn=copy.deepcopy(next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==name))
    # Run actual method body with plain view mocks; this is NOT a Qt rendering test.
    fn.decorator_list=[]
    fn.returns=None
    for arg in fn.args.args: arg.annotation=None
    mod=ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[]))
    env=dict(Path=Path,deepcopy=copy.deepcopy,change_kind=change_kind,validate_local_model=validate_local_model,ModelError=ModelError,
             save_config=save_config,load_config=load_config,human_size=lambda n:str(n),LOG=logging.getLogger('test'))
    env.update(extra)
    exec(compile(mod,str(SOURCE),'exec'),env)
    return env[name]


def view(tmp_path):
    cfg=Config();cfg.asr.model='';path=tmp_path/'config.toml';save_config(cfg,path)
    w=SimpleNamespace(cfg=load_config(path),cfg_path=path,_model_ready=False,_model_enabled=False,
        _start_engine=False,runtime=None,_busy=lambda:False,
        _busy_message=lambda:'Завершите текущую операцию',_allow_close=False,
        _applied_cfg=copy.deepcopy(cfg),_runtime_model_requested=False,
        model=Mock(),local_status=Mock(),active_model_status=Mock(),_refresh_home=Mock(),start_runtime=Mock())
    return w


def test_local_choice_validates_before_config_change(tmp_path):
    w=view(tmp_path); old=w.cfg_path.read_bytes()
    with pytest.raises(ModelError): method('_select_model')(w,tmp_path/'missing')
    assert w.cfg_path.read_bytes()==old and w.cfg.asr.model==''


def test_local_choice_commits_but_does_not_load_or_download(tmp_path):
    w=view(tmp_path); target=seed_model(tmp_path/'model')
    method('_select_model')(w,target)
    assert load_config(w.cfg_path).asr.model==str(target)
    assert not w._model_enabled
    w.start_runtime.assert_not_called()


def test_busy_dictation_cannot_be_interrupted_by_model_completion(tmp_path):
    w=view(tmp_path);w._busy=lambda:True
    with pytest.raises(ModelError,match='Завершите'): method('_select_model')(w,seed_model(tmp_path/'model'))
    assert load_config(w.cfg_path).asr.model==''


def test_failed_save_keeps_selected_model(tmp_path):
    w=view(tmp_path); save=Mock(side_effect=OSError('disk full'))
    with pytest.raises(OSError): method('_select_model',save_config=save)(w,seed_model(tmp_path/'model'))
    assert w.cfg.asr.model=='' and load_config(w.cfg_path).asr.model==''


def test_stale_runtime_events_ignored():
    w=SimpleNamespace(_runtime_generation=4,_allow_close=False,_on_runtime_event=Mock())
    receive=method('_receive_runtime_event')
    receive(w,3,'stale');w._on_runtime_event.assert_not_called()
    receive(w,4,'live');w._on_runtime_event.assert_called_once_with('live')


def test_internal_success_requires_actual_matching_text():
    editor=Mock();editor.toPlainText.return_value='not expected'
    w=SimpleNamespace(_selftest_expected='expected',test_editor=editor,test_status=Mock(),paste_summary=Mock(),_test_controls=Mock(),_refresh_home=Mock())
    method('_verify_test_field')(w,attempts=0)
    assert 'НЕ ПОДТВЕРЖДЕНО' in w.test_status.setText.call_args.args[0]
    w.paste_summary.setText.assert_not_called()


def test_internal_matching_text_sets_verified():
    editor=Mock();editor.toPlainText.return_value='expected'
    w=SimpleNamespace(_selftest_expected='expected',test_editor=editor,test_status=Mock(),paste_summary=Mock(),_test_controls=Mock(),_refresh_home=Mock())
    method('_verify_test_field')(w,attempts=0)
    assert w.test_status.setText.call_args.args[0].startswith('ПОДТВЕРЖДЕНО')
    assert w._selftest_expected is None


def test_model_directory_and_beep_persist(tmp_path):
    cfg=Config();cfg.app.models_directory=str(tmp_path/'Другой диск');cfg.app.download_repo='Qwen/Qwen3-ASR-0.6B-hf';cfg.app.beep=True;cfg.app.start_model_on_launch=False
    p=tmp_path/'cfg.toml';save_config(cfg,p);result=load_config(p)
    assert result.app==cfg.app


def test_uninstaller_preserves_models_folder():
    source=(ROOT/'packaging/windows/uninstall.ps1').read_text(encoding='utf-8-sig')
    assert "$_ .Name" not in source
    assert "Where-Object { $_.Name -ne 'models' }" in source
    assert 'Remove-Item -LiteralPath $root -Recurse -Force' not in source


def test_preflight_instantiates_all_pages_before_activation():
    verify=(ROOT/'packaging/windows/verify-runtime.py').read_text()
    assert 'MainWindow(load_config(cfg_path), cfg_path, start_engine=False)' in verify
    assert 'assert window.pages.count() == 4' in verify
    worker=(ROOT/'packaging/windows/setup-worker.ps1').read_text(encoding='utf-8-sig')
    assert worker.index("'verify-runtime.py'")<worker.index("'activate','--root'")


def test_safe_hf_catalog_and_local_runtime_policy():
    from voice_bridge.models import CATALOG
    assert {s.repo for s in CATALOG}=={'Qwen/Qwen3-ASR-1.7B-hf','Qwen/Qwen3-ASR-0.6B-hf'}
    source=(ROOT/'src/voice_bridge/asr/qwen.py').read_text()
    assert 'trust_remote_code=False' in source and "load_kwargs['trust_remote_code'] = False" in source


def test_model_start_validation_rolls_back_in_memory_settings(tmp_path):
    w=view(tmp_path); w.cfg.asr.model=str(seed_model(tmp_path/'model'));save_config(w.cfg,w.cfg_path)
    w.active_model_status=Mock();w.status=Mock();w._navigate=Mock()
    original=w.cfg.app.mode
    def invalid_form():
        w.cfg.app.mode='raw'
        raise ValueError('invalid memory limit')
    w._apply_form=invalid_form
    modules=SimpleNamespace(util=SimpleNamespace(find_spec=lambda name:object()))
    method('_load_model_now',deepcopy=copy.deepcopy,importlib=modules)(w)
    assert w.cfg.app.mode==original and load_config(w.cfg_path).app.mode==original
    w.start_runtime.assert_not_called()


def test_autostart_failure_does_not_claim_profile_was_not_saved(tmp_path):
    w=view(tmp_path);w.autostart_cb=Mock();w.footer=Mock();w.cfg.app.mode='raw'
    w._apply_form=lambda:None
    dialog=Mock();startup=Mock();startup.set_enabled.side_effect=OSError('registry denied')
    method('_save_and_restart',QMessageBox=dialog,autostart=startup)(w)
    assert load_config(w.cfg_path).app.mode=='raw' and w.cfg.app.mode=='raw'
    dialog.critical.assert_not_called();dialog.warning.assert_called_once()
