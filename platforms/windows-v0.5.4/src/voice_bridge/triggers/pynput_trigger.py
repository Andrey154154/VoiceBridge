from __future__ import annotations

from typing import Callable


class PynputHoldTrigger:
    def __init__(self, *, kind: str = "mouse", button: str = "x1", key: str = "f8") -> None:
        self.kind = kind
        self.button = button.lower()
        self.key = key.lower()

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

        def cb(_x: int, _y: int, pressed_button: object, pressed: bool) -> None:
            if pressed_button != button:
                return
            if pressed:
                on_press()
            else:
                on_release()

        with mouse.Listener(on_click=cb) as listener:
            listener.join()

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
            if value == target and not down:
                down = True
                on_press()

        def release(value: object) -> None:
            nonlocal down
            if value == target and down:
                down = False
                on_release()

        with keyboard.Listener(on_press=press, on_release=release) as listener:
            listener.join()
