"""Explicit Win32 ABI, importable for layout tests on non-Windows hosts.

Windows uses LLP64 (DWORD/LONG stay 32-bit even on x64). Never use a
keyboard-only union for INPUT: its size is determined by MOUSEINPUT.
"""
from __future__ import annotations
import ctypes as C
import os

DWORD = C.c_uint32
WORD = C.c_uint16
LONG = C.c_int32
UINT = C.c_uint32
BOOL = C.c_int32
ULONG_PTR = C.c_size_t
WPARAM = C.c_size_t
LPARAM = C.c_ssize_t
LRESULT = C.c_ssize_t
HANDLE = C.c_void_p
HWND = HANDLE
WINFUNCTYPE = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)

class POINT(C.Structure):
    _fields_ = [('x', LONG), ('y', LONG)]

class MOUSEINPUT(C.Structure):
    _fields_ = [('dx', LONG), ('dy', LONG), ('mouseData', DWORD),
                ('dwFlags', DWORD), ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]

class KEYBDINPUT(C.Structure):
    _fields_ = [('wVk', WORD), ('wScan', WORD), ('dwFlags', DWORD),
                ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]

class HARDWAREINPUT(C.Structure):
    _fields_ = [('uMsg', DWORD), ('wParamL', WORD), ('wParamH', WORD)]

class INPUT_UNION(C.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]

class INPUT(C.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', DWORD), ('u', INPUT_UNION)]

class MSLLHOOKSTRUCT(C.Structure):
    _fields_ = [('pt', POINT), ('mouseData', DWORD), ('flags', DWORD),
                ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]

class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [('vkCode', DWORD), ('scanCode', DWORD), ('flags', DWORD),
                ('time', DWORD), ('dwExtraInfo', ULONG_PTR)]

class MSG(C.Structure):
    _fields_ = [('hwnd', HWND), ('message', UINT), ('wParam', WPARAM),
                ('lParam', LPARAM), ('time', DWORD), ('pt', POINT), ('lPrivate', DWORD)]

class RECT(C.Structure):
    _fields_ = [('left', LONG), ('top', LONG), ('right', LONG), ('bottom', LONG)]

class GUITHREADINFO(C.Structure):
    _fields_ = [('cbSize', DWORD), ('flags', DWORD), ('hwndActive', HWND),
                ('hwndFocus', HWND), ('hwndCapture', HWND), ('hwndMenuOwner', HWND),
                ('hwndMoveSize', HWND), ('hwndCaret', HWND), ('rcCaret', RECT)]

HOOKPROC = WINFUNCTYPE(LRESULT, C.c_int, WPARAM, LPARAM)

def bind(dll, name, args, result):
    fn = getattr(dll, name)
    fn.argtypes = args
    fn.restype = result
    return fn

def load_dll(name):
    if os.name != 'nt':
        raise RuntimeError('This operation requires native Windows')
    return C.WinDLL(name, use_last_error=True)

def last_error():
    return getattr(C, 'get_last_error', lambda: 0)()
