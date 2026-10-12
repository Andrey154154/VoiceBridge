"""Owned Unicode clipboard + complete SendInput ABI; no focus stealing.

We acknowledge only delivery to the system input queue, NOT insertion into
another application's field. The GUI self-test verifies actual field text.
"""
from __future__ import annotations
import ctypes as C
import logging
import threading
import time
from ..win32 import (INPUT, KEYBDINPUT, INPUT_UNION, MOUSEINPUT, HARDWAREINPUT,
    HWND, HANDLE, DWORD, UINT, BOOL, GUITHREADINFO, bind, load_dll, last_error)

from .errors import DeliveryDeferred

LOG = logging.getLogger(__name__)
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 2
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 2
KEYEVENTF_EXTENDEDKEY = 1
VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN = 0x11, 0x10, 0x12, 0x5B, 0x5C
VK_V, VK_Z, VK_INSERT = 0x56, 0x5A, 0x2D
COMBOS = {'ctrl+v': (VK_CONTROL, VK_V),
          'ctrl+shift+v': (VK_CONTROL, VK_SHIFT, VK_V),
          'shift+insert': (VK_SHIFT, VK_INSERT), 'ctrl+z': (VK_CONTROL, VK_Z)}

def _key(vk: int, up: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk == VK_INSERT else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, 0, flags, 0, 0))

def combo_events(combo):
    try:
        keys = COMBOS[combo]
    except KeyError:
        raise ValueError(f'Unsupported shortcut: {combo}') from None
    return [_key(k) for k in keys] + [_key(k, True) for k in reversed(keys)]

class WindowsTextInjector:
    supports_guard = True
    def __init__(self, *, paste_shortcut='ctrl+v', api=None, clipboard_timeout=1.5):
        self.paste_shortcut = paste_shortcut.lower().strip()
        if self.paste_shortcut not in COMBOS or self.paste_shortcut == 'ctrl+z':
            raise ValueError(f'Unsupported paste shortcut: {paste_shortcut}')
        self.api = api or NativeDeliveryAPI()
        self.clipboard_timeout = clipboard_timeout
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def prepare(self):
        expected = 40 if C.sizeof(C.c_void_p) == 8 else 28
        if C.sizeof(INPUT) != expected:
            raise RuntimeError('Win32 INPUT structure size mismatch')
        return f'Windows: Unicode clipboard + SendInput; {self.paste_shortcut}; INPUT={expected} bytes'

    def _guard(self, guard, target=None):
        if self._closed.is_set():
            raise RuntimeError('Delivery is stopped')
        if guard:
            guard()
        if target is not None:
            current = self.api.target()
            if current != target:
                LOG.info('Delivery focus changed during transfer: bound=%s current=%s', target, current)
                raise DeliveryDeferred('Окно или поле изменилось во время вставки. Текст сохранён; повторите вставку.')

    def _wait_keys(self, guard, target):
        deadline = time.monotonic() + 1.5
        while self.api.modifiers_down():
            self._guard(guard, target)
            if time.monotonic() >= deadline:
                raise RuntimeError('Отпустите Ctrl, Alt, Shift и Win и повторите вставку.')
            self._closed.wait(0.015)
        self._guard(guard, target)

    def _send_combo(self, combo):
        seq = combo_events(combo)
        sent = self.api.send(seq)
        if sent != len(seq):
            # Release only keys actually injected and not yet released. Never
            # retry the paste automatically: partial input can already act.
            down = []
            for event in seq[:sent]:
                k = event.ki.wVk
                if event.ki.dwFlags & KEYEVENTF_KEYUP:
                    if k in down: down.remove(k)
                elif k not in down:
                    down.append(k)
            if down:
                self.api.send([_key(k, True) for k in reversed(down)])
            raise RuntimeError(f'SendInput: {sent}/{len(seq)} событий. Вставка не подтверждена. '
                'Windows не разрешает ввод из обычного приложения в окно с повышенными правами; '
                'запустите целевую программу без «от имени администратора».')

    def insert(self, text: str, *, guard=None):
        if not text:
            return 'empty'
        if '\0' in text:
            raise ValueError('Clipboard text must not contain NUL')
        with self._lock:
            self._guard(guard)
            target = self.api.target()
            if not target or not target[0]:
                raise DeliveryDeferred('Нет активного окна для вставки. Выберите поле и повторите вставку.')
            self._wait_keys(guard, target)
            # Owned HWND is created/destroyed on the calling delivery thread.
            self.api.set_text(text, timeout=self.clipboard_timeout,
                              guard=lambda: self._guard(guard, target))
            self._wait_keys(guard, target)
            if self.api.read_text(timeout=self.clipboard_timeout,
                                  guard=lambda: self._guard(guard, target)) != text:
                raise RuntimeError('Буфер изменился до отправки сочетания. Текст сохранён.')
            self._guard(guard, target)
            # Do not wait again after verification: another application could
            # change the clipboard during that wait. No keys have been sent yet.
            if self.api.modifiers_down():
                raise RuntimeError('Нажата Ctrl, Alt, Shift или Win. Отпустите клавишу и повторите вставку.')
            self._send_combo(self.paste_shortcut)
            return 'windows-input-sent; application-unverified'

    def undo(self):
        with self._lock:
            self._guard(None)
            target = self.api.target()
            if not target or not target[0]:
                raise RuntimeError('Нет активного окна')
            self._wait_keys(None, target)
            self._send_combo('ctrl+z')

    def close(self):
        self._closed.set()

class NativeDeliveryAPI:
    def __init__(self):
        self.u = load_dll('user32')
        self.k = load_dll('kernel32')
        bind(self.k,'GlobalAlloc',[UINT,C.c_size_t],HANDLE)
        bind(self.k,'GlobalLock',[HANDLE],C.c_void_p)
        bind(self.k,'GlobalUnlock',[HANDLE],BOOL)
        bind(self.k,'GlobalFree',[HANDLE],HANDLE)
        bind(self.k,'GlobalSize',[HANDLE],C.c_size_t)
        bind(self.k,'GetModuleHandleW',[C.c_wchar_p],HANDLE)
        bind(self.u,'CreateWindowExW',[DWORD,C.c_wchar_p,C.c_wchar_p,DWORD,
            C.c_int,C.c_int,C.c_int,C.c_int,HWND,HANDLE,HANDLE,C.c_void_p],HWND)
        bind(self.u,'DestroyWindow',[HWND],BOOL)
        bind(self.u,'OpenClipboard',[HWND],BOOL)
        bind(self.u,'CloseClipboard',[],BOOL)
        bind(self.u,'EmptyClipboard',[],BOOL)
        bind(self.u,'SetClipboardData',[UINT,HANDLE],HANDLE)
        bind(self.u,'GetClipboardData',[UINT],HANDLE)
        bind(self.u,'SendInput',[UINT,C.POINTER(INPUT),C.c_int],UINT)
        bind(self.u,'GetForegroundWindow',[],HWND)
        bind(self.u,'GetWindowThreadProcessId',[HWND,C.POINTER(DWORD)],DWORD)
        bind(self.u,'GetGUIThreadInfo',[DWORD,C.POINTER(GUITHREADINFO)],BOOL)
        bind(self.u,'GetAsyncKeyState',[C.c_int],C.c_int16)

    def target(self):
        # No window titles or field contents are inspected/logged. HWND alone
        # can be reused; include the owning process/thread in the binding.
        hwnd = self.u.GetForegroundWindow()
        if not hwnd:
            return (0, 0, 0, 0)
        process = DWORD()
        thread = self.u.GetWindowThreadProcessId(hwnd, C.byref(process))
        if not thread or not process.value:
            return (0, 0, 0, 0)
        info = GUITHREADINFO(cbSize=C.sizeof(GUITHREADINFO))
        focus = info.hwndFocus if self.u.GetGUIThreadInfo(thread, C.byref(info)) else 0
        # Activation can change between the two Win32 queries. Never combine
        # an old foreground HWND with a new thread's focus as a valid target.
        if self.u.GetForegroundWindow() != hwnd:
            return (0, 0, 0, 0)
        return (int(hwnd), int(focus or 0), int(process.value), int(thread))

    def modifiers_down(self):
        return any(self.u.GetAsyncKeyState(vk) & 0x8000
                   for vk in (VK_CONTROL,VK_SHIFT,VK_MENU,VK_LWIN,VK_RWIN))

    def send(self, events):
        array = (INPUT * len(events))(*events)
        return self.u.SendInput(len(events),array,C.sizeof(INPUT))

    def set_text(self, text, *, timeout, guard):
        data = (text+'\0').encode('utf-16-le')
        # STATIC is a predefined Win32 window class. A message-only window is
        # neither visible nor eligible to activate/steal foreground focus.
        hwnd = self.u.CreateWindowExW(0,'STATIC','VoiceBridge clipboard owner',0,
                  0,0,0,0,HWND(-3),None,self.k.GetModuleHandleW(None),None)
        if not hwnd:
            raise OSError(last_error(),'Cannot create clipboard owner window')
        handle = None
        opened = False
        try:
            handle = self.k.GlobalAlloc(GMEM_MOVEABLE,len(data))
            if not handle: raise OSError('GlobalAlloc failed')
            ptr = self.k.GlobalLock(handle)
            if not ptr: raise OSError('GlobalLock failed')
            try: C.memmove(ptr,data,len(data))
            finally: self.k.GlobalUnlock(handle)
            deadline = time.monotonic()+timeout
            while True:
                guard()
                if self.u.OpenClipboard(hwnd):
                    opened=True
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Буфер занят другим приложением. Повторите вставку.')
                time.sleep(0.015)
            guard()
            if not self.u.EmptyClipboard(): raise OSError('EmptyClipboard failed')
            if not self.u.SetClipboardData(CF_UNICODETEXT,handle):
                raise OSError('SetClipboardData failed')
            handle=None  # Ownership transferred to Windows; do NOT free it.
        finally:
            if opened: self.u.CloseClipboard()
            if handle: self.k.GlobalFree(handle)
            self.u.DestroyWindow(hwnd)

    def read_text(self, *, timeout=1.5, guard=None):
        # Clipboard listeners can briefly open it immediately after set_text.
        # Retry only acquiring the clipboard, never SendInput or the whole paste.
        deadline = time.monotonic() + timeout
        while True:
            if guard:
                guard()
            if self.u.OpenClipboard(None):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Буфер остаётся занят при проверке. Текст сохранён; повторите вставку.')
            time.sleep(0.015)
        try:
            if guard:
                guard()
            handle=self.u.GetClipboardData(CF_UNICODETEXT)
            if not handle: return None
            size=self.k.GlobalSize(handle)
            if not size or size>8*1024*1024:
                raise RuntimeError('Invalid clipboard text size')
            ptr=self.k.GlobalLock(handle)
            if not ptr: raise OSError('GlobalLock(read) failed')
            try:
                return C.string_at(ptr,size-(size%2)).decode('utf-16-le').split('\0',1)[0]
            finally: self.k.GlobalUnlock(handle)
        finally: self.u.CloseClipboard()
