"""Native global PTT hook: callback is bounded, capture happens off hook thread."""
from __future__ import annotations
import ctypes as C
import logging
import queue
import threading
from ..win32 import (MSLLHOOKSTRUCT, KBDLLHOOKSTRUCT, HOOKPROC, HWND, HANDLE,
    DWORD, UINT, BOOL, WPARAM, LPARAM, LRESULT, MSG, bind, load_dll, last_error)

LOG = logging.getLogger(__name__)
WH_MOUSE_LL, WH_KEYBOARD_LL = 14, 13
WM_XBUTTONDOWN, WM_XBUTTONUP, WM_XBUTTONDBLCLK = 0x020B, 0x020C, 0x020D
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_QUIT = 0x12
HC_ACTION=0
XBUTTON1, XBUTTON2=1,2
LowLevelMouseProc=HOOKPROC

def _high_word(value): return (value >> 16) & 0xffff

class ButtonState:
    """Pure event filter, tested independently of a live desktop."""
    def __init__(self, kind='mouse', button='x1', key='f8', consume=True):
        self.kind=kind
        self.consume=consume
        self.down=False
        self.enabled=True
        if kind=='mouse':
            if button not in {'x1','x2'}: raise ValueError('Mouse must be x1 or x2')
            self.target=1 if button=='x1' else 2
        elif kind=='keyboard':
            if key not in {f'f{i}' for i in range(1,25)}: raise ValueError('Use F1..F24 for native keyboard PTT')
            self.target=0x70+int(key[1:])-1
        else: raise ValueError('PTT kind must be mouse or keyboard')

    def feed(self, message, code, injected=False):
        if injected or code!=self.target or not self.enabled: return False,None
        downs={WM_XBUTTONDOWN,WM_XBUTTONDBLCLK} if self.kind=='mouse' else {WM_KEYDOWN,WM_SYSKEYDOWN}
        ups={WM_XBUTTONUP} if self.kind=='mouse' else {WM_KEYUP,WM_SYSKEYUP}
        if message not in downs|ups: return False,None
        event=None
        if message in downs and not self.down:
            self.down=True; event='press'
        elif message in ups and self.down:
            self.down=False; event='release'
        return self.consume,event

class WindowsMouseHookTrigger:
    """Mouse X1/X2 or keyboard F1..F24. stop() tears down the message loop."""
    def __init__(self, *, button='x1', consume=True, kind='mouse', key='f8'):
        self.state=ButtonState(kind,button.lower(),key.lower(),consume)
        self.button=button
        self.consume=consume
        self.on_ready=None
        self._stop=threading.Event()
        self._thread_id=0
        self._hook=None
        self._callback=None
        self._api=None

    def set_enabled(self, enabled):
        self.state.enabled=bool(enabled)
        if not enabled: self.state.down=False

    def stop(self):
        self._stop.set()
        if self._api and self._thread_id:
            if not self._api.PostThreadMessageW(self._thread_id,WM_QUIT,0,0):
                LOG.warning('Cannot post hook shutdown (error=%d)',last_error())

    def run(self,on_press,on_release):
        if self._stop.is_set(): return
        u,k=load_dll('user32'),load_dll('kernel32')
        self._api=u
        bind(u,'SetWindowsHookExW',[C.c_int,HOOKPROC,HANDLE,DWORD],HANDLE)
        bind(u,'CallNextHookEx',[HANDLE,C.c_int,WPARAM,LPARAM],LRESULT)
        bind(u,'UnhookWindowsHookEx',[HANDLE],BOOL)
        bind(u,'GetMessageW',[C.POINTER(MSG),HWND,UINT,UINT],C.c_int)
        bind(u,'PeekMessageW',[C.POINTER(MSG),HWND,UINT,UINT,UINT],BOOL)
        bind(u,'TranslateMessage',[C.POINTER(MSG)],BOOL)
        bind(u,'DispatchMessageW',[C.POINTER(MSG)],LRESULT)
        bind(u,'PostThreadMessageW',[DWORD,UINT,WPARAM,LPARAM],BOOL)
        bind(k,'GetModuleHandleW',[C.c_wchar_p],HANDLE)
        bind(k,'GetCurrentThreadId',[],DWORD)
        events=queue.SimpleQueue()
        def dispatch():
            while not self._stop.is_set():
                try: action=events.get(timeout=0.1)
                except queue.Empty: continue
                if self._stop.is_set(): break
                try: (on_press if action=='press' else on_release)()
                except Exception: LOG.exception('PTT action failed')
        worker=threading.Thread(target=dispatch,daemon=True,name='voice-bridge-ptt-actions')
        @HOOKPROC
        def callback(ncode,message,pointer):
            try:
                if ncode==HC_ACTION and pointer:
                    if self.state.kind=='mouse' and message in {WM_XBUTTONDOWN,WM_XBUTTONUP,WM_XBUTTONDBLCLK}:
                        info=C.cast(pointer,C.POINTER(MSLLHOOKSTRUCT)).contents
                        consume,action=self.state.feed(message,_high_word(info.mouseData),bool(info.flags & 1))
                    elif self.state.kind=='keyboard' and message in {WM_KEYDOWN,WM_KEYUP,WM_SYSKEYDOWN,WM_SYSKEYUP}:
                        info=C.cast(pointer,C.POINTER(KBDLLHOOKSTRUCT)).contents
                        consume,action=self.state.feed(message,info.vkCode,bool(info.flags & 0x10))
                    else: consume,action=False,None
                    if action: events.put(action)
                    if consume: return 1
            except Exception:
                LOG.exception('PTT hook callback error; passing input through')
            return u.CallNextHookEx(self._hook,ncode,message,pointer)
        self._callback=callback
        message=MSG()
        # Create the thread message queue before making the thread ID visible.
        u.PeekMessageW(C.byref(message),None,0,0,0)
        self._thread_id=k.GetCurrentThreadId()
        try:
            if self._stop.is_set(): return
            hook_type=WH_MOUSE_LL if self.state.kind=='mouse' else WH_KEYBOARD_LL
            self._hook=u.SetWindowsHookExW(hook_type,callback,k.GetModuleHandleW(None),0)
            if not self._hook: raise OSError(last_error(),'SetWindowsHookExW failed')
            worker.start()
            if self.on_ready: self.on_ready()
            while not self._stop.is_set():
                result=u.GetMessageW(C.byref(message),None,0,0)
                if result==0: break
                if result==-1: raise OSError(last_error(),'GetMessageW failed')
                u.TranslateMessage(C.byref(message));u.DispatchMessageW(C.byref(message))
        finally:
            self._stop.set()
            if self._hook:
                u.UnhookWindowsHookEx(self._hook);self._hook=None
            self._thread_id=0
            if worker.is_alive(): worker.join(timeout=1)
            if self.state.down:
                self.state.down=False
                try: on_release()
                except Exception: LOG.exception('Release at hook shutdown failed')
