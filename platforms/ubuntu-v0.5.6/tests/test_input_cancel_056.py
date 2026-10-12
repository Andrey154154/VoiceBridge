"""Cancellation must veto delivery before the evdev read loop returns to polling."""
import threading
from unittest.mock import Mock

import pytest

from test_lifecycle_053 import engine_fixture, load, dictate, wait_for, Trigger
from voice_bridge.desktop.worker import GuardedInjector


def test_linux_callback_is_bound_to_current_trigger(engine_fixture,monkeypatch):
    monkeypatch.setattr(Trigger,'on_cancel',None,raising=False)
    f=engine_fixture;load(f)
    assert callable(f.hooks[-1].on_cancel)
    f.hooks[-1].on_cancel()
    wait_for(lambda:any(e[0]=='warning' for e in f.events))
    assert f.engine._trigger_online and len(f.models)==1 and len(f.hooks)==1


def test_loss_during_recording_cancels_without_submitting_asr(engine_fixture):
    f=engine_fixture;load(f)
    f.hooks[-1].press();wait_for(lambda:f.engine.controller.recorder.is_recording)
    f.engine._input_lost(f.engine._trigger_epoch)
    wait_for(lambda:not f.engine.controller.busy)
    f.hooks[-1].release()
    assert f.asr.calls==0 and not f.api.sent
    for _ in range(2): dictate(f)
    assert f.asr.calls==2 and len(f.api.sent)==2 and len(f.models)==len(f.hooks)==1
    assert not any(e[0]=='model_error' for e in f.events)


def test_loss_during_asr_preserves_text_but_vetoes_paste(engine_fixture):
    f=engine_fixture;load(f);f.asr.proceed.clear()
    f.hooks[-1].press();wait_for(lambda:f.engine.controller.recorder.is_recording)
    f.hooks[-1].release();assert f.asr.entered.wait(2)
    f.engine._input_lost(f.engine._trigger_epoch)
    assert f.engine.controller._cancelled.is_set()
    f.asr.proceed.set();wait_for(lambda:not f.engine.controller.busy)
    wait_for(lambda:not f.engine._input_reset_pending.is_set())
    assert not f.api.sent and f.engine.last_text=='Привет, мир.'
    dictate(f);assert len(f.api.sent)==1 and len(f.models)==1


def test_input_loss_callback_never_waits_for_operation_lock_or_portaudio(engine_fixture):
    f=engine_fixture;load(f)
    f.engine.controller.cancel=Mock(side_effect=AssertionError('must not run on reader'))
    with f.engine._operation_lock:
        done=threading.Event()
        def drop(): f.engine._input_lost(f.engine._trigger_epoch);done.set()
        thread=threading.Thread(target=drop);thread.start()
        assert done.wait(.5)
        assert f.engine._input_reset_pending.is_set() and f.engine.controller._cancelled.is_set()
        f.engine.controller.cancel.assert_not_called()
        f.engine._input_reset_pending.clear()
    thread.join(1)
    # Restore original method for fixture's cooperative close.
    from voice_bridge.controller import VoiceBridgeController
    f.engine.controller.cancel=VoiceBridgeController.cancel.__get__(f.engine.controller)


def test_queued_press_before_loss_is_invalidated_not_delayed_until_recovery(engine_fixture):
    f=engine_fixture;load(f)
    with f.engine._operation_lock:
        f.hooks[-1].press();f.hooks[-1].release()
        f.engine._input_lost(f.engine._trigger_epoch)
    wait_for(lambda:any(e[0]=='warning' for e in f.events))
    assert f.asr.calls==0 and not f.engine.controller.recorder.is_recording
    dictate(f);assert f.asr.calls==1


def test_loss_bypasses_full_control_queue(engine_fixture):
    f=engine_fixture;load(f)
    with f.engine._operation_lock:
        for _ in range(128):
            try: f.engine._events.put_nowait((f.engine._input_epoch,'unused'))
            except Exception: break
        f.engine._input_lost(f.engine._trigger_epoch)
    wait_for(lambda:any(e[0]=='warning' for e in f.events))
    assert f.engine._trigger_online and not f.engine._input_reset_pending.is_set()


def test_old_trigger_loss_does_not_cancel_new_trigger(engine_fixture):
    f=engine_fixture;load(f)
    f.engine.controller._cancelled.clear()
    f.engine._input_lost(f.engine._trigger_epoch-1)
    assert not f.engine._input_reset_pending.is_set() and not f.engine.controller._cancelled.is_set()


def test_delivery_guard_rejects_loss_even_if_cancel_flag_cleared_by_racing_press(engine_fixture):
    f=engine_fixture;load(f)
    with f.engine._operation_lock:
        f.engine._input_reset_pending.set();f.engine.controller._cancelled.clear()
        with pytest.raises(RuntimeError,match='Paste cancelled'):
            GuardedInjector(f.engine).insert('not to be sent')
        assert not f.api.sent
    wait_for(lambda:not f.engine._input_reset_pending.is_set())
