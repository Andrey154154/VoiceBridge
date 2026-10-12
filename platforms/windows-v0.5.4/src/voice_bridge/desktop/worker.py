"""Headless engine subprocess used by the desktop; no Qt imports.

Commands/events travel over inherited stdin/stdout pipes, not a network port.
The parent owns the worker lifetime; stdin EOF cancels pending dictation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import logging
import os
import queue
import sys
import threading
import time

from ..config import Config, load_config
from ..injectors import create_injector
from ..instance import UserInstanceLock
from ..injectors.errors import DeliveryDeferred
from ..platform_paths import log_dir

LOG = logging.getLogger(__name__)
PREFIX = "VB_EVENT "


class GuardedInjector:
    def __init__(self, engine):
        self.engine = engine

    def _guard(self):
        e = self.engine
        if e.stopping.is_set() or not e.enabled or (e.controller and e.controller._cancelled.is_set()):
            raise RuntimeError("Paste cancelled")
        if e.gui_active:
            raise DeliveryDeferred("Текст готов. Выберите поле в другом приложении и нажмите «Вставить через 5 секунд».")
        recorded = getattr(e, "delivery_target", None)
        # Do not bind a multi-second recognition to a transient child HWND by
        # default. The real Windows injector still binds the *delivery* itself
        # and rejects a focus change before emitting any paste keys.
        if getattr(e.injector, "supports_guard", False) is True:
            current = e.injector.api.target()
            if len(current) >= 4 and current[2] in {os.getpid(), os.getppid()}:
                raise DeliveryDeferred("Текст готов. Активно окно Voice Bridge, а не целевое приложение. Выберите поле и повторите вставку.")
            policy = e.config.app.paste_target
            if policy != "active" and recorded is not None:
                # Window mode permits a new child focus within the same window.
                # Process/thread IDs guard against HWND reuse by a different app.
                match = (bool(current and recorded and current[0] and recorded[0]) and
                         current[0] == recorded[0] and current[2:] == recorded[2:])
                if policy == "strict":
                    match = match and current == recorded
                if not match:
                    LOG.info("Delivery deferred policy=%s recorded=%s current=%s", policy, recorded, current)
                    raise DeliveryDeferred("Текст готов, но исходное окно или поле изменилось. "
                                           "Выберите нужное поле и нажмите «Вставить через 5 секунд». "
                                           "Для поведения CLI выберите «Активное поле после распознавания».")

    def insert(self, text):
        if getattr(self.engine.injector, "supports_guard", False) is True:
            LOG.info("Delivery binding policy=%s recorded=%s current=%s", self.engine.config.app.paste_target,
                     self.engine.delivery_target, self.engine.injector.api.target())
        from ..injectors.linux import LinuxTextInjector
        if isinstance(self.engine.injector, LinuxTextInjector) or getattr(self.engine.injector, "supports_guard", False) is True:
            return self.engine.injector.insert(text, guard=self._guard)
        self._guard()
        return self.engine.injector.insert(text)

    def undo(self):
        self._guard()
        self.engine.injector.undo()


class Engine:
    def __init__(self, config: Config, emit):
        self.config = config
        self._sink = emit
        i = config.injector
        self.injector = create_injector(backend=i.backend, paste_shortcut=i.paste_shortcut,
                                       keyboard_backend=i.keyboard_backend, ydotool_syntax=i.ydotool_syntax,
                                       key_delay_ms=i.key_delay_ms, clipboard_settle_ms=i.clipboard_settle_ms)
        self.controller = None
        self.trigger = None
        self.enabled = True
        self.gui_active = True  # fail closed until the UI sends its focus state
        self.stopping = threading.Event()
        self.delivery_ready = threading.Event()
        self._test_lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._model_started = False
        self._trigger_thread = None
        self._events = queue.Queue(maxsize=128)
        self._operation_lock = threading.RLock()
        self.last_text = ""
        self.delivery_target = None
        self._loading = False
        self._reconfiguring = False
        self._trigger_online = False
        self._trigger_epoch = 0
        self._input_epoch = 0
        self._pressed = False
        self._stage = ""
        self._load_started_at = 0.0
        self._load_generation = 0
        self._test_cancel = threading.Event()
        self._closed = False

    def emit(self, state, detail=""):
        # Never log a user's transcript or paste-test text.
        logged = f"{len(detail)} characters" if state == "transcript" else detail
        LOG.info("Engine state=%s detail=%s", state, logged)
        self._sink(state, detail)

    def _model_stage(self, detail):
        self._stage = detail
        self.emit("model_stage", detail)

    def _heartbeat(self, generation):
        while not self.stopping.wait(5):
            if not self._loading or generation != self._load_generation:
                return
            elapsed = int(time.monotonic() - self._load_started_at)
            self.emit("model_stage", f"{self._stage} · {elapsed} с")

    def start(self, load_model: bool) -> None:
        threading.Thread(target=self._prepare, args=(load_model,), daemon=True,
                         name="voice-bridge-prepare").start()
        threading.Thread(target=self._dispatch, daemon=True, name="voice-bridge-control").start()

    def _prepare(self, load_model: bool) -> None:
        try:
            prepare = getattr(self.injector, "prepare", None)
            detail = prepare() if prepare else "Delivery initialized"
            if self.stopping.is_set():
                return
            self.delivery_ready.set()
            self.emit("delivery_ready", detail)
            if load_model:
                self.load_model()
        except Exception as exc:
            LOG.exception("Delivery initialization failed")
            self.emit("delivery_error", str(exc))

    def load_model(self) -> None:
        with self._operation_lock, self._load_lock:
            if self.stopping.is_set() or self._loading:
                LOG.info("Model load ignored: loading=%s stopping=%s", self._loading, self.stopping.is_set())
                return
            if self._reconfiguring or self._test_lock.locked() or (self.controller and self.controller.busy):
                self.emit("load_rejected", "Завершите текущую операцию перед включением модели.")
                return
            if self._model_started and self._trigger_online:
                LOG.info("Model load ignored: already ready")
                self.emit("ready" if self.enabled else "disabled", "Модель уже загружена")
                return
            self._loading = True
            self._model_started = True
            self._load_started_at = time.monotonic()
            self._load_generation += 1
            generation = self._load_generation
            self._stage = "Подготовка библиотек распознавания"
            self.emit("loading", self._stage)
        threading.Thread(target=self._heartbeat, args=(generation,), daemon=True, name="voice-bridge-load-progress").start()
        threading.Thread(target=self._load, daemon=True, name="voice-bridge-model").start()

    def _load(self):
        try:
            if not self.delivery_ready.wait(5):
                raise RuntimeError("Механизм вставки не подготовлен. Откройте раздел проверки вставки.")
            if self.stopping.is_set():
                return
            # Read the saved profile at the explicit Load command, not the old
            # profile from the diagnostics worker's start several minutes ago.
            updated = load_config(self.config.source_path) if self.config.source_path else self.config
            if self.controller and updated.asr != self.config.asr:
                raise RuntimeError("Модель изменилась: нужен явный перезапуск движка.")
            self._apply_config(updated)
            if not self.controller:
                self._model_stage("Импорт библиотек распознавания и подготовка модели")
                from ..factory import create_controller
                controller = create_controller(self.config, state_callback=self._state,
                                               injector_override=GuardedInjector(self))
                if self.stopping.is_set():
                    return
                self.controller = controller
            else:
                self._model_stage("Модель сохранена в памяти; повторное подключение кнопки")
            self._model_stage("Подключение кнопки записи")
            self._start_trigger(self.config)
            if self.stopping.is_set():
                return
            self._loading = False
            self._trigger_ready()
        except Exception as exc:
            LOG.exception("Dictation engine failed at stage=%s", self._stage)
            with self._load_lock:
                self._model_started = False
            self._loading = False
            if not self.stopping.is_set():
                self.emit("model_error", str(exc))
        finally:
            # A retry can arrive synchronously after model_error is published.
            # Do not clear the next load's reservation from this old thread.
            if self.stopping.is_set():
                self._loading = False

    def _start_trigger(self, config):
        """Start only a trigger thread. The loaded controller/model is retained."""
        from ..triggers import create_trigger
        if self.trigger is not None:
            self._stop_trigger()
        cfg = config.trigger
        trigger = create_trigger(**asdict(cfg))
        ready = threading.Event()
        failures = []
        self._trigger_epoch += 1
        epoch = self._trigger_epoch
        self.trigger = trigger
        if hasattr(trigger, "set_enabled"):
            trigger.set_enabled(self.enabled and not self.gui_active)
        reported_ready = hasattr(trigger, "on_ready")
        if reported_ready:
            trigger.on_ready = ready.set

        def run():
            try:
                if not reported_ready:
                    ready.set()
                trigger.run(lambda: self._enqueue("press", epoch), lambda: self._enqueue("release", epoch))
            except Exception as exc:
                failures.append(exc)
                LOG.exception("Trigger thread failed")
            finally:
                ready.set()  # unblock startup, also when the hook fails
                if (self.trigger is trigger and not self.stopping.is_set() and
                        not self._loading and not self._reconfiguring):
                    self._trigger_online = False
                    self._model_started = False
                    if self.controller:
                        self.controller.cancel()
                    self.emit("model_error", "Обработчик кнопки остановился. Модель осталась в памяти. "
                              "Нажмите «Включить диктовку» для повторного подключения кнопки.")
        thread = threading.Thread(target=run, daemon=True, name="voice-bridge-trigger")
        self._trigger_thread = thread
        thread.start()
        if not ready.wait(5) or not thread.is_alive() or failures:
            self._stop_trigger()
            raise RuntimeError(f"Не удалось подключить кнопку записи: {failures[0] if failures else 'нет подтверждения готовности'}")
        if self.stopping.is_set():
            self._stop_trigger()
            return
        self._trigger_online = True

    def _stop_trigger(self):
        self._trigger_online = False
        self._trigger_epoch += 1
        self._input_epoch += 1
        self._pressed = False
        trigger = self.trigger
        thread = self._trigger_thread
        if trigger and hasattr(trigger, "stop"):
            trigger.stop()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError("Прежний обработчик кнопки не остановился; второй не запускается.")
        self.trigger = None
        self._trigger_thread = None

    def _trigger_ready(self):
        self.emit("ready" if self.enabled else "disabled", "Кнопка записи готова")

    def _state(self, state, detail=""):
        if state == "transcript":
            self.last_text = detail
        if state == "model_stage":
            self._stage = detail
        self.emit(state, detail)

    def _enqueue(self, event, trigger_epoch=None):
        # Mouse hook must never wait for audio/ASR. Events from a replaced hook
        # or from before disable/focus changes cannot start a new recording.
        if trigger_epoch is not None and trigger_epoch != self._trigger_epoch:
            return
        if self.stopping.is_set() or self._loading or self._reconfiguring:
            return
        try:
            self._events.put_nowait((self._input_epoch, event))
        except queue.Full:
            LOG.error("Trigger event queue overflow; cancelling recording")
            self._input_epoch += 1
            if self.controller:
                self.controller.cancel()

    def _dispatch(self):
        while not self.stopping.is_set():
            try:
                epoch, event = self._events.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                with self._operation_lock:
                    if (epoch != self._input_epoch or self.controller is None or not self.enabled or
                            self.stopping.is_set() or self._test_lock.locked() or self._loading or
                            self._reconfiguring or not self._trigger_online):
                        continue
                    if event == "press":
                        if self.gui_active:
                            continue
                        if self.controller.busy:
                            continue
                        if getattr(self.injector, "supports_guard", False) is True:
                            self.delivery_target = self.injector.api.target()
                            LOG.info("Recording target policy=%s target=%s", self.config.app.paste_target, self.delivery_target)
                        self.controller.press()
                        self._pressed = True
                    elif event == "release" and self._pressed:
                        self._pressed = False
                        self.controller.release()
            except Exception as exc:
                LOG.exception("Trigger action failed")
                self._pressed = False
                self.emit("error", str(exc))
                if self.controller and not self.controller.busy:
                    self.emit("operation_idle")

    def command(self, data):
        command = data.get("command")
        if command != "focus":
            LOG.info("Worker command=%s loading=%s busy=%s", command, self._loading,
                     bool(self.controller and self.controller.busy))
        if command == "focus":
            active = bool(data.get("active", True))
            with self._operation_lock:
                changed = self.gui_active != active
                self.gui_active = active
                if changed:
                    self._input_epoch += 1
                    self._pressed = False
                    if active and self.controller and self.controller.busy:
                        self.controller.cancel()
                if self.trigger and hasattr(self.trigger, "set_enabled"):
                    self.trigger.set_enabled(self.enabled and not active)
        elif command == "enabled":
            with self._operation_lock:
                self.enabled = bool(data.get("enabled"))
                self._input_epoch += 1
                self._pressed = False
                if self.trigger and hasattr(self.trigger, "set_enabled"):
                    self.trigger.set_enabled(self.enabled and not self.gui_active)
                if not self.enabled and self.controller:
                    self.controller.cancel()
            if not self._loading:
                self.emit("ready" if self.enabled and self._trigger_online else "disabled", "")
        elif command == "load_model":
            self.load_model()
        elif command in {"configure", "configure_delivery"}:
            self._begin_configure(only_delivery=command == "configure_delivery")
        elif command == "cancel":
            self._test_cancel.set()
            self._input_epoch += 1
            self._pressed = False
            if self.controller:
                self.controller.cancel()
        elif command == "paste_test":
            with self._operation_lock:
                if self._loading or self._reconfiguring or (self.controller and self.controller.busy):
                    self.emit("test_error", "Завершите загрузку модели или диктовку перед проверкой вставки.")
                    return
                if not self._test_lock.acquire(blocking=False):
                    self.emit("test_error", "Проверка вставки уже выполняется")
                    return
                self._test_cancel.clear()
                text = str(data.get("text", ""))[:32768]
                try:
                    delay = max(0.0, min(10.0, float(data.get("delay", 0))))
                except (TypeError, ValueError):
                    self._test_lock.release()
                    self.emit("test_error", "Invalid test delay")
                    return
            threading.Thread(target=self._paste_test,
                             args=(text, delay, bool(data.get("internal", False))),
                             daemon=True, name="voice-bridge-paste-test").start()
        elif command == "stop":
            LOG.info("Cooperative stop reason=%s stage=%s", str(data.get("reason", "parent_request"))[:100], self._stage)
            self.stopping.set()
            self._test_cancel.set()
        else:
            self.emit("error", f"Unknown command: {command}")

    def _begin_configure(self, only_delivery=False):
        with self._operation_lock:
            if (self._loading or self._reconfiguring or self.stopping.is_set() or
                    (self.controller and self.controller.busy) or self._test_lock.locked()):
                self.emit("config_error", "Параметры сохранены, но движок занят. Примените их после текущей операции.")
                return
            self._reconfiguring = True
            self._input_epoch += 1
            self._pressed = False
        def configure():
            try:
                updated = load_config(self.config.source_path)
                if only_delivery:
                    candidate = deepcopy(self.config)
                    candidate.injector = updated.injector
                    candidate.app.paste_target = updated.app.paste_target
                    updated = candidate
                with self._operation_lock:
                    self._apply_config(updated)
                self._reconfiguring = False
                self.emit("config_applied", "Настройки применены без перезагрузки модели.")
            except Exception as exc:
                LOG.exception("Configuration was not applied")
                self._reconfiguring = False
                self.emit("config_error", str(exc))
        threading.Thread(target=configure, daemon=True, name="voice-bridge-configure").start()

    def _apply_config(self, updated):
        """Prepare replacements before changing live objects; ASR is never rebuilt here."""
        from ..audio import AudioRecorder
        from ..text_engine import TextEngine
        from ..config import resolve_optional_config_path
        if self.stopping.is_set():
            raise RuntimeError("Движок останавливается")
        old_config = self.config
        if self.controller and updated.asr != old_config.asr:
            raise RuntimeError("Изменены параметры модели: требуется явная перезагрузка модели.")
        replacement = None
        trigger_changed = bool(self.controller and self._trigger_online and updated.trigger != old_config.trigger)
        recorder = text_engine = None
        try:
            if self.controller and updated.audio != old_config.audio:
                recorder = AudioRecorder(sample_rate=updated.audio.sample_rate, device=updated.audio.input_device,
                                         dtype=updated.audio.dtype, max_seconds=updated.audio.max_seconds)
            if self.controller and updated.text != old_config.text:
                text_engine = TextEngine(vocabulary_file=resolve_optional_config_path(updated, updated.text.vocabulary_file),
                                         capitalize_sentences=updated.text.capitalize_sentences)
            if updated.injector != old_config.injector:
                replacement = create_injector(**asdict(updated.injector))
                prepare = getattr(replacement, "prepare", None)
                if prepare:
                    prepare()
            if trigger_changed:
                self._stop_trigger()
                self._start_trigger(updated)
            if self.stopping.is_set():
                raise RuntimeError("Изменение параметров отменено при выходе")
            if self.controller:
                if recorder is not None:
                    self.controller.recorder = recorder
                if text_engine is not None:
                    self.controller.text_engine = text_engine
                    if text_engine.vocabulary_warning:
                        self.emit("warning", text_engine.vocabulary_warning)
                self.controller.mode = updated.app.mode
                self.controller.language = updated.app.language
            if replacement is not None:
                old, self.injector = self.injector, replacement
                close = getattr(old, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        LOG.exception("Old delivery close failed after replacement")
            self.config = updated
            LOG.info("Configuration committed model_retained=%s trigger_rebound=%s", bool(self.controller), trigger_changed)
        except Exception:
            if replacement is not None and replacement is not self.injector:
                close = getattr(replacement, "close", None)
                if close:
                    close()
            if trigger_changed and not self.stopping.is_set():
                try:
                    self._stop_trigger()
                    self._start_trigger(old_config)
                except Exception:
                    LOG.exception("Previous trigger could not be restored")
                    self._model_started = False
                    self.emit("model_error", "Не удалось восстановить обработчик кнопки. Нажмите «Включить диктовку».")
            raise

    def _paste_test(self, text, delay, internal):
        outcome = ("test_error", "Проверка вставки не завершена")
        try:
            with self._operation_lock:
                if self.controller and self.controller.busy:
                    raise RuntimeError("Finish or cancel dictation before testing paste")
            if not text:
                raise RuntimeError("No text to paste")
            if not self.delivery_ready.wait(5):
                raise RuntimeError("Delivery not initialized")
            self.emit("test_pending", f"{delay:g}")
            if self._test_cancel.wait(delay) or self.stopping.is_set():
                raise RuntimeError("Проверка вставки отменена")
            def guard():
                if self.stopping.is_set() or self._test_cancel.is_set():
                    raise RuntimeError("Test cancelled")
                if internal and not self.gui_active:
                    raise RuntimeError("Test window lost focus; internal paste test cancelled")
                if not internal and self.gui_active:
                    raise RuntimeError("Switch to the target field before the test countdown ends")
            guard()
            from ..injectors.linux import LinuxTextInjector
            if isinstance(self.injector, LinuxTextInjector) or getattr(self.injector, "supports_guard", False) is True:
                delivery = self.injector.insert(text, guard=guard)
            else:
                delivery = self.injector.insert(text)
            outcome = ("test_sent", delivery or "application-unverified")
        except Exception as exc:
            LOG.exception("Paste test failed")
            outcome = ("test_error", str(exc))
        finally:
            self._test_lock.release()
        self.emit(*outcome)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stopping.set()
        self._test_cancel.set()
        LOG.info("Engine shutdown stage=%s loading=%s", self._stage, self._loading)
        if self.controller:
            self.controller.cancel()
        try:
            self._stop_trigger()
        except Exception:
            LOG.exception("Trigger shutdown failed")
        close = getattr(self.injector, "close", None)
        if close:
            close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--no-model", action="store_true")
    parser.add_argument("--initial-enabled", choices=("0", "1"), default="1")
    args = parser.parse_args(argv)
    wire = sys.stdout
    sys.stdout = sys.stderr  # libraries must not corrupt the IPC stream
    output_lock = threading.Lock()
    def emit(state, detail=""):
        with output_lock:
            try:
                wire.write(PREFIX + json.dumps({"state": state, "detail": detail}, ensure_ascii=False) + "\n")
                wire.flush()
            except (BrokenPipeError, OSError):
                if engine:
                    engine.stopping.set()
    engine = None
    lock = UserInstanceLock("engine")
    try:
        lock.acquire()
        config = load_config(args.config)
        logging.basicConfig(level=getattr(logging, config.app.log_level.upper(), logging.INFO),
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            stream=sys.stderr, force=True)
        from .. import __version__
        LOG.info("Headless engine %s pid=%d python=%s source=%s session=%s Wayland=%s DISPLAY=%s",
                 __version__, os.getpid(), sys.executable, __file__, os.environ.get("XDG_SESSION_TYPE"),
                 os.environ.get("WAYLAND_DISPLAY"), os.environ.get("DISPLAY"))
        engine = Engine(config, emit)
        engine.enabled = args.initial_enabled == "1"
        engine.start(not args.no_model)
        for line in sys.stdin:
            try:
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError("Command must be a JSON object")
                engine.command(command)
            except Exception as exc:
                LOG.exception("Command failed")
                emit("error", f"Command failed: {exc}")
            if engine.stopping.is_set():
                break
        if not engine.stopping.is_set():
            LOG.info("Parent IPC closed (stdin EOF); stopping engine")
        return 0
    except Exception as exc:
        LOG.exception("Worker failed")
        emit("error", str(exc))
        return 1
    finally:
        if engine:
            engine.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
