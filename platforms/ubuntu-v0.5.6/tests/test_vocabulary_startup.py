"""Regression for 0.5.0 Roaming directory being opened as a vocabulary file."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from voice_bridge.config import Config, load_config, resolve_optional_config_path
from voice_bridge.desktop.settings_store import save_config
from voice_bridge.text_engine import TextEngine
import voice_bridge.factory as factory


def config_in_roaming(tmp_path, value):
    directory = tmp_path/'AppData'/'Roaming'/'VoiceBridge'
    cfg = Config(); cfg.text.vocabulary_file = value
    save_config(cfg, directory/'config.toml')
    return load_config(directory/'config.toml')


def stub_asr(monkeypatch):
    asr = Mock()
    monkeypatch.setattr(factory, 'QwenASRProvider', asr)
    monkeypatch.setattr(factory, 'AudioRecorder', Mock())
    return asr


@pytest.mark.parametrize('value', ['', ' ', '\t\n', '   \t   '])
def test_saved_empty_vocabulary_starts_real_controller_without_reading_roaming(tmp_path, monkeypatch, value):
    cfg = config_in_roaming(tmp_path, value)
    asr = stub_asr(monkeypatch)
    def no_file_read(*args, **kwargs):
        pytest.fail('A disabled vocabulary must never attempt read_text')
    monkeypatch.setattr(Path, 'read_text', no_file_read)
    controller = factory.create_controller(cfg, injector_override=Mock())
    asr.assert_called_once()
    assert controller.text_engine.vocabulary == []
    assert controller.text_engine.vocabulary_warning is None
    assert controller.text_engine.process('привет запятая мир точка').text == 'Привет, мир.'


@pytest.mark.parametrize('value', [None, '', ' ', '\t'])
def test_optional_path_does_not_resolve_empty_value(value):
    assert resolve_optional_config_path(Config(), value) is None


def test_nonempty_relative_vocab_preserves_existing_cli_resolution(tmp_path):
    cfg = Config(); cfg.source_path = tmp_path/'config'/'default.toml'
    assert resolve_optional_config_path(cfg, 'config/vocabulary.txt') == tmp_path/'config'/'vocabulary.txt'


def test_absolute_vocabulary_not_rebased(tmp_path):
    cfg = config_in_roaming(tmp_path, '')
    target = tmp_path/'словарь.txt'
    assert resolve_optional_config_path(cfg, str(target)) == target


def test_directory_warns_before_asr_loading_and_controller_still_starts(tmp_path, monkeypatch):
    cfg = config_in_roaming(tmp_path, str(tmp_path))
    order = []
    stub_asr(monkeypatch).side_effect = lambda **_: order.append('asr') or Mock()
    events = []
    def emit(state, text):
        events.append((state, text)); order.append(state)
    controller = factory.create_controller(cfg, injector_override=Mock(), state_callback=emit)
    assert [state for state in order if state != 'model_stage'] == ['warning', 'asr']
    warnings = [detail for state, detail in events if state == 'warning']
    assert len(warnings) == 1 and 'Диктовка работает без словаря' in warnings[0]
    assert controller.text_engine.vocabulary == []


def test_missing_vocabulary_does_not_prevent_start(tmp_path, monkeypatch):
    cfg = config_in_roaming(tmp_path, str(tmp_path/'missing.txt'))
    stub_asr(monkeypatch)
    controller = factory.create_controller(cfg, injector_override=Mock())
    assert controller.text_engine.vocabulary_warning
    assert controller.text_engine.process('добрый день').text == 'Добрый день'


def test_unreadable_vocabulary_is_nonfatal_and_visible(tmp_path, monkeypatch):
    path=tmp_path/'vocab.txt';path.write_text('x = y')
    def denied(*a, **kw): raise PermissionError('simulated ACL denial')
    monkeypatch.setattr(Path, 'read_text', denied)
    engine=TextEngine(vocabulary_file=path)
    assert engine.vocabulary == []
    assert 'simulated ACL denial' in engine.vocabulary_warning


def test_invalid_encoding_warns_instead_of_crashing(tmp_path):
    p=tmp_path/'vocab.txt';p.write_bytes(b'\xff\xff=\xfe')
    e=TextEngine(vocabulary_file=p)
    assert e.vocabulary == [] and 'UTF-8' in e.vocabulary_warning


def test_utf8_bom_first_phrase_is_applied(tmp_path):
    p=tmp_path/'vocab.txt';p.write_text('давинчи = DaVinci\n',encoding='utf-8-sig')
    e=TextEngine(vocabulary_file=p)
    assert e.process('давинчи', mode='raw').text == 'DaVinci'
    assert e.vocabulary_warning is None


def test_longest_replacement_first_still_works(tmp_path):
    p=tmp_path/'vocab.txt';p.write_text('давинчи = DaVinci\nдавинчи резолв = Resolve\n',encoding='utf-8')
    assert TextEngine(vocabulary_file=p).process('давинчи резолв',mode='raw').text == 'Resolve'


def test_ensure_desktop_config_empty_default_connects_through_factory(tmp_path,monkeypatch):
    import voice_bridge.desktop.settings_store as store
    path=tmp_path/'AppData'/'Roaming'/'VoiceBridge'/'config.toml'
    monkeypatch.setattr(store,'default_config_path',lambda:path)
    cfg=load_config(store.ensure_user_config());stub_asr(monkeypatch)
    controller=factory.create_controller(cfg,injector_override=Mock())
    assert cfg.text.vocabulary_file == '' and not controller.text_engine.vocabulary_warning


def test_worker_reaches_ready_after_empty_vocabulary_not_only_widget_smoke(tmp_path,monkeypatch):
    import voice_bridge.desktop.worker as worker
    import voice_bridge.triggers as triggers
    cfg=config_in_roaming(tmp_path,'');stub_asr(monkeypatch)
    monkeypatch.setattr(worker,'create_injector',lambda **_:Mock())
    import threading
    class Trigger:
        on_ready=None
        def __init__(self): self.stopped=threading.Event()
        def set_enabled(self,value): pass
        def run(self,*args):
            self.on_ready()
            self.stopped.wait(3)
        def stop(self): self.stopped.set()
    monkeypatch.setattr(triggers,'create_trigger',lambda **_:Trigger())
    events=[];engine=worker.Engine(cfg,lambda *args:events.append(args))
    engine.delivery_ready.set();engine._model_started=True
    try:
        engine._load()
        assert engine.controller is not None
        assert any(state=='ready' for state,_ in events)
        assert not any(state=='model_error' for state,_ in events)
    finally:
        engine.close()


def test_warning_event_does_not_disable_ready_model():
    from test_ui_contracts import method
    w=SimpleNamespace(vocabulary_warning=Mock(),_model_enabled=True,_model_ready=True,_last_state='ready')
    method('_on_runtime_event')(w,SimpleNamespace(state='warning',detail='optional file unavailable'))
    w.vocabulary_warning.setText.assert_called_once_with('optional file unavailable')
    w.vocabulary_warning.show.assert_called_once()
    assert w._model_enabled and w._model_ready and w._last_state=='ready'


def test_form_saves_empty_vocabulary_without_touching_model(tmp_path):
    from test_ui_contracts import method
    cfg=Config(); cfg.text.vocabulary_file='old.txt'; cfg.asr.model='keep-model'
    controls={}
    for name in ['mic','placement','mode','language','beep_cb','auto_model_cb','button','consume',
                 'mouse_device','reserve','cpu_ram','prompt','vocabulary_file','keyboard_backend','paste_combo','target_policy']:
        controls[name]=Mock()
    controls['reserve'].text.return_value='1.5';controls['cpu_ram'].text.return_value='16'
    controls['vocabulary_file'].text.return_value='  '
    w=SimpleNamespace(cfg=cfg,**controls)
    method('_apply_form',platform=SimpleNamespace(system=lambda:'Windows'))(w)
    assert w.cfg.text.vocabulary_file == ''
    assert w.cfg.asr.model == 'keep-model'


def test_cli_api_mode_uses_same_disabled_vocabulary_contract(tmp_path,monkeypatch):
    import sys
    import voice_bridge.cli as cli
    import voice_bridge.asr.qwen as qwen
    fake_api=Mock();fake_server=Mock()
    monkeypatch.setitem(sys.modules,'uvicorn',fake_server)
    monkeypatch.setitem(sys.modules,'voice_bridge.api',SimpleNamespace(create_api=fake_api))
    monkeypatch.setattr(qwen,'QwenASRProvider',Mock())
    assert cli.command_serve(config_in_roaming(tmp_path,''))==0
    engine=fake_api.call_args.args[1]
    assert engine.vocabulary==[] and engine.vocabulary_warning is None
