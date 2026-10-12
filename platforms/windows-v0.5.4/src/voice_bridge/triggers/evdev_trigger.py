from __future__ import annotations

import logging
import select
import threading
import time

from ..injectors.linux_keyboard import is_virtual_device
from typing import Callable

LOG = logging.getLogger(__name__)


def _is_consumed_target_event(event, *, target_code: int, ev_key: int) -> bool:
    """Return True only for the configured physical trigger event.

    Kept as a small pure helper so selective suppression can be tested without
    requiring a real evdev/uinput device in CI.
    """

    return event.type == ev_key and event.code == target_code


class EvdevHoldTrigger:
    """Global Linux input trigger that works independently of X11/Wayland.

    When ``consume`` is enabled, the physical evdev device is grabbed and all
    events except the configured trigger button are re-emitted through a uinput
    proxy. This makes one mouse button private to Voice Input Bridge without
    disabling movement, wheel, or the other mouse buttons.
    """

    def __init__(
        self,
        *,
        device_match: str = "",
        code: str = "BTN_SIDE",
        consume: bool = False,
    ) -> None:
        self.device_match = device_match.lower()
        self.code = code
        self.consume = consume
        self._stop_event = threading.Event()
        self.on_ready: Callable[[], None] | None = None

    @staticmethod
    def devices() -> list[tuple[str, str]]:
        try:
            import evdev
        except ImportError as exc:
            raise RuntimeError("evdev is not installed") from exc
        result: list[tuple[str, str]] = []
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            if not is_virtual_device(dev.name, getattr(dev, "phys", "")):
                result.append((path, dev.name))
            dev.close()
        return result

    def _select_device(self):
        try:
            import evdev
        except ImportError as exc:
            raise RuntimeError("evdev is not installed") from exc
        candidates = []
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            if is_virtual_device(dev.name, getattr(dev, "phys", "")):
                dev.close()
                continue
            if not self.device_match or self.device_match in dev.name.lower() or self.device_match in path.lower():
                candidates.append(dev)
            else:
                dev.close()
        if not candidates:
            raise RuntimeError(f"No evdev device matches {self.device_match!r}")
        # Prefer a device that advertises the requested key/button code.
        code_value = evdev.ecodes.ecodes.get(self.code)
        if code_value is None:
            for dev in candidates:
                dev.close()
            raise ValueError(f"Unknown evdev code: {self.code}")
        for dev in candidates:
            keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
            if code_value in keys:
                for other in candidates:
                    if other is not dev:
                        other.close()
                return dev, code_value
        names = ", ".join(f"{d.path}:{d.name}" for d in candidates)
        for dev in candidates:
            dev.close()
        raise RuntimeError(f"No matching device exposes {self.code}. Candidates: {names}")

    def stop(self) -> None:
        self._stop_event.set()

    def _events(self, dev):
        while not self._stop_event.is_set():
            ready, _, _ = select.select([dev], [], [], 0.2)
            if ready:
                try:
                    yield from dev.read()
                except BlockingIOError:
                    continue

    def _run_observer(self, dev, target_code: int, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        import evdev

        if self.on_ready:
            self.on_ready()
        for event in self._events(dev):
            if event.type != evdev.ecodes.EV_KEY or event.code != target_code:
                continue
            if event.value == 1:
                on_press()
            elif event.value == 0:
                on_release()

    def _run_consuming_proxy(
        self,
        dev,
        target_code: int,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        import evdev

        proxy = None
        grabbed = False
        try:
            try:
                proxy = evdev.UInput.from_device(
                    dev,
                    name=f"Voice Input Bridge proxy - {dev.name}",
                )
            except (PermissionError, OSError) as exc:
                raise RuntimeError(
                    "Cannot create the selective mouse proxy via /dev/uinput. "
                    "Check /dev/uinput write permissions and ydotool/uinput setup."
                ) from exc

            # Let libinput discover the replacement before grabbing the mouse.
            # Re-creation happens only at startup, never for each dictation.
            time.sleep(0.5)
            try:
                dev.grab()
                grabbed = True
            except (PermissionError, OSError) as exc:
                raise RuntimeError(
                    f"Cannot exclusively grab {dev.path} ({dev.name}). "
                    "Another process may already hold the device, or evdev permissions are missing."
                ) from exc

            LOG.info(
                "Exclusive trigger enabled: consuming %s from %s; all other mouse events are proxied",
                self.code,
                dev.name,
            )

            if self.on_ready:
                self.on_ready()
            for event in self._events(dev):
                if _is_consumed_target_event(
                    event,
                    target_code=target_code,
                    ev_key=evdev.ecodes.EV_KEY,
                ):
                    if event.value == 1:
                        on_press()
                    elif event.value == 0:
                        on_release()
                    # Do NOT forward the trigger event to the desktop.
                    continue

                if event.type == evdev.ecodes.EV_SYN:
                    if event.code == evdev.ecodes.SYN_REPORT:
                        proxy.syn()
                    continue

                # Mirror every non-trigger event so movement, wheel and the
                # remaining buttons continue to behave exactly like a mouse.
                proxy.write(event.type, event.code, event.value)
        finally:
            if grabbed:
                try:
                    dev.ungrab()
                except OSError:
                    pass
            if proxy is not None:
                proxy.close()

    def run(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        try:
            import evdev
        except ImportError as exc:
            raise RuntimeError("evdev is not installed") from exc

        dev, target_code = self._select_device()
        try:
            if self.consume:
                self._run_consuming_proxy(dev, target_code, on_press, on_release)
            else:
                self._run_observer(dev, target_code, on_press, on_release)
        finally:
            dev.close()
