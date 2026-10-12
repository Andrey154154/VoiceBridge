"""Execute production GUI handler bodies. No Qt rendering is claimed here."""
import copy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock
import pytest
from voice_bridge.config import load_config
from voice_bridge.desktop.runtime import RuntimeEvent
from voice_bridge.desktop.settings_store import save_config
from test_ui_contracts import method, view
from test_models import seed_model


def ui(tmp_path):
    w=view(tmp_path)
    w.cfg.asr.model=str(seed_model(tmp_path/'model'));save_config(w.cfg,w.cfg_path)
    w._applied_cfg=copy.deepcopy(w.cfg)
    w._start_engine=True
    w._last_state='ready';w._model_loading=False;w._operation_busy=False
    w._test_active=False;w._config_pending=False;w._restart_pending=False;w._delivery_ready=True
    w._runtime_model_requested=False;w._allow_close=False
    w._runtime_generation=1;w._test_generation=1;w._next_start_reason='test';w._pending_config=None
    w.runtime=Mock(running=True,enabled=True)
    w._busy=MethodType(method('_busy'),w)
    w._apply_form=Mock();w._request_restart=Mock();w.footer=Mock();w.status=Mock();w.status_badge=Mock()
    w._navigate=Mock();w.autostart_cb=Mock();w.delivery_info=Mock();w.test_status=Mock()
    w._on_runtime_event=Mock();w.signals=Mock()
    return w


@pytest.mark.parametrize('flag',['_model_loading','_operation_busy','_test_active','_config_pending','_restart_pending'])
def test_save_rejected_without_disk_write_or_stop_during_any_operation(tmp_path,flag):
    w=ui(tmp_path);setattr(w,flag,True);before=w.cfg_path.read_bytes()
    method('_save_and_restart')(w)
    assert w.cfg_path.read_bytes()==before
    w._apply_form.assert_not_called();w.runtime.stop.assert_not_called();w._request_restart.assert_not_called()


def test_loading_state_alone_also_protects_save(tmp_path):
    w=ui(tmp_path);w._last_state='loading'
    method('_save_and_restart')(w)
    w._apply_form.assert_not_called();w.runtime.stop.assert_not_called()


def test_noop_save_and_ui_only_save_send_no_command(tmp_path):
    w=ui(tmp_path);w._runtime_model_requested=True;w._model_ready=True
    for form in [lambda:None,lambda:setattr(w.cfg.app,'beep',True)]:
        w._apply_form=form
        method('_save_and_restart',autostart=Mock())(w)
    w.runtime.configure.assert_not_called();w._request_restart.assert_not_called()
    assert w._model_ready


def test_live_save_waits_for_ack_and_retains_loaded_model(tmp_path):
    w=ui(tmp_path);w._runtime_model_requested=True;w._model_ready=True
    w._apply_form=lambda:setattr(w.cfg.audio,'input_device','new microphone')
    method('_save_and_restart',autostart=Mock())(w)
    w.runtime.configure.assert_called_once();w.runtime.stop.assert_not_called()
    w._request_restart.assert_not_called();assert w._config_pending
    assert w._applied_cfg.audio.input_device != 'new microphone'
    method('_on_runtime_event')(w,RuntimeEvent('config_applied','OK'))
    assert not w._config_pending and w._applied_cfg.audio.input_device=='new microphone'
    assert w._model_ready


def test_model_parameter_change_is_explicit_single_restart(tmp_path):
    w=ui(tmp_path);w._runtime_model_requested=True;w._model_enabled=True
    w._apply_form=lambda:setattr(w.cfg.asr,'gpu_reserve_gb',9.0)
    method('_save_and_restart',autostart=Mock())(w)
    w._request_restart.assert_called_once_with('model_parameters_changed',load_model=True)
    w.runtime.configure.assert_not_called()


def test_load_uses_existing_diagnostic_pid_and_duplicate_does_not_reload(tmp_path):
    w=ui(tmp_path)
    imports=SimpleNamespace(util=SimpleNamespace(find_spec=lambda _:True))
    action=method('_load_model_now',importlib=imports)
    action(w);action(w)
    w.runtime.load_model.assert_called_once();w.runtime.stop.assert_not_called()
    w.start_runtime.assert_not_called();w._request_restart.assert_not_called()
    assert w._model_loading


def test_same_local_model_choice_preserves_ready_and_process(tmp_path):
    w=ui(tmp_path);w._model_ready=True;w._model_enabled=True
    before=w.cfg_path.read_bytes()
    method('_select_model')(w,w.cfg.asr.model)
    assert w._model_ready and w._model_enabled and before==w.cfg_path.read_bytes()
    w._request_restart.assert_not_called()


def test_cancel_loading_is_the_only_cancel_path_that_replaces_process(tmp_path):
    w=ui(tmp_path);w._model_loading=True
    method('_cancel_operation')(w)
    w._request_restart.assert_called_once_with('user_cancel_model_load',load_model=False)
    w.runtime.cancel.assert_not_called()


def test_cancel_inference_does_not_claim_idle(tmp_path):
    w=ui(tmp_path);w._operation_busy=True
    method('_cancel_operation')(w)
    w.runtime.cancel.assert_called_once();w._request_restart.assert_not_called()
    assert w._operation_busy
    method('_on_runtime_event')(w,RuntimeEvent('operation_idle'))
    assert not w._operation_busy


def test_stale_stop_completion_does_not_start_new_child(tmp_path):
    w=ui(tmp_path);w._runtime_generation=10;w._restart_pending=True
    method('_runtime_stopped')(w,9,'')
    assert w._restart_pending
    w.start_runtime.assert_not_called()


def test_failed_stop_does_not_launch_second_child(tmp_path):
    w=ui(tmp_path);w._restart_pending=True
    method('_runtime_stopped')(w,1,'cannot stop')
    w.start_runtime.assert_not_called();assert w.runtime is not None
    assert not w._restart_pending and w._last_state=='error'


def test_delivery_failure_exits_starting_not_permanent_loading(tmp_path):
    w=ui(tmp_path);w._model_loading=True;w._last_state='starting'
    method('_on_runtime_event')(w,RuntimeEvent('delivery_error','device unavailable'))
    assert not w._busy() and not w._delivery_ready and not w._model_ready


def test_idempotent_ui_start_does_not_replace_runtime(tmp_path):
    w=ui(tmp_path);runtime=w.runtime
    method('start_runtime')(w)
    assert w.runtime is runtime
    runtime.start.assert_not_called();runtime.stop.assert_not_called()


def test_cancel_before_internal_timer_fires_invalidates_scheduled_send(tmp_path):
    w=ui(tmp_path);w._test_active=True;w._test_dispatched=False;w._selftest_expected='test'
    old=w._test_generation
    method('_cancel_operation')(w)
    assert w._test_generation==old+1 and not w._test_active
    assert w._selftest_expected is None
    w.runtime.paste_test.assert_not_called()
