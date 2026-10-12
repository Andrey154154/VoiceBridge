from types import SimpleNamespace

from voice_bridge.triggers.evdev_trigger import _is_consumed_target_event


def event(event_type, code, value=1):
    return SimpleNamespace(type=event_type, code=code, value=value)


def test_only_configured_key_event_is_consumed():
    EV_KEY = 1
    BTN_SIDE = 275
    assert _is_consumed_target_event(event(EV_KEY, BTN_SIDE), target_code=BTN_SIDE, ev_key=EV_KEY)
    assert not _is_consumed_target_event(event(EV_KEY, 272), target_code=BTN_SIDE, ev_key=EV_KEY)
    assert not _is_consumed_target_event(event(2, BTN_SIDE), target_code=BTN_SIDE, ev_key=EV_KEY)


def test_release_of_target_is_consumed_too():
    EV_KEY = 1
    BTN_SIDE = 275
    assert _is_consumed_target_event(event(EV_KEY, BTN_SIDE, 0), target_code=BTN_SIDE, ev_key=EV_KEY)
