import ctypes as C
import pytest
from voice_bridge.win32 import INPUT, KEYBDINPUT, MOUSEINPUT, MSLLHOOKSTRUCT, MSG
from voice_bridge.injectors.windows import WindowsTextInjector, combo_events, KEYEVENTF_KEYUP, NativeDeliveryAPI
from voice_bridge.triggers.windows_hook import (ButtonState, WM_XBUTTONDOWN, WM_XBUTTONUP,
    WM_KEYDOWN, WM_KEYUP, WM_XBUTTONDBLCLK)

class API:
    def __init__(self):
        self.focus=(101,102); self.text=None; self.sends=[]; self.writes=[]
        self.count=None; self.keys=False; self.on_set=None
    def target(self): return self.focus
    def modifiers_down(self): return self.keys
    def set_text(self,text,*,timeout,guard):
        guard();self.writes.append(text);self.text=text
        if self.on_set:self.on_set()
        guard()
    def read_text(self, *, timeout=1.5, guard=None):
        if guard: guard()
        return self.text
    def send(self,events):
        self.sends.append(events)
        return len(events) if self.count is None else self.count

@pytest.fixture
def pair():
    api=API()
    return WindowsTextInjector(api=api),api

def test_real_x64_abi_not_linux_ulong_layout():
    assert C.sizeof(INPUT)==40
    assert C.sizeof(KEYBDINPUT)==24
    assert C.sizeof(MOUSEINPUT)==32
    assert C.sizeof(MSLLHOOKSTRUCT)==32
    assert C.sizeof(MSG)==48
    assert INPUT.u.offset==8
    assert KEYBDINPUT.dwExtraInfo.offset==16

@pytest.mark.parametrize('combo,n',[('ctrl+v',4),('ctrl+shift+v',6),('shift+insert',4),('ctrl+z',4)])
def test_balanced_key_events(combo,n):
    events=combo_events(combo)
    assert len(events)==n
    keys=[x.ki.wVk for x in events[:n//2]]
    assert [x.ki.wVk for x in events[n//2:]]==keys[::-1]
    assert all(x.ki.dwFlags&KEYEVENTF_KEYUP for x in events[n//2:])

def test_insert_unicode_and_honest_outcome(pair):
    injector,api=pair
    assert 'INPUT=40' in injector.prepare()
    result=injector.insert('Проверка 👋\nDaVinci')
    assert api.text=='Проверка 👋\nDaVinci'
    assert len(api.sends)==1
    assert result=='windows-input-sent; application-unverified'

def test_no_paste_if_guard_cancels(pair):
    injector,api=pair
    def reject():raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):injector.insert('x',guard=reject)
    assert not api.writes and not api.sends

def test_cancel_after_clipboard_prevents_key_events(pair):
    injector,api=pair
    api.on_set=injector.close
    with pytest.raises(RuntimeError,match='stopped'):injector.insert('x')
    assert api.text=='x' and not api.sends

def test_focus_switch_after_clipboard_prevents_wrong_window_paste(pair):
    injector,api=pair
    api.on_set=lambda:setattr(api,'focus',(202,203))
    with pytest.raises(RuntimeError,match='изменилось'):injector.insert('x')
    assert not api.sends

def test_focus_switch_in_same_window_prevents_wrong_field_paste(pair):
    injector,api=pair
    api.on_set=lambda:setattr(api,'focus',(101,103))
    with pytest.raises(RuntimeError,match='изменилось'):injector.insert('x')
    assert not api.sends

def test_clipboard_race_fails_closed(pair):
    injector,api=pair
    api.on_set=lambda:setattr(api,'text','user copied something else')
    with pytest.raises(RuntimeError,match='Буфер изменился'):injector.insert('x')
    assert not api.sends

def test_partial_send_releases_only_actually_injected_keys(pair):
    injector,api=pair
    api.count=2
    with pytest.raises(RuntimeError,match='2/4'):injector.insert('x')
    assert len(api.sends)==2
    assert [(x.ki.wVk,x.ki.dwFlags) for x in api.sends[1]]==[(0x56,2),(0x11,2)]

def test_zero_send_does_not_try_duplicate_paste(pair):
    injector,api=pair;api.count=0
    with pytest.raises(RuntimeError,match='0/4'):injector.insert('x')
    assert len(api.sends)==1

def test_no_foreground_and_nul_rejected(pair):
    injector,api=pair
    with pytest.raises(ValueError):injector.insert('a\0b')
    api.focus=(0,0)
    with pytest.raises(RuntimeError,match='активного'):injector.insert('x')
    assert not api.sends

def test_invalid_combo_is_not_silently_substituted():
    with pytest.raises(ValueError):WindowsTextInjector(paste_shortcut='bogus',api=API())

def test_shift_insert_is_supported(pair):
    _,api=pair
    injector=WindowsTextInjector(paste_shortcut='shift+insert',api=api)
    injector.insert('x')
    assert api.sends[0][1].ki.wVk==0x2d
    assert api.sends[0][1].ki.dwFlags&1

def test_hook_repeated_down_does_not_restart_recording():
    s=ButtonState()
    assert s.feed(WM_XBUTTONDOWN,1)==(True,'press')
    assert s.feed(WM_XBUTTONDOWN,1)==(True,None)
    assert s.feed(WM_XBUTTONUP,1)==(True,'release')
    assert s.feed(WM_XBUTTONUP,1)==(True,None)

def test_hook_passes_unselected_and_synthetic_buttons():
    s=ButtonState()
    assert s.feed(WM_XBUTTONDOWN,2)==(False,None)
    assert s.feed(WM_XBUTTONDOWN,1,True)==(False,None)
    assert not s.down

def test_hook_double_click_has_balanced_second_press():
    s=ButtonState()
    for msg in (WM_XBUTTONDOWN,WM_XBUTTONUP):s.feed(msg,1)
    assert s.feed(WM_XBUTTONDBLCLK,1)==(True,'press')
    assert s.feed(WM_XBUTTONUP,1)==(True,'release')

def test_keyboard_f8_repeats_and_suppression():
    s=ButtonState(kind='keyboard',key='f8')
    assert s.feed(WM_KEYDOWN,0x77)==(True,'press')
    assert s.feed(WM_KEYDOWN,0x77)==(True,None)
    assert s.feed(WM_KEYUP,0x77)==(True,'release')
    assert s.feed(WM_KEYDOWN,0x41)==(False,None)

def test_hook_disable_restores_pass_through():
    s=ButtonState();s.enabled=False
    assert s.feed(WM_XBUTTONDOWN,1)==(False,None)

def test_hook_nonconsuming_still_emits_events():
    assert ButtonState(consume=False).feed(WM_XBUTTONDOWN,1)==(False,'press')

@pytest.mark.parametrize('kwargs',[{'kind':'invalid'},{'button':'left'},{'kind':'keyboard','key':'a'}])
def test_invalid_hook_configuration(kwargs):
    with pytest.raises(ValueError):ButtonState(**kwargs)

# Exercise low-level clipboard lifetime and transfer, without pretending this
# fake implementation validates user32 on a native Windows desktop.
class NativeFake:
    def __init__(self):
        self.memory=C.create_string_buffer(1024);self.freed=[];self.destroyed=[]
        self.opens=[];self.closed=0;self.fail='';self.attempts=0
    def CreateWindowExW(self,*args):return 99
    def DestroyWindow(self,h):self.destroyed.append(h);return 1
    def GetModuleHandleW(self,_):return 1
    def GlobalAlloc(self,*_):return 333
    def GlobalLock(self,h):return C.addressof(self.memory) if self.fail!='lock' else 0
    def GlobalUnlock(self,h):return 1
    def GlobalFree(self,h):self.freed.append(h);return 0
    def GlobalSize(self,h):return 1024
    def OpenClipboard(self,h):
        self.opens.append(h);self.attempts+=1
        return self.attempts>1 if self.fail=='busy_once' else 1
    def CloseClipboard(self):self.closed+=1;return 1
    def EmptyClipboard(self):return int(self.fail!='empty')
    def SetClipboardData(self,*args):return 0 if self.fail=='set' else 333

def fake_native():
    native=NativeDeliveryAPI.__new__(NativeDeliveryAPI)
    fake=NativeFake();native.u=fake;native.k=fake
    return native,fake

def test_clipboard_owned_hwnd_and_system_owns_successful_allocation():
    native,f=fake_native()
    native.set_text('Привет',timeout=.1,guard=lambda:None)
    assert f.opens==[99] and f.closed==1 and f.destroyed==[99]
    assert f.freed==[]
    assert f.memory.raw.startswith('Привет\0'.encode('utf-16-le'))

@pytest.mark.parametrize('failure',['lock','empty','set'])
def test_clipboard_failure_frees_alloc_and_destroys_window(failure):
    native,f=fake_native();f.fail=failure
    with pytest.raises(OSError):native.set_text('x',timeout=.1,guard=lambda:None)
    assert f.freed==[333] and f.destroyed==[99]
    assert f.closed==(0 if failure=='lock' else 1)

def test_clipboard_busy_retries_same_non_null_owner():
    native,f=fake_native();f.fail='busy_once'
    native.set_text('x',timeout=.1,guard=lambda:None)
    assert f.opens==[99,99] and f.closed==1

def test_clipboard_cancel_after_open_closes_without_emptying():
    native,f=fake_native();n=0
    def guard():
        nonlocal n
        n+=1
        if n==2:raise RuntimeError('cancel')
    with pytest.raises(RuntimeError):native.set_text('x',timeout=.1,guard=guard)
    assert f.closed==1 and f.freed==[333]

def test_wasapi_default_device_uses_native_shared_rate_conversion(monkeypatch):
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock
    from voice_bridge.audio import AudioRecorder
    import voice_bridge.audio as audio
    stream=Mock();settings=object()
    sd=SimpleNamespace(query_hostapis=lambda *args: ({'name':'Windows WASAPI'} if args else [{'name':'Windows WASAPI','default_input_device':7}]),
        query_devices=lambda *args:{'hostapi':0},WasapiSettings=Mock(return_value=settings),InputStream=Mock(return_value=stream))
    monkeypatch.setitem(sys.modules,'sounddevice',sd)
    monkeypatch.setattr(audio.platform,'system',lambda:'Windows')
    recorder=AudioRecorder();recorder.start();recorder.stop()
    assert sd.InputStream.call_args.kwargs['device']==7
    assert sd.InputStream.call_args.kwargs['extra_settings'] is settings
    assert sd.InputStream.call_args.kwargs['samplerate']==16000
    sd.WasapiSettings.assert_called_once_with(auto_convert=True)

def test_windows_hook_stop_before_run_does_not_open_user32():
    from voice_bridge.triggers.windows_hook import WindowsMouseHookTrigger
    trigger=WindowsMouseHookTrigger();trigger.stop()
    trigger.run(lambda:None,lambda:None)  # no Windows DLL access, even on Linux

def test_windows_factory_supports_native_keyboard(monkeypatch):
    import voice_bridge.triggers as factory
    from voice_bridge.triggers.windows_hook import WindowsMouseHookTrigger
    monkeypatch.setattr(factory.platform,'system',lambda:'Windows')
    trigger=factory.create_trigger(kind='keyboard',key='f8')
    assert isinstance(trigger,WindowsMouseHookTrigger)
    assert trigger.state.target==0x77

def test_windows_profile_roundtrip(tmp_path):
    from voice_bridge.config import Config,load_config
    from voice_bridge.desktop.settings_store import save_config
    config=Config()
    config.asr.model=r'D:\Модели ASR\Qwen3'
    config.audio.input_device='Микрофон, Windows WASAPI'
    config.trigger.backend='windows_hook';config.trigger.kind='keyboard';config.trigger.key='f8'
    p=tmp_path/'config.toml';save_config(config,p)
    loaded=load_config(p)
    assert loaded.asr.model==config.asr.model
    assert loaded.audio.input_device==config.audio.input_device
    assert loaded.trigger.key=='f8'
