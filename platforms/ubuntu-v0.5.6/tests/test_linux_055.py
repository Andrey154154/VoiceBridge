"""Ubuntu-specific delivery and device lifecycle. OS devices are explicit test doubles."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from voice_bridge.triggers.evdev_trigger import EvdevHoldTrigger
from voice_bridge.triggers.pynput_trigger import PynputHoldTrigger
from voice_bridge import triggers
from voice_bridge.desktop import autostart, settings_store
from voice_bridge.config import Config, load_config

ROOT = Path(__file__).resolve().parents[1]


def input_module(monkeypatch, devices):
    def open_device(path):
        item=devices[path]
        if isinstance(item, Exception): raise item
        return item
    listed=Mock(side_effect=lambda writable=True: [] if writable else list(devices))
    module=NS(list_devices=listed,InputDevice=open_device,
              ecodes=NS(ecodes={'BTN_SIDE':275,'BTN_EXTRA':276},EV_KEY=1,EV_SYN=0,SYN_REPORT=0,SYN_DROPPED=3))
    monkeypatch.setitem(sys.modules,'evdev',module)
    return module


def device(name='MX Master', path='/dev/input/event2', keys=(275,276)):
    d=Mock();d.name=name;d.phys='usb-0001';d.path=path
    d.capabilities.return_value={1:list(keys)};d.active_keys.return_value=[]
    d.read.side_effect=BlockingIOError
    return d


def test_readonly_device_is_visible_and_selected(monkeypatch):
    d=device();m=input_module(monkeypatch,{d.path:d})
    selected,code=EvdevHoldTrigger()._select_device()
    assert selected is d and code==275
    m.list_devices.assert_called_once_with(writable=False)
    d.close.assert_not_called()


def test_legacy_evdev_uses_readable_paths_not_rw_default(monkeypatch):
    import voice_bridge.triggers.evdev_trigger as mod
    def legacy(): return []  # Legacy implementation would incorrectly omit R-only devices.
    monkeypatch.setattr(mod.glob,'glob',lambda pattern:['/dev/input/event1','/dev/input/event2'])
    monkeypatch.setattr(mod.os,'access',lambda path,mode: mode==os.R_OK and path.endswith('2'))
    assert EvdevHoldTrigger._readable_paths(NS(list_devices=legacy))==['/dev/input/event2']


def test_unplugged_and_inaccessible_device_do_not_abort_search(monkeypatch):
    d=device();m=input_module(monkeypatch,{'/dev/input/event0':PermissionError(),'/dev/input/event1':FileNotFoundError(),d.path:d})
    assert EvdevHoldTrigger()._select_device()==(d,275)
    assert EvdevHoldTrigger.devices(mouse_only=True)==[(d.path,d.name)]


def test_brand_name_without_mouse_word_is_listed(monkeypatch):
    d=device('MX Master 3S');input_module(monkeypatch,{d.path:d})
    assert EvdevHoldTrigger.devices(mouse_only=True)==[(d.path,'MX Master 3S')]
    d.close.assert_called_once()


def test_list_excludes_keyboards_and_virtual_devices(monkeypatch):
    keyboard=device('USB Keyboard','/dev/input/event0',(29,47))
    virtual=device('Voice Input Bridge proxy - MX Master','/dev/input/event1')
    mouse=device();input_module(monkeypatch,{d.path:d for d in (keyboard,virtual,mouse)})
    assert EvdevHoldTrigger.devices(mouse_only=True)==[(mouse.path,mouse.name)]
    for d in (keyboard,virtual,mouse): d.close.assert_called_once()


def test_exact_device_path_survives_missing_enumeration(monkeypatch):
    d=device();m=input_module(monkeypatch,{d.path:d});m.list_devices.return_value=[];m.list_devices.side_effect=None
    assert EvdevHoldTrigger(device_match=d.path)._select_device()==(d,275)


def test_device_mismatch_does_not_silently_fallback(monkeypatch):
    d=device();input_module(monkeypatch,{d.path:d})
    with pytest.raises(RuntimeError,match='Не найдена'):
        EvdevHoldTrigger(device_match='another')._select_device()
    d.close.assert_called_once()


@pytest.mark.parametrize('value',[1,0,2])
def test_disabled_target_is_not_consumed(monkeypatch,value):
    t=EvdevHoldTrigger(consume=True);t.set_enabled(False)
    press=Mock();release=Mock()
    assert not t._target_event(value,press,release)
    press.assert_not_called();release.assert_not_called()


def test_pause_midhold_keeps_release_claimed():
    t=EvdevHoldTrigger();p=Mock();r=Mock()
    assert t._target_event(1,p,r)
    t.set_enabled(False)
    assert t._target_event(2,p,r)
    assert t._target_event(0,p,r)
    p.assert_called_once();r.assert_called_once()
    assert not t._target_event(1,p,r)
    assert not t._target_event(0,p,r)


def test_enable_mid_desktop_hold_does_not_claim_release():
    t=EvdevHoldTrigger();p=Mock();r=Mock();t.set_enabled(False)
    assert not t._target_event(1,p,r)
    t.set_enabled(True)
    assert not t._target_event(2,p,r)
    assert not t._target_event(0,p,r)
    p.assert_not_called();r.assert_not_called()
    assert t._target_event(1,p,r) and t._target_event(0,p,r)
    p.assert_called_once();r.assert_called_once()


def test_duplicate_down_and_release_are_not_replayed():
    t=EvdevHoldTrigger();p=Mock();r=Mock()
    for v in (1,1,2,2,0,0): t._target_event(v,p,r)
    p.assert_called_once();r.assert_called_once()


def proxy_fixture(monkeypatch):
    d=device();m=input_module(monkeypatch,{d.path:d});proxy=Mock()
    m.UInput=NS(from_device=Mock(return_value=proxy))
    t=EvdevHoldTrigger(consume=True)
    t._stop_event=NS(wait=lambda _:False,is_set=lambda:False)
    return t,d,proxy


def event(typ,code,value): return NS(type=typ,code=code,value=value)


def test_proxy_forwards_pause_and_other_buttons_releases_on_exit(monkeypatch):
    t,d,proxy=proxy_fixture(monkeypatch);pressed=Mock();released=Mock()
    def events(_):
        yield event(1,275,1);yield event(1,275,0);yield event(0,0,0)  # dictated, not forwarded
        t.set_enabled(False)
        yield event(1,275,1);yield event(1,275,0)  # ordinary X1, forwarded
        yield event(2,0,30);yield event(2,8,-1)    # motion + wheel
        yield event(1,272,1)                     # held button on shutdown
        yield event(0,0,0)
    t._events=events
    t._run_consuming_proxy(d,275,pressed,released)
    assert [c.args for c in proxy.write.call_args_list]==[(1,275,1),(1,275,0),(2,0,30),(2,8,-1),(1,272,1),(1,272,0)]
    pressed.assert_called_once();released.assert_called_once()
    d.ungrab.assert_called_once();proxy.close.assert_called_once()


def test_proxy_incomplete_syn_recovery_still_releases(monkeypatch):
    t,d,proxy=proxy_fixture(monkeypatch)
    t._events=lambda _:iter([event(1,272,1),event(0,0,0),event(0,3,0),event(1,275,1)])
    press=Mock()
    with pytest.raises(RuntimeError,match='SYN_DROPPED'):
        t._run_consuming_proxy(d,275,press,Mock())
    assert [c.args for c in proxy.write.call_args_list]==[(1,272,1),(1,272,0)]
    press.assert_not_called();d.ungrab.assert_called_once();proxy.close.assert_called_once()


def test_proxy_grab_failure_closes_without_publishing_ready(monkeypatch):
    t,d,proxy=proxy_fixture(monkeypatch);d.grab.side_effect=OSError('busy');t.on_ready=Mock()
    with pytest.raises(RuntimeError,match='grab'):
        t._run_consuming_proxy(d,275,Mock(),Mock())
    t.on_ready.assert_not_called();proxy.close.assert_called_once();d.ungrab.assert_not_called()


def test_proxy_creation_failure_never_grabs(monkeypatch):
    t,d,proxy=proxy_fixture(monkeypatch)
    sys.modules['evdev'].UInput.from_device.side_effect=PermissionError('denied')
    with pytest.raises(RuntimeError,match='/dev/uinput'):
        t._run_consuming_proxy(d,275,Mock(),Mock())
    d.grab.assert_not_called()


def test_proxy_waits_for_preexisting_click_before_grab(monkeypatch):
    t,d,proxy=proxy_fixture(monkeypatch);d.active_keys.side_effect=[[272],[],[]]
    t._events=lambda _:iter([])
    t._run_consuming_proxy(d,275,Mock(),Mock())
    assert d.active_keys.call_count==3  # initial click, released, post-grab baseline
    d.grab.assert_called_once();proxy.close.assert_called_once()


@pytest.mark.parametrize('session,consume,expected',[('wayland',False,'EvdevHoldTrigger'),('x11',True,'EvdevHoldTrigger'),('x11',False,'PynputHoldTrigger')])
def test_auto_session_and_consumption_contract(monkeypatch,session,consume,expected):
    monkeypatch.setattr(triggers.platform,'system',lambda:'Linux')
    monkeypatch.delenv('WAYLAND_DISPLAY',raising=False);monkeypatch.setenv('XDG_SESSION_TYPE',session)
    assert type(triggers.create_trigger(consume=consume)).__name__==expected


def test_pynput_listener_ready_stop_and_close_order():
    t=PynputHoldTrigger();listener=Mock();events=[]
    listener.start.side_effect=lambda:events.append('start')
    listener.wait.side_effect=lambda:events.append('wait')
    t.on_ready=lambda:events.append('ready')
    listener.join.side_effect=t.stop
    t._listen(listener)
    assert events==['start','wait','ready'] and t._listener is None
    assert listener.stop.call_count==2


def test_pynput_stop_before_start_never_hooks():
    t=PynputHoldTrigger();listener=Mock();t.stop();t._listen(listener)
    listener.start.assert_not_called();listener.wait.assert_not_called();listener.stop.assert_called_once()


def test_pynput_mouse_disabled_press_not_used(monkeypatch):
    callback={}
    class Listener:
        def __init__(self,**kwargs): callback.update(kwargs)
        def start(self): pass
        def wait(self): pass
        def join(self):
            callback['on_click'](0,0,'X1',True)
            callback['on_click'](0,0,'X1',False)
        def stop(self): pass
    monkeypatch.setitem(sys.modules,'pynput',NS(mouse=NS(Button=NS(x1='X1'),Listener=Listener)))
    t=PynputHoldTrigger();t.set_enabled(False);p=Mock();r=Mock();t.run(p,r)
    p.assert_not_called();r.assert_not_called()


@pytest.mark.parametrize('text',['bad\ncommand','bad\rcommand','bad\0command'])
def test_autostart_rejects_control_lines(text):
    with pytest.raises(ValueError): autostart._desktop_exec_argument(text)


def test_autostart_escapes_desktop_not_shell(tmp_path,monkeypatch):
    monkeypatch.setattr(autostart.platform,'system',lambda:'Linux')
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'конфиг'))
    monkeypatch.setattr(autostart,'runtime_command',lambda:['/tmp/a b/python','-m','voice_bridge.desktop.app'])
    autostart.set_enabled(True)
    text=(tmp_path/'конфиг/autostart/voice-bridge.desktop').read_text()
    assert 'Exec="/tmp/a b/python" "-m" "voice_bridge.desktop.app" --background' in text
    assert "'/tmp/a b/python'" not in text
    autostart.set_enabled(False)
    assert not (tmp_path/'конфиг/autostart/voice-bridge.desktop').exists()


def test_autostart_percent_and_reserved_characters():
    s=autostart._desktop_exec_argument('x%f$`"\\')
    assert s.startswith('"') and s.endswith('"') and '%%f' in s
    assert '\\\\$' in s and '\\\\`' in s and '\\\\"' in s
    assert '\\\\\\\\' in s


def test_linux_generic_new_config_and_existing_bytes_unchanged(tmp_path,monkeypatch):
    target=tmp_path/'config.toml'
    monkeypatch.setattr(settings_store,'default_config_path',lambda:target)
    assert settings_store.ensure_user_config()==target
    cfg=load_config(target)
    assert cfg.trigger.backend=='evdev' and cfg.trigger.consume
    assert not cfg.asr.model and not cfg.text.vocabulary_file
    cfg.asr.model='/tmp/example-user/модель';settings_store.save_config(cfg,target)
    before=target.read_bytes();settings_store.ensure_user_config()
    assert target.read_bytes()==before


def stop_module():
    spec=importlib.util.spec_from_file_location('vb_stop_055',ROOT/'packaging/stop_old.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


@pytest.mark.parametrize('argv',[
 ['python','-X','utf8','-u','-m','voice_bridge.desktop.worker','--config','a'],
 ['python','-m','voice_bridge.desktop.app'],
 ['python','-X','utf8','-m','voice_bridge.cli','run'],
 ['/usr/bin/python','/opt/voice-bridge/runtime/bin/voice-bridge-desktop'],
])
def test_upgrade_recognizes_only_real_app_commands(argv): assert stop_module().matches(argv)


@pytest.mark.parametrize('argv',[
 ['python','-m','unrelated','voice_bridge.desktop.worker'],
 ['python','other.py','-config','voice_bridge.desktop.app'],
 ['python','-m','voice_bridge.cli','--help'],
 ['python','-m','voice_bridge.cli','list-devices'],
])
def test_upgrade_does_not_kill_other_commands(argv): assert not stop_module().matches(argv)


def test_packaging_keeps_update_offline_and_fresh_profile_explicit():
    s=(ROOT/'packaging/install.sh').read_text()
    assert '"$PROFILE" == keep' in s
    branch=s.split('"$PROFILE" == keep',1)[1].split('\nelse',1)[0]
    assert '--no-index --no-deps --force-reinstall' in branch and 'bootstrap-runtime' not in branch
    assert 'flock -w 5 8' in s and 'flock -w 5 9' in s
    assert 'load_config(p)' in s and 'ensure_user_config()' in s and 'save_config(' not in s
    assert '*-local.toml' in (ROOT/'packaging/build.py').read_text()
    assert 'trap recover ERR' in s and 'ОШИБКА ОТКАТА' in s


def test_preflight_is_offscreen_and_never_loads_model():
    s=(ROOT/'packaging/linux/preflight.py').read_text()
    assert "os.environ['QT_QPA_PLATFORM'] = 'offscreen'" in s
    assert 'start_engine=False' in s and 'check_ui_lifecycle(window, app)' in s
    assert 'from_pretrained' not in s and '.grab(' not in s


def test_permissions_do_not_grant_global_input_or_world_write():
    s=(ROOT/'packaging/deb-root/opt/voice-bridge/setup-input-access.sh').read_text()
    assert 'ID_INPUT_MOUSE' in s and "f'u:{user}:r'" in s
    assert 'chmod 666' not in s and 'chmod 777' not in s and 'usermod' not in s


def test_native_libraries_declared():
    s=(ROOT/'packaging/deb-root/DEBIAN/control').read_text()
    for dep in ('python3-dev','linux-libc-dev','build-essential','libxcb-cursor0','libxkbcommon-x11-0','libportaudio2','wl-clipboard','xclip'):
        assert dep in s


def test_updater_queries_the_actual_deb_package_name():
    control=(ROOT/'packaging/deb-root/DEBIAN/control').read_text()
    name=control.split('Package: ',1)[1].splitlines()[0]
    script=(ROOT/'packaging/install.sh').read_text()
    assert "-f='${Version}' " + name + " 2>" in script


def test_interrupted_dependency_install_is_resumable():
    setup=(ROOT/'packaging/install.sh').read_text()
    bootstrap=(ROOT/'packaging/deb-root/opt/voice-bridge/bootstrap-runtime.sh').read_text()
    assert 'cat "$RUNTIME/bootstrap.pending"' in setup
    assert bootstrap.index('bootstrap.pending') < bootstrap.index('pip install')
    assert bootstrap.rindex('rm -f "$APP/runtime/bootstrap.pending"') > bootstrap.rindex('pip install')


def test_fresh_root_runtime_does_not_inherit_private_snapshot_umask():
    setup=(ROOT/'packaging/install.sh').read_text()
    bootstrap=(ROOT/'packaging/deb-root/opt/voice-bridge/bootstrap-runtime.sh').read_text()
    assert setup.index('umask 022') < setup.index('sudo apt-get')
    assert bootstrap.index('umask 022') < bootstrap.index('-m venv')
