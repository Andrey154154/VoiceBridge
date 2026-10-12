from __future__ import annotations

import logging
from collections import deque
import glob
import os
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
        self._state_lock = threading.Lock()
        self._enabled = True
        self._target_down = False
        self._hold_claimed = False
        self._target_quarantined = False
        # Loss must cancel a recording, never call on_release (which submits ASR).
        self.on_cancel: Callable[[], None] | None = None
        self._sync_started: float | None = None
        self._sync_times = deque()
        self._settle_seconds = 0.5
        self._sync_timeout = 2.0

    @staticmethod
    def _readable_paths(evdev):
        # evdev.list_devices() defaults to requiring R/W, but observing/grabbing
        # the physical mouse needs only R. Older evdev has no writable argument.
        try:
            return sorted(evdev.list_devices(writable=False))
        except TypeError:
            return sorted(p for p in glob.glob('/dev/input/event*') if os.access(p, os.R_OK))

    @staticmethod
    def devices(*, mouse_only=False) -> list[tuple[str, str]]:
        try:
            import evdev
        except ImportError as exc:
            raise RuntimeError("evdev is not installed") from exc
        result = []
        for path in EvdevHoldTrigger._readable_paths(evdev):
            dev = None
            try:
                dev = evdev.InputDevice(path)
                if not is_virtual_device(dev.name, getattr(dev, "phys", "")):
                    keys = dev.capabilities().get(evdev.ecodes.EV_KEY, []) if mouse_only else []
                    if not mouse_only or 275 in keys or 276 in keys:
                        result.append((path, dev.name))
            except OSError as exc:
                LOG.debug("Input device disappeared or is inaccessible: %s: %s", path, exc)
            finally:
                if dev is not None:
                    dev.close()
        return result

    def _select_device(self):
        try:
            import evdev
        except ImportError as exc:
            raise RuntimeError("evdev is not installed") from exc
        code_value = evdev.ecodes.ecodes.get(self.code)
        if code_value is None:
            raise ValueError(f"Unknown evdev code: {self.code}")
        candidates = []
        paths = self._readable_paths(evdev)
        # Explicit event paths may be absent from an old library's enumeration.
        if self.device_match.startswith('/dev/input/') and self.device_match not in paths:
            paths.insert(0, self.device_match)
        for path in paths:
            dev = None
            try:
                dev = evdev.InputDevice(path)
                if is_virtual_device(dev.name, getattr(dev, "phys", "")):
                    continue
                matches = (not self.device_match or self.device_match in dev.name.lower() or
                           self.device_match == path.lower())
                if not matches:
                    continue
                keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
                candidates.append(f"{path}: {dev.name}")
                if code_value in keys:
                    LOG.info("Physical trigger selected: path=%s name=%s code=%s consume=%s",
                             path, dev.name, self.code, self.consume)
                    selected, dev = dev, None
                    return selected, code_value
            except OSError as exc:
                LOG.debug("Cannot inspect input device %s: %s", path, exc)
            finally:
                if dev is not None:
                    dev.close()
        names = "; ".join(candidates) or "нет доступных устройств"
        raise RuntimeError(
            f"Не найдена доступная мышь с {self.code} (фильтр: {self.device_match or 'авто'}). "
            f"Устройства: {names}. Проверьте выбор мыши и выполните bash check.sh. "
            "Для настройки доступа используйте bash repair-input.sh, не запускайте приложение через sudo.")

    def set_enabled(self, enabled: bool) -> None:
        # Keep a press/release pair's routing stable when the GUI gains focus or
        # pauses dictation halfway through a hold. Never leak an orphan release.
        with self._state_lock:
            self._enabled = bool(enabled)

    def _target_event(self, value, on_press, on_release) -> bool:
        """Return whether this event belongs to a dictation hold (not desktop)."""
        action = None
        with self._state_lock:
            # A down discovered by EVIOCGKEY is not a new intentional press.
            # Suppress this hold until its real up, without dispatching ASR.
            if self._target_quarantined:
                if value == 0:
                    self._target_quarantined = False
                    self._target_down = False
                    self._hold_claimed = False
                return True
            if value == 1:
                if self._target_down:
                    return self._hold_claimed
                self._target_down = True
                self._hold_claimed = self._enabled
                if self._hold_claimed:
                    action = on_press
            elif value == 0:
                claimed = self._target_down and self._hold_claimed
                self._target_down = False
                self._hold_claimed = False
                if claimed:
                    action = on_release
                if action is None:
                    return False
            elif value == 2:
                return self._target_down and self._hold_claimed
            else:
                return False
            claimed = value == 0 or self._hold_claimed
        if action:
            action()
        return claimed

    def stop(self) -> None:
        self._stop_event.set()

    def _drain_startup(self, dev, *, budget=0.02):
        """Discard only pre-baseline events, with a bounded nonblocking read.

        Before EVIOCGRAB the real desktop already receives these events. Replaying
        them through the new proxy would duplicate old clicks and movement.
        Return True on EAGAIN (caught up); never wait for the mouse to stop moving.
        """
        deadline = time.monotonic() + budget
        while not self._stop_event.is_set():
            try:
                count = 0
                for _ in dev.read():
                    count += 1
                if not count:
                    return True
            except BlockingIOError:
                return True
            if time.monotonic() >= deadline:
                return False
        return False

    def _prepare_input(self, dev, *, settle=0.0):
        # Keep draining during device discovery and while waiting for an old
        # physical click to end. The 0.5.5 blind sleep overran gaming-mouse queues.
        settled_at = time.monotonic() + settle
        deadline = settled_at + 2.0
        while not self._stop_event.is_set():
            caught_up = self._drain_startup(dev)
            if time.monotonic() >= settled_at and caught_up and not dev.active_keys():
                return True
            if time.monotonic() >= deadline:
                raise RuntimeError("Отпустите кнопки мыши и повторите включение диктовки. "
                                   "Не удалось подготовить чистый поток событий.")
            if self._stop_event.wait(0.002):
                return False
        return False

    def _baseline(self, dev, target_code, proxy=None, forwarded_down=None):
        # Establish state only after the old queue was consumed. No callbacks are
        # synthesized for keys found held at the grab boundary.
        if not self._drain_startup(dev, budget=0.1):
            raise RuntimeError("Не удалось прочитать очередь мыши при подключении. Захват освобождён.")
        current = set(dev.active_keys())
        with self._state_lock:
            self._target_down = target_code in current
            self._hold_claimed = False
            # A target already held at startup belongs to the desktop, not ASR.
            self._target_quarantined = False
        if proxy is not None:
            for code in sorted(current):
                proxy.write(1, code, 1)
                forwarded_down.add(code)
            if current:
                proxy.syn()

    def _events(self, dev):
        while not self._stop_event.is_set():
            if (self._sync_started is not None and
                    time.monotonic() - self._sync_started > self._sync_timeout):
                raise RuntimeError("SYN_DROPPED: поток мыши не восстановился. Захват освобождён.")
            ready, _, _ = select.select([dev], [], [], 0.05)
            if ready:
                try:
                    for event in dev.read():
                        if self._stop_event.is_set():
                            return
                        yield event
                except BlockingIOError:
                    continue

    def _lost_sync(self):
        now = time.monotonic()
        self._sync_times.append(now)
        while self._sync_times and now - self._sync_times[0] > 10:
            self._sync_times.popleft()
        if self._sync_started is None:
            self._sync_started = now
            if self.on_cancel is not None:
                self.on_cancel()
            elif self._hold_claimed:
                raise RuntimeError("SYN_DROPPED: нет обработчика отмены диктовки. Захват освобождён.")
            LOG.warning("SYN_DROPPED: discarding through SYN_REPORT; resynchronizing mouse state")
        # Do not keep the physical device grabbed in an unbounded recovery loop.
        if len(self._sync_times) > 8:
            raise RuntimeError("SYN_DROPPED: повторная перегрузка потока мыши. Захват освобождён.")

    def _restore_state(self, dev, target_code, proxy=None, forwarded_down=None):
        """EVIOCGKEY snapshot after SYN_REPORT, not guessed missing edges.

        Relative movement/scroll from the lost interval cannot be reconstructed.
        Preserve ordinary held buttons and release those whose up was lost. A
        target hold of unknown origin is quarantined, never started as dictation.
        """
        current = set(dev.active_keys())
        old_forwarded = set(forwarded_down or ())
        pass_target = target_code in current and target_code in old_forwarded
        with self._state_lock:
            self._target_down = target_code in current
            self._hold_claimed = False
            self._target_quarantined = target_code in current and not pass_target
        if proxy is not None:
            desired = current - ({target_code} if self._target_quarantined else set())
            for code in sorted(old_forwarded - desired):
                proxy.write(1, code, 0)
                forwarded_down.discard(code)
            for code in sorted(desired - old_forwarded):
                proxy.write(1, code, 1)
                forwarded_down.add(code)
            # Also synchronize non-multitouch absolute axes and switches if a
            # mouse exposes them. Slot-based touch devices are rejected below.
            caps = dev.capabilities(absinfo=False)
            for code in caps.get(3, ()):
                proxy.write(3, code, dev.absinfo(code).value)
            switch_codes = caps.get(5, ())
            if switch_codes:
                active_switches = set(dev.switches())
                for code in switch_codes:
                    proxy.write(5, code, int(code in active_switches))
            proxy.syn()
        self._sync_started = None
        LOG.info("Mouse input resynchronized; held_target_quarantined=%s; model retained",
                 self._target_quarantined)

    def _synchronized_events(self, dev, target_code, proxy=None, forwarded_down=None):
        import evdev
        ec = evdev.ecodes
        packet = []
        for event in self._events(dev):
            if event.type == ec.EV_SYN and event.code == getattr(ec, "SYN_DROPPED", 3):
                packet.clear()  # An incomplete packet is not safe to replay.
                self._lost_sync()
                continue
            if self._sync_started is not None:
                if event.type == ec.EV_SYN and event.code == ec.SYN_REPORT:
                    self._restore_state(dev, target_code, proxy, forwarded_down)
                continue
            if event.type == ec.EV_SYN and event.code == ec.SYN_REPORT:
                # Forward whole frames only; no partial movement/click survives
                # SYN_DROPPED. Packets are tiny for a normal relative mouse.
                yield from packet
                packet.clear()
                yield event
            else:
                packet.append(event)
                if len(packet) > 1024:
                    raise RuntimeError("Некорректный поток мыши: нет границы пакета. Захват освобождён.")
        if self._sync_started is not None and not self._stop_event.is_set():
            raise RuntimeError("SYN_DROPPED: поток закончился без SYN_REPORT. Захват освобождён.")

    def _run_observer(self, dev, target_code: int, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        import evdev

        if not self._prepare_input(dev):
            return
        self._baseline(dev, target_code)
        if self.on_ready:
            self.on_ready()
        for event in self._synchronized_events(dev, target_code):
            if event.type != evdev.ecodes.EV_KEY or event.code != target_code:
                continue
            self._target_event(event.value, on_press, on_release)

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
        forwarded_down = set()
        try:
            try:
                proxy = evdev.UInput.from_device(
                    dev,
                    name=f"Voice Input Bridge proxy - {dev.name}",
                    phys="voice-bridge/mouse-proxy",
                )
            except (PermissionError, OSError) as exc:
                raise RuntimeError(
                    "Cannot create the selective mouse proxy via /dev/uinput. "
                    "Check /dev/uinput write permissions and ydotool/uinput setup."
                ) from exc

            # Do not clone multitouch slot state with a mouse-only synchronizer.
            # Ordinary relative mice, including high-polling gaming mice, are supported.
            caps = dev.capabilities(absinfo=False)
            if any(0x2f <= code <= 0x3f for code in caps.get(3, ())):
                raise RuntimeError("Выберите устройство мыши, а не multitouch-панель, для захвата кнопки.")
            if caps.get(5) and not callable(getattr(dev, "switches", None)):
                raise RuntimeError("Это устройство требует чтения переключателей; выберите обычное устройство мыши.")
            # Physical events continue to reach the desktop during this phase.
            if not self._prepare_input(dev, settle=self._settle_seconds):
                return
            try:
                dev.grab()
                grabbed = True
            except (PermissionError, OSError) as exc:
                raise RuntimeError(
                    f"Cannot exclusively grab {dev.path} ({dev.name}). "
                    "Another process may already hold the device, or evdev permissions are missing."
                ) from exc

            self._baseline(dev, target_code, proxy, forwarded_down)
            LOG.info(
                "Exclusive trigger enabled: consuming %s from %s; all other mouse events are proxied",
                self.code,
                dev.name,
            )

            if self.on_ready:
                self.on_ready()
            for event in self._synchronized_events(dev, target_code, proxy, forwarded_down):
                if _is_consumed_target_event(
                    event,
                    target_code=target_code,
                    ev_key=evdev.ecodes.EV_KEY,
                ):
                    if self._target_event(event.value, on_press, on_release):
                        # Only active dictation holds are suppressed. While
                        # paused/in settings the mouse button works normally.
                        continue

                if event.type == evdev.ecodes.EV_SYN:
                    if event.code == evdev.ecodes.SYN_REPORT:
                        proxy.syn()
                    continue

                # Mirror every non-trigger event so movement, wheel and the
                # remaining buttons continue to behave exactly like a mouse.
                if event.type == evdev.ecodes.EV_KEY and event.value == 1:
                    forwarded_down.add(event.code)
                proxy.write(event.type, event.code, event.value)
                if event.type == evdev.ecodes.EV_KEY and event.value == 0:
                    forwarded_down.discard(event.code)
        finally:
            if proxy is not None:
                for code in forwarded_down:
                    try:
                        proxy.write(evdev.ecodes.EV_KEY, code, 0)
                        proxy.syn()
                    except OSError:
                        LOG.warning("Cannot release proxied button %s", code)
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
