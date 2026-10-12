from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable

from ..config import Config
from ..platform_paths import log_dir

LOG = logging.getLogger(__name__)
PREFIX = "VB_EVENT "


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    state: str
    detail: str = ""


class DesktopRuntime:
    """One owned worker process. Loading/configuration are commands, not restarts.

    The UI calls stop() off its event thread for an explicit model replacement.
    There is no automatic crash/restart loop and no PID-based termination of
    another program. ASR libraries never run on the Qt thread.
    """
    def __init__(self, config: Config, emit: Callable[[RuntimeEvent], None], *, diagnostics_only=False) -> None:
        self.config = config
        self.emit = emit
        self.enabled = True
        self.gui_active = True
        self.diagnostics_only = diagnostics_only
        self._process = None
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._stopping = threading.Event()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, *, reason="application_start") -> None:
        with self._lifecycle_lock, self._lock:
            if self.running:
                LOG.info("Worker start ignored: already running pid=%s", self._process.pid)
                return
            if not self.config.source_path:
                raise ValueError("Desktop worker needs a saved configuration file")
            self._stopping.clear()
            python = sys.executable
            if os.name == "nt" and Path(python).name.lower() == "pythonw.exe":
                python = str(Path(python).with_name("python.exe"))
            command = [python, "-X", "utf8", "-u", "-m", "voice_bridge.desktop.worker",
                       "--config", str(self.config.source_path)]
            if self.diagnostics_only:
                command.append("--no-model")
            # Initial focus/enabled values are sent before permitting trigger input.
            command += ["--initial-enabled", "1" if self.enabled else "0"]
            with (log_dir() / "voice-bridge.log").open("a", encoding="utf-8") as errors:
                self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=errors, text=True, encoding="utf-8", bufsize=1, close_fds=True,
                    creationflags=(0x08000000 if os.name == "nt" else 0))
            process = self._process
            LOG.info("Worker start pid=%s reason=%s model=%s config=%s",
                     process.pid, reason, not self.diagnostics_only, self.config.source_path)
            threading.Thread(target=self._read_events, args=(process,), daemon=True,
                             name="voice-bridge-events").start()
            self._send({"command": "focus", "active": self.gui_active})

    def _read_events(self, process):
        try:
            for line in process.stdout:
                if not line.startswith(PREFIX):
                    LOG.warning("Non-protocol worker output: %s", line[:300].rstrip())
                    continue
                try:
                    raw = json.loads(line[len(PREFIX):])
                    if self._process is process and not self._stopping.is_set():
                        self.emit(RuntimeEvent(str(raw["state"]), str(raw.get("detail", ""))))
                except (ValueError, KeyError, TypeError):
                    LOG.exception("Malformed engine event")
        except (OSError, ValueError):
            if not self._stopping.is_set():
                LOG.exception("Worker event channel failed")
        finally:
            process.stdout.close()
            returncode = process.wait()
            LOG.info("Worker exit pid=%s code=%s expected=%s", process.pid, returncode,
                     self._stopping.is_set() or self._process is not process)
            if self._process is process and not self._stopping.is_set():
                self.emit(RuntimeEvent("error", f"Процесс движка завершился (код {returncode}). "
                    "Причина записана в журнал. Нажмите «Включить диктовку» для повторного запуска."))

    def _send(self, payload):
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                raise RuntimeError("Движок Voice Bridge не запущен")
            try:
                process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise RuntimeError("Потеряно соединение с движком Voice Bridge") from exc

    def set_enabled(self, enabled):
        self._send({"command": "enabled", "enabled": bool(enabled)})
        self.enabled = bool(enabled)

    def set_gui_active(self, active):
        self.gui_active = active
        if self.running and not self._stopping.is_set():
            self._send({"command": "focus", "active": active})

    def paste_test(self, text, *, delay=0.0, internal=False):
        self._send({"command": "paste_test", "text": text, "delay": delay, "internal": internal})

    def configure_delivery(self):
        self._send({"command": "configure_delivery"})

    def configure(self):
        self._send({"command": "configure"})

    def load_model(self):
        self._send({"command": "load_model"})

    def cancel(self):
        self._send({"command": "cancel"})

    def stop(self, *, reason="application_exit"):
        # Serialize with start(), without holding the IPC write lock during wait.
        with self._lifecycle_lock:
            self._stopping.set()
            process = self._process
            if not process:
                return
            LOG.info("Worker stop requested pid=%s reason=%s", process.pid, reason)
            if process.poll() is None:
                try:
                    self._send({"command": "stop", "reason": str(reason)[:100]})
                    process.stdin.close()
                    process.wait(timeout=3.0)
                except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        LOG.warning("Worker did not exit cooperatively; terminating owned pid=%s reason=%s",
                                    process.pid, reason)
                        process.terminate()
                        try:
                            process.wait(timeout=2.0)
                        except subprocess.TimeoutExpired:
                            LOG.warning("Killing owned worker pid=%s after terminate timeout", process.pid)
                            process.kill()
                            process.wait(timeout=2.0)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                raise RuntimeError("Прежний процесс ещё работает; новый процесс не запущен")
            self._process = None
