import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from voice_bridge.injectors import linux
from voice_bridge.injectors import linux_keyboard as kb


@pytest.mark.parametrize('combo, expected', [
    ('ctrl+v', [(29,1),(47,1),(47,0),(29,0)]),
    ('ctrl+shift+v', [(29,1),(42,1),(47,1),(47,0),(42,0),(29,0)]),
    ('shift+insert', [(42,1),(110,1),(110,0),(42,0)]),
    ('ctrl+z', [(29,1),(44,1),(44,0),(29,0)]),
])
def test_key_sequence(combo, expected):
    assert kb.key_events(combo) == expected


@pytest.mark.parametrize('output, syntax', [
    ('ydotool 0.1.8', 'legacy'), ('ydotool v1.0.4', 'modern'),
    ('1.0.4', 'modern'), ('ydotool version 0.1.8', 'legacy'),
    ('example: ydotool key ctrl+alt+f1', 'legacy'),
    ('Example: ydotool key 28:1 28:0', 'modern'),
])
def test_ydotool_detection(output, syntax):
    assert kb.detect_ydotool_syntax(output) == syntax


def test_unknown_ydotool_never_assumed_modern():
    with pytest.raises(RuntimeError):
        kb.detect_ydotool_syntax('Usage: ydotool. Exit status: 0')


def test_legacy_ydotool_gets_names_not_numeric_events():
    assert kb.ydotool_command('/bin/ydotool', 'ctrl+v', 'legacy', 25) == ['/bin/ydotool','key','ctrl+v']


def test_modern_ydotool_gets_numeric_events():
    assert kb.ydotool_command('ydotool', 'ctrl+v', 'modern', 25) == ['ydotool','key','--key-delay','25','29:1','47:1','47:0','29:0']


def fake_evdev(monkeypatch):
    device = Mock()
    factory = Mock(return_value=device)
    module = SimpleNamespace(UInput=factory, ecodes=SimpleNamespace(EV_KEY=1, EV_LED=17, BUS_USB=3))
    monkeypatch.setitem(sys.modules, 'evdev', module)
    monkeypatch.setattr(kb.time, 'sleep', lambda _: None)
    return factory, device


def test_native_keyboard_persistent_full_capabilities_and_close(monkeypatch):
    factory, device = fake_evdev(monkeypatch)
    keyboard = kb.UInputKeyboard()
    keyboard.prepare()
    keyboard.send('ctrl+v')
    keyboard.send('ctrl+v')
    factory.assert_called_once()
    assert list(range(1,249)) == factory.call_args.args[0][1]
    assert device.syn.call_count == 8
    assert [call.args for call in device.write.call_args_list] == [(1,k,v) for k,v in kb.key_events('ctrl+v')]*2
    keyboard.close()
    device.close.assert_called_once()


def test_native_error_releases_pressed_modifiers(monkeypatch):
    _, device = fake_evdev(monkeypatch)
    keyboard = kb.UInputKeyboard()
    device.syn.side_effect = [None, OSError('syn failed'), None, None]
    with pytest.raises(OSError):
        keyboard.send('ctrl+v')
    calls = [c.args for c in device.write.call_args_list]
    assert calls[-2:] == [(1,47,0),(1,29,0)]
    assert keyboard._pressed == []


def test_uinput_permissions_do_not_silently_fall_back(monkeypatch):
    factory, _ = fake_evdev(monkeypatch)
    factory.side_effect = PermissionError('denied')
    with pytest.raises(RuntimeError, match='/dev/uinput'):
        kb.UInputKeyboard().prepare()


def test_wayland_rejects_xwayland_only_keyboard(monkeypatch):
    monkeypatch.setenv('WAYLAND_DISPLAY','wayland-0')
    monkeypatch.setenv('DISPLAY', ':1')
    with pytest.raises(RuntimeError, match='Wayland'):
        kb.X11Keyboard().prepare()


def test_auto_on_wayland_uses_native_even_with_display(monkeypatch):
    monkeypatch.setenv('WAYLAND_DISPLAY','wayland-0')
    monkeypatch.setenv('DISPLAY', ':1')
    assert isinstance(kb.make_keyboard('auto', syntax='auto', delay_ms=25), kb.UInputKeyboard)


@pytest.mark.parametrize('name', ['Voice Bridge Paste Keyboard', 'Voice Input Bridge proxy - ROG Mouse', 'ydotoold virtual device'])
def test_virtual_devices_identified(name):
    assert kb.is_virtual_device(name)


@pytest.fixture
def injector(monkeypatch):
    keyboard = Mock(name='keyboard')
    keyboard.name = 'fake keyboard'
    monkeypatch.setattr(linux, 'make_keyboard', lambda *a, **kw: keyboard)
    monkeypatch.setattr(linux.time, 'sleep', lambda _: None)
    monkeypatch.setenv('WAYLAND_DISPLAY','wayland-0')
    monkeypatch.setattr(linux.shutil, 'which', lambda name: '/bin/'+name)
    return linux.LinuxTextInjector()


def test_ctrl_v_not_remapped_and_no_clipboard_read(monkeypatch, injector):
    calls = []
    monkeypatch.setattr(injector, '_copy_command', lambda c,t: calls.append((c,t)))
    result = injector.insert('Привет, мир!')
    assert len(calls) == 1
    assert calls[0][0] == ['/bin/wl-copy','--type','text/plain;charset=utf-8']
    assert calls[0][1] == 'Привет, мир!'
    injector.keyboard.send.assert_called_once_with('ctrl+v')
    assert 'application-unverified' in result


def test_shift_insert_publishes_both_selections(monkeypatch, injector):
    calls = []
    injector.paste_shortcut = 'shift+insert'
    monkeypatch.setattr(injector, '_copy_command', lambda c,t: calls.append(c))
    injector.insert('Новое, не старое выделение')
    assert len(calls) == 2
    assert '--primary' not in calls[0]
    assert '--primary' in calls[1]
    injector.keyboard.send.assert_called_once_with('shift+insert')


def test_copy_failure_never_sends_shortcut(monkeypatch, injector):
    monkeypatch.setattr(injector, '_copy', Mock(side_effect=RuntimeError('clipboard failed')))
    with pytest.raises(RuntimeError, match='clipboard failed'):
        injector.insert('not pasted')
    injector.keyboard.send.assert_not_called()


def test_send_failure_not_retried_with_atspi_or_other_chord(monkeypatch, injector):
    monkeypatch.setattr(injector, '_copy', lambda _: 'fake')
    injector.keyboard.send.side_effect = OSError('partial send')
    with pytest.raises(OSError):
        injector.insert('only once')
    assert injector.keyboard.send.call_count == 1


def test_focus_guard_rechecked_after_clipboard(monkeypatch, injector):
    monkeypatch.setattr(injector, '_copy', lambda _: 'fake')
    guard = Mock(side_effect=[None, RuntimeError('focus changed')])
    with pytest.raises(RuntimeError, match='focus changed'):
        injector.insert('private', guard=guard)
    assert guard.call_count == 2
    injector.keyboard.send.assert_not_called()


def test_clipboard_subprocess_has_timeout_and_no_inherited_pipe(monkeypatch):
    def run(command, **kwargs):
        assert kwargs['timeout'] == 5
        assert kwargs['stdout'] == subprocess.DEVNULL
        assert hasattr(kwargs['stderr'], 'fileno')
        assert kwargs['input'] == 'Текст'.encode()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(linux.subprocess, 'run', run)
    linux.LinuxTextInjector._copy_command(['wl-copy'], 'Текст')


def test_clipboard_timeout_reports_no_paste(monkeypatch):
    monkeypatch.setattr(linux.subprocess, 'run', Mock(side_effect=subprocess.TimeoutExpired('wl-copy', 5)))
    with pytest.raises(RuntimeError, match='no paste shortcut'):
        linux.LinuxTextInjector._copy_command(['wl-copy'], 'Текст')


def test_closed_injector_does_not_recreate_keyboard(injector):
    injector.close()
    with pytest.raises(RuntimeError, match='closed'):
        injector.insert('test')
    injector.keyboard.send.assert_not_called()


def test_empty_text_has_no_side_effect(injector):
    assert injector.insert('') == 'empty'
    injector.keyboard.prepare.assert_not_called()


def test_wayland_never_falls_back_to_x11_clipboard(monkeypatch, injector):
    monkeypatch.setattr(linux.shutil, 'which', lambda n: None if n=='wl-copy' else '/bin/'+n)
    with pytest.raises(RuntimeError, match='wl-copy'):
        injector.insert('text')


def test_x11_clipboard_uses_utf8_and_primary_when_requested(monkeypatch, injector):
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
    monkeypatch.setenv('DISPLAY', ':9')
    injector.paste_shortcut='shift+insert'
    calls=[]
    monkeypatch.setattr(injector, '_copy_command', lambda c,t: calls.append(c))
    injector.insert('Юникод')
    assert calls == [['/bin/xclip','-selection','clipboard','-target','UTF8_STRING'],
                     ['/bin/xclip','-selection','primary','-target','UTF8_STRING']]
