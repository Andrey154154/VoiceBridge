"""Native API simulated: retry clipboard acquire, never retry injected keys."""
import ctypes as C
from types import SimpleNamespace
import pytest
import voice_bridge.injectors.windows as mod
from voice_bridge.injectors.windows import WindowsTextInjector, NativeDeliveryAPI
from test_windows_delivery import API

class Clock:
    now=0.0
    def monotonic(self): return self.now
    def sleep(self,n): self.now+=n

class Clipboard:
    def __init__(self, text='Проверка 👋', failures=2):
        self.memory=C.create_string_buffer((text+'\0').encode('utf-16-le'))
        self.failures=failures;self.opens=0;self.closed=0;self.unlocked=0;self.reads=0;self.on_attempt=None
    def OpenClipboard(self,h):
        self.opens+=1
        if self.on_attempt:self.on_attempt(self.opens)
        return self.opens>self.failures
    def CloseClipboard(self):self.closed+=1;return 1
    def GetClipboardData(self,fmt):self.reads+=1;return 55
    def GlobalSize(self,h):return len(self.memory.raw)
    def GlobalLock(self,h):return C.addressof(self.memory)
    def GlobalUnlock(self,h):self.unlocked+=1;return 1


def native(text='Проверка 👋',failures=2):
    f=Clipboard(text,failures);n=NativeDeliveryAPI.__new__(NativeDeliveryAPI);n.u=f;n.k=f
    return n,f

@pytest.fixture
def clock(monkeypatch):
    c=Clock();monkeypatch.setattr(mod,'time',c);return c


def test_read_recovers_from_temporary_clipboard_busy(clock):
    n,f=native()
    assert n.read_text(timeout=.1,guard=lambda:None)=='Проверка 👋'
    assert f.opens==3 and f.closed==1 and f.unlocked==1 and f.reads==1


def test_read_busy_timeout_is_bounded_and_never_closes_unopened_clipboard(clock):
    n,f=native(failures=1000)
    with pytest.raises(RuntimeError,match='остаётся занят'):n.read_text(timeout=.04)
    assert .04<=clock.now<=.06
    assert f.closed==0 and f.reads==0


def test_read_cancel_during_busy_wait_never_reads(clock):
    n,f=native(failures=1000)
    def guard():
        if f.opens:raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):n.read_text(timeout=1,guard=guard)
    assert f.opens==1 and f.closed==0 and f.reads==0


def test_read_cancel_immediately_after_open_releases_clipboard(clock):
    n,f=native(failures=0)
    def guard():
        if f.opens:raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):n.read_text(timeout=1,guard=guard)
    assert f.closed==1 and f.reads==0


def test_read_decode_failure_releases_all_handles(clock):
    n,f=native(failures=0)
    f.memory=C.create_string_buffer(b'\x00\xd8\x00\x00')  # unpaired surrogate
    with pytest.raises(UnicodeDecodeError):n.read_text(timeout=1)
    assert f.closed==1 and f.unlocked==1


def injector_with_read(failures=2):
    a=API();n,f=native('voice text',failures)
    a.read_text=n.read_text
    injector=WindowsTextInjector(api=a,clipboard_timeout=.06)
    return injector,a,f


def test_retry_read_then_exactly_one_send(clock):
    i,a,f=injector_with_read()
    assert i.insert('voice text')=='windows-input-sent; application-unverified'
    assert f.opens==3 and a.writes==['voice text'] and len(a.sends)==1


def test_read_timeout_never_sends(clock):
    i,a,f=injector_with_read(1000)
    with pytest.raises(RuntimeError,match='остаётся занят'):i.insert('voice text')
    assert a.writes==['voice text'] and not a.sends


def test_focus_change_during_read_retry_never_pastes_elsewhere(clock):
    i,a,f=injector_with_read(1000)
    f.on_attempt=lambda _:setattr(a,'focus',(202,203))
    with pytest.raises(RuntimeError,match='изменилось'):i.insert('voice text')
    assert f.opens==1 and not a.sends


def test_shutdown_during_read_retry_never_pastes(clock):
    i,a,f=injector_with_read(1000);f.on_attempt=lambda _:i.close()
    with pytest.raises(RuntimeError,match='stopped'):i.insert('voice text')
    assert f.opens==1 and not a.sends


def test_different_clipboard_content_after_wait_cancels(clock):
    i,a,f=injector_with_read()
    with pytest.raises(RuntimeError,match='Буфер изменился'):i.insert('different text')
    assert f.opens==3 and not a.sends


def test_modifier_pressed_during_read_does_not_inject(clock):
    i,a,f=injector_with_read();f.on_attempt=lambda _:setattr(a,'keys',True)
    with pytest.raises(RuntimeError,match='Отпустите клавишу'):i.insert('voice text')
    assert not a.sends
