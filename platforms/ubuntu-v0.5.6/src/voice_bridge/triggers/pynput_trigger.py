from __future__ import annotations

import threading
from typing import Callable


class PynputHoldTrigger:
    def __init__(self, *, kind: str = "mouse", button: str = "x1", key: str = "f8") -> None:
        self.kind = kind
        self.button = button.lower()
        self.key = key.lower()
        self._listener = None
        self._stop_event = threading.Event()
        self._enabled = True
        self.on_ready: Callable[[], None] | None = None

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def stop(self) -> None:
        self._stop_event.set()
        if self._listener is not None:
            self._listener.stop()

    def _listen(self, listener) -> None:
        self._listener = listener
        try:
            if self._stop_event.is_set():
                return
            listener.start()
            listener.wait()
            if self._stop_event.is_set():
                listener.stop()
            elif self.on_ready:
                self.on_ready()
            listener.join()
        finally:
            listener.stop()
            self._listener = None

    def run(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        if self.kind == "mouse":
            self._run_mouse(on_press, on_release)
        elif self.kind == "keyboard":
            self._run_keyboard(on_press, on_release)
        else:
            raise ValueError(f"Unsupported trigger kind: {self.kind}")

    def _run_mouse(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        try:
            from pynput import mouse
        except ImportError as exc:
            raise RuntimeError("pynput is not installed") from exc
        button = getattr(mouse.Button, self.button, None)
        if button is None:
            raise ValueError(f"Unknown mouse button: {self.button}")

        down = False
        def cb(_x: int, _y: int, pressed_button: object, pressed: bool) -> None:
            nonlocal down
            if pressed_button != button:
                return
            if pressed and self._enabled and not down:
                down = True
                on_press()
            elif not pressed and down:
                down = False
                on_release()

        self._listen(mouse.Listener(on_click=cb))

    def _run_keyboard(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        try:
            from pynput import keyboard
        except ImportError as exc:
            raise RuntimeError("pynput is not installed") from exc
        target = getattr(keyboard.Key, self.key, None)
        if target is None and len(self.key) == 1:
            target = keyboard.KeyCode.from_char(self.key)
        if target is None:
            raise ValueError(f"Unknown keyboard key: {self.key}")
        down = False

        def press(value: object) -> None:
            nonlocal down
            if value == target and self._enabled and not down:
                down = True
                on_press()

        def release(value: object) -> None:
            nonlocal down
            if value == target and down:
                down = False
                on_release()

        self._listen(keyboard.Listener(on_press=press, on_release=release))
